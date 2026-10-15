"""Read public source formats only; never writes forecasts or observations."""
import json
from datetime import datetime,timedelta
from bs4 import BeautifulSoup
from triple_forward import JST,request_bytes
from all_bet_forward import parse_table,payout_map
from all_bet_baseline import probabilities
from win5_research import official_targets


def main():
    rid = '202605040502'  # Completed 2026-10-12; not a forward observation.
    html=request_bytes(f'https://race.netkeiba.com/race/result.html?race_id={rid}')
    soup=BeautifulSoup(html,'html.parser')
    # Result rows supply coverage for a format check only, never a saved forecast.
    horses,frames=[],[]
    for row in soup.select('tr.HorseList'):
        cells=row.find_all('td',recursive=False)
        if len(cells)<4 or not cells[0].select_one('.Rank'):
            continue
        number=int(cells[2].get_text(strip=True));name=cells[3].select_one('.Horse_Name').get_text(strip=True)
        horses.append({'number':number,'name':name,'aiProbability':100/12})
        frames.append({'number':number,'name':name,'frame':int(cells[1].get_text(strip=True)),'withdrawn':False})
    assert len(horses)==12,'completed source entrant coverage changed'
    for kind in (1, 2, 3, 4, 6):
        raw = request_bytes(f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={rid}'
                            f'&type={kind}&action=init&compress=0&output=json')
        payload = json.loads(raw)
        data = payload.get('data', {})
        tables = data.get('odds', {})
        assert tables.get(str(kind)), f'API {kind} table unavailable'
        print('ALL_BET_SOURCE_SCHEMA', json.dumps({'raceID': rid, 'requestedType': kind,
              'status': payload.get('status'),
              'metadata': {k:v for k,v in data.items() if k != 'odds'},
              'tables': {k:{'count':len(v),'examples':list(v.items())[:3]} for k,v in tables.items()}},
              ensure_ascii=False))
        label={1:'win',2:'place',3:'frame_quinella',4:'quinella',6:'exacta'}[kind]
        expected=probabilities(horses,'aiProbability',label,{'runners':frames})
        values=parse_table(tables[str(kind)],expected,label)
        print('ALL_BET_SOURCE_PARSE_OK',label,len(values))
        print('ALL_BET_PAYOUT_PARSE_OK',label,json.dumps(payout_map(html,rid,label)))
    raw=request_bytes('https://www.jra.go.jp/kouza/win5/info/racelist.html')
    confirmed=[]
    for offset in range(14):
        day=(datetime.now(JST)+timedelta(days=offset)).date().isoformat()
        try:
            target=official_targets(raw,day)
        except ValueError:
            continue
        if target:
            confirmed.append(target)
    assert confirmed,'no upcoming official WIN5 target parsed; source/date coverage needs inspection'
    for target in confirmed[:2]:
        print('WIN5_OFFICIAL_PARSE_OK',json.dumps(target,ensure_ascii=False))
    archive=BeautifulSoup(request_bytes('https://race.netkeiba.com/top/win5.html?date=20261012'),'html.parser')
    print('WIN5_ARCHIVE_TITLE_SCHEMA',archive.title.get_text() if archive.title else None)
    for table in archive.select('table'):
        if 'win5' in ' '.join(table.get('class',[])):
            print('WIN5_ARCHIVE_TABLE_SCHEMA',str(table)[:10500])


if __name__ == '__main__':
    main()
