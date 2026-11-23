"""Bounded retry eligibility for pre-post win-odds collection.

Only upstream odds transport, missing rows and source freshness failures can
be retried. Model, feature, publication and archival exceptions remain fatal.
The caller enforces the original formal prediction window (T-12..T-8).
"""
from __future__ import annotations

from win_odds_freshness import WinOddsFreshnessError


class WinOddsFetchError(RuntimeError):
    """Missing or unusable upstream win odds, potentially temporary."""


def retryable_win_odds_failure(exc: BaseException) -> bool:
    return isinstance(exc, (WinOddsFreshnessError, WinOddsFetchError))
