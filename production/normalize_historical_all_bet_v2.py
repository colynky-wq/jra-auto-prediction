"""Fail-closed historical odds normalizer with unavailable-row prefiltering.

The collector can retain placeholder rows whose quote is blank/unavailable and
whose combination columns are also blank.  The original normalizer tried to
canonicalize the combination before checking quote availability, so such rows
could raise a misleading ``missing historical column`` error even though they
should simply be ignored.

This wrapper changes only that ordering:
1. identify race/pool,
2. inspect final/range quote,
3. skip rows with no usable quote,
4. only then require/canonicalize the combination.

Rows that have a usable quote but incomplete combination data still fail
closed.  Forward files are never read or written.
"""
from __future__ import annotations

from typing import Iterable

import normalize_historical_all_bet as base
import research_all_bet_retrospective as core


def normalize_odds_rows(rows: Iterable[dict], source_name: str = '') -> list[dict]:
    out = []
    seen = set()

    for row in rows:
        rid = str(base._first(row, ('レースID', 'raceID', 'race_id'), True)).strip()
        bet = core.canonical_bet(base._first(row, ('券種', 'betType', 'bet_type'), True))

        # Decide whether this is a usable quoted outcome BEFORE requiring its
        # combination columns. Placeholder/no-vote rows are intentionally
        # ignored by the retrospective study.
        direct = core._number(base._first(row, base.DIRECT_ODDS), positive=True)
        low = core._number(base._first(row, base.LOW_ODDS), positive=True)
        high = core._number(base._first(row, base.HIGH_ODDS), positive=True)

        if direct is not None:
            low = high = direct
            basis = 'exact_quote'
        elif low is not None:
            if high is None:
                high = low
            if high < low:
                raise ValueError(f'odds range reversed: {source_name or rid}/{rid}/{bet}')
            basis = 'lower_bound_conservative'
        else:
            # Includes blank 枠連 cells, 票数なし, 発売なし, and other
            # unavailable quotes. Combination fields may legitimately be blank.
            continue

        try:
            combo = core.canonical_combo(bet, base._parts(row, bet))
        except Exception as exc:
            raise ValueError(
                f'{source_name or rid}: quoted row has invalid/missing combination '
                f'for {rid}/{bet}: {exc}'
            ) from exc

        key = (rid, bet, combo)
        if key in seen:
            raise ValueError(f'{source_name or rid}: duplicate normalized odds: {key}')
        seen.add(key)

        out.append({
            'レースID': rid,
            '券種': bet,
            '組合せ': combo,
            '最終オッズ': low,
            '最終オッズ上限_監査用': high,
            'オッズ採用基準': basis,
            'source_file': source_name,
        })

    return out
