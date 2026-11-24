"""Learn an unordered top-three distribution and derive wide probabilities."""

from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
import math

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from train_longshot_top3 import odds_underdog


SPECS = {
    "temperature": ("marketLogMass",),
    "coherentV2": (
        "marketLogMass", "longshots", "residualSum", "residualMin",
        "residualMax", "residualProduct",
    ),
    "coherentV7": (
        "marketLogMass", "longshots", "residualSum", "residualMin",
        "residualMax", "residualProduct", "rankSum", "rankMin", "rankMax",
    ),
}
PENALTIES = (0.001, 0.01, 0.1)
EDGE_FEATURES = SPECS["coherentV7"][2:]


@lru_cache(maxsize=None)
def combinations_for_field(n):
    pairs = np.asarray(list(combinations(range(n), 2)))
    triples = np.asarray(list(combinations(range(n), 3)))
    lookup = np.zeros((n, n), dtype=int)
    lookup[pairs[:, 0], pairs[:, 1]] = np.arange(len(pairs))
    a, b, c = triples.T
    edges = np.column_stack((lookup[a, b], lookup[a, c], lookup[b, c]))
    return pairs, triples, edges


def market_triples(weights, triples):
    """Sum the Plackett–Luce mass of the six orders of each top-three set."""
    weights = np.asarray(weights, dtype=float)
    if not np.isfinite(weights).all() or (weights <= 0).any():
        raise ValueError("market weights must be positive and finite")
    q = weights / weights.sum()
    a, b, c = q[triples].T

    def first_two_mass(x, y):
        return (1.0 / (1.0 - x) + 1.0 / (1.0 - y)) / (1.0 - x - y)

    mass = a * b * c * (first_two_mass(a, b) + first_two_mass(a, c) + first_two_mass(b, c))
    if not np.isclose(mass.sum(), 1.0, atol=1e-10, rtol=0) or (mass <= 0).any():
        raise ValueError("invalid market triple probability")
    return mass / mass.sum()


@dataclass
class Race:
    day: pd.Timestamp
    features: np.ndarray
    edges: np.ndarray
    observed: int | None
    pair_rows: np.ndarray
    candidate_edges: np.ndarray
    pair_count: int


def triple_features(weights, longshot, candidate_edges, values, triples, edges, pair_count):
    market = market_triples(weights, triples)
    edge_features = np.zeros((pair_count, len(EDGE_FEATURES)))
    edge_features[candidate_edges] = values
    features = np.column_stack((
        np.log(market), longshot[triples].sum(axis=1),
        edge_features[edges].sum(axis=1),
    ))
    return features, market


def build_races(full, candidates):
    """Use the identical complete fields and pair identities as the wide baseline."""
    races = []
    groups = candidates.reset_index(drop=True).groupby("raceID", sort=False)
    full_by_id = full.loc[full["レースID"].astype(str).isin(groups.groups)]
    by_id = full_by_id.groupby(full_by_id["レースID"].astype(str), sort=False)
    for race_id, rows in groups:
        field = by_id.get_group(race_id).sort_values("馬番")
        horse_numbers = field["馬番"].to_numpy(int)
        ranks = field["着順"].to_numpy(int)
        n = len(field)
        pairs, triples, edges = combinations_for_field(n)
        pair_index = {tuple(pair): i for i, pair in enumerate(horse_numbers[pairs])}
        candidate_edges = np.array([pair_index[pair] for pair in zip(rows["a"], rows["b"])])
        longshot = odds_underdog(field["人気"], field["単勝オッズ"])
        if (
            not np.array_equal(candidate_edges, np.flatnonzero(longshot[pairs].any(axis=1)))
            or not np.array_equal(longshot[pairs[candidate_edges]].sum(axis=1), rows["longshots"])
        ):
            raise ValueError(f"longshot identities disagree: {race_id}")

        features, market = triple_features(
            field["市場確率"], longshot, candidate_edges, rows[list(EDGE_FEATURES)].to_numpy(),
            triples, edges, len(pairs),
        )
        observed = np.flatnonzero((ranks[triples] <= 3).all(axis=1))
        if len(observed) != 1:
            raise ValueError(f"expected one observed top-three set: {race_id}")
        race = Race(rows["date"].iloc[0], features, edges, int(observed[0]),
                    rows.index.to_numpy(), candidate_edges, len(pairs))
        marginal = pair_marginals(race, market)[candidate_edges]
        if not np.allclose(marginal, rows["market"], atol=1e-10, rtol=0):
            raise ValueError(f"market baseline disagrees: {race_id}")
        if not np.array_equal(np.isin(candidate_edges, edges[race.observed]), rows["hit"]):
            raise ValueError(f"pair outcome disagrees: {race_id}")
        races.append(race)
    return races


def softmax_by_race(log_weights, starts, sizes):
    maximum = np.maximum.reduceat(log_weights, starts)
    shifted = log_weights - np.repeat(maximum, sizes)
    weights = np.exp(shifted)
    sums = np.add.reduceat(weights, starts)
    return weights / np.repeat(sums, sizes), maximum + np.log(sums)


def fit(races, spec, penalties):
    if not races or any(race.day.year != 2025 for race in races):
        raise ValueError("coherent training is restricted to 2025 races")
    feature_count = len(SPECS[spec])
    raw = np.concatenate([race.features[:, :feature_count] for race in races])
    mean = raw.mean(axis=0)
    scale = np.maximum(raw.std(axis=0), 1e-8)
    base = raw[:, 0].copy()
    x = (raw - mean) / scale
    sizes = np.asarray([len(race.features) for race in races])
    starts = np.r_[0, sizes.cumsum()[:-1]]
    winners = starts + np.asarray([race.observed for race in races])
    actual = x[winners].mean(axis=0)

    def objective(beta, penalty):
        logits = base + x @ beta
        p, log_normalizer = softmax_by_race(logits, starts, sizes)
        loss = (log_normalizer - logits[winners]).mean() + 0.5 * penalty * (beta @ beta)
        gradient = x.T @ p / len(races) - actual + penalty * beta
        return loss, gradient

    models = []
    for penalty in penalties:
        result = minimize(
            objective, np.zeros(feature_count), args=(penalty,), jac=True, method="L-BFGS-B",
            options={"ftol": 1e-11},
        )
        if not result.success or not np.isfinite(result.x).all():
            raise ValueError(f"coherent optimization failed: {spec}, {penalty}: {result.message}")
        models.append({
            "spec": spec, "penalty": penalty, "mean": mean.tolist(),
            "scale": scale.tolist(), "coefficients": result.x.tolist(),
        })
    return models


def outcome_probabilities(race, model):
    log_weights = race.features[:, 0]
    if model["spec"] != "market":
        x = (race.features[:, :len(model["mean"])] - model["mean"]) / model["scale"]
        log_weights = log_weights + x @ model["coefficients"]
    weights = np.exp(log_weights - log_weights.max())
    return weights / weights.sum()


def pair_marginals(race, probabilities):
    return np.bincount(
        race.edges.ravel(), weights=np.repeat(probabilities, 3), minlength=race.pair_count,
    )


def probability_of_any(edges, probabilities, selected_edges):
    return float(probabilities[np.isin(edges, selected_edges).any(axis=1)].sum())


def scores(races, model, frame, evaluate_tickets=False):
    predicted = np.empty(len(frame)) if evaluate_tickets else None
    race_losses, triple_losses, tickets = [], [], {1: [], 3: []}
    hits = frame["hit"].to_numpy()
    # Keep the baseline's numerical tie-breaking when choosing the same market tickets.
    market_ranking = frame["market"].to_numpy()
    for race in races:
        outcomes = outcome_probabilities(race, model)
        pair_prob = pair_marginals(race, outcomes)[race.candidate_edges]
        actual = hits[race.pair_rows]
        race_losses.append(np.square(pair_prob - actual).mean())
        triple_losses.append(-math.log(max(float(outcomes[race.observed]), 1e-300)))
        if evaluate_tickets:
            predicted[race.pair_rows] = pair_prob
            # Candidate rows are in ascending horse number order, including after slicing.
            ranking = market_ranking[race.pair_rows] if model["spec"] == "market" else pair_prob
            ranked = np.argsort(-ranking, kind="stable")
            for k, sample in tickets.items():
                pick = ranked[:k]
                p_any = probability_of_any(race.edges, outcomes, race.candidate_edges[pick])
                sample.append((p_any, int(actual[pick].any())))

    result = {
        "equalRaceBrier": float(np.mean(race_losses)),
        "meanTopThreeSetLogLoss": float(np.mean(triple_losses)),
    }
    if evaluate_tickets:
        result["portfolio"] = {}
        for k, sample in tickets.items():
            p, y = np.asarray(sample).T
            result["portfolio"][str(k)] = {
                "races": len(y), "racesHit": int(y.sum()), "observedHitRate": float(y.mean()),
                "meanPredictedProbabilityAnyHit": float(p.mean()),
                "brierAnyHit": float(np.square(p - y).mean()),
            }
    return result, predicted


def evaluate(full25, full26, pairs25, pairs26, *, calibration_output=None, bootstrap=None):
    cutoff = pd.Timestamp("2025-09-01")
    train_pairs = pairs25.loc[pairs25["date"].lt(cutoff)]
    validation_pairs = pairs25.loc[pairs25["date"].ge(cutoff)]
    train = build_races(full25, train_pairs)
    validation = build_races(full25, validation_pairs)
    market = {"spec": "market", "penalty": None}
    grid = []
    for spec in ("market", *SPECS):
        models = [market] if spec == "market" else fit(train, spec, PENALTIES)
        for model in models:
            quality, _ = scores(validation, model, validation_pairs)
            grid.append({"spec": spec, "penalty": model["penalty"], **quality})
    selected = min(grid, key=lambda r: (
        r["equalRaceBrier"], len(SPECS.get(r["spec"], ())), r["penalty"] or 0,
    ))
    all_training = train + validation
    final = {"market": market}
    for spec in SPECS:
        best = min((row for row in grid if row["spec"] == spec), key=lambda r: r["equalRaceBrier"])
        final[spec], = fit(all_training, spec, [best["penalty"]])

    test = build_races(full26, pairs26)
    quality = {}
    for name, model in final.items():
        quality[name], p = scores(test, model, pairs26, evaluate_tickets=True)
        if name != "market":
            pairs26[name] = p
    if calibration_output is not None:
        from research_wide_portfolio_calibration import write_calibration

        write_calibration(calibration_output, train, validation, validation_pairs,
                          test, pairs26, final, bootstrap)
    return final, {
        "selected2025": selected,
        "grid2025": grid,
        "trainingRaces2025BeforeSeptember": len(train),
        "selectionRaces2025FromSeptember": len(validation),
        "coherentDistribution2026": quality,
    }, test
