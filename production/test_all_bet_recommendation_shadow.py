from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import all_bet_recommendation_shadow as target


RACE = {
    "raceDate": "2026-10-24", "raceID": "202605040101", "venue": "東京",
    "raceNumber": 1, "raceName": "テスト", "startTime": "10:05",
    "horses": [
        {"number": 1, "name": "A", "odds": 2.0, "aiProbability": 50.0, "marketProbability": 50.0},
        {"number": 2, "name": "B", "odds": 2.0, "aiProbability": 50.0, "marketProbability": 50.0},
    ],
}


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def candidate(cid: str, label: str, p: float, low: float, ev: float) -> dict:
    return {
        "id": cid, "label": label, "probabilityPct": p,
        "oddsLow": low, "oddsHigh": low, "expectedMultiplierLow": ev,
        "expectedMultiplierHigh": ev, "unitYen": 100,
    }


class RecommendationTests(unittest.TestCase):
    def root(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        write(root / "RaceData.json", [RACE])
        self.addCleanup(temporary.cleanup)
        return root

    def test_no_saved_odds_is_explicitly_pending(self):
        value = target.build(self.root())
        race = value["races"][0]
        self.assertEqual(race["recommendationStatus"], "odds_pending")
        self.assertEqual(set(race["pendingBetTypes"]), set(target.BET_TYPES))
        self.assertIsNone(race["bestBet"])
        self.assertFalse(race["formalRecommendation"])

    def test_cross_pool_score_is_deterministic(self):
        root = self.root()
        day = root / "reports" / "20261024"
        bets = {
            "raceDate": "2026-10-24",
            "races": [
                {"raceDate": "2026-10-24", "raceID": RACE["raceID"], "betType": "win", "status": "ready",
                 "candidates": [candidate("01", "1番 A", 30.0, 3.6, 1.08)]},
                {"raceDate": "2026-10-24", "raceID": RACE["raceID"], "betType": "wide", "status": "ready",
                 "candidates": [candidate("0102", "1番 A − 2番 B", 20.0, 6.0, 1.20)]},
            ],
        }
        signal = {"races": [{"raceDate": "2026-10-24", "raceID": RACE["raceID"], "eligible": True,
                              "battle": {"grade": "A"}, "chaos": {"level": 3}}]}
        write(day / "BetResearchData.json", bets)
        write(day / "SignalData.json", signal)
        value = target.build(root)
        race = value["races"][0]
        self.assertEqual(race["recommendationStatus"], "candidate")
        self.assertEqual(race["bestBet"]["betType"], "wide")
        self.assertEqual([x["betType"] for x in race["betRecommendations"]], ["wide", "win"])
        self.assertEqual(race["battleGrade"], "A")

    def test_battle_grade_c_forces_skip(self):
        root = self.root()
        day = root / "reports" / "20261024"
        bets = {"raceDate": "2026-10-24", "races": [
            {"raceDate": "2026-10-24", "raceID": RACE["raceID"], "betType": "win", "status": "ready",
             "candidates": [candidate("01", "1番 A", 30.0, 3.6, 1.08)]},
        ]}
        signal = {"races": [{"raceDate": "2026-10-24", "raceID": RACE["raceID"], "eligible": True,
                              "battle": {"grade": "C"}, "chaos": {"level": 2}}]}
        write(day / "BetResearchData.json", bets)
        write(day / "SignalData.json", signal)
        race = target.build(root)["races"][0]
        self.assertEqual(race["recommendationStatus"], "skip")
        self.assertIsNone(race["bestBet"])
        self.assertEqual(race["betRecommendations"], [])

    def test_no_qualifying_candidate_skips_when_all_pools_resolved(self):
        root = self.root()
        day = root / "reports" / "20261024"
        rows = []
        for kind in target.BET_TYPES:
            rows.append({"raceDate": "2026-10-24", "raceID": RACE["raceID"], "betType": kind,
                         "status": "ready", "candidates": []})
        write(day / "BetResearchData.json", {"raceDate": "2026-10-24", "races": rows})
        write(day / "SignalData.json", {"races": [{"raceDate": "2026-10-24", "raceID": RACE["raceID"],
                                                     "eligible": True, "battle": {"grade": "A"},
                                                     "chaos": {"level": 2}}]})
        race = target.build(root)["races"][0]
        self.assertEqual(race["recommendationStatus"], "skip")
        self.assertEqual(race["pendingBetTypes"], [])

    def test_candidate_without_signal_stays_signal_pending(self):
        root = self.root()
        day = root / "reports" / "20261024"
        write(day / "BetResearchData.json", {"raceDate": "2026-10-24", "races": [
            {"raceDate": "2026-10-24", "raceID": RACE["raceID"], "betType": "place", "status": "ready",
             "candidates": [candidate("01", "1番 A", 40.0, 2.7, 1.08)]},
        ]})
        race = target.build(root)["races"][0]
        self.assertEqual(race["recommendationStatus"], "signal_pending")
        self.assertEqual(race["betRecommendations"], [])


if __name__ == "__main__":
    unittest.main()
