"""Four-way replay from immutable T-10 evidence and optional fresh JRA Wide odds."""

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd

from aligned_portfolio_review import read, wide_results
from all_bet_baseline import result_state
from all_bet_forward import validate as validate_win_record
from build_bet_research_data import checked_results
from formal_portfolio_capture import stamp, window
from jra_wide_research_archive import read_record, verify_snapshot
from research_longshot_wide_coherent import outcome_probabilities, pair_marginals, probability_of_any
from research_longshot_wide_odds_replay import frozen_coherent_field, prices, scan_archive
from research_longshot_wide_portfolio import maximum_any_three, POLICIES, compare_models
from research_longshot_wide_joint import bootstrap
from train_longshot_top3 import JST, ROOT, odds_underdog


MODELS = ("market", "coherentV2")
COLUMNS = [
    "raceID", "date", "model", "policy", "ticket", "pairRow", "a", "b",
    "pairProbability", "probabilityAnyHit", "hit", "payout",
]


def select_cards(field, models, pair_numbers, quotes=None):
    selections = {}
    for name in MODELS:
        outcomes = outcome_probabilities(field, models[name])
        marginals = pair_marginals(field, outcomes)
        candidates = field.candidate_edges
        if quotes is not None:
            candidates = np.array([
                i for i in candidates
                if quotes[tuple(pair_numbers[i])][0] >= 3
                and marginals[i] * quotes[tuple(pair_numbers[i])][0] >= 1
            ], dtype=int)
        ranked = candidates[np.argsort(-marginals[candidates], kind="stable")]
        if len(ranked) < 3:
            picks = ([], [])
        else:
            picks = (ranked[:3], maximum_any_three(field, outcomes, ranked, marginals))

        for policy, edges in zip(POLICIES, picks, strict=True):
            chance = probability_of_any(field.edges, outcomes, edges)
            selections[name, policy] = (chance, [(i, float(marginals[i])) for i in edges])
    return selections


def frozen_inputs(path, now):
    day8, rid = path.parents[1].name, path.stem
    day = datetime.strptime(day8, "%Y%m%d").date().isoformat()
    saved = read(path)
    win = validate_win_record(read(path.parents[1] / "all_bet_v1" / "win" / path.name))
    race = win["prediction"]
    if (win["betType"] != "win" or race["raceID"] != rid or race["raceDate"] != day
            or race["captureQuality"] != "formal_10min" or race["predictionTiming"] != "live_10min"):
        raise ValueError("invalid frozen formal win prediction")
    start = race["startTime"]
    captured = window(day, start, race["capturedAtJST"])
    available = window(day, start, win["predictionAvailableAtJST"])
    written = window(day, start, win["recordedAtJST"])
    frozen = max(written, window(day, start, saved["recordedAtJST"]))
    if not captured <= available <= written <= frozen <= now:
        raise ValueError("frozen pre-post evidence clock mismatch")

    horses = sorted(race["horses"], key=lambda h: h["number"])
    longshots = odds_underdog([h["popularity"] for h in horses], [h["odds"] for h in horses])
    if len(horses) < 8 or not longshots.any():
        raise ValueError("race outside complete eight-plus-runner longshot cohort")
    field, pairs = frozen_coherent_field(race, horses, longshots, saved)
    return race, frozen, field, pairs


def ticket_rows(cards, race, pairs, finish, payouts):
    winners = set(finish)
    output = []
    for (model, policy), (chance, chosen) in cards.items():
        for n, (i, probability) in enumerate(chosen, start=1):
            a, b = pairs[i]
            hit = {a, b} <= winners
            settled = payouts.get(f"{a:02}{b:02}")
            if settled is not None and settled[0] != hit:
                raise ValueError("Wide payout hit identity mismatch")
            output.append({
                "raceID": race["raceID"], "date": pd.Timestamp(race["raceDate"]), "model": model, "policy": policy,
                "ticket": n, "pairRow": i, "a": a, "b": b, "pairProbability": probability,
                "probabilityAnyHit": chance, "hit": hit,
                "payout": settled[1] if settled is not None else None,
            })
    return output


def quote_summary(tickets, eligible_races):
    rows = []
    for model in MODELS:
        for policy in POLICIES:
            part = tickets.loc[tickets["model"].eq(model) & tickets["policy"].eq(policy)]
            g = part.groupby("raceID").agg(payout=("payout", "sum"), hit=("hit", "max"))
            stake = 100 * len(part)
            rows.append({
                "model": model, "policy": policy, "eligibleRaces": eligible_races,
                "playedRaces": len(g), "skippedRaces": eligible_races - len(g),
                "racesHit": int(g["hit"].sum()), "stakeYen": stake,
                "payoutYen": int(g["payout"].sum()),
                "returnPct": float(100 * g["payout"].sum() / stake) if stake else None,
                "torigamiRaces": int(((g["payout"] > 0) & (g["payout"] < 300)).sum()),
            })
    return rows


def run(root, output, models, now, *, jra_archive=None):
    archive = sorted(jra_archive.glob("*/*.json")) if jra_archive is not None else []
    official_by_key = {(path.parent.name, path.stem): path for path in archive}
    states, quote_states = Counter(), Counter()
    history_rows, priced_rows = [], []
    eligible_priced = 0
    results_by_day = {}
    audits = []
    paths = sorted(root.glob("reports/*/longshot_top3_v2/*.json"))
    for path in paths:
        day8, rid = path.parents[1].name, path.stem
        audit = {"raceID": rid, "date": day8, "status": "invalid", "officialWideStatus": "missing"}
        try:
            race, frozen, field, pairs = frozen_inputs(path, now)
            cards = select_cards(field, models, pairs)
            day = race["raceDate"]

            fresh_cards = None
            if source := official_by_key.get((day8, rid)):
                audit["officialWideStatus"] = "invalid"
                try:
                    record = read_record(source, day8, rid)
                    quote = record["snapshot"]
                    ready = max(frozen, stamp(record["recordedAtJST"]))
                    if ready > now or stamp(quote["scheduledPostJST"]) != stamp(f'{day}T{race["startTime"]}'):
                        raise ValueError("official Wide schedule or decision clock mismatch")
                    age = verify_snapshot(quote, day8, rid, ready)
                    available = prices(quote)
                    if set(available) != set(map(tuple, pairs)):
                        raise ValueError("official Wide price roster differs from frozen prediction")
                    fresh_cards = select_cards(field, models, pairs, available)
                    audit["officialWideStatus"] = "verifiedAtDecision"
                    audit["wideSourceAgeSeconds"] = age
                except (KeyError, ValueError, OSError, TypeError) as exc:
                    audit["officialWideReason"] = str(exc)

            if day not in results_by_day:
                results_by_day[day] = checked_results(path.parents[1] / "ResultData.json", day, now)
            state, finish = result_state(race, results_by_day[day].get((day, rid)))
            audit["status"] = state
            if state != "matched":
                continue
            payouts = wide_results(root, race, finish)
            history_rows.extend(ticket_rows(cards, race, pairs, finish, payouts))
            if fresh_cards is not None:
                if payouts:
                    eligible_priced += 1
                    priced_rows.extend(ticket_rows(fresh_cards, race, pairs, finish, payouts))
                else:
                    audit["officialWideStatus"] = "awaitingPayout"
        except (KeyError, ValueError, OSError, TypeError) as exc:
            audit.update(status="invalid", reason=str(exc))
        finally:
            states[audit["status"]] += 1
            quote_states[audit["officialWideStatus"]] += 1
            audits.append(audit)

    history = pd.DataFrame(history_rows, columns=COLUMNS)
    priced = pd.DataFrame(priced_rows, columns=COLUMNS)
    settled = history.loc[history.groupby("raceID")["payout"].transform(lambda p: p.notna().all())]
    odds_audit = scan_archive(root)
    report = {
        "version": "longshot-wide-four-way-t10-v1",
        "researchOnly": True,
        "frozenInputRaces": len(paths),
        "matchStates": dict(states),
        "matchedRaceDays": int(history["date"].nunique()),
        "fourWayHitRateWithFrozenInputs": compare_models(history.drop(columns="payout"), bootstrap),
        "fourWayArchivedPayouts": compare_models(settled, bootstrap),
        "officialWide": {
            "archivedSnapshots": len(archive), "predictionMatchedSnapshotStates": dict(quote_states),
            "freshAndPayoutSettledRaces": eligible_priced, "results": quote_summary(priced, eligible_priced),
        },
        "legacyWideTimestampAudit": {
            "snapshots": len(odds_audit),
            "states": dict(Counter(row["state"] for row in odds_audit)),
        },
        "limitations": [
            "The source inputs were saved before post; this analysis generates ticket selections retrospectively.",
            "The payout figures without fresh Wide odds use probability-only selection.",
            "Fresh price selection requires the odds lower bound >= 3 and marginal probability times lower bound >= 1.",
            "Both policies abstain when fewer than three prices qualify; each selected ticket stakes 100 yen.",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "research.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (output / "race_audit.json").write_text(json.dumps(audits, ensure_ascii=False, indent=2) + "\n")
    history.drop(columns="pairRow").to_csv(output / "tickets_t10.csv", index=False)
    priced.drop(columns="pairRow").to_csv(output / "tickets_jra_fresh.csv", index=False)
    print("WIDE_FOUR_WAY_T10_COMPLETE", json.dumps({
        "states": dict(states), "freshAndPayoutSettledRaces": eligible_priced,
    }), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coherent-models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jra-archive", type=Path)
    args = parser.parse_args()
    if args.jra_archive is not None and not args.jra_archive.is_dir():
        parser.error("--jra-archive must be an existing directory")
    run(ROOT, args.output, read(args.coherent_models), datetime.now(JST), jra_archive=args.jra_archive)


if __name__ == "__main__":
    main()
