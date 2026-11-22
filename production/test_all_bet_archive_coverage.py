import csv
import tempfile
import unittest
from pathlib import Path

import audit_historical_archive_coverage as coverage


def write_manifest(path: Path, race_ids):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['レースID', '日付'])
        writer.writeheader()
        for i, rid in enumerate(race_ids, 1):
            writer.writerow({'レースID': rid, '日付': f'2025010{i}'})


def add_three_files(races: Path, payouts: Path, rid: str):
    (races / f'{rid}_odds.parquet').write_bytes(b'x')
    (races / f'{rid}_horses.parquet').write_bytes(b'x')
    (payouts / f'{rid}.html.gz').write_bytes(b'x')


class ArchiveCoverageTests(unittest.TestCase):
    def test_partial_archive_is_reported_as_contiguous_prefix(self):
        ids = ['202506010101', '202506010102', '202506010103', '202506010104']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            races, payouts = root / 'races', root / 'payout_raw'
            races.mkdir(); payouts.mkdir()
            manifest = root / 'race_manifest_2025.csv'
            write_manifest(manifest, ids)
            for rid in ids[:3]:
                add_three_files(races, payouts, rid)
            report = coverage.audit(manifest, races, payouts)
        self.assertEqual(report['manifest_races'], 4)
        self.assertEqual(report['contiguous_prefix_races'], 3)
        self.assertEqual(report['last_contiguous_race_id'], ids[2])
        self.assertEqual(report['first_missing_race_id'], ids[3])
        self.assertEqual(report['scope_label'], 'contiguous_partial_year_prefix')
        self.assertTrue(report['contiguous_prefix_is_hole_free'])
        self.assertFalse(report['archive_covers_full_manifest'])

    def test_internal_gap_is_not_silently_called_contiguous(self):
        ids = ['202506010101', '202506010102', '202506010103', '202506010104']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            races, payouts = root / 'races', root / 'payout_raw'
            races.mkdir(); payouts.mkdir()
            manifest = root / 'race_manifest_2025.csv'
            write_manifest(manifest, ids)
            add_three_files(races, payouts, ids[0])
            add_three_files(races, payouts, ids[2])
            report = coverage.audit(manifest, races, payouts)
        self.assertEqual(report['contiguous_prefix_races'], 1)
        self.assertEqual(report['internal_hole_count'], 1)
        self.assertIn(ids[1], report['internal_holes_first_50'])
        self.assertEqual(report['covered_after_first_gap_count'], 1)
        self.assertEqual(report['scope_label'], 'non_contiguous_archive')
        self.assertFalse(report['contiguous_prefix_is_hole_free'])

    def test_full_manifest_coverage(self):
        ids = ['202506010101', '202506010102']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            races, payouts = root / 'races', root / 'payout_raw'
            races.mkdir(); payouts.mkdir()
            manifest = root / 'race_manifest_2025.csv'
            write_manifest(manifest, ids)
            for rid in ids:
                add_three_files(races, payouts, rid)
            report = coverage.audit(manifest, races, payouts)
        self.assertEqual(report['scope_label'], 'full_manifest')
        self.assertTrue(report['archive_covers_full_manifest'])
        self.assertEqual(report['manifest_races_missing_odds'], 0)


if __name__ == '__main__':
    unittest.main()
