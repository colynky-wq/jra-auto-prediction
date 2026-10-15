"""Read-only schema probe of one completed race. Never saves race observations."""
import json
from triple_forward import fetch_odds, parse_table, payout_map, request_bytes


def main():
    rid = '202605040502'  # Completed 2026-10-12 race; compatibility only.
    for kind in (7, 8):
        payload = fetch_odds(rid, 'trio' if kind == 7 else 'trifecta')
        data = payload.get('data', {})
        odds = data.get('odds', {}).get(str(kind), {})
        assert odds, 'missing odds table'
        parsed = parse_table(odds, 'trio' if kind == 7 else 'trifecta', range(1,13))
        print('TRIPLE_SOURCE_SCHEMA', json.dumps({'raceID': rid, 'apiType': kind,
              'status': payload.get('status'), 'officialDatetime': data.get('official_datetime'),
              'count': len(odds), 'examples': list(odds.items())[:2]}, ensure_ascii=False))
        print('TRIPLE_SOURCE_PARSE_OK', kind, len(parsed))
    html = request_bytes(f'https://race.netkeiba.com/race/result.html?race_id={rid}').decode('utf-8')
    for kind in ('trio','trifecta'):
        print('TRIPLE_PAYOUT_PARSE_OK', kind, json.dumps(payout_map(html,rid,kind)))


if __name__ == '__main__':
    main()
