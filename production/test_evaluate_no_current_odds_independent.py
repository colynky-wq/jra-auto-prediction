"""Network-free independent settlement tests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
import pandas as pd
from market_shrink_shadow import fingerprint
from no_current_odds_independent import VERSION, DIRECTORY
from evaluate_no_current_odds_independent import evaluate


class IndependentSettlementTests(TestCase):
    def fixture(self, root):
        day="2026-11-28"; rid="202605050601"; date8=day.replace("-","")
        meta={"version":"test","modelSha256":"a"*64}
        active=[{"number":n,"name":name,"age":3,"withdrawn":False} for n,name in enumerate("abc",1)]
        v={"raceDate":day,"raceID":rid,"startTime":"11:00","version":VERSION,"schemaVersion":1,
           "status":"independent_prospectively_frozen_not_approved","researchOnly":True,
           "currentOddsUsed":False,"formalRecommendationsEnabled":False,"formalPredictionAIChanged":False,
           "candidatePromotionApproved":False,"autoBetEnabled":False,"modelVersion":meta["version"],
           "modelSha256":meta["modelSha256"],"featureInputSha256":"b"*64,
           "capturedAtJST":day+"T10:50:00+09:00","recordedAtJST":day+"T10:50:05+09:00",
           "sourceEntryEvidence":{"raceDate":day,"raceID":rid,"sourceSHA256":"c"*64,"runners":active,
               "capturedAtJST":day+"T10:50:00+09:00","scheduledPostJST":day+"T11:00:00+09:00"},
           "candidateHorses":[{"number":n,"name":name,"winPct":p,"top2Pct":q,"top3Pct":100.}
               for n,name,p,q in zip([1,2,3],"abc",[20.,60.,20.],[55.,90.,55.])]}
        v["recordFingerprint"]=fingerprint(v)
        path=root/"reports"/date8/DIRECTORY/(rid+".json");path.parent.mkdir(parents=True)
        path.write_text(json.dumps(v))
        receipt={"status":"applied","applyRequested":True,"targetDate":date8,"newLatestDate":day,
                 "duplicateRaceHorseKeys":0,"generatedAtJST":day+"T22:00:00+09:00",
                 "addedRaceCount":1,"newRows":3,"raceListSource":{"method":"jra_official_calendar_v1",
                     "targetDate":date8,"raceIDs":[rid]}}
        rp=root/"reports"/"history_updates"/(date8+".json");rp.parent.mkdir()
        d=pd.DataFrame({"日付":pd.Timestamp(day),"レースID":rid,"馬番":[1,2,3],"馬名":list("abc"),
                        "年齢":3,"頭数":3,"着順":[2,1,3],"出典":"JRA公式更新"})
        return meta,d,receipt,rp,v,path

    def test_match_without_any_formal_prediction(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);meta,d,r,rp,_,_=self.fixture(root)
            rp.write_text(json.dumps(r))
            report=evaluate(root,d,meta)
            self.assertEqual(report["counts"]["matchedRaces"],1)
            self.assertEqual(report["byEvent"]["win"]["topPickHits"],1)
            self.assertFalse(report["formalCohortChanged"])
            self.assertEqual(report["status"],"insufficient_prospective_evidence")

    def test_missing_receipt_or_roster_excluded_not_a_loss(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);meta,d,r,rp,_,_=self.fixture(root)
            self.assertEqual(evaluate(root,d,meta)["counts"]["awaitingHistory"],1)
            rp.write_text(json.dumps(r))
            d.loc[0,"馬名"]="wrong"
            result=evaluate(root,d,meta)
            self.assertEqual(result["counts"]["excludedRaces"],1)
            self.assertIsNone(result["byEvent"]["win"]["equalRaceBrier"])

    def test_digest_or_late_record_rejected(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);meta,d,_,_,v,path=self.fixture(root)
            v["recordedAtJST"]="2026-11-28T10:53:00+09:00"
            path.write_text(json.dumps(v))
            with self.assertRaisesRegex(ValueError,"digest"):
                evaluate(root,d,meta)
            v["recordFingerprint"]=fingerprint({k:x for k,x in v.items() if k!="recordFingerprint"})
            path.write_text(json.dumps(v))
            with self.assertRaisesRegex(ValueError,"T10"):
                evaluate(root,d,meta)


if __name__=="__main__":
    main()
