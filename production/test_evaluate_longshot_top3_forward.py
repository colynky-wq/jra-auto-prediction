"""Offline independent audit tests for frozen underdog forecasts."""
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from market_shrink_shadow import JST, _write_first
from longshot_top3_shadow import build, path_for
from evaluate_longshot_top3_forward import evaluate
from test_longshot_top3_shadow import samples, FakeBooster
from train_longshot_top3 import VERSION


def now():
    return datetime(2026,11,23,9,50,10,tzinfo=JST)


def fixture():
    race, features, meta=samples()
    meta.update({
        "researchOnly":True,
        "autoBetEnabled":False,"formalPredictionAIChanged":False,
        "popularityMinimum":6,"winOddsMinimum":10,
        "longshotFilter":"popularity >= 6 AND win odds >= 10.0",
        "selectedResidualMultiplier":1.0,
    })
    actual={str(i):i for i in range(1,9)}
    actual.update({"2":3,"3":4,"4":5,"5":6,"6":2,"7":7,"8":8})
    audit={
        "projectionState":"verified",
        "generatedAtJST":"2026-11-23T21:00:00+09:00",
        "aligned":{
            "countsAreCurrent":True,
            "racesObserved":1,
            "matchedResultRaces":1,
        },
        "quality":{"issues":[],"resultExclusions":[]},
        "races":[{
            "raceDate":race["raceDate"],"raceID":race["raceID"],
            "resultState":"matched","inputFingerprint":"audit-hash",
            "actualFinish":actual,
        }],
    }
    return race,features,meta,audit


class LongshotForwardReviewTests(unittest.TestCase):
    def test_missing_future_capture_is_reported_not_backfilled(self):
        race,features,meta,audit=fixture()
        with TemporaryDirectory() as tmp:
            output=evaluate(tmp,audit,meta)
            self.assertEqual(output["counts"]["firstFrozenShadowRaces"],0)
            self.assertEqual(output["counts"]["missingShadowRaces"],1)
            self.assertEqual(output["counts"]["candidateCaptureCoveragePct"],0)
            self.assertFalse(path_for(tmp,race).exists())

    def test_first_frozen_before_post_and_official_podium_scored(self):
        race,features,meta,audit=fixture()
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            _write_first(path_for(root,race),build(
                race,features,meta,FakeBooster(),clock=now))
            scored=evaluate(root,audit,meta,load_bundle=lambda *_:{
                "fingerprint":"audit-hash","prediction":race
            })
            self.assertEqual(scored["counts"]["firstFrozenShadowRaces"],1)
            self.assertEqual(scored["counts"]["matchedScorableRaces"],1)
            self.assertEqual(scored["counts"]["matchedUnderdogHorseRows"],3)
            self.assertEqual(scored["counts"]["matchedUnderdogTop3Rows"],1)
            self.assertEqual(scored["counts"]["candidateCaptureCoveragePct"],100)
            self.assertEqual(scored["top3OutcomesByOdds"]["10-29.9"]["hits"],1)
            self.assertFalse(scored["autoBetEnabled"])
            self.assertIsNotNone(scored["top3BrierEqualRace"]["longshotCandidate"])

    def test_wrong_source_or_failed_audit_never_receives_results(self):
        race,features,meta,audit=fixture()
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            _write_first(path_for(root,race),build(
                race,features,meta,FakeBooster(),clock=now))
            with self.assertRaisesRegex(ValueError,"fingerprint mismatch"):
                evaluate(root,audit,meta,load_bundle=lambda *_:{
                    "fingerprint":"wrong-hash","prediction":race
                })
            audit["projectionState"]="failed"
            with self.assertRaisesRegex(ValueError,"strict verified audit"):
                evaluate(root,audit,meta)

    def test_unsettled_race_never_counted_as_zero_hits(self):
        race,features,meta,audit=fixture()
        audit["races"][0]["resultState"]="awaiting_result"
        audit["aligned"]["matchedResultRaces"]=0
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            _write_first(path_for(root,race),build(
                race,features,meta,FakeBooster(),clock=now))
            out=evaluate(root,audit,meta)
            self.assertEqual(out["counts"]["matchedScorableRaces"],0)
            self.assertEqual(out["counts"]["unmatchedOrExcludedShadows"],1)
            self.assertIsNone(out["top3BrierEqualRace"]["longshotCandidate"])


if __name__=="__main__":
    unittest.main()
