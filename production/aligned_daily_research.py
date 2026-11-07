#!/usr/bin/env python3
"""Daily read-only diagnostics of verified, frozen KeibaAI aligned T-10 records.

Research outputs only. Never changes the source audit, frozen predictions,
bet selections, model rules, unit stakes, or beta eligibility.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path

BINS = ((0, 5, "0-<5%"), (5, 10, "5-<10%"), (10, 20, "10-<20%"),
        (20, 40, "20-<40%"), (40, 101, "40-100%"))
ODDS_BINS = ((0, 5, "<5x"), (5, 10, "5-<10x"), (10, 20, "10-<20x"),
             (20, math.inf, ">=20x"))
OUTPUT_JSON = Path("research/AlignedDailyResearch.json")
OUTPUT_MD = Path("research/AlignedDailyResearch.md")


def require(ok, reason):
    if not ok:
        raise ValueError("ALIGNED_RESEARCH_BLOCKED: " + reason)


def number(value, field, low=None, high=None):
    require(type(value) in (int, float), field + " must be numeric")
    v = float(value)
    require(math.isfinite(v), field + " must be finite")
    if low is not None:
        require(v >= low, field + " below lower bound")
    if high is not None:
        require(v <= high, field + " above upper bound")
    return v


def normalize_ticket(raw, date, race_id, venue, grade, chaos, selected):
    require(isinstance(raw, dict), "ticket is not an object")
    hit = raw.get("hit")
    pay = raw.get("payoutYen")
    require(type(hit) is bool, "unsettled ticket in settlement sample")
    require(type(pay) is int and pay >= 0, "invalid settled ticket payout")
    require((hit and pay > 0) or (not hit and pay == 0),
            "ticket hit/payout disagreement")
    unit = raw.get("unitYen")
    require(type(unit) is int and unit > 0, "invalid stake")
    p = number(raw.get("probabilityPct"), "probabilityPct", 0, 100)
    low = raw.get("oddsLow")
    if low is not None:
        low = number(low, "oddsLow", 0)
    kind = str(raw.get("betType") or "")
    require(kind != "", "missing bet type")
    return {"raceDate": date, "raceID": race_id, "venue": venue,
            "battleGrade": grade, "chaosLevel": chaos, "selected": selected,
            "betType": kind, "probabilityPct": p, "oddsLow": low,
            "unitYen": unit, "hit": hit, "payoutYen": pay}


def ticket_stats(tickets):
    n = len(tickets)
    stake = sum(t["unitYen"] for t in tickets)
    returned = sum(t["payoutYen"] for t in tickets)
    expected = sum(t["probabilityPct"] / 100 for t in tickets)
    brier = (sum((t["probabilityPct"] / 100 - int(t["hit"])) ** 2
                 for t in tickets) / n) if n else None
    conservative = (
        sum(t["unitYen"] * t["probabilityPct"] / 100 * t["oddsLow"]
            for t in tickets)
        if all(t["oddsLow"] is not None for t in tickets) else None
    )
    return {
        "tickets": n,
        "distinctRaces": len({(t["raceDate"], t["raceID"]) for t in tickets}),
        "winningTickets": sum(t["hit"] for t in tickets),
        "expectedWinningTickets": round(expected, 4),
        "meanPredictedProbabilityPct": round(expected * 100 / n, 3) if n else None,
        "actualHitRatePct": round(sum(t["hit"] for t in tickets) * 100 / n, 3) if n else None,
        "brierScore": round(brier, 6) if brier is not None else None,
        "stakeYen": stake,
        "returnYen": returned,
        "profitYen": returned - stake,
        "returnIndex": round(returned / stake, 6) if stake else None,
        "conservativeFrozenOddsExpectationYen": (
            round(conservative, 2) if conservative is not None else None),
    }


def split_stats(tickets, key):
    groups = defaultdict(list)
    for t in tickets:
        groups[str(key(t))].append(t)
    return {k: ticket_stats(groups[k]) for k in sorted(groups)}


def bin_name(value, bins):
    for low, high, label in bins:
        if low <= value < high:
            return label
    raise ValueError("value outside diagnostic bin range")


def calibration(tickets):
    # Every ticket is a Bernoulli outcome; these are descriptive summaries.
    # Tickets sharing a race are correlated; no IID or binomial CI is claimed.
    return split_stats(tickets,
                       lambda t: bin_name(t["probabilityPct"], BINS))


def concentration(races):
    # Select winning payouts for removal without touching frozen selection.
    ranked = sorted(races, key=lambda r: (-r["returnYen"], r["raceDate"], r["raceID"]))
    total_stake = sum(r["stakeYen"] for r in ranked)
    total_return = sum(r["returnYen"] for r in ranked)
    result = {}
    for k in (0, 1, 2):
        rest = ranked[k:]
        stake = sum(r["stakeYen"] for r in rest)
        ret = sum(r["returnYen"] for r in rest)
        result["removeTop" + str(k) + "ReturnRaces"] = {
            "remainingRaces": len(rest), "stakeYen": stake,
            "returnYen": ret, "returnIndex": round(ret / stake, 6) if stake else None,
            "excludedRaceIDs": [r["raceID"] for r in ranked[:k]],
        }
    result["largestOneRaceShareOfPayoutPct"] = (
        round(ranked[0]["returnYen"] / total_return * 100, 3)
        if ranked and total_return else None)
    result["originalReturnIndex"] = (
        round(total_return / total_stake, 6) if total_stake else None)
    return result


def build(audit, protocol):
    require(isinstance(audit, dict) and isinstance(protocol, dict), "invalid input")
    aligned = audit.get("aligned") or {}
    quality = audit.get("quality") or {}
    require(audit.get("projectionState") == "verified", "projection not verified")
    require(aligned.get("countsAreCurrent") is True, "counts are not current")
    require(audit.get("cohortLockRecoveryRequired") is not True,
            "cohort locks need recovery")
    require(quality.get("issues") == [] and quality.get("invalidCaptureCount") == 0
            and quality.get("missingCaptureCount") == 0,
            "aligned input-quality failures")
    require(audit.get("protocolVersion") == protocol.get("version"),
            "protocol version mismatch")
    require(protocol.get("policy", {}).get("noV1Changes") is True,
            "non-frozen research protocol")
    rows = audit.get("races")
    require(isinstance(rows, list), "missing audited race rows")
    require(len(rows) == aligned.get("racesObserved"),
            "race count differs from aligned summary")
    require(sum(r.get("resultState") == "matched" for r in rows)
            == aligned.get("matchedResultRaces"), "result-match count mismatch")
    require(audit.get("generatedAtJST"), "missing audit provenance timestamp")
    overall = aligned.get("overall") or {}
    require(sum(r.get("planType") != "skip" for r in rows)
            == overall.get("selectedRaces"), "selection count mismatch")

    selected = []
    reference = []
    settled_races = []
    races_by_day = defaultdict(lambda: {"all": 0, "matched": 0, "selected": 0,
                                         "settledSelected": 0})
    zero_ref = {"selected": 0, "skipped": 0}
    pending_ref_races = 0
    observed = set()
    for row in rows:
        date = str(row.get("raceDate") or "")
        rid = str(row.get("raceID") or "")
        require(bool(date) and bool(rid) and (date, rid) not in observed,
                "missing/duplicate race identity")
        observed.add((date, rid))
        require(date >= protocol["startDate"] and row.get("scope") == "prospective",
                "pre-cohort or nonprospective race in aligned sample")
        require(row.get("ruleVersion") == protocol.get("sourceRuleVersion"),
                "frozen rule version mismatch")
        venue = str(row.get("venue") or "")
        grade = str(row.get("battleGrade") or "unknown")
        chaos = str(row.get("chaosLevel") or "unknown")
        is_selected = row.get("planType") != "skip"
        day = races_by_day[date]
        day["all"] += 1
        day["matched"] += int(row.get("resultState") == "matched")
        day["selected"] += int(is_selected)

        raw_ref = row.get("commonWideReference") or []
        require(isinstance(raw_ref, list), "invalid reference list")
        if not raw_ref:
            zero_ref["selected" if is_selected else "skipped"] += 1
        if row.get("referenceSettled") is True and row.get("resultState") == "matched":
            for t in raw_ref:
                reference.append(normalize_ticket(t, date, rid, venue, grade, chaos, is_selected))
        elif raw_ref:
            pending_ref_races += 1

        if not is_selected or row.get("settled") is not True:
            continue
        require(row.get("resultState") == "matched", "settled but result not matched")
        raw_legs = row.get("legs")
        require(isinstance(raw_legs, list) and bool(raw_legs),
                "settled selected race has no legs")
        tickets = [normalize_ticket(t, date, rid, venue, grade, chaos, True)
                   for t in raw_legs]
        require(sum(t["unitYen"] for t in tickets) == row.get("totalStakeYen"),
                "audited stake/legs mismatch")
        require(sum(t["payoutYen"] for t in tickets) == row.get("returnYen"),
                "audited return/legs mismatch")
        day["settledSelected"] += 1
        selected.extend(tickets)
        settled_races.append({"raceDate": date, "raceID": rid, "venue": venue,
                              "battleGrade": grade, "chaosLevel": chaos,
                              "stakeYen": row["totalStakeYen"],
                              "returnYen": row["returnYen"]})

    require(len(settled_races) == overall.get("settledSelectedRaces"),
            "settled race count mismatch")
    require(sum(r["stakeYen"] for r in settled_races) == overall.get("stakeYen"),
            "settled stake count mismatch")
    require(sum(r["returnYen"] for r in settled_races) == overall.get("returnYen"),
            "settled return count mismatch")
    require(len(rows) - len(settled_races) >= 0, "invalid cohort")

    selected_wide = [t for t in selected if t["betType"] == "wide"]
    selected_trio = [t for t in selected if t["betType"] == "trio"]
    selected_groups = {
        "all": ticket_stats(selected),
        "wide": ticket_stats(selected_wide),
        "trio": ticket_stats(selected_trio),
        "byDate": split_stats(selected, lambda t: t["raceDate"]),
        "byVenue": split_stats(selected, lambda t: t["venue"]),
        "byBattleGrade": split_stats(selected, lambda t: t["battleGrade"]),
        "byBattleAndChaos": split_stats(
            selected, lambda t: t["battleGrade"] + "/Chaos" + t["chaosLevel"]),
        "wideByBattleGrade": split_stats(selected_wide, lambda t: t["battleGrade"]),
        "wideByBattleChaos": split_stats(
            selected_wide, lambda t: t["battleGrade"] + "/Chaos" + t["chaosLevel"]),
        "wideByFrozenOdds": split_stats(
            selected_wide, lambda t: bin_name(t["oddsLow"], ODDS_BINS)
            if t["oddsLow"] is not None else "missing"),
    }
    selected_ref = [t for t in reference if t["selected"]]
    skipped_ref = [t for t in reference if not t["selected"]]
    trio_race_ids = {(t["raceDate"], t["raceID"]) for t in selected_trio}
    trio_race_wide = [t for t in selected_wide
                      if (t["raceDate"], t["raceID"]) in trio_race_ids]
    normalized = {
        "schemaVersion": 1,
        "status": "verified_research_only",
        "source": {
            "file": "AlignedPortfolioReview.json",
            "generatedAtJST": audit["generatedAtJST"],
            "projectionState": "verified",
            "countsAreCurrent": True,
            "inputProtocolVersion": audit["protocolVersion"],
            "inputFingerprint": hashlib.sha256(
                json.dumps([audit.get("protocolFingerprint"), rows],
                           ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest(),
        },
        "rules": {
            "modelUnchanged": True, "selectionUnchanged": True,
            "automaticBetting": False, "thresholdFittingOnEvaluation": False,
            "betaEligibilityUnchanged": audit.get("betaDecision", {}).get("eligibleForResearchBeta") is True,
        },
        "cohort": {
            "racesObserved": len(rows),
            "resultMatchedRaces": aligned["matchedResultRaces"],
            "selectedRaces": overall["selectedRaces"],
            "skippedRaces": overall.get("skippedRaces"),
            "settledSelectedRaces": len(settled_races),
            "pendingSelectedRaces": overall.get("resultsPendingSelectedRaces"),
            "referenceUnsettledRaces": pending_ref_races,
            "zeroReferenceCandidateRaces": zero_ref,
            "dailyCoverage": {k: races_by_day[k] for k in sorted(races_by_day)},
        },
        "probability": {
            "byBetType": {"wide": ticket_stats(selected_wide),
                          "trio": ticket_stats(selected_trio)},
            "reliabilityByBetType": {
                "wide": calibration(selected_wide),
                "trio": calibration(selected_trio),
            },
            "note": "Ticket outcomes within a race are correlated; these summaries are descriptive, not IID confidence intervals.",
        },
        "selection": {
            "preRegisteredH1SameWideReference": {
                "selectedRaceReference": ticket_stats(selected_ref),
                "skippedRaceReference": ticket_stats(skipped_ref),
                "allRacesReference": ticket_stats(reference),
                "byDayAndSelection": split_stats(
                    reference, lambda t: t["raceDate"] + ("/selected" if t["selected"] else "/skipped")),
                "caution": "Same frozen Wide reference in both cohorts; missing reference candidates remain counted as races. Not a causally identified strategy improvement.",
            },
            "preRegisteredH2Battle": {
                "wideByGrade": selected_groups["wideByBattleGrade"],
                "wideByGradeChaos": selected_groups["wideByBattleChaos"],
                "wideByFrozenOdds": selected_groups["wideByFrozenOdds"],
            },
            "preRegisteredH3Concentration": {
                "payoutConcentration": concentration(settled_races),
                "byDate": selected_groups["byDate"],
                "byVenue": selected_groups["byVenue"],
                "allSelectedByGradeChaos": selected_groups["byBattleAndChaos"],
            },
            "preRegisteredH4TrioIncrement": {
                "trioIncrement": ticket_stats(selected_trio),
                "wideLegsInSameTrioRaces": ticket_stats(trio_race_wide),
                "allWideLegs": ticket_stats(selected_wide),
                "caution": "Incremental trio tickets are not an independent comparison with separately fitted rules.",
            },
            "actualSelectedTickets": selected_groups["all"],
        },
        "limitations": [
            "Research-only diagnostics under registered aligned H1-H4: no V1 changes, no backfill promotion, no post-hoc parameter fitting.",
            "The first 100 aligned races are diagnostic; 101-200 are frozen-rule confirmation; beta needs 300 plus other requirements.",
            "Multiple tickets from the same race are correlated; return concentration and final payout changes make one-day ROI unstable.",
            "Conservative odds expected return is a frozen-odds counterfactual, not guaranteed realized payout or a profitability proof.",
        ],
    }
    return normalized


def render(payload):
    c = payload["cohort"]
    prob = payload["probability"]["byBetType"]
    h1 = payload["selection"]["preRegisteredH1SameWideReference"]
    h2 = payload["selection"]["preRegisteredH2Battle"]
    h3 = payload["selection"]["preRegisteredH3Concentration"]
    h4 = payload["selection"]["preRegisteredH4TrioIncrement"]
    s = payload["selection"]["actualSelectedTickets"]
    lines = [
        "# KeibaAI aligned daily research (read-only)",
        "",
        "**Source audit:** " + payload["source"]["generatedAtJST"],
        "**Status:** verified input, research-only. No model/selection/betting changes.",
        "",
        "## Cohort and outcome",
        f"- Aligned races / matched results: {c['racesObserved']} / {c['resultMatchedRaces']}",
        f"- Selected / skipped races: {c['selectedRaces']} / {c['skippedRaces']}",
        f"- Settled selected / pending selected: {c['settledSelectedRaces']} / {c['pendingSelectedRaces']}",
        f"- Paper tickets: {s['tickets']} (Wide {prob['wide']['tickets']}, trio {prob['trio']['tickets']})",
        f"- Paper stake / return / profit (JPY): {s['stakeYen']} / {s['returnYen']} / {s['profitYen']}",
        f"- Return multiple: {s['returnIndex']}",
        f"- Predicted vs actual winning tickets: {s['expectedWinningTickets']} vs {s['winningTickets']}",
        "",
        "## H1: frozen common-Wide reference, selected vs skipped races",
        "",
        "| Cohort | Tickets | Expected wins | Actual wins | Stake (JPY) | Return (JPY) | Return multiple |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, label in (("selectedRaceReference", "Selected"),
                       ("skippedRaceReference", "Skipped"),
                       ("allRacesReference", "All")):
        x = h1[key]
        lines.append(
            f"| {label} | {x['tickets']} | {x['expectedWinningTickets']} | "
            f"{x['winningTickets']} | {x['stakeYen']} | {x['returnYen']} | {x['returnIndex']} |")
    lines += [
        "",
        f"Zero-reference-candidate race counts (never dropped from cohort): {c['zeroReferenceCandidateRaces']}",
        "",
        "## H2: observed Wide performance by frozen battle grade",
        "",
        "| Grade | Tickets | Expected wins | Actual wins | Return multiple |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key, x in h2["wideByGrade"].items():
        lines.append(
            f"| {key} | {x['tickets']} | {x['expectedWinningTickets']} | "
            f"{x['winningTickets']} | {x['returnIndex']} |")
    lines += [
        "",
        "## H3: largest-return-race concentration",
        "",
        "| Excluding top returned races | Races remaining | Return multiple |",
        "| --- | ---: | ---: |",
    ]
    for n in (0, 1, 2):
        x = h3["payoutConcentration"]["removeTop" + str(n) + "ReturnRaces"]
        lines.append(f"| {n} | {x['remainingRaces']} | {x['returnIndex']} |")
    lines += [
        "",
        "## H4: incremental trio tickets",
        "",
        f"- Trio-only incremental: {h4['trioIncrement']['tickets']} tickets; "
        f"{h4['trioIncrement']['stakeYen']} JPY stake, "
        f"{h4['trioIncrement']['returnYen']} JPY return.",
        f"- Wide legs in those same races: {h4['wideLegsInSameTrioRaces']['tickets']} tickets; "
        f"{h4['wideLegsInSameTrioRaces']['returnIndex']} return multiple.",
        "",
        "## Probability reliability / daily trends",
        "",
        "Detailed, fixed-bin reliability, venue, date, grade/chaos, frozen-odds, "
        "and scope comparisons are in the sibling JSON file.",
        "",
        "**Interpretation guardrails:** Multiple legs from the same race are correlated. "
        "Do not infer calibration, a better optimized rule, or profitable betting "
        "from a short run. No beta enabling or backtest promotion.",
        "",
    ]
    return "\n".join(lines)


def main():
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    audit = json.loads((root / "AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    protocol = json.loads((root / "production/ALIGNED_PORTFOLIO_PROTOCOL.json").read_text(encoding="utf-8"))
    report = build(audit, protocol)
    outputs = ((root / OUTPUT_JSON,
                json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"),
               (root / OUTPUT_MD, render(report)))
    for path, content in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == content:
            print("RESEARCH_UNCHANGED", path.relative_to(root), flush=True)
            continue
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)
        print("RESEARCH_UPDATED", path.relative_to(root), flush=True)
    print(json.dumps({
        "sourceAuditJST": report["source"]["generatedAtJST"],
        "races": report["cohort"]["racesObserved"],
        "matched": report["cohort"]["resultMatchedRaces"],
        "settled": report["cohort"]["settledSelectedRaces"],
        "researchOnly": True,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
