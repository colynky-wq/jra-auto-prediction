"""Causal new research features: field style pressure and prior performance dynamics.
All current-race feature values are emitted before any outcome on that date.
Only historical columns already present in the repository are required.
"""
from collections import defaultdict, deque

import numpy as np
import pandas as pd

from train_no_current_odds_challenger import ensure

STYLE = ("v4StyleStarts","v4OwnFrontShare","v4OwnPositionMean","v4OwnPositionSpread",
         "v4OtherFrontShare","v4OtherStyleCoverage","v4OtherFrontMass",
         "v4FrontPressureInteraction","v4PositionRelativeToField","v4Straight")
FORM = ("v4FormStarts","v4RecentFinishTrend","v4RecentMarginTrend",
        "v4RecentRatingTrend","v4LastOppStrength","v4LastOppStrengthVsToday",
        "v4RecentResultSurprise","v4RecentPositionGain","v4PositionGainStarts",
        "v4RecentClosingGap","v4ClosingStarts")
NEW = STYLE + FORM
COLS = ("日付","レースID","馬番","馬キー","芝ダ","距離","頭数","障害",
        "着順","初角","タイム秒","上がり3F")


def mean(a, default):
    return float(np.mean(a)) if len(a) else float(default)


def trend(a):
    # Recent two vs previous two observations; neutral when history insufficient.
    return mean(a[-2:],0)-mean(a[-4:-2],0) if len(a)>=4 else 0.


def build(history):
    d=history.loc[:,list(COLS)].copy()
    d["日付"]=pd.to_datetime(d["日付"],errors="coerce").dt.normalize()
    ensure(d["日付"].notna().all() and d["馬キー"].notna().all(),"missing date/horse key")
    ensure(not d.duplicated(["レースID","馬番"]).any(),"duplicate horse number")
    d=d.sort_values(["日付","レースID","馬番"]).reset_index(drop=True)
    out=np.empty((len(d),len(NEW)),dtype=np.float64)
    styles=defaultdict(lambda:deque(maxlen=6))
    forms=defaultdict(lambda:deque(maxlen=5))
    gains=defaultdict(lambda:deque(maxlen=5))
    closes=defaultdict(lambda:deque(maxlen=5))
    rating={}
    for _,day in d.groupby("日付",sort=False):
        pending=[]
        duplicate=set(day.loc[day.duplicated("馬キー",keep=False),"馬キー"])
        for rid,g in day.groupby("レースID",sort=False):
            keys=g["馬キー"].tolist(); n=len(keys)
            # Niigata turf 1000m is a straight race without corner-based style.
            straight=(str(rid)[4:6]=="04" and str(g["芝ダ"].iloc[0])=="芝"
                      and g["距離"].iloc[0]==1000)
            r=np.array([rating.get(k,1500.) for k in keys])
            position=[list(styles[k]) for k in keys]
            known=np.array([len(s)>=2 for s in position]) & (not straight)
            pos=np.array([mean(s,.5) for s in position])
            front=np.array([mean([v<=.25 for v in s],.25) for s in position])
            rank=pd.to_numeric(g["着順"],errors="coerce").to_numpy(float)
            corner=pd.to_numeric(g["初角"],errors="coerce").to_numpy(float)
            clock=pd.to_numeric(g["タイム秒"],errors="coerce").to_numpy(float)
            closing=pd.to_numeric(g["上がり3F"],errors="coerce").to_numpy(float)
            field=pd.to_numeric(g["頭数"],errors="coerce").to_numpy(float)
            distance=pd.to_numeric(g["距離"],errors="coerce").to_numpy(float)
            valid=(n>=3 and len(set(keys))==n and np.all(field==n)
                   and not g["障害"].fillna(True).astype(bool).any()
                   and np.isfinite(rank).all() and np.all(rank==np.floor(rank))
                   and np.all((rank>=1)&(rank<=n)) and np.sum(rank==1)==1
                   and not set(keys)&duplicate)
            # Earlier-day ratings only, full field. Never use today's finish in output.
            expected=1/(1+10**((r[None,:]-r[:,None])/400))
            np.fill_diagonal(expected,0)
            strength=(r.sum()-r)/max(n-1,1)
            for i,(idx,k) in enumerate(zip(g.index,keys)):
                other=known.copy();other[i]=False
                other_count=int(other.sum())
                pressure=mean(front[other],.25)
                other_pos=mean(pos[other],.5)
                own=position[i] if not straight else []
                own_pos=mean(own,.5)
                own_front=mean([v<=.25 for v in own],.25)
                history_form=list(forms[k])
                finish=[v[0] for v in history_form]
                margin=[v[1] for v in history_form if v[1] is not None]
                rates=[v[2] for v in history_form]
                last_opp=history_form[-1][3] if history_form else 1500.
                # Both opponent strength terms are prior ratings, no closing odds.
                out[idx]=(
                    len(own),own_front,own_pos,float(np.std(own)) if own else 0.,
                    pressure,other_count/max(n-1,1),float(front[other].sum()),
                    own_front*pressure,own_pos-other_pos,float(straight),
                    len(history_form),trend(finish),trend(margin),trend(rates),
                    last_opp-1500.,last_opp-strength[i] if history_form else 0.,
                    mean([v[4] for v in history_form],0.),
                    mean(list(gains[k]),0.),len(gains[k]),
                    mean(list(closes[k]),0.),len(closes[k]))
            if not valid:
                continue
            # All subsequent values are queued and applied only after this date.
            actual=(rank[:,None]<rank[None,:]).astype(float)+.5*(rank[:,None]==rank[None,:])
            np.fill_diagonal(actual,0.)
            surprise=(actual.sum(axis=1)-expected.sum(axis=1))/(n-1)
            winner=clock[rank==1][0]
            best_closing=np.min(closing[np.isfinite(closing)&(closing>0)]) if np.any(np.isfinite(closing)&(closing>0)) else np.nan
            for i,k in enumerate(keys):
                f=(rank[i]-1)/(n-1)
                p=(corner[i]-1)/(n-1) if not straight and np.isfinite(corner[i]) and 1<=corner[i]<=n else None
                m=float(np.clip((clock[i]-winner)*1000/distance[i],0,10)) if np.isfinite(clock[i]) and clock[i]>0 and np.isfinite(winner) and winner>0 and np.isfinite(distance[i]) and distance[i]>0 else None
                c=float(np.clip(closing[i]-best_closing,0,8)) if np.isfinite(closing[i]) and closing[i]>0 and np.isfinite(best_closing) else None
                pending.append((k,p,f,m,float(r[i]),float(strength[i]),float(surprise[i]),c))
        for k,p,f,m,r0,opp,s,c in pending:
            rating[k]=r0+32*s
            forms[k].append((f,m,r0,opp,s))
            if p is not None:
                styles[k].append(p);gains[k].append(p-f)
            if c is not None:
                closes[k].append(c)
    ensure(np.isfinite(out).all(),"nonfinite new features")
    result=d[["レースID","馬番"]].copy()
    result[list(NEW)]=out
    return result
