from __future__ import annotations

import itertools
import numpy as np


def _stats_from_theta(theta: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Distribution over unordered Top-3 sets:
        P({i,j,k}) proportional to exp(theta_i + theta_j + theta_k)

    Returns horse Top3 marginals, pair (wide) marginals, and normalized
    triple probabilities in lexicographic combination order.
    """
    th = np.asarray(theta, dtype=float)
    n = len(th)
    if n < 3:
        raise ValueError("at least 3 horses are required")

    th = th - np.max(th)
    w = np.exp(np.clip(th, -50.0, 50.0))

    triples = list(itertools.combinations(range(n), 3))
    mass = np.empty(len(triples), dtype=float)
    for t, (i, j, k) in enumerate(triples):
        mass[t] = w[i] * w[j] * w[k]

    z = float(mass.sum())
    if not np.isfinite(z) or z <= 0:
        raise RuntimeError("invalid Top3 set normalizer")

    triple_prob = mass / z
    marginal = np.zeros(n, dtype=float)
    pair = np.zeros((n, n), dtype=float)

    for p, (i, j, k) in zip(triple_prob, triples):
        marginal[i] += p
        marginal[j] += p
        marginal[k] += p
        pair[i, j] += p
        pair[j, i] += p
        pair[i, k] += p
        pair[k, i] += p
        pair[j, k] += p
        pair[k, j] += p

    return marginal, pair, triple_prob


def fit_top3_set_distribution(
    target_top3: np.ndarray,
    *,
    tolerance: float = 1e-8,
    max_iter: int = 60,
) -> dict[str, np.ndarray | float | int]:
    """
    Fit a coherent probability distribution over all unordered Top-3 sets
    whose horse-level inclusion probabilities match target_top3.

    target_top3 must be strictly between 0 and 1 and sum to 3.
    Newton updates use the exact covariance Hessian of the fixed-size
    exponential-family distribution. The model is unique up to a common
    additive shift in theta; theta is centered after each update.
    """
    target = np.asarray(target_top3, dtype=float)
    n = len(target)

    if n < 3:
        raise ValueError("at least 3 horses are required")
    if not np.isfinite(target).all():
        raise ValueError("target_top3 contains NaN/inf")
    if (target <= 0).any() or (target >= 1).any():
        raise ValueError("target_top3 must be strictly between 0 and 1")
    if abs(float(target.sum()) - 3.0) > 1e-6:
        raise ValueError(f"target_top3 must sum to 3, got {target.sum():.12f}")

    theta = np.log(np.clip(target / (1.0 - target), 1e-12, 1e12))
    theta -= theta.mean()

    marginal = np.zeros(n, dtype=float)
    pair = np.zeros((n, n), dtype=float)
    triple_prob = np.array([], dtype=float)

    for iteration in range(1, max_iter + 1):
        marginal, pair, triple_prob = _stats_from_theta(theta)
        gradient = marginal - target
        error = float(np.max(np.abs(gradient)))

        if error <= tolerance:
            break

        hessian = pair - np.outer(marginal, marginal)
        np.fill_diagonal(hessian, marginal * (1.0 - marginal))

        # The Hessian has one null direction (adding a constant to all theta).
        # pinv gives the minimum-norm update and is stable for small race fields.
        direction = np.linalg.pinv(hessian, rcond=1e-10) @ gradient

        accepted = False
        step = 1.0
        while step >= 1e-4:
            candidate = theta - step * direction
            candidate -= candidate.mean()
            candidate_marginal, _, _ = _stats_from_theta(candidate)
            candidate_error = float(np.max(np.abs(candidate_marginal - target)))
            if candidate_error < error:
                theta = candidate
                accepted = True
                break
            step *= 0.5

        if not accepted:
            # Safe fallback if Newton line-search stalls.
            theta = theta - 0.1 * gradient
            theta -= theta.mean()

    marginal, pair, triple_prob = _stats_from_theta(theta)
    final_error = float(np.max(np.abs(marginal - target)))

    return {
        "theta": theta,
        "top3": marginal,
        "pair": pair,
        "triple": triple_prob,
        "max_error": final_error,
        "iterations": iteration,
        "pair_sum": float(np.triu(pair, 1).sum()),
    }


def pair_rows(
    horse_numbers: np.ndarray,
    pair_probability: np.ndarray,
) -> list[dict[str, float | int]]:
    numbers = np.asarray(horse_numbers, dtype=int)
    p = np.asarray(pair_probability, dtype=float)
    if p.shape != (len(numbers), len(numbers)):
        raise ValueError("pair_probability shape mismatch")

    rows: list[dict[str, float | int]] = []
    for i in range(len(numbers) - 1):
        for j in range(i + 1, len(numbers)):
            rows.append(
                {
                    "horse1": int(numbers[i]),
                    "horse2": int(numbers[j]),
                    "probability": float(p[i, j]),
                }
            )
    return rows
