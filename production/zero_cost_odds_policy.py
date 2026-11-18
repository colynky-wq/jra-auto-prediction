"""Fail-closed zero-additional-cost policy for independent odds research.

This is a configuration validator, not a supplier integration or permission to
scrape any website. No network, provider credentials, subscriptions or purchases.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

POLICY_STATUS="free_only_source_unverified"


def validate_policy(config):
    issues=[]
    if not isinstance(config,dict):
        return ["invalid_config"]
    if config.get("monthlyIncrementalCostCapJPY") != 0:
        issues.append("nonzero_spend_cap")
    if config.get("userBudgetConstraint") != "additional_cost_zero_yen":
        issues.append("zero_cost_constraint_missing")
    if config.get("paidProvidersDisallowed") is not True:
        issues.append("paid_providers_not_disabled")
    if config.get("providerSelection") is not None:
        issues.append("active_provider_selection_must_be_null")
    if config.get("selectionStatus") != POLICY_STATUS:
        issues.append("unverified_free_status_not_preserved")
    if config.get("operationalStatus") != "not_connected":
        issues.append("unverified_source_must_remain_disconnected")
    for key in ("subscriptionActive","credentialAvailable",
                "sourceAuthenticityVerified","formalBettingEnabled",
                "productionModelChanged"):
        if config.get(key) is not False:
            issues.append(key+"_must_be_false")
    candidates=config.get("zeroCostCandidates")
    if not isinstance(candidates,list) or not candidates:
        issues.append("zero_cost_candidates_not_recorded")
    else:
        for item in candidates:
            if not isinstance(item,dict):
                issues.append("invalid_candidate")
            elif item.get("costJPY") != 0:
                issues.append("priced_candidate_in_zero_cost_list")
    return issues


def inspect(path):
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    issues=validate_policy(data)
    return {
        "zeroAdditionalSpendRequired": True,
        "spendCapJPY": 0,
        "policyPass": not issues,
        "issues": issues,
        "independentFreshOddsVerified": False,
        "paidSourceConnectionAllowed": False,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=Path(
        "research/independent_odds_provider_selection.json"))
    args=parser.parse_args()
    result=inspect(args.config)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["policyPass"] else 1


if __name__=="__main__":
    raise SystemExit(main())
