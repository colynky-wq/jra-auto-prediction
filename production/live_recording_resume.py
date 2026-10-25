"""Resume live sessions from explicit formal or quarantined recovery evidence."""
from datetime import datetime
import json
import math
from pathlib import Path
import re

from live_capture_policy import FORMAL_TIMING_MARKER, RECOVERY_TIMING_MARKER


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


def _validate_live_row(row, expected_day, expected_rid, expected_timing):
    if row.get('raceDate') != expected_day or row.get('raceID') != expected_rid:
        raise ValueError('live capture identity mismatch')
    if row.get('predictionTiming') != expected_timing:
        raise ValueError('live capture timing mismatch')
    horses = row.get('horses')
    if not isinstance(horses, list) or len(horses) < 2:
        raise ValueError('incomplete published live forecast')
    numbers = set()
    for h in horses:
        n = h.get('number')
        if type(n) is not int or n < 1 or n in numbers or not isinstance(h.get('name'), str) or not h.get('name'):
            raise ValueError('invalid published entrant')
        numbers.add(n)
        for key in BASE_PROBABILITY_KEYS:
            if key not in h or not _valid_probability(h[key]):
                raise ValueError('incomplete published probabilities')
        for key in TOPK_PROBABILITY_KEYS:
            if key in h and not _valid_probability(h[key]):
                raise ValueError('invalid published TopK probability')


def published_race_ids(root, date8, schedule, require_research=False):
    """Return race IDs safe to skip when a live session restarts.

    Formal T-10 captures live in RaceData.json and, when requested, require all
    formal research sidecars.  Late recovery is quarantined under
    reports/<date>/late_recovery so it can prevent repeated fetching without ever
    entering RaceData, SignalData, app recommendations, or formal research.
    """
    root = Path(root)
    day = datetime.strptime(date8, '%Y%m%d').date().isoformat()
    expected = {r['race_id']: r for r in schedule}
    ready = set()

    # Recovery evidence is intentionally separate from formal app inputs.
    recovery_dir = root / 'reports' / date8 / 'late_recovery'
    for rid in expected:
        path = recovery_dir / (rid + '.json')
        if not path.exists():
            continue
        row = json.loads(path.read_text(encoding='utf-8'))
        _validate_live_row(row, day, rid, RECOVERY_TIMING_MARKER)
        ready.add(rid)

    path = root / 'RaceData.json'
    if not path.exists():
        return ready
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise ValueError('invalid existing forecast list; cannot safely resume')
    seen = set()
    for row in rows:
        if row.get('raceDate') != day:
            continue
        rid = row.get('raceID')
        if not isinstance(rid, str) or not re.fullmatch(r'\d{12}', rid) or rid in seen:
            raise ValueError('duplicate/invalid published race identity')
        seen.add(rid)
        if rid not in expected:
            continue

        # Morning/early rows are not a live capture. Explicit recovery rows are
        # forbidden in RaceData by the current publisher and are ignored here so
        # they cannot be treated as formal evidence by resume logic.
        if row.get('predictionTiming') != FORMAL_TIMING_MARKER:
            continue
        _validate_live_row(row, day, rid, FORMAL_TIMING_MARKER)
        if require_research and not research_sidecars_complete(root,date8,rid):
            continue
        ready.add(rid)
    return ready
