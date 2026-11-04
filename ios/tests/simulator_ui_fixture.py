"""Generate offline phone frames with the production v0.2 engine and adapter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "production"))
from build_race_simulation_data_v02 import audit_race
from race_simulator_v02_adapter import simulate_current_keibaai_v02


def build_simulator_fixture():
    rid = "202605040601"
    horses = [{"number": n, "name": f"再生確認{n}", "horseKey": f"ui-{n}",
               "aiProbability": probability}
              for n, probability in [(1, 60), (2, 30), (3, 10)]]
    race = {"raceID": rid, "venue": "東京", "raceNumber": 1,
            "raceName": "フレーム操作確認", "course": "芝1600m", "horses": horses}
    research = [{"raceID": rid, "horses": [
        {"number": n, "styleTendency": {"sampleRaces": 6, "dominantStyle": style,
                                       "distributionPct": {style: 100}},
         "initialPositionHistory": {"meanRelativePosition": position}}
        for n, style, position in [(1, "差し", .8), (2, "先行", .3), (3, "逃げ", .1)]
    ]}]
    profiles = {"profiles": {f"ui-{n}": {
        "horseKey": f"ui-{n}", "distanceStats": [
            {"distanceBand": "1600-1799m", "starts": 8, "smoothedPerformanceScore": score}]
    } for n, score in [(1, 10), (2, 50), (3, 95)]}}
    result = simulate_current_keibaai_v02([race], research, profiles, rid, 30, 20261104, .5)
    result["audit"] = audit_race(result)
    assert result["audit"]["status"] == "pass", result["audit"]
    assert result["independence"]["strengthSource"] == "historical-performance-v0.2"
    assert max(result["horses"], key=lambda h: h["simulationWinProbability"])["horseNumber"] != 1
    return {"schemaVersion": "race-simulator-v0.2-batch", "generatedAt": result["generatedAt"],
            "status": "ok", "settings": {"runsPerRace": 30, "baseSeed": 20261104, "dtSeconds": .5},
            "summary": {"inputRaces": 2, "simulatedRaces": 1, "skippedRaces": 1, "errorRaces": 0},
            "races": [result]}
