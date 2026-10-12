from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import wide_odds_runtime as wide


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RACEDATA_PATH = REPO / "RaceData.json"
TARGET_DATE8 = os.environ.get("KEIBAAI_LIVE_DATE", "").strip() or datetime.now(JST).strftime("%Y%m%d")


def _date_text() -> str:
    if len(TARGET_DATE8) != 8 or not TARGET_DATE8.isdigit():
        raise RuntimeError(f"invalid target date: {TARGET_DATE8!r}")
    return f"{TARGET_DATE8[:4]}-{TARGET_DATE8[4:6]}-{TARGET_DATE8[6:8]}"


def _post_time(race: dict) -> datetime | None:
    try:
        return datetime.strptime(
            f"{TARGET_DATE8} {race.get('startTime')}", "%Y%m%d %H:%M"
        ).replace(tzinfo=JST)
    except Exception:
        return None


def _load_races() -> list[dict]:
    if not RACEDATA_PATH.exists():
        return []
    try:
        data = json.loads(RACEDATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    wanted = _date_text()
    return [r for r in data if str(r.get("raceDate") or "") == wanted]


def main() -> None:
    now = datetime.now(JST)
    repaired = 0
    waiting = 0
    skipped_post = 0

    for race in _load_races():
        rid = str(race.get("raceID") or "").strip()
        if not rid:
            continue
        existing = wide.first_snapshot_for_race(rid)
        if existing is not None:
            # Ensure the immutable historical copy exists even if an older
            # runtime created only WideOddsData.json.
            wide._archive_snapshot(existing)
            continue

        post = _post_time(race)
        if post is None or now >= post:
            skipped_post += 1
            continue

        try:
            snap = wide.record_first_wide_snapshot(
                race_id=rid,
                race_date=str(race.get("raceDate") or _date_text()),
                venue=str(race.get("venue") or ""),
                race_number=int(race.get("raceNumber") or 0),
                start_time=str(race.get("startTime") or ""),
            )
            repaired += 1
            print(
                "WIDE REPAIRED:", rid,
                "captured=", snap.get("capturedAtJST"),
                "minutes_to_post=", snap.get("minutesToPost"),
                "pairs=", snap.get("pairCount"),
            )
        except Exception as exc:
            waiting += 1
            print("WIDE REPAIR WAIT:", rid, repr(exc))

    if repaired:
        try:
            import build_signal_data_v2 as signals
            signals.main()
            print("SignalData v2 refreshed after Wide repair.")
        except Exception as exc:
            print("WARNING: signal refresh after Wide repair failed:", repr(exc))

    print("wide repaired:", repaired)
    print("wide still waiting:", waiting)
    print("wide skipped after post:", skipped_post)


if __name__ == "__main__":
    main()
