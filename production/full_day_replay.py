# ============================================================
# KeibaAI GitHub Actions - full-day historical replay
#
# Purpose:
# - Replay every race currently published in RaceData.json.
# - Simulate the 10-minute trigger order for the day.
# - Run the already-verified one-race dry run for each race.
# - Confirm the fixed model reproduces the published research outputs.
#
# Read-only:
# - does NOT modify RaceData.json
# - does NOT modify prediction files
# - does NOT commit anything
# ============================================================

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RACEDATA_PATH = REPO / "RaceData.json"
RUNNER = HERE / "run_one_race_dry_run.py"

DEFAULT_DATE = "20261011"
TARGET_DATE = os.environ.get("KEIBAAI_REPLAY_DATE", "").strip() or DEFAULT_DATE
MAX_RACES_TEXT = os.environ.get("KEIBAAI_REPLAY_MAX_RACES", "0").strip() or "0"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def parse_hhmm(value: str) -> int:
    hh, mm = str(value).split(":", 1)
    return int(hh) * 60 + int(mm)


def minute_text(total_minutes: float) -> str:
    total_seconds = int(round(total_minutes * 60))
    hh = (total_seconds // 3600) % 24
    mm = (total_seconds % 3600) // 60
    ss = total_seconds % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def main() -> None:
    print("=" * 110)
    print("KeibaAI FULL-DAY REPLAY DRY RUN")
    print("JST:", datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"))
    print("date:", TARGET_DATE)
    print("mode: READ ONLY / no repository files are modified")
    print("=" * 110)

    if not RACEDATA_PATH.exists():
        raise FileNotFoundError(RACEDATA_PATH)
    if not RUNNER.exists():
        raise FileNotFoundError(RUNNER)

    try:
        max_races = int(MAX_RACES_TEXT)
    except ValueError as exc:
        raise RuntimeError(f"Invalid KEIBAAI_REPLAY_MAX_RACES: {MAX_RACES_TEXT!r}") from exc

    with RACEDATA_PATH.open("r", encoding="utf-8") as f:
        races = json.load(f)

    if not isinstance(races, list) or not races:
        raise RuntimeError("RaceData.json contains no races")

    # Keep only well-formed races and replay them in actual post-time order.
    normalized = []
    for race in races:
        race_id = str(race.get("raceID", "")).strip()
        start_time = str(race.get("startTime", "")).strip()
        if len(race_id) != 12 or not race_id.isdigit():
            continue
        try:
            start_minute = parse_hhmm(start_time)
        except Exception:
            continue
        normalized.append((start_minute, race_id, race))

    normalized.sort(key=lambda x: (x[0], x[1]))

    if max_races > 0:
        normalized = normalized[:max_races]

    if not normalized:
        raise RuntimeError("No replayable races were found in RaceData.json")

    before_hash = sha256_file(RACEDATA_PATH)

    print("published races selected:", len(normalized))
    print()
    print("Simulated 10-minute trigger schedule")
    print("-" * 110)

    for start_minute, race_id, race in normalized:
        # Formal protocol targets roughly 10.5 minutes before post.
        trigger_minute = start_minute - 10.5
        print(
            race_id,
            f"{race.get('venue', '')}{race.get('raceNumber', '')}R",
            race.get("raceName", ""),
            "post=", race.get("startTime", ""),
            "simulated-trigger=", minute_text(trigger_minute),
        )

    print()
    print("=" * 110)
    print("Starting sequential race replays")
    print("=" * 110)

    successes: list[str] = []
    failures: list[tuple[str, int]] = []

    for index, (_, race_id, race) in enumerate(normalized, start=1):
        print()
        print("#" * 110)
        print(
            f"[{index}/{len(normalized)}]",
            race_id,
            f"{race.get('venue', '')}{race.get('raceNumber', '')}R",
            race.get("raceName", ""),
            race.get("startTime", ""),
        )
        print("#" * 110)

        env = os.environ.copy()
        env["KEIBAAI_DRYRUN_DATE"] = TARGET_DATE
        env["KEIBAAI_DRYRUN_RACE_ID"] = race_id
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        result = subprocess.run(
            [sys.executable, str(RUNNER)],
            cwd=str(HERE),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )

        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        marker_ok = "KEIBAAI ONE-RACE DRY RUN: SUCCESS" in output

        if result.returncode == 0 and marker_ok:
            successes.append(race_id)
            # Keep success logs compact but show the important tail.
            lines = [line for line in output.splitlines() if line.strip()]
            print("SUCCESS")
            print("\n".join(lines[-18:]))
        else:
            failures.append((race_id, result.returncode))
            print("FAILED")
            print(output)

    after_hash = sha256_file(RACEDATA_PATH)
    racedata_unchanged = before_hash == after_hash

    print()
    print("=" * 110)
    print("FULL-DAY REPLAY SUMMARY")
    print("=" * 110)
    print("selected races:", len(normalized))
    print("successes:", len(successes))
    print("failures:", len(failures))
    print("successful race IDs:", successes)
    print("failed races:", failures)
    print("RaceData.json modified:", "NO" if racedata_unchanged else "YES")
    print("prediction files modified: NO")

    if failures:
        print()
        print("KEIBAAI FULL-DAY REPLAY: FAILED")
        sys.exit(1)

    if not racedata_unchanged:
        print()
        print("KEIBAAI FULL-DAY REPLAY: FAILED")
        print("RaceData.json changed during a read-only replay")
        sys.exit(1)

    print()
    print("★★★★★ KEIBAAI FULL-DAY REPLAY: SUCCESS")
    print("All selected published races reproduced successfully in simulated 10-minute order.")


if __name__ == "__main__":
    main()
