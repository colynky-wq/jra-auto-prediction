#!/usr/bin/env python3
"""Ablation: is longshot AI better than simply recalibrating Top3 market odds?

Fixed hypothesis before 2025/2026 scoring: market-only logit INTERCEPT is
selected on 2024 outcomes alone. Compare this simple baseline to the already
published, fully frozen, longshot residual AI on same horses/races (2025 with
<=2023 provisional booster; 2026 with <=2025 published refit).
No live T10 odds: historical final odds and popularity, not first-frozen prices.
No new model selection on 2025/2026 or automatic betting/promotion.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from train_longshot_top3 import (
    ROOT, HISTORY, METADATA, MODEL_PATH,
    VERSION as LONGSHOT_VERSION, FEATURES, JST,
    fit, infer_without_results, prepare_longshot, split_longshot,
    training_targets, logit, sigmoid
)
from train_no_current_odds_challenger import ensure, prepare_history
from evaluate_no_current_odds_2026_history import prepare_2026
from train_top2_top3_models import BASE_COLS, binary_logloss

REPORT = ROOT / "research/LongshotMarketCalibrationAblation.json"
VERSION = "longshot-market-calibration-ablation-v1-20261123"
OFFSET_GRID = (-0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
SEED = 20261123


def simple_market_calibration(p, offset):
    ensure(offset in OFFSET_GRID, "unregistered market-only offset")
    p = np.asarray(p, dtype=np.float64)
    ensure(len(p) > 0 and np.isfinite(p).all() and
           ((p > 0) & (p < 1)).all(), "invalid Top3 market reference")
    q = sigmoid(logit(p) + offset)
    ensure(np.isfinite(q).all() and ((q > 0) & (q < 1)).all(),
           "market calibration invalid")
    return q


def choose_offset(validation):
    dates = pd.to_datetime(validation["日付"]).dt.year
    ensure(len(dates) > 1000 and dates.eq(2024).all(),
           "market-only calibration MUST use 2024 exclusively")
    y, market = training_targets(validation)
    grid = [{
        "logitIntercept": o,
        "winOddsSource": "closing-market-implied-Top3",
        "validationBrier": float(np.mean(
            (simple_market_calibration(market, o) - y) ** 2)),
    } for o in OFFSET_GRID]
    pick = min(grid, key=lambda x:(x["validationBrier"],
                                   abs(x["logitIntercept"])))
    return pick, grid


def score_period(data, candidate, offset, period):
    y, market = training_targets(data)
    candidate = np.asarray(candidate, dtype=float)
    ensure(len(candidate) == len(y) and np.isfinite(candidate).all()
           and ((candidate > 0) & (candidate < 1)).all(),
           "candidate probability shape/validity mismatch")
    simple = simple_market_calibration(market, offset)
    probs = {"marketRaw": market, "marketIntercept": simple,
             "longshotResidualAI": candidate}
    brier = {k:float(np.mean((v-y)**2)) for k,v in probs.items()}
    logloss = {k:binary_logloss(y,v) for k,v in probs.items()}
    race_ids = data["レースID"].astype(str).to_numpy()
    per_race = pd.DataFrame({
        "race": race_ids,
        "versusSimple": (candidate-y)**2-(simple-y)**2,
        "versusMarket": (candidate-y)**2-(market-y)**2,
    }).groupby("race",sort=False).mean()
    ci = {}
    if len(per_race) >= 30:
        rng = np.random.default_rng(SEED)
        samples = rng.integers(0, len(per_race),
                               size=(1000, len(per_race)))
        for key in ("versusSimple","versusMarket"):
            boot = per_race[key].to_numpy()[samples].mean(axis=1)
            ci[key] = [float(np.quantile(boot, .025)),
                       float(np.quantile(boot, .975))]
    picked = {"sameTopHorse":0, "changedTopHorse":0,
              "bothHit":0,"marketOnlyHit":0,
              "candidateOnlyHit":0,"neitherHit":0}
    for _, positions in data.groupby("レースID", sort=False).indices.items():
        ids = np.asarray(positions, dtype=int)
        market_top = ids[np.argmax(market[ids])]
        ai_top = ids[np.argmax(candidate[ids])]
        same = market_top == ai_top
        picked["sameTopHorse" if same else "changedTopHorse"] += 1
        m_hit = bool(y[market_top] == 1)
        ai_hit = bool(y[ai_top] == 1)
        if m_hit and ai_hit: picked["bothHit"] += 1
        elif m_hit: picked["marketOnlyHit"] += 1
        elif ai_hit: picked["candidateOnlyHit"] += 1
        else: picked["neitherHit"] += 1
    ensure(sum(picked[k] for k in ("bothHit","marketOnlyHit",
                                  "candidateOnlyHit","neitherHit")) ==
           data["レースID"].nunique(), "ranking cohort incomplete")
    return {
        "period": period,
        "horseRows": len(data),
        "races": int(data["レースID"].nunique()),
        "top3HorseRows": int(y.sum()),
        "observedTop3Rate": float(y.mean()),
        "expectedRate": {k: float(v.mean()) for k,v in probs.items()},
        "horsePooledBrier": brier,
        "horsePooledLogloss": logloss,
        "aiMinusSimpleMarketBrier": brier["longshotResidualAI"]-brier["marketIntercept"],
        "aiMinusRawMarketBrier": brier["longshotResidualAI"]-brier["marketRaw"],
        "pairedRaceMeanBrierDifference":{
            k: float(per_race[k].mean()) for k in ("versusSimple","versusMarket")
        },
        "pairedRaceBootstrap95":ci,
        "rankingAmongLongshots":picked,
        "topRankedLongshotPodiumHits": {
            "marketAndSimple":picked["bothHit"]+picked["marketOnlyHit"],
            "longshotResidualAI":picked["bothHit"]+picked["candidateOnlyHit"],
        },
    }


def verify_meta(meta, model_raw):
    ensure(meta.get("version")==LONGSHOT_VERSION and
           meta.get("modelSha256")==hashlib.sha256(model_raw).hexdigest() and
           meta.get("features")==list(FEATURES) and
           meta.get("trainThroughFor2025Test")=="2023-12-31" and
           meta.get("yearUsedForEarlyStoppingAndMultiplier")==2024 and
           meta.get("refitThroughFor2026Test")=="2025-12-31" and
           meta.get("selectedResidualMultiplier") in (0.0,0.25,0.5,1.0) and
           meta.get("candidatePromotionApproved") is False and
           meta.get("autoBetEnabled") is False and
           meta.get("formalPredictionAIChanged") is False,
           "untrusted frozen longshot model/provenance")


def main():
    ensure(HISTORY.is_file() and MODEL_PATH.is_file() and METADATA.is_file(),
           "historical data and frozen candidate unavailable")
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    frozen_raw = MODEL_PATH.read_bytes()
    verify_meta(meta, frozen_raw)
    hist = pd.read_parquet(HISTORY)
    ensure(list(hist.columns)==BASE_COLS, "historical schema changed")
    older = prepare_longshot(prepare_history(hist))
    train, valid, test25 = split_longshot(older)
    choice, grid = choose_offset(valid)
    offset = choice["logitIntercept"]
    provisional, rounds = fit(train, valid)
    ensure(rounds == meta["bestIterationsChosenWithout2025"],
           "candidate training tree count changed")
    _, base25 = training_targets(test25)
    pred25 = infer_without_results(test25, base25, provisional,
                                   meta["selectedResidualMultiplier"])
    result25 = score_period(test25, pred25, offset, "2025-trained-through-2023")
    recent = prepare_longshot(prepare_2026(hist))
    ensure(pd.to_datetime(recent["日付"]).dt.year.eq(2026).all(),
           "2026 evaluation membership changed")
    frozen = lgb.Booster(model_file=str(MODEL_PATH))
    ensure(frozen.feature_name()==list(FEATURES),
           "2026 frozen model features changed")
    _, base26 = training_targets(recent)
    pred26 = infer_without_results(recent, base26, frozen,
                                   meta["selectedResidualMultiplier"])
    result26 = score_period(recent, pred26, offset, "2026-trained-through-2025")
    report = {
        "schemaVersion":1, "version":VERSION,
        "generatedAtJST": datetime.now(JST).isoformat(),
        "status":"retrospective_calibration_ablation_only",
        "researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,"autoBetEnabled":False,
        "longshotModelVersion":meta["version"],
        "longshotModelSha256":meta["modelSha256"],
        "sourceHistorySha256":hashlib.sha256(HISTORY.read_bytes()).hexdigest(),
        "calibrationSelectedIn":"2024-only",
        "calibrationSelection":choice, "calibrationGrid2024":grid,
        "2025":result25, "2026":result26,
        "limitations":[
            "Only final historical market odds; never compare this with real T-10 odds.",
            "Market-only logit-intercept selected on 2024 exclusively; 2025/26 outcomes never determine it.",
            "The specialized longshot model already selected early stopping and strength using 2024; 2025/26 are not pristine project-wide.",
            "An intercept alone cannot change the ranking among horses in the same race.",
            "Any candidate improvement over calibration is descriptive, not proof of a causal or profitable trading edge.",
            "Only frozen future formal T-10 races can establish genuine deployment quality.",
            "No live model changes, feature selection, subgroup selection, tickets, or auto-betting.",
        ],
    }
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2,
                                 allow_nan=False)+"\n",encoding="utf-8")
    lines = [
        "# 人気薄の予測改善：本当に過去成績が役立つのか", "",
        "2024年だけで校正した市場確率と、固定の人気薄AIを2025・2026年で比較。",
        f"市場確率の単純補正値：logit切片 {offset:+.2f}", "",
        "| 年 | 市場Brier | 市場だけを補正 | 人気薄専用AI | AI−単純補正 | レース単位95%CI |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for year, result in (("2025",result25),("2026",result26)):
        b=result["horsePooledBrier"]
        lines.append(
            f"| {year} | {b['marketRaw']:.6f} | "
            f"{b['marketIntercept']:.6f} | "
            f"{b['longshotResidualAI']:.6f} | "
            f"{result['aiMinusSimpleMarketBrier']:+.6f} | "
            f"{result['pairedRaceBootstrap95'].get('versusSimple')} |"
        )
    lines += ["",
              "差が負ならAIが単純補正より正確。信頼区間はレース平均の誤差差に対応。", "",
              "## 各レースで1頭の人気薄を選ぶ3着内命中", ""]
    for year,result in (("2025",result25),("2026",result26)):
        k=result["rankingAmongLongshots"]
        lines.append(
            f"- {year}: 市場の1頭選択 {result['topRankedLongshotPodiumHits']['marketAndSimple']}/"
            f"{result['races']}R、AI {result['topRankedLongshotPodiumHits']['longshotResidualAI']}/"
            f"{result['races']}R。AIのみ成功{k['candidateOnlyHit']}R、"
            f"市場のみ成功{k['marketOnlyHit']}R、選択馬が異なる{k['changedTopHorse']}R。"
        )
    lines += ["", "最終オッズを使用した過去検証。発走前T-10の実測とは別。",
              "馬券の回収率や将来の優位性を保証せず、正式AIは変更していません。", ""]
    REPORT.with_suffix(".md").write_text("\n".join(lines),encoding="utf-8")
    print("LONGSHOT_MARKET_CALIBRATION_ABLATION_OK",
          json.dumps({"offset":offset,
                      "2025":{k:result25[k] for k in (
                          "horsePooledBrier", "rankingAmongLongshots")},
                      "2026":{k:result26[k] for k in (
                          "horsePooledBrier", "rankingAmongLongshots")}},
                     ensure_ascii=False,allow_nan=False))


if __name__=="__main__":
    main()
