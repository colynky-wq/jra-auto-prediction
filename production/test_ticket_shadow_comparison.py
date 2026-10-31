import json
from pathlib import Path
import tempfile
import unittest

import ticket_shadow_comparison as compare


class TestTicketShadowComparison(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.day = self.root / "reports" / "20261031"
        (self.day / "ticket_shadow_v1" / "prediction").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_case(self, win, fitted, finish=(2, 1, 3)):
        numbers = [1, 2, 3, 4]
        record = {
            "formalPrediction": {"raceID": "race1", "venue": "東京", "raceNumber": 1, "startTime": "10:00"},
            "horseMarginals": [
                {"number": n, "win": win[i], "top2": min(1.0, win[i] + .2), "top3": min(1.0, win[i] + .4),
                 "fittedRankingWeight": fitted[i]}
                for i, n in enumerate(numbers)
            ],
        }
        (self.day / "ticket_shadow_v1" / "prediction" / "race1.json").write_text(json.dumps(record), encoding="utf-8")
        horses = []
        for n in numbers:
            pos = finish.index(n) + 1 if n in finish else 4
            horses.append({"number": n, "name": f"H{n}", "finishPosition": pos, "resultStatus": "確定"})
        result = [{"raceID": "race1", "horses": horses}]
        (self.day / "ResultData.json").write_text(json.dumps(result), encoding="utf-8")

    def test_same_weights_tie_all_ticket_scores(self):
        weights = [.4, .3, .2, .1]
        self.write_case(weights, weights)
        report = compare.comparison(self.root, "20261031")
        self.assertEqual(report["shadowRecordsObserved"], 1)
        self.assertEqual(report["statusCounts"], {"matched": 1})
        for kind in compare.KINDS:
            row = report["betTypeComparison"][kind]
            self.assertEqual(row["ties"], 1)
            self.assertEqual(row["shadowBetterRaces"], 0)
            self.assertEqual(row["currentABetterRaces"], 0)

    def test_shadow_reordering_can_improve_winner_and_exact_outcome(self):
        self.write_case([.5, .2, .2, .1], [.2, .5, .2, .1])
        report = compare.comparison(self.root, "20261031")
        horse = report["horseWinnerComparison"]
        self.assertEqual(horse["currentATopWinnerHits"], 0)
        self.assertEqual(horse["shadowTopWinnerHits"], 1)
        exacta = report["betTypeComparison"]["exacta"]
        self.assertEqual(exacta["shadowBetterRaces"], 1)
        self.assertLess(exacta["meanNegativeLogProbabilityLowerIsBetter"]["shadow"],
                        exacta["meanNegativeLogProbabilityLowerIsBetter"]["currentA"])

    def test_missing_result_is_kept_in_cohort_not_silently_dropped(self):
        numbers = [1, 2, 3]
        record = {"formalPrediction": {"raceID": "missing", "venue": "東京", "raceNumber": 1, "startTime": "10:00"},
                  "horseMarginals": [{"number": n, "win": 1/3, "top2": 2/3, "top3": 1.0,
                                      "fittedRankingWeight": 1/3} for n in numbers]}
        (self.day / "ticket_shadow_v1" / "prediction" / "missing.json").write_text(json.dumps(record), encoding="utf-8")
        (self.day / "ResultData.json").write_text("[]", encoding="utf-8")
        report = compare.comparison(self.root, "20261031")
        self.assertEqual(report["shadowRecordsObserved"], 1)
        self.assertEqual(report["statusCounts"], {"results_missing": 1})
        self.assertEqual(report["betTypeComparison"]["trifecta"]["matchedRaces"], 0)


if __name__ == "__main__":
    unittest.main()
