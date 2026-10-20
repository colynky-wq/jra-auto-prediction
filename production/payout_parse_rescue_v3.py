"""Final fail-closed payout parser wrapper for compact JRA result cells.

This layer adds one guard in front of the v2 rescue parser: canonicalization of
candidate combinations must never abort parsing merely because an ambiguous
compact split temporarily creates an impossible same-horse combination such as
``11-11`` for ワイド.  Such candidates are discarded, which turns the row into
an ordinary cardinality mismatch.  The v2 rescue can then use independent
3連複/3連単/result-table evidence to recover the unique valid wide layout.

枠連 is intentionally left alone: same-frame combinations can be valid when a
frame contains multiple horses.  Other pair/triple horse pools reject repeated
horse numbers through ``core.canonical_combo``.
"""
from __future__ import annotations

import normalize_historical_payouts as base
import payout_parse_rescue_v2 as v2
import research_all_bet_retrospective as core


def _canonicalize_valid(bet: str, groups) -> list[str]:
    out: list[str] = []
    for group in groups:
        try:
            combo = core.canonical_combo(bet, group)
        except ValueError:
            # Ambiguous compact parsing can temporarily yield impossible horse
            # combinations (e.g. ワイド 11-11).  Do not promote them and do not
            # abort the whole page; leaving fewer combos than payouts lets the
            # strict parser emit a normal fail-closed warning for v2 to rescue.
            continue
        if combo not in out:
            out.append(combo)
    return out


def _guarded_combos(text: str, bet: str, expected_count: int | None = None) -> list[str]:
    text = base._clean(text)
    size = 1 if bet in {'単勝', '複勝'} else 3 if bet in base.TRIPLE_BETS else 2

    if expected_count:
        exact = base._compact_exact_groups(text, size, expected_count)
        if exact:
            # If the unique digit split contains an impossible group, return
            # only the valid portion. _append_pool_segment will detect that the
            # payout cardinality no longer matches and route it to rescue.
            return _canonicalize_valid(bet, exact)

    if size == 1:
        return _canonicalize_valid(bet, ((n,) for n in base._ints(text)))

    return _canonicalize_valid(bet, base._separated_groups(text, size))


# v2 captures base.parse_payout_html as its original parser. That function
# resolves _combos from the normalize_historical_payouts module at call time,
# so replacing this helper is sufficient without changing the tested base
# parser or duplicating the v2 rescue logic.
base._combos = _guarded_combos


def parse_payout_html(html: str, race_id: str, source_name: str = '', *, strict: bool = True):
    return v2.parse_payout_html(html, race_id, source_name, strict=strict)
