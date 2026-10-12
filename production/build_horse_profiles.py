from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from horse_profile_runtime import (
    distance_band,
    horse_profile_id,
    race_number_from_race_id,
    venue_from_race_id,
)


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
OUT_DIR = REPO / "HorseProfiles"
INDEX_PATH = REPO / "HorseIndex.json"
MANIFEST_PATH = REPO / "HorseProfileManifest.json"

RECENT_RACES = int(os.environ.get("KEIBAAI_HORSE_PROFILE_RECENT", "12") or "12")
LIMIT = int(os.environ.get("KEIBAAI_HORSE_PROFILE_LIMIT", "0") or "0")
PRIOR_STRENGTH = 3.0


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if not math.isfinite(float(value)):
            return None
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _safe_num(value, default=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return x


def _confidence(n: int) -> str:
    if n >= 5:
        return "高"
    if n >= 2:
        return "中"
    return "低"


def _performance(row: pd.Series) -> float | None:
    finish = _safe_num(row.get("着順"))
    field = _safe_num(row.get("頭数"))
    if finish is None or field is None or field <= 1:
        return None
    pct = min(1.0, max(0.0, (finish - 1.0) / (field - 1.0)))
    return 1.0 - pct


def _summary_stats(g: pd.DataFrame) -> dict:
    finish = pd.to_numeric(g["着順"], errors="coerce")
    valid = finish.notna()
    n = int(valid.sum())
    wins = int((finish == 1).sum())
    top3 = int((finish <= 3).sum())
    return {
        "starts": n,
        "wins": wins,
        "top3": top3,
        "winRatePct": round(wins / n * 100.0, 1) if n else None,
        "top3RatePct": round(top3 / n * 100.0, 1) if n else None,
        "averageFinish": round(float(finish[valid].mean()), 2) if n else None,
    }


def _group_affinity(g: pd.DataFrame, keys: list[str], label_fields: list[str]) -> list[dict]:
    rows = []
    for key, part in g.groupby(keys, dropna=False, sort=False):
        if not isinstance(key, tuple):
            key = (key,)
        perf = pd.to_numeric(part["performance"], errors="coerce")
        perf = perf[perf.notna()]
        n = int(len(perf))
        if n == 0:
            continue
        mean_perf = float(perf.mean())
        smoothed = (float(perf.sum()) + 0.5 * PRIOR_STRENGTH) / (n + PRIOR_STRENGTH)
        stats = _summary_stats(part)
        item = {
            field: _json_value(value)
            for field, value in zip(label_fields, key)
        }
        item.update(stats)
        item.update(
            {
                "performanceScore": round(mean_perf * 100.0, 1),
                "smoothedPerformanceScore": round(smoothed * 100.0, 1),
                "confidence": _confidence(n),
            }
        )
        rows.append(item)
    rows.sort(
        key=lambda x: (
            -float(x.get("smoothedPerformanceScore") or 0.0),
            -int(x.get("starts") or 0),
            str(tuple(x.get(f) for f in label_fields)),
        )
    )
    return rows


def _recent_races(g: pd.DataFrame) -> list[dict]:
    out = []
    latest = g.sort_values(["日付", "レースID", "馬番"], ascending=[False, False, True]).head(RECENT_RACES)
    for _, row in latest.iterrows():
        rid = str(row.get("レースID") or "")
        out.append(
            {
                "date": _json_value(row.get("日付")),
                "raceID": rid,
                "venue": row.get("競馬場"),
                "raceNumber": race_number_from_race_id(rid),
                "surface": _json_value(row.get("芝ダ")),
                "distance": int(row["距離"]) if _safe_num(row.get("距離")) is not None else None,
                "class": _json_value(row.get("クラス")),
                "horseNumber": int(row["馬番"]) if _safe_num(row.get("馬番")) is not None else None,
                "finish": int(row["着順"]) if _safe_num(row.get("着順")) is not None else None,
                "popularity": int(row["人気"]) if _safe_num(row.get("人気")) is not None else None,
                "winOdds": round(float(row["単勝オッズ"]), 1) if _safe_num(row.get("単勝オッズ")) is not None else None,
                "jockey": _json_value(row.get("騎手")),
                "weightCarried": _safe_num(row.get("斤量")),
                "bodyWeight": int(row["馬体重"]) if _safe_num(row.get("馬体重")) is not None else None,
                "bodyWeightChange": int(row["増減"]) if _safe_num(row.get("増減")) is not None else None,
            }
        )
    return out


def _profile(horse_key: str, g: pd.DataFrame) -> dict:
    g = g.sort_values(["日付", "レースID", "馬番"]).copy()
    latest = g.iloc[-1]
    pid = horse_profile_id(horse_key)

    venues = _group_affinity(g, ["競馬場"], ["venue"])
    venue_surface = _group_affinity(g, ["競馬場", "芝ダ"], ["venue", "surface"])
    surfaces = _group_affinity(g, ["芝ダ"], ["surface"])
    distances = _group_affinity(g, ["距離帯表示"], ["distanceBand"])

    best_venues = [x for x in venues if int(x.get("starts") or 0) >= 2][:3]

    return {
        "schemaVersion": 1,
        "profileID": pid,
        "horseKey": horse_key,
        "name": str(latest.get("馬名") or ""),
        "currentAge": int(latest["年齢"]) if _safe_num(latest.get("年齢")) is not None else None,
        "latestDate": _json_value(latest.get("日付")),
        "latestJockey": _json_value(latest.get("騎手")),
        "latestTrainer": _json_value(latest.get("調教師")),
        "career": _summary_stats(g),
        "bestVenues": best_venues,
        "venueStats": venues,
        "venueSurfaceStats": venue_surface,
        "surfaceStats": surfaces,
        "distanceStats": distances,
        "recentRaces": _recent_races(g),
        "notes": {
            "venueScore": "着順を頭数で補正した0-100の走破スコアを、少数出走では50点へ寄せた参考値。",
            "confidence": "出走1回=低、2-4回=中、5回以上=高。少数サンプルを過信しないための表示。",
        },
    }


def build_profiles(df: pd.DataFrame) -> tuple[list[dict], dict[str, dict], dict]:
    required = {
        "レースID", "日付", "馬番", "馬名", "馬キー", "年齢", "斤量", "騎手", "調教師",
        "着順", "人気", "単勝オッズ", "芝ダ", "距離", "クラス", "障害", "馬体重", "増減", "頭数",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"production_history missing columns: {missing}")

    d = df.copy()
    d["日付"] = pd.to_datetime(d["日付"], errors="coerce")
    d = d[d["日付"].notna()].copy()
    d["馬キー"] = d["馬キー"].astype(str).str.strip()
    d = d[(d["馬キー"] != "") & (d["馬キー"] != "nan")].copy()
    d["競馬場"] = d["レースID"].map(venue_from_race_id)
    d["距離帯表示"] = d["距離"].map(distance_band)
    d["performance"] = d.apply(_performance, axis=1)

    flat = d[~d["障害"].fillna(False).astype(bool)].copy()
    flat = flat[flat["競馬場"].notna()].copy()

    keys = flat.groupby("馬キー")["日付"].max().sort_values(ascending=False).index.tolist()
    if LIMIT > 0:
        keys = keys[:LIMIT]
        flat = flat[flat["馬キー"].isin(keys)].copy()

    profiles: dict[str, dict] = {}
    index = []
    for i, (horse_key, g) in enumerate(flat.groupby("馬キー", sort=False), start=1):
        profile = _profile(str(horse_key), g)
        pid = profile["profileID"]
        profiles[pid] = profile
        index.append(
            {
                "profileID": pid,
                "name": profile["name"],
                "currentAge": profile["currentAge"],
                "latestDate": profile["latestDate"],
                "starts": profile["career"]["starts"],
                "wins": profile["career"]["wins"],
                "top3RatePct": profile["career"]["top3RatePct"],
                "bestVenue": profile["bestVenues"][0].get("venue") if profile["bestVenues"] else None,
                "bestVenueScore": profile["bestVenues"][0].get("smoothedPerformanceScore") if profile["bestVenues"] else None,
                "shard": pid[:2],
            }
        )
        if i % 2000 == 0:
            print("profiles built:", i)

    index.sort(key=lambda x: (str(x.get("name") or ""), str(x.get("profileID") or "")))
    latest_date = flat["日付"].max().strftime("%Y-%m-%d") if len(flat) else None
    manifest = {
        "schemaVersion": 1,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "production/production_history.parquet",
        "horseCount": len(profiles),
        "historyRowsUsed": int(len(flat)),
        "latestHistoryDate": latest_date,
        "shardCount": len({pid[:2] for pid in profiles}),
        "profilePathPattern": "HorseProfiles/{shard}.json",
        "identity": "SHA1(existing 馬キー) first 20 hex chars",
        "scope": "JRA flat races only",
        "affinityMethod": "field-size adjusted finish performance, shrunk toward 50 with prior strength 3",
    }
    return index, profiles, manifest


def main() -> None:
    if not HISTORY_PATH.exists():
        raise RuntimeError(f"missing history: {HISTORY_PATH}")

    df = pd.read_parquet(HISTORY_PATH)
    print("history rows:", len(df))
    print("history columns:", list(df.columns))

    index, profiles, manifest = build_profiles(df)

    _write_json(INDEX_PATH, {
        "schemaVersion": 1,
        "generatedAtJST": manifest["generatedAtJST"],
        "horseCount": len(index),
        "horses": index,
    })
    _write_json(MANIFEST_PATH, manifest)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shards: dict[str, dict[str, dict]] = {}
    for pid, profile in profiles.items():
        shards.setdefault(pid[:2], {})[pid] = profile
    for shard, payload in shards.items():
        _write_json(OUT_DIR / f"{shard}.json", {
            "schemaVersion": 1,
            "shard": shard,
            "profiles": payload,
        })

    print("horse profiles:", len(profiles))
    print("shards:", len(shards))
    print("index:", INDEX_PATH)
    print("manifest:", MANIFEST_PATH)


if __name__ == "__main__":
    main()
