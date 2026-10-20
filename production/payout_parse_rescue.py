"""Fail-closed rescue for ambiguous concatenated JRA place payout numbers.

Some saved JRA result pages concatenate multiple 複勝 horse numbers without a
separator. A token such as ``6115`` can represent either ``6,11,5`` or
``6,1,15``. The base parser deliberately refuses to guess in that situation.

This wrapper keeps that fail-closed behavior. When the only parse warnings are
ambiguous 複勝 rows, it corroborates candidate splits with independent evidence
from the same saved result page:

* rows that the base parser already normalized successfully (単勝/馬連/馬単/
  ワイド/3連複/3連単), and
* the official result table headed by ``着順`` and ``馬番`` when that table is
  recoverable from the saved HTML.

A rescue is accepted only when exactly one candidate split is supported by the
available evidence. Otherwise the original exception is re-raised.
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
    values = []
    for raw in re.findall(r'(?<!\d)(\d{1,2})(?!\d)', base._clean(cell)):
        value = int(raw)
        if low <= value <= high:
            values.append(value)
    unique = list(dict.fromkeys(values))
    return unique[0] if len(unique) == 1 else None


def _result_table_hint_sets(html: str, expected_count: int) -> set[frozenset[int]]:
    """Read place-winning horse numbers from a conventional result table."""
    if expected_count <= 0:
        return set()

    rows = base.html_table_rows(html)
    hints: set[frozenset[int]] = set()
    rank_limit = 1 if expected_count == 1 else 2 if expected_count == 2 else 3

    for header_index, header in enumerate(rows):
        keys = [_header_key(cell) for cell in header]
        rank_candidates = [i for i, key in enumerate(keys) if key == '着順' or key.startswith('着順')]
        horse_candidates = [i for i, key in enumerate(keys) if key == '馬番' or key.startswith('馬番')]
        if not rank_candidates or not horse_candidates:
            continue
        rank_col = rank_candidates[0]
        horse_col = horse_candidates[0]
        winners: list[int] = []

        for cells in rows[header_index + 1:]:
            next_keys = [_header_key(cell) for cell in cells]
            if any(key == '着順' or key.startswith('着順') for key in next_keys) and any(
                key == '馬番' or key.startswith('馬番') for key in next_keys
            ):
                break
            if rank_col >= len(cells) or horse_col >= len(cells):
                continue

            rank = _one_small_int(cells[rank_col], low=1, high=99)
            horse = _one_small_int(cells[horse_col], low=1, high=18)
            if rank is None or horse is None:
                continue
            if rank > rank_limit:
                if winners:
                    break
                continue
            if horse not in winners:
                winners.append(horse)

        if len(winners) == expected_count:
            hints.add(frozenset(winners))

    return hints


def _other_pool_hint_sets(html: str, expected_count: int) -> set[frozenset[int]]:
    """Derive complete winner sets from independently parsed other pools."""
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
    semantic_sizes = {'馬連': 2, '馬単': 2, '3連複': 3, '3連単': 3}
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


def _parsed_pool_groups(loose_rows: list[dict]) -> dict[str, list[tuple[int, ...]]]:
    """Return normalized winning combinations already accepted by the base parser."""
    grouped: dict[str, list[tuple[int, ...]]] = defaultdict(list)
    for row in loose_rows:
        bet = str(row.get('券種') or '')
        if bet == '複勝':
            continue
        numbers = _combo_numbers(str(row.get('組合せ') or ''))
        if numbers:
            grouped[bet].append(numbers)
    return dict(grouped)


def _candidate_matches_parsed_evidence(candidate: tuple[int, ...], loose_rows: list[dict]) -> tuple[bool, int]:
    """Check candidate against strong partial/complete evidence from parsed pools.

    For an ordinary three-place race, a single 馬単 winner fixes the first two
    horses in order, while a single 馬連 winner fixes the same pair unordered.
    These constraints are sufficient to distinguish e.g. 6,11,5 from 6,1,15.
    We only apply a pool when its normalized winning combination is unique, so
    dead-heat multi-winner layouts remain fail-closed instead of being guessed.
    """
    groups = _parsed_pool_groups(loose_rows)
    checks: list[bool] = []

    win = groups.get('単勝', [])
    if len(win) == 1 and len(win[0]) == 1 and candidate:
        checks.append(candidate[0] == win[0][0])

    if len(candidate) >= 2:
        exacta = groups.get('馬単', [])
        if len(exacta) == 1 and len(exacta[0]) == 2:
            checks.append(tuple(candidate[:2]) == tuple(exacta[0]))

        quinella = groups.get('馬連', [])
        if len(quinella) == 1 and len(quinella[0]) == 2:
            checks.append(frozenset(candidate[:2]) == frozenset(quinella[0]))

    if len(candidate) >= 3:
        trifecta = groups.get('3連単', [])
        if len(trifecta) == 1 and len(trifecta[0]) == 3:
            checks.append(tuple(candidate[:3]) == tuple(trifecta[0]))

        trio = groups.get('3連複', [])
        if len(trio) == 1 and len(trio[0]) == 3:
            checks.append(frozenset(candidate[:3]) == frozenset(trio[0]))

        wide = groups.get('ワイド', [])
        if wide and all(len(group) == 2 for group in wide):
            wide_union = set().union(*(set(group) for group in wide))
            if len(wide_union) == 3:
                checks.append(frozenset(candidate[:3]) == frozenset(wide_union))

    return (all(checks), len(checks)) if checks else (False, 0)


def _hint_sets(html: str, expected_count: int) -> set[frozenset[int]]:
    return _other_pool_hint_sets(html, expected_count) | _result_table_hint_sets(html, expected_count)


def _recover_place_rows(html: str, race_id: str, source_name: str, loose_rows: list[dict]):
    recovered: list[dict] = []
    recovered_segments = 0
    parsed_evidence_used = 0

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
        if not candidates:
            continue

        # First prefer combinations the base parser already accepted. This is
        # stronger than reparsing the raw HTML again because loose_rows are the
        # exact normalized rows that survived parsing for this same file.
        parsed_matches = []
        evidence_counts = []
        for candidate in candidates:
            ok, count = _candidate_matches_parsed_evidence(candidate, loose_rows)
            if ok and count > 0:
                parsed_matches.append(candidate)
                evidence_counts.append(count)

        matched: list[tuple[int, ...]] = []
        if len(parsed_matches) == 1:
            matched = parsed_matches
            parsed_evidence_used += max(evidence_counts or [0])
        else:
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

    return recovered, recovered_segments, parsed_evidence_used


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

        recovered, segment_count, evidence_count = _recover_place_rows(
            html, str(race_id), source_name, list(loose_rows)
        )
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
            'parsed_pool_evidence_count': evidence_count,
            'result_table_hint_count': len(_result_table_hint_sets(html, len(recovered))),
        })
        return merged, audit
