from __future__ import annotations

from itertools import combinations
import unittest

import all_bet_baseline as single
import all_bet_recommendation_shadow as shadow
import early_all_bet_recommendation as target
import triple_forward as triple


PROBS = [25, 18, 14, 11, 9, 7, 6, 5, 5]
MARKET = [22, 19, 15, 12, 10, 8, 6, 5, 3]
FRAMES = [1, 2, 3, 4, 5, 6, 7, 8, 8]
RACE = {
    'raceDate': '2026-10-24',
    'raceID': '202605040101',
    'venue': '東京',
    'raceNumber': 1,
    'startTime': '10:05',
    'raceName': '朝テスト',
    'course': 'ダ1400m',
    'horses': [
        {
            'number': i + 1,
            'name': chr(ord('A') + i),
            'odds': round(100 / p, 1),
            'aiProbability': p,
            'marketProbability': MARKET[i],
            'aiTop2Probability': 200 / 9,
            'marketTop2Probability': 200 / 9,
            'aiTop3Probability': 300 / 9,
            'marketTop3Probability': 300 / 9,
        }
        for i, p in enumerate(PROBS)
    ],
}
EVIDENCE = {
    'runners': [
        {'number': i + 1, 'name': chr(ord('A') + i), 'frame': FRAMES[i], 'withdrawn': False}
        for i in range(9)
    ]
}


def simple_payload(race, evidence, kind):
    expected = single.probabilities(race['horses'], 'aiProbability', kind, evidence)
    table = {}
    for key in expected:
        if kind == 'place':
            table[key] = ['4.0', '4.4']
        elif kind == 'win':
            table[key] = ['6.0']
        elif kind == 'frame_quinella':
            table[key] = ['12.0']
        elif kind == 'quinella':
            table[key] = ['18.0']
        else:
            table[key] = ['28.0']
    return {'status': 'middle', 'data': {'odds': {str(single.TYPES[kind]): table}}}


def triple_payload(kind):
    numbers = range(1, 10)
    table = {key: ['80.0'] for key in triple.expected_keys(kind, numbers)}
    return {'status': 'middle', 'data': {'odds': {str(triple.TYPES[kind]): table}}}


def wide_snapshot(*args, **kwargs):
    pairs = [
        {'number1': a, 'number2': b, 'oddsLow': 8.0, 'oddsHigh': 8.6, 'popularity': None}
        for a, b in combinations(range(1, 10), 2)
    ]
    return {
        'raceDate': RACE['raceDate'],
        'raceID': RACE['raceID'],
        'venue': RACE['venue'],
        'raceNumber': RACE['raceNumber'],
        'startTime': RACE['startTime'],
        'capturedAtJST': '2026-10-24 07:30:00',
        'officialDatetime': '2026-10-24 07:29:50',
        'betType': 'ワイド',
        'pairCount': len(pairs),
        'pairs': pairs,
    }


class MorningAllBetTests(unittest.TestCase):
    def fetch_simple(self, rid, kind):
        self.assertEqual(rid, RACE['raceID'])
        return simple_payload(RACE, EVIDENCE, kind)

    def fetch_triple(self, rid, kind):
        self.assertEqual(rid, RACE['raceID'])
        return triple_payload(kind)

    def test_all_nine_pools_use_real_odds_without_formal_flags(self):
        value = target.build_one(
            RACE,
            EVIDENCE,
            fetch_simple=self.fetch_simple,
            fetch_triple=self.fetch_triple,
            fetch_wide=wide_snapshot,
        )
        coverage = {row['betType']: row for row in value['coverage']}
        self.assertEqual(set(coverage), set(shadow.BET_TYPES))
        self.assertEqual(len(coverage), 9)
        self.assertTrue(all(row['sourceStatus'] == 'ready' for row in coverage.values()))
        self.assertTrue(all(row['realOddsAvailable'] for row in coverage.values()))
        self.assertEqual(value['pendingBetTypes'], [])
        self.assertEqual(value['snapshotStage'], 'morning')
        self.assertTrue(value['realOddsSnapshot'])
        self.assertFalse(value['formalRecommendation'])
        self.assertFalse(value['autoBetEnabled'])
        self.assertFalse(value['stakeSizingEnabled'])
        self.assertIn(value['recommendationStatus'], {'candidate', 'skip'})

    def test_one_missing_pool_stays_pending_and_support_does_not_invent_odds(self):
        def fetch_simple(rid, kind):
            if kind == 'place':
                raise OSError('place odds unavailable')
            return simple_payload(RACE, EVIDENCE, kind)

        value = target.build_one(
            RACE,
            EVIDENCE,
            fetch_simple=fetch_simple,
            fetch_triple=self.fetch_triple,
            fetch_wide=wide_snapshot,
        )
        coverage = {row['betType']: row for row in value['coverage']}
        self.assertEqual(coverage['place']['sourceStatus'], 'held')
        self.assertFalse(coverage['place']['realOddsAvailable'])
        self.assertEqual(coverage['support']['sourceStatus'], 'held')
        self.assertFalse(coverage['support']['realOddsAvailable'])
        self.assertIn('place', value['pendingBetTypes'])
        self.assertIn('support', value['pendingBetTypes'])
        self.assertFalse(value['formalRecommendation'])


if __name__ == '__main__':
    unittest.main()
