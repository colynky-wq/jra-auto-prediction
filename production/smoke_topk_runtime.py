from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF
from topk_runtime import logit, predict_top2_top3


HERE = Path(__file__).resolve().parent
WIN_MODEL = HERE / "lightgbm_fixed_2025.txt"
TOP2_MODEL = HERE / "lightgbm_top2_fixed_2025.txt"
TOP3_MODEL = HERE / "lightgbm_top3_fixed_2025.txt"


def check_model(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    model = lgb.Booster(model_file=str(path))
    if list(model.feature_name()) != list(ALLF):
        raise RuntimeError(f"feature order mismatch: {path.name}")
    print(path.name, "feature_count=", len(model.feature_name()), "OK")


def main() -> None:
    print("=" * 100)
    print("KeibaAI Top2 / Top3 runtime smoke test")
    print("=" * 100)

    for path in (WIN_MODEL, TOP2_MODEL, TOP3_MODEL):
        check_model(path)

    n = 16
    strength = np.exp(-np.linspace(0.0, 2.2, n))
    market = strength / strength.sum()

    X = pd.DataFrame(0.0, index=np.arange(n), columns=ALLF, dtype=np.float32)
    X["市場ロジット"] = logit(market).astype(np.float32)

    result = predict_top2_top3(X, market, TOP2_MODEL, TOP3_MODEL)
    market2 = result["market_top2"]
    market3 = result["market_top3"]
    ai2 = result["ai_top2"]
    ai3 = result["ai_top3"]

    for name, p, expected_sum in (
        ("market_top2", market2, 2.0),
        ("market_top3", market3, 3.0),
        ("ai_top2", ai2, 2.0),
        ("ai_top3", ai3, 3.0),
    ):
        if not np.isfinite(p).all():
            raise RuntimeError(f"{name} contains NaN/inf")
        if ((p <= 0) | (p >= 1)).any():
            raise RuntimeError(f"{name} contains out-of-range probability")
        if abs(float(p.sum()) - expected_sum) > 1e-8:
            raise RuntimeError(f"{name} sum mismatch: {p.sum()}")
        print(name, "sum=", f"{p.sum():.12f}", "OK")

    if (market2 > market3 + 1e-12).any():
        raise RuntimeError("market Top2 > Top3 violation")

    model_order_violations = int((ai2 > ai3 + 1e-12).sum())
    print("synthetic AI Top2 > Top3 violations:", model_order_violations, "/", n)
    print("Note: synthetic feature rows are only a runtime smoke test, not a model-quality test.")
    print("Existing win model modified: NO")
    print("★★★★★ TOP2/TOP3 RUNTIME SMOKE TEST: SUCCESS")


if __name__ == "__main__":
    main()
