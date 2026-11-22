"""Network-free guards for the underdog-specific Top3 market residual."""
import unittest
import numpy as np
import pandas as pd

from train_longshot_top3 import (
    POPRANK_MIN, WINODDS_MIN, FEATURES, WEIGHTS,
    choose_multiplier, infer_without_results, odds_underdog,
    prepare_longshot, score, training_targets, sigmoid,
)


def sample(year=2024):
    rows=[]
    for day in (1,2):
        odds=[2.,4.,6.,8.,9.,10.,20.,30.]
        if day==1:
            ranks=[2,3,4,5,6,1,7,8]   # Rare 6th favorite podium finish
        else:
            ranks=[1,2,3,4,5,6,7,8]   # No qualifying longshot in Top3
        for i,odds_i in enumerate(odds):
            row={"レースID":f"R{year}{day}","馬番":i+1,"頭数":8,
                 "日付":pd.Timestamp(year,3,day),
                 "人気":i+1,"単勝オッズ":odds_i,
                 "着順":ranks[i],"市場確率":1/odds_i/sum(1/o for o in odds)}
            row.update({f:float(i+1) for f in FEATURES})
            rows.append(row)
    return pd.DataFrame(rows)


class FakeBooster:
    def feature_name(self):
        return list(FEATURES)
    def predict(self,frame,raw_score=True,validate_features=True):
        assert raw_score and validate_features
        assert "市場ロジット" not in frame.columns
        return np.repeat(.4,len(frame))


class LongshotTop3Tests(unittest.TestCase):
    def test_6th_and_odds_10_are_joint_requirements(self):
        odds=[9.9,10.,12.,2.]
        ranks=[6,5,6,9]
        self.assertEqual(odds_underdog(ranks,odds).tolist(),
                         [False,False,True,False])
        with self.assertRaisesRegex(ValueError,"invalid underdog identification"):
            odds_underdog([6],[float("nan")])

    def test_full_field_market_reference_calculated_before_filtering(self):
        raw=sample()
        allrunners=raw.loc[raw["レースID"]=="R20241"]
        underdogs=prepare_longshot(raw)
        self.assertEqual(len(underdogs),6)
        self.assertTrue((underdogs["人気"]>=POPRANK_MIN).all())
        self.assertTrue((underdogs["単勝オッズ"]>=WINODDS_MIN).all())
        self.assertGreater(underdogs["市場3着内基準"].min(),0)
        self.assertLess(underdogs["市場3着内基準"].max(),1)
        expected=float(underdogs.loc[(underdogs["馬番"]==6)&
                                     (underdogs["レースID"]=="R20241"),
                                      "市場3着内基準"].iloc[0])
        # A horse 6th by odds has much lower Top3 chance in a full
        # 8-runner race than if all 5 favourites were wrongly removed.
        self.assertLess(expected,0.8)
        y,_=training_targets(underdogs)
        self.assertEqual(int(y.sum()),1)

    def test_incomplete_field_cannot_renormalize_market_after_dropping_runner(self):
        full=sample()
        broken=full[~((full["レースID"]=="R20241") &
                      (full["馬番"]==1))].reset_index(drop=True)
        result=prepare_longshot(broken)
        self.assertEqual(result["レースID"].unique().tolist(),["R20242"])

    def test_inference_requires_no_result_or_finish_column(self):
        data=prepare_longshot(sample())
        fake=FakeBooster()
        no_finish=data.loc[:,list(FEATURES)].copy()
        base=data["市場3着内基準"].to_numpy()
        raw=infer_without_results(no_finish,base,fake,1.0)
        zero=infer_without_results(no_finish,base,fake,0.0)
        np.testing.assert_allclose(zero,base,atol=1e-9)
        self.assertTrue(np.all(raw>base))
        with self.assertRaisesRegex(ValueError,"unregistered"):
            infer_without_results(no_finish,base,fake,.7)
        with self.assertRaisesRegex(ValueError,"missing pre-race features"):
            infer_without_results(no_finish.drop(columns=[FEATURES[0]]),base,fake,1.)

    def test_2024_only_model_weight_selection(self):
        d=prepare_longshot(sample())
        selected,options=choose_multiplier(d,FakeBooster())
        self.assertIn(selected["residualMultiplier"],WEIGHTS)
        self.assertEqual(len(options),4)
        with self.assertRaisesRegex(ValueError,"only 2024"):
            choose_multiplier(prepare_longshot(sample(2026)),FakeBooster())

    def test_score_report_honors_actual_longshots_and_base(self):
        d=prepare_longshot(sample(2025))
        result=score(d,FakeBooster(),.0,"2025")
        self.assertEqual(result["races"],2)
        self.assertEqual(result["underdogHorseRows"],6)
        self.assertEqual(result["underdogPodiumRows"],1)
        self.assertEqual(result["topRankedLongshotPodiumHits"]["market"],1)
        self.assertAlmostEqual(result["candidateMinusMarketHorseBrier"],0,places=10)
        self.assertIsNone(result["pairedRaceBootstrap95"])


if __name__=="__main__":
    unittest.main()
