"""Offline independent-odds comparison tests; all fixtures synthetic."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "production"))
from independent_odds_audit import compare_observation, run_audit, build_map


class IndependentOddsAuditTests(unittest.TestCase):
    def setUp(self):
        self.win_record = {"prediction": {
            "raceID":"202608050412","raceDate":"2026-11-15",
            "startTime":"16:15","capturedAtJST":"2026-11-15T16:03:30+09:00",
            "horses":[
                {"number":1,"odds":4.1},{"number":2,"odds":3.0},
                {"number":3,"odds":6.3}],
        }}
        self.formal_wide = {
            "raceID":"202608050412","raceDate":"2026-11-15",
            "startTime":"16:15",
            "pairs":[
                {"number1":1,"number2":2,"oddsLow":6.2,"oddsHigh":7.0},
                {"number1":1,"number2":3,"oddsLow":8.0,"oddsHigh":9.2},
                {"number1":2,"number2":3,"oddsLow":7.4,"oddsHigh":8.6},
            ],
        }
        self.independent = {
            "schemaVersion":1,"source":"jra_official_observation",
            "sourceReference":"https://www.jra.go.jp/keiba/",
            "permissionConfirmed":True,
            "raceID":"202608050412","raceDate":"2026-11-15",
            "startTime":"16:15","status":"pre_post",
            "observedAtJST":"2026-11-15T16:04:00+09:00",
            "asOfJST":"2026-11-15T16:03:00+09:00",
            "winOdds":[{"number":1,"odds":4.2},{"number":2,"odds":2.8},
                        {"number":3,"odds":6.5}],
            "wideOdds":[
                {"number1":1,"number2":2,"oddsLow":5.5,"oddsHigh":6.0},
                {"number1":1,"number2":3,"oddsLow":8.3,"oddsHigh":9.5},
                {"number1":2,"number2":3,"oddsLow":7.6,"oddsHigh":8.9},
            ],
        }

    def test_matching_source_returns_structurally_comparable(self):
        x=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertEqual(x["comparisonStatus"],"structurally_comparable_not_authenticated")
        self.assertEqual(x["errors"],[])
        self.assertFalse(x["sourceAuthenticityVerified"])
        self.assertFalse(x["monetizableEdgeValidated"])
        self.assertEqual(x["winCoverage"]["provided"],3)
        self.assertEqual(x["wideCoverage"]["missingPairs"],0)
        p=next(y for y in x["wideComparison"] if y["pair"]=="0102")
        self.assertEqual(p["frozenOddsLow"],6.2)
        self.assertEqual(p["independentOddsLow"],5.5)

    def test_old_source_asof_rejected(self):
        self.independent["asOfJST"]="2026-11-15T15:05:00+09:00"
        x=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("independent_quote_not_recently_confirmed",x["errors"])
        self.assertEqual(x["comparisonStatus"],"rejected")

    def test_after_the_race_rejected(self):
        self.independent["observedAtJST"]="2026-11-15T16:16:00+09:00"
        self.independent["asOfJST"]="2026-11-15T16:15:00+09:00"
        x=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("outside_8_to_12_minute_window",x["errors"])

    def test_wrong_race_id_rejected(self):
        self.independent["raceID"]="WRONG"
        x=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("identity_mismatch_raceID",x["errors"])

    def test_missing_source_or_permission_rejected(self):
        self.independent["sourceReference"]=""
        self.independent["permissionConfirmed"]=False
        x=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("missing_source_reference",x["errors"])
        self.assertIn("permission_not_confirmed",x["errors"])

    def test_duplicate_wide_pair_rejected(self):
        self.independent["wideOdds"].append(dict(self.independent["wideOdds"][0]))
        x=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("wide_duplicate_quote",x["errors"])

    def test_sparse_reference_can_only_attest_subset(self):
        self.independent["winOdds"]=[]
        self.independent["wideOdds"]=self.independent["wideOdds"][:1]
        x=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertEqual(x["comparisonStatus"],"structurally_comparable_not_authenticated")
        self.assertEqual(x["winCoverage"]["provided"],0)
        self.assertEqual(x["wideCoverage"]["missingPairs"],2)
        self.assertEqual(len(x["wideComparison"]),1)

    def test_no_external_source_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState":"verified",
                "aligned":{"countsAreCurrent":True},
                "races":[{"raceDate":"2026-11-15","raceID":"202608050412"}],
            }),encoding="utf-8")
            x=run_audit(root,root/"not_existing")
            self.assertEqual(x["observationFilesPresent"],0)
            self.assertEqual(x["unobservedRaces"],1)
            self.assertEqual(x["qualityGate"],"independent_source_evidence_pending")
            self.assertEqual(x["predictionFilesChanged"],0)

    def test_external_capture_can_be_compared_without_touching_formal(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState":"verified",
                "aligned":{"countsAreCurrent":True},
                "races":[{"raceDate":"2026-11-15","raceID":"202608050412"}],
            }),encoding="utf-8")
            freeze=root/"reports"/"20261115"
            (freeze/"all_bet_v1"/"win").mkdir(parents=True)
            (freeze/"wide_odds_live10").mkdir(parents=True)
            wfile=freeze/"all_bet_v1"/"win"/"202608050412.json"
            wfile.write_text(json.dumps(self.win_record),encoding="utf-8")
            before=wfile.read_bytes()
            (freeze/"wide_odds_live10"/"202608050412.json").write_text(
                json.dumps(self.formal_wide),encoding="utf-8")
            external=root/"research_independent_odds"/"20261115"
            external.mkdir(parents=True)
            (external/"202608050412.json").write_text(
                json.dumps(self.independent),encoding="utf-8")
            result=run_audit(root,root/"research_independent_odds")
            self.assertEqual(result["observationFilesPresent"],1)
            self.assertEqual(result["comparableObservations"],1)
            self.assertEqual(result["fullyCoveredWinObservations"],1)
            self.assertEqual(result["fullyCoveredWideObservations"],1)
            self.assertEqual(before,wfile.read_bytes())
            self.assertEqual(result["results"][0]["sourceAuthenticityVerified"],False)

    def test_unverified_cohort_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState":"failed","aligned":{"countsAreCurrent":False},
                "races":[],
            }),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"not verified"):
                run_audit(root,root/"research_independent_odds")


if __name__=="__main__":
    unittest.main()
