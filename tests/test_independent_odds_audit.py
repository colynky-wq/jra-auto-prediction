"""Offline independent-odds comparison tests; all fixtures synthetic."""
from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "production"))
from independent_odds_audit import (compare_observation, run_audit, build_map,
    reprice_reference_wide, parse_independent_json_bytes,
    verify_local_intake_receipt)


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

    def test_shadow_repricing_uses_independent_low_quote_only(self):
        difference=compare_observation(self.independent,self.win_record,self.formal_wide)
        race={"commonWideReference":[{"candidateID":"0102",
            "probabilityPct":20.0,"oddsLow":6.2,
            "expectedMultiplierLow":1.24}]}
        result=reprice_reference_wide(race,difference)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["frozenEstimatedReturnMultiplier"],1.24)
        self.assertEqual(result[0]["independentEstimatedReturnMultiplier"],1.1)
        self.assertTrue(result[0]["independentEdgeAboveBreakEven"])
        self.assertTrue(result[0]["researchOnly"])
        self.assertFalse(result[0]["sourceAuthenticityVerified"])

    def test_repricing_rejected_if_independent_quote_is_stale(self):
        self.independent["asOfJST"]="2026-11-15T15:03:00+09:00"
        diff=compare_observation(self.independent,self.win_record,self.formal_wide)
        x=reprice_reference_wide(
            {"commonWideReference":[{"candidateID":"0102",
                "probabilityPct":20.0,"oddsLow":6.2}]},diff)
        self.assertEqual(x,[])

    def test_fractional_horse_number_is_never_truncated(self):
        self.independent["winOdds"][0]["number"]=1.5
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("win_invalid_quote",result["errors"])
        self.assertEqual(result["comparisonStatus"],"rejected")

    def test_boolean_horse_number_is_invalid(self):
        self.independent["wideOdds"][0]["number1"]=True
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("wide_invalid_quote",result["errors"])

    def test_boolean_odds_is_invalid(self):
        self.independent["winOdds"][0]["odds"]=True
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("win_invalid_quote",result["errors"])

    def test_quoted_pair_with_reversed_order_is_accepted(self):
        self.independent["wideOdds"][0]["number1"]=2
        self.independent["wideOdds"][0]["number2"]=1
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertEqual(result["comparisonStatus"],"structurally_comparable_not_authenticated")
        self.assertEqual(result["wideComparison"][0]["pair"],"0102")

    def test_untyped_source_claim_rejected_instead_of_crashing(self):
        self.independent["source"]=["jra_official_observation"]
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("unknown_source_claim",result["errors"])

    def test_netkeiba_is_not_an_independent_source(self):
        self.independent["sourceReference"]="https://race.netkeiba.com/odds"
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("nonindependent_netkeiba_source",result["errors"])
        self.assertIn("jra_source_hostname_mismatch",result["errors"])

    def test_subdomain_spoofing_does_not_pass_as_jra(self):
        self.independent["sourceReference"]="https://www.jra.go.jp.evil.example/odds"
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("jra_source_hostname_mismatch",result["errors"])

    def test_naive_source_timestamp_is_not_trusted(self):
        self.independent["asOfJST"]="2026-11-15 16:03:00"
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("timestamp_without_explicit_offset_asOfJST",result["errors"])

    def test_malformed_quote_collection_is_rejected(self):
        self.independent["winOdds"]={"number":1,"odds":2.1}
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("win_invalid_quote_collection",result["errors"])

    def test_cross_source_capture_gap_over_two_minutes_rejected(self):
        self.independent["observedAtJST"]="2026-11-15T16:06:30+09:00"
        self.independent["asOfJST"]="2026-11-15T16:05:30+09:00"
        result=compare_observation(self.independent,self.win_record,self.formal_wide)
        self.assertIn("cross_source_capture_gap_too_large",result["errors"])

    def test_duplicate_json_key_is_rejected(self):
        raw=b'{"schemaVersion":1,"raceID":"a","raceID":"b"}'
        with self.assertRaisesRegex(ValueError,"duplicate JSON key"):
            parse_independent_json_bytes(raw)

    def test_nonfinite_json_number_is_rejected(self):
        raw=b'{"schemaVersion":1,"odds":NaN}'
        with self.assertRaisesRegex(ValueError,"non-finite JSON"):
            parse_independent_json_bytes(raw)

    def test_boolean_schema_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"unsupported independent"):
            parse_independent_json_bytes(b'{"schemaVersion":true}')

    def test_local_receipt_matches_only_unmodified_raw_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"20261115"/"202608050412.json"
            p.parent.mkdir(parents=True)
            raw=json.dumps(self.independent).encode("utf-8")
            p.write_bytes(raw)
            sha=hashlib.sha256(raw).hexdigest()
            receipt={"schemaVersion":1,"raceID":"202608050412",
                     "raceDate":"2026-11-15","observationSha256":sha,
                     "prospectiveClockCheckPassed":True,
                     "sourceAuthenticityVerified":False,
                     "intakeLagSeconds":15.0,
                     "intakeAtJST":"2026-11-15T16:04:15+09:00"}
            p.with_suffix(".receipt.json").write_text(json.dumps(receipt))
            self.assertEqual(
                verify_local_intake_receipt(p,sha)["status"],"matched_local_receipt")
            self.assertTrue(
                verify_local_intake_receipt(p,sha)["prospectiveClockCheckPassed"])
            self.assertEqual(
                verify_local_intake_receipt(p,"bad-sha")["status"],
                "invalid_local_receipt")

    def test_missing_receipt_does_not_count_as_prospective(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"20261115"/"202608050412.json"
            p.parent.mkdir(parents=True)
            p.write_bytes(b'{}')
            receipt=verify_local_intake_receipt(p,"irrelevant")
            self.assertEqual(receipt["status"],"missing_local_receipt")
            self.assertFalse(receipt["prospectiveClockCheckPassed"])

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
