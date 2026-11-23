#!/usr/bin/env python3
"""V2 longshot T-10 vs closing-odds drift, with 44 other inputs held fixed.

Research-only diagnostic. Closing odds are POST-T10 hindsight and MUST NOT be
fed to live predictions, simulated pre-race selections or profit calculations.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

BASELINE = "t10_original"
SCENARIOS = ("t10_original", "closing_baseline_only", "closing_market_context")
MOVEMENT = ("shorten_20pct", "stable", "lengthen_25pct")
MIN_RACES = 30


def sha_of_dict(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def ensure(ok, reason):
    if not ok:
        raise ValueError("ODDS_DRIFT_DIAGNOSTIC_BLOCKED: " + reason)


def valid_rank(value):
    return type(value) is int and 1 <= value <= 3


def observed_podium(audited, entrant_numbers):
    finish = audited.get("actualFinish", {})
    leaders = [n for n in entrant_numbers if valid_rank(finish.get(str(n)))]
    ranks = [finish[str(n)] for n in leaders]
    if sorted(ranks) != [1, 2, 3]:
        return None
    return set(leaders)


def movement_bin(t10, closing):
    ratio = closing / t10
    if ratio < .8:
        return "shorten_20pct"
    if ratio <= 1.25:
        return "stable"
    return "lengthen_25pct"


def logit(prob):
    p = np.clip(np.asarray(prob, dtype=float), 1e-9, 1-1e-9)
    return np.log(p / (1-p))


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -35, 35)))


def score(model, x, baseline, cols):
    ensure(np.isfinite(x).all().all(), "nonfinite model features")
    ensure((np.asarray(baseline) > 0).all() and (np.asarray(baseline) < 1).all(), "invalid market Top3")
    raw = model.predict(x.astype("float32"), raw_score=True, validate_features=True, num_threads=2)
    return sigmoid(logit(baseline) + np.asarray(raw, dtype=float))


def metrics(rows, key):
    if not rows:
        return {"horses": 0, "races": 0, "brier_equal_race": None}
    by_race = defaultdict(list)
    for x in rows:
        by_race[x["raceID"]].append(x)
    briers, losses = [], []
    for race in by_race.values():
        briers.append(sum((x[key]-x["hit"])**2 for x in race)/len(race))
        losses.append(-sum(x["hit"]*math.log(max(1e-10, x[key])) +
                           (1-x["hit"])*math.log(max(1e-10, 1-x[key]))
                           for x in race)/len(race))
    tops = [max(race, key=lambda x: (x[key], -x["number"]))["hit"]
            for race in by_race.values()]
    return {"horses": len(rows), "races": len(by_race),
            "brier_equal_race": round(sum(briers)/len(briers), 9),
            "logloss_equal_race": round(sum(losses)/len(losses), 9),
            "top_pick_top3": sum(tops),
            "expected_top3": round(sum(x[key] for x in rows), 4),
            "observed_top3": sum(x["hit"] for x in rows)}


def source_ages(details):
    """Wide odds timestamps are reported separately; not the win-odds timestamp."""
    try:
        a = datetime.strptime(details["officialDatetime"], "%Y-%m-%d %H:%M:%S")
        b = datetime.strptime(details["capturedAtJST"], "%Y-%m-%d %H:%M:%S")
        diff = (b-a).total_seconds()/60
        return diff if diff >= 0 else None
    except (TypeError, ValueError, KeyError):
        return None


def run(root):
    sys.path.insert(0, str(root / "production"))
    from topk_runtime import market_top2_top3

    audit = json.loads((root/"AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    ensure(audit.get("projectionState") == "verified" and audit.get("aligned", {}).get("countsAreCurrent") is True
           and audit.get("cohortLockRecoveryRequired") is not True, "unverified strict cohort")

    registry = json.loads((root/"research/model_candidates/longshot_top3_v2/research.json")
                          .read_text(encoding="utf-8"))
    ensure(registry.get("researchOnly") is True and registry.get("candidatePromotionApproved") is False,
           "V2 policy changed")
    model_file = root/"research/model_candidates/longshot_top3_v2/model_through2025.txt"
    ensure(hashlib.sha256(model_file.read_bytes()).hexdigest() ==
           registry["modelHashes"]["model_through2025.txt"], "V2 model hash changed")
    model = lgb.Booster(model_file=str(model_file))
    cols = registry["selectedFeatures"]
    ensure(model.feature_name() == cols and len(cols) == 47, "V2 feature order changed")
    extra = ("marketTop3LogitV2", "logWinOddsV2", "popularityFractionV2")
    ensure(cols[-3:] == list(extra), "market context column names moved")

    result_records = json.loads((root/"reports/20261123/ResultData.json").read_text(encoding="utf-8"))
    by_id = {x["raceID"]: x for x in result_records}
    ensure(len(by_id) == len(result_records), "duplicate official result records")
    rows = []
    quality = Counter()
    shifts = []
    age_wide = []
    per_race = []
    for race in audit["races"]:
        if race["raceDate"] != "2026-11-23" or not race.get("inputFingerprint"):
            continue
        quality["audit_formal"] += 1
        rid = race["raceID"]
        folder = root/"reports/20261123"
        portfolio_file = folder/"portfolio_live10"/(rid+".json")
        v2_file = folder/"longshot_top3_v2"/(rid+".json")
        if not portfolio_file.exists() or not v2_file.exists() or rid not in by_id:
            quality["missing_linked_files"] += 1
            continue
        bundle = json.loads(portfolio_file.read_text(encoding="utf-8"))
        v2 = json.loads(v2_file.read_text(encoding="utf-8"))
        pred = bundle["prediction"]
        ensure(bundle.get("fingerprint") == race["inputFingerprint"], "formal audit fingerprint "+rid)
        ensure(v2.get("recordFingerprint") == sha_of_dict({k: v for k,v in v2.items()
                                                        if k != "recordFingerprint"}), "V2 record "+rid)
        ensure(v2.get("sourcePredictionSha256") == sha_of_dict(pred) and
               v2.get("capturedAtJST") == pred.get("capturedAtJST"), "original T10 linkage "+rid)
        ensure(v2.get("captureQuality") == "formal_10min" and
               v2.get("predictionTiming") == "live_10min" and
               v2["model"]["sha256"] == registry["modelHashes"]["model_through2025.txt"],
               "T10 eligibility and model SHA "+rid)
        if race.get("resultState") != "matched":
            quality["awaiting_official_result"] += 1
            continue
        entire = pred["horses"]
        numbers = [x["number"] for x in entire]
        finals = {h["number"]: h for h in by_id[rid]["horses"]}
        ensure(len(set(numbers)) == len(numbers) and set(numbers) == set(finals) and
               all(isinstance(finals[n].get("finalOdds"), (float, int)) and
                   math.isfinite(finals[n]["finalOdds"]) and finals[n]["finalOdds"] > 0 and
                   type(finals[n].get("popularity")) is int
                   for n in numbers), "field or closing odds mismatch "+rid)
        podium = observed_podium(race, numbers)
        if podium is None:
            quality["ambiguous_podium"] += 1
            continue

        candidates = [h for h in entire if h["popularity"] >= 6 and h["odds"] >= 10]
        old = v2.get("modelInput")
        ensure(isinstance(old, dict) and
               old.get("horseNumbers") == [h["number"] for h in candidates] and
               old.get("features") == cols and
               v2.get("featureInputSha256") == sha_of_dict(old),
               "frozen feature inputs mismatch "+rid)
        if not candidates:
            quality["no_t10_longshots"] += 1
            continue
        x0 = pd.DataFrame(old["rows"], columns=cols, dtype=float)
        ensure(x0.shape == (len(candidates), len(cols)), "model matrix shape "+rid)
        raw_t10 = np.asarray([h["odds"] for h in entire], dtype=float)
        raw_final = np.asarray([finals[h["number"]]["finalOdds"] for h in entire], dtype=float)
        ensure(np.isfinite(raw_t10).all() and (raw_t10 > 0).all(), "T10 odds invalid "+rid)
        _, top3_t10 = market_top2_top3(1/raw_t10)
        _, top3_final = market_top2_top3(1/raw_final)
        by_number_t10 = {h["number"]: float(v) for h, v in zip(entire, top3_t10)}
        by_number_final = {h["number"]: float(v) for h, v in zip(entire, top3_final)}
        score_map = {x["number"]: x for x in v2["horses"]}
        ensure(set(score_map) == set(h["number"] for h in candidates), "V2 horse population "+rid)
        b0 = np.array([by_number_t10[h["number"]] for h in candidates])
        bf = np.array([by_number_final[h["number"]] for h in candidates])
        # This reconstruction gate is mandatory: scenario runs cannot be
        # compared unless the frozen T10 model is independently reproduced.
        ensure(max(abs(x0["marketTop3LogitV2"].to_numpy() - logit(b0))) < .002,
               "T10 market PL reconstruction mismatch "+rid)
        p0 = score(model, x0, b0, cols)
        saved = np.asarray([score_map[h["number"]]["top3ProbabilityPct"]/100 for h in candidates])
        ensure(max(abs(p0-saved)) < .0002, "frozen V2 inference not reproduced "+rid)
        p_baseline = score(model, x0, bf, cols)
        xf = x0.copy()
        xf["marketTop3LogitV2"] = logit(bf)
        xf["logWinOddsV2"] = np.log([finals[h["number"]]["finalOdds"] for h in candidates])
        xf["popularityFractionV2"] = [finals[h["number"]]["popularity"]/len(entire)
                                     for h in candidates]
        p_market_context = score(model, xf, bf, cols)
        ensure(np.isfinite(p_market_context).all(), "V2 final scenario invalid "+rid)
        age_wide.append(source_ages(bundle["signal"]["wideEvaluation"]))

        after = 0
        lost = 0
        for i, horse in enumerate(candidates):
            n = horse["number"]
            final_h = finals[n]
            t10 = float(horse["odds"])
            close = float(final_h["finalOdds"])
            l = math.log(close/t10)
            final_eligible = final_h["popularity"] >= 6 and close >= 10
            if not final_eligible:
                lost += 1
            rows.append({
                "raceID": rid, "raceDate": race["raceDate"], "number": n,
                "popularity_t10": horse["popularity"],
                "popularity_final": final_h["popularity"],
                "odds_t10": t10, "odds_final": close,
                "move": movement_bin(t10, close),
                "log_odds_change": l,
                "hit": int(n in podium),
                "market_t10": b0[i], "market_final": bf[i],
                "t10_original": p0[i], "closing_baseline_only": p_baseline[i],
                "closing_market_context": p_market_context[i],
                "t10_market_error_sq": (b0[i]-int(n in podium))**2,
                "t10_v2_error_sq": (p0[i]-int(n in podium))**2,
            })
            shifts.append(abs(l))
        for horse in entire:
            n=horse["number"]
            final_h=finals[n]
            if final_h["popularity"] >= 6 and final_h["finalOdds"] >= 10 and not (horse["popularity"]>=6 and horse["odds"]>=10):
                after += 1
        per_race.append({"raceID":rid,"eligible_T10":len(candidates),"eligible_final":sum(
            finals[h["number"]]["popularity"]>=6 and finals[h["number"]]["finalOdds"]>=10 for h in entire),
            "lost_t10_on_final":lost,"gained_final_vs_t10":after})
        quality["scored_races"] += 1

    def movement_stats(group):
        if not group:
            return {}
        return {
            "horses": len(group), "races": len({x["raceID"] for x in group}),
            "hits": sum(x["hit"] for x in group),
            "hit_pct": round(100*sum(x["hit"] for x in group)/len(group),2),
            "mean_t10_v2_prob_pct":round(100*sum(x["t10_original"] for x in group)/len(group),2),
            "mean_t10_market_prob_pct":round(100*sum(x["market_t10"] for x in group)/len(group),2),
            "t10_v2_brier":round(sum(x["t10_v2_error_sq"] for x in group)/len(group),6),
            "mean_abs_odds_change_pct":round(100*sum(abs(x["odds_final"]/x["odds_t10"]-1) for x in group)/len(group),2),
        }

    drift = {group: movement_stats([x for x in rows if x["move"]==group]) for group in MOVEMENT}
    by_scen = {name: metrics(rows, name) for name in SCENARIOS}
    by_scen["market_t10"] = metrics(rows, "market_t10")
    by_scen["market_final"] = metrics(rows, "market_final")
    m = {
        "status": "research_only_one_day_closing_odds_hindsight",
        "analysisType": "same_horse_T10_vs_official_closing_odds",
        "warning": "Final odds are NOT available at T10. Counterfactual outputs are hindsight diagnostics only.",
        "eligibleDefinition": "T10 popularity>=6 AND T10 win odds>=10; FIXED across all scenarios",
        "sourceAge": "wide odds timestamps, NOT win-odds timestamps",
        "predictionSource": "immutable first-formal T10 specialist V2 features, 47 inputs",
        "frozenVariables": "44 non-market V2 features unchanged; only 3 market features and Top3 baseline changed",
        "metrics": by_scen,
        "driftGroups": drift,
        "quality": dict(quality),
        "recordedOddsAgesWideMinutes": {
            "n": sum(a is not None for a in age_wide),
            "min":min([a for a in age_wide if a is not None],default=None),
            "max":max([a for a in age_wide if a is not None],default=None),
        },
        "cohortChange": {
            "scoredRaces":len(per_race),
            "t10Longshots":len(rows),
            "t10LongshotsNoLongerEligibleAtFinal":sum(x["lost_t10_on_final"] for x in per_race),
            "newFinalLongshotsNotEligibleAtT10":sum(x["gained_final_vs_t10"] for x in per_race),
        },
        "magnitude": {
            "absolute_log_odds_change_mean": sum(shifts)/len(shifts) if shifts else None,
            "changed_odds_20pct_or_more":sum(x["move"]!="stable" for x in rows),
        },
        "notes":[
            "One calendar day and one market cross-section. Large historical evidence is not a comparable T10 benchmark.",
            "Final popularity is never used to change the T10 candidate set.",
            "T10 score must reproduce the frozen V2 within 0.0002 probability, otherwise evaluation aborts.",
            "Attribution is sensitivity to market input; not a proven causal reason for good/bad finishing.",
            "Post-race results and closing prices are diagnostic references, never prospective inputs.",
        ],
        "researchOnly": True, "candidatePromotionApproved":False,"autoBetEnabled":False,
    }
    return m


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,default=Path("."))
    parser.add_argument("--output",type=Path,default=Path("longshot_t10_closing_odds_drift.json"))
    args=parser.parse_args()
    report=run(args.root)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n")
    print("ODDS_DRIFT_COHORT",report["quality"],report["cohortChange"])
    print("ODDS_DRIFT_MOVEMENTS",json.dumps(report["driftGroups"],ensure_ascii=False))
    print("ODDS_DRIFT_SCENARIOS",json.dumps(report["metrics"],ensure_ascii=False))
    print("ODDS_DRIFT_WIDE_AGE_ONLY",report["recordedOddsAgesWideMinutes"])


if __name__=="__main__":
    main()
