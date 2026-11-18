"""Unit tests enforcing the user's zero-yen budget cap."""
from __future__ import annotations
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"production"))
from zero_cost_odds_policy import validate_policy, inspect


class ZeroCostOddsPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy={
            "monthlyIncrementalCostCapJPY":0,
            "userBudgetConstraint":"additional_cost_zero_yen",
            "paidProvidersDisallowed":True,
            "providerSelection":None,
            "selectionStatus":"free_only_source_unverified",
            "operationalStatus":"not_connected",
            "subscriptionActive":False,
            "credentialAvailable":False,
            "sourceAuthenticityVerified":False,
            "formalBettingEnabled":False,
            "productionModelChanged":False,
            "zeroCostCandidates":[{"name":"JRA official viewer","costJPY":0}],
        }

    def test_free_policy_passes(self):
        self.assertEqual(validate_policy(self.policy),[])

    def test_any_nonzero_monthly_cap_is_rejected(self):
        self.policy["monthlyIncrementalCostCapJPY"]=1
        self.assertIn("nonzero_spend_cap",validate_policy(self.policy))

    def test_paid_vendor_cannot_be_reselected(self):
        self.policy["providerSelection"]="team_nave_keiba_odds_api"
        self.assertIn("active_provider_selection_must_be_null",
                      validate_policy(self.policy))

    def test_subscription_must_remain_inactive(self):
        self.policy["subscriptionActive"]=True
        self.assertIn("subscriptionActive_must_be_false",
                      validate_policy(self.policy))

    def test_paid_candidate_cannot_masquerade_as_free(self):
        self.policy["zeroCostCandidates"][0]["costJPY"]=14300
        self.assertIn("priced_candidate_in_zero_cost_list",
                      validate_policy(self.policy))

    def test_invalid_provider_source_not_claimed_verified(self):
        self.policy["sourceAuthenticityVerified"]=True
        self.assertIn("sourceAuthenticityVerified_must_be_false",
                      validate_policy(self.policy))

    def test_inspect_reports_fail_closed_status(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"config.json"
            p.write_text(json.dumps(self.policy))
            result=inspect(p)
            self.assertTrue(result["policyPass"])
            self.assertFalse(result["independentFreshOddsVerified"])
            self.assertFalse(result["paidSourceConnectionAllowed"])


if __name__=="__main__":
    unittest.main()
