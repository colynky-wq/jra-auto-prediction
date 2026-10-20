"""Normalize saved JRA payout HTML(.gz) into retrospective research rows.

The historical archive stores one gzip-compressed result/payout HTML document per
race.  This module deliberately avoids CSS-class selectors: it extracts table
rows, recognises the standard JRA pool labels, and pairs winning combinations
with yen payouts.  That makes the research pipeline less fragile when markup
classes change while visible payout text remains stable.

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
    return re.sub(r'\s+', ' ', str(text).replace('\u3000', ' ')).strip()


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
    compact = re.sub(r'\s+', '', cell)
    for label, canonical in BET_LABELS.items():
        if compact == label or compact.startswith(label):
            return canonical
    return None


def _yen_values(cells: Iterable[str]) -> list[float]:
    values: list[float] = []
    for cell in cells:
        for raw in re.findall(r'([0-9][0-9,]*)\s*円', cell):
            value = core._number(raw, positive=True)
            if value is not None:
                values.append(value)
    return values


def _combo_cells(cells: Iterable[str]) -> str:
    # Exclude payout/popularity cells so their numbers are never mistaken for
    # horse/frame numbers.
    usable = [c for c in cells if '円' not in c and '人気' not in c and '票' not in c]
    return ' | '.join(usable)


def _ints(text: str) -> list[int]:
    out = []
    for raw in re.findall(r'(?<!\d)(\d{1,2})(?!\d)', text):
        value = int(raw)
        if 1 <= value <= 18:
            out.append(value)
    return out


def _separated_groups(text: str, size: int) -> list[tuple[int, ...]]:
    # JRA renders unordered pools with dashes and ordered pools with arrows.
    # Accept common full-width variants as historical snapshots can differ.
    sep = r'\s*(?:-|－|―|ー|→|⇒|>)\s*'
    number = r'(\d{1,2})'
    if size == 2:
        pattern = number + sep + number
    else:
        pattern = number + sep + number + sep + number
    groups = []
    for match in re.finditer(pattern, text):
        nums = tuple(int(x) for x in match.groups())
        if all(1 <= n <= 18 for n in nums):
            groups.append(nums)
    return groups


def _combos(text: str, bet: str, expected_count: int | None = None) -> list[str]:
    text = _clean(text)
    if bet in {'単勝', '複勝'}:
        nums = _ints(text)
        return [core.canonical_combo(bet, (n,)) for n in nums]

    size = 3 if bet in TRIPLE_BETS else 2
    groups = _separated_groups(text, size)

    # Some archived layouts put numbers into separate spans with whitespace but
    # no visible dash. Only use this fallback when the expected payout count
    # makes the grouping unambiguous.
    if not groups and expected_count:
        nums = _ints(text)
        if len(nums) == expected_count * size:
            groups = [tuple(nums[i:i + size]) for i in range(0, len(nums), size)]

    result = []
    for group in groups:
        result.append(core.canonical_combo(bet, group))
    return result


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
        bet = None
        label_index = None
        for i, cell in enumerate(cells):
            found = _bet_in_cell(cell)
            if found:
                bet = found
                label_index = i
                current_bet = found
                break

        payouts = _yen_values(cells)
        if not payouts:
            # Keep current_bet across a rowspan label, but rows without yen are
            # not payout observations.
            continue
        if bet is None:
            bet = current_bet
        if bet is None:
            continue

        candidate_cells = cells[label_index + 1:] if label_index is not None else cells
        combo_text = _combo_cells(candidate_cells)
        combos = _combos(combo_text, bet, expected_count=len(payouts))

        if len(combos) != len(payouts):
            message = (
                f'{source_name or race_id}: row {row_no} {bet} could not pair '
                f'combinations={combos!r} with payouts={payouts!r}; cells={cells!r}'
            )
            warnings.append(message)
            if strict:
                raise ValueError(message)
            continue

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
