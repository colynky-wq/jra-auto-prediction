from __future__ import annotations

"""Early KeibaAI prediction snapshot.

Purpose
- Produce an early same-day / previous-evening snapshot separately from the formal
  8-12 minute live prediction.
- Reuse the fixed 2025 models and current market odds, but never overwrite
  RaceData.json (the formal live prediction bundle).
- Store the output in EarlyPredictionData.json so the iOS app can show
  "early" vs "final" side by side.

Important
- Early prediction is a research snapshot, not a formal recommendation.
- When current body weight/change is not yet published, use each horse's latest
  historical body-weight state as a forward-safe fallback. No future result data
  are used.
"""

import json
import os
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

import live_monitor_topk as live
import live_monitor_dry_run as base


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
    """Run the normal Top2/Top3 path; if unpublished body weight causes missing
    features, retry with latest historical body state only for the current rows.
    """
    try:
        return live.predict_live_race_topk(race, hist, target_date)
    except RuntimeError as exc:
        message = str(exc)
        if "37 features contain missing values" not in message:
            raise

    # Rebuild the same current table but fill unpublished body state before the
    # shared feature engine runs. This code deliberately mirrors live runtime.
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
    current["人気"] = current["単勝オッズ"].rank(ascending=True, method="min")

    for i, row in current.iterrows():
        if pd.isna(row.get("馬体重")) or pd.isna(row.get("増減")):
            body, diff = _latest_body_state(hist, str(row["馬キー"]))
            if pd.isna(row.get("馬体重")):
                current.at[i, "馬体重"] = body
            if pd.isna(row.get("増減")):
                current.at[i, "増減"] = diff

    # Temporarily replace JRA entry fetch result so the normal prediction path
    # receives the forward-safe filled body state without changing formal live code.
    original_fetch = base.fetch_jra_entry_table

    def patched_fetch(*args, **kwargs):
        e = entries.copy()
        by_no = current.set_index("馬番")
        e["馬体重"] = e["馬番"].map(by_no["馬体重"])
        e["増減"] = e["馬番"].map(by_no["増減"])
        return e, datetime.now(JST), "early-fallback://latest-historical-body-state"

    base.fetch_jra_entry_table = patched_fetch
    live.base.fetch_jra_entry_table = patched_fetch
    try:
        return live.predict_live_race_topk(race, hist, target_date)
    finally:
        base.fetch_jra_entry_table = original_fetch
        live.base.fetch_jra_entry_table = original_fetch


def build_snapshot(date8: str) -> dict:
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
        "schemaVersion": 1,
        "status": "research_early_snapshot",
        "formalRecommendation": False,
        "raceDate": f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}",
        "capturedAtJST": datetime.now(JST).isoformat(),
        "method": "fixed2025 models + available early win odds + latest historical body-state fallback when current weight unavailable",
        "races": output,
    }


def main() -> None:
    date8 = os.environ.get("KEIBAAI_EARLY_DATE", "").strip() or datetime.now(JST).strftime("%Y%m%d")
    payload = build_snapshot(date8)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print("EARLY_PREDICTION_WRITTEN", OUT_PATH, "races=", len(payload["races"]), "captured=", payload["capturedAtJST"])


if __name__ == "__main__":
    main()
