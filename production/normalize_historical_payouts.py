"""Normalize saved JRA payout HTML(.gz) into retrospective research rows.

The historical archive stores one gzip-compressed result/payout HTML document per
race. This module deliberately avoids CSS-class selectors. Real JRA payout rows
can contain two pool blocks side-by-side in one <tr>, and multiple winning
combinations can be concatenated inside one cell. The parser therefore splits a
row at each visible pool label and uses the known payout count to recover exactly
the same number of combinations.

Output schema is the narrow format consumed by research_all_bet_retrospective:
    レースID, 券種, 組合せ, 払戻円, source_file

Only confirmed winning payout rows are emitted. Missing/unsold pools are absent,
not converted to losing tickets. Forward ledgers are never read or written.
"""
from __future__ import annotations

import argparse
import csv
import gzip
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Iterable
import unicodedata

import research_all_bet_retrospective as core

BET_LABELS = {
    '単勝': '単勝', '複勝': '複勝', '枠連': '枠連', '馬連': '馬連',
    'ワイド': 'ワイド', '馬単': '馬単', '3連複': '3連複', '三連複': '3連複',
    '3連単': '3連単', '三連単': '3連単',
}
PAIR_BETS = {'枠連', '馬連', 'ワイド', '馬単'}
TRIPLE_BETS = {'3連複', '3連単'}
ORDERED_BETS = {'馬単', '3連単'}


def _clean(text: str) -> str:
    normalized = unicodedata.normalize('NFKC', str(text).replace('\u3000', ' '))
    return re.sub(r'\s+', ' ', normalized).strip()


class _TableRows(HTMLParser):
    """Small stdlib-only table-row extractor preserving cell boundaries."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_row = False
        self._cell_depth = 0
        self._cell_parts: list[str] = []
        self._row: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == 'tr':
            self._in_row = True
            self._row = []
        elif self._in_row and tag in {'th', 'td'}:
            self._cell_depth += 1
            if self._cell_depth == 1:
                self._cell_parts = []
        elif self._in_row and self._cell_depth and tag in {'br', 'li'}:
            self._cell_parts.append(' ')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._in_row and tag in {'th', 'td'} and self._cell_depth:
            self._cell_depth -= 1
            if self._cell_depth == 0:
                self._row.append(_clean(''.join(self._cell_parts)))
                self._cell_parts = []
        elif tag == 'tr' and self._in_row:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._in_row = False
            self._cell_depth = 0
            self._cell_parts = []
            self._row = []

    def handle_data(self, data):
        if self._in_row and self._cell_depth:
            self._cell_parts.append(data)


def html_table_rows(html: str) -> list[list[str]]:
    parser = _TableRows()
    parser.feed(html)
    parser.close()
    return parser.rows


def _bet_in_cell(cell: str):
    compact = re.sub(r'\s+', '', _clean(cell))
    for label, canonical in BET_LABELS.items():
        if compact == label or compact.startswith(label):
            return canonical
    return None


def _yen_values(cells: Iterable[str]) -> list[float]:
    values: list[float] = []
    for cell in cells:
        for raw in re.findall(r'([0-9][0-9,]*)\s*円', _clean(cell)):
            value = core._number(raw, positive=True)
            if value is not None:
                values.append(value)
    return values


def _combo_cells(cells: Iterable[str]) -> str:
    # Exclude payout/popularity cells so their numbers are never mistaken for
    # horse/frame numbers.
    usable = [
        _clean(c) for c in cells
        if '円' not in _clean(c) and '人気' not in _clean(c) and '票' not in _clean(c)
    ]
    return ' | '.join(usable)


def _ints(text: str) -> list[int]:
    out = []
    for raw in re.findall(r'(?<!\d)(\d{1,2})(?!\d)', _clean(text)):
        value = int(raw)
        if 1 <= value <= 18:
            out.append(value)
    return out


def _separated_groups(text: str, size: int) -> list[tuple[int, ...]]:
    # Simple readable layout such as 6-2 or 2-4-6.
    sep = r'\s*(?:-|―|ー|→|⇒|>)\s*'
    number = r'(\d{1,2})'
    if size == 2:
        pattern = number + sep + number
    else:
        pattern = number + sep + number + sep + number
    groups = []
    for match in re.finditer(pattern, _clean(text)):
        nums = tuple(int(x) for x in match.groups())
        if all(1 <= n <= 18 for n in nums):
            groups.append(nums)
    return groups


def _compact_exact_groups(text: str, size: int, expected_count: int) -> list[tuple[int, ...]]:
    """Recover concatenated JRA combinations using exact payout cardinality.

    Examples from the saved archive:
      複勝: 624              -> (6), (2), (4)
      ワイド: 2-64-62-4     -> (2,6), (4,6), (2,4)

    Numbers are 1..18. Between numbers inside one combination a visible pool
    separator is required for pair/triple pools; between adjacent winning
    combinations JRA may render no delimiter at all. We accept a parse only
    when the complete cell has exactly expected_count groups and the resulting
    canonical group sequence is unique.
    """
    if expected_count <= 0:
        return []
    compact = re.sub(r'[\s|,、/]+', '', _clean(text))
    separators = {'-', '―', 'ー', '→', '⇒', '>'}
    solutions: list[tuple[tuple[int, ...], ...]] = []

    def number_options(pos: int):
        for width in (1, 2):
            token = compact[pos:pos + width]
            if len(token) != width or not token.isdigit() or token.startswith('0'):
                continue
            value = int(token)
            if 1 <= value <= 18:
                yield value, pos + width

    def one_group(pos: int, remaining_numbers: int, values: tuple[int, ...] = ()):
        for value, after_number in number_options(pos):
            new_values = values + (value,)
            if remaining_numbers == 1:
                yield new_values, after_number
                continue
            if after_number < len(compact) and compact[after_number] in separators:
                yield from one_group(after_number + 1, remaining_numbers - 1, new_values)

    def many(pos: int, groups_left: int, groups: tuple[tuple[int, ...], ...] = ()):
        if groups_left == 0:
            if pos == len(compact):
                solutions.append(groups)
            return
        for group, after in one_group(pos, size):
            many(after, groups_left - 1, groups + (group,))

    many(0, expected_count)

    unique: list[tuple[tuple[int, ...], ...]] = []
    for solution in solutions:
        if solution not in unique:
            unique.append(solution)
    if len(unique) == 1:
        return list(unique[0])
    return []


def _combos(text: str, bet: str, expected_count: int | None = None) -> list[str]:
    text = _clean(text)
    size = 1 if bet in {'単勝', '複勝'} else 3 if bet in TRIPLE_BETS else 2

    # Exact cardinality is the safest route for real payout cells because JRA
    # can concatenate multiple winners without whitespace between combinations.
    if expected_count:
        exact = _compact_exact_groups(text, size, expected_count)
        if exact:
            return [core.canonical_combo(bet, group) for group in exact]

    if size == 1:
        nums = _ints(text)
        return [core.canonical_combo(bet, (n,)) for n in nums]

    groups = _separated_groups(text, size)
    result = []
    for group in groups:
        result.append(core.canonical_combo(bet, group))
    return result


def _append_pool_segment(out, seen, warnings, *, cells, bet, row_no, race_id, source_name, strict):
    payouts = _yen_values(cells)
    if not payouts:
        return
    combo_text = _combo_cells(cells)
    combos = _combos(combo_text, bet, expected_count=len(payouts))
    if len(combos) != len(payouts):
        message = (
            f'{source_name or race_id}: row {row_no} {bet} could not pair '
            f'combinations={combos!r} with payouts={payouts!r}; cells={cells!r}'
        )
        warnings.append(message)
        if strict:
            raise ValueError(message)
        return

    for combo, amount in zip(combos, payouts):
        key = (race_id, bet, combo)
        old = seen.get(key)
        if old is not None:
            if old != amount:
                raise ValueError(f'conflicting payout for {key}: {old} vs {amount}')
            continue
        seen[key] = amount
        out.append({
            'レースID': race_id,
            '券種': bet,
            '組合せ': combo,
            '払戻円': amount,
            'source_file': source_name,
        })


def parse_payout_html(html: str, race_id: str, source_name: str = '', *, strict: bool = True):
    """Return (normalized_rows, audit) for one saved payout/result HTML page."""
    race_id = str(race_id).strip()
    if not race_id:
        raise ValueError('race_id is required')

    out: list[dict] = []
    warnings: list[str] = []
    current_bet = None
    seen: dict[tuple[str, str, str], float] = {}

    for row_no, cells in enumerate(html_table_rows(html), 1):
        labels = []
        for i, cell in enumerate(cells):
            found = _bet_in_cell(cell)
            if found:
                labels.append((i, found))

        if labels:
            # A real JRA <tr> can contain two independent pool blocks, e.g.
            # [単勝, 6, 2,890円, ..., 馬単, 6-2, 19,220円, ...].
            for j, (label_index, bet) in enumerate(labels):
                end = labels[j + 1][0] if j + 1 < len(labels) else len(cells)
                segment = cells[label_index + 1:end]
                _append_pool_segment(
                    out, seen, warnings, cells=segment, bet=bet, row_no=row_no,
                    race_id=race_id, source_name=source_name, strict=strict,
                )
            current_bet = labels[-1][1]
            continue

        # Preserve legacy rowspan support for a single pool label followed by a
        # payout-only continuation row. Rows without yen are irrelevant.
        if current_bet is not None and _yen_values(cells):
            _append_pool_segment(
                out, seen, warnings, cells=cells, bet=current_bet, row_no=row_no,
                race_id=race_id, source_name=source_name, strict=strict,
            )

    coverage = sorted({row['券種'] for row in out}, key=core.ALL_BETS.index)
    audit = {
        'race_id': race_id,
        'source_file': source_name,
        'payout_rows': len(out),
        'covered_bets': ','.join(coverage),
        'covered_bet_count': len(coverage),
        'warning_count': len(warnings),
        'warnings': ' || '.join(warnings),
    }
    return out, audit


def race_id_from_path(path: Path) -> str:
    name = Path(path).name
    match = re.search(r'(\d{12})', name)
    if not match:
        raise ValueError(f'12-digit race id not found in filename: {name}')
    return match.group(1)


def read_saved_html(path: Path) -> str:
    path = Path(path)
    if path.suffix.lower() == '.gz':
        with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as handle:
            return handle.read()
    return path.read_text(encoding='utf-8', errors='replace')


def normalize_files(paths: Iterable[Path], *, strict: bool = True):
    rows: list[dict] = []
    audits: list[dict] = []
    global_seen: dict[tuple[str, str, str], float] = {}
    for path in paths:
        path = Path(path)
        rid = race_id_from_path(path)
        parsed, audit = parse_payout_html(read_saved_html(path), rid, path.name, strict=strict)
        audits.append(audit)
        for row in parsed:
            key = (row['レースID'], row['券種'], row['組合せ'])
            amount = row['払戻円']
            old = global_seen.get(key)
            if old is not None:
                if old != amount:
                    raise ValueError(f'conflicting payout across files for {key}: {old} vs {amount}')
                continue
            global_seen[key] = amount
            rows.append(row)
    return rows, audits


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True,
                        help='Directory containing saved race payout/result HTML or HTML.GZ files')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audit-output', type=Path, default=None)
    parser.add_argument('--allow-parse-warnings', action='store_true',
                        help='Skip unpairable payout rows but record them in audit CSV')
    args = parser.parse_args()

    inputs = sorted(p for p in args.input_dir.iterdir()
                    if p.is_file() and (p.name.endswith('.html') or p.name.endswith('.html.gz')))
    if not inputs:
        raise SystemExit(f'no payout HTML files found: {args.input_dir}')

    rows, audits = normalize_files(inputs, strict=not args.allow_parse_warnings)
    write_csv(args.output, rows, ['レースID', '券種', '組合せ', '払戻円', 'source_file'])
    audit_path = args.audit_output or args.output.with_name(args.output.stem + '_audit.csv')
    write_csv(audit_path, audits,
              ['race_id', 'source_file', 'payout_rows', 'covered_bets',
               'covered_bet_count', 'warning_count', 'warnings'])

    races = len({r['レースID'] for r in rows})
    warnings = sum(a['warning_count'] for a in audits)
    print(f'normalized_payout_rows={len(rows)} races={races} files={len(inputs)} warnings={warnings}')
    print(f'output={args.output}')
    print(f'audit={audit_path}')
    print('retrospective research input only; forward ledgers untouched')


if __name__ == '__main__':
    main()
