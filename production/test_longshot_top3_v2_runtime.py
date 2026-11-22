"""Exercise the trained model, frozen capture, and verified result comparison."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

import formal_portfolio_capture as capture
from evaluate_longshot_top3_v2 import evaluate
from longshot_top3_v2_runtime import FEATURES, path_for, prepare, record_first, score_race

DAY = "2026-11-28"
RACE_ID = "202605050801"
NOW = datetime.fromisoformat(DAY + "T10:00:00+09:00")
CONTEXT = {"surface": "芝", "distance": 1600, "class": "1勝"}


def inputs():
    horses = [{
        "number": n, "name": f"Horse {n}", "horseKey": f"Horse {n}_2023",
        "popularity": n, "odds": odds, "bodyWeight": None if n == 7 else 490,
        "marketTop3Probability": market, "aiTop3Probability": ai,
    } for n, (odds, market, ai) in enumerate(zip(
        [2., 3., 5., 6., 7., 9.9, 10., 30.], [90, 60, 45, 36, 31, 23, 22, 8],
        [85, 58, 42, 35, 30, 26, 19, 23], strict=True,
    ), 1)]
    race = {"raceDate": DAY, "raceID": RACE_ID, "startTime": "10:10",
            "course": "芝1600m", "predictionTiming": "live_10min",
            "captureQuality": "formal_10min", "capturedAtJST": NOW.isoformat(), "horses": horses}
    features = {"featureNames": list(FEATURES), "horseNumbers": list(range(1, 9)),
                "rows": [[490.0 if f == "馬体重f" else 0.5 for f in FEATURES] for _ in horses]}
    prior = pd.DataFrame([{
        "レースID": "202605050701", "馬番": 4, "馬キー": "Horse 7_2023", "日付": "2026-11-23",
        "芝ダ": "芝", "距離": 1600, "クラス": "1勝", "着順": 2, "頭数": 8, "障害": False,
    }])
    return race, features, prior


def write_bundle(root, race):
    bundle = capture.path_for(root, DAY, RACE_ID)
    sources = {}
    for folder in ("all_bet_v1/win", "triple_v1/trio", "wide_odds_live10"):
        path = bundle.parent.parent / folder / (RACE_ID + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}\n")
        sources[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    data = {"schemaVersion": 1, "inputPolicy": capture.POLICY, "prediction": race,
            "recordedAtJST": NOW.isoformat(), "sources": sources,
            "plan": {"ruleVersion": "portfolio-shadow-v1-20261019"}}
    data["fingerprint"] = capture.digest(data)
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(json.dumps(data), encoding="utf-8")
    return data["fingerprint"]


class LongshotV2RuntimeTest(unittest.TestCase):
    def test_pre_race_scoring_uses_real_booster_and_excludes_same_day_results(self):
        race, features, history = inputs()
        baseline = prepare(race, features, CONTEXT, history)
        forbidden = history.assign(レースID="202605050802", 日付=DAY, 着順=8)
        changed = prepare(race, features, CONTEXT, pd.concat([history, forbidden]))
        pd.testing.assert_frame_equal(changed, baseline)
        self.assertEqual(baseline.loc[baseline["馬番"] == 7, "馬体重f"].item(), 480)
        self.assertEqual(features["rows"][6][list(FEATURES).index("馬体重f")], 490)
        scores = score_race(race, features, CONTEXT, history)
        self.assertEqual({horse["number"] for horse in scores["horses"]}, {7, 8})
        self.assertTrue(all(0 < horse["top3ProbabilityPct"] < 100 for horse in scores["horses"]))
        self.assertEqual(scores["horses"][0]["number"], scores["selectedHorseNumber"])

    def test_first_capture_and_comparison_share_the_original_t10_inputs(self):
        race, features, history = inputs()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = path_for(root, DAY, RACE_ID)
            def write(source=race, *, at=NOW, first=True):
                return record_first(root, source, features, CONTEXT, history,
                                    first_formal_capture=first, clock=lambda: at)
            self.assertEqual(write(first=False), "first_capture_missing")
            self.assertFalse(path.exists())
            times = iter((NOW, NOW + timedelta(minutes=3)))
            with self.assertRaisesRegex(ValueError, "outside"):
                record_first(root, race, features, CONTEXT, history,
                             first_formal_capture=True, clock=lambda: next(times))
            self.assertFalse(path.exists())
            self.assertEqual(write(), "frozen")
            saved = path.read_bytes()
            self.assertEqual(write(at=NOW + timedelta(hours=1), first=False), "already_frozen")
            self.assertEqual(saved, path.read_bytes())
            changed = deepcopy(race)
            changed["horses"][7]["odds"] = 35
            with self.assertRaisesRegex(ValueError, "conflicts"):
                write(changed, first=False)

            audit_race = {"raceDate": DAY, "raceID": RACE_ID, "resultState": "matched",
                          "inputFingerprint": write_bundle(root, race),
                          "actualFinish": {str(n): rank for n, rank in enumerate(
                              [1, 2, 4, 5, 6, 7, 3, 8], 1)}}
            audit = {"projectionState": "verified", "generatedAtJST": (NOW + timedelta(hours=1)).isoformat(),
                     "aligned": {"countsAreCurrent": True, "racesObserved": 1}, "races": [audit_race]}
            report = evaluate(root, audit)
            self.assertEqual(report["matchedRaces"], 1)
            self.assertEqual(report["topPickComparison"]["market"]["top3Hits"], 1)
            self.assertEqual(report["topPickComparison"]["formalAI"]["top3Hits"], 0)
            self.assertEqual(report["matchedCandidateHorses"], 2)
            audit_race["inputFingerprint"] = write_bundle(root, changed)
            report = evaluate(root, audit)
            self.assertEqual(report["rejectedCaptures"], 1)
            self.assertEqual(report["matchedRaces"], 0)


if __name__ == "__main__":
    unittest.main()
