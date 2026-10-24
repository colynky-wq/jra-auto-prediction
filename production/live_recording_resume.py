"""Resume live sessions only from forecasts proven to be live-window captures."""
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
LIVE_TIMING_MARKER = 'live_10min'
ALL_BET_SIDECARS = ('win','place','frame_quinella','quinella','exacta')
TRIPLE_SIDECARS = ('trio','trifecta')


def research_sidecars_complete(root,date8,rid):
    day=Path(root)/'reports'/date8
    required=[day/'all_bet_v1'/kind/(rid+'.json') for kind in ALL_BET_SIDECARS]
    required += [day/'triple_v1'/kind/(rid+'.json') for kind in TRIPLE_SIDECARS]
    required.append(day/'wide_axis_v1'/(rid+'.json'))
    return all(p.is_file() and p.stat().st_size>0 for p in required)


def _valid_probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def published_race_ids(root, date8, schedule, require_research=False):
    """Return only races already captured in the formal live window.

    RaceData can contain morning/early forecasts for every race. Those rows must
    never be treated as proof that the 12-to-8-minute live capture has happened.
    New live publications carry predictionTiming='live_10min'; only those rows
    are safe to mark processed when a monitor session resumes.

    Historical/morning rows are still validated enough to detect corrupt race
    identity, but they remain pending for live capture. A post-time-only change
    is allowed because the freshly fetched live schedule is authoritative.
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

        # Morning/early forecasts are not completed live captures.
        if row.get('predictionTiming') != LIVE_TIMING_MARKER:
            continue

        horses = row['horses']
        if not isinstance(horses, list) or len(horses) < 2:
            raise ValueError('incomplete published live forecast')
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
        if require_research and not research_sidecars_complete(root,date8,rid):
            continue
        ready.add(rid)
    return ready
