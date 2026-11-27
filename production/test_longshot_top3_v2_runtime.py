"""Exercise the trained model, frozen capture, and verified result comparison."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import lightgbm as lgb
import pandas as pd

import formal_portfolio_capture as capture
from evaluate_longshot_top3_v2 import (calibration_summary, evaluate, verified_fresh_win_odds,
    shortlist_summary, day_cluster_interval, daily_brier_intervals, markdown)
from longshot_top3_v2_runtime import (
    FEATURES, MODEL_FEATURES, OUTPUT, path_for, prepare, record_first, score_race, validate_record,
)
from market_shrink_shadow import fingerprint
from research_longshot_top3_v2 import predict

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
            "captureQuality": "formal_10min", "capturedAtJST": NOW.isoformat(), "horses": horses,
            "winOddsFreshness": {
                "sourceField": "data.official_datetime",
                "sourceUpdatedAtJST": (NOW - timedelta(minutes=5)).isoformat(),
                "receivedAtJST": (NOW - timedelta(minutes=1)).isoformat(),
                "sourceAgeSecondsAtReceipt": 240,
                "sourceAgeSecondsAtPublication": 300,
                "maxSourceAgeSeconds": 1800,
                "freshnessStatus": "verified_within_30m",
                "publicationFreshnessStatus": "verified_within_30m",
            }}
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
        stored = pd.DataFrame(scores["modelInput"]["rows"], columns=MODEL_FEATURES)
        stored["市場3着内基準"] = baseline.set_index("馬番").loc[
            scores["modelInput"]["horseNumbers"], "市場3着内基準"].to_numpy()
        model = lgb.Booster(model_file=str(OUTPUT / "model_through2025.txt"))
        replay = predict(stored, model, MODEL_FEATURES, scores["model"]["weight"])
        by_number = dict(zip(scores["modelInput"]["horseNumbers"], replay * 100, strict=True))
        for horse in scores["horses"]:
            self.assertAlmostEqual(horse["top3ProbabilityPct"], by_number[horse["number"]])

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
            self.assertEqual(report["freshOddsVerified30m"]["matchedRaces"], 1)
            self.assertEqual(report["freshOddsVerified30m"]["matchedCandidateHorses"], 2)
            self.assertEqual(report["freshOddsVerified30m"]["topPickComparison"]["market"]["top3Hits"], 1)
            self.assertEqual(report["freshOddsVerified30m"]["status"], "insufficient_verified_30m_evidence")
            self.assertEqual(report["topPickComparison"]["market"]["top3Hits"], 1)
            self.assertEqual(report["topPickComparison"]["formalAI"]["top3Hits"], 0)
            self.assertEqual(report["matchedCandidateHorses"], 2)
            calibration = report["calibrationAllQualifyingHorses"]["market"]
            self.assertEqual(calibration["horses"], 2)
            self.assertEqual(calibration["observedHits"], 1)
            self.assertAlmostEqual(calibration["expectedHits"], .30)
            self.assertAlmostEqual(calibration["meanPredictedPct"], 15.)
            self.assertEqual(calibration["observedRatePct"], 50.)
            self.assertEqual(report["calibrationSelectedHorse"]["market"]["horses"], 1)
            self.assertEqual(report["matchedRaceDates"], 1)
            short = report["freshOddsVerified30m"]["shortlistComparison"]
            self.assertEqual(short["byCount"]["2"]["methods"]["market"]["racesHit"], 1)
            self.assertEqual(short["byCount"]["3"]["methods"]["market"]["selectedHorses"], 2)
            self.assertIsNone(report["pairedCandidateBrierDifferenceByDate"]["market"]["bootstrap95"])
            self.assertIn("穴馬1〜3頭の好走率", markdown(report))
            corrupted = json.loads(saved)
            corrupted["modelInput"]["rows"][0][0] += 1
            corrupted["recordFingerprint"] = fingerprint({
                k: v for k, v in corrupted.items() if k != "recordFingerprint"
            })
            with self.assertRaisesRegex(ValueError, "inputs differ"):
                validate_record(corrupted, DAY, RACE_ID, report["model"])
            altered_baseline = json.loads(saved)
            altered_baseline["horses"][0]["marketTop3Probability"] += 1
            altered_baseline["recordFingerprint"] = fingerprint({
                k: v for k, v in altered_baseline.items() if k != "recordFingerprint"
            })
            path.write_text(json.dumps(altered_baseline))
            self.assertEqual(evaluate(root, audit)["rejectedCaptures"], 1)
            path.write_bytes(saved)
            audit_race["inputFingerprint"] = write_bundle(root, changed)
            report = evaluate(root, audit)
            self.assertEqual(report["rejectedCaptures"], 1)
            self.assertEqual(report["matchedRaces"], 0)

    def test_fresh_source_evidence_requires_true_upstream_update_time(self):
        race, _, _ = inputs()
        self.assertEqual(verified_fresh_win_odds(race),
                         (True, "verified_30m_at_publication"))
        old = deepcopy(race)
        old.pop("winOddsFreshness")
        self.assertEqual(verified_fresh_win_odds(old),
                         (False, "missing_source_provenance"))
        stale = deepcopy(race)
        stale["winOddsFreshness"]["sourceUpdatedAtJST"] = (
            NOW - timedelta(minutes=31)).isoformat()
        self.assertEqual(verified_fresh_win_odds(stale)[0], False)
        future = deepcopy(race)
        future["winOddsFreshness"]["sourceUpdatedAtJST"] = (
            NOW + timedelta(seconds=5)).isoformat()
        self.assertEqual(verified_fresh_win_odds(future)[0], False)
        spoofed = deepcopy(race)
        spoofed["winOddsFreshness"]["sourceField"] = "HTTP.receivedAt"
        self.assertEqual(verified_fresh_win_odds(spoofed)[0], False)
        forged = deepcopy(race)
        forged["winOddsFreshness"]["sourceAgeSecondsAtPublication"] = 0
        self.assertEqual(verified_fresh_win_odds(forged)[0], False)
        after_capture = deepcopy(race)
        after_capture["winOddsFreshness"]["receivedAtJST"] = (
            NOW + timedelta(seconds=1)).isoformat()
        self.assertEqual(verified_fresh_win_odds(after_capture)[0], False)

    def test_calibration_counts_each_probability_boundary_once(self):
        probabilities = (0., .05, .10, .20, .30, .50, 1.)
        samples = [{"raceID": "R1", "raceDate": DAY, "probability": p, "outcome": int(p == 1)}
                   for p in probabilities]
        summary = calibration_summary(samples)
        self.assertEqual([b["horses"] for b in summary["bins"]], [1, 1, 1, 1, 1, 2])
        self.assertEqual(sum(b["observedHits"] for b in summary["bins"]), 1)
        empty = calibration_summary([])
        self.assertIsNone(empty["observedRatePct"])
        self.assertIsNone(empty["brierScore"])

    def test_shortlist_cohort_ties_and_multiple_hits(self):
        rows = [{"raceID": "R", "raceDate": DAY, "number": n, "outcome": y,
                 "probability": p} for n, y, p in [(3, 1, .2), (1, 0, .2), (2, 1, .1)]]
        samples = {"candidate": rows, "market": rows, "formalAI": rows}
        out = shortlist_summary(samples)["byCount"]
        self.assertEqual(out["1"]["methods"]["candidate"]["racesHit"], 0)
        self.assertEqual(out["2"]["methods"]["candidate"]["racesHit"], 1)
        self.assertEqual(out["3"]["methods"]["candidate"]["racesWithTwoOrMoreHits"], 1)
        self.assertIsNone(shortlist_summary({})["byCount"]["1"]["methods"]["market"]["raceHitRatePct"])
        with self.assertRaisesRegex(ValueError, "horse cohorts"):
            shortlist_summary({**samples, "market": rows[:2]})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            shortlist_summary({**samples, "market": rows + rows[:1]})

    def test_day_cluster_equal_race_weight_and_threshold(self):
        self.assertIsNone(day_cluster_interval({"a": [1], "b": [0, 0, 0]})["bootstrap95"])
        self.assertEqual(day_cluster_interval({"a": [1], "b": [0, 0, 0]})["meanDelta"], .25)
        days = {str(i): [.25, .25] for i in range(20)}
        self.assertEqual(day_cluster_interval(days)["bootstrap95"], [.25, .25])
        self.assertEqual(day_cluster_interval(days), day_cluster_interval(dict(reversed(list(days.items())))))
        self.assertIsNone(day_cluster_interval({})["meanDelta"])
        result = daily_brier_intervals({"a": {"candidate": [.1], "market": [.2], "formalAI": [.1]}})
        self.assertAlmostEqual(result["market"]["meanDelta"], -.1)
        self.assertEqual(result["formalAI"]["meanDelta"], 0.)

    def test_live_publication_passes_the_original_history_to_capture(self):
        import live_monitor_topk_publish as publisher
        race, features, history = inputs()
        frame = pd.DataFrame({"馬番": features["horseNumbers"]})
        frame.attrs["noCurrentOddsFeatures"] = features
        frame.attrs["longshotTop3Context"] = CONTEXT
        with patch.object(publisher, "_original_predict", return_value=frame), \
                patch.object(publisher, "PUBLISH_TO_APP", True), \
                patch.object(publisher, "publish_race_to_racedata") as publish:
            result = publisher.predict_live_race_topk_and_publish(race, history, pd.Timestamp(DAY))
        self.assertIs(result, frame)
        self.assertIs(publish.call_args.args[1], frame)
        self.assertIs(publish.call_args.kwargs["history"], history)


if __name__ == "__main__":
    unittest.main()
