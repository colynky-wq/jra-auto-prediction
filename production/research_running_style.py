from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
RACEDATA_PATH = REPO / "RaceData.json"
OUT_PATH = REPO / "reports" / "RunningStyleResearch.json"

STYLE_ORDER = ["逃げ", "先行", "差し", "追込"]


def _num(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def classify_style(first_corner, field_size) -> str | None:
    """Coarse historical running-style tendency from first-corner position.

    This is deliberately a tendency label, not a definitive tactical forecast.
    """
    pos = _num(first_corner)
    n = _num(field_size)
    if pos is None or n is None or n <= 1 or pos < 1:
        return None
    if int(round(pos)) == 1:
        return "逃げ"
    ratio = (pos - 1.0) / (n - 1.0)
    if ratio <= 0.33:
        return "先行"
    if ratio <= 0.67:
        return "差し"
    return "追込"


def _confidence(n: int, dominance: float) -> str:
    if n >= 5 and dominance >= 0.60:
        return "高"
    if n >= 3 and dominance >= 0.45:
        return "中"
    return "低"


def _tendency(part: pd.DataFrame, recent: int = 6) -> dict | None:
    p = part.sort_values(["日付", "レースID"], ascending=[False, False]).copy()
    labels = [x for x in p["style"].head(recent).tolist() if x in STYLE_ORDER]
    if not labels:
        return None
    counts = Counter(labels)
    n = len(labels)
    dominant = max(STYLE_ORDER, key=lambda s: (counts[s], -STYLE_ORDER.index(s)))
    dominance = counts[dominant] / n
    return {
        "sampleRaces": n,
        "dominantStyle": dominant,
        "dominantSharePct": round(dominance * 100.0, 1),
        "confidence": _confidence(n, dominance),
        "distributionPct": {s: round(counts[s] / n * 100.0, 1) for s in STYLE_ORDER},
        "note": "過去の初角位置から見た脚質傾向。今回の実際の戦法を断定するものではありません。",
    }


def _evaluate_last_style(df: pd.DataFrame) -> dict:
    """Simple out-of-time baseline: previous valid style predicts the next style."""
    d = df.sort_values(["馬キー", "日付", "レースID"]).copy()
    d["prevStyle"] = d.groupby("馬キー")["style"].shift(1)
    e = d[d["style"].notna() & d["prevStyle"].notna()].copy()
    if len(e) == 0:
        return {"rows": 0, "exactPct": None, "adjacentPct": None}

    idx = {s: i for i, s in enumerate(STYLE_ORDER)}
    exact = 0
    adjacent = 0
    for _, row in e.iterrows():
        a = str(row["style"])
        b = str(row["prevStyle"])
        if a == b:
            exact += 1
        if a in idx and b in idx and abs(idx[a] - idx[b]) <= 1:
            adjacent += 1
    return {
        "rows": int(len(e)),
        "exactPct": round(exact / len(e) * 100.0, 2),
        "adjacentPct": round(adjacent / len(e) * 100.0, 2),
        "meaning": "前走脚質を次走脚質と仮定した単純基準。正確な4分類より『前寄り/中団/後ろ寄り』の傾向表示を優先するための参考。",
    }


def build() -> dict:
    if not HISTORY_PATH.exists():
        raise RuntimeError(f"missing history: {HISTORY_PATH}")
    df = pd.read_parquet(HISTORY_PATH)
    required = {"レースID", "日付", "馬名", "馬キー", "初角", "頭数", "障害"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"production_history missing columns: {missing}")

    d = df.copy()
    d["日付"] = pd.to_datetime(d["日付"], errors="coerce")
    d = d[d["日付"].notna()].copy()
    d = d[~d["障害"].fillna(False).astype(bool)].copy()
    d["style"] = [classify_style(a, n) for a, n in zip(d["初角"], d["頭数"])]
    d = d[d["style"].notna()].copy()

    # Evaluate a simple baseline on 2025 only; 2026 stays available for future
    # forward checking instead of being used to tune labels.
    evaluation_2025 = _evaluate_last_style(d[d["日付"].dt.year == 2025].copy())

    races = []
    if RACEDATA_PATH.exists():
        value = json.loads(RACEDATA_PATH.read_text(encoding="utf-8"))
        races = value if isinstance(value, list) else []

    by_name = defaultdict(list)
    for name, part in d.groupby("馬名", sort=False):
        by_name[str(name)].append(part)

    current = []
    for race in races:
        horses = []
        for h in race.get("horses") or []:
            name = str(h.get("name") or "")
            parts = by_name.get(name) or []
            tendency = None
            if parts:
                # If duplicate names ever exist, prefer the history block with
                # the most recent race date.
                part = max(parts, key=lambda x: x["日付"].max())
                tendency = _tendency(part)
            horses.append({
                "number": h.get("number"),
                "name": name,
                "styleTendency": tendency,
            })
        current.append({
            "raceDate": race.get("raceDate"),
            "raceID": str(race.get("raceID") or ""),
            "venue": race.get("venue"),
            "raceNumber": race.get("raceNumber"),
            "startTime": race.get("startTime"),
            "horses": horses,
        })

    return {
        "schemaVersion": 1,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "status": "research_tendency_only",
        "styleOrder": STYLE_ORDER,
        "evaluation2025": evaluation_2025,
        "currentRaces": current,
    }


def main() -> None:
    payload = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with_data = sum(
        1 for race in payload["currentRaces"] for horse in race["horses"]
        if horse.get("styleTendency")
    )
    print("2025 evaluation:", payload["evaluation2025"])
    print("current horses with tendency:", with_data)
    print("output:", OUT_PATH)


if __name__ == "__main__":
    main()
