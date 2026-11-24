"""Calibrate the selected three-ticket event on a chronologically earlier cohort."""

import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

from research_longshot_wide_coherent import fit
from research_longshot_wide_portfolio import make_selections


MODELS = ("market", "coherentV2")
CALIBRATORS = ("raw", "intercept", "interceptSlope")


def event_rows(tickets):
    return tickets.groupby(["model", "policy", "raceID"], as_index=False).agg(
        date=("date", "first"), probability=("probabilityAnyHit", "first"), hit=("hit", "max"),
    )


def logits(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-8, 1 - 1e-8)
    return np.log(p / (1 - p))


def fit_calibrator(probability, hit, method):
    """Use a nondecreasing logit mapping and weak shrinkage toward the identity."""
    x = logits(probability)
    y = np.asarray(hit, dtype=float)
    if not 0 < y.sum() < len(y) or len(y) != len(x):
        raise ValueError("calibration requires both hit and miss observations")
    if method not in CALIBRATORS[1:]:
        raise ValueError("unknown probability calibrator")

    def objective(theta):
        offset, slope = theta
        z = offset + slope * x
        errors = expit(z) - y
        penalty = 0.001 * (slope - 1) ** 2 / 2
        return (
            np.mean(np.logaddexp(0, z) - y * z) + penalty,
            np.array([errors.mean(), (errors * x).mean() + 0.001 * (slope - 1)]),
        )

    bounds = [(None, None), (1.0, 1.0) if method == "intercept" else (0.0, None)]
    result = minimize(objective, [0., 1.], jac=True, bounds=bounds, method="L-BFGS-B")
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError("portfolio calibration did not converge")
    return {"offset": float(result.x[0]), "slope": float(result.x[1])}


def calibration_report(training, evaluation, bootstrap):
    train = event_rows(training)
    test = event_rows(evaluation)
    if not train["date"].dt.year.eq(2025).all() or not test["date"].dt.year.eq(2026).all():
        raise ValueError("calibration and evaluation periods drifted")
    reports = {}
    rows = []
    for (model, policy), check in test.groupby(["model", "policy"], sort=False):
        learn = train.loc[train["model"].eq(model) & train["policy"].eq(policy)]
        probs = check["probability"].to_numpy()
        hit = check["hit"].to_numpy(float)
        raw_loss = (probs - hit) ** 2
        stats = {}
        row = check.copy()
        for method in CALIBRATORS:
            coeff = {"offset": 0.0, "slope": 1.0}
            if method == "raw":
                p = probs
            else:
                coeff = fit_calibrator(learn["probability"], learn["hit"], method)
                p = expit(coeff["offset"] + coeff["slope"] * logits(probs))
            loss = (p - hit) ** 2
            z = logits(p)
            delta = loss - raw_loss
            stats[method] = {
                **coeff,
                "brier": float(loss.mean()),
                "logLoss": float(np.mean(np.logaddexp(0, z) - hit * z)),
                "meanProbability": float(p.mean()),
                "brierDeltaFromRaw": float(delta.mean()),
                "brierDeltaFromRaw95": bootstrap(check["date"].to_numpy(), delta, np.ones(len(delta))),
            }
            row[method] = p
        reports.setdefault(model, {})[policy] = {
            "calibrationRaces2025": len(learn), "evaluationRaces2026": len(check),
            "evaluationHitRate": float(hit.mean()), "methods": stats,
        }
        rows.append(row)
    return reports, pd.concat(rows, ignore_index=True)


def write_calibration(output, train, validation, validation_pairs, test, test_pairs, models, bootstrap):
    # Learn the September–December 2025 calibration from distributions fitted
    # on January–August, using the same V2 penalty as the locked research model.
    early, = fit(train, "coherentV2", [models["coherentV2"]["penalty"]])
    frozen_training = {"market": models["market"], "coherentV2": early}
    training = make_selections(validation, validation_pairs, frozen_training, model_names=MODELS)
    check = make_selections(test, test_pairs, models, model_names=MODELS)
    report, rows = calibration_report(training, check, bootstrap)
    output.mkdir(parents=True, exist_ok=True)
    result = {
        "version": "longshot-wide-three-hit-calibration-v1",
        "researchOnly": True,
        "calibrationWindow": ["2025-09-01", "2025-12-31"],
        "trainingBaseModelThrough": "2025-08-31",
        "evaluationYear": 2026,
        "evaluationBaseModelThrough": "2025-12-31",
        "results": report,
        "limitations": [
            "Historical final win odds and field, previously examined 2026 races.",
            "Calibration applies to the probability after selection. Ticket identities and payouts do not change.",
            "The calibration and evaluation distributions use the same model specification and different fit lengths.",
        ],
    }
    (output / "research.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    rows.to_csv(output / "calibrated_probabilities_2026.csv", index=False)
    print("WIDE_THREE_HIT_CALIBRATION_COMPLETE", flush=True)
    return result
