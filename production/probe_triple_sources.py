"""Read-only schema probe of one completed race. Never saves race observations."""
import json
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


def main():
    rid = '202605040502'  # Completed 2026-10-12 race; compatibility only.
    for kind in (7, 8):
        url = (f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={rid}'
               f'&type={kind}&action=init&compress=0&output=json')
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0',
                      'Referer': f'https://race.netkeiba.com/odds/index.html?race_id={rid}&type=b{kind}'})
        with urlopen(req, timeout=12) as response:
            payload = json.load(response)
        data = payload.get('data', {})
        odds = data.get('odds', {}).get(str(kind), {})
        assert odds, 'missing odds table'
        print('TRIPLE_SOURCE_SCHEMA', json.dumps({'raceID': rid, 'apiType': kind,
              'status': payload.get('status'), 'officialDatetime': data.get('official_datetime'),
              'count': len(odds), 'examples': list(odds.items())[:2]}, ensure_ascii=False))
    req = Request(f'https://race.netkeiba.com/race/result.html?race_id={rid}',
                  headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=12) as response:
        html = response.read().decode('utf-8')
    soup = BeautifulSoup(html, 'html.parser')
    for row in soup.select('tr'):
        title = row.find('th')
        if title and title.get_text(strip=True) in {'3連複', '3連単'}:
            print('TRIPLE_PAYOUT_SCHEMA', title.get_text(strip=True),
                  str(row)[:1600])


if __name__ == '__main__':
    main()
