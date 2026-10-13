"""Used-history diagnostics, not a new independent validation."""
from pathlib import Path
import json
import hashlib
import numpy as np
import pandas as pd
from research_prerace_pace import COLUMNS,replay,references,prepare
from research_pace_classifier import attach_class,probabilities,CUTOFF
ROOT=Path(__file__).resolve().parent.parent

def threshold_stats(y,p,t):
    called=p>=t;actual=y==2;tp=int(sum(called&actual));n=int(sum(called));total=int(sum(actual))
    return {'threshold':t,'calledRaces':n,'trueSlow':tp,'falseSlow':n-tp,'precisionPct':round(tp/n*100,2) if n else None,'recallPct':round(tp/total*100,2) if total else None}

def main():
    h=pd.read_parquet(ROOT/'production/production_history.parquet',columns=COLUMNS+['クラス'])
    h=h[pd.to_datetime(h['日付'])<=CUTOFF]
    laps=[]
    for path in sorted((ROOT/'research/laps').glob('*/RaceLapData.json')):laps.extend(json.loads(path.read_text())['races'])
    laps=[r for r in laps if '2024-01-01'<=r['raceDate']<=CUTOFF]
    rows=attach_class(replay(h,laps,last_year=2026),h)
    refs=references(rows[rows.year==2024]);train=prepare(rows[rows.year<=2025],refs);test=prepare(rows[rows.year==2026],refs)
    p=probabilities(train,test,'logistic:0.1');y=test.label.to_numpy();slow=p[:,2]
    grid=[threshold_stats(y,slow,t) for t in [.20,.25,.30,.35,.40,.45,.50]]
    eligible=[r for r in grid if r['calledRaces']>=30 and r['precisionPct']>=50 and r['recallPct']>=20]
    selected=max(eligible,key=lambda r:r['recallPct'])['threshold'] if eligible else None
    bins=[]
    for lo,hi in [(0,.1),(.1,.2),(.2,.3),(.3,.4),(.4,.5),(.5,1.01)]:
        m=(slow>=lo)&(slow<hi)
        bins.append({'probabilityRange':[lo,hi],'races':int(sum(m)),'meanPredictedSlowPct':round(float(slow[m].mean())*100,2) if sum(m) else None,'actualSlowPct':round(float(np.mean(y[m]==2))*100,2) if sum(m) else None})
    result={'schemaVersion':1,'methodVersion':'slow-pace-used-history-diagnostics-v3','status':'development_only','usedHistoryThrough':CUTOFF,'independentEvaluation':False,'races':len(test),'actualSlowRaces':int(sum(y==2)),'argmaxSlowRecallPct':round(float(np.mean(p[y==2].argmax(axis=1)==2))*100,2),'thresholdGrid':grid,'calibrationBins':bins,'chosenResearchSlowFlagThreshold':selected,'selectionRule':'At least 30 flags, precision >=50%, slow recall >=20% on USED 2026 history; future observations required, no app adoption.','paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False,'meaning':'Threshold analysis reuses the previously evaluated 2026 period for development. It cannot be presented as independent improvement.'}
    (ROOT/'reports/PaceSlowDiagnostics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    # Scratch checkpoint for deterministic final fitting and numerical export tests.
    trainall=prepare(rows,refs);trainall.to_pickle(ROOT/'tmp/pace-prospective-training.pkl')
    (ROOT/'tmp/pace-prospective-references.json').write_text(json.dumps([{'condition':list(k),'quantiles':v.tolist()} for k,v in refs.items()],ensure_ascii=False))
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':
    (ROOT/'tmp').mkdir(exist_ok=True);main()
