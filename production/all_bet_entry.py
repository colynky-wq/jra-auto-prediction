"""Optional research metadata from the exact official response used by the model."""
import re
from bs4 import BeautifulSoup

from prerace_snapshot_runtime import parse_snapshot


def parse_entry(text, rid, date8, url, captured, entries):
    source = parse_snapshot(text,rid,date8,url,captured,entries)
    table = BeautifulSoup(text,'html.parser').find('table')
    frames = {}
    last_frame, span = None, 0
    for tr in table.find_all('tr'):
        cells = tr.find_all('td',recursive=False)
        # Some official tables use a rowspan for a shared frame.
        if len(cells) < 3:
            continue
        offset = 0
        if span:
            frame = last_frame; span -= 1; offset = -1
        else:
            text0 = cells[0].get_text(' ',strip=True)+' '+str(cells[0])
            match = re.search(r'(?:枠|waku|w)([1-8])',text0,re.I)
            if not match:
                match = re.search(r'([1-8])枠',text0)
            if not match:
                match = re.search(r'(?<!\d)([1-8])(?!\d)',cells[0].get_text(' ',strip=True))
            if not match:
                continue
            frame = int(match[1]); last_frame = frame
            span = max(0,int(cells[0].get('rowspan',1))-1)
        match = re.fullmatch(r'\s*(\d{1,2})\s*',cells[1+offset].get_text(' ',strip=True))
        if not match:
            continue
        number = int(match[1])
        if number in frames:
            raise ValueError('duplicate official frame row')
        frames[number] = frame
    for runner in source['runners']:
        runner['frame'] = frames.get(runner['number'])
    return {k:source[k] for k in ('raceID','raceDate','scheduledPostJST','capturedAtJST','sourceURL','sourceSHA256','runners')}
