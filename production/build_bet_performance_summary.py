"""Aggregate settled BetResearchData rows into deterministic per-bet performance metrics.

This is evaluation-only. It never changes selection rules, recommendations, or stakes.
Each frozen candidate is treated at its recorded unit amount. The published summary is
rewritten only when material metrics change; source-generation timestamps alone do not
create repository churn.

The legacy/reference aggregate remains available as ``betTypes``. A separate
``formalT10`` cohort uses the same PortfolioResearchData source, prospective start,
immutable live_10min evidence, 8-12 minute capture window, chronological ordering and
first-100 lock as PortfolioReview100.json. Per-bet recording and settlement coverage is
measured against that independent formal race cohort so missing bet records cannot
silently shrink the denominator.
"""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from formal_10min_evidence import (
    FORMAL_TIMING,
    MAX_MINUTES_TO_POST,
    MIN_MINUTES_TO_POST,
    inspect_formal_10min_evidence,
)
from portfolio_review_100 import (
    PROSPECTIVE_START,
    TARGET_RACES,
    race_key as portfolio_race_key,
    read_json,
    validate_source,
)

BET_LABELS = {
    'win':'単勝','place':'複勝','frame_quinella':'枠連','quinella':'馬連','wide':'ワイド',
    'exacta':'馬単','trio':'3連複','trifecta':'3連単','support':'応援馬券','win5':'WIN5'
}
VOLATILE_TOP_LEVEL_FIELDS = {'sourceGeneratedAtJST'}


def pct(n, d):
    return round(n / d * 100, 2) if d else None


def load_rows(root):
    rows = {}
    latest_generated = None
    for path in sorted((root / 'reports').glob('*/BetResearchData.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        stamp = data.get('generatedAtJST')
        if stamp and (latest_generated is None or stamp > latest_generated):
            latest_generated = stamp
        for row in data.get('races', []):
            key = row.get('id') or f"{row.get('betType')}:{row.get('raceDate')}:{row.get('raceID')}"
            rows[key] = row
    return list(rows.values()), latest_generated


def race_key(row):
    day = row.get('raceDate')
    rid = row.get('raceID')
    if not isinstance(day, str) or not isinstance(rid, str) or not day or not rid:
        return None
    return day, rid


def authoritative_formal_cohort(root):
    """Recreate PortfolioReview100's formal race cohort from its authoritative source."""
    root = Path(root)
    data = read_json(root / 'PortfolioResearchData.json')
    validate_source(data)
    races = data.get('races')
    if not isinstance(races, list):
        raise ValueError('portfolio races must be a list')

    prospective = []
    verified = []
    reasons = Counter()
    seen = set()
    for row in races:
        if not isinstance(row, dict) or row.get('scope') != 'prospective':
            continue
        key = race_key(row)
        if key is None or key[0] < PROSPECTIVE_START:
            raise ValueError('invalid prospective race identity/date')
        if key in seen:
            raise ValueError(f'duplicate prospective race: {key}')
        seen.add(key)
        prospective.append(row)
        evidence = inspect_formal_10min_evidence(root, row)
        reason = str(evidence.get('reason') or 'unknown')
        reasons[reason] += 1
        if evidence.get('eligible') is True:
            verified.append(row)

    prospective.sort(key=portfolio_race_key)
    verified.sort(key=portfolio_race_key)
    cohort = verified[:TARGET_RACES]
    cohort_keys = {race_key(row) for row in cohort}
    return cohort_keys, {
        'prospectiveStartDate': PROSPECTIVE_START,
        'targetRaces': TARGET_RACES,
        'requiredTiming': FORMAL_TIMING,
        'formalWindowMinutesToPost': [MIN_MINUTES_TO_POST, MAX_MINUTES_TO_POST],
        'prospectiveRacesObserved': len(prospective),
        'verifiedFormalRacesObserved': len(verified),
        'eligibleRaces': len(cohort),
        'remainingTo100': max(0, TARGET_RACES - len(cohort)),
        'excludedRaces': len(prospective) - len(verified),
        'laterFormalRacesExcludedAfterFirst100': max(0, len(verified) - TARGET_RACES),
        'first100CohortLockedByRule': True,
        'evidenceReasons': dict(sorted(reasons.items())),
    }


def formal_t10_rows(root, rows):
    cohort_keys, meta = authoritative_formal_cohort(root)
    filtered = [row for row in rows if race_key(row) in cohort_keys]
    return filtered, meta


def summarize(kind, rows, cohort_size=None):
    kind_rows = [r for r in rows if r.get('betType') == kind]
    recorded_keys = {race_key(r) for r in kind_rows if race_key(r) is not None}
    settled = [r for r in kind_rows if r.get('status') == 'matched']
    settled_keys = {race_key(r) for r in settled if race_key(r) is not None}
    candidates = [c for r in settled for c in r.get('candidates', []) if c.get('hit') is not None]
    hits = sum(c.get('hit') is True for c in candidates)
    hit_races = sum(any(c.get('hit') is True for c in r.get('candidates', [])) for r in settled)
    stake = sum(int(c.get('unitYen') or 0) for c in candidates)
    returned = sum(int(c.get('payoutYen') or 0) for c in candidates)
    result = {
        'id': kind,
        'label': BET_LABELS[kind],
        'recordedRaces': len(recorded_keys),
        'settledRaces': len(settled_keys),
        'hitRaces': hit_races,
        'raceHitRatePct': pct(hit_races, len(settled_keys)),
        'candidateTickets': len(candidates),
        'hitTickets': hits,
        'ticketHitRatePct': pct(hits, len(candidates)),
        'stakeYen': stake,
        'returnYen': returned,
        'profitYen': returned - stake,
        'returnRatePct': round(returned / stake * 100, 2) if stake else None,
    }
    if cohort_size is not None:
        result['recordingCoveragePct'] = pct(len(recorded_keys), cohort_size)
        result['settledCoveragePct'] = pct(len(settled_keys), cohort_size)
        result['missingRecordRaces'] = max(0, cohort_size - len(recorded_keys))
        result['unsettledOrUnmatchedRaces'] = max(0, len(recorded_keys) - len(settled_keys))
    return result


def build(root):
    root = Path(root)
    rows, generated = load_rows(root)
    metrics = [summarize(k, rows) for k in BET_LABELS]
    formal_rows, formal_meta = formal_t10_rows(root, rows)
    formal_metrics = [summarize(k, formal_rows, formal_meta['eligibleRaces']) for k in BET_LABELS]
    return {
        'schemaVersion': 2,
        'version': 'bet-performance-summary-v2',
        'status': 'research_evaluation_only',
        'sourceGeneratedAtJST': generated,
        'definition': '発走前に保存された各候補を記録単位額で1点ずつ購入したと仮定した実測集計。選定ルールや正式推奨は変更しない。',
        'referenceCohortNote': 'betTypes は全研究記録の参考集計で、正式forward性能の証明には使わない。',
        'betTypes': metrics,
        'formalT10': {
            **formal_meta,
            'definition': 'PortfolioReview100と同じ10/24開始・厳格live_10min証拠・最初の100R固定コホート。券種ごとの欠測は母数から除外せずcoverageとして明示する。',
            'betTypes': formal_metrics,
        },
    }


def material(value):
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    for field in VOLATILE_TOP_LEVEL_FIELDS:
        result.pop(field, None)
    return result


def write_if_material_changed(root):
    root = Path(root)
    output = root / 'BetPerformanceSummary.json'
    candidate = build(root)
    if output.exists():
        try:
            current = json.loads(output.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            current = None
        if isinstance(current, dict) and material(current) == material(candidate):
            return False, current
    output.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return True, candidate


def main():
    root = Path(__file__).resolve().parent.parent
    changed, summary = write_if_material_changed(root)
    formal = summary.get('formalT10') if isinstance(summary.get('formalT10'), dict) else {}
    print('BET_PERFORMANCE_SUMMARY', 'changed' if changed else 'unchanged',
          'betTypes', len(summary.get('betTypes', [])),
          'formalT10Races', formal.get('eligibleRaces'))


if __name__ == '__main__':
    main()
