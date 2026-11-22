#!/usr/bin/env python3
"""Score the immutable <=2025 no-current-odds candidate on 2026 history.

READ-ONLY MODEL: 2026 is never fitted, used for early stopping, or used
to choose model parameters. This is retrospective, not a first-frozen T-10
prospective result. 2026 final odds are used only as a later-timestamp
benchmark and eligibility check, never as a challenger input.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import add_features, add_rating
from train_no_current_odds_challenger import (
    FEATURES, HISTORY, MODEL_PATH, REPORT_PATH, ROOT, VERSION,
    ensure, evaluate_event, normalized_win, probability_triplet,
    verify_features,
)
from train_top2_top3_models import BASE_COLS, plackett_luce_top2_top3

JST = timezone(timedelta(hours=9))
REPORT = ROOT / "research" / "NoCurrentOdds2026Retrospective.json"


def verified_candidate(meta, model_bytes):
    ensure(meta.get("version") == VERSION, "candidate version changed")
    ensure(meta.get("modelSha256") ==
           hashlib.sha256(model_bytes).hexdigest(),
           "model SHA differs from its frozen metadata")
    ensure(meta.get("featureCount") == 36 and
           meta.get("features") == list(FEATURES) and
           "市場ロジット" not in FEATURES, "model feature contract changed")
    ensure(meta.get("finalRefitThrough") == "2025-12-31" and
           meta.get("candidatePromotionApproved") is False and
           meta.get("autoBetEnabled") is False and
           meta.get("formalPredictionAIChanged") is False,
           "model promotion and training boundary must remain locked")


def prepare_2026(frame: pd.DataFrame) -> pd.DataFrame:
    ensure(list(frame.columns) == BASE_COLS,
           "source history schema changed")
    d = frame.copy()
    d["日付"] = pd.to_datetime(d["日付"], errors="coerce")
    ensure(d["日付"].notna().all(), "unparseable race date")
    ensure((d["日付"].dt.year < 2027).all(), "future-year results in source")
    ensure((d["日付"].dt.year <= 2025).any() and
           (d["日付"].dt.year == 2026).any(),
           "historical prior and 2026 evaluations required")
    d = d.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    d = add_rating(d)
    d = add_features(d)
    valid = (
        (d["日付"].dt.year == 2026) &
        d["対象"].fillna(False).astype(bool) &
        d["着順"].notna() &
        ~d["障害"].fillna(False).astype(bool) &
        (d["頭数"] >= 3) &
        d["市場確率"].notna()
    )
    d = d.loc[valid].copy().reset_index(drop=True)

    def complete(g):
        rank = pd.to_numeric(g["着順"], errors="coerce")
        return (len(g) >= 3 and g["馬番"].nunique() == len(g)
                and all((rank == k).sum() == 1 for k in (1, 2, 3)))
    d = d.groupby("レースID", sort=False).filter(complete).reset_index(drop=True)
    ensure(len(d) > 1000 and d["レースID"].nunique() > 80,
           "insufficient complete 2026 evaluation races")
    ensure((d["日付"].dt.year == 2026).all(),
           "cross-year evaluation leakage")
    verify_features(d)
    return d


def score(data: pd.DataFrame, model: lgb.Booster) -> dict:
    candidate = probability_triplet(data, model)
    reference = normalized_win(
        data["市場確率"].to_numpy(dtype=float), data["レースID"])
    rank = pd.DataFrame({
        "レースID": data["レースID"].to_numpy(),
        "市場確率": reference,
    })
    r2, r3 = plackett_luce_top2_top3(rank)
    benchmarks = {"win": reference, "top2": r2, "top3": r3}
    label = {
        "win": (data["着順"] == 1).to_numpy(dtype=float),
        "top2": (data["着順"] <= 2).to_numpy(dtype=float),
        "top3": (data["着順"] <= 3).to_numpy(dtype=float),
    }
    return {event: evaluate_event(data, label[event], benchmarks[event],
                                   candidate[event], event)
            for event in ("win", "top2", "top3")}


def build_report(meta, hist, data, evaluations, model_sha):
    ensure((data["日付"].dt.year == 2026).all(),
           "non-2026 evaluation date")
    ensure(meta["modelSha256"] == model_sha, "unfrozen model")
    return {
        "schemaVersion": 1,
        "version": "no-current-odds-2026-retrospective-v1",
        "generatedAtJST": datetime.now(JST).isoformat(),
        "status": "retrospective_research_only",
        "researchOnly": True,
        "formalPredictionAIChanged": False,
        "autoBetEnabled": False,
        "candidatePromotionApproved": False,
        "candidateModelVersion": meta["version"],
        "candidateModelSha256": model_sha,
        "candidateTrainingThrough": meta["finalRefitThrough"],
        "features": list(FEATURES),
        "inputOddsIndependent": True,
        "sourceHistoricalRows": len(hist),
        "sourceLastRaceDate": pd.to_datetime(hist["日付"]).max().date().isoformat(),
        "validationPeriod": {
            "start": data["日付"].min().date().isoformat(),
            "end": data["日付"].max().date().isoformat(),
        },
        "scoredRaces": int(data["レースID"].nunique()),
        "scoredHorseRows": len(data),
        "results": evaluations,
        "limitations": [
            "2026 history is retrospective and has been examined in the wider project.",
            "The challenger is frozen from <=2025, with no 2026 training or tuning.",
            "Feature values were rebuilt chronologically from race result archives; this is NOT a timestamp-frozen T-10 prediction.",
            "Closing market odds are not available at T-10 and only serve as a later-timestamp benchmark.",
            "No direct comparison with existing production AI at the exact same prediction time.",
            "Previous race popularity may be a historical signal; same-race market logit and current popularity are excluded.",
            "Any feature found unavailable before post time must be excluded before interpreting scores as prereace performance.",
            "No horse-level iid confidence claims: paired uncertainty resamples races.",
            "The model was not promoted and no real-money return is implied.",
        ],
    }


def markdown(report):
    lines = [
        "# 2026 retrospective test: no-current-odds challenger", "",
        "Status: " + report["status"],
        f"Historical candidate training through: {report['candidateTrainingThrough']}",
        f"2026 races: {report['scoredRaces']:,} / horses: {report['scoredHorseRows']:,}",
        f"Evaluation dates: {report['validationPeriod']['start']} to {report['validationPeriod']['end']}",
        "",
        "| Event | Candidate Brier | Closing-market Brier | Candidate minus market | Paired-race bootstrap 95% |",
        "|---|---:|---:|---:|---|",
    ]
    for e in ("win", "top2", "top3"):
        r = report["results"][e]
        lines.append(
            f"| {e} | {r['candidateNoCurrentOddsBrier']:.6f} | "
            f"{r['marketFinalOddsBrier']:.6f} | "
            f"{r['candidateMinusFinalMarketBrier']:+.6f} | "
            f"{r['pairedRaceBootstrap95']} |"
        )
    lines += [
        "",
        "Lower Brier is better. Positive delta means the final market is better.",
        "Final odds are a *later* benchmark; they cannot be directly compared with T-10.",
        "These 2026 records are reconstructed offline and may NOT be counted as first-frozen future shadows.",
        "No betting or automatic promotion.", "",
    ]
    return "\n".join(lines)


def main():
    ensure(HISTORY.is_file() and MODEL_PATH.is_file() and REPORT_PATH.is_file(),
           "existing research candidate and source required")
    model_bytes = MODEL_PATH.read_bytes()
    metadata = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    verified_candidate(metadata, model_bytes)
    model = lgb.Booster(model_file=str(MODEL_PATH))
    ensure(list(model.feature_name()) == list(FEATURES),
           "candidate feature identity mismatch")
    history = pd.read_parquet(HISTORY)
    data = prepare_2026(history)
    evaluations = score(data, model)
    report = build_report(metadata, history, data, evaluations,
                          hashlib.sha256(model_bytes).hexdigest())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                 allow_nan=False) + "\n", encoding="utf-8")
    REPORT.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print("NO_CURRENT_ODDS_2026_RETROSPECTIVE_OK",
          json.dumps({"races": report["scoredRaces"],
                      "eventBrierDeltas": {
                          e: r["candidateMinusFinalMarketBrier"]
                          for e, r in report["results"].items()
                      }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
