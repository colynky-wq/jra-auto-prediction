#!/usr/bin/env python3
"""Build and audit RaceSimulationData v0.2."""
from __future__ import annotations
import argparse, hashlib, json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from race_simulator_v02_adapter import load_profiles_for_race, simulate_current_keibaai_v02

SCHEMA_VERSION="race-simulator-v0.2-batch"

def _load(p: Optional[Path]):
    if p is None: return None
    with p.open("r",encoding="utf-8") as f:return json.load(f)
def _rows(d: Any):
    if isinstance(d,list): return [x for x in d if isinstance(x,Mapping)]
    if isinstance(d,Mapping) and isinstance(d.get("races"),list): return [x for x in d["races"] if isinstance(x,Mapping)]
    raise ValueError("expected race list")
def _sha(p: Optional[Path]):
    if p is None or not p.exists(): return None
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()
def _seed(base:int,rid:str): return base+(int(hashlib.sha256(rid.encode()).hexdigest()[:8],16)%1_000_000)
def _distance(r):
    for v in (r.get("distanceMeters"),r.get("course"),r.get("distance"),r.get("track")):
        for c in "".join(ch if(ch.isdigit()or ch==".")else" " for ch in str(v or "")).split():
            try:x=float(c)
            except:continue
            if 600<=x<=5000:return x
    return None
def _repair(data:Any,overrides:Any):
    om=(overrides or {}).get("races",overrides or {}) if isinstance(overrides,Mapping) else {}; out=[]; applied=[]
    for original in _rows(data):
        r=dict(original); rid=str(r.get("raceID","")); o=om.get(rid) if isinstance(om,Mapping) else None
        if _distance(r) is None and isinstance(o,Mapping):
            if o.get("course"):r["course"]=str(o["course"])
            if o.get("distanceMeters"):r["distanceMeters"]=o["distanceMeters"]
            if _distance(r) is not None: applied.append({"raceID":rid,"course":r.get("course"),"distanceMeters":_distance(r),"source":o.get("source"),"verifiedDate":o.get("verifiedDate")})
        out.append(r)
    return out,applied
def audit_race(r: Mapping[str,Any]) -> Dict[str,Any]:
    issues=[]; horses=r.get("horses",[]); replay=r.get("replay",{}); frames=replay.get("frames",[]) if isinstance(replay,Mapping) else []
    strengths=[float(h.get("independentStrength",1)) for h in horses]
    if r.get("independence",{}).get("usesPredictionAIForMotion") is not False: issues.append("prediction_ai_enters_motion")
    if r.get("independence",{}).get("usesMarketProbabilityForMotion") is not False: issues.append("market_enters_motion")
    if r.get("independence",{}).get("usesOddsForMotion") is not False: issues.append("odds_enter_motion")
    if len(horses)<2: issues.append("too_few_horses")
    if not frames: issues.append("missing_replay_frames")
    if frames:
        last_t=-1.; last_progress={}
        for f in frames:
            t=float(f.get("t",-1))
            if t<last_t: issues.append("non_monotonic_frame_time"); break
            last_t=t
            for h in f.get("horses",[]):
                n=int(h["horseNumber"]); p=float(h["progress"]); speed=float(h["speedMps"]); lane=float(h["lane"])
                if not 0<=p<=1: issues.append("progress_out_of_range")
                if n in last_progress and p+1e-6<last_progress[n]: issues.append("horse_moves_backwards")
                last_progress[n]=p
                if not 5.0<=speed<=22.0: issues.append("implausible_speed")
                if not 0<=lane<=5: issues.append("lane_out_of_range")
    avg_times=[float(h.get("averageFinishTimeSeconds",0)) for h in horses]
    nominal=float(r.get("distanceMeters",0))/16.6 if r.get("distanceMeters") else 0
    if nominal and any(t<nominal*.72 or t>nominal*1.55 for t in avg_times): issues.append("implausible_finish_time")
    if r.get("independence",{}).get("strengthSource")=="historical-performance-v0.2" and len(horses)>=4 and max(strengths)-min(strengths)<.002: issues.append("strengths_effectively_equal")
    return {"status":"pass" if not issues else "fail","issues":sorted(set(issues)),"frameCount":len(frames),"strengthSpread":round(max(strengths)-min(strengths),6) if strengths else 0}
def build_batch(race_data:Any,research_data:Any,profiles_dir:Path,metadata_overrides:Any=None,*,runs=250,seed=20261103,dt=.5,all_dates=False):
    races,repairs=_repair(race_data,metadata_overrides); dates=sorted({str(r.get("raceDate")) for r in races if r.get("raceDate")}); target=dates[-1] if dates else None
    selected=races if all_dates or not target else [r for r in races if str(r.get("raceDate"))==target]
    research_by={str(r.get("raceID")):r for r in _rows(research_data) if r.get("raceID")} if research_data is not None else {}
    output=[]; errors=[]; skipped=[]
    for race in selected:
        rid=str(race.get("raceID","")); course=str(race.get("course",race.get("track","")))
        if not rid: errors.append({"raceID":"","reason":"missing raceID"}); continue
        if "障" in course or "障害" in str(race.get("raceName","")): skipped.append({"raceID":rid,"reason":"obstacle race outside v0.2 scope"}); continue
        try:
            profiles=load_profiles_for_race(race,profiles_dir); rr=research_by.get(rid); research=[rr] if rr else None
            result=simulate_current_keibaai_v02(races,research,profiles,rid,runs,_seed(seed,rid),dt)
            result.update({"raceDate":race.get("raceDate"),"raceNumber":race.get("raceNumber"),"startTime":race.get("startTime"),"venue":race.get("venue")})
            result["audit"]=audit_race(result)
            if result["audit"]["status"]!="pass": errors.append({"raceID":rid,"reason":"audit: "+",".join(result["audit"]["issues"])}); continue
            output.append(result)
        except Exception as e: errors.append({"raceID":rid,"reason":f"{type(e).__name__}: {e}"})
    output.sort(key=lambda r:(str(r.get("raceDate")or""),str(r.get("venue")or""),int(r.get("raceNumber")or 0)))
    return {"schemaVersion":SCHEMA_VERSION,"generatedAt":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"status":"ok" if output and not errors else "partial" if output else "failed","targetRaceDate":target,"settings":{"runsPerRace":runs,"baseSeed":seed,"dtSeconds":dt,"allDates":all_dates},"summary":{"sourceRaces":len(races),"inputRaces":len(selected),"simulatedRaces":len(output),"skippedRaces":len(skipped),"errorRaces":len(errors),"metadataRepairs":len(repairs),"auditedRaces":len(output)},"metadataRepairs":repairs,"races":output,"skipped":skipped,"errors":errors}
def _write(path:Path,payload:Any):
    path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=path.name+".",suffix=".tmp",dir=str(path.parent))
    with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2); f.write("\n")
    os.replace(tmp,path)
def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--race-data",type=Path,default=Path("RaceData.json"));p.add_argument("--research-data",type=Path,default=Path("AppResearchData.json"));p.add_argument("--profiles-dir",type=Path,default=Path("HorseProfiles"));p.add_argument("--metadata-overrides",type=Path,default=Path("production/race_metadata_overrides.json"));p.add_argument("--runs",type=int,default=250);p.add_argument("--seed",type=int,default=20261103);p.add_argument("--dt",type=float,default=.5);p.add_argument("--all-dates",action="store_true");p.add_argument("--output",type=Path,default=Path("RaceSimulationData.json"));a=p.parse_args(argv)
    payload=build_batch(_load(a.race_data),_load(a.research_data),a.profiles_dir,_load(a.metadata_overrides) if a.metadata_overrides.exists() else None,runs=a.runs,seed=a.seed,dt=a.dt,all_dates=a.all_dates); payload["sourceSHA256"]={"RaceData.json":_sha(a.race_data),"AppResearchData.json":_sha(a.research_data),"metadataOverrides":_sha(a.metadata_overrides)};_write(a.output,payload);print(json.dumps({"status":payload["status"],**payload["summary"]},ensure_ascii=False));return 0 if payload["status"]=="ok" else 2
if __name__=="__main__": raise SystemExit(main())
