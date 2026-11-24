import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from formal_portfolio_capture import stamp
from research_longshot_wide_odds_replay import evaluate_policies, scan_archive, select_tickets, source_age, summary


class WideOddsReplayTests(unittest.TestCase):
    def snapshot(self, source="2026-11-23 10:20:00", captured="2026-11-23 10:50:00"):
        return {
            "raceDate": "2026-11-23", "raceID": "202605050601", "startTime": "11:00",
            "officialDatetime": source, "capturedAtJST": captured,
            "apiType": 5, "apiStatus": "middle", "frozenAtFirstSuccessfulCapture": True,
            "pairCount": 1, "pairs": [{"number1": 1, "number2": 2, "oddsLow": 3, "oddsHigh": 5}],
        }

    def test_real_receipt_and_decision_clocks_have_separate_ages(self):
        doc = self.snapshot()
        self.assertEqual(source_age(doc, stamp("2026-11-23 10:50:00")), 1800)
        self.assertEqual(source_age(doc, stamp("2026-11-23 10:50:01")), 1801)
        with self.assertRaises(ValueError):
            source_age(self.snapshot(source="2026-11-23 10:50:01"), stamp("2026-11-23 10:51:00"))
        with self.assertRaises(ValueError):
            source_age(self.snapshot(source=None), stamp("2026-11-23 10:51:00"))

    def test_same_source_time_and_price_change_are_detected_separately(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            day = root / "reports/20261123"
            for folder, snapshot in (
                ("wide_odds_live10", self.snapshot()),
                ("wide_odds", self.snapshot(captured="2026-11-23 10:40:00")),
            ):
                target = day / folder / "202605050601.json"
                target.parent.mkdir(parents=True)
                target.write_text(json.dumps(snapshot))
            first, = scan_archive(root)
            self.assertEqual(first["state"], "sourceWithin30Minutes")
            self.assertEqual(first["earlierComparison"], "sameSourceTime:samePrices")
            old = json.loads(target.read_text())
            old["pairs"][0]["oddsLow"] = 2
            target.write_text(json.dumps(old))
            revised, = scan_archive(root)
            self.assertEqual(revised["earlierComparison"], "sameSourceTime:pricesChanged")

    def test_exact_ticket_count_and_actual_torigami(self):
        race = self.snapshot()
        odds = {(1, 2): (2.7, 4), (1, 3): (4, 5), (2, 3): (5, 7), (2, 4): (7, 8)}
        p = {(1, 2): .5, (1, 3): .4, (2, 3): .35, (2, 4): .2}
        settled = {"0102": (False, 0), "0103": (True, 290), "0203": (False, 0), "0204": (False, 0)}
        rows, bets = evaluate_policies(race, {"method": p}, odds, settled, 1800)
        picked = next(r for r in rows if r["policy"] == "oddsFiltered" and r["targetTickets"] == 3)
        self.assertEqual(picked["tickets"], 3)
        self.assertTrue(picked["torigami"])
        self.assertEqual(picked["hitBelowQuotedLower"], 1)
        self.assertEqual(len(bets), 8)
        self.assertEqual(summary([picked])[0]["torigamiRaces"], 1)
        self.assertEqual(select_tickets([{"a": 1, "b": 2, "probability": .5, "oddsLow": 2.7}], 3, "oddsFiltered"), [])


if __name__ == "__main__":
    unittest.main()
