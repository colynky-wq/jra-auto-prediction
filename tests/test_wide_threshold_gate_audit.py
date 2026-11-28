"""Frozen probability vs recorded-price EV gate; independent of outcomes."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"production"))
from wide_threshold_gate_audit import (
    gate_label,qualified_pair_outcomes,statistics,GROUP_LABELS,
    ev_comparison_in_strata,ev_comparison_by_date,
)


class WideThresholdGateTests(unittest.TestCase):
    def setUp(self):
        pairs=[
            (1,2,15.0,8.0),(1,3,10.0,8.0),(1,4,5.0,22.0),
            (2,3,4.0,28.0),(2,4,3.0,30.0),(3,4,2.0,40.0),
        ]
        arr=[]
        for rank,(a,b,p,odds) in enumerate(pairs,1):
            arr.append({
                "number1":a,"number2":b,
                "aiWideProbability":p,
                "wideOddsLow":odds,
                "conservativeExpectedValue":round(p/100*odds,3),
                "practicalRank":rank,
                "practicalScore":1.2-rank*0.1,
                "marketWideProbability":p+1,
            })
        self.signal={"wideEvaluation":{"status":"live_odds_connected",
                                      "pairs":arr,"validationPairs":arr}}
        self.result={
            "raceDate":"2026-11-15","raceID":"202608050412",
            "resultState":"matched",
            "actualFinish":{"1":1,"2":2,"3":3,"4":4},
        }
        self.horses=[{"number":n} for n in range(1,5)]

    def test_boundaries_exactly_five_percent_and_1_10_ev(self):
        self.assertEqual(gate_label(5,1.1),GROUP_LABELS[0])
        self.assertEqual(gate_label(5,1.099),GROUP_LABELS[1])
        self.assertEqual(gate_label(4.999,1.1),GROUP_LABELS[2])
        self.assertEqual(gate_label(4.999,1.099),GROUP_LABELS[3])

    def test_reject_negative_or_nonfinite_gate(self):
        with self.assertRaisesRegex(ValueError,"invalid probability"):
            gate_label(-1,1.2)
        with self.assertRaisesRegex(ValueError,"invalid number"):
            gate_label(float("nan"),1.2)

    def test_pair_partition_uses_pre_result_thresholds(self):
        rows,reason=qualified_pair_outcomes(self.signal,self.result,self.horses)
        self.assertIsNone(reason)
        self.assertEqual(len(rows),6)
        by_group={k:[x for x in rows if x["gate"]==k] for k in GROUP_LABELS}
        self.assertEqual([len(by_group[k]) for k in GROUP_LABELS],[2,1,1,2])
        self.assertEqual([sum(x["hit"] for x in by_group[k]) for k in GROUP_LABELS],
                         [1,1,1,0])
        self.assertEqual(statistics(by_group[GROUP_LABELS[0]])["expectedHits"],0.2)
        self.assertEqual(statistics(rows)["observedHits"],3)

    def test_declines_missing_finish_instead_of_false_negative(self):
        self.result["actualFinish"]["4"]=None
        rows,reason=qualified_pair_outcomes(self.signal,self.result,self.horses)
        self.assertIsNone(rows)
        self.assertEqual(reason,"incomplete_finish")

    def test_not_matched_not_counted(self):
        self.result["resultState"]="pending"
        rows,reason=qualified_pair_outcomes(self.signal,self.result,self.horses)
        self.assertIsNone(rows)
        self.assertEqual(reason,"not_matched")

    def test_impossible_extra_or_missing_pairs_rejected(self):
        self.signal["wideEvaluation"]["validationPairs"].pop()
        self.signal["wideEvaluation"]["pairs"].pop()
        with self.assertRaisesRegex(ValueError,"full Wide signal pair coverage"):
            qualified_pair_outcomes(self.signal,self.result,self.horses)

    def test_ev_gate_comparison_within_fixed_forecast_bands(self):
        rows,_=qualified_pair_outcomes(self.signal,self.result,self.horses)
        groups=ev_comparison_in_strata(rows)
        self.assertEqual(len(groups),4)
        over_five_under_ten=groups[1]
        self.assertEqual(over_five_under_ten["forecastProbabilityBand"],"5-<10%")
        self.assertEqual(over_five_under_ten["priceEVAtLeast1_10"]["pairs"],1)
        self.assertEqual(over_five_under_ten["priceEVBelow1_10"]["pairs"],0)
        above_ten=groups[2]
        self.assertEqual(above_ten["priceEVAtLeast1_10"]["pairs"],1)
        self.assertEqual(above_ten["priceEVBelow1_10"]["pairs"],1)

    def test_date_groups_do_not_interchange_price_gate(self):
        rows,_=qualified_pair_outcomes(self.signal,self.result,self.horses)
        grouped=ev_comparison_by_date(rows)
        self.assertEqual(len(grouped),1)
        self.assertEqual(grouped[0]["priceEVAtLeast1_10"]["pairs"],3)
        self.assertEqual(grouped[0]["priceEVBelow1_10"]["pairs"],3)
        self.assertEqual(grouped[0]["priceEVAtLeast1_10"]["observedHits"],2)
        self.assertEqual(grouped[0]["priceEVBelow1_10"]["observedHits"],1)

    def test_stats_no_price_or_probability_authentication_claim(self):
        self.assertEqual(statistics([])["pairs"],0)
        rows,reason=qualified_pair_outcomes(self.signal,self.result,self.horses)
        self.assertEqual(statistics(rows)["aiProbabilityNoHigherThanWinDerivedMarketCount"],6)


if __name__=="__main__":
    unittest.main()
