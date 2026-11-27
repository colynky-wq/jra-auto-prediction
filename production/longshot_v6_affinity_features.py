"""Prior-date horse/course/jockey affinity and historical wet/dry profiles.
Past market residuals are computed only when updating earlier completed races.
Current going is deliberately not an input.
"""
from collections import defaultdict
import re
import unicodedata
import numpy as np
import pandas as pd
from train_no_current_odds_challenger import ensure
from train_top2_top3_models import plackett_luce_top2_top3
from longshot_v5_lap_features import check_record

COURSE_GROUPS=("HorseVenue","HorseCourse","HorseDistanceVenueBand")
JOCKEY_GROUPS=("Pair","PairSurface","JockeyVenue","JockeyCourse","JockeyDistanceBand")
GOING_GROUPS=("HorseDry","HorseWet","HorseHeavy")
def names(groups):
    return tuple("v6"+group+suffix for group in groups for suffix in ("LogStarts","Top3Rate","MarketResidual"))
COURSE=names(COURSE_GROUPS)
JOCKEY=names(JOCKEY_GROUPS)
GOING=names(GOING_GROUPS)
NEW=COURSE+JOCKEY+GOING
COLS=("日付","レースID","馬番","馬キー","芝ダ","距離","頭数","障害","着順",
      "単勝オッズ","騎手","出典","タイム秒")


def jockey_key(value, source):
    if pd.isna(value):
        return None
    name=re.sub(r"\s+","",unicodedata.normalize("NFKC",str(value)))
    if not name or name in {"nan","不明","None","-"}:
        return None
    # Old feed abbreviations are not proven to identify the same individual
    # as the newer JRA names; keep source eras separate.
    return ("old" if source=="旧データ" else "current",name)


def group_keys(horse,jockey,venue,surface,distance):
    band=int(distance)//200
    course=(venue,surface,int(distance))
    return {
        "HorseVenue":(horse,venue,surface),
        "HorseCourse":(horse,*course),
        "HorseDistanceVenueBand":(horse,venue,surface,band),
        "Pair":(horse,jockey) if jockey is not None else None,
        "PairSurface":(horse,jockey,surface) if jockey is not None else None,
        "JockeyVenue":(jockey,venue,surface) if jockey is not None else None,
        "JockeyCourse":(jockey,*course) if jockey is not None else None,
        "JockeyDistanceBand":(jockey,surface,band) if jockey is not None else None,
        "HorseDry":(horse,surface),
        "HorseWet":(horse,surface),
        "HorseHeavy":(horse,surface),
    }


def stats(state,group,key):
    if key is None:
        n,hit,res=0,0.,0.
    else:
        n,hit,res=state[group].get(key,(0,0.,0.))
    shrink=30. if group.startswith("Jockey") else 5.
    return (float(np.log1p(n)),float((hit+.21*shrink)/(n+shrink)),float(res/(n+shrink)))


def build(history,lap_records):
    d=history.loc[:,list(COLS)].copy()
    d["日付"]=pd.to_datetime(d["日付"],errors="coerce").dt.normalize()
    ensure(d["日付"].notna().all() and d["馬キー"].notna().all(),"invalid identity")
    d=d.sort_values(["日付","レースID","馬番"]).reset_index(drop=True)
    ensure(not d.duplicated(["レースID","馬番"]).any(),"duplicate runner")
    # Historical full-field market probabilities. Current result is never used
    # in a feature: the values below are used only for post-date state updates.
    odds=pd.to_numeric(d["単勝オッズ"],errors="coerce")
    market=pd.DataFrame({"レースID":d["レースID"],"市場確率":1./odds.where(odds>=1)})
    _,p3=plackett_luce_top2_top3(market)
    d["_market3"]=p3
    state={g:{} for g in COURSE_GROUPS+JOCKEY_GROUPS+GOING_GROUPS}
    audit=defaultdict(lambda:defaultdict(int))
    out=np.zeros((len(d),len(NEW)),float)
    for date,day in d.groupby("日付",sort=False):
        pending=[]; duplicate=set(day.loc[day.duplicated("馬キー",keep=False),"馬キー"])
        for rid,g in day.groupby("レースID",sort=False):
            venue=str(rid)[4:6];surface=str(g["芝ダ"].iloc[0]);distance=g["距離"].iloc[0]
            ensure(re.fullmatch(r"\d{12}",str(rid)) and venue in {f"{i:02d}" for i in range(1,11)}
                   and pd.notna(distance) and float(distance)>0,"bad race metadata")
            keys=g["馬キー"].tolist(); n=len(keys)
            groups=[]
            for horse,jockey,source in g[["馬キー","騎手","出典"]].itertuples(index=False,name=None):
                groups.append(group_keys(horse,jockey_key(jockey,source),venue,surface,distance))
            for idx,group in zip(g.index,groups):
                out[idx]=[v for name,key in group.items() for v in stats(state,name,key)]
            if g["障害"].fillna(True).astype(bool).any():
                continue
            year=str(date.year);audit[year]["flatRaces"]+=1
            rank=pd.to_numeric(g["着順"],errors="coerce").to_numpy(float)
            m=g["_market3"].to_numpy(float)
            valid=(n>=3 and len(set(keys))==n and not set(keys)&duplicate
                   and g["頭数"].eq(n).all() and g["芝ダ"].eq(surface).all() and g["距離"].eq(distance).all()
                   and np.isfinite(rank).all() and np.all(rank==np.floor(rank))
                   and np.all((rank>=1)&(rank<=n)) and all(np.sum(rank==i)==1 for i in (1,2,3))
                   and np.isfinite(m).all())
            if not valid:
                continue
            audit[year]["usableRaces"]+=1
            r=lap_records.get(str(rid))
            winner=pd.to_numeric(g.loc[g["着順"].eq(1),"タイム秒"],errors="coerce").to_numpy(float)
            going=None
            if (r and check_record(r) and r["raceDate"]==date.date().isoformat()
                and r["surface"]==surface and r["distanceM"]==distance
                and len(winner)==1 and np.isfinite(winner[0]) and abs(winner[0]-r["winnerTimeSeconds"])<=.051):
                going=r["going"];audit[year]["goingMatchedRaces"]+=1
            for i,group in enumerate(groups):
                hit=float(rank[i]<=3);res=hit-m[i]
                for name in COURSE_GROUPS+JOCKEY_GROUPS:
                    key=group[name]
                    if key is not None:pending.append((name,key,hit,res))
                if going=="良":
                    pending.append(("HorseDry",group["HorseDry"],hit,res))
                elif going in ("稍重","重","不良"):
                    pending.append(("HorseWet",group["HorseWet"],hit,res))
                    if going in ("重","不良"):
                        pending.append(("HorseHeavy",group["HorseHeavy"],hit,res))
        for name,key,hit,res in pending:
            n,h,r=state[name].get(key,(0,0.,0.));state[name][key]=(n+1,h+hit,r+res)
    ensure(np.isfinite(out).all(),"nonfinite V6 features")
    result=d[["レースID","馬番"]].copy();result[list(NEW)]=out
    return result,{k:dict(v) for k,v in audit.items()}
