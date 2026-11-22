"""Final fail-closed rescue for compact JRA payout cells.

v3 already guards invalid transient compact parses (for example ワイド 11-11).
This layer adds ordered evidence from already-parsed official payout pools.

Two real archive ambiguities require order, not just an unordered top-three set:

* 複勝 ``7111`` can be ``7,11,1`` or ``7,1,11``.
* ワイド ``7-111-71-11`` can be segmented in more than one pair order.

A unique 3連単 (or, when sufficient, 馬単 + 3連複) fixes the official top-three
order.  JRA's ordinary three-payout ワイド row is then interpreted in finish
pair order: 1st-2nd, 1st-3rd, 2nd-3rd.  Rescue remains fail-closed whenever
ordered evidence is missing, non-unique, or inconsistent with the compact cell.
"""
from __future__ import annotations

import normalize_historical_payouts as base
import payout_parse_rescue as place_rescue
import payout_parse_rescue_v2 as v2
import payout_parse_rescue_v3 as v3
import research_all_bet_retrospective as core


def _ordered_pool_hints(loose_rows: list[dict], expected_count: int) -> set[tuple[int, ...]]:
    """Return finish-order hints proven by unique ordered payout pools."""
    groups = place_rescue._parsed_pool_groups(loose_rows)
    hints: set[tuple[int, ...]] = set()

    if expected_count == 1:
        win = groups.get('単勝', [])
        if len(win) == 1 and len(win[0]) == 1:
            hints.add(tuple(win[0]))
        return hints

    if expected_count == 2:
        exacta = groups.get('馬単', [])
        if len(exacta) == 1 and len(exacta[0]) == 2:
            hints.add(tuple(exacta[0]))
        return hints

    if expected_count == 3:
        trifecta = groups.get('3連単', [])
        if len(trifecta) == 1 and len(trifecta[0]) == 3:
            hints.add(tuple(trifecta[0]))

        exacta = groups.get('馬単', [])
        trio = groups.get('3連複', [])
        if len(exacta) == 1 and len(exacta[0]) == 2 and len(trio) == 1 and len(trio[0]) == 3:
            first_two = tuple(exacta[0])
            remaining = [n for n in trio[0] if n not in set(first_two)]
            if len(remaining) == 1:
                hints.add(first_two + (remaining[0],))

    return hints


def _ordered_top3_hints(html: str, evidence_rows: list[dict]) -> set[tuple[int, int, int]]:
    hints = {
        tuple(values) for values in _ordered_pool_hints(evidence_rows, 3)
        if len(values) == 3
    }
    hints |= {
        tuple(values) for values in place_rescue._result_table_ordered_hints(html, 3)
        if len(values) == 3
    }
    return hints


def _expected_wide_sequence(top3: tuple[int, int, int]) -> tuple[tuple[int, int], ...]:
    a, b, c = top3
    return (
        tuple(sorted((a, b))),
        tuple(sorted((a, c))),
        tuple(sorted((b, c))),
    )


def _recover_wide_rows(html: str, race_id: str, source_name: str, loose_rows: list[dict]):
    recovered: list[dict] = []
    recovered_segments = 0
    evidence_count = 0
    ordered_hints = _ordered_top3_hints(html, loose_rows)

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

        if len(payouts) != 3:
            continue

        candidates = v2._wide_pair_solutions(combo_text, len(payouts))
        ordered_matches = []
        for candidate in candidates:
            for top3 in ordered_hints:
                if tuple(candidate) == _expected_wide_sequence(top3):
                    ordered_matches.append(candidate)
                    break

        # If ordered evidence does not uniquely resolve the row, retain the v2
        # unordered rescue as a safe fallback for cases already handled there.
        chosen = None
        if len(ordered_matches) == 1:
            chosen = ordered_matches[0]
            evidence_count += 1
        else:
            fallback_rows, fallback_segments, fallback_evidence = v2._recover_wide_rows(
                html, race_id, source_name, loose_rows
            )
            if fallback_segments == 1 and fallback_rows:
                recovered.extend(fallback_rows)
                recovered_segments += 1
                evidence_count += fallback_evidence
                continue

        if chosen is None:
            continue

        recovered_segments += 1
        for pair, amount in zip(chosen, payouts):
            recovered.append({
                'レースID': race_id,
                '券種': 'ワイド',
                '組合せ': core.canonical_combo('ワイド', pair),
                '払戻円': amount,
                'source_file': source_name,
            })

    return recovered, recovered_segments, evidence_count


def _recover_place_rows(html: str, race_id: str, source_name: str, evidence_rows: list[dict]):
    recovered: list[dict] = []
    recovered_segments = 0
    evidence_count = 0

    for _row_no, bet, cells in place_rescue._segments(html):
        if bet != '複勝':
            continue
        payouts = base._yen_values(cells)
        if not payouts:
            continue

        combo_text = base._combo_cells(cells)
        direct = base._combos(combo_text, bet, expected_count=len(payouts))
        if len(direct) == len(payouts):
            continue

        candidates = place_rescue._singleton_solutions(combo_text, len(payouts))
        if not candidates:
            continue

        matched: list[tuple[int, ...]] = []

        ordered_pool_hints = _ordered_pool_hints(evidence_rows, len(payouts))
        ordered_pool_matches = [c for c in candidates if tuple(c) in ordered_pool_hints]
        if len(ordered_pool_matches) == 1:
            matched = ordered_pool_matches
            evidence_count += 1

        if not matched:
            ordered_table_hints = place_rescue._result_table_ordered_hints(html, len(payouts))
            table_matches = [c for c in candidates if tuple(c) in ordered_table_hints]
            if len(table_matches) == 1:
                matched = table_matches
                evidence_count += 1

        if not matched:
            parsed_matches = []
            parsed_counts = []
            for candidate in candidates:
                ok, count = place_rescue._candidate_matches_parsed_evidence(candidate, evidence_rows)
                if ok and count > 0:
                    parsed_matches.append(candidate)
                    parsed_counts.append(count)
            if len(parsed_matches) == 1:
                matched = parsed_matches
                evidence_count += max(parsed_counts or [0])

        if not matched:
            hints = place_rescue._hint_sets(html, len(payouts))
            unordered_matches = [c for c in candidates if frozenset(c) in hints]
            if len(unordered_matches) == 1:
                matched = unordered_matches
                evidence_count += 1

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

    return recovered, recovered_segments, evidence_count


def _merge(rows: list[dict], original_error: Exception) -> list[dict]:
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
        return v3.parse_payout_html(html, race_id, source_name, strict=False)

    try:
        return v3.parse_payout_html(html, race_id, source_name, strict=True)
    except ValueError as original_error:
        loose_rows, loose_audit = v3.parse_payout_html(html, race_id, source_name, strict=False)
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
            place_rows, place_segments, place_evidence = _recover_place_rows(
                html, str(race_id), source_name, evidence_rows
            )
            if place_segments != len(place_warnings) or not place_rows:
                raise original_error

        merged = _merge(evidence_rows + place_rows, original_error)
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
            'ordered_pool_evidence_count': place_evidence,
            'wide_ordered_evidence_count': wide_evidence,
        })
        return merged, audit
