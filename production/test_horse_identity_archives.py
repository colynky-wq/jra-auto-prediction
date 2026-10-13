import json
from pathlib import Path
import tempfile
import unittest
import pandas as pd
from build_horse_identity_data import build_archives, write_archives


def row(number=1, name="テスト", key="テスト_2024", date="2026-10-11", rid="202605040405", age=2):
    return {"レースID": rid, "日付": date, "馬番": number, "馬名": name, "馬キー": key, "年齢": age, "障害": False}


class ArchiveTests(unittest.TestCase):
    def test_only_identification_is_published(self):
        d = pd.DataFrame([dict(row(), 着順=1, 初角=1, 単勝オッズ=2.)])
        p = build_archives(d, as_of_date="2026-10-13")["2026-10-11"]
        horse = p["races"][0]["horses"][0]
        self.assertEqual(set(horse), {"number", "name", "age", "horseKey"})
        self.assertEqual(horse["horseKey"], "テスト_2024")
        self.assertEqual(p["unresolvedRows"], 0)

    def test_invalid_generation_does_not_remove_healthy_entrant(self):
        d = pd.DataFrame([row(key="テスト_2013"), row(number=2, name="正常", key="正常_2024")])
        p = build_archives(d, as_of_date="2026-10-13")["2026-10-11"]
        self.assertEqual([h["number"] for h in p["races"][0]["horses"]], [2])
        self.assertEqual(p["unresolvedRows"], 1)

    def test_future_unknown_and_obstacle_rows_are_excluded(self):
        d = pd.DataFrame([row(), row(date="2026-10-17"), dict(row(), 障害=True), row(rid="not-jra")])
        p = build_archives(d, as_of_date="2026-10-13")
        self.assertEqual(list(p), ["2026-10-11"])
        self.assertEqual(len(p["2026-10-11"]["races"][0]["horses"]), 1)

    def test_duplicate_numbers_are_unresolved(self):
        p = build_archives(pd.DataFrame([row(), row()]), as_of_date="2026-10-13")["2026-10-11"]
        self.assertEqual(p["races"], [])
        self.assertEqual(p["unresolvedRows"], 2)

    def test_all_future_returns_no_archive(self):
        self.assertEqual(build_archives(pd.DataFrame([row(date="2026-10-17")]), as_of_date="2026-10-13"), {})

    def test_recent_limit_and_idempotent_save(self):
        d = pd.DataFrame([row(date="2026-10-10"), row(date="2026-10-11")])
        p = build_archives(d, limit=1, as_of_date="2026-10-13")
        self.assertEqual(list(p), ["2026-10-11"])
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(write_archives(p, root), 1)
            path = Path(root) / "20261011/HorseIdentityData.json"
            content = path.read_bytes(); stamp = path.stat().st_mtime_ns
            self.assertEqual(write_archives(p, root), 0)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), (content, stamp))


if __name__ == "__main__": unittest.main()
