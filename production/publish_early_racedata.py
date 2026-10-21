from __future__ import annotations

"""Publish the latest early snapshot through the app's canonical RaceData feed.

EarlyPredictionData.json remains the research/audit source.  This adapter only
projects its latest requested stage into the RaceData schema already consumed
by the iOS app.  The normal formal live prediction may overwrite RaceData.json
later; no early snapshot is promoted to a formal recommendation by this file.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EARLY = ROOT / "EarlyPredictionData.json"
RACE_DATA = ROOT / "RaceData.json"


def main() -> None:
    stage = os.environ.get("KEIBAAI_EARLY_STAGE", "morning").strip() or "morning"
    payload = json.loads(EARLY.read_text(encoding="utf-8"))
    snapshot = payload.get("snapshots", {}).get(stage)
    if not isinstance(snapshot, dict):
        raise RuntimeError(f"missing early snapshot stage: {stage}")

    races = []
    for race in snapshot.get("races", []):
        horses = []
        for horse in race.get("horses", []):
            odds = float(horse["odds"])
            ai_probability = float(horse["aiProbability"])
            horses.append({
                "number": int(horse["number"]),
                "name": str(horse["name"]),
                "odds": odds,
                "marketProbability": float(horse["marketProbability"]),
                "aiProbability": ai_probability,
                "aiTop2Probability": horse.get("aiTop2Probability"),
                "aiTop3Probability": horse.get("aiTop3Probability"),
                "aiMarketRatio": float(horse["aiMarketRatio"]),
                "expectedIndex": round(ai_probability * odds, 3),
            })

        races.append({
            "raceID": str(race["raceID"]),
            "raceDate": race.get("raceDate"),
            "venue": str(race.get("venue") or ""),
            "raceNumber": int(race.get("raceNumber") or 0),
            "startTime": str(race.get("startTime") or ""),
            "raceName": str(race.get("raceName") or ""),
            # EarlyPredictionData currently has no course field. Keep the
            # Race schema valid; the formal live feed replaces this later.
            "course": str(race.get("course") or "前日・朝時点"),
            "horses": horses,
        })

    if not races:
        raise RuntimeError("early snapshot contains no races; refusing to replace RaceData.json")

    tmp = RACE_DATA.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(races, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(RACE_DATA)
    print("EARLY_RACEDATA_PUBLISHED", "stage=", stage, "races=", len(races))


if __name__ == "__main__":
    main()
