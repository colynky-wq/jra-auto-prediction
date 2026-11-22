"""Network-free guards for historical payout availability, identity and no ROI invention."""
from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd

from research_longshot_10to50_place_return import (
    inspect_payout, payout_inventory, target_band, segment_quality,
    eligible_race_payouts, pick_one_per_race, gross_return_summary,
    paired_return_comparison
)


def synthetic_longshots():
    d=pd.DataFrame({
        "レースID":["202605050501"]*3,"日付":pd.to_datetime(["2026-11-22"]*3),
        "馬番":[6,7,8],"着順":[3,7,8],
        "単勝オッズ":[20., 45., 55.],"人気":[6,7,8],
        "市場3着内基準":[.10,.15,.05],
    })
    return d


class LongshotPlacePayoutTests(unittest.TestCase):
    def test_10to50_is_strict_bounded_and_uplift_rule_result_independent(self):
        frame=synthetic_longshots()
        market=frame["市場3着内基準"].to_numpy()
        cand=np.asarray([.22,.11,.06])
        band=target_band(frame,market,cand)
        self.assertEqual(band["horseNumber"].tolist(),[6,7])
        self.assertEqual(band["uplift20Pct"].tolist(),[True,False])
        self.assertEqual(segment_quality(band)["top3Count"],1)
        self.assertEqual(band.loc[0,"ratio"],2.2)
        frame.loc[0,"人気"]=5
        with self.assertRaisesRegex(ValueError,"cohort mismatch"):
            target_band(frame,market,cand[:2])

    def test_payout_json_accepted_only_with_matching_source_and_date(self):
        rid="202605050501"
        with TemporaryDirectory() as tmp:
            path=(Path(tmp)/"reports/20261122/all_bet_payouts_v1/place"/(rid+".json"))
            path.parent.mkdir(parents=True)
            source=f"https://race.netkeiba.com/race/result.html?race_id={rid}"
            doc={"sourceURL":source, "retrievedAtJST":"2026-11-22T18:10:00+09:00",
                 "sourceFingerprint":"a"*64,"recordFingerprint":"b"*64,
                 "fingerprint":"c"*64,"payouts":{"01":120,"02":160,"06":470}}
            path.write_text(json.dumps(doc),encoding="utf-8")
            self.assertEqual(inspect_payout(path,expected_race_id=rid),
                             {1:120,2:160,6:470})
            self.assertEqual(len(payout_inventory(Path(tmp))),1)
            doc["sourceURL"]="https://other.example/odds"
            path.write_text(json.dumps(doc),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"identity"):
                inspect_payout(path,expected_race_id=rid)
            doc["sourceURL"]=source
            doc["payouts"]["06"]=0
            path.write_text(json.dumps(doc),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"payout per"):
                inspect_payout(path,expected_race_id=rid)

    def test_only_matching_real_payouts_enter_same_race_strategy_roi(self):
        rid="202605050501"
        full=pd.DataFrame({
            "レースID":[rid]*8,
            "馬番":list(range(1,9)),
            "着順":[1,2,4,5,6,3,7,8],
            "頭数":[8]*8,
        })
        d=synthetic_longshots()
        band=target_band(d,d["市場3着内基準"].to_numpy(),
                         np.asarray([.22,.11,.06]))
        doc={rid:{"place":{1:120,2:160,6:470},"path":"saved.json"}}
        checked,excluded=eligible_race_payouts(full,band,doc)
        self.assertEqual(len(checked),1)
        self.assertEqual(sum(excluded.values()),0)
        entries=pick_one_per_race(checked)
        self.assertEqual(entries[0]["marketPick"]["number"],7)
        self.assertEqual(entries[0]["aiPick"]["number"],6)
        self.assertEqual(entries[0]["uplift20PctPick"]["number"],6)
        self.assertTrue(entries[0]["aiPick"]["hit"])
        self.assertFalse(entries[0]["marketPick"]["hit"])
        self.assertEqual(gross_return_summary(entries,"aiPick")["grossReturnPct"],470.)
        self.assertEqual(gross_return_summary(entries,"marketPick")["grossReturnPct"],0.)
        paired=paired_return_comparison(entries)
        self.assertEqual(paired["sameRaces"],1)
        self.assertEqual(paired["candidateOnlyPlaceHits"],1)
        self.assertEqual(paired["marketOnlyPlaceHits"],0)
        # An invalid payout set must be EXCLUDED, not converted into a loss.
        doc[rid]["place"]={1:120,7:470}
        other,excluded=eligible_race_payouts(full,band,doc)
        self.assertEqual(other,{})
        self.assertEqual(excluded["payoutAndFinishDisagree"],1)


if __name__=="__main__":
    unittest.main()
