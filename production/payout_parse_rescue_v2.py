"""Fail-closed rescue for jointly ambiguous JRA 複勝 and ワイド payout cells.

Real saved JRA result rows can concatenate multiple winning combinations in one
cell without a delimiter between adjacent combinations, for example:

    複勝  6115           -> 6 / 11 / 5
    ワイド 6-115-65-11  -> 6-11 / 5-6 / 5-11

The base parser deliberately refuses to guess when more than one digit split is
possible.  This wrapper keeps that contract and only rescues a row when other
already-normalized pools on the same result page uniquely identify the winning
horse set.  In particular, an ordinary three-payout ワイド row must resolve to
all three unordered pairs among the unique top-three set independently proven
by 3連複/3連単 or the official result table.  After ワイド is recovered, the
existing 複勝 rescue can use all normalized evidence.  Any unresolved or other
warning is re-raised.
"""
from __future__ import annotations

import itertools
import re

import normalize_historical_payouts as base
import payout_parse_rescue as place_rescue
import research_all_bet_retrospective as core

_ORIGINAL_PARSE = base.parse_payout_html


def _number_options(compact: str, pos: int):
    for width in (1, 2):
        token = compact[pos:pos + width]
        if len(token) != width or not token.isdigit() or token.startswith('0'):
            continue
        value = int(token)
        if 1 <= value <= 18:
            yield value, pos + width


def _wide_pair_solutions(text: str, expected_count: int) -> list[tuple[tuple[int, int], ...]]:
    """Return all complete compact parses into ``expected_count`` wide pairs."""
    if expected_count <= 0:
        return []
    compact = re.sub(r'[\s|,、/]+', '', base._clean(text))
    separators = {'-', '―', 'ー', '→', '⇒', '>'}
    raw_solutions: list[tuple[tuple[int, int], ...]] = []

    def one_pair(pos: int):
        for first, after_first in _number_options(compact, pos):
            if after_first >= len(compact) or compact[after_first] not in separators:
                continue
            for second, after_second in _number_options(compact, after_first + 1):
                if first != second:
                    yield (first, second), after_second

    def many(pos: int, groups_left: int, groups: tuple[tuple[int, int], ...] = ()):
        if groups_left == 0:
            if pos == len(compact):
                raw_solutions.append(groups)
            return
        for group, after in one_pair(pos):
            many(after, groups_left - 1, groups + (group,))

    many(0, expected_count)

    unique: list[tuple[tuple[int, int], ...]] = []
    for solution in raw_solutions:
        canonical = tuple(tuple(sorted(group)) for group in solution)
        # The same ワイド combination cannot have two independent payouts in
        # one ordinary row. Duplicate canonical pairs indicate a bad split.
        if len(set(canonical)) != len(canonical):
            continue
        if canonical not in unique:
            unique.append(canonical)
    return unique


def _top_three_hint_sets(html: str, loose_rows: list[dict]) -> set[frozenset[int]]:
    """Return independently proven three-horse winner sets."""
    hints: set[frozenset[int]] = set()
    grouped = place_rescue._parsed_pool_groups(loose_rows)

    trio = grouped.get('3連複', [])
    if len(trio) == 1 and len(trio[0]) == 3:
        hints.add(frozenset(trio[0]))

    trifecta = grouped.get('3連単', [])
    if len(trifecta) == 1 and len(trifecta[0]) == 3:
        hints.add(frozenset(trifecta[0]))

    hints |= place_rescue._result_table_hint_sets(html, 3)
    return hints


def _solution_matches_top_three(solution: tuple[tuple[int, int], ...], hint: frozenset[int]) -> bool:
    if len(solution) != 3 or len(hint) != 3:
        return False
    if set().union(*(set(pair) for pair in solution)) != set(hint):
        return False
    expected_pairs = {
        frozenset(pair) for pair in itertools.combinations(sorted(hint), 2)
    }
    actual_pairs = {frozenset(pair) for pair in solution}
    return actual_pairs == expected_pairs


def _recover_wide_rows(html: str, race_id: str, source_name: str, loose_rows: list[dict]):
    recovered: list[dict] = []
    recovered_segments = 0
    evidence_used = 0
    hints = _top_three_hint_sets(html, loose_rows)

    for _row_no, bet, cells in place_rescue._segments(html):
        if bet != 'ワイド':
            continue
        payouts = base._yen_values(cells)
        if not payouts:
            continue
        combo_text = base._combo_cells(cells)
        direct = base._combos(combo_text, bet, expected_count=len(payouts))
        if len(direct) == len(payouts):
            continue

        # Three ordinary wide payouts are exactly the three unordered pairs of
        # the top-three horse set. Other cardinalities (e.g. dead heats) remain
        # fail-closed until independently modelled.
        if len(payouts) != 3:
            continue
        candidates = _wide_pair_solutions(combo_text, len(payouts))
        matches = []
        for candidate in candidates:
            matching_hints = [hint for hint in hints if _solution_matches_top_three(candidate, hint)]
            if matching_hints:
                matches.append(candidate)
        if len(matches) != 1:
            continue

        chosen = matches[0]
        recovered_segments += 1
        evidence_used += 1
        for pair, amount in zip(chosen, payouts):
            recovered.append({
                'レースID': race_id,
                '券種': 'ワイド',
                '組合せ': core.canonical_combo('ワイド', pair),
                '払戻円': amount,
                'source_file': source_name,
            })

    return recovered, recovered_segments, evidence_used


def _merge_rows(rows: list[dict], original_error: Exception) -> list[dict]:
    seen: dict[tuple[str, str, str], float] = {}
    merged: list[dict] = []
    for row in rows:
        key = (str(row['レースID']), str(row['券種']), str(row['組合せ']))
        amount = float(row['払戻円'])
        old = seen.get(key)
        if old is not None:
            if old != amount:
                raise original_error
            continue
        seen[key] = amount
        merged.append(row)
    return merged


def parse_payout_html(html: str, race_id: str, source_name: str = '', *, strict: bool = True):
    if not strict:
        return _ORIGINAL_PARSE(html, race_id, source_name, strict=False)

    try:
        return _ORIGINAL_PARSE(html, race_id, source_name, strict=True)
    except ValueError as original_error:
        loose_rows, loose_audit = _ORIGINAL_PARSE(html, race_id, source_name, strict=False)
        warnings = [w for w in str(loose_audit.get('warnings') or '').split(' || ') if w]
        if not warnings:
            raise original_error

        place_warnings = [w for w in warnings if ' 複勝 could not pair ' in w]
        wide_warnings = [w for w in warnings if ' ワイド could not pair ' in w]
        if len(place_warnings) + len(wide_warnings) != len(warnings):
            raise original_error

        wide_rows: list[dict] = []
        wide_segments = wide_evidence = 0
        if wide_warnings:
            wide_rows, wide_segments, wide_evidence = _recover_wide_rows(
                html, str(race_id), source_name, list(loose_rows)
            )
            if wide_segments != len(wide_warnings) or not wide_rows:
                raise original_error

        evidence_rows = list(loose_rows) + wide_rows

        place_rows: list[dict] = []
        place_segments = place_evidence = 0
        if place_warnings:
            place_rows, place_segments, place_evidence = place_rescue._recover_place_rows(
                html, str(race_id), source_name, evidence_rows
            )
            if place_segments != len(place_warnings) or not place_rows:
                raise original_error

        merged = _merge_rows(evidence_rows + place_rows, original_error)
        coverage = sorted({row['券種'] for row in merged}, key=core.ALL_BETS.index)
        audit = dict(loose_audit)
        audit.update({
            'payout_rows': len(merged),
            'covered_bets': ','.join(coverage),
            'covered_bet_count': len(coverage),
            'warning_count': 0,
            'warnings': '',
            'rescued_place_segments': place_segments,
            'rescued_wide_segments': wide_segments,
            'parsed_pool_evidence_count': place_evidence,
            'wide_top3_evidence_count': wide_evidence,
        })
        return merged, audit
