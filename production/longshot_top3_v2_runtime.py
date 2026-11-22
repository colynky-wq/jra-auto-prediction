"""Run the frozen longshot probability model on first-capture T-10 inputs."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from formal_portfolio_capture import path_for as portfolio_path
from market_shrink_shadow import JST, _aware, _write_first, fingerprint
from research_longshot_top3_v2 import (
    FEATURE_SETS, FEATURES, OUTPUT, VERSION, WEIGHTS, add_extras, place_history,
    predict,
)
from topk_runtime import market_top2_top3
from train_longshot_top3 import POPRANK_MIN, WINODDS_MIN

SCHEMA_VERSION = 1
FOLDER = "longshot_top3_v2"
MODEL_FILE = "model_through2025.txt"
MODEL_FEATURES = FEATURE_SETS["place_and_market"]
CAPTURE_START = "2026-11-23"


def path_for(root, day: str, race_id: str) -> Path:
    return portfolio_path(root, day, race_id).parent.with_name(FOLDER) / f"{race_id}.json"


def model_spec(directory=OUTPUT) -> dict:
    report = json.loads((directory / "research.json").read_text(encoding="utf-8"))
    selected = report["selection2024"]["selected"]
    model_sha = report["modelHashes"][MODEL_FILE]
    if (report.get("version") != VERSION or report.get("researchOnly") is not True
            or report.get("candidatePromotionApproved") is not False
            or report.get("formalPredictionAIChanged") is not False
            or report.get("formalRecommendationsEnabled") is not False
            or report.get("autoBetEnabled") is not False
            or report.get("selectedFeatures") != list(MODEL_FEATURES)
            or selected.get("featureSet") != "place_and_market"
            or selected.get("weight") not in WEIGHTS
            or report.get("longshotDefinition") != {
                "popularityMinimum": POPRANK_MIN, "winOddsMinimum": WINODDS_MIN,
            }
            or report.get("causalHistory", {}).get("fixedBodyWeightImputationKg") != 480
            or hashlib.sha256((directory / MODEL_FILE).read_bytes()).hexdigest() != model_sha):
        raise ValueError("unverified frozen longshot v2 model")
    return {"version": VERSION, "sha256": model_sha, "weight": selected["weight"],
            "trainedThrough": "2025-12-31"}


def capture_time(race, timestamp: datetime) -> datetime:
    day = race["raceDate"]
    if (day < CAPTURE_START or race.get("predictionTiming") != "live_10min"
            or race.get("captureQuality") != "formal_10min"):
        raise ValueError("first formal T-10 prediction required")
    if timestamp.utcoffset() is None:
        raise ValueError("research clock must include a timezone")
    now = timestamp.astimezone(JST)
    captured = _aware(race["capturedAtJST"])
    post = datetime.fromisoformat(day + "T" + race["startTime"]).replace(tzinfo=JST)
    first, last = post - timedelta(minutes=12), post - timedelta(minutes=8)
    if captured.date().isoformat() != day or not first <= captured <= now <= last:
        raise ValueError("longshot prediction outside T-12 to T-8 window")
    return now


def entry_horses(race) -> list[dict]:
    horses = race["horses"]
    numbers = [horse["number"] for horse in horses]
    if (len(numbers) < 3 or any(type(number) is not int or number < 1 for number in numbers)
            or len(set(numbers)) != len(numbers)):
        raise ValueError("incomplete longshot field")
    for horse in horses:
        popularity, odds = horse["popularity"], horse["odds"]
        if (type(popularity) is not int or not 1 <= popularity <= len(horses)
                or type(odds) not in (int, float) or not math.isfinite(odds) or odds < 1):
            raise ValueError("invalid frozen popularity or win odds")
    return horses


def qualifying_horses(horses) -> list[dict]:
    return [horse for horse in horses if horse["popularity"] >= POPRANK_MIN
            and horse["odds"] >= WINODDS_MIN]


def prepare(race, feature_input, context, history) -> pd.DataFrame:
    """Reuse the training feature builder with the active field and prior results."""
    horses = entry_horses(race)
    numbers = [horse["number"] for horse in horses]
    if (feature_input["featureNames"] != list(FEATURES)
            or feature_input["horseNumbers"] != numbers
            or len(feature_input["rows"]) != len(numbers)):
        raise ValueError("longshot input horse order or feature schema differs")
    frame = pd.DataFrame(feature_input["rows"], columns=FEATURES, dtype=float)
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("missing longshot pre-race inputs")
    surface, distance, race_class = context["surface"], context["distance"], context["class"]
    if (surface not in {"芝", "ダ"} or type(distance) is not int or distance <= 0
            or race_class not in {"新馬", "未勝利", "1勝", "2勝", "3勝", "OP"}
            or race["course"] != f"{surface}{distance}m"):
        raise ValueError("invalid or mismatched frozen race conditions")
    keys = [horse["horseKey"] for horse in horses]
    if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("missing or duplicate horse identities")
    day = pd.Timestamp(race["raceDate"])
    attrs = ["レースID", "馬番", "馬キー", "日付", "芝ダ", "距離", "クラス", "着順", "頭数", "障害"]
    prior = history.loc[history["馬キー"].isin(keys), attrs].copy()
    dates = pd.to_datetime(prior["日付"], errors="raise")
    prior = prior.loc[dates < day]
    current = pd.DataFrame({
        "レースID": race["raceID"], "馬番": numbers, "馬キー": keys, "日付": day,
        "芝ダ": surface, "距離": distance, "クラス": race_class,
        "着順": np.nan, "頭数": len(numbers), "障害": False,
    })
    features = place_history(pd.concat([prior, current], ignore_index=True))
    current_features = features.loc[features["レースID"] == race["raceID"]]
    odds = np.asarray([horse["odds"] for horse in horses], dtype=float)
    _, market = market_top2_top3(1.0 / odds)
    # Training imputes missing current body weight at 480 kg. Only the
    # research copy gets this value; the existing matrix remains untouched.
    frame["馬体重f"] = [480.0 if horse.get("bodyWeight") is None else value
                     for horse, value in zip(horses, frame["馬体重f"], strict=True)]
    frame["レースID"] = race["raceID"]
    frame["馬番"] = numbers
    frame["市場3着内基準"] = market
    frame["人気"] = [horse["popularity"] for horse in horses]
    frame["単勝オッズ"] = odds
    frame["頭数"] = len(horses)
    return add_extras(frame, current_features)


def top_number(scores, field: str) -> int | None:
    if not scores:
        return None
    return max(scores, key=lambda horse: (horse[field], -horse["number"]))["number"]


def score_race(race, feature_input, context, history, directory=OUTPUT) -> dict:
    """Produce pre-race estimates from frozen inputs, with no outcome argument."""
    spec = model_spec(directory)
    horses = qualifying_horses(entry_horses(race))
    scores = []
    feature_hash = None
    model_input = None
    if horses:
        frame = prepare(race, feature_input, context, history)
        frame = frame.set_index("馬番").loc[[horse["number"] for horse in horses]]
        model = lgb.Booster(model_file=str(directory / MODEL_FILE))
        probabilities = predict(frame, model, MODEL_FEATURES, spec["weight"])
        model_input = {
            "horseNumbers": frame.index.tolist(), "features": list(MODEL_FEATURES),
            "rows": frame.loc[:, list(MODEL_FEATURES)].to_numpy().tolist(),
        }
        feature_hash = fingerprint(model_input)
        for horse, probability in zip(horses, probabilities, strict=True):
            scores.append({
                "number": horse["number"], "name": horse["name"],
                "popularity": horse["popularity"], "winOdds": horse["odds"],
                "top3ProbabilityPct": float(probability * 100),
                "marketTop3Probability": horse["marketTop3Probability"],
                "aiTop3Probability": horse["aiTop3Probability"],
            })
    scores.sort(key=lambda horse: (-horse["top3ProbabilityPct"], horse["number"]))
    return {"model": spec, "featureInputSha256": feature_hash, "modelInput": model_input,
            "selectedHorseNumber": top_number(scores, "top3ProbabilityPct"),
            "horses": scores}


def validate_record(record, day, race_id, spec):
    if (record.get("schemaVersion") != SCHEMA_VERSION
            or record.get("raceDate") != day or record.get("raceID") != race_id
            or record.get("model") != spec or record.get("researchOnly") is not True
            or record.get("recordFingerprint") != fingerprint({
                key: value for key, value in record.items() if key != "recordFingerprint"
            })):
        raise ValueError("longshot first capture identity or fingerprint changed")
    capture_time(record, _aware(record["recordedAtJST"]))
    if "modelInput" in record:
        inputs = record["modelInput"]
        if inputs is None:
            if record["horses"] or record["featureInputSha256"] is not None:
                raise ValueError("missing longshot model inputs")
        else:
            numbers = inputs["horseNumbers"]
            rows = np.asarray(inputs["rows"], dtype=float)
            if (fingerprint(inputs) != record["featureInputSha256"]
                    or inputs["features"] != list(MODEL_FEATURES)
                    or len(set(numbers)) != len(numbers)
                    or set(numbers) != {horse["number"] for horse in record["horses"]}
                    or rows.shape != (len(numbers), len(MODEL_FEATURES))
                    or not np.isfinite(rows).all()):
                raise ValueError("frozen longshot model inputs differ")


def record_first(root, race, feature_input, context, history, *, first_formal_capture,
                 clock=lambda: datetime.now(JST)) -> str:
    day, race_id = race["raceDate"], race["raceID"]
    path = path_for(root, day, race_id)
    source_digest = fingerprint(race)
    if path.is_file():
        saved = json.loads(path.read_text(encoding="utf-8"))
        validate_record(saved, day, race_id, model_spec())
        if saved["sourcePredictionSha256"] != source_digest:
            raise ValueError("longshot capture conflicts with the first formal prediction")
        return "already_frozen"
    if not first_formal_capture:
        return "first_capture_missing"
    capture_time(race, clock())
    scores = score_race(race, feature_input, context, history)
    # Repeat the clock check after feature construction and inference.
    now = capture_time(race, clock())
    record = {
        "schemaVersion": SCHEMA_VERSION, "researchOnly": True,
        "raceDate": day, "raceID": race_id, "startTime": race["startTime"],
        "predictionTiming": race["predictionTiming"], "captureQuality": race["captureQuality"],
        "capturedAtJST": race["capturedAtJST"], "recordedAtJST": now.isoformat(),
        "sourcePredictionSha256": source_digest, **scores,
    }
    record["recordFingerprint"] = fingerprint(record)
    _write_first(path, record)
    return "frozen"
