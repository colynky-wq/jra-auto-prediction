"""Fail-closed rescue for ambiguous concatenated JRA place payout numbers.

Some saved JRA result pages concatenate multiple 複勝 horse numbers without a
separator. A token such as ``6115`` can represent either ``6,11,5`` or
``6,1,15``. The base parser deliberately refuses to guess in that situation.

This wrapper keeps that fail-closed behavior. When the only parse warnings are
ambiguous 複勝 rows, it corroborates candidate splits with two independent
sources on the same saved result page:

* winning combinations from other pools (3連単/3連複/ワイド/馬単/馬連), and
* the official result table headed by ``着順`` and ``馬番``.

A rescue is accepted only when exactly one candidate split matches a
corroborating winner set. Otherwise the original exception is re-raised.
"""
from __future__ import annotations

from collections import defaultdict
import re

import normalize_historical_payouts as base
import research_all_bet_retrospective as core

_ORIGINAL_PARSE = base.parse_payout_html


def _segments(html: str):
    """Yield (row_no, bet, segment_cells) for labelled payout blocks."""
    for row_no, cells in enumerate(base.html_table_rows(html), 1):
        labels = []
        for i, cell in enumerate(cells):
            found = base._bet_in_cell(cell)
            if found:
                labels.append((i, found))
        for j, (label_index, bet) in enumerate(labels):
            end = labels[j + 1][0] if j + 1 < len(labels) else len(cells)
            yield row_no, bet, cells[label_index + 1:end]


def _combo_numbers(combo: str) -> tuple[int, ...]:
    nums = []
    for raw in re.findall(r'\d{1,2}', str(combo)):
        value = int(raw)
        if 1 <= value <= 18:
            nums.append(value)
    return tuple(nums)


def _singleton_solutions(text: str, expected_count: int) -> list[tuple[int, ...]]:
    """Return every distinct-number split of a compact digit string into 1..18."""
    compact = re.sub(r'[\s|,、/]+', '', base._clean(text))
    if expected_count <= 0 or not compact.isdigit():
        return []

    solutions: list[tuple[int, ...]] = []

    def walk(pos: int, remaining: int, values: tuple[int, ...]):
        if remaining == 0:
            if pos == len(compact) and len(values) == len(set(values)):
                if values not in solutions:
                    solutions.append(values)
            return
        digits_left = len(compact) - pos
        if digits_left < remaining or digits_left > remaining * 2:
            return
        for width in (1, 2):
            token = compact[pos:pos + width]
            if len(token) != width or not token.isdigit() or token.startswith('0'):
                continue
            value = int(token)
            if not 1 <= value <= 18 or value in values:
                continue
            walk(pos + width, remaining - 1, values + (value,))

    walk(0, expected_count, ())
    return solutions


def _header_key(cell: str) -> str:
    return re.sub(r'\s+', '', base._clean(cell))


def _one_small_int(cell: str, *, low: int, high: int):
    """Return one unambiguous integer embedded in a table cell, else None."""
    values = []
    for raw in re.findall(r'(?<!\d)(\d{1,2})(?!\d)', base._clean(cell)):
        value = int(raw)
        if low <= value <= high:
            values.append(value)
    unique = list(dict.fromkeys(values))
    return unique[0] if len(unique) == 1 else None


def _result_table_hint_sets(html: str, expected_count: int) -> set[frozenset[int]]:
    """Read place-winning horse numbers from the official result table.

    JRA result pages contain a table whose header includes ``着順`` and ``馬番``.
    For ordinary fields 複勝 pays through 3rd place; for small fields it can pay
    through 2nd. Dead heats can therefore produce more than three payout rows,
    but all place-winning rows still have an official finish rank within the
    applicable place cutoff.
    """
    if expected_count <= 0:
        return set()

    rows = base.html_table_rows(html)
    hints: set[frozenset[int]] = set()
    rank_limit = 1 if expected_count == 1 else 2 if expected_count == 2 else 3

    for header_index, header in enumerate(rows):
        keys = [_header_key(cell) for cell in header]
        if '着順' not in keys or '馬番' not in keys:
            continue
        rank_col = keys.index('着順')
        horse_col = keys.index('馬番')
        winners: list[int] = []

        for cells in rows[header_index + 1:]:
            next_keys = [_header_key(cell) for cell in cells]
            if '着順' in next_keys and '馬番' in next_keys:
                break
            if rank_col >= len(cells) or horse_col >= len(cells):
                continue

            rank = _one_small_int(cells[rank_col], low=1, high=99)
            horse = _one_small_int(cells[horse_col], low=1, high=18)
            if rank is None or horse is None:
                continue
            if rank > rank_limit:
                # Result rows are ordered by finish. Once a later placing is
                # reached, no further row can belong to the place payout set.
                if winners:
                    break
                continue
            if horse not in winners:
                winners.append(horse)

        if len(winners) == expected_count:
            hints.add(frozenset(winners))

    return hints


def _other_pool_hint_sets(html: str, expected_count: int) -> set[frozenset[int]]:
    """Derive corroborating winner sets from independently parsed other pools."""
    by_bet: dict[str, list[set[int]]] = defaultdict(list)

    for _row_no, bet, cells in _segments(html):
        if bet == '複勝':
            continue
        payouts = base._yen_values(cells)
        if not payouts:
            continue
        combos = base._combos(base._combo_cells(cells), bet, expected_count=len(payouts))
        if len(combos) != len(payouts):
            continue
        for combo in combos:
            numbers = set(_combo_numbers(combo))
            if numbers:
                by_bet[bet].append(numbers)

    hints: set[frozenset[int]] = set()
    semantic_sizes = {
        '馬連': 2,
        '馬単': 2,
        '3連複': 3,
        '3連単': 3,
    }
    for bet, groups in by_bet.items():
        native_size = semantic_sizes.get(bet)
        if native_size == expected_count:
            for group in groups:
                if len(group) == expected_count:
                    hints.add(frozenset(group))
        union = set().union(*groups) if groups else set()
        if len(union) == expected_count:
            hints.add(frozenset(union))

    wide_groups = by_bet.get('ワイド', [])
    if wide_groups:
        union = set().union(*wide_groups)
        if len(union) == expected_count:
            hints.add(frozenset(union))

    return hints


def _hint_sets(html: str, expected_count: int) -> set[frozenset[int]]:
    return _other_pool_hint_sets(html, expected_count) | _result_table_hint_sets(html, expected_count)


def _recover_place_rows(html: str, race_id: str, source_name: str):
    recovered: list[dict] = []
    recovered_segments = 0

    for _row_no, bet, cells in _segments(html):
        if bet != '複勝':
            continue
        payouts = base._yen_values(cells)
        if not payouts:
            continue
        combo_text = base._combo_cells(cells)
        direct = base._combos(combo_text, bet, expected_count=len(payouts))
        if len(direct) == len(payouts):
            continue

        candidates = _singleton_solutions(combo_text, len(payouts))
        hints = _hint_sets(html, len(payouts))
        matched = [candidate for candidate in candidates if frozenset(candidate) in hints]
        if len(matched) != 1:
            continue

        recovered_segments += 1
        for horse_no, amount in zip(matched[0], payouts):
            recovered.append({
                'レースID': race_id,
                '券種': '複勝',
                '組合せ': core.canonical_combo('複勝', (horse_no,)),
                '払戻円': amount,
                'source_file': source_name,
            })

    return recovered, recovered_segments


def parse_payout_html(html: str, race_id: str, source_name: str = '', *, strict: bool = True):
    """Run the original parser; rescue only uniquely corroborated 複勝 ambiguity."""
    if not strict:
        return _ORIGINAL_PARSE(html, race_id, source_name, strict=False)

    try:
        return _ORIGINAL_PARSE(html, race_id, source_name, strict=True)
    except ValueError as original_error:
        loose_rows, loose_audit = _ORIGINAL_PARSE(html, race_id, source_name, strict=False)
        warnings = [w for w in str(loose_audit.get('warnings') or '').split(' || ') if w]
        if not warnings or any(' 複勝 could not pair ' not in warning for warning in warnings):
            raise original_error

        recovered, segment_count = _recover_place_rows(html, str(race_id), source_name)
        if segment_count != len(warnings) or not recovered:
            raise original_error

        seen: dict[tuple[str, str, str], float] = {}
        merged: list[dict] = []
        for row in list(loose_rows) + recovered:
            key = (str(row['レースID']), str(row['券種']), str(row['組合せ']))
            amount = float(row['払戻円'])
            old = seen.get(key)
            if old is not None:
                if old != amount:
                    raise original_error
                continue
            seen[key] = amount
            merged.append(row)

        coverage = sorted({row['券種'] for row in merged}, key=core.ALL_BETS.index)
        audit = dict(loose_audit)
        audit.update({
            'payout_rows': len(merged),
            'covered_bets': ','.join(coverage),
            'covered_bet_count': len(coverage),
            'warning_count': 0,
            'warnings': '',
            'rescued_place_segments': segment_count,
            'result_table_hint_count': len(_result_table_hint_sets(html, len(recovered))),
        })
        return merged, audit
