"""Standard-library-only, isolated tests for the research baseline."""
from copy import deepcopy
from itertools import permutations
import json
import math
from pathlib import Path
import tempfile
import unittest

from research_triple_baseline import audit, distributions, index_unique, result_state


def race(n=4):
    return {'raceDate': '2026-10-12', 'raceID': '202605040502', 'startTime': '10:20',
            'horses': [{'number': i, 'name': 'horse'+str(i), 'aiProbability': 100/n,
                        'marketProbability': 100/n} for i in range(1, n+1)]}


def result(r):
    value = deepcopy(r)
    value.update(fieldSize=len(r['horses']), resultUpdatedAtJST='2026-10-12 10:30:00')
    for i, h in enumerate(value['horses'], 1):
        h.update(finishPosition=i, resultStatus='確定')
    return value


class BaselineTests(unittest.TestCase):
    def test_uniform_and_grouping(self):
        for n in (3, 4, 18):
            ordered, sets = distributions(race(n)['horses'], 'aiProbability')
            self.assertEqual(len(ordered), n*(n-1)*(n-2))
            self.assertEqual(len(sets), math.comb(n, 3))
            self.assertAlmostEqual(math.fsum(ordered.values()), 1)
            for key, value in sets.items():
                self.assertAlmostEqual(value, sum(ordered[p] for p in permutations(key)))
                self.assertAlmostEqual(value, 1/math.comb(n, 3))

    def test_nonuniform_sequential_probability_not_independent_product(self):
        horses = race()['horses']
        for h, w in zip(horses, [40., 30., 20., 10.]):
            h['aiProbability'] = w
        ordered, sets = distributions(horses, 'aiProbability')
        self.assertAlmostEqual(ordered[1, 2, 3], .4 * .3/.6 * .2/.3)
        self.assertNotAlmostEqual(ordered[1, 2, 3], .4*.3*.2)
        self.assertAlmostEqual(sets[1, 2, 3], sum(ordered[p] for p in permutations((1, 2, 3))))
        self.assertAlmostEqual(sum(p for k, p in ordered.items() if k[0] == 1), .4)

    def test_invalid_probability_and_roster(self):
        for invalid in (float('nan'), float('inf'), True, -1, 0, '25', 101):
            h = race()['horses']; h[0]['aiProbability'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                distributions(h, 'aiProbability')
        h = race()['horses']; h[0]['number'] = h[1]['number']
        with self.assertRaises(ValueError): distributions(h, 'aiProbability')
        h = race()['horses']; h[0]['aiProbability'] = 30
        with self.assertRaises(ValueError): distributions(h, 'aiProbability')

    def test_result_missing_unfinished_and_changed(self):
        r = race(); value = result(r)
        self.assertEqual(result_state(r, None)[0], 'awaiting_results')
        value['horses'][3]['finishPosition'] = None
        self.assertEqual(result_state(r, value)[0], 'awaiting_results')
        value = result(r); value['horses'][0]['name'] = 'changed'
        self.assertEqual(result_state(r, value)[0], 'entrant_changed')
        for state in ('取消', '除外'):
            value = result(r); value['horses'][3].update(resultStatus=state, finishPosition=None)
            self.assertEqual(result_state(r, value)[0], 'entrant_changed')

    def test_dead_heat_never_scored_as_loss(self):
        r = race(); value = result(r)
        value['horses'][3]['finishPosition'] = 3
        self.assertEqual(result_state(r, value)[0], 'dead_heat_excluded')

    def test_unconfirmed_status_with_numeric_places_is_not_final(self):
        r = race(); value = result(r)
        value['horses'][0]['resultStatus'] = '未確定'
        self.assertEqual(result_state(r, value)[0], 'invalid')

    def test_result_identity_time_and_terminal(self):
        r = race(); value = result(r)
        self.assertEqual(result_state(r, value), ('matched_retrospective', (1, 2, 3)))
        value['resultUpdatedAtJST'] = '2026-10-13 10:30:00'
        self.assertEqual(result_state(r, value)[0], 'matched_retrospective')
        value['resultUpdatedAtJST'] = '2026-10-12 10:19:59'
        self.assertEqual(result_state(r, value)[0], 'invalid')
        value = result(r); value['startTime'] = '10:21'
        self.assertEqual(result_state(r, value)[0], 'invalid')
        for state in ('中止', '失格'):
            value = result(r); value['horses'][3].update(resultStatus=state, finishPosition=None)
            self.assertEqual(result_state(r, value)[0], 'matched_retrospective')

    def test_audit_never_converts_historical_inputs_to_forward_profit(self):
        r = race()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in [('RaceData.json', [r]), ('ResultData.json', [result(r)])]:
                (root/name).write_text(json.dumps(value))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            got = audit(root)
            self.assertEqual(got['counts']['matched_retrospective'], 1)
            self.assertEqual(got['forwardValidatedRaces'], 0)
            self.assertIsNone(got['returnIndex'])
            self.assertFalse(got['ticketSelectionEnabled'])
            self.assertFalse(got['realOddsConnected'])
            self.assertFalse(got['appDisplayEnabled'])
            self.assertFalse(got['autoBetEnabled'])
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            json.dumps(got, allow_nan=False)
        with self.assertRaises(ValueError): index_unique([r, r])


if __name__ == '__main__':
    unittest.main()
