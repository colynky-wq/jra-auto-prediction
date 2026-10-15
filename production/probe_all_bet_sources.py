"""Read public source formats only; never writes forecasts or observations."""
import json
from bs4 import BeautifulSoup
from triple_forward import request_bytes


def main():
    rid = '202605040502'
    for kind in (1, 2, 3, 4, 6):
        payload = json.loads(request_bytes(f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={rid}&type={kind}&action=init&compress=0&output=json'))
        data = payload.get('data', {})
        tables = data.get('odds', {})
        assert tables.get(str(kind)), f'API {kind} table unavailable'
        print('ALL_BET_SOURCE_SCHEMA', json.dumps({'raceID': rid, 'requestedType': kind, 'status': payload.get('status'), 'metadata': {k:v for k,v in data.items() if k != 'odds'}, 'tables': {k:{'count':len(v),'examples':list(v.items())[:3]} for k,v in tables.items()}}, ensure_ascii=False))
    soup = BeautifulSoup(request_bytes(f'https://race.netkeiba.com/race/result.html?race_id={rid}'), 'html.parser')
    for row in soup.select('.Payout_Detail_Table tr'):
        print('ALL_BET_PAYOUT_SCHEMA', str(row)[:1300])
    for row in soup.select('.RaceTable01 tr')[:4]:
        print('ALL_BET_RESULT_TABLE_SCHEMA', str(row)[:1800])
    official = BeautifulSoup(request_bytes('https://www.jra.go.jp/kouza/win5/info/racelist.html'), 'html.parser')
    for row in official.select('tr'):
        if '10月17日' in row.get_text() or '10月18日' in row.get_text():
            print('WIN5_OFFICIAL_ROW_SCHEMA', str(row))
    w = BeautifulSoup(request_bytes('https://race.netkeiba.com/top/win5.html'), 'html.parser')
    print('WIN5_PAGE_TITLE_SCHEMA', w.title.get_text() if w.title else None)
    for table in w.select('table'):
        if '清滝' in table.get_text() or len(table.select('a[href*="race_id="]')) == 5:
            print('WIN5_TARGET_TABLE_SCHEMA', str(table)[:6500])


if __name__ == '__main__':
    main()
