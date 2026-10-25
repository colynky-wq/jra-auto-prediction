"""Resume live sessions from captures whose timing quality is explicit."""
from datetime import datetime
import json
import math
from pathlib import Path
import re

from live_capture_policy import (
    CAPTURE_TIMING_MARKERS,
    FORMAL_TIMING_MARKER,
    RECOVERY_TIMING_MARKER,
)


BASE_PROBABILITY_KEYS = ('aiProbability', 'marketProbability')
TOPK_PROBABILITY_KEYS = (
    'aiTop2Probability', 'marketTop2Probability',
    'aiTop3Probability', 'marketTop3Probability',
)
LIVE_TIMING_MARKER = FORMAL_TIMING_MARKER
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
    """Return races whose live capture is already complete enough to resume past.

    Formal 8-12 minute captures use predictionTiming='live_10min'.  A bounded
    late fallback uses 'live_late_recovery'.  Recovery rows count as captured so
    overlapping/restarted monitors do not fetch them repeatedly, but they never
    satisfy or create formal research evidence.  Morning/early forecasts remain
    pending for a proper live capture.
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

        timing = row.get('predictionTiming')
        if timing not in CAPTURE_TIMING_MARKERS:
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

        # Formal captures are only resume-safe when all formal research writes
        # required by this monitor are present.  Late recovery deliberately has
        # no formal sidecars and is nevertheless complete as recovery evidence.
        if timing == FORMAL_TIMING_MARKER and require_research and not research_sidecars_complete(root,date8,rid):
            continue
        if timing == RECOVERY_TIMING_MARKER:
            ready.add(rid)
            continue
        ready.add(rid)
    return ready
