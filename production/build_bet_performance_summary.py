"""Aggregate settled BetResearchData rows into deterministic per-bet performance metrics.

This is evaluation-only. It never changes selection rules, recommendations, or stakes.
Each frozen candidate is treated at its recorded unit amount. The published summary is
rewritten only when material metrics change; source-generation timestamps alone do not
create repository churn.
"""
from copy import deepcopy
import json
from pathlib import Path

BET_LABELS = {
    'win':'単勝','place':'複勝','frame_quinella':'枠連','quinella':'馬連','wide':'ワイド',
    'exacta':'馬単','trio':'3連複','trifecta':'3連単','support':'応援馬券','win5':'WIN5'
}
VOLATILE_TOP_LEVEL_FIELDS = {'sourceGeneratedAtJST'}


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


def summarize(kind, rows):
    settled = [r for r in rows if r.get('betType') == kind and r.get('status') == 'matched']
    candidates = [c for r in settled for c in r.get('candidates', []) if c.get('hit') is not None]
    hits = sum(c.get('hit') is True for c in candidates)
    hit_races = sum(any(c.get('hit') is True for c in r.get('candidates', [])) for r in settled)
    stake = sum(int(c.get('unitYen') or 0) for c in candidates)
    returned = sum(int(c.get('payoutYen') or 0) for c in candidates)
    return {
        'id': kind,
        'label': BET_LABELS[kind],
        'settledRaces': len(settled),
        'hitRaces': hit_races,
        'raceHitRatePct': round(hit_races / len(settled) * 100, 2) if settled else None,
        'candidateTickets': len(candidates),
        'hitTickets': hits,
        'ticketHitRatePct': round(hits / len(candidates) * 100, 2) if candidates else None,
        'stakeYen': stake,
        'returnYen': returned,
        'profitYen': returned - stake,
        'returnRatePct': round(returned / stake * 100, 2) if stake else None,
    }


def build(root):
    root = Path(root)
    rows, generated = load_rows(root)
    metrics = [summarize(k, rows) for k in BET_LABELS]
    return {
        'schemaVersion': 1,
        'version': 'bet-performance-summary-v1',
        'status': 'research_evaluation_only',
        'sourceGeneratedAtJST': generated,
        'definition': '発走前に保存された各候補を記録単位額で1点ずつ購入したと仮定した実測集計。選定ルールや正式推奨は変更しない。',
        'betTypes': metrics,
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
    print('BET_PERFORMANCE_SUMMARY', 'changed' if changed else 'unchanged',
          'betTypes', len(summary.get('betTypes', [])))


if __name__ == '__main__':
    main()
