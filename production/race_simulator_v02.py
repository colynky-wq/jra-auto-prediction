#!/usr/bin/env python3
"""RaceSimulator v0.2.

Independent race-flow engine for KeibaAI.
- Prediction AI, market probabilities, odds and popularity never enter motion.
- Supports an independent pre-race strength score.
- Produces a deterministic representative frame replay (position/lane/speed/rank/gap).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from race_simulator_calibration import settings as calibration_settings

SCHEMA_VERSION = "race-simulator-v0.2"
CHECKPOINTS: Tuple[float, ...] = (0.25, 0.50, 0.75, 0.90)
STYLE_NAMES = ("逃げ", "先行", "差し", "追込")
STYLE_PARAMS = {
    "逃げ": {"early": 1.055, "middle": 1.018, "turn": 0.995, "late": 0.970, "kick": 0.975, "aggression": 1.12},
    "先行": {"early": 1.030, "middle": 1.012, "turn": 1.005, "late": 0.990, "kick": 0.995, "aggression": 1.06},
    "差し": {"early": 0.985, "middle": 0.995, "turn": 1.010, "late": 1.035, "kick": 1.045, "aggression": 0.98},
    "追込": {"early": 0.955, "middle": 0.985, "turn": 1.015, "late": 1.060, "kick": 1.075, "aggression": 0.93},
    "不明": {"early": 1.000, "middle": 1.000, "turn": 1.000, "late": 1.000, "kick": 1.000, "aggression": 1.00},
}
PACE_EARLY = {"SLOW": 0.986, "MID": 1.000, "FAST": 1.018}
PACE_FATIGUE = {"SLOW": 0.96, "MID": 1.00, "FAST": 1.08}


def _clamp(v: float, lo: float, hi: float) -> float: return max(lo, min(hi, v))
def _canonical_pace(v: Any) -> str:
    t = str(v or "MID").upper().strip()
    return "FAST" if t in {"H", "HIGH", "FAST", "ハイ", "ハイペース"} else "SLOW" if t in {"S", "SLOW", "スロー", "スローペース"} else "MID"
def _canonical_style(v: Any) -> str:
    t = str(v or "").strip()
    return t if t in STYLE_PARAMS else "不明"
def _nominal_speed(d: float) -> float:
    return 17.20 if d <= 1200 else 17.00 if d <= 1600 else 16.75 if d <= 2000 else 16.45 if d <= 2600 else 16.15
def _phase(p: float) -> str:
    return "early" if p < .20 else "middle" if p < .65 else "turn" if p < .82 else "late"
def _quantile(values: Sequence[float], q: float) -> float:
    a = sorted(values)
    if not a: return float("nan")
    if len(a) == 1: return float(a[0])
    x = (len(a)-1)*q; lo = math.floor(x); hi = math.ceil(x)
    return float(a[lo] if lo == hi else a[lo]*(hi-x)+a[hi]*(x-lo))

@dataclass(frozen=True)
class HorseInput:
    number: int
    name: str
    style: str = "不明"
    style_scores: Mapping[str, float] = field(default_factory=dict)
    sample_count: int = 0
    avg_corner1: Optional[float] = None
    avg_corner4: Optional[float] = None
    independent_strength: float = 1.0
    prediction_ai_probability: Optional[float] = None

@dataclass
class HorseState:
    horse: HorseInput
    position_m: float
    speed_mps: float
    stamina: float
    lane: float
    finished_time_s: Optional[float] = None
    blocked_steps: int = 0
    lane_changes: int = 0

@dataclass(frozen=True)
class RunResult:
    order: Tuple[int, ...]
    finish_times: Mapping[int, float]
    checkpoint_ranks: Mapping[float, Mapping[int, int]]
    blocked_steps: Mapping[int, int]
    lane_changes: Mapping[int, int]
    frames: Tuple[Mapping[str, Any], ...] = ()
    first600_seconds: Optional[float] = None
    last600_seconds: Mapping[int, float] = field(default_factory=dict)


def _style_params(h: HorseInput, scale: float = 1.0) -> Dict[str, float]:
    base = STYLE_PARAMS[_canonical_style(h.style)].copy()
    parsed = {k: max(0., float(v)) for k, v in h.style_scores.items() if k in STYLE_NAMES}
    total = sum(parsed.values())
    if total > 0:
        conf = max(parsed.values()) / total
        soften = _clamp((conf-.25)/.75, 0., 1.)
        for k in ("early", "middle", "turn", "late", "kick", "aggression"):
            base[k] = 1. + (base[k]-1.) * (.55 + .45*soften)
    return {key: 1. + (value-1.)*scale for key, value in base.items()}

def _lane_open(candidate: float, s: HorseState, states: Sequence[HorseState]) -> bool:
    return 0 <= candidate <= 5 and all(o.horse.number == s.horse.number or o.finished_time_s is not None or abs(o.position_m-s.position_m) >= 2.4 or abs(o.lane-candidate) >= .55 for o in states)
def _blocker(s: HorseState, states: Sequence[HorseState]) -> Optional[HorseState]:
    c = [(o.position_m-s.position_m, o) for o in states if o.horse.number != s.horse.number and o.finished_time_s is None and 0 < o.position_m-s.position_m < 3.4 and abs(o.lane-s.lane) < .58]
    return min(c, key=lambda x:x[0])[1] if c else None
def _rank_bias(h: HorseInput, states: Sequence[HorseState], p: float) -> float:
    target = h.avg_corner1 if p < .55 else h.avg_corner4
    if target is None or target <= 0: return 0.
    ordered = sorted(states, key=lambda s:s.position_m, reverse=True)
    rank = next((i+1 for i,s in enumerate(ordered) if s.horse.number == h.number), len(ordered))
    return _clamp((rank-target)*.0035, -.018, .018)

def _frame(time_s: float, states: Sequence[HorseState], distance_m: float) -> Mapping[str, Any]:
    ranked = sorted(states, key=lambda s:(s.position_m, -(s.finished_time_s or 1e9)), reverse=True)
    ranks = {s.horse.number:i+1 for i,s in enumerate(ranked)}
    leader = max(s.position_m for s in states)
    horses = []
    for s in sorted(states, key=lambda x:x.horse.number):
        pos = _clamp(s.position_m, 0., distance_m)
        horses.append({
            "horseNumber": s.horse.number,
            "progress": round(pos/distance_m, 6),
            "positionMeters": round(pos, 2),
            "lane": round(s.lane, 3),
            "speedMps": round(s.speed_mps, 3),
            "rank": ranks[s.horse.number],
            "gapMeters": round(max(0., leader-s.position_m), 2),
            "blocked": bool(_blocker(s, states)) if s.finished_time_s is None else False,
        })
    return {"t": round(time_s, 3), "horses": horses}

def _one_run(horses: Sequence[HorseInput], distance_m: float, pace: str, seed: int, dt: float, capture_frames: bool=False, calibration=None) -> RunResult:
    cfg = calibration_settings(calibration)
    rng = random.Random(seed); nominal = _nominal_speed(distance_m)*cfg['speedScale']; n = len(horses)
    expected = distance_m/nominal; max_seconds = expected*1.70+25.; states=[]
    for i,h in enumerate(sorted(horses,key=lambda x:x.number)):
        delay=_clamp(rng.gauss(.15,.10),0,.45); style=_style_params(h,cfg['styleScale'])
        start=nominal*(.83+.04*(style["early"]-1.)/.055)
        states.append(HorseState(h,-delay*start,max(8.,start*rng.uniform(.985,1.015)),1., 2.0 if n<=1 else 5.*i/(n-1)))
    checkpoints={}; frames=[]; time_s=0.; pace=_canonical_pace(pace)
    first600={}; last600_start={}
    if capture_frames: frames.append(_frame(0.,states,distance_m))
    while time_s < max_seconds and any(s.finished_time_s is None for s in states):
        active=[s for s in states if s.finished_time_s is None]
        leader=max(s.position_m for s in active); gp=_clamp(leader/distance_m,0,1)
        for cp in CHECKPOINTS:
            if cp not in checkpoints and gp>=cp:
                ordered=sorted(states,key=lambda s:s.position_m,reverse=True); checkpoints[cp]={s.horse.number:i+1 for i,s in enumerate(ordered)}
        updates={}
        for s in active:
            h=s.horse; p=_clamp(s.position_m/distance_m,0,1); phase=_phase(p); style=_style_params(h,cfg['styleScale'])
            strength=1.+.115*math.tanh((h.independent_strength-1.)*3.5*cfg['strengthScale'])
            target=nominal*strength*style[phase]
            if phase=="early": target*=PACE_EARLY[pace]
            elif phase=="late": target*=1.-.017*(PACE_FATIGUE[pace]-1.)/.08
            target*=1.+_rank_bias(h,states,p)
            if p>=.74: target*=1.+(style["kick"]-1.)*_clamp((s.stamina-.18)/.58,0,1)
            if s.stamina<.30: target*=.91+.30*s.stamina
            blocker=_blocker(s,states); blocked=False; lane=s.lane
            if blocker is not None:
                open_lane=next((c for c in (s.lane+.82,s.lane-.82) if _lane_open(c,s,states)),None)
                if open_lane is not None and rng.random()<.58+.18*(1.-p): lane=open_lane
                else: blocked=True; target=min(target,max(7.,blocker.speed_mps-.10))
            accel=_clamp((target-s.speed_mps)*.72+rng.gauss(0,.16 if phase!="late" else .20),-1.65,1.45)
            speed=_clamp(s.speed_mps+accel*dt,7.,20.7); intensity=speed/nominal
            drain=dt/max(expected,1.)*.70*(1.+1.35*max(0.,intensity-.965)+.22*max(0.,style["aggression"]-1.))
            if phase=="early": drain*=PACE_FATIGUE[pace]
            stamina=_clamp(s.stamina-drain,0,1); position=s.position_m+.5*(s.speed_mps+speed)*dt
            updates[h.number]=(speed,stamina,position,lane,blocked)
        for s in active:
            speed,stamina,position,lane,blocked=updates[s.horse.number]; prev=s.position_m
            if abs(lane-s.lane)>.2: s.lane_changes+=1
            if blocked: s.blocked_steps+=1
            s.speed_mps= speed; s.stamina=stamina; s.lane=lane; s.position_m=position
            for marker, table in ((600., first600), (distance_m-600., last600_start)):
                if marker > 0 and prev < marker <= position and s.horse.number not in table:
                    table[s.horse.number] = time_s + dt*(marker-prev)/max(position-prev,1e-9)
            if prev<distance_m<=position:
                frac=(distance_m-prev)/max(position-prev,1e-9); s.finished_time_s=time_s+dt*frac; s.position_m=distance_m
        time_s += dt
        if capture_frames: frames.append(_frame(time_s,states,distance_m))
    final=sorted(states,key=lambda s:(s.finished_time_s is not None,s.position_m),reverse=True)
    for cp in CHECKPOINTS:
        if cp not in checkpoints: checkpoints[cp]={s.horse.number:i+1 for i,s in enumerate(final)}
    penalty=max_seconds+60.; finish={s.horse.number:(s.finished_time_s if s.finished_time_s is not None else penalty-s.position_m/max(nominal,1.)) for s in states}
    order=tuple(k for k,_ in sorted(finish.items(),key=lambda x:x[1]))
    tail = {n: finish[n]-t for n,t in last600_start.items() if n in finish}
    return RunResult(order,finish,checkpoints,{s.horse.number:s.blocked_steps for s in states},{s.horse.number:s.lane_changes for s in states},tuple(frames), min(first600.values()) if first600 else None, tail)

def simulate_race(horses: Sequence[HorseInput], distance_m: float, pace: str="MID", runs: int=1000, seed: int=20261103, dt: float=.5, replay_dt: float=.5, *, calibration=None) -> Dict[str, Any]:
    cfg = calibration_settings(calibration)
    if len(horses)<2: raise ValueError("at least two horses are required")
    if distance_m<=0 or runs<1 or not .1<=dt<=2.: raise ValueError("invalid simulator settings")
    nums=[h.number for h in horses]
    if len(set(nums))!=len(nums): raise ValueError("horse numbers must be unique")
    win={n:0 for n in nums}; top2={n:0 for n in nums}; top3={n:0 for n in nums}; ranks={n:[] for n in nums}; times={n:[] for n in nums}
    cps={n:{cp:[] for cp in CHECKPOINTS} for n in nums}; blocked={n:[] for n in nums}; changes={n:[] for n in nums}
    early_times=[]; winner_times=[]; tail_times={n:[] for n in nums}
    for i in range(runs):
        r=_one_run(horses,distance_m,pace,seed+i*7919,dt,calibration=cfg)
        winner_times.append(min(r.finish_times.values()))
        if r.first600_seconds is not None: early_times.append(r.first600_seconds)
        for n,t in r.last600_seconds.items(): tail_times[n].append(t)
        for rank,n in enumerate(r.order,1):
            ranks[n].append(rank); times[n].append(float(r.finish_times[n])); win[n]+=rank==1; top2[n]+=rank<=2; top3[n]+=rank<=3
        for cp in CHECKPOINTS:
            for n in nums: cps[n][cp].append(int(r.checkpoint_ranks[cp][n]))
        for n in nums: blocked[n].append(r.blocked_steps[n]); changes[n].append(r.lane_changes[n])
    by={h.number:h for h in horses}; rows=[]
    for n in nums:
        h=by[n]; bands=[]
        for cp in CHECKPOINTS:
            v=cps[n][cp]; bands.append({"progress":cp,"p20Rank":round(_quantile(v,.2),2),"medianRank":round(_quantile(v,.5),2),"p80Rank":round(_quantile(v,.8),2)})
        v=ranks[n]; bands.append({"progress":1.0,"p20Rank":round(_quantile(v,.2),2),"medianRank":round(_quantile(v,.5),2),"p80Rank":round(_quantile(v,.8),2)})
        rows.append({"horseNumber":n,"horseName":h.name,"style":h.style,"independentStrength":round(h.independent_strength,6),"simulationWinProbability":round(win[n]/runs,6),"simulationTop2Probability":round(top2[n]/runs,6),"simulationTop3Probability":round(top3[n]/runs,6),"expectedFinish":round(sum(v)/len(v),3),"earlyLeadProbability":round(sum(1 for x in cps[n][.25] if x==1)/runs,6),"top3At75Probability":round(sum(1 for x in cps[n][.75] if x<=3)/runs,6),"averageFinishTimeSeconds":round(sum(times[n])/len(times[n]),3),"averageBlockedSeconds":round(sum(blocked[n])/len(blocked[n])*dt,3),"averageLaneChanges":round(sum(changes[n])/len(changes[n]),3),"rankBands":bands})
    rows.sort(key=lambda x:(-x["simulationWinProbability"],x["expectedFinish"],x["horseNumber"]))
    for row in rows:
        tail=tail_times[row['horseNumber']]
        row['averageLast600Seconds']=round(sum(tail)/len(tail),3) if tail else None
    representative=_one_run(horses,distance_m,pace,seed+104729,replay_dt,True,cfg)
    duration=max(representative.finish_times.values())
    replay={"kind":"deterministic-representative-run","seed":seed+104729,"dtSeconds":replay_dt,"durationSeconds":round(duration,3),"frames":list(representative.frames),"note":"Synthetic simulator trajectory; not GPS or race video."}
    return {"schemaVersion":SCHEMA_VERSION,"distanceMeters":distance_m,"pace":_canonical_pace(pace),"settings":{"runs":runs,"seed":seed,"dtSeconds":dt},"calibration":cfg,"flowPrediction":{"meanWinnerTimeSeconds":round(sum(winner_times)/len(winner_times),3),"meanFirst600Seconds":round(sum(early_times)/len(early_times),3) if early_times else None},"horses":rows,"replay":replay}
