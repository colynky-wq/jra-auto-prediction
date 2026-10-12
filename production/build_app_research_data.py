from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app_research_runtime import METHOD_VERSION, STYLE_ORDER, classify_style, finite, practical_candidates, tendency
from horse_profile_runtime import normalize_horse_name

REPO = Path(__file__).resolve().parent.parent
HISTORY_PATH = REPO / "production/production_history.parquet"
RACEDATA_PATH = REPO / "RaceData.json"
OUT_PATH = REPO / "AppResearchData.json"
VALIDATION_PATH = REPO / "reports/RunningStyleValidation.json"
HISTORY_COLUMNS = ["レースID", "日付", "馬名", "馬キー", "初角", "頭数", "障害"]
JST = timezone(timedelta(hours=9))


def prepare_history(history: pd.DataFrame) -> pd.DataFrame:
    d = history.copy()
    d["日付"] = pd.to_datetime(d["日付"], errors="coerce").dt.normalize()
    d = d[d["日付"].notna() & ~d["障害"].fillna(False).astype(bool)].copy()
    d = d[d["馬キー"].notna() & d["馬名"].notna()].copy()
    names = {name: normalize_horse_name(name) for name in d["馬名"].unique()}
    d["normalizedName"] = d["馬名"].map(names)
    return d.sort_values(["日付", "レースID"])


def race_date(race: dict) -> pd.Timestamp:
    text = str(race.get("raceDate") or "").replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError("raceDate must be YYYY-MM-DD or YYYYMMDD")
    return pd.Timestamp(datetime.strptime(text, "%Y%m%d")).normalize()


def validate_race(race: dict) -> None:
    race_date(race)
    numbers = [horse["number"] for horse in race["horses"]]
    if len(set(numbers)) != len(numbers):
        raise ValueError("duplicate horse number")
    for horse in race["horses"]:
        for key in ["aiProbability", "marketProbability"]:
            value = finite(horse[key])
            if value is None or not 0 <= value <= 100:
                raise ValueError("invalid probability in " + key)
        odds = finite(horse["odds"])
        if odds is None or odds <= 0:
            raise ValueError("invalid win odds")


def build_races(history: pd.DataFrame, races: list[dict]) -> list[dict]:
    d = prepare_history(history)
    wanted = {normalize_horse_name(h["name"]) for race in races for h in race["horses"]}
    d = d[d["normalizedName"].isin(wanted)].copy()
    d["style"] = [classify_style(pos, count) for pos, count in zip(d["初角"], d["頭数"])]
    out = []
    for race in races:
        validate_race(race)
        cutoff = race_date(race)
        # Same-day results and all future results are excluded, even for replay.
        past = d[d["日付"] < cutoff]
        latest = past.drop_duplicates("normalizedName", keep="last")
        keys = dict(zip(latest["normalizedName"], latest["馬キー"]))
        groups = {key: group for key, group in past.groupby("馬キー", sort=False)}
        horses = []
        for horse in race["horses"]:
            key = keys.get(normalize_horse_name(horse["name"]))
            group = groups.get(key)
            stat = None
            if group is not None:
                valid = group[group["style"].notna()].tail(6)
                stat = tendency(valid["style"].tolist())
                if stat:
                    stat["latestHistoryDate"] = valid["日付"].max().strftime("%Y-%m-%d")
            horses.append({
                "number": horse["number"], "name": horse["name"],
                "sourceAiProbabilityPct": horse["aiProbability"],
                "sourceMarketProbabilityPct": horse["marketProbability"],
                "sourceOdds": horse["odds"], "styleTendency": stat,
            })
        out.append({
            "raceID": race["raceID"], "raceDate": cutoff.strftime("%Y-%m-%d"),
            "horses": horses, "longshotCandidates": practical_candidates(race),
        })
    return out


def evaluate_2025(history: pd.DataFrame) -> dict:
    d = prepare_history(history)
    d = d[d["日付"].dt.year <= 2025].copy()
    d["style"] = [classify_style(pos, count) for pos, count in zip(d["初角"], d["頭数"])]
    d = d[d["style"].notna()]
    keys = set(d.loc[d["日付"].dt.year == 2025, "馬キー"])
    d = d[d["馬キー"].isin(keys)].sort_values(["馬キー", "日付", "レースID"])
    window = deque(maxlen=6)
    current_key = None
    rows = last_exact = six_exact = last_adjacent = six_adjacent = 0
    idx = {style: i for i, style in enumerate(STYLE_ORDER)}
    records = d[["馬キー", "日付", "style"]].itertuples(index=False, name=None)
    for (key, day), group in itertools.groupby(records, key=lambda row: (row[0], row[1])):
        if key != current_key:
            window.clear()
            current_key = key
        labels = [row[2] for row in group]
        if day.year == 2025 and window:
            predicted = tendency(list(window))["dominantStyle"]
            last = window[-1]
            for actual in labels:
                rows += 1
                last_exact += actual == last
                six_exact += actual == predicted
                last_adjacent += abs(idx[actual] - idx[last]) <= 1
                six_adjacent += abs(idx[actual] - idx[predicted]) <= 1
        # Add the day's outcomes only after every prediction on that day.
        window.extend(labels)
    pct = lambda count: round(count / rows * 100, 2) if rows else None
    return {
        "schemaVersion": 1, "methodVersion": METHOD_VERSION, "evaluationYear": 2025,
        "rows": rows, "lastStyleExactPct": pct(last_exact), "lastSixExactPct": pct(six_exact),
        "lastStyleAdjacentPct": pct(last_adjacent), "lastSixAdjacentPct": pct(six_adjacent),
        "meaning": "各レースより前の履歴だけで比較。2026年は評価や条件選びに使わない。脚質割合は過去の観測頻度であり、次走の戦法の確率ではない。",
    }


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    race_bytes = RACEDATA_PATH.read_bytes()
    races = json.loads(race_bytes)
    if not isinstance(races, list):
        raise ValueError("RaceData.json must contain a list")
    if len({race["raceID"] for race in races}) != len(races):
        raise ValueError("duplicate raceID")
    inputs = {
        "predictionSHA256": hashlib.sha256(race_bytes).hexdigest(),
        "historySHA256": hashlib.sha256(HISTORY_PATH.read_bytes()).hexdigest(),
        "methodVersion": METHOD_VERSION,
    }
    if OUT_PATH.exists() and not args.evaluate:
        old = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        if old.get("sourceInputs") == inputs:
            print("Research display unchanged")
            return
    history = pd.read_parquet(HISTORY_PATH, columns=HISTORY_COLUMNS)
    evaluation = None
    if args.evaluate:
        evaluation = evaluate_2025(history)
        write_json(VALIDATION_PATH, evaluation)
        print("2025 evaluation:", evaluation)
    elif VALIDATION_PATH.exists():
        stored = json.loads(VALIDATION_PATH.read_text(encoding="utf-8"))
        if stored.get("methodVersion") == METHOD_VERSION:
            evaluation = stored
    payload = {
        "schemaVersion": 1, "status": "research_display_only",
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "stakeSizingEnabled": False, "formalSignalsChanged": False,
        "sourceInputs": inputs, "evaluation2025": evaluation, "races": build_races(history, races),
    }
    write_json(OUT_PATH, payload)
    for day in sorted({race["raceDate"] for race in payload["races"]}):
        dated = {**payload, "races": [race for race in payload["races"] if race["raceDate"] == day]}
        write_json(REPO / "reports" / day.replace("-", "") / "AppResearchData.json", dated)
    print("Research races:", len(payload["races"]))
    print("Style profiles:", sum(bool(h["styleTendency"]) for race in payload["races"] for h in race["horses"]))


if __name__ == "__main__":
    main()
