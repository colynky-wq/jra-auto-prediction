"""Compare exact three-ticket coverage with the three largest pair marginals."""

from functools import lru_cache
import json
from math import isqrt

import numpy as np
import pandas as pd

from research_longshot_wide_coherent import (
    combinations_for_field,
    outcome_probabilities,
    pair_marginals,
    probability_of_any,
)


POLICIES = ("pairRank", "maximumAnyHit")
MODELS = ("coherentV2", "coherentV7")
ROUNDING_TOLERANCE = 1e-12


@lru_cache(maxsize=None)
def closing_edges(field_size):
    """For two Wide pairs in one podium, return the third pair in that podium."""
    pairs, _, edges = combinations_for_field(field_size)
    closing = np.full((len(pairs), len(pairs)), -1, dtype=int)
    for i, j, k in ((0, 1, 2), (0, 2, 1), (1, 2, 0)):
        first, second, third = edges[:, i], edges[:, j], edges[:, k]
        closing[first, second] = closing[second, first] = third
    return closing


def maximum_any_three(race, outcomes, ranked_edges, marginals):
    """Exact search with upper bounds. Inputs are probabilities and candidate pairs."""
    if len(ranked_edges) < 3:
        raise ValueError("three distinct candidate pairs required")
    # Two distinct Wide hits intersect only when their union is one top-three set.
    field = (1 + isqrt(1 + 8 * race.pair_count)) // 2
    closing = closing_edges(field)
    intersections = np.zeros((race.pair_count, race.pair_count), dtype=float)
    for first, second in ((0, 1), (0, 2), (1, 2)):
        a, b = race.edges[:, first], race.edges[:, second]
        intersections[a, b] = intersections[b, a] = outcomes

    edges = ranked_edges
    probs = marginals[edges]
    best = float("-inf")
    chosen = None
    for i, first in enumerate(edges[:-2]):
        if probs[i] + probs[i + 1] + probs[i + 2] <= best + ROUNDING_TOLERANCE:
            break
        for j in range(i + 1, len(edges) - 1):
            second = edges[j]
            unadjusted = probs[i] + probs[j]
            if unadjusted + probs[j + 1] <= best + ROUNDING_TOLERANCE:
                break
            shared = intersections[first, second]
            first_two = unadjusted - shared
            # Coverage of a third ticket can increase the union by at most its marginal.
            end = min(len(edges), int(np.searchsorted(-probs, first_two - best - ROUNDING_TOLERANCE)))
            if end <= j + 1:
                continue
            third = edges[j + 1:end]
            # Three tickets hit together only for all three edges of one triangle.
            union = (
                first_two + probs[j + 1:end] - intersections[first, third]
                - intersections[second, third] + shared * (closing[first, second] == third)
            )
            k = int(np.argmax(union))
            candidate = float(union[k])
            if candidate > best + ROUNDING_TOLERANCE:
                chosen = np.array([first, second, third[k]])
                best = candidate

    measured = probability_of_any(race.edges, outcomes, chosen)
    if not np.isclose(measured, best, atol=ROUNDING_TOLERANCE, rtol=0):
        raise ValueError("portfolio search and top-three outcome coverage disagree")
    return chosen


def make_selections(races, frame, models, *, model_names=MODELS):
    selection_rows = []
    for race in races:
        if race.observed is None:
            raise ValueError("observed result required for settling portfolio research")
        pair_rows = frame.iloc[race.pair_rows]
        edge_to_row = dict(zip(race.candidate_edges, race.pair_rows, strict=True))
        race_id = pair_rows["raceID"].iloc[0]
        winners = race.edges[race.observed]
        for name in model_names:
            outcomes = outcome_probabilities(race, models[name])
            marginals = pair_marginals(race, outcomes)
            ranked = race.candidate_edges[np.argsort(-marginals[race.candidate_edges], kind="stable")]
            baseline = ranked[:3]
            optimized = maximum_any_three(race, outcomes, ranked, marginals)
            baseline_prob = probability_of_any(race.edges, outcomes, baseline)
            optimized_prob = probability_of_any(race.edges, outcomes, optimized)
            if optimized_prob + ROUNDING_TOLERANCE < baseline_prob:
                raise ValueError("optimized portfolio is worse than the available baseline")
            for policy, edges, chance in zip(POLICIES, (baseline, optimized), (baseline_prob, optimized_prob), strict=True):
                for pick, edge in enumerate(edges, start=1):
                    selection_rows.append({
                        "raceID": race_id, "date": race.day, "model": name, "policy": policy,
                        "ticket": pick, "pairRow": edge_to_row[edge], "probabilityAnyHit": chance,
                        "pairProbability": marginals[edge], "hit": bool(edge in winners),
                    })
    return pd.DataFrame.from_records(selection_rows)


def compare_models(tickets, bootstrap):
    out = {}
    for model, subset in tickets.groupby("model", sort=False):
        out[model] = {}
        reports = {}
        for policy, chosen in subset.groupby("policy", sort=False):
            portfolio = chosen.groupby("raceID").agg(
                date=("date", "first"), hits=("hit", "sum"), predicted=("probabilityAnyHit", "first"),
            )
            portfolio["hadHit"] = portfolio["hits"].gt(0).astype(int)
            portfolio["brier"] = np.square(portfolio["predicted"] - portfolio["hadHit"])
            reports[policy] = portfolio
            row = out[model][policy] = {
                "races": len(portfolio),
                "racesHit": int(portfolio["hadHit"].sum()),
                "racesWithMultipleHits": int(portfolio["hits"].ge(2).sum()),
                "ticketsHit": int(portfolio["hits"].sum()),
                "observedHitRate": float(portfolio["hadHit"].mean()),
                "meanPredictedProbabilityAnyHit": float(portfolio["predicted"].mean()),
                "brierAnyHit": float(portfolio["brier"].mean()),
            }
            if "payout" in subset:
                payout = chosen.groupby("raceID")["payout"].sum()
                row.update(
                    stakeYen=300 * len(portfolio), payoutYen=int(payout.sum()),
                    returnPct=float(payout.mean() / 3),
                    torigamiRaces=int(((payout > 0) & (payout < 300)).sum()),
                    roi95=bootstrap(portfolio["date"], payout, np.full(len(portfolio), 3)),
                )
                portfolio["payout"] = payout
        base, optimized = (reports[name] for name in POLICIES)
        if not base.index.equals(optimized.index):
            raise ValueError("portfolio policies have unequal race coverage")
        change = optimized["hadHit"] - base["hadHit"]
        brier_change = optimized["brier"] - base["brier"]
        changed_picks = (
            subset.groupby(["raceID", "pairRow"]).size().eq(1)
            .groupby(level="raceID").any()
        )
        delta = out[model]["maximumMinusRank"] = {
            "racesWithDifferentTickets": int(changed_picks.sum()),
            "hitOnlyMaximum": int(change.gt(0).sum()),
            "hitOnlyRank": int(change.lt(0).sum()),
            "hitRateDifference": float(change.mean()),
            "hitRateDifference95": bootstrap(base["date"], change, np.ones(len(base))),
            "predictedDifference": float((optimized["predicted"] - base["predicted"]).mean()),
            "brierDifference": float(brier_change.mean()),
            "brierDifference95": bootstrap(base["date"], brier_change, np.ones(len(base))),
        }
        if "payout" in subset:
            payoff_change = optimized["payout"] - base["payout"]
            delta["returnPctDifference"] = float(payoff_change.mean() / 3)
            delta["returnPctDifference95"] = bootstrap(base["date"], payoff_change, np.full(len(base), 3))
    return out


def write_comparison(output, races, frame, verified, models, bootstrap):
    tickets = make_selections(races, frame, models)
    keys = frame.loc[tickets["pairRow"], ["a", "b"]].reset_index(drop=True)
    tickets = tickets.join(keys)
    lookup = verified.set_index(["raceID", "a", "b"])["payout"]
    settled = tickets.join(lookup, on=["raceID", "a", "b"])
    # Require settlement for every selected ticket across both policies and both models.
    complete = settled.groupby("raceID")["payout"].transform(lambda p: p.notna().all())
    settled = settled.loc[complete].copy()
    report = {
        "version": "longshot-wide-three-ticket-selection-v1",
        "researchOnly": True,
        "candidatePromotionApproved": False,
        "primaryModel": "coherentV2",
        "policies": {
            "pairRank": "Three largest individual Wide pair probabilities.",
            "maximumAnyHit": "Three pairs of which at least one is most likely to win under the same model.",
        },
        "historical2026": compare_models(tickets, bootstrap),
        "verifiedPayoutCommonSupport": compare_models(settled, bootstrap),
        "verifiedArchiveRaces": int(verified["raceID"].nunique()),
        "verifiedPayoutCommonRaces": int(settled["raceID"].nunique()),
        "limitations": [
            "Historical final win odds and final fields, with no live Wide odds used in selection.",
            "Three fixed hypothetical stakes of 100 yen. No actual wagering.",
            "Only verified archived payouts on a race set shared by all four combinations.",
            "Previously examined 2026 races. No model tuning or prospective promotion in this comparison.",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "research.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    tickets.drop(columns="pairRow").to_csv(output / "portfolio_tickets_2026.csv", index=False)
    settled.drop(columns="pairRow").to_csv(output / "portfolio_verified_payouts_2026.csv", index=False)
    print("WIDE_PORTFOLIO_SELECTION_COMPLETE", json.dumps({
        "primary": report["historical2026"]["coherentV2"],
        "payoutRaces": report["verifiedPayoutCommonRaces"],
    }), flush=True)
    return report
