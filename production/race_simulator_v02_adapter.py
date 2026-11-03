#!/usr/bin/env python3
"""KeibaAI -> RaceSimulator v0.2 adapter.

Motion inputs are deliberately independent of aiProbability, marketProbability,
odds and popularity. Strength is derived only from saved pre-race historical
performance profiles. Pace is inferred from saved pre-race style/initial-position
history when a validated explicit pace is unavailable.
"""
from __future__ import annotations
import hashlib, json, math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from race_simulator_v02 import HorseInput, simulate_race


def _f(v: Any, default=None):
    try: return default if v is None or v == "" else float(v)
    except (TypeError, ValueError): return default
def _i(v: Any, default=-1):
    try: return int(v)
    except (TypeError, ValueError): return default
def _select(data: Any, race_id: str) -> Mapping[str, Any]:
    rows=data.get("races") if isinstance(data,dict) else data
    if not isinstance(rows,list): raise ValueError("expected race list")
    for r in rows:
        if isinstance(r,dict) and str(r.get("raceID",""))==race_id: return r
    raise KeyError(f"raceID not found: {race_id}")
def _distance(r: Mapping[str,Any]) -> float:
    for v in (r.get("distanceMeters"),r.get("course"),r.get("distance"),r.get("track")):
        if isinstance(v,(int,float)) and 600<=float(v)<=5000: return float(v)
        for c in "".join(ch if (ch.isdigit() or ch==".") else " " for ch in str(v or "")).split():
            try: x=float(c)
            except ValueError: continue
            if 600<=x<=5000: return x
    raise ValueError("cannot parse distance")
def _surface(r: Mapping[str,Any]) -> Optional[str]:
    t=str(r.get("course",r.get("track","")))
    return "芝" if "芝" in t else "ダ" if "ダ" in t else None
def _distance_band(d: float) -> str:
    lo=int(d//200)*200; return f"{lo}-{lo+199}m"
def _prob(v: Any):
    x=_f(v)
    if x is None or x<=0: return None
    return min(1.,x if x<=1 else x/100.) if x<=100 else None
def _style(v: Any) -> str:
    t=str(v or "").strip(); return t if t in {"逃げ","先行","差し","追込"} else "不明"

def _research_horses(rr: Optional[Mapping[str,Any]], field: int) -> Dict[int,Dict[str,Any]]:
    out={}
    if not rr: return out
    for row in rr.get("horses",[]) if isinstance(rr.get("horses",[]),list) else []:
        if not isinstance(row,dict): continue
        n=_i(row.get("number",row.get("horseNumber")))
        if n<0: continue
        tend=row.get("styleTendency") if isinstance(row.get("styleTendency"),dict) else {}
        pos=row.get("initialPositionHistory") if isinstance(row.get("initialPositionHistory"),dict) else {}
        rel=_f(pos.get("meanRelativePosition"))
        out[n]={"style":_style(tend.get("dominantStyle")),"scores":tend.get("distributionPct",{}) if isinstance(tend.get("distributionPct"),dict) else {},"sampleCount":_i(tend.get("sampleRaces"),0),"avgCorner1":None if rel is None else 1.+max(0.,min(1.,rel))*max(field-1,0)}
    return out

def _profile_map(profile_data: Any) -> Dict[str,Mapping[str,Any]]:
    if not isinstance(profile_data,Mapping): return {}
    profiles=profile_data.get("profiles",profile_data)
    if not isinstance(profiles,Mapping): return {}
    return {str(p.get("horseKey")):p for p in profiles.values() if isinstance(p,Mapping) and p.get("horseKey")}
def _stat_score(items: Any, match) -> Optional[Tuple[float,int]]:
    if not isinstance(items,list): return None
    for x in items:
        if isinstance(x,Mapping) and match(x):
            score=_f(x.get("smoothedPerformanceScore")); starts=_i(x.get("starts"),0)
            if score is not None and starts>0: return score,starts
    return None
def _history_strengths(race: Mapping[str,Any], profiles: Any, distance: float) -> Tuple[Dict[int,float],Dict[int,Dict[str,Any]]]:
    pm=_profile_map(profiles); venue=str(race.get("venue",race.get("racecourse",""))); surface=_surface(race); band=_distance_band(distance)
    raw={}; meta={}
    for h in race.get("horses",[]):
        n=_i(h.get("number",h.get("horseNumber"))); key=str(h.get("horseKey","") or ""); p=pm.get(key)
        components=[]
        if p:
            for label,items,match,weight in (
                ("venueSurface",p.get("venueSurfaceStats"),lambda x:x.get("venue")==venue and x.get("surface")==surface,0.42),
                ("distance",p.get("distanceStats"),lambda x:x.get("distanceBand")==band,0.33),
                ("surface",p.get("surfaceStats"),lambda x:x.get("surface")==surface,0.15),
                ("venue",p.get("venueStats"),lambda x:x.get("venue")==venue,0.10),
            ):
                found=_stat_score(items,match)
                if found:
                    score,starts=found; reliability=min(1.,starts/5.); components.append((label,score,weight*reliability,starts))
        if components:
            w=sum(x[2] for x in components); score=sum(x[1]*x[2] for x in components)/w
            confidence=min(1.,w/.65); shrunk=50.+(score-50.)*confidence
            strength=math.exp((shrunk-50.)/100.*0.55)
            raw[n]=strength; meta[n]={"score":round(shrunk,2),"components":[{"name":a,"score":round(b,1),"starts":d} for a,b,c,d in components]}
        else:
            raw[n]=1.; meta[n]={"score":50.0,"components":[]}
    vals=list(raw.values()); mean=sum(vals)/max(len(vals),1)
    strengths={n:max(.82,min(1.18,v/mean)) for n,v in raw.items()}
    return strengths,meta

def _pace(rr: Optional[Mapping[str,Any]], info: Mapping[int,Mapping[str,Any]], field: int) -> Tuple[str,str,Dict[str,Any]]:
    formation=rr.get("formation",{}) if isinstance(rr,Mapping) else {}
    if not isinstance(formation,Mapping): formation={}
    explicit=formation.get("paceRaw",formation.get("pace"))
    if explicit:
        t=str(explicit).upper(); p="FAST" if t in {"H","HIGH","FAST"} else "SLOW" if t in {"S","SLOW"} else "MID"
        return p,"research-explicit",{"paceConfidence":"high","pacePressureScore":None,"formationStatus":formation.get("status"),"formationCoveragePct":_f(formation.get("coveragePct")),"paceForecastStatus":"explicit"}
    forecast=formation.get("paceForecast",{})
    if isinstance(forecast,Mapping) and str(forecast.get("status","")).lower() in {"available","validated","ok"} and forecast.get("paceRaw",forecast.get("pace")):
        t=str(forecast.get("paceRaw",forecast.get("pace"))).upper(); p="FAST" if t in {"H","HIGH","FAST"} else "SLOW" if t in {"S","SLOW"} else "MID"
        return p,"research-pace-forecast",{"paceConfidence":"high","pacePressureScore":None,"formationStatus":formation.get("status"),"formationCoveragePct":_f(formation.get("coveragePct")),"paceForecastStatus":forecast.get("status")}
    known=[x for x in info.values() if x.get("style")!="不明" and x.get("sampleCount",0)>0]
    coverage=len(known)/max(field,1)
    pressure=0.
    for x in known:
        s=x.get("style"); samples=min(1.,x.get("sampleCount",0)/3.); pos=x.get("avgCorner1")
        pos_factor=1.-max(0.,min(1.,((pos or (field+1)/2)-1)/max(field-1,1)))
        pressure += {"逃げ":1.0,"先行":.58,"差し":.16,"追込":.05}.get(s,0)*(.65+.35*pos_factor)*samples
    if coverage<.35: return "MID","neutral-mid-fallback",{"paceConfidence":"low","pacePressureScore":round(pressure,3),"formationStatus":formation.get("status"),"formationCoveragePct":round(coverage*100,1),"paceForecastStatus":"insufficient-style-coverage"}
    normalized=pressure/max(math.sqrt(field),1.)
    pace="FAST" if normalized>=.72 else "SLOW" if normalized<=.30 else "MID"
    conf="medium" if coverage>=.65 else "low"
    return pace,"style-pressure-v0.2",{"paceConfidence":conf,"pacePressureScore":round(normalized,3),"formationStatus":formation.get("status"),"formationCoveragePct":round(coverage*100,1),"paceForecastStatus":"derived-pre-race-history"}

def _comparison(horses: Sequence[HorseInput], rows: Sequence[Mapping[str,Any]]) -> Dict[str,Any]:
    probs={h.number:h.prediction_ai_probability for h in horses if h.prediction_ai_probability is not None}; total=sum(probs.values())
    if not probs or total<=0: return {"available":False,"reason":"aiProbability missing or zero"}
    norm={n:p/total for n,p in probs.items()}; out=[]
    for r in rows:
        n=int(r["horseNumber"])
        if n not in norm: continue
        d=float(r["simulationWinProbability"])-norm[n]
        out.append({"horseNumber":n,"horseName":r["horseName"],"predictionAIWinProbabilityNormalized":round(norm[n],6),"simulationWinProbability":r["simulationWinProbability"],"delta":round(d,6),"label":"AGREE" if abs(d)<=.025 else "SIMULATOR_HIGHER" if d>0 else "PREDICTOR_HIGHER"})
    out.sort(key=lambda x:abs(x["delta"]),reverse=True)
    return {"available":True,"note":"Post-hoc only; prediction AI never enters race motion.","horses":out}

def simulate_current_keibaai_v02(race_data: Any, research_data: Any, profile_data: Any, race_id: str, runs: int=400, seed: int=20261103, dt: float=.5) -> Dict[str,Any]:
    race=_select(race_data,race_id); rr=_select(research_data,race_id) if research_data is not None else None
    rows=race.get("horses",[])
    if not isinstance(rows,list) or len(rows)<2: raise ValueError("race has fewer than two horses")
    distance=_distance(race); info=_research_horses(rr,len(rows)); strengths,strength_meta=_history_strengths(race,profile_data,distance)
    horses=[]
    for row in rows:
        n=_i(row.get("number",row.get("horseNumber"))); x=info.get(n,{})
        horses.append(HorseInput(n,str(row.get("name",row.get("horseName",f"#{n}"))),str(x.get("style","不明")),{str(k):float(v) for k,v in x.get("scores",{}).items() if _f(v) is not None},_i(x.get("sampleCount"),0),_f(x.get("avgCorner1")),None,strengths.get(n,1.),_prob(row.get("aiProbability"))))
    pace,pace_source,quality=_pace(rr,info,len(rows)); sim=simulate_race(horses,distance,pace,runs,seed,dt,dt)
    known=sum(h.style!="不明" for h in horses); strength_known=sum(bool(strength_meta.get(h.number,{}).get("components")) for h in horses)
    return {"schemaVersion":"race-simulator-v0.2-current-keibaai","generatedAt":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"raceID":race_id,"raceName":race.get("raceName"),"racecourse":race.get("venue",race.get("racecourse")),"track":race.get("course",race.get("track")),"distanceMeters":distance,"pace":pace,"paceSource":pace_source,"inputQuality":{**quality,"styleKnownHorses":known,"styleTotalHorses":len(horses),"styleCoveragePct":round(100*known/len(horses),1),"strengthKnownHorses":strength_known,"strengthCoveragePct":round(100*strength_known/len(horses),1)},"independence":{"usesPredictionAIForMotion":False,"usesMarketProbabilityForMotion":False,"usesOddsForMotion":False,"usesPopularityForMotion":False,"strengthSource":"historical-performance-v0.2" if strength_known else "neutral-equal-strength"},"strengthDiagnostics":{str(n):m for n,m in strength_meta.items()},"settings":sim["settings"],"horses":sim["horses"],"replay":sim["replay"],"comparisonWithPredictionAI":_comparison(horses,sim["horses"])}

def load_profiles_for_race(race: Mapping[str,Any], profiles_dir: Path) -> Dict[str,Any]:
    merged={"profiles":{}}
    for h in race.get("horses",[]):
        key=str(h.get("horseKey","") or "")
        if not key: continue
        pid=hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]; path=profiles_dir/f"{pid[:2]}.json"
        if not path.exists(): continue
        data=json.loads(path.read_text(encoding="utf-8")); p=data.get("profiles",{}).get(pid)
        if isinstance(p,Mapping): merged["profiles"][pid]=p
    return merged
