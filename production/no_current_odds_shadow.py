"""First-frozen formal-T10 challenger forecast: NEVER backfill a past race.

All outputs are research-only. A missing candidate, late capture, mismatch or
model failure must not disturb original live forecasts, tickets or recording.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from market_shrink_shadow import JST, _write_first, fingerprint
from train_no_current_odds_challenger import (
    CANDIDATE, FEATURES, MODEL_PATH, REPORT_PATH, VERSION, probability_triplet
)

SHADOW_VERSION = "no-current-odds-first-T10-shadow-v1-20261122"


def shadow_path(root, race):
    return (Path(root) / "reports" / str(race["raceDate"]).replace("-", "") /
            "no_current_odds_v1" / (str(race["raceID"]) + ".json"))


def verify_model():
    if not MODEL_PATH.is_file() or not REPORT_PATH.is_file():
        raise FileNotFoundError("research-only trained model not published")
    meta = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    if (meta.get("version") != VERSION or
            meta.get("modelSha256") != hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() or
            meta.get("features") != list(FEATURES) or
            meta.get("featureCount") != len(FEATURES) or
            meta.get("candidatePromotionApproved") is not False or
            meta.get("formalPredictionAIChanged") is not False or
            meta.get("autoBetEnabled") is not False):
        raise ValueError("invalid research candidate identity/policy")
    model = lgb.Booster(model_file=str(MODEL_PATH))
    if list(model.feature_name()) != list(FEATURES):
        raise ValueError("trained model feature order differs from T10 source")
    return meta, model


def build(race, feature_record, meta, model, *, clock=lambda: datetime.now(JST)):
    if (race.get("predictionTiming") != "live_10min" or
            race.get("captureQuality") != "formal_10min"):
        raise ValueError("formal T10 evidence required")
    post = datetime.fromisoformat(str(race["raceDate"]) + "T" +
                                 str(race["startTime"])).replace(tzinfo=JST)
    captured = datetime.fromisoformat(str(race["capturedAtJST"]))
    recorded = clock().astimezone(JST)
    if (captured.utcoffset() != timedelta(hours=9) or
            captured.date().isoformat() != race["raceDate"] or
            not 8 <= (post - captured).total_seconds()/60 <= 12 or
            not captured <= recorded <= post - timedelta(minutes=8)):
        raise ValueError("prospective first T10 window passed")
    if (meta.get("finalRefitThrough") != "2025-12-31" or
            meta.get("version") != VERSION or
            meta.get("featureCount") != 36 or
            meta.get("features") != list(FEATURES) or
            meta.get("candidatePromotionApproved") is not False):
        raise ValueError("research-only candidate contract mismatch")
    if not isinstance(feature_record, dict):
        raise ValueError("no first-capture feature matrix")
    if feature_record.get("featureNames") != list(FEATURES):
        raise ValueError("feature names differ from approved candidate")
    horses = race.get("horses")
    if not isinstance(horses, list) or len(horses) < 3:
        raise ValueError("incomplete formal entrant list")
    numbers = [x.get("number") for x in horses]
    if (any(type(n) is not int or n < 1 for n in numbers) or
            len(set(numbers)) != len(numbers) or
            feature_record.get("horseNumbers") != numbers):
        raise ValueError("feature-to-entrant identity mismatch")
    values = feature_record.get("rows")
    if (not isinstance(values, list) or len(values) != len(numbers) or
            any(not isinstance(x, list) or len(x) != len(FEATURES) for x in values)):
        raise ValueError("incomplete model features")
    X = pd.DataFrame(values, columns=list(FEATURES), dtype=np.float32)
    if not np.isfinite(X.to_numpy()).all():
        raise ValueError("invalid T10 features")
    X["レースID"] = str(race["raceID"])
    probs = probability_triplet(X, model)
    prediction = []
    for i, number in enumerate(numbers):
        prediction.append({
            "number": number,
            "winPct": round(float(probs["win"][i])*100, 8),
            "top2Pct": round(float(probs["top2"][i])*100, 8),
            "top3Pct": round(float(probs["top3"][i])*100, 8),
        })
    value = {
        "schemaVersion": 1,
        "version": SHADOW_VERSION,
        "researchOnly": True,
        "formalPredictionAIChanged": False,
        "formalRecommendationsEnabled": False,
        "autoBetEnabled": False,
        "candidatePromotionApproved": False,
        "status": "prospectively_frozen_not_approved",
        "raceDate": race["raceDate"],
        "raceID": str(race["raceID"]),
        "startTime": str(race["startTime"]),
        "predictionTiming": "live_10min",
        "capturedAtJST": str(race["capturedAtJST"]),
        "recordedAtJST": recorded.isoformat(),
        "modelVersion": meta["version"],
        "modelSha256": meta["modelSha256"],
        "sourcePredictionSha256": fingerprint(race),
        "featureInputSha256": hashlib.sha256(
            json.dumps(feature_record, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest(),
        "candidateHorses": prediction,
    }
    value["recordFingerprint"] = fingerprint(value)
    return value


def record_best_effort(root, race, features, *, first_formal_capture,
                       clock=lambda: datetime.now(JST)):
    """Only first formal captures generate a new shadow; failed work is isolated."""
    try:
        path = shadow_path(root, race)
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if (saved.get("sourcePredictionSha256") != fingerprint(race) or
                    saved.get("recordFingerprint") != fingerprint({
                        k: v for k, v in saved.items() if k != "recordFingerprint"
                    })):
                raise ValueError("immutable candidate shadow conflict")
            return {"status": "already_frozen", "path": str(path)}
        if not first_formal_capture:
            return {"status": "missing_first_capture_no_backfill", "path": None}
        if not MODEL_PATH.is_file() or not REPORT_PATH.is_file():
            return {"status": "candidate_model_not_published", "path": None}
        meta, model = verify_model()
        value = build(race, features, meta, model, clock=clock)
        return {"status": "prospectively_frozen",
                "path": str(_write_first(path, value))}
    except Exception as exc:
        return {"status": "error_no_formal_impact", "path": None,
                "errorType": type(exc).__name__, "reason": str(exc)[:200]}
