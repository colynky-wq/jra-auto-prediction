from __future__ import annotations

FORMAL_TIMING_MARKER = "live_10min"
RECOVERY_TIMING_MARKER = "live_late_recovery"
CAPTURE_TIMING_MARKERS = frozenset({FORMAL_TIMING_MARKER, RECOVERY_TIMING_MARKER})


def validate_capture_windows(formal_early: float, formal_late: float, recovery_late: float) -> None:
    """Fail closed when the live-capture windows are configured inconsistently."""
    if not (0.0 <= recovery_late < formal_late < formal_early):
        raise ValueError(
            "capture windows must satisfy 0 <= recovery_late < formal_late < formal_early; "
            f"got recovery_late={recovery_late}, formal_late={formal_late}, formal_early={formal_early}"
        )


def classify_capture_timing(
    remaining_minutes: float,
    *,
    formal_early: float,
    formal_late: float,
    recovery_late: float,
) -> str | None:
    """Classify a trigger without ever disguising a late recovery as formal T-10.

    The formal window is inclusive at both edges.  The recovery window starts
    immediately below the formal window and is also inclusive at its late edge.
    Outside those windows no capture should be attempted.
    """
    validate_capture_windows(formal_early, formal_late, recovery_late)
    remaining = float(remaining_minutes)
    if remaining > formal_early or remaining < recovery_late:
        return None
    if remaining >= formal_late:
        return FORMAL_TIMING_MARKER
    return RECOVERY_TIMING_MARKER


def is_formal_timing(value: object) -> bool:
    return value == FORMAL_TIMING_MARKER
