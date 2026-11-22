"""Research-only first-formal-T10 market anchored forecasts.

Market-only (2024 calibrated) always runs when its frozen 2024 strategy exists.
Historical 2%-AI challenger runs only when the separate no-odds forecast is
ALREADY first-frozen for the same race. Never backfill on old results.
No modification to production predictions or tickets.
"""
from __future__ import annotations
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from market_shrink_shadow import JST, _write_first, fingerprint, _aware
from no_current_odds_shadow import shadow_path as no_odds_path, SHADOW_VERSION
from train_market_anchored_challenger import (
    ROOT, VERSION as STRATEGY_VERSION, combine,
)
from train_top2_top3_models import plackett_luce_top2_top3

STRATEGY_PATH = ROOT / "research/model_candidates/market_anchored_v1/frozen_strategy.json"
VERSION = "market-anchored-first-T10-v1-20261122"


def output_path(root, race):
    return (Path(root)/"reports"/str(race["raceDate"]).replace("-", "")/
            "market_anchored_v1"/(str(race["raceID"])+".json"))


def verify_strategy(path=STRATEGY_PATH):
    raw = Path(path).read_bytes()
    strategy = json.loads(raw)
    if (strategy.get("version") != STRATEGY_VERSION or
            strategy.get("parametersSelectedFrom") != "2024-only" or
            strategy.get("candidatePromotionApproved") is not False or
            strategy.get("formalPredictionAIChanged") is not False or
            strategy.get("autoBetEnabled") is not False or
            strategy.get("formalRecommendationsEnabled") is not False):
        raise ValueError("invalid research strategy identity/policy")
    if set(strategy.get("strategies") or {}) != {"marketCalibrated", "historyAugmented"}:
        raise ValueError("missing frozen market-anchored strategy")
    return strategy, hashlib.sha256(raw).hexdigest()


def triplet(probs, race_id):
    tmp = pd.DataFrame({"レースID":[race_id]*len(probs), "市場確率":probs})
    t2, t3 = plackett_luce_top2_top3(tmp)
    if not (np.isfinite(t2).all() and np.isfinite(t3).all()):
        raise ValueError("invalid market anchored topk probabilities")
    return {"win": probs, "top2": t2, "top3": t3}


def build(race, strategy, sha, independent=None, *, clock=lambda: datetime.now(JST)):
    if (race.get("predictionTiming") != "live_10min" or
            race.get("captureQuality") != "formal_10min"):
        raise ValueError("not a formal first-T10 prediction")
    day, rid = str(race["raceDate"]), str(race["raceID"])
    post = datetime.fromisoformat(day+"T"+str(race["startTime"])).replace(tzinfo=JST)
    captured = _aware(race["capturedAtJST"])
    now = clock().astimezone(JST)
    if (captured.date().isoformat() != day or
            not 8 <= (post-captured).total_seconds()/60 <= 12 or
            not captured <= now <= post - timedelta(minutes=8)):
        raise ValueError("first-T10 timestamp expired")
    if (strategy.get("version") != STRATEGY_VERSION or
            strategy.get("parametersSelectedFrom") != "2024-only" or
            strategy.get("candidatePromotionApproved") is not False):
        raise ValueError("untrusted preregistered parameters")
    horses = race.get("horses")
    if not isinstance(horses, list) or len(horses) < 3:
        raise ValueError("incomplete first-T10 entrants")
    numbers = [h["number"] for h in horses]
    if (any(type(n) is not int or n < 1 for n in numbers) or
            len(set(numbers)) != len(numbers)):
        raise ValueError("duplicate/incomplete entrant numbers")
    market = np.array([h.get("marketProbability") for h in horses],
                      dtype=np.float64)/100
    if not (np.isfinite(market).all() and (market>0).all() and (market<=1).all()):
        raise ValueError("invalid first-T10 market probabilities")
    history = market
    historical_status = "no_independently_frozen_no_odds_candidate"
    source_no_odds_hash = None
    if independent is not None:
        if (independent.get("version") != SHADOW_VERSION or
                independent.get("raceDate") != day or
                independent.get("raceID") != rid or
                independent.get("capturedAtJST") != race["capturedAtJST"] or
                independent.get("sourcePredictionSha256") != fingerprint(race) or
                independent.get("recordFingerprint") != fingerprint({
                    k:v for k,v in independent.items() if k!="recordFingerprint"
                }) or
                independent.get("researchOnly") is not True or
                independent.get("candidatePromotionApproved") is not False or
                independent.get("autoBetEnabled") is not False or
                independent.get("modelSha256") != strategy["independentModelSHA256"]):
            raise ValueError("frozen no-odds independent model identity conflict")
        entries = independent.get("candidateHorses")
        if (not isinstance(entries, list) or
                [e.get("number") for e in entries] != numbers):
            raise ValueError("independent horse ordering mismatch")
        history = np.asarray([e["winPct"] for e in entries], dtype=float)/100
        if not (np.isfinite(history).all() and (history > 0).all() and (history <= 1).all()):
            raise ValueError("invalid independent first-T10 win probabilities")
        historical_status = "valid_first_frozen_no_odds_snapshot"
        source_no_odds_hash = independent["recordFingerprint"]
    strategies = {}
    for name in ("marketCalibrated", "historyAugmented"):
        if name == "historyAugmented" and independent is None:
            continue
        p = strategy["strategies"][name]
        dist = combine(market, history, [rid]*len(numbers),
                       market_power=p["marketPower"],
                       historical_weight=p["historicalWeight"],
                       method=p["method"])
        probs = triplet(dist, rid)
        strategies[name] = [
            {
                "number": number,
                "winPct": round(float(probs["win"][i])*100,8),
                "top2Pct": round(float(probs["top2"][i])*100,8),
                "top3Pct": round(float(probs["top3"][i])*100,8),
            }
            for i,number in enumerate(numbers)
        ]
    result = {
        "schemaVersion":1, "version":VERSION,
        "status":"first_frozen_future_research_only",
        "researchOnly":True, "candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,
        "formalRecommendationsEnabled":False, "autoBetEnabled":False,
        "raceDate":day, "raceID":rid, "startTime":race["startTime"],
        "predictionTiming":"live_10min",
        "capturedAtJST":race["capturedAtJST"],
        "recordedAtJST":now.isoformat(),
        "sourcePredictionSha256":fingerprint(race),
        "strategyVersion":strategy["version"],
        "strategySHA256":sha,
        "independentHistoricalModelSHA256":strategy["independentModelSHA256"],
        "independentHistoricalStatus":historical_status,
        "sourceNoOddsShadowFingerprint":source_no_odds_hash,
        "candidateHorsesByStrategy":strategies,
    }
    result["recordFingerprint"]=fingerprint(result)
    return result


def record_best_effort(root, race, *, first_formal_capture,
                       clock=lambda: datetime.now(JST)):
    try:
        path=output_path(root,race)
        if path.exists():
            saved=json.loads(path.read_text(encoding="utf-8"))
            if (saved.get("sourcePredictionSha256")!=fingerprint(race) or
                    saved.get("recordFingerprint")!=fingerprint({
                        k:v for k,v in saved.items() if k!="recordFingerprint"
                    })):
                raise ValueError("immutable anchored shadow conflict")
            return {"status":"already_frozen","path":str(path)}
        if not first_formal_capture:
            return {"status":"missing_first_capture_no_backfill","path":None}
        if not STRATEGY_PATH.is_file():
            return {"status":"frozen_strategy_not_published","path":None}
        strategy,sha=verify_strategy()
        other_path=no_odds_path(root,race)
        independent=json.loads(other_path.read_text(encoding="utf-8")) if other_path.is_file() else None
        v=build(race,strategy,sha,independent,clock=clock)
        _write_first(path,v)
        return {"status":"first_frozen","path":str(path),
                "historicalStatus":v["independentHistoricalStatus"]}
    except Exception as exc:
        return {"status":"error_no_formal_impact","path":None,
                "errorType":type(exc).__name__, "reason":str(exc)[:200]}
