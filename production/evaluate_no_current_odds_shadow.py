"""Read-only matched-race evaluation of first-frozen no-current-odds shadows.

No historical replays/backfills: only separate pre-post immutable shadow files
are evaluated. Original formal T-10 snapshots and official settled results
come from the strict current verified aligned audit.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from inspect_frozen_win_probabilities import official_podium, paired_race_uncertainty
from market_shrink_shadow import JST, fingerprint
from no_current_odds_shadow import SHADOW_VERSION
from train_no_current_odds_challenger import REPORT_PATH, VERSION
import formal_portfolio_capture as capture

# The first eligible race date after the candidate and first-T10 publisher were
# deployed. This gate is immutable: historical observations do not gain fake
# 'missing' shadows or simulated candidate results after the fact.
ACTIVATION_DATE = "2026-11-23"

EVENTS = {
    "win": ("aiProbability", "marketProbability", "winPct", 1),
    "top2": ("aiTop2Probability", "marketTop2Probability", "top2Pct", 2),
    "top3": ("aiTop3Probability", "marketTop3Probability", "top3Pct", 3),
}


def ensure(ok, reason):
    if not ok:
        raise ValueError("NO_CURRENT_ODDS_FORWARD_BLOCKED: " + reason)


def evaluate(root: Path, audit: dict, load_bundle=capture.load) -> dict:
    aligned = audit.get("aligned") or {}
    ensure(audit.get("projectionState") == "verified" and
           aligned.get("countsAreCurrent") is True, "verified current audit required")
    ensure(audit.get("cohortLockRecoveryRequired") is not True,
           "cohort identity recovery required")
    ensure(bool(audit.get("generatedAtJST")), "missing audit provenance")
    records = audit.get("races")
    ensure(isinstance(records, list) and len(records) == aligned.get("racesObserved"),
           "aligned race membership mismatch")
    keyed = {(r["raceDate"], r["raceID"]): r for r in records}
    ensure(len(keyed) == len(records), "duplicate race identity")
    excluded = {(r["raceDate"], r["raceID"]) for r in
                (audit.get("quality", {}).get("resultExclusions") or [])}
    # Each aligned row has passed the strict portfolio input/record gate;
    # only records after deployment are expected to have a companion challenger.
    eligible_forward = {
        (row["raceDate"], row["raceID"])
        for row in records
        if row["raceDate"] >= ACTIVATION_DATE
        and row.get("inputFingerprint")
    }

    model_meta = None
    if REPORT_PATH.is_file():
        model_meta = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        ensure(model_meta.get("version") == VERSION and
               model_meta.get("candidatePromotionApproved") is False,
               "candidate model metadata invalid")
    counts = {
        "frozenShadows": 0,
        "pairedAuditedRaces": 0,
        "unmatchedOrNotAudited": 0,
        "excludedResult": 0,
        "missingPodiumEvents": 0,
        "eligibleAlignedCapturesSinceActivation": 0,
        "missingCandidateShadowsSinceActivation": 0,
        "candidateShadowCoveragePct": None,
    }
    grouped = defaultdict(list)
    seen = set()
    for path in sorted((Path(root) / "reports").glob("*/no_current_odds_v1/*.json")):
        v = json.loads(path.read_text(encoding="utf-8"))
        date, rid = v.get("raceDate"), v.get("raceID")
        ensure(bool(date) and bool(rid), "missing shadow identity")
        identity = (date, rid)
        ensure(identity not in seen, "duplicate shadow identity")
        seen.add(identity)
        ensure(path == Path(root) / "reports" / date.replace("-", "") /
               "no_current_odds_v1" / (rid + ".json"), "shadow path mismatch")
        ensure(v.get("version") == SHADOW_VERSION and
               v.get("status") == "prospectively_frozen_not_approved" and
               v.get("researchOnly") is True and
               v.get("candidatePromotionApproved") is False and
               v.get("autoBetEnabled") is False and
               v.get("formalRecommendationsEnabled") is False and
               v.get("formalPredictionAIChanged") is False,
               "invalid frozen research-only policy")
        ensure(v.get("recordFingerprint") == fingerprint({
            k: val for k, val in v.items() if k != "recordFingerprint"
        }), "immutable shadow digest mismatch")
        ensure(model_meta is not None and
               v.get("modelVersion") == model_meta["version"] and
               v.get("modelSha256") == model_meta["modelSha256"],
               "candidate version changed since first capture")
        post = datetime.fromisoformat(date + "T" + v["startTime"]).replace(tzinfo=JST)
        captured = datetime.fromisoformat(v["capturedAtJST"])
        recorded = datetime.fromisoformat(v["recordedAtJST"])
        ensure(captured.utcoffset() == timedelta(hours=9) and
               recorded.utcoffset() == timedelta(hours=9) and
               8 <= (post - captured).total_seconds()/60 <= 12 and
               captured <= recorded <= post - timedelta(minutes=8),
               "shadow not first-frozen in valid T10 window")
        counts["frozenShadows"] += 1
        row = keyed.get(identity)
        if row is None or row.get("resultState") != "matched":
            counts["unmatchedOrNotAudited"] += 1
            continue
        if identity in excluded:
            counts["excludedResult"] += 1
            continue
        original = load_bundle(root, date, rid)
        ensure(original.get("fingerprint") == row.get("inputFingerprint"),
               "formal input fingerprint mismatch")
        race = original["prediction"]
        ensure(race.get("predictionTiming") == "live_10min" and
               race.get("captureQuality") == "formal_10min" and
               v.get("sourcePredictionSha256") == fingerprint(race) and
               v.get("capturedAtJST") == race["capturedAtJST"],
               "shadow does not match first frozen prediction")
        horses = race.get("horses")
        candidate = v["candidateHorses"]
        numbers = [h["number"] for h in horses]
        ensure([h["number"] for h in candidate] == numbers,
               "candidate entrants mismatch original T10")
        ensure(len(numbers) >= 3 and len(set(numbers)) == len(numbers),
               "invalid original entrants")
        for kind, (ai_field, market_field, challenger_field, places) in EVENTS.items():
            finish = row.get("actualFinish")
            ensure(isinstance(finish, dict), "missing official finish mapping")
            try:
                podium = official_podium(finish, numbers, places, rid)
            except ValueError as exc:
                if "ambiguous podium dead heat" in str(exc):
                    counts["missingPodiumEvents"] += 1
                    continue
                raise
            if podium is None:
                counts["missingPodiumEvents"] += 1
                continue
            score = {"market": 0., "formalAI": 0., "noCurrentOdds": 0.}
            for h, pred in zip(horses, candidate):
                actual = int(h["number"] in podium)
                for name, raw in (
                    ("market", h[market_field]),
                    ("formalAI", h[ai_field]),
                    ("noCurrentOdds", pred[challenger_field]),
                ):
                    ensure(type(raw) in (float, int) and 0 <= raw <= 100,
                           "invalid frozen probability")
                    score[name] += (raw/100.0 - actual) ** 2 / len(numbers)
            grouped[kind].append(score)
        counts["pairedAuditedRaces"] += 1

    missing_keys = sorted(eligible_forward - seen)
    counts["eligibleAlignedCapturesSinceActivation"] = len(eligible_forward)
    counts["missingCandidateShadowsSinceActivation"] = len(missing_keys)
    counts["candidateShadowCoveragePct"] = (
        round(100 * len(eligible_forward & seen) / len(eligible_forward), 2)
        if eligible_forward else None
    )
    by_event = {}
    for kind in EVENTS:
        scores = grouped[kind]
        means = {name: (round(sum(s[name] for s in scores) / len(scores), 8)
                         if scores else None)
                 for name in ("market", "formalAI", "noCurrentOdds")}
        uncertainty_ai = paired_race_uncertainty([
            s["noCurrentOdds"] - s["formalAI"] for s in scores
        ])
        uncertainty_market = paired_race_uncertainty([
            s["noCurrentOdds"] - s["market"] for s in scores
        ])
        by_event[kind] = {
            "pairedRaces": len(scores),
            "meanBrier": means,
            "pairedRaceUncertaintyVsFormalAI": uncertainty_ai,
            "pairedRaceUncertaintyVsMarket": uncertainty_market,
            "candidateMinusFormalAIBrier": (
                round(means["noCurrentOdds"] - means["formalAI"], 8)
                if scores else None),
            "candidateMinusMarketBrier": (
                round(means["noCurrentOdds"] - means["market"], 8)
                if scores else None),
        }
    ensure(counts["pairedAuditedRaces"] <= aligned.get("matchedResultRaces"),
           "unfounded future matched counts")
    return {
        "schemaVersion": 1, "version": "no-current-odds-forward-review-v1",
        "status": ("candidate_not_published" if model_meta is None else
                   "insufficient_prospective_evidence"
                   if counts["pairedAuditedRaces"] < 100 else
                   "prospective_descriptive_only"),
        "sourceAuditGeneratedAtJST": audit["generatedAtJST"],
        "candidateActivationDate": ACTIVATION_DATE,
        "missingCandidateRaceIDs": [
            {"raceDate": date, "raceID": rid}
            for date, rid in missing_keys[:50]
        ],
        "qualityIssueCount": len((audit.get("quality") or {}).get("issues") or []),
        "modelVersion": model_meta["version"] if model_meta else None,
        "counts": counts, "byEvent": by_event,
        "researchOnly": True, "candidatePromotionApproved": False,
        "formalPredictionAIChanged": False, "autoBetEnabled": False,
        "limitations": [
            "Only separately first-frozen T10 snapshots count, never historical reconstructions.",
            "Original formal AI and market shares are from the same original T10 input.",
            "100 matched future races is a descriptive milestone, not proof of profitability.",
            "No automatic candidate selection, ROI claim, beta activation or betting.",
            "Any unresolved strict-audit quality problems remain visible.",
        ],
    }


def main():
    root = Path(__file__).resolve().parent.parent
    audit = json.loads((root/"AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    report = evaluate(root, audit)
    output = root / "research" / "NoCurrentOddsForwardReview.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                 allow_nan=False)+"\n", encoding="utf-8")
    lines = [
        "# No-current-odds prospective validation (research only)", "",
        "Status: " + report["status"],
        "Source strict audit: " + report["sourceAuditGeneratedAtJST"],
        f"First-frozen T-10 shadows: {report['counts']['frozenShadows']}",
        f"Independently matched: {report['counts']['pairedAuditedRaces']}",
        f"Eligible formal captures since {ACTIVATION_DATE}: {report['counts']['eligibleAlignedCapturesSinceActivation']}",
        f"Missing candidate shadows: {report['counts']['missingCandidateShadowsSinceActivation']}",
        f"Capture coverage: {report['counts']['candidateShadowCoveragePct']}%",
        f"Quality issues in strict audit: {report['qualityIssueCount']}", "",
        "| Event | Races | Formal AI Brier | Market T10 Brier | Odds-free Brier | Challenger - formal AI | Paired race bootstrap 95% |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for kind in ("win", "top2", "top3"):
        e = report["byEvent"][kind]
        b = e["meanBrier"]
        lines.append(f"| {kind} | {e['pairedRaces']} | {b['formalAI']} | "
                     f"{b['market']} | {b['noCurrentOdds']} | "
                     f"{e['candidateMinusFormalAIBrier']} | "
                     f"{e['pairedRaceUncertaintyVsFormalAI']['bootstrap95Pct']} |")
    lines += ["", "No model promotion or automatic wagers; first-frozen future evidence only.",
              "Capture coverage measures only aligned formal races after candidate activation.",
              "Bootstrap resamples whole races, not individual horses. A confidence interval crossing zero is inconclusive.",
              ""]
    if report["missingCandidateRaceIDs"]:
        lines += [
            "## Missing candidate snapshots (first 50; do not backfill)",
            "",
            *[
                f"- {r['raceDate']} / {r['raceID']}"
                for r in report["missingCandidateRaceIDs"]
            ],
            "",
        ]
    output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print("NO_CURRENT_ODDS_FORWARD_REVIEW", report["status"], report["counts"])


if __name__ == "__main__":
    main()
