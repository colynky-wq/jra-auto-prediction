import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from copy import deepcopy
import pandas as pd

import aggregate_forward_results as agg
from archived_forward_results import load_archive, result_frame
from forward_prediction_source import load_published_day

ROOT = Path(__file__).resolve().parent.parent
DAY = "20261123"

class ArchivedResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "reports" / DAY / "ResultData.json"
        cls.archive, cls.digest = load_archive(cls.path)
        cls.predictions, cls.provenance = load_published_day(ROOT, DAY)

    def test_real_published_prediction_result_match(self):
        self.assertEqual(len(self.predictions), 23)
        self.assertEqual(len(self.archive), 23)
        frames = [result_frame(self.archive[p["raceID"]], p, DAY)
                  for p in self.predictions]
        self.assertEqual(sum(map(len, frames)), 345)
        self.assertEqual(sum(p.get("predictionTiming") == "live_10min"
                             for p in self.predictions), 22)

    def test_mismatched_or_incomplete_archives_rejected(self):
        prediction = self.predictions[0]
        original = self.archive[prediction["raceID"]]
        for kind in ["date", "name", "duplicate", "incomplete", "post"]:
            with self.subTest(kind=kind):
                record = deepcopy(original)
                if kind == "date": record["raceDate"] = "2026-11-22"
                if kind == "name": record["horses"][0]["name"] = "別の馬"
                if kind == "duplicate": record["horses"][1]["number"] = record["horses"][0]["number"]
                if kind == "incomplete": record["horses"].pop()
                if kind == "post": record["resultUpdatedAtJST"] = "2026-11-23 00:00:00"
                with self.assertRaises(ValueError):
                    result_frame(record, prediction, DAY)

    def test_full_offline_aggregation_in_temporary_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            reports = Path(temp)
            day = reports / DAY
            day.mkdir()
            (day / "ResultData.json").write_bytes(self.path.read_bytes())
            # Schedule IDs supplied only as a test fixture. The production
            # independent day-list check remains enabled.
            with patch.multiple(agg, TARGET_DATE8=DAY, REPORTS_DIR=reports,
                                LEDGER_PATH=reports/"ledger.csv",
                                PREDICTION_PROVENANCE=self.provenance), \
                 patch.object(agg, "load_predictions", return_value=self.predictions), \
                 patch.object(agg, "fetch_day_race_ids", return_value=set(self.archive)), \
                 patch.object(agg, "fetch_result_table", side_effect=AssertionError("unexpected result network fetch")):
                agg.main()
            s = json.loads((day/"summary.json").read_text())
            self.assertEqual(s["publishedRaces"], 23)
            self.assertEqual(s["publishedHorses"], 345)
            self.assertEqual(len(s["resultProvenance"]["archivedRaceIDs"]), 23)
            self.assertEqual(s["resultProvenance"]["fetchedRaceIDs"], [])
            self.assertEqual(s["resultProvenance"]["archivedResultDataSHA256"], self.digest)
            self.assertEqual(s["publishedPredictionTimingCounts"]["live_10min"], 22)
            self.assertTrue(pd.read_csv(day/"all_predictions.csv")["nameMatch"].all())

if __name__ == "__main__":
    unittest.main()
