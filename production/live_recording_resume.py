"""Resume live sessions from published forecasts without making new predictions."""
from datetime import datetime
import json
import math
from pathlib import Path
import re


BASE_PROBABILITY_KEYS = ('aiProbability', 'marketProbability')
TOPK_PROBABILITY_KEYS = (
    'aiTop2Probability', 'marketTop2Probability',
    'aiTop3Probability', 'marketTop3Probability',
)


def _valid_probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def published_race_ids(root, date8, schedule):
    """Return races that already have a safe published forecast.

    Win/market probabilities are the minimum contract for historical RaceData.
    Top2/Top3 fields were added later, so older rows may legitimately omit them.
    When any Top2/Top3 field is present we validate every present value, but missing
    optional TopK fields must not abort the live recorder.

    The live schedule is authoritative for post time. A published morning forecast
    can contain the post time known when it was created; JRA may subsequently move
    that time. A time-only difference must therefore not abort odds capture. Race
    identity and entrant/probability validation remain strict, and the monitor uses
    the freshly fetched schedule to decide the 12-to-8-minute capture window.
    """
    day = datetime.strptime(date8, '%Y%m%d').date().isoformat()
    path = Path(root) / 'RaceData.json'
    if not path.exists():
        return set()
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise ValueError('invalid existing forecast list; cannot safely resume')
    expected = {r['race_id']: r for r in schedule}
    seen, ready = set(), set()
    for row in rows:
        if row.get('raceDate') != day:
            continue
        rid = row['raceID']
        if not isinstance(rid, str) or not re.fullmatch(r'\d{12}', rid) or rid in seen:
            raise ValueError('duplicate/invalid published race identity')
        seen.add(rid)
        if rid not in expected:
            continue
        # Do not fail on a post-time-only change. The current schedule fetched by
        # the live monitor is authoritative for capture timing. We deliberately do
        # not rewrite the already-published morning forecast here.
        horses = row['horses']
        if not isinstance(horses, list) or len(horses) < 2:
            raise ValueError('incomplete published forecast')
        numbers = set()
        for h in horses:
            n = h['number']
            if type(n) is not int or n < 1 or n in numbers or not isinstance(h['name'], str) or not h['name']:
                raise ValueError('invalid published entrant')
            numbers.add(n)
            for key in BASE_PROBABILITY_KEYS:
                if key not in h or not _valid_probability(h[key]):
                    raise ValueError('incomplete published probabilities')
            for key in TOPK_PROBABILITY_KEYS:
                if key in h and not _valid_probability(h[key]):
                    raise ValueError('invalid published TopK probability')
        ready.add(rid)
    return ready
