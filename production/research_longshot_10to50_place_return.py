#!/usr/bin/env python3
"""Research-only 10-50x longshot validation with archived actual place payouts.

2025 uses the <=2023 provisional longshot Top3 candidate; 2026 uses the
published <=2025 refit. Source is historical FINAL odds/popularity, NOT
first-frozen T-10. The 10-50x focus and the >=1.2 AI-to-market uplift group
have both been explored in this research project. All results are
exploratory/post-hoc, never proof of real-time profit.

Place payouts are independent result-page extracts (yen per 100-yen bet),
available for only a *selected 2026 date subset*; 2025 payouts unavailable.
No payout is inferred from win odds. No automatic tickets/model promotion.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import lightgbm as lgb
import numpy as np
import pandas as pd

from train_longshot_top3 import (
    FEATURES, HISTORY, JST, METADATA, MODEL_PATH, ROOT,
    fit, infer_without_results, prepare_longshot, split_longshot, training_targets,
)
from train_no_current_odds_challenger import ensure, prepare_history
from evaluate_no_current_odds_2026_history import prepare_2026
from train_top2_top3_models import BASE_COLS
from research_longshot_factor_robustness import verify_meta as verify_frozen

VERSION = "longshot-10to50-place-return-research-v1-20261123"
REPORT = ROOT / "research/Longshot10to50PlaceReturnReview.json"
STAKED_YEN_PER_RACE = 100
ODDS_MIN = 10.0
ODDS_MAX_EXCLUSIVE = 50.0
UPLIFT_RATIO = 1.2
RNG_SEED = 20261123


def target_band(frame, market, candidate):
    """Fixed exploratory subset; all 36 pre-race features remain from model."""
    y, base = training_targets(frame)
    ensure(len(y) == len(market) == len(candidate) and
           np.allclose(base, market, atol=1e-12),
           "candidate / historical cohort mismatch")
    cond = (frame["人気"].to_numpy(float) >= 6) & (
        frame["単勝オッズ"].to_numpy(float) >= ODDS_MIN) & (
        frame["単勝オッズ"].to_numpy(float) < ODDS_MAX_EXCLUSIVE)
    chosen = frame.loc[cond].copy().reset_index(drop=True)
    ensure(len(chosen) > 0, "no 10-50 longshot entries")
    out = pd.DataFrame({
        "raceID": chosen["レースID"].astype(str).to_numpy(),
        "raceDate": pd.to_datetime(chosen["日付"]).dt.strftime("%Y-%m-%d").to_numpy(),
        "horseNumber": chosen["馬番"].astype(int).to_numpy(),
        "finish": chosen["着順"].astype(int).to_numpy(),
        "winOdds": chosen["単勝オッズ"].astype(float).to_numpy(),
        "market": np.asarray(market)[cond],
        "candidate": np.asarray(candidate)[cond],
        "top3": np.asarray(y)[cond],
    })
    ensure(out["horseNumber"].ge(1).all() and
           out["candidate"].between(0,1, inclusive="neither").all() and
           out["market"].between(0,1, inclusive="neither").all(),
           "invalid pre-outcome candidate record")
    out["ratio"] = out["candidate"] / out["market"]
    out["uplift20Pct"] = out["ratio"] >= UPLIFT_RATIO
    return out


def segment_quality(frame):
    ensure(len(frame)>0, "empty diagnostic segment")
    y = frame["top3"].to_numpy(dtype=float)
    p = frame["candidate"].to_numpy(dtype=float)
    m = frame["market"].to_numpy(dtype=float)
    return {
        "horseRows":int(len(frame)),
        "races":int(frame["raceID"].nunique()),
        "top3Count":int(y.sum()),
        "observedTop3Rate":float(y.mean()),
        "marketExpectedTop3Rate":float(m.mean()),
        "candidateExpectedTop3Rate":float(p.mean()),
        "marketBrier":float(np.mean((m-y)**2)),
        "candidateBrier":float(np.mean((p-y)**2)),
        "candidateMinusMarketBrier":float(np.mean((p-y)**2-(m-y)**2)),
    }


def inspect_payout(path: Path, *, expected_race_id: str):
    """Validate archived race/payout identity. Never infer missing payouts."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    source = f"https://race.netkeiba.com/race/result.html?race_id={expected_race_id}"
    ensure(doc.get("sourceURL") == source and
           all(re.fullmatch("[a-f0-9]{64}", doc.get(k, "")) for k in (
               "sourceFingerprint","recordFingerprint","fingerprint")),
           "archived result-page payout identity invalid")
    stamp = datetime.fromisoformat(doc["retrievedAtJST"])
    ensure(stamp.utcoffset() is not None and
           path.parts[-4] == stamp.strftime("%Y%m%d"),
           "payout collection date mismatch")
    payments = doc.get("payouts")
    ensure(isinstance(payments,dict) and
           all(re.fullmatch(r"(?:0[1-9]|1[0-8])",key) and
               type(value) is int and value >= 100
               for key,value in payments.items()) and
           len(payments) >= 1,
           "payout per ¥100 must be backed by actual archived result")
    return {int(key):amount for key,amount in payments.items()}


def payout_inventory(root: Path):
    paths = sorted((root/"reports").glob("*/all_bet_payouts_v1/place/*.json"))
    payoff = {}
    for p in paths:
        rid = p.stem
        ensure(re.fullmatch(r"\d{12}",rid) is not None,
               "invalid stored race ID")
        ensure(rid not in payoff, "conflicting duplicate race payout extracts")
        payoff[rid] = {
            "place":inspect_payout(p,expected_race_id=rid),
            "path":str(p.relative_to(root)),
        }
    return payoff


def eligible_race_payouts(complete_2026, longshots, archive):
    """Use only independently recorded payouts agreeing with entire finish order."""
    full_by_race = complete_2026.groupby("レースID",sort=False)
    band_by_race = longshots.groupby("raceID",sort=False)
    verified = {}
    problems = {"notInHistoricalEvaluation":0,
                "noQualifying10to50Longshot":0,
                "payoutAndFinishDisagree":0,
                "incompleteFinishOrEntrants":0}
    for rid,payoff in archive.items():
        if rid not in full_by_race.indices:
            problems["notInHistoricalEvaluation"] += 1
            continue
        all_horses=full_by_race.get_group(rid)
        n=int(all_horses["頭数"].iloc[0])
        positions=pd.to_numeric(all_horses["着順"],errors="coerce")
        if (len(all_horses)!=n or n<8 or
                len(all_horses["馬番"].unique())!=n or
                positions.isna().any()):
            problems["incompleteFinishOrEntrants"] += 1
            continue
        # Includes ties (may be 4+ qualifying place payouts). Since only
        # field >=8 is allowed there must be at least 3 settled places.
        paid=set(payoff["place"])
        finished={int(num) for num,rank in zip(all_horses["馬番"],positions)
                  if rank<=3}
        if paid!=finished:
            problems["payoutAndFinishDisagree"] += 1
            continue
        if rid not in band_by_race.indices:
            problems["noQualifying10to50Longshot"] += 1
            continue
        verified[rid]={"rows":band_by_race.get_group(rid),
                       "payout":payoff["place"],
                       "path":payoff["path"]}
    ensure(len(verified)+sum(problems.values())==len(archive),
           "payout inventory/validation count mismatch")
    return verified,problems


def pick_one_per_race(verified):
    """Same stake on each available race; no use of result to select a horse."""
    entries=[]
    for rid,d in sorted(verified.items()):
        rows=d["rows"]
        ensure(len(rows)>0 and rows["horseNumber"].is_unique,
               "bad candidate roster for payout comparison")
        base=rows.sort_values(["market","horseNumber"],
                               ascending=[False,True]).iloc[0]
        ai=rows.sort_values(["candidate","horseNumber"],
                             ascending=[False,True]).iloc[0]
        # The ratio strategy is diagnostic only and was noticed in prior
        # analyses; no use of winners or payouts to choose the threshold.
        uplift=rows.loc[rows["uplift20Pct"]]
        high=(uplift.sort_values(["ratio","horseNumber"],
                                 ascending=[False,True]).iloc[0]
              if not uplift.empty else None)
        def bet(row):
            if row is None:return None
            number=int(row["horseNumber"])
            return {
                "number":number,
                "hit":number in d["payout"],
                "returnedYenPer100":int(d["payout"].get(number,0)),
                "marketTop3":float(row["market"]),
                "candidateTop3":float(row["candidate"]),
                "historicalWinOdds":float(row["winOdds"]),
            }
        entries.append({
            "raceID":rid,"sourcePath":d["path"],
            "marketPick":bet(base),"aiPick":bet(ai),"uplift20PctPick":bet(high),
        })
    return entries


def gross_return_summary(rows, kind):
    eligible=[r[kind] for r in rows if r[kind] is not None]
    if not eligible:return None
    payoff=np.asarray([r["returnedYenPer100"] for r in eligible],dtype=float)
    sample=np.random.default_rng(RNG_SEED).integers(
        0,len(payoff),size=(1000,len(payoff)))
    boot=np.mean(payoff[sample],axis=1) / STAKED_YEN_PER_RACE
    return {
        "bets":len(eligible),"grossStakeYen":len(eligible)*STAKED_YEN_PER_RACE,
        "grossPayoutYen":int(payoff.sum()),
        "netYen":int(payoff.sum()-len(eligible)*STAKED_YEN_PER_RACE),
        "grossReturnPct":float(payoff.mean()/STAKED_YEN_PER_RACE*100),
        "top3Hits":int((payoff>0).sum()),
        "top3HitRate":float(np.mean(payoff>0)),
        "returnPctRaceBootstrap95":[
            float(np.quantile(boot,.025)*100),
            float(np.quantile(boot,.975)*100),
        ],
        "largestSinglePlacePayoutYenPer100":int(payoff.max()),
    }


def paired_return_comparison(rows):
    if not rows:return None
    payout_ai=np.asarray([r["aiPick"]["returnedYenPer100"] for r in rows])
    payout_mkt=np.asarray([r["marketPick"]["returnedYenPer100"] for r in rows])
    delta=(payout_ai-payout_mkt).astype(float)
    sample=np.random.default_rng(RNG_SEED).integers(
        0,len(delta),size=(1000,len(delta)))
    boot=delta[sample].mean(axis=1)
    return {
        "sameRaces":len(rows),
        "sameHorseSelections":int(sum(
            r["aiPick"]["number"]==r["marketPick"]["number"] for r in rows)),
        "meanExtraYenPer100Bet":float(delta.mean()),
        "aiMinusMarketGrossReturnPct":float(delta.mean()),
        "pairedRaceBootstrap95ForExtraReturnPct":[
            float(np.quantile(boot,.025)),
            float(np.quantile(boot,.975)),
        ],
        "candidateOnlyPlaceHits":int(sum(
            r["aiPick"]["hit"] and not r["marketPick"]["hit"] for r in rows)),
        "marketOnlyPlaceHits":int(sum(
            r["marketPick"]["hit"] and not r["aiPick"]["hit"] for r in rows)),
    }


def main():
    ensure(HISTORY.is_file() and METADATA.is_file() and MODEL_PATH.is_file(),
           "published model and historical archive required")
    model_bytes=MODEL_PATH.read_bytes()
    meta=json.loads(METADATA.read_text(encoding="utf-8"))
    verify_frozen(meta,model_bytes)
    hist=pd.read_parquet(HISTORY)
    ensure(list(hist.columns)==BASE_COLS, "historical source schema changed")
    train,val,test25=split_longshot(prepare_longshot(prepare_history(hist)))
    provisional,rounds=fit(train,val)
    ensure(rounds==meta["bestIterationsChosenWithout2025"],
           "2024 validation tree count has changed")
    y25,m25=training_targets(test25)
    p25=infer_without_results(test25,m25,provisional,
                              meta["selectedResidualMultiplier"])
    cohort25=target_band(test25,m25,p25)

    full_2026=prepare_2026(hist)
    recent=prepare_longshot(full_2026)
    ensure(pd.to_datetime(recent["日付"]).dt.year.eq(2026).all(),
           "2026 historical eligibility boundary violated")
    model=lgb.Booster(model_file=str(MODEL_PATH))
    ensure(model.feature_name()==list(FEATURES),"frozen model feature mismatch")
    y26,m26=training_targets(recent)
    p26=infer_without_results(recent,m26,model,
                              meta["selectedResidualMultiplier"])
    cohort26=target_band(recent,m26,p26)
    quality={}
    for year,frame in (("2025",cohort25),("2026",cohort26)):
        quality[year]={
            "entire10to50":segment_quality(frame),
            "aiAtLeast20PctAboveMarket":segment_quality(
                frame.loc[frame["uplift20Pct"]]),
            "aiLessThan20PctAboveMarket":segment_quality(
                frame.loc[~frame["uplift20Pct"]]),
        }
    archive=payout_inventory(ROOT)
    verified,miss=eligible_race_payouts(full_2026,cohort26,archive)
    entries=pick_one_per_race(verified)
    summaries={kind:gross_return_summary(entries,kind)
               for kind in ("marketPick","aiPick","uplift20PctPick")}
    comparisons=paired_return_comparison(entries)
    result={
        "schemaVersion":1,"version":VERSION,
        "generatedAtJST":datetime.now(JST).isoformat(),
        "status":"retrospective_exploratory_not_bettable",
        "researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,"autoBetEnabled":False,
        "modelSha256":meta["modelSha256"],
        "sourceHistorySha256":hashlib.sha256(HISTORY.read_bytes()).hexdigest(),
        "definition":{
            "popularityMinimum":6,
            "winOddsFrom":ODDS_MIN,
            "winOddsStrictlyBelow":ODDS_MAX_EXCLUSIVE,
            "upliftRatioAtLeast":UPLIFT_RATIO,
            "selection": "observed 2026 result-independent, but retrospective FINAL odds",
            "placeUnitStakeYen":STAKED_YEN_PER_RACE,
        },
        "cohortQuality2025And2026":quality,
        "2026PlacePayoutEvidence":{
            "archivedRacePayoutFiles":len(archive),
            "eligibleCompleteMatching10to50Races":len(verified),
            "excludedCounts":miss,
            "subsetDateBoundariesNotFullSeason":True,
            "payoutSource": "archived netkeiba race/result page place payouts per ¥100",
            "noInferredOrSynthesizedPayouts":True,
            "grossReturnSimulationByMethod":summaries,
            "sameRaceAiVsMarketPlaceReturn":comparisons,
        },
        "limitations":[
            "Selection uses retrospective FINAL win odds and popularity; not a reproducible first-frozen T-10 bet and not a realized trade.",
            "10-50 odds and 1.2x uplift cohorts arose from prior research, so analyses here are exploratory and potentially selected on 2025/26 outcomes.",
            "Payout evidence covers only archived late-2026 subset of race result extracts. No 2025 payouts; the sample is not the full season.",
            "Only races with validated independent place payouts and matching complete 2026 result rows are scored. Missing payouts never treated as zero or fabricated.",
            "All place return examples are ¥100 fixed stake per selected qualifying race, paid using actual archived place winnings, with no taxes, liquidity or price drift modeled.",
            "A large historical positive return does not establish future profitability; race-level bootstrap cannot correct data-selection bias.",
            "Model 2026 was frozen through 2025; models and current recommendation/bet policy are unmodified.",
            "No automatic betting, bankroll suggestions, or promotion of experimental criteria.",
        ],
    }
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2,
                                 allow_nan=False)+"\n",encoding="utf-8")
    lines=[
        "# 人気薄10〜50倍の好走予測・複勝収支：過去検証", "",
        "研究専用。過去の最終オッズに基づく選択で、T-10の実戦予測ではありません。",
        f"複勝払戻アーカイブ{len(archive)}レース分から、整合した対象{len(verified)}レースを採用。",
        "", "## 3着以内の予測と確率校正（対象：6番人気以下・単勝10〜50倍未満）", "",
        "| 年 | 区分 | 頭数 | 実際3着内 | 市場予測 | AI予測 | AI−市場Brier |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for year in ("2025","2026"):
        for name,v in quality[year].items():
            lines.append(
                f"| {year} | {name} | {v['horseRows']} | "
                f"{v['observedTop3Rate']:.2%} | {v['marketExpectedTop3Rate']:.2%} | "
                f"{v['candidateExpectedTop3Rate']:.2%} | {v['candidateMinusMarketBrier']:+.6f} |"
            )
    lines+=["", "## 保存済みの実際の複勝払戻を使った反実仮想の100円1点買い", "",
            "| 選び方 | 予測上の購入回数 | 的中 | 投資 | 払戻 | 回収率 | 95%ブートストラップ |",
            "|---|---:|---:|---:|---:|---:|---|"]
    for method,v in summaries.items():
        if v is not None:
            lines.append(
                f"| {method} | {v['bets']} | {v['top3Hits']} | ¥{v['grossStakeYen']:,} | "
                f"¥{v['grossPayoutYen']:,} | {v['grossReturnPct']:.1f}% | "
                f"{v['returnPctRaceBootstrap95']} |")
    if comparisons:
        lines+=["",f"同一レースのAI−市場選択の払戻差："
                f"{comparisons['aiMinusMarketGrossReturnPct']:+.1f}ポイント "
                f"（95% {comparisons['pairedRaceBootstrap95ForExtraReturnPct']}）。",
                f"AIのみ的中{comparisons['candidateOnlyPlaceHits']}レース、"
                f"市場のみ的中{comparisons['marketOnlyPlaceHits']}レース。"]
    lines+=["", "## 収集対象外・除外数", "",
            *(f"- {key}: {val}R" for key,val in miss.items()), "",
            "本集計は後から確定した最終人気・オッズで候補を抽出した履歴検証です。",
            "将来の利益、発走10分前に同じ馬を買えること、因果関係を証明しません。",
            "正式モデルや馬券推奨は変更していません。", ""]
    REPORT.with_suffix(".md").write_text("\n".join(lines),encoding="utf-8")
    print("LONGSHOT_10TO50_PLACE_RETURN_RESEARCH_OK",json.dumps({
        "2025":quality["2025"]["entire10to50"],
        "2026":quality["2026"]["entire10to50"],
        "payoutRecords":len(archive),"payoutEligible":len(verified),
        "grossReturn":{k:(v or {}).get("grossReturnPct") for k,v in summaries.items()},
        "paired":comparisons,
    },ensure_ascii=False,allow_nan=False))


if __name__=="__main__":
    main()
