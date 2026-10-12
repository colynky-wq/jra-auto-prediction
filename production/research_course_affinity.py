from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from horse_profile_runtime import venue_from_race_id


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
OUT_PATH = REPO / "reports" / "research_course_affinity.json"
PRIOR = 0.5
PRIOR_STRENGTH = 3.0


def _prior_mean(values: pd.Series, keys: list[pd.Series]) -> tuple[pd.Series, pd.Series]:
    x = pd.to_numeric(values, errors="coerce")
    xv = x.fillna(0.0)
    has = x.notna().astype(float)
    grouped_sum = xv.groupby(keys, sort=False).cumsum() - xv
    grouped_count = has.groupby(keys, sort=False).cumsum() - has
    mean = (grouped_sum + PRIOR * PRIOR_STRENGTH) / (grouped_count + PRIOR_STRENGTH)
    return mean.astype(float), grouped_count.astype(float)


def add_course_affinity_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["日付"] = pd.to_datetime(d["日付"], errors="coerce")
    d = d.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    d["競馬場"] = d["レースID"].map(venue_from_race_id)

    finish = pd.to_numeric(d["着順"], errors="coerce")
    field = pd.to_numeric(d["頭数"], errors="coerce")
    denom = (field - 1).where(field > 1)
    finish_pct = ((finish - 1) / denom).clip(0, 1)
    d["走破スコア"] = 1.0 - finish_pct

    d["同場走破平均"], d["同場回数"] = _prior_mean(
        d["走破スコア"], [d["馬キー"], d["競馬場"]]
    )
    d["同場芝ダ走破平均"], d["同場芝ダ回数"] = _prior_mean(
        d["走破スコア"], [d["馬キー"], d["競馬場"], d["芝ダ"]]
    )

    # Model-facing names. Higher is better and all values are strictly based on
    # races before the current row because the current result is subtracted.
    d["同場適性"] = d["同場走破平均"]
    d["同場回数c"] = d["同場回数"].clip(upper=5)
    d["同場芝ダ適性"] = d["同場芝ダ走破平均"]
    d["同場芝ダ回数c"] = d["同場芝ダ回数"].clip(upper=5)
    return d


def _bucket_table(eval_df: pd.DataFrame, score_col: str, count_col: str) -> list[dict]:
    x = eval_df[eval_df[count_col] >= 1].copy()
    if len(x) < 100:
        return []
    x["bucket"] = pd.qcut(x[score_col], 5, labels=False, duplicates="drop")
    rows = []
    for bucket, g in x.groupby("bucket", sort=True):
        finish = pd.to_numeric(g["着順"], errors="coerce")
        rows.append(
            {
                "bucket": int(bucket) + 1,
                "rows": int(len(g)),
                "meanAffinityScore": round(float(g[score_col].mean()), 4),
                "winRatePct": round(float((finish == 1).mean() * 100.0), 3),
                "top3RatePct": round(float((finish <= 3).mean() * 100.0), 3),
                "meanFinish": round(float(finish.mean()), 3),
            }
        )
    return rows


def main() -> None:
    d = pd.read_parquet(HISTORY_PATH)
    required = {"レースID", "日付", "馬番", "馬キー", "芝ダ", "障害", "着順", "頭数"}
    missing = sorted(required - set(d.columns))
    if missing:
        raise RuntimeError(f"missing columns: {missing}")

    d = add_course_affinity_features(d)
    d = d[(~d["障害"].fillna(False).astype(bool)) & d["競馬場"].notna()].copy()
    d["year"] = d["日付"].dt.year
    eval_2025 = d[d["year"] == 2025].copy()

    payload = {
        "schemaVersion": 1,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "馬×競馬場、および馬×競馬場×芝ダの過去相性が2025年の成績と関係するかを、未来情報なしで確認する探索研究。",
        "leakagePolicy": (
            "各行の適性値は、その行より前の同一馬の成績だけで計算。"
            "現在レースの着順は累積和から差し引くため特徴量に入らない。"
        ),
        "featureCandidates": ["同場適性", "同場回数c", "同場芝ダ適性", "同場芝ダ回数c"],
        "evaluationYear": 2025,
        "evaluationRows": int(len(eval_2025)),
        "venue": _bucket_table(eval_2025, "同場適性", "同場回数"),
        "venueSurface": _bucket_table(eval_2025, "同場芝ダ適性", "同場芝ダ回数"),
        "nextDecision": (
            "順位に明確な傾向があっても即採用しない。37特徴量の現行モデル対比で再学習し、"
            "2025未使用評価が改善した場合だけ本番候補にする。"
        ),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
