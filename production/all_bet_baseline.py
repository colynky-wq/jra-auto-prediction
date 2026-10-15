"""Frozen comparison math for the five added pools; no training or selection."""
from collections import defaultdict
import math
from itertools import permutations

from research_triple_baseline import finite, race_key

METHOD = 'all-bet-pl-win-baseline-v1-20261015'
TYPES = {'win': 1, 'place': 2, 'frame_quinella': 3, 'quinella': 4, 'exacta': 6}
LABELS = {'win':'単勝', 'place':'複勝', 'frame_quinella':'枠連', 'quinella':'馬連', 'exacta':'馬単'}


def roster(horses):
    if not isinstance(horses, list) or not 2 <= len(horses) <= 18:
        raise ValueError('2 to 18 entrants required')
    out = {}
    for horse in horses:
        n, name = horse['number'], horse['name']
        if type(n) is not int or not 1 <= n <= 18 or n in out or not isinstance(name,str) or not name.strip():
            raise ValueError('unique horse numbers and names required')
        out[n] = name
    return out


def win_weights(horses, key):
    roster(horses)
    values = {h['number']:finite(h[key]) for h in horses}
    total = math.fsum(values.values())
    if any(not 0 < p <= 100 for p in values.values()) or abs(total-100) > .05:
        raise ValueError('positive win probabilities summing to 100 required')
    return {n:p/total for n,p in values.items()}


def outcome_key(numbers):
    return ''.join(f'{n:02}' for n in numbers)


def frame_map(evidence, horses):
    names = roster(horses)
    rows = evidence['runners']
    seen, frames = {}, {}
    for row in rows:
        n, frame = row['number'], row['frame']
        if type(n) is not int or n in seen or type(frame) is not int or not 1 <= frame <= 8:
            raise ValueError('invalid source frame assignment')
        seen[n] = row['name']; frames[n] = frame
    if seen != names:
        raise ValueError('official entry/prediction entrant mismatch')
    return frames


def place_count(evidence, horses):
    """Only an intact complete declaration establishes the opening field here.

    A cancellation or missing number makes opening terms unconfirmed. Never use
    the smaller active field after a withdrawal to change the number of places.
    """
    names = roster(horses)
    rows = evidence['runners']
    if ({r['number']:r['name'] for r in rows} != names or len(rows) != len(names)
            or set(names) != set(range(1,len(names)+1))
            or any(r.get('withdrawn') is not False for r in rows)):
        raise ValueError('intact opening declaration not established')
    if len(names) < 5:
        raise ValueError('place pool not offered for this intact declaration')
    return 2 if len(names) <= 7 else 3


def probabilities(horses, key, kind, evidence=None):
    """Place events overlap (mass 2/3); other added pools have mass one."""
    if kind not in TYPES:
        raise ValueError('unsupported added pool')
    weights = win_weights(horses,key)
    if kind == 'win':
        return {outcome_key((n,)):p for n,p in weights.items()}
    if kind in {'quinella','exacta'} and len(weights) < 3:
        raise ValueError('pair pool not offered for two entrants')
    frames = frame_map(evidence, horses) if kind == 'frame_quinella' else None
    if frames and len(weights) <= 8 and len(set(frames.values())) == len(frames):
        raise ValueError('frame pool not offered for this intact declaration')
    places = place_count(evidence,horses) if kind == 'place' else None
    out = defaultdict(float)
    if places:
        for finish in permutations(sorted(weights),places):
            p, remaining = 1., 1.
            chosen = set()
            for number in finish:
                remaining = math.fsum(v for n,v in weights.items() if n not in chosen)
                p *= weights[number]/remaining
                chosen.add(number)
            for number in finish:
                out[outcome_key((number,))] += p
    else:
        for a,b in permutations(sorted(weights),2):
            p = weights[a]*weights[b]/math.fsum(v for n,v in weights.items() if n != a)
            numbers = (a,b) if kind == 'exacta' else tuple(sorted((frames[a],frames[b]) if frames else (a,b)))
            out[outcome_key(numbers)] += p
    if not math.isclose(math.fsum(out.values()), places or 1, abs_tol=1e-9):
        raise ValueError('probability mass mismatch')
    return dict(out)


def result_state(race, result):
    if result is None:
        return 'awaiting_results', None
    if race_key(race) != race_key(result):
        return 'invalid', None
    from triple_forward import stamp
    if stamp(result['resultUpdatedAtJST']) < stamp(race['raceDate']+'T'+race['startTime']):
        return 'invalid', None
    names, actual = roster(race['horses']), roster(result['horses'])
    if type(result.get('fieldSize')) is not int or result['fieldSize'] != len(actual):
        return 'invalid', None
    if names != actual or any(h.get('resultStatus') in {'取消','除外'} for h in result['horses']):
        return 'entrant_changed', None
    positions = defaultdict(list)
    for h in result['horses']:
        p = h.get('finishPosition')
        if p is None and h.get('resultStatus') in {'中止','失格'}:
            continue
        if p is None:
            return 'awaiting_results', None
        if type(p) is not int or not 1 <= p <= len(names) or h.get('resultStatus') != '確定':
            return 'invalid', None
        positions[p].append(h['number'])
    limit = min(3,len(names))
    if any(len(positions[p]) > 1 for p in range(1,limit+1)):
        return 'dead_heat_excluded', None
    if any(len(positions[p]) != 1 for p in range(1,limit+1)):
        return 'invalid', None
    return 'matched', tuple(positions[p][0] for p in range(1,limit+1))


def truth_keys(race, kind, evidence, finish):
    if kind == 'win':
        return {outcome_key(finish[:1])}
    if kind == 'place':
        return {outcome_key((n,)) for n in finish[:place_count(evidence,race['horses'])]}
    if kind == 'frame_quinella':
        frames = frame_map(evidence,race['horses'])
        return {outcome_key(sorted(frames[n] for n in finish[:2]))}
    return {outcome_key(finish[:2] if kind == 'exacta' else sorted(finish[:2]))}
