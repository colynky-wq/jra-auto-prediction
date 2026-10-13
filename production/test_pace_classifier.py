import unittest
import pandas as pd
import numpy as np
from research_pace_classifier import attach_class,probabilities,measures
from research_prerace_pace import FEATURES
class TestClassifier(unittest.TestCase):
    def test_class_consistency(self):
        rows=pd.DataFrame({'raceID':['a']})
        with self.assertRaises(ValueError):attach_class(rows,pd.DataFrame({'レースID':['a','a'],'クラス':['1勝','2勝']}))
        self.assertEqual(attach_class(rows,pd.DataFrame({'レースID':['a'],'クラス':['1勝']})).raceClass.iloc[0],'1勝')
    def test_baseline_uses_training_only(self):
        train=pd.DataFrame({'venue':['05']*6,'surface':['芝']*6,'distanceM':[1200]*6,'earlyDistanceM':[600]*6,'label':[0,1,1,1,1,2]})
        test=train.copy();a=probabilities(train,test,'coursePrior');test.label=2
        np.testing.assert_array_equal(a,probabilities(train,test,'coursePrior'))
        np.testing.assert_allclose(a.sum(axis=1),1)
    def test_confusion_matrix(self):
        r,*_=measures(pd.DataFrame({'label':[0,1,2]}),np.eye(3))
        self.assertEqual(r['accuracyPct'],100);self.assertEqual(r['brierScore'],0)
    def test_feature_whitelist(self):
        self.assertNotIn('label',FEATURES);self.assertNotIn('actualSeconds',FEATURES)
if __name__=='__main__':unittest.main()
