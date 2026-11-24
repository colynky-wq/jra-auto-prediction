"""Reproduce wide ticket policies from immutable pre-post inputs and official settlement."""

import argparse
from collections import Counter
import csv
from datetime import datetime
import hashlib
from itertools import combinations
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from aligned_portfolio_review import read, wide_results
from all_bet_baseline import result_state
from all_bet_forward import validate as validate_win_record
from build_bet_research_data import checked_results
from build_signal_data_v2 import _all_wide_pairs
from formal_portfolio_capture import digest, load, stamp, window
from jra_wide_research_archive import read_record as read_jra_record, verify_snapshot
from longshot_top3_v2_runtime import model_spec, validate_record
from research_longshot_top3_v7 import blend, raw, within_race
from research_longshot_wide_coherent import (
    Race, combinations_for_field, outcome_probabilities, pair_marginals, triple_features,
)
from research_longshot_wide_joint import joint
from topk_runtime import market_top2_top3
from train_longshot_top3 import JST, ROOT, logit, odds_underdog
from win_odds_freshness import MAX_AGE, parse_official_datetime


POLICIES = ("probability", "oddsFiltered")


def prices(snapshot):
    pairs = {}
    for row in snapshot["pairs"]:
        a, b = sorted((row["number1"], row["number2"]))
        low, high = row["oddsLow"], row["oddsHigh"]
        if (type(a) is not int or type(b) is not int or not 1 <= a < b
                or (a, b) in pairs or type(low) not in (int, float)
                or type(high) not in (int, float) or not np.isfinite([low, high]).all()
                or min(low, high) < 1):
            raise ValueError("invalid Wide price row")
        if low > high:
            raise ValueError("Wide lower odds exceed upper odds")
        pairs[a, b] = (low, high)
    if not pairs or len(pairs) != snapshot["pairCount"]:
        raise ValueError("Wide price count mismatch")
    return pairs


def source_age(snapshot, clock):
    origin = parse_official_datetime(snapshot.get("officialDatetime"))
    receipt = stamp(snapshot["capturedAtJST"])
    if origin.date().isoformat() != snapshot["raceDate"] or not origin <= receipt <= clock:
        raise ValueError("invalid Wide source chronology")
    return (clock - origin).total_seconds()


def scan_archive(root):
    rows = []
    for path in sorted(root.glob("reports/*/wide_odds_live10/*.json")):
        row = {"raceID": path.stem, "day": path.parents[1].name, "state": "invalid",
               "receiptAgeSeconds": None, "earlierComparison": "unavailable"}
        try:
            saved = read(path)
            day = saved["raceDate"]
            if (day.replace("-", "") != row["day"] or saved["raceID"] != path.stem
                    or saved["apiType"] != 5 or saved["apiStatus"] != "middle"
                    or saved["frozenAtFirstSuccessfulCapture"] is not True):
                raise ValueError("invalid Wide archive identity")
            captured = window(day, saved["startTime"], saved["capturedAtJST"])
            age = source_age(saved, captured)
            row["receiptAgeSeconds"] = age
            current = prices(saved)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            row["reason"] = str(exc)
            rows.append(row)
            continue
        row["state"] = "sourceWithin30Minutes" if age <= MAX_AGE.total_seconds() else "sourceOver30Minutes"
        early_path = path.parent.with_name("wide_odds") / path.name
        if early_path.is_file():
            try:
                earlier = read(early_path)
                if any(earlier[k] != saved[k] for k in ("raceDate", "raceID", "startTime")):
                    raise ValueError("earlier Wide identity differs")
                before = stamp(earlier["capturedAtJST"])
                old_prices = prices(earlier)
                if before >= captured:
                    row["earlierComparison"] = "noEarlierReceipt"
                elif old_prices.keys() != current.keys():
                    row["earlierComparison"] = "pairRosterChanged"
                else:
                    previous = parse_official_datetime(earlier["officialDatetime"])
                    published = parse_official_datetime(saved["officialDatetime"])
                    time_change = "laterSourceTime" if previous < published else (
                        "sameSourceTime" if previous == published else "earlierSourceTime"
                    )
                    price_change = "pricesChanged" if old_prices != current else "samePrices"
                    row["earlierComparison"] = f"{time_change}:{price_change}"
            except (ValueError, KeyError, TypeError, OSError) as exc:
                row["earlierComparison"] = "invalidEarlierRecord"
                row["earlierReason"] = str(exc)
        rows.append(row)
    return rows


def coherent_predictions(race, horses, longshot, saved, rank_model, models, choice):
    spec = model_spec()
    day, rid = race["raceDate"], race["raceID"]
    validate_record(saved, day, rid, spec)
    if saved["sourcePredictionSha256"] != digest(race) or saved["startTime"] != race["startTime"]:
        raise ValueError("longshot input does not match frozen race")

    numbers = np.array([h["number"] for h in horses])
    active = {h["number"]: h for h in horses}
    recorded = {h["number"]: h for h in saved["horses"]}
    if set(numbers[longshot]) != set(recorded):
        raise ValueError("recorded longshots differ from known entrants")
    for number, horse in recorded.items():
        if (horse["name"] != active[number]["name"]
                or horse["popularity"] != active[number]["popularity"]
                or horse["winOdds"] != active[number]["odds"]):
            raise ValueError("recorded horse identity or odds differ")
    inputs = saved["modelInput"]
    if inputs is None:
        raise ValueError("frozen rank features absent")
    frame = pd.DataFrame(inputs["rows"], columns=inputs["features"], index=inputs["horseNumbers"])
    frame = frame.loc[numbers[longshot]].copy()
    frame["レースID"] = rid
    p2 = np.array([recorded[n]["top3ProbabilityPct"] / 100 for n in frame.index])
    if not np.isfinite(p2).all() or not ((0 < p2) & (p2 < 1)).all():
        raise ValueError("invalid frozen longshot probabilities")

    weights = 1 / np.array([h["odds"] for h in horses], dtype=float)
    market3 = market_top2_top3(weights)[1][longshot]
    if not np.allclose(frame["marketTop3LogitV2"], logit(market3), atol=1e-8, rtol=0):
        raise ValueError("market odds differ from frozen feature inputs")
    residual = np.zeros(len(horses))
    rank = np.zeros(len(horses))
    residual[longshot] = logit(p2) - logit(market3)
    rank[longshot] = within_race(frame, blend(frame, p2, raw(frame, rank_model, choice["rounds"]), choice["alpha"]))

    pairs, triples, edges = combinations_for_field(len(horses))
    candidates = np.flatnonzero(longshot[pairs].any(axis=1))
    left, right = pairs[candidates].T
    x, y = residual[left], residual[right]
    u, v = rank[left], rank[right]
    features, _ = triple_features(
        weights, longshot, candidates,
        np.column_stack((x+y, np.minimum(x,y), np.maximum(x,y), x*y, u+v, np.minimum(u,v), np.maximum(u,v))),
        triples, edges, len(pairs),
    )
    field = Race(pd.Timestamp(day), features, edges, None, np.arange(len(candidates)), candidates, len(pairs))
    output = {}
    for name in ("coherentV2", "coherentV7"):
        marginal = pair_marginals(field, outcome_probabilities(field, models[name]))
        output[name] = {tuple(numbers[pairs[i]]): float(marginal[i]) for i in candidates}
    return output


def select_tickets(candidates, count, policy):
    """Select exactly count tickets or abstain. No finish or payout input."""
    ranked = sorted(candidates, key=lambda row: (-row["probability"], row["a"], row["b"]))
    if policy == "oddsFiltered":
        ranked = [
            row for row in ranked
            if row["oddsLow"] >= count and row["probability"] * row["oddsLow"] >= 1.0
        ]
    elif policy != "probability":
        raise ValueError("unknown Wide replay policy")
    return ranked[:count] if len(ranked) >= count else []


def evaluate_policies(race, estimates, odds, payouts, age):
    rows, tickets = [], []
    for method, chances in estimates.items():
        candidate = [
            {"a": a, "b": b, "probability": p, "oddsLow": odds[a,b][0], "oddsHigh": odds[a,b][1]}
            for (a,b), p in chances.items()
        ]
        for policy in POLICIES:
            for count in (1, 3):
                selected = select_tickets(candidate, count, policy)
                row = {"raceID": race["raceID"], "raceDate": race["raceDate"], "method": method,
                       "policy": policy, "targetTickets": count,
                       "sourceAgeSecondsAtDecision": age, "tickets": len(selected)}
                settled = []
                for bet in selected:
                    key = f"{bet['a']:02}{bet['b']:02}"
                    hit, payout = payouts[key]
                    settled.append({**bet, "hit": hit, "payoutYen": payout})
                tickets.extend({**row, **bet} for bet in settled)
                pay = sum(bet["payoutYen"] for bet in settled)
                stake = 100 * len(settled)
                rows.append({
                    **row, "stakeYen": stake, "payoutYen": pay,
                    "hits": sum(bet["hit"] for bet in settled),
                    "torigami": 0 < pay < stake,
                    "hitBelowQuotedLower": sum(
                        bet["hit"] and bet["payoutYen"] + 1e-6 < 100 * bet["oddsLow"]
                        for bet in settled
                    ),
                })
    return rows, tickets


def summary(rows):
    indexes = sorted({(r["method"], r["policy"], r["targetTickets"]) for r in rows})
    out = []
    for method, policy, target in indexes:
        group = [r for r in rows if (r["method"], r["policy"], r["targetTickets"]) == (method, policy, target)]
        stake = sum(r["stakeYen"] for r in group)
        payout = sum(r["payoutYen"] for r in group)
        out.append({
            "method": method, "policy": policy, "targetTickets": target,
            "eligibleRaces": len(group), "days": len({r["raceDate"] for r in group}),
            "playedRaces": sum(r["tickets"] > 0 for r in group),
            "racesHit": sum(r["hits"] > 0 for r in group),
            "hits": sum(r["hits"] for r in group), "stakeYen": stake, "payoutYen": payout,
            "returnPct": 100 * payout / stake if stake else None,
            "torigamiRaces": sum(r["torigami"] for r in group),
            "winningTicketsPaidBelowQuotedLower": sum(r["hitBelowQuotedLower"] for r in group),
        })
    return out


def legacy_inputs(root, path, day, rid, now):
    bundle = load(root, day, rid)
    race = bundle["prediction"]
    captured = window(day, race["startTime"], race["capturedAtJST"])
    frozen = window(day, race["startTime"], bundle["recordedAtJST"])
    odds = read(path.parent.with_name("wide_odds_live10") / path.name)
    if (race["captureQuality"] != "formal_10min" or race["predictionTiming"] != "live_10min"
            or any(odds[k] != race[k] for k in ("raceID", "raceDate", "startTime"))
            or odds["apiStatus"] != "middle" or odds["apiType"] != 5
            or odds["frozenAtFirstSuccessfulCapture"] is not True
            or not captured <= frozen <= now):
        raise ValueError("invalid bound pre-race inputs")
    window(day, race["startTime"], odds["capturedAtJST"])
    return race, odds, frozen


def official_inputs(race_dir, path, day, rid, now):
    date8 = day.replace("-", "")
    record = read_jra_record(path, date8, rid)
    source = record["snapshot"]
    win = validate_win_record(read(race_dir / "all_bet_v1" / "win" / path.name))
    race = win["prediction"]
    if (win["betType"] != "win" or race["raceID"] != rid or race["raceDate"] != day
            or race["captureQuality"] != "formal_10min" or race["predictionTiming"] != "live_10min"
            or stamp(f'{day}T{race["startTime"]}') != stamp(source["scheduledPostJST"])):
        raise ValueError("official Wide and frozen prediction identities differ")
    captured = window(day, race["startTime"], race["capturedAtJST"])
    prediction_available = window(day, race["startTime"], win["predictionAvailableAtJST"])
    prediction_recorded = window(day, race["startTime"], win["recordedAtJST"])
    decision = max(prediction_recorded, stamp(record["recordedAtJST"]))
    if not captured <= prediction_available <= prediction_recorded <= decision <= now:
        raise ValueError("official Wide replay input chronology differs")
    return race, source, decision


def run(root, output, models, rank_model, choice, now, *, jra_archive=None):
    official = jra_archive is not None
    if official:
        paths = sorted(jra_archive.glob("*/*.json"))
        inventories = []
    else:
        paths = sorted(root.glob("reports/*/portfolio_live10/*.json"))
        inventories = scan_archive(root)
    rows, tickets = [], []
    audits = []
    results_by_day = {}
    for path in paths:
        day8, rid = (path.parent.name if official else path.parents[1].name), path.stem
        race_dir = root / "reports" / day8
        audit = {"raceID": rid, "raceDate": None, "status": "invalid", "coherent": "noFrozenInput"}
        try:
            day = datetime.strptime(day8, "%Y%m%d").date().isoformat()
            audit["raceDate"] = day
            if official:
                if not (race_dir / "all_bet_v1" / "win" / path.name).is_file():
                    read_jra_record(path, day8, rid)
                    audit["status"] = "awaitingPrediction"
                    continue
                race, odds_doc, frozen = official_inputs(race_dir, path, day, rid, now)
                age = verify_snapshot(odds_doc, day8, rid, frozen)
            else:
                race, odds_doc, frozen = legacy_inputs(root, path, day, rid, now)
                age = source_age(odds_doc, frozen)
            audit["sourceAgeSecondsAtDecision"] = age
            odds = prices(odds_doc)
            horses = sorted(race["horses"], key=lambda h: h["number"])
            numbers = [h["number"] for h in horses]
            if len(numbers) < 8:
                audit["status"] = "fieldUnder8"
                continue
            if len(numbers) != len(set(numbers)) or set(odds) != set(combinations(numbers, 2)):
                raise ValueError("Wide price roster differs")
            longshot = odds_underdog([h["popularity"] for h in horses], [h["odds"] for h in horses])
            if not longshot.any():
                audit["status"] = "noLongshot"
                continue
            weights = 1 / np.array([h["odds"] for h in horses], dtype=float)
            market = joint(weights)
            candidates = {
                (numbers[i], numbers[j]): float(market[i, j])
                for i, j in combinations(range(len(horses)), 2) if longshot[i] or longshot[j]
            }
            ai = {
                tuple(sorted((row["number1"], row["number2"]))): row["aiWideProbability"] / 100
                for row in _all_wide_pairs(race)
            }
            estimates = {"marketPL": candidates, "currentAI": {pair: ai[pair] for pair in candidates}}

            v2_path = race_dir / "longshot_top3_v2" / path.name
            coherent_age = age
            if v2_path.is_file():
                try:
                    v2 = read(v2_path)
                    rank_ready = max(frozen, window(day, race["startTime"], v2["recordedAtJST"]))
                    if rank_ready > now:
                        raise ValueError("Wide rank inputs postdate the replay clock")
                    if official:
                        coherent_age = verify_snapshot(odds_doc, day8, rid, rank_ready)
                    else:
                        coherent_age = source_age(odds_doc, rank_ready)
                    estimates.update(coherent_predictions(race, horses, longshot, v2, rank_model, models, choice))
                    audit["coherent"] = "inputVerified"
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    audit["coherent"] = "invalidFrozenInput"
                    audit["coherentReason"] = str(exc)

            if day not in results_by_day:
                results_by_day[day] = checked_results(race_dir / "ResultData.json", day, now)
            actual = results_by_day[day].get((day, rid))
            state, finish = result_state(race, actual)
            if state != "matched":
                audit["status"] = state
                continue
            payouts = wide_results(root, race, finish)
            if not payouts:
                audit["status"] = "missingPayout"
                continue
            for method, probabilities in estimates.items():
                decision_age = coherent_age if method.startswith("coherent") else age
                decisions, bets = evaluate_policies(race, {method: probabilities}, odds, payouts, decision_age)
                rows.extend(decisions)
                tickets.extend(bets)
            audit["status"] = "matched"
        except (ValueError, KeyError, TypeError, OSError) as exc:
            audit["reason"] = str(exc)
        finally:
            audits.append(audit)

    ages = [row["receiptAgeSeconds"] for row in inventories if row["receiptAgeSeconds"] is not None]
    fresh = [row for row in rows if row["sourceAgeSecondsAtDecision"] <= MAX_AGE.total_seconds()]
    stale = [row for row in rows if row["sourceAgeSecondsAtDecision"] > MAX_AGE.total_seconds()]
    coherent_races = {(r["raceDate"], r["raceID"]) for r in rows if r["method"] == "coherentV7"}
    on_coherent = [r for r in stale if (r["raceDate"], r["raceID"]) in coherent_races]
    report = {
        "version": "longshot-wide-odds-replay-v1",
        "researchOnly": True,
        "candidatePromotionApproved": False,
        "modelChoice": choice,
        "coherentModelInputDigest": digest(models),
        "policy": {
            "ticketStakeYen": 100,
            "ticketsPerRace": [1, 3],
            "longshotMinimumPopularityRank": 6,
            "longshotMinimumWinOdds": 10,
            "oddsFiltered": "Rank by probability after requiring probability × quoted lower odds >= 1 and quoted lower odds >= ticket count.",
            "clock": "Freshness uses the later time when all required inputs were available.",
        },
        "rankModelSha256": hashlib.sha256(rank_model.model_to_string().encode()).hexdigest(),
        "freshnessMaximumSeconds": int(MAX_AGE.total_seconds()),
        "snapshotInventory": {
            "count": len(inventories), "days": len({r["day"] for r in inventories}),
            "counts": dict(Counter(row["state"] for row in inventories)),
            "originTimestampsOver30Minutes": sum(age > MAX_AGE.total_seconds() for age in ages),
            "earlierComparison": dict(Counter(row["earlierComparison"] for row in inventories)),
            "ageAtReceiptSeconds": {
                "min": min(ages, default=None), "median": float(np.median(ages)) if ages else None,
                "max": max(ages, default=None),
            },
        },
        "boundReplay": {
            "portfolioRecords": len(audits), "states": dict(Counter(row["status"] for row in audits)),
            "coherentInputStates": dict(Counter(row["coherent"] for row in audits)),
            "settledCoherentRaces": len(coherent_races),
            "freshEligibleRaces": len({(r["raceDate"], r["raceID"]) for r in fresh}),
            "freshSummary": summary(fresh),
            "staleDiagnosticsAllSettledRaces": summary(stale),
            "staleDiagnosticsCoherentCommonRaces": summary(on_coherent),
        },
    }
    if official:
        report["sourceProvider"] = "jra_official_web"
        report["snapshotInventory"] = {
            "count": len(paths), "days": len({path.parent.name for path in paths}),
        }
        report["boundReplay"]["officialArchiveRecords"] = report["boundReplay"].pop("portfolioRecords")
    output.mkdir(parents=True, exist_ok=True)
    (output / "research.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (output / "race_audit.json").write_text(json.dumps({
        "snapshots": inventories, "replay": audits,
    }, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    columns = (
        "raceID", "raceDate", "method", "policy", "targetTickets", "sourceAgeSecondsAtDecision",
        "tickets", "a", "b", "probability", "oddsLow", "oddsHigh", "hit", "payoutYen",
    )
    with (output / "tickets.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(tickets)
    print("WIDE_ODDS_REPLAY_COMPLETE", json.dumps({
        "snapshots": report["snapshotInventory"], "states": report["boundReplay"]["states"],
        "coherentRaces": len(coherent_races), "freshRaces": report["boundReplay"]["freshEligibleRaces"],
    }), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coherent-models", type=Path, required=True)
    parser.add_argument("--rank-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jra-archive", type=Path, help="replay only frozen official JRA Wide records")
    args = parser.parse_args()
    if args.jra_archive is not None and not args.jra_archive.is_dir():
        parser.error("--jra-archive must be an existing directory")
    choice = read(ROOT / "research/LongshotV7VerifiedSummary.json")["results"]["selected"]
    run(ROOT, args.output, read(args.coherent_models), lgb.Booster(model_file=str(args.rank_model)),
        choice, datetime.now(JST), jra_archive=args.jra_archive)


if __name__ == "__main__":
    main()
