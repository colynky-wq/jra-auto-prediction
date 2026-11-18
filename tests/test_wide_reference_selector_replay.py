"""No-network synthetic tests for immutable Wide reference selector replay."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"production"))
from wide_reference_selector_replay import (
    validate_signal_pairs,race_selection,selected_ids,summarize_candidates,
)


class WideSelectorReplayTests(unittest.TestCase):
    def setUp(self):
        numbers=[1,2,3,4,5,6,7,8]
        pairs=[(a,b) for a in numbers for b in numbers if a<b]
        all_rows=[]
        for rank,(a,b) in enumerate(pairs,1):
            # Top 20 are price-positive but miss the 5% probability gate.
            # Remaining 8 qualify but are outside the displayed top-20 window.
            p=4.0 if rank<=20 else 6.0
            odds=30.0 if rank<=20 else 20.0
            ev=p/100*odds
            all_rows.append({
                "number1":a,"number2":b,
                "name1":str(a),"name2":str(b),
                "aiWideProbability":p,
                "marketWideProbability":p-1,
                "aiMarketProbabilityRatio":1.1,
                "conservativeExpectedValue":ev,
                "wideOddsLow":odds,"wideOddsHigh":odds+1,
                "practicalScore":round(2-rank*0.03,3),
                "practicalRank":rank,
            })
        self.signal={"wideEvaluation":{
            "status":"live_odds_connected",
            "capturedAtJST":"2026-11-15T16:13:00+09:00",
            "officialDatetime":"2026-11-15T15:20:00+09:00",
            "validationPairs":all_rows,
            "pairs":all_rows[:20],
        }}
        self.race={"raceID":"202608050412","raceDate":"2026-11-15",
                   "commonWideReference":[]}

    def test_detects_top20_hidden_qualifiers_without_actually_selecting(self):
        result=race_selection({"signal":self.signal},self.race)
        self.assertEqual(result["fullPairs"],28)
        self.assertEqual(result["displayPairs"],20)
        self.assertEqual(result["qualifyingFullPairs"],8)
        self.assertEqual(result["qualifiedHiddenBeyondDisplay"],8)
        self.assertEqual(result["savedReferencePairs"],[])
        self.assertEqual(len(result["allPairCounterfactualPairs"]),2)
        self.assertTrue(result["top20WindowChangedSelectedPairs"])
        self.assertFalse(result["sourceTimestampAuthenticated"])

    def test_display_window_mismatch_rejected(self):
        bad=deepcopy(self.signal["wideEvaluation"])
        bad["pairs"][0]["number1"]=7
        with self.assertRaisesRegex(ValueError,"display window"):
            validate_signal_pairs(bad)

    def test_bad_rank_sequence_rejected(self):
        bad=deepcopy(self.signal["wideEvaluation"])
        bad["validationPairs"][3]["practicalRank"]=3
        with self.assertRaisesRegex(ValueError,"practical ranks"):
            validate_signal_pairs(bad)

    def test_bad_price_probability_ev_binding_rejected(self):
        bad=deepcopy(self.signal["wideEvaluation"])
        bad["validationPairs"][2]["conservativeExpectedValue"]=1000
        with self.assertRaisesRegex(ValueError,"unbound EV"):
            validate_signal_pairs(bad)

    def test_registered_reference_reproducibility_is_required(self):
        self.race["commonWideReference"]=[{"candidateID":"0102"}]
        with self.assertRaisesRegex(ValueError,"does not match saved signal"):
            race_selection({"signal":self.signal},self.race)

    def test_selected_probability_and_market_ratio_summary(self):
        self.assertEqual(summarize_candidates([]),{"pairs":0})
        x=summarize_candidates([{
            "probabilityPct":6.0,"oddsLow":20.0,
            "conservativeExpectedValue":1.2,
            "practicalRank":22,"practicalScore":1.2,
            "aiToMarketProbabilityRatio":1.1,
        }])
        self.assertEqual(x["pairs"],1)
        self.assertEqual(x["meanProbabilityPct"],6.0)
        self.assertEqual(x["withAIProbabilityHigherThanMarket"],1)


if __name__=="__main__":
    unittest.main()
