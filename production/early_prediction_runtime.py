from __future__ import annotations

"""KeibaAI early-prediction research snapshot.

This is deliberately separate from the formal 8-12 minute RaceData prediction.
It records either the previous-evening or same-day-morning market state, never
replaces RaceData.json, and is displayed as research-only in the app.

When the current body weight/change is not yet published, the latest historical
body state for that horse is used as a forward-safe fallback. The snapshot is
therefore useful for comparison research but is NOT treated as the formal live
prediction until it has accumulated validation data.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

import live_monitor_dry_run as base
import live_monitor_topk as live


HERE = Path(__file__).resolve().parent
OUT_PATH = HERE.parent / "EarlyPredictionData.json"
JST = base.JST


def _latest_body_state(hist: pd.DataFrame, horse_key: str) -> tuple[float, float]:
    rows = hist.loc[hist["馬キー"].astype(str) == str(horse_key)].sort_values("日付")
    if rows.empty:
        return 0.0, 0.0
    last = rows.iloc[-1]
    body = pd.to_numeric(pd.Series([last.get("馬体重")]), errors="coerce").iloc[0]
    diff = pd.to_numeric(pd.Series([last.get("増減")]), errors="coerce").iloc[0]
    return (float(body) if pd.notna(body) else 0.0, float(diff) if pd.notna(diff) else 0.0)


def _safe_predict(race: dict, hist: pd.DataFrame, target_date: pd.Timestamp) -> pd.DataFrame:
    try:
        return live.predict_live_race_topk(race, hist, target_date)
    except RuntimeError as exc:
        if "37 features contain missing values" not in str(exc):
            raise

    race = base.fetch_meta(race)
    rid = race["race_id"]
    odds, _ = base.fetch_win_odds(rid)
    if len(odds) < 2:
        raise RuntimeError("insufficient live win odds")

    entries, _, _ = base.fetch_jra_entry_table(rid, base.TARGET_DATE8, base.JST)
    active = set(odds["馬番"].astype(int))
    entries = entries.loc[entries["馬番"].astype(int).isin(active)].copy().sort_values("馬番").reset_index(drop=True)
    if len(entries) != len(odds):
        raise RuntimeError(f"entry/odds count mismatch: {len(entries)} / {len(odds)}")

    hist_jockeys = sorted(set(hist["騎手"].dropna().astype(str)))
    hist_trainers = sorted(set(hist["調教師"].dropna().astype(str)))
    entries["騎手"] = entries["騎手_JRA"].map(lambda x: base.match_history_name(x, hist_jockeys))
    entries["調教師"] = entries["調教師_JRA"].map(lambda x: base.match_history_name(x, hist_trainers))
    if entries["騎手"].isna().any() or entries["調教師"].isna().any():
        raise RuntimeError("jockey/trainer history-name matching failed")

    from horse_identity_runtime import assign_horse_keys
    entries["馬キー"] = assign_horse_keys(entries, hist, target_date)
    current = entries.merge(odds, on="馬番", how="inner", validate="one_to_one")

    for i, row in current.iterrows():
        body_missing = pd.isna(row.get("馬体重"))
        diff_missing = pd.isna(row.get("増減"))
        if body_missing or diff_missing:
            body, diff = _latest_body_state(hist, str(row["馬キー"]))
            if body_missing:
                current.at[i, "馬体重"] = body
            if diff_missing:
                current.at[i, "増減"] = diff

    original_fetch = base.fetch_jra_entry_table

    def patched_fetch(*args, **kwargs):
        filled = entries.copy()
        by_no = current.set_index("馬番")
        filled["馬体重"] = filled["馬番"].map(by_no["馬体重"])
        filled["増減"] = filled["馬番"].map(by_no["増減"])
        return filled, datetime.now(JST), "early-fallback://latest-historical-body-state"

    base.fetch_jra_entry_table = patched_fetch
    live.base.fetch_jra_entry_table = patched_fetch
    try:
        return live.predict_live_race_topk(race, hist, target_date)
    finally:
        base.fetch_jra_entry_table = original_fetch
        live.base.fetch_jra_entry_table = original_fetch


def _snapshot(date8: str, stage: str) -> dict:
    target_date = pd.Timestamp(f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}")
    hist = base.load_history(target_date)
    races = base.fetch_schedule(date8)
    output = []

    for race in races:
        if race.get("surface") == "障" or "障害" in str(race.get("race_name", "")):
            continue
        try:
            out = _safe_predict(race, hist, target_date)
        except Exception as exc:
            print("EARLY_SKIP", race.get("race_id"), type(exc).__name__, str(exc))
            continue

        ranked = out.sort_values("AI勝率(%)", ascending=False).reset_index(drop=True)
        horses = []
        for rank, (_, row) in enumerate(ranked.iterrows(), start=1):
            horses.append({
                "rank": rank,
                "number": int(row["馬番"]),
                "name": str(row["馬名"]),
                "odds": round(float(row["単勝オッズ"]), 1),
                "marketProbability": round(float(row["市場勝率(%)"]), 3),
                "aiProbability": round(float(row["AI勝率(%)"]), 3),
                "aiTop2Probability": round(float(row["AI2着内率(%)"]), 3),
                "aiTop3Probability": round(float(row["AI3着内率(%)"]), 3),
                "aiMarketRatio": round(float(row["AI÷市場"]), 4),
            })
        output.append({
            "raceID": str(race["race_id"]),
            "raceDate": f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}",
            "venue": str(race.get("venue") or ""),
            "raceNumber": int(race.get("race_no") or 0),
            "startTime": str(race.get("start_time") or ""),
            "raceName": str(race.get("race_name") or ""),
            "horses": horses,
        })

    return {
        "stage": stage,
        "capturedAtJST": datetime.now(JST).isoformat(),
        "races": output,
    }


def _load_existing() -> dict:
    if not OUT_PATH.exists():
        return {}
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main() -> None:
    date8 = os.environ.get("KEIBAAI_EARLY_DATE", "").strip() or datetime.now(JST).strftime("%Y%m%d")
    stage = os.environ.get("KEIBAAI_EARLY_STAGE", "morning").strip() or "morning"
    if stage not in {"previous_evening", "morning"}:
        raise RuntimeError(f"unsupported KEIBAAI_EARLY_STAGE: {stage}")

    snap = _snapshot(date8, stage)
    race_date = f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"
    existing = _load_existing()
    snapshots = existing.get("snapshots", {}) if existing.get("raceDate") == race_date else {}
    if not isinstance(snapshots, dict):
        snapshots = {}
    snapshots[stage] = snap

    payload = {
        "schemaVersion": 1,
        "status": "research_early_snapshot",
        "formalRecommendation": False,
        "raceDate": race_date,
        "method": "fixed2025 models + available early win odds; current body state uses latest historical fallback when unpublished",
        "snapshots": snapshots,
    }
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print("EARLY_PREDICTION_WRITTEN", OUT_PATH, "stage=", stage, "races=", len(snap["races"]), "captured=", snap["capturedAtJST"])


if __name__ == "__main__":
    main()
