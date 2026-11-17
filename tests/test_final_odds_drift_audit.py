"""Regression tests for post-hoc price movement; all inputs synthetic."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "production"))
from final_odds_drift_audit import compare_race, audit_repository


class FinalOddsDriftTests(unittest.TestCase):
    def setUp(self):
        self.record={"prediction":{"raceID":"202608050412","raceDate":"2026-11-15",
            "startTime":"16:25","horses":[
                {"number":1,"odds":2.0},
                {"number":2,"odds":5.0},
                {"number":3,"odds":7.0}
            ]}}
        self.outcome={"raceID":"202608050412","horses":[
            {"number":1,"finalOdds":6.0},
            {"number":2,"finalOdds":2.1},
            {"number":3,"finalOdds":7.0}
        ]}

    def test_detects_favorite_reversal_and_movement(self):
        x=compare_race(self.record,self.outcome)
        self.assertEqual(x["matchedHorses"],3)
        self.assertTrue(x["favoriteChanged"])
        self.assertEqual(x["frozenFavoriteNumber"],1)
        self.assertEqual(x["finalFavoriteNumber"],2)
        self.assertEqual(x["rows"][0]["finalToFrozenOddsRatio"],3.0)

    def test_never_mismatches_race_identity(self):
        self.outcome["raceID"]="OTHER"
        with self.assertRaisesRegex(ValueError,"identity mismatch"):
            compare_race(self.record,self.outcome)

    def test_ignores_bad_and_nonfinite_odds(self):
        self.outcome["horses"][0]["finalOdds"]="NA"
        self.outcome["horses"][1]["finalOdds"]="nan"
        x=compare_race(self.record,self.outcome)
        self.assertEqual(x["matchedHorses"],1)

    def test_whole_cohort_is_diagnostic_only(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState":"verified","aligned":{"countsAreCurrent":True},
                "races":[
                    {"raceDate":"2026-11-15","raceID":"202608050412","resultState":"matched"},
                    {"raceDate":"2026-11-15","raceID":"202608050411","resultState":"entrant_changed"}
                ],
            }))
            base=root/"reports"/"20261115"
            (base/"all_bet_v1"/"win").mkdir(parents=True)
            (base/"ResultData.json").write_text(json.dumps([self.outcome]))
            (base/"all_bet_v1"/"win"/"202608050412.json").write_text(json.dumps(self.record))
            result=audit_repository(root)
            self.assertEqual(result["matchedRacesAudited"],1)
            self.assertEqual(result["totalHorsesCompared"],3)
            self.assertEqual(result["favoriteChangedRaces"],1)
            self.assertFalse(result["finalOddsUsedAsPreRace"])
            self.assertFalse(result["liveRecommendationsChanged"])
            self.assertEqual(result["networkRequests"],0)

    def test_bad_alignment_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState":"failed","aligned":{"countsAreCurrent":False},"races":[]
            }))
            with self.assertRaisesRegex(ValueError,"not verified"):
                audit_repository(root)


if __name__=="__main__":
    unittest.main()
