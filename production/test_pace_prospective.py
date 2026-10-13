import copy
from datetime import datetime,timedelta,timezone
import hashlib,json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
from pace_prospective_runtime import field_features,forecast,softmax_predict,record_first_prediction
from evaluate_prospective_pace import check_prediction,run
JST=timezone(timedelta(hours=9));NOW=datetime(2026,10,17,9,40,tzinfo=JST)

def fixture():
    h=[];laps=[]
    for i,day in enumerate(['2026-10-01','2026-10-03','2026-10-05']):
        rid=f'2026050401{i+1:02d}'
        laps.append(dict(raceID=rid,raceDate=day,earlyRecordedDistanceM=600,earlyRecordedTimeSeconds=36,going='良',validation={'ok':True}))
        for n in [1,2,3]:h.append({'レースID':rid,'日付':day,'馬番':n,'馬名':f'テスト{n}','馬キー':f'テスト{n}_2022','初角':n,'頭数':3,'障害':False,'芝ダ':'芝','距離':1200})
    obs=dict(raceID='202605040601',raceDate='2026-10-17',distanceM=1200,surface='芝',raceClass='1勝',runners=[{'number':n,'name':f'テスト{n}','age':4,'withdrawn':False} for n in [1,2,3]],activeEntrantsAtCapture=3,capturedAtJST=(NOW-timedelta(minutes=1)).isoformat(),scheduledPostJST=(NOW+timedelta(minutes=10)).isoformat(),prePostConfirmed=True)
    m=dict(methodVersion='unit-v3',modelID='unit-model',trainingCutoffDate='2026-10-12',fittedAtJST=datetime(2026,10,13,14,tzinfo=JST).isoformat(),numericFeatures=['fieldSize'],categoricalFeatures=['raceClass'],numericMean=[0],numericScale=[1],categories=[['1勝']],coefficients=[[0,0],[0,0],[0,0]],intercepts=[0,1,0],classes=[0,1,2],references=[{'condition':['05','芝',1200,600],'quantiles':[35,36,37],'prior':[.2,.6,.2]}],slowFlagThreshold=None)
    actual=dict(raceID=obs['raceID'],raceDate=obs['raceDate'],surface='芝',distanceM=1200,earlyRecordedDistanceM=600,earlyRecordedTimeSeconds=38,going='良',validation={'ok':True})
    return pd.DataFrame(h),laps,obs,m,actual

class TestProspective(unittest.TestCase):
    def test_own_day_and_future_results_do_not_enter(self):
        h,laps,o,m,_=fixture();a=field_features(o,h,laps)
        extra=h.copy();extra['日付']=o['raceDate'];extra['初角']=1
        future=h.copy();future['日付']='2026-11-01'
        b=field_features(o,pd.concat([h,extra,future]),laps)
        self.assertEqual(a,b);self.assertLess(a['latestSourceDay'],o['raceDate'])
    def test_same_name_new_generation_not_borrowed(self):
        h,laps,o,m,_=fixture();new=h.iloc[-3:].copy();new['日付']='2026-10-10';new['馬キー']=[f'テスト{n}_2024' for n in [1,2,3]];new['距離']=1000;new['レースID']='202604040401'

        for runner in o['runners']:runner['age']=2
        pred=forecast(o,pd.concat([h,new]),laps,m,NOW)
        self.assertEqual(pred['status'],'held');self.assertEqual(pred['holdReason'],'insufficient_style_history')
    def test_withdrawal_is_removed(self):
        h,laps,o,m,_=fixture();o['runners'][0]['withdrawn']=True;o['activeEntrantsAtCapture']=2
        with self.assertRaises(ValueError):field_features(o,h,laps)
    def test_json_softmax(self):
        h,laps,o,m,_=fixture();p=softmax_predict(m,field_features(o,h,laps))
        self.assertAlmostEqual(sum(p),1);self.assertEqual(int(p.argmax()),1)
    def test_not_after_post_or_before_model_fit(self):
        h,laps,o,m,_=fixture()
        for now in [NOW+timedelta(minutes=10),NOW-timedelta(days=5)]:
            with self.assertRaises(ValueError):forecast(o,h,laps,m,now)
        m['trainingCutoffDate']='2026-10-17'
        with self.assertRaises(ValueError):forecast(o,h,laps,m,NOW)
    def test_predicted_then_scored(self):
        h,laps,o,m,actual=fixture();p=forecast(o,h,laps,m,NOW)
        row,reason=check_prediction(p,o,actual,[1,2,3])
        self.assertIsNone(reason);self.assertEqual(row['actual'],2);self.assertFalse(row['correct'])
        self.assertFalse(p['paceForecastEnabled']);self.assertFalse(p['formalSignalsChanged'])
    def test_changed_entries_not_silently_scored(self):
        h,laps,o,m,actual=fixture();p=forecast(o,h,laps,m,NOW)
        row,reason=check_prediction(p,o,actual,[1,2,4]);self.assertIsNone(row);self.assertEqual(reason,'entrant_set_changed')
    def test_cannot_rewrite_first_prediction(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'PacePrediction.json';p.write_text('immutable')
            self.assertIsNone(record_first_prediction(Path(root)/'snapshot.json'));self.assertEqual(p.read_text(),'immutable')
    def test_ledger_validation_and_missing_records(self):
        h,laps,o,m,actual=fixture();p=forecast(o,h,laps,m,NOW)
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);folder=root/'reports/20261017/prerace'/o['raceID'];folder.mkdir(parents=True)
            (folder/'index.json').write_text('{}');snapshot=folder/'sample.json';snapshot.write_text(json.dumps(o))
            models=root/'models';models.mkdir();mf=models/'unit.json';mf.write_text(json.dumps(m))
            lapdir=root/'laps/20261017';lapdir.mkdir(parents=True);(lapdir/'RaceLapData.json').write_text(json.dumps({'races':[actual]}))
            final=pd.DataFrame({'レースID':[o['raceID']]*3,'馬番':[1,2,3]})
            missing=run(root/'reports',root/'laps',final,models);self.assertEqual(missing['counts']['missingPrediction'],1)
            p.update(snapshotFile='sample.json',snapshotSHA256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),modelFileSHA256=hashlib.sha256(mf.read_bytes()).hexdigest())
            (folder/'PacePrediction.json').write_text(json.dumps(p))
            report=run(root/'reports',root/'laps',final,models);self.assertEqual(report['counts']['evaluatedRaces'],1)
            snapshot.write_text('changed')
            report=run(root/'reports',root/'laps',final,models);self.assertEqual(report['counts']['invalidRecords'],1)
            self.assertEqual(report['counts']['evaluatedRaces'],0)
    def test_snapshot_availability_matches(self):
        h,laps,o,m,actual=fixture();p=forecast(o,h,laps,m,NOW)
        o['completeInputAvailableAtJST']=(NOW+timedelta(minutes=11)).isoformat()
        with self.assertRaises(ValueError):check_prediction(p,o,actual,[1,2,3])
if __name__=='__main__':unittest.main()
