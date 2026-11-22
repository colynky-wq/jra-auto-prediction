"""Market-anchored research tests, without network or production mutation."""
import unittest
import numpy as np
import pandas as pd
from train_market_anchored_challenger import (
    POWERS, WEIGHTS, METHODS, combine, choose_2024, score_all
)


def test_frame(year=2024):
    d = pd.DataFrame({
        "日付": pd.to_datetime([f"{year}-03-01"] * 3 + [f"{year}-03-02"] * 3),
        "レースID": ["R1"] * 3 + ["R2"] * 3,
        "馬番": [1,2,3,1,2,3],
        "着順": [1,3,2,3,1,2],
        "市場確率": [.6,.2,.2,.3,.5,.2],
    })
    return d


class HistoricalEnsembleTests(unittest.TestCase):
    def test_market_only_and_history_anchored_normalization(self):
        d=test_frame()
        m=d["市場確率"].to_numpy(dtype=float)
        h=np.asarray([.3,.4,.3,.6,.2,.2])
        for method in METHODS:
            for weight in (0.0, .05, .25):
                p=combine(m,h,d["レースID"],market_power=1.0,
                          historical_weight=weight,method=method)
                self.assertAlmostEqual(float(p[:3].sum()), 1.0)
                self.assertAlmostEqual(float(p[3:].sum()), 1.0)
                self.assertGreater(float(p.min()), 0)
        np.testing.assert_allclose(
            combine(m,h,d["レースID"],market_power=1.0,
                    historical_weight=0.0,method="linear"), m, atol=1e-12)

    def test_current_market_values_cannot_be_replaced_with_non_probabilities(self):
        with self.assertRaises(ValueError):
            combine([0.3, float("nan"), .3], [.2,.5,.3], ["A"]*3,
                    market_power=1.0,historical_weight=0.05,method="linear")
        with self.assertRaises(ValueError):
            combine([0.3, .4, .3], [.2,.5,.3], ["A"]*3,
                    market_power=0.8,historical_weight=0.05,method="linear")
        with self.assertRaises(ValueError):
            combine([0.3, .4, .3], [.2,.5,.3], ["A"]*3,
                    market_power=1.0,historical_weight=0.35,method="linear")

    def test_parameter_selection_is_2024_only(self):
        d=test_frame()
        params,proof=choose_2024(d,np.asarray([.3,.4,.3,.6,.2,.2]))
        self.assertEqual(proof["2024Races"],2)
        self.assertEqual(proof["trials"], len(POWERS)*(1+len(METHODS)*len(WEIGHTS)))
        self.assertEqual(params["marketCalibrated"]["historicalWeight"],0)
        self.assertGreater(params["historyAugmented"]["historicalWeight"],0)
        bad=test_frame(2026)
        with self.assertRaisesRegex(ValueError,"2024 only"):
            choose_2024(bad,np.asarray([.3,.4,.3,.6,.2,.2]))

    def test_all_events_and_periods_same_parameters(self):
        d=test_frame(2025)
        selected={
            "marketCalibrated": {"marketPower":1.0,"historicalWeight":0.0,
                                  "method":"linear"},
            "historyAugmented": {"marketPower":1.05,"historicalWeight":0.05,
                                 "method":"geometric"},
        }
        out=score_all(d,np.asarray([.3,.4,.3,.6,.2,.2]),selected,period="2025")
        self.assertEqual(out["races"],2)
        self.assertEqual(out["horses"],6)
        self.assertEqual(set(out["events"]),{"win","top2","top3"})
        for event in ("win","top2","top3"):
            b=out["events"][event]["pooledHorseBrier"]
            self.assertAlmostEqual(b["finalMarket"],b["marketCalibrated"],places=9)


if __name__=="__main__":
    unittest.main()
