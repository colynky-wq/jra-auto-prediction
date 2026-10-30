"""Refresh the strict formal T-10 review only when its material content changes.

This is intentionally separate from the live prediction publisher. It never changes
predictions, portfolio rules, thresholds or stake sizing. The normal review builder
includes generatedAtJST, so running it every few minutes would create noise commits;
this wrapper ignores only that volatile top-level timestamp when deciding whether a
new public review is necessary.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

from portfolio_review_100 import attach_formal_evidence, build, read_json, write_json

VOLATILE_TOP_LEVEL_FIELDS = {"generatedAtJST"}


def material(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    for field in VOLATILE_TOP_LEVEL_FIELDS:
        result.pop(field, None)
    return result


def refresh(root: Path) -> tuple[bool, dict[str, Any]]:
    source = read_json(root / "PortfolioResearchData.json")
    review = build(attach_formal_evidence(root, source))
    target = root / "PortfolioReview100.json"

    prior: dict[str, Any] | None = None
    if target.exists():
        loaded = read_json(target)
        if isinstance(loaded, dict):
            prior = loaded

    if prior is not None and material(prior) == material(review):
        return False, prior

    write_json(target, review)
    return True, review


def main() -> None:
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    changed, review = refresh(root)
    quality = review.get("qualityChecks") if isinstance(review.get("qualityChecks"), dict) else {}
    print(json.dumps({
        "changed": changed,
        "reviewStatus": review.get("reviewStatus"),
        "racesObserved": review.get("racesObserved"),
        "remainingTo100": review.get("remainingTo100"),
        "formal10minVerifiedRaces": quality.get("formal10minVerifiedRaces"),
        "nonFormalProspectiveExcluded": quality.get("nonFormalProspectiveExcluded"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
