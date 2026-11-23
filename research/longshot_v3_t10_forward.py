#!/usr/bin/env python3
"""Research-only longshot Top3 challenger: frozen T-10 V2 anchored to T-10 market.

No model refit on 2026 outcomes, no betting, no production changes.
The 75/25 market/V2 recipe is a *candidate*, not a trained/approved model.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

CANDIDATES = ("market", "formalAI", "specialistV2", "market75_v2_25", "market50_v2_50")
RESEARCH_START = "2026-11-23"
MODEL_VERSION = "longshot-top3-place-history-v2-20261123"
MIN_RACES_FOR_PROMOTION = 100


def fingerprint(value):
    value = json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def eligible(record):
    if not record.get("popularity") or record.get("odds") is None:
        return False
    return record["popularity"] >= 6 and record["odds"] >= 10


def podium(audited, field):
    result = audited.get("actualFinish", {})
    numbers = {horse["number"] for horse in field}
    places = {}
    for number in numbers:
        rank = result.get(str(number))
        if type(rank) is int and 1 <= rank <= 3:
            places.setdefault(rank, []).append(number)
    if any(len(places.get(rank, [])) != 1 for rank in (1, 2, 3)):
        return None
    return {places[1][0], places[2][0], places[3][0]}


def load_samples(root):
    audit = json.loads((root / "AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    aligned = audit.get("aligned", {})
    if (audit.get("projectionState") != "verified" or aligned.get("countsAreCurrent") is not True
            or audit.get("cohortLockRecoveryRequired") is True):
        raise ValueError("Strict verified alignment required; refusing unverified results")
    frozen = json.loads((root / "research/model_candidates/longshot_top3_v2/research.json")
                        .read_text(encoding="utf-8"))
    if frozen.get("candidatePromotionApproved") is not False or frozen.get("researchOnly") is not True:
        raise ValueError("Longshot V2 model is not designated research-only")
    expected_hash = frozen["modelHashes"]["model_through2025.txt"]
    counted = defaultdict(int)
    groups = []
    for r in audit["races"]:
        if r["raceDate"] < RESEARCH_START or not r.get("inputFingerprint"):
            continue
        counted["eligible_formal"] += 1
        directory = root / "reports" / r["raceDate"].replace("-", "")
        v2_path = directory / "longshot_top3_v2" / (r["raceID"] + ".json")
        original_path = directory / "portfolio_live10" / (r["raceID"] + ".json")
        if not v2_path.is_file() or not original_path.is_file():
            counted["missing_first_frozen"] += 1
            continue
        record = json.loads(v2_path.read_text(encoding="utf-8"))
        bundle = json.loads(original_path.read_text(encoding="utf-8"))
        pred = bundle["prediction"]
        if (bundle.get("fingerprint") != r["inputFingerprint"]
                or record.get("sourcePredictionSha256") != fingerprint(pred)
                or record.get("recordFingerprint") != fingerprint({k: v for k, v in record.items()
                                                                      if k != "recordFingerprint"})
                or record.get("captureQuality") != "formal_10min"
                or record.get("predictionTiming") != "live_10min"
                or record.get("capturedAtJST") != pred.get("capturedAtJST")
                or pred.get("captureQuality") != "formal_10min"
                or record.get("researchOnly") is not True
                or record.get("model", {}).get("sha256") != expected_hash
                or record.get("model", {}).get("version") != MODEL_VERSION
                or record.get("model", {}).get("trainedThrough") != "2025-12-31"):
            counted["bad_provenance"] += 1
            continue
        if r.get("resultState") != "matched":
            counted["not_matched"] += 1
            continue
        horses = pred["horses"]
        top3 = podium(r, horses)
        if top3 is None:
            counted["ambiguous_podium"] += 1
            continue
        expected = [h for h in horses if eligible(h)]
        scores = record["horses"]
        by_number = {row["number"]: row for row in scores}
        if len(by_number) != len(scores) or set(by_number) != {h["number"] for h in expected}:
            counted["candidate_mismatch"] += 1
            continue
        if not expected:
            counted["no_longshots"] += 1
            continue
        rows = []
        for horse in expected:
            z = by_number[horse["number"]]
            if (z["popularity"] != horse["popularity"] or z["winOdds"] != horse["odds"]
                    or z["name"] != horse["name"]):
                rows = []
                break
            market = horse["marketTop3Probability"] / 100
            ai = horse["aiTop3Probability"] / 100
            specialist = z["top3ProbabilityPct"] / 100
            if any(not math.isfinite(p) or p < 0 or p > 1 for p in (market, ai, specialist)):
                rows = []
                break
            rows.append({
                "horse": horse["number"], "odds": horse["odds"], "popularity": horse["popularity"],
                "actual": int(horse["number"] in top3),
                "market": market, "formalAI": ai, "specialistV2": specialist,
                # Strictly pre-specified blends; no 2026 fit/threshold selection.
                "market75_v2_25": .75 * market + .25 * specialist,
                "market50_v2_50": .50 * market + .50 * specialist,
            })
        if not rows:
            counted["invalid_candidate_rows"] += 1
            continue
        groups.append({"raceDate": r["raceDate"], "raceID": r["raceID"], "rows": rows})
        counted["matched_scored"] += 1
        counted["longshot_horses"] += len(rows)
        counted["longshot_top3"] += sum(row["actual"] for row in rows)
    return groups, dict(counted)


def summarize(groups):
    rows = [row for g in groups for row in g["rows"]]
    stats = {}
    eps = 1e-10
    for method in CANDIDATES:
        per_race_brier = []
        per_race_logloss = []
        winners = 0
        selected_horses = []
        for group in groups:
            horse_rows = group["rows"]
            brier = sum((r[method] - r["actual"]) ** 2 for r in horse_rows) / len(horse_rows)
            ll = -sum(r["actual"] * math.log(max(eps, r[method]))
                      + (1-r["actual"]) * math.log(max(eps, 1-r[method]))
                      for r in horse_rows) / len(horse_rows)
            per_race_brier.append(brier)
            per_race_logloss.append(ll)
            top = max(horse_rows, key=lambda r: (r[method], -r["horse"]))
            winners += top["actual"]
            selected_horses.append(top["horse"])
        stats[method] = {
            "races": len(groups), "longshots": len(rows),
            "top_pick_hits": winners, "top_pick_hit_rate": winners/len(groups) if groups else None,
            "brier_equal_race": sum(per_race_brier)/len(groups) if groups else None,
            "logloss_equal_race": sum(per_race_logloss)/len(groups) if groups else None,
            "expected_hits": sum(r[method] for r in rows),
            "observed_hits": sum(r["actual"] for r in rows),
            "mean_probability": sum(r[method] for r in rows)/len(rows) if rows else None,
            "race_brier": per_race_brier,
            "selected_numbers": selected_horses,
        }
    for method, base in [("specialistV2", "market"), ("market75_v2_25", "market"),
                         ("market75_v2_25", "formalAI"), ("market50_v2_50", "market")]:
        if not groups: break
        delta = [a-b for a, b in zip(stats[method]["race_brier"], stats[base]["race_brier"])]
        stats[method].setdefault("paired", {})["vs_"+base] = {
            "mean_brier_delta": sum(delta)/len(delta),
            "better_races": sum(d < -1e-12 for d in delta),
            "worse_races": sum(d > 1e-12 for d in delta),
        }
    for row in stats.values():
        row.pop("race_brier", None)
        row.pop("selected_numbers", None)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--output", type=Path, default=Path("research/reports/longshot_v3_forward_result.json"))
    args = ap.parse_args()
    groups, counts = load_samples(args.root)
    stats = summarize(groups)
    report = {
        "status": "research_only_insufficient_prospective_evidence"
                  if len(groups) < MIN_RACES_FOR_PROMOTION else "research_only_review_required",
        "model": "longshot-v3-fixed-market-anchor-candidate",
        "source": "immutable-first-frozen-T10-snapshots",
        "first_frozen_activation_date": RESEARCH_START,
        "training": "specialist V2 frozen through 2025; V3 mixes probabilities without 2026 fitting",
        "scored_days": len({g["raceDate"] for g in groups}),
        "counts": counts,
        "stats": stats,
        "rules": {
            "v3_25": "0.75*T10_marketTop3 + 0.25*frozen_specialistV2",
            "v3_50": "0.50*T10_marketTop3 + 0.50*frozen_specialistV2",
        },
        "limitations": [
            "Single-day early data cannot establish generalization.",
            "Closing-odds retrospective historical results cannot be equated with T10.",
            "T10 cohort has been reviewed before; it is not a pristine holdout.",
            "Brier/logloss and pick accuracy do not prove bet profitability.",
            "All mixes are exploratory; no candidate promotion or automatic betting.",
        ],
        "approved_for_production": False, "auto_bet_enabled": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+"\n",
                           encoding="utf-8")
    print("FROZEN_LONGSHOT_V3_RESEARCH", json.dumps({
        "scored_days": report["scored_days"], "counts": counts,
        "results": {k: {x: z[x] for x in ("races", "longshots", "top_pick_hits",
                                       "brier_equal_race", "logloss_equal_race")}
                    for k, z in stats.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
