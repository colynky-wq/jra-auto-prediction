from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF


def sigmoid(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))


def logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(np.asarray(p, dtype=float), 1e-9, 1.0 - 1e-9)
    return np.log(q / (1.0 - q))


def market_top2_top3(market_win: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Exact Plackett-Luce Top2/Top3 marginals for one race."""
    q = np.asarray(market_win, dtype=float)
    if q.ndim != 1 or len(q) < 3:
        raise ValueError("market_win must contain at least 3 horses")
    if not np.isfinite(q).all() or (q <= 0).any() or q.sum() <= 0:
        raise ValueError("market_win contains invalid probabilities")

    q = q / q.sum()
    n = len(q)

    first = q[:, None]
    target = q[None, :]
    den2 = 1.0 - first
    term2 = np.divide(
        first * target,
        den2,
        out=np.zeros((n, n), dtype=float),
        where=den2 > 1e-15,
    )
    np.fill_diagonal(term2, 0.0)
    rank2 = term2.sum(axis=0)

    qj = q[:, None, None]
    qk = q[None, :, None]
    qi = q[None, None, :]
    den_after_j = 1.0 - qj
    den_after_jk = 1.0 - qj - qk
    term3 = np.divide(
        qj * qk * qi,
        den_after_j * den_after_jk,
        out=np.zeros((n, n, n), dtype=float),
        where=(den_after_j > 1e-15) & (den_after_jk > 1e-15),
    )

    idx = np.arange(n)
    j = idx[:, None, None]
    k = idx[None, :, None]
    i = idx[None, None, :]
    distinct = (j != k) & (j != i) & (k != i)
    term3 = np.where(distinct, term3, 0.0)
    rank3 = term3.sum(axis=(0, 1))

    top2 = np.clip(q + rank2, 1e-9, 1.0 - 1e-9)
    top3 = np.clip(q + rank2 + rank3, 1e-9, 1.0 - 1e-9)
    return top2, top3


def normalize_probability_total(raw_prob: np.ndarray, target_total: int) -> np.ndarray:
    """Common-logit shift so probabilities sum to target_total exactly."""
    p = np.clip(np.asarray(raw_prob, dtype=float), 1e-9, 1.0 - 1e-9)
    if p.ndim != 1:
        raise ValueError("raw_prob must be one-dimensional")
    n = len(p)
    if n <= target_total:
        return np.ones(n, dtype=float)

    z = logit(p)
    lo, hi = -30.0, 30.0
    total = float(target_total)

    for _ in range(80):
        mid = (lo + hi) / 2.0
        s = float(sigmoid(z + mid).sum())
        if s < total:
            lo = mid
        else:
            hi = mid

    return sigmoid(z + (lo + hi) / 2.0)


def _load_model(model_path: Path) -> lgb.Booster:
    if not model_path.exists():
        raise FileNotFoundError(model_path)
    model = lgb.Booster(model_file=str(model_path))
    if list(model.feature_name()) != list(ALLF):
        raise RuntimeError(f"model feature order mismatch: {model_path.name}")
    return model


def _predict_one(
    X: pd.DataFrame,
    baseline: np.ndarray,
    model_path: Path,
    target_total: int,
) -> np.ndarray:
    model = _load_model(model_path)
    correction = model.predict(X, raw_score=True, validate_features=True)
    pre = sigmoid(logit(baseline) + correction)
    return normalize_probability_total(pre, target_total)


def predict_top2_top3(
    X: pd.DataFrame,
    market_win: np.ndarray,
    top2_model_path: Path,
    top3_model_path: Path,
) -> dict[str, np.ndarray]:
    """Return market and AI Top2/Top3 probabilities for one race."""
    if list(X.columns) != list(ALLF):
        raise RuntimeError("X feature order differs from feature_engine.ALLF")
    if len(X) != len(market_win):
        raise ValueError("X and market_win row counts differ")
    if not np.isfinite(X.to_numpy(dtype=float)).all():
        raise ValueError("X contains NaN/inf")

    market2, market3 = market_top2_top3(market_win)
    ai2 = _predict_one(X, market2, Path(top2_model_path), 2)
    ai3 = _predict_one(X, market3, Path(top3_model_path), 3)

    return {
        "market_top2": market2,
        "market_top3": market3,
        "ai_top2": ai2,
        "ai_top3": ai3,
    }
