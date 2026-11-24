"""Prior-day official race-sectional history, not today's realized pace.
No interpolation; exact early measurement distance is part of reference key.
Course references are built online from earlier dates only.
"""
from collections import defaultdict,deque
import json
import re
import hashlib
from urllib.parse import urlparse
import numpy as np
import pandas as pd
from train_no_current_odds_challenger import ensure

COVER=("v5LapStarts","v5ReferenceStarts","v5SameSurfaceStarts","v5LastStartHasLap")
PERF=("v5MeanEarlyVsCourse","v5LastEarlyVsCourse","v5MeanLateVsCourse",
      "v5FastStarts","v5SlowStarts","v5FastPlaceRate","v5SlowPlaceRate",
      "v5FastFinishMean","v5SlowFinishMean","v5MeanEarlyLateContrast",
      "v5MeanOwnClosingVsRace","v5OwnClosingStarts","v5SameSurfacePlaceRate",
      "v5MeanSameSurfaceEarlyVsCourse")
NEW=COVER+PERF
MIN_REF=20
COLS=("日付","レースID","馬番","馬キー","芝ダ","距離","頭数","障害","着順","タイム秒","上がり3F")
REQUIRED_VALID=("headingMatches","distanceMatches","winnerTimeMatches","raceLast600mMatches")


def check_record(r):
    try:
        u=urlparse(r["sourceURL"])
        if (u.scheme!="https" or u.hostname not in {"www.jra.go.jp","jra.go.jp","www.jra.jp","jra.jp","own.jra.jp"}
            or not re.fullmatch(r"[0-9a-f]{64}",r["sourceSHA256"]) or
            not re.fullmatch(r"\d{12}",r["raceID"])
            or not r["raceDate"].startswith(r["raceID"][:4]+"-")
            or r["surface"] not in ("芝","ダ") or r["going"] not in ("良","稍重","重","不良")
            or not all(r["validation"].get(k) is True for k in REQUIRED_VALID)
            or not all(v is True for v in r["validation"].values())):
            return False
        lens=np.asarray(r["lapDistancesM"],float); times=np.asarray(r["lapTimesSeconds"],float)
        n=len(lens);dist=r["distanceM"]
        if (n<3 or len(times)!=n or not np.isfinite(times).all() or not np.isfinite(lens).all()
            or not np.all((times>0)&(times<60)) or lens[0]!=(dist%200 or 200)
            or not np.all(lens[1:]==200) or lens.sum()!=dist or lens[-3:].sum()!=600):
            return False
        ends=np.cumsum(lens); i=int(np.searchsorted(ends,600))
        return (ends[i]==r["earlyRecordedDistanceM"] and
                abs(times[:i+1].sum()-r["earlyRecordedTimeSeconds"])<=.11 and
                abs(times.sum()-r["winnerTimeSeconds"])<=.31 and
                abs(times[-3:].sum()-r["raceLast600mSeconds"])<=.31)
    except (KeyError,ValueError,TypeError,IndexError,AttributeError):
        return False


def load(root):
    records={}; invalid=0; files=0; hash_=hashlib.sha256()
    for path in sorted(root.glob("*/RaceLapData.json")):
        raw=path.read_bytes();hash_.update(path.parent.name.encode());hash_.update(raw)
        pack=json.loads(raw);files+=1
        day=pd.Timestamp(pack["raceDate"])
        ensure(day.strftime("%Y%m%d")==path.parent.name,"lap file date mismatch")
        for r in pack["races"]:
            ensure(r["raceDate"]==pack["raceDate"],"lap record date mismatch")
            ensure(r["raceID"] not in records,"duplicate lap race")
            records[r["raceID"]]=r if check_record(r) else None
            invalid+=int(records[r["raceID"]] is None)
    ensure(len(records)>1000,"insufficient lap archive")
    return records,{"files":files,"records":len(records),"invalidRecords":invalid,"archiveSha256":hash_.hexdigest()}


def mean(a,default=0.):
    return float(np.mean(a)) if len(a) else float(default)


def rate(a):
    return (sum(x["hit"] for x in a)+.42)/(len(a)+2)


def build(history, records):
    d=history.loc[:,list(COLS)].copy()
    d["日付"]=pd.to_datetime(d["日付"],errors="coerce").dt.normalize()
    ensure(d["日付"].notna().all() and d["馬キー"].notna().all(),"invalid horse/date")
    d=d.loc[d["日付"].dt.year.ge(2024)].sort_values(["日付","レースID","馬番"]).reset_index(drop=True)
    ensure(not d.duplicated(["レースID","馬番"]).any(),"duplicate history identity")
    out=np.zeros((len(d),len(NEW)),float)
    states=defaultdict(lambda:deque(maxlen=6))
    refs=defaultdict(lambda:deque(maxlen=300))
    audit=defaultdict(lambda:defaultdict(int))
    for date,day in d.groupby("日付",sort=False):
        pending=[]; reference_updates=[]
        duplicated=set(day.loc[day.duplicated("馬キー",keep=False),"馬キー"])
        for rid,g in day.groupby("レースID",sort=False):
            surface=str(g["芝ダ"].iloc[0]); dist=g["距離"].iloc[0]
            n=len(g); keys=g["馬キー"].tolist()
            flat=not g["障害"].fillna(True).astype(bool).any()
            if flat: audit[str(date.year)]["historyFlatRaces"]+=1
            # Emit entirely BEFORE consulting the current race lap or finish.
            for idx,k in zip(g.index,keys):
                h=list(states[k]); lap=[v for v in h if v is not None]
                referenced=[v for v in lap if v["early"] is not None]
                fast=[v for v in referenced if v["bucket"]=="fast"]
                slow=[v for v in referenced if v["bucket"]=="slow"]
                same=[v for v in lap if v["surface"]==surface]
                clos=[v["ownClose"] for v in lap if v["ownClose"] is not None]
                out[idx]=(len(lap),len(referenced),len(same),float(bool(h) and h[-1] is not None),
                    mean([v["early"] for v in referenced]),referenced[-1]["early"] if referenced else 0.,
                    mean([v["late"] for v in referenced]),len(fast),len(slow),rate(fast),rate(slow),
                    mean([v["finish"] for v in fast],.5),mean([v["finish"] for v in slow],.5),
                    mean([v["shape"] for v in lap]),mean(clos),len(clos),rate(same),
                    mean([v["early"] for v in same if v["early"] is not None]))
            # Now inspect historical outcomes only to update FUTURE dates.
            if not flat or set(keys)&duplicated:
                continue
            rank=pd.to_numeric(g["着順"],errors="coerce").to_numpy(float)
            times=pd.to_numeric(g["タイム秒"],errors="coerce").to_numpy(float)
            close=pd.to_numeric(g["上がり3F"],errors="coerce").to_numpy(float)
            valid=(n>=3 and g["頭数"].eq(n).all() and np.isfinite(rank).all()
                   and np.all(rank==np.floor(rank)) and np.all((rank>=1)&(rank<=n))
                   and all(np.sum(rank==i)==1 for i in (1,2,3)))
            if not valid:
                continue
            r=records.get(str(rid))
            ok=bool(r and check_record(r) and r["raceDate"]==date.date().isoformat()
                   and r["surface"]==surface and r["distanceM"]==dist
                   and g["芝ダ"].eq(surface).all() and g["距離"].eq(dist).all()
                   and np.isfinite(times[rank==1]).all()
                   and abs(times[rank==1][0]-r["winnerTimeSeconds"])<=.051)
            if not ok:
                audit[str(date.year)]["completeRacesWithoutMatchedLap"]+=1
                pending.extend((k,None) for k in keys)
                continue
            audit[str(date.year)]["matchedLapRaces"]+=1
            early=r["earlyRecordedTimeSeconds"]; late=r["raceLast600mSeconds"]
            length=r["earlyRecordedDistanceM"]
            # Exact course, going, measured endpoint; no fake 600m.
            refkey=(str(rid)[4:6],surface,int(dist),str(r["courseDetail"]),r["going"],length)
            ref=list(refs[refkey]); e=l=None; bucket=None
            if len(ref)>=MIN_REF:
                a=np.asarray(ref,float)
                q25,med,q75=np.quantile(a[:,0],[.25,.5,.75])
                e=float(early-med);l=float(late-np.median(a[:,1]))
                bucket="fast" if early<q25 else "slow" if early>q75 else "middle"
                audit[str(date.year)]["matchedWithPriorReference"]+=1
            reference_updates.append((refkey,(early,late)))
            # Seconds per 200m, not an interpolated sectional time.
            shape=early*200/length-late/3
            for i,k in enumerate(keys):
                own=float(close[i]-late) if np.isfinite(close[i]) and close[i]>0 else None
                pending.append((k,{"surface":surface,"early":e,"late":l,"bucket":bucket,
                    "shape":shape,"ownClose":own,"hit":int(rank[i]<=3),"finish":float((rank[i]-1)/(n-1))}))
        for k,value in pending:
            states[k].append(value)
        for key,value in reference_updates:
            refs[key].append(value)
    ensure(np.isfinite(out).all(),"nonfinite lap features")
    result=d[["レースID","馬番"]].copy();result[list(NEW)]=out
    return result,{k:dict(v) for k,v in audit.items()}
