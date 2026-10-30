from datetime import datetime
import json
import math

import numpy as np

from ticket_probability_runtime import _pl_marginals
from ticket_shadow_forward import JST, record_manifest_first, record_prediction_first, validate_manifest, validate_prediction


def _fixture():
    numbers=[1,2,3,4,5]
    win=np.array([.34,.25,.18,.13,.10],dtype=float)
    _,top2,top3=_pl_marginals(win)
    race={
        'raceDate':'2026-11-01','raceID':'202605040111','venue':'東京','raceNumber':11,
        'startTime':'15:40','predictionTiming':'live_10min','captureQuality':'formal_10min',
        'capturedAtJST':'2026-11-01T15:30:00+09:00',
        'horses':[{'number':n,'name':f'H{n}'} for n in numbers],
    }
    inputs={'numbers':numbers,'win':win.tolist(),'top2':top2.tolist(),'top3':top3.tolist()}
    return race,inputs


def test_prediction_is_full_precision_shadow_only_and_immutable(tmp_path):
    race,inputs=_fixture()
    clock=lambda:datetime.fromisoformat('2026-11-01T15:30:01+09:00')
    path=record_prediction_first(tmp_path,race,inputs,clock=clock)
    first=path.read_bytes()
    row=validate_prediction(json.loads(first))
    assert row['researchLabel']=='🧪研究モデル'
    assert row['formalPrediction']['predictionTiming']=='live_10min'
    assert row['formalPrediction']['capturedAtJST']=='2026-11-01T15:30:00+09:00'
    assert row['autoBetEnabled'] is False and row['recommendationEnabled'] is False
    assert row['selectedBets']==0 and row['stakes'] is None
    assert math.isclose(sum(x['win'] for x in row['horseMarginals']),1,abs_tol=1e-12)
    assert math.isclose(sum(x['top2'] for x in row['horseMarginals']),2,abs_tol=1e-12)
    assert math.isclose(sum(x['top3'] for x in row['horseMarginals']),3,abs_tol=1e-12)
    p=row['ticketProbabilities']
    assert len(p['wide'])==10 and len(p['quinella'])==10 and len(p['exacta'])==20
    assert len(p['trio'])==10 and len(p['trifecta'])==60
    for kind in ('quinella','exacta','trio','trifecta'):
        assert math.isclose(sum(p[kind].values()),1,abs_tol=1e-8)
    # A later call cannot replace the first prospective bytes.
    changed={**inputs,'top2':(np.asarray(inputs['top2'])+1e-4).tolist()}
    assert record_prediction_first(tmp_path,race,changed,clock=clock)==path
    assert path.read_bytes()==first


def test_manifest_records_missing_odds_explicitly_without_bet_decision(tmp_path):
    race,inputs=_fixture()
    prediction_clock=lambda:datetime.fromisoformat('2026-11-01T15:30:01+09:00')
    record_prediction_first(tmp_path,race,inputs,clock=prediction_clock)
    manifest_clock=lambda:datetime.fromisoformat('2026-11-01T15:30:02+09:00')
    path=record_manifest_first(tmp_path,race,clock=manifest_clock)
    row=validate_manifest(json.loads(path.read_text(encoding='utf-8')))
    assert row['formalPredictionCapturedAtJST']=='2026-11-01T15:30:00+09:00'
    assert row['autoBetEnabled'] is False and row['recommendationEnabled'] is False
    assert row['selectedBets']==0 and row['stakes'] is None
    assert set(row['oddsEvidence'])=={'wide','quinella','exacta','trio','trifecta'}
    assert all(x['status']=='unavailable' for x in row['oddsEvidence'].values())
    assert all(x['usableForForwardROI'] is False for x in row['oddsEvidence'].values())
    assert all(x['reason']=='odds_record_missing' for x in row['oddsEvidence'].values())


def test_nonformal_capture_is_rejected(tmp_path):
    race,inputs=_fixture(); race['predictionTiming']='late_recovery'; race['captureQuality']='late_recovery'
    try:
        record_prediction_first(tmp_path,race,inputs,clock=lambda:datetime.fromisoformat('2026-11-01T15:30:01+09:00'))
    except ValueError as error:
        assert 'formal live_10min' in str(error)
    else:
        raise AssertionError('late recovery must never enter prospective ticket shadow')
