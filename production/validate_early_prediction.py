from __future__ import annotations

"""Fail closed when an early prediction snapshot is incomplete.

The early builder intentionally catches per-race fetch/prediction errors so it can
print useful diagnostics. This validator runs immediately after the build and
before RaceData publication, preventing a partial race day from replacing the
last known-good app feed.
"""

import json
import os
from pathlib import Path

import live_monitor_dry_run as base


ROOT = Path(__file__).resolve().parent.parent
EARLY_PATH = ROOT / "EarlyPredictionData.json"


def _expected_flat_ids(date8: str) -> list[str]:
    races = base.fetch_schedule(date8)
    return [
        str(race["race_id"])
        for race in races
        if race.get("surface") != "障" and "障害" not in str(race.get("race_name", ""))
    ]


def main() -> None:
    date8 = os.environ.get("KEIBAAI_EARLY_DATE", "").strip()
    stage = os.environ.get("KEIBAAI_EARLY_STAGE", "").strip()
    if len(date8) != 8 or not date8.isdigit():
        raise RuntimeError(f"invalid KEIBAAI_EARLY_DATE: {date8!r}")
    if stage not in {"previous_evening", "morning"}:
        raise RuntimeError(f"invalid KEIBAAI_EARLY_STAGE: {stage!r}")
    if not EARLY_PATH.exists():
        raise RuntimeError("EarlyPredictionData.json was not produced")

    payload = json.loads(EARLY_PATH.read_text(encoding="utf-8"))
    expected_date = f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"
    if payload.get("raceDate") != expected_date:
        raise RuntimeError(
            f"early snapshot date mismatch: {payload.get('raceDate')} != {expected_date}"
        )

    snapshots = payload.get("snapshots") or {}
    snap = snapshots.get(stage)
    if not isinstance(snap, dict):
        raise RuntimeError(f"early snapshot stage missing: {stage}")

    expected_ids = _expected_flat_ids(date8)
    races = snap.get("races") or []
    actual_ids = [str(race.get("raceID") or "") for race in races]

    expected = set(expected_ids)
    actual = set(actual_ids)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    duplicates = sorted({rid for rid in actual_ids if actual_ids.count(rid) > 1})
    empty_horses = sorted(
        str(race.get("raceID") or "")
        for race in races
        if len(race.get("horses") or []) < 2
    )

    print("EARLY_EXPECTED_FLAT", len(expected_ids))
    print("EARLY_ACTUAL_RACES", len(races))
    print("EARLY_MISSING", missing)
    print("EARLY_EXTRA", extra)
    print("EARLY_DUPLICATES", duplicates)
    print("EARLY_EMPTY_HORSES", empty_horses)

    if (
        len(races) != len(expected_ids)
        or missing
        or extra
        or duplicates
        or empty_horses
    ):
        raise RuntimeError(
            "early prediction completeness gate failed: "
            f"expected={len(expected_ids)} actual={len(races)} "
            f"missing={missing} extra={extra} duplicates={duplicates} "
            f"empty_horses={empty_horses}"
        )

    print("EARLY_PREDICTION_COMPLETENESS_OK", expected_date, stage, len(races))


if __name__ == "__main__":
    main()
