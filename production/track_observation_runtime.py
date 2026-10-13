"""Read timestamped official track conditions; never infer going from moisture."""
from __future__ import annotations
from datetime import datetime
import hashlib
import re
import time
from urllib.parse import urljoin,urlparse
from bs4 import BeautifulSoup
from horse_profile_runtime import JRA_VENUE_MAP
from prerace_snapshot_runtime import JST

BASE='https://www.jra.go.jp/keiba/baba/index.html'
_CACHE={}

def parse_track_observation(text,url,venue_code,date8,captured_at):
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname!='www.jra.go.jp' or not re.fullmatch(r'/keiba/baba/index\d*\.html',parsed.path):raise ValueError('not an official current track page')
    if captured_at.tzinfo is None:raise ValueError('timezone required')
    captured_at=captured_at.astimezone(JST);soup=BeautifulSoup(text,'html.parser');venue=JRA_VENUE_MAP[venue_code]
    heading=next((h.get_text(' ',strip=True) for h in soup.find_all('h2') if re.search(r'第?\d+回'+venue+r'競馬',h.get_text())),None)
    if not heading:raise ValueError('track venue mismatch')
    d=re.search(r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日',heading)
    if not d or f'{int(d[1]):04d}{int(d[2]):02d}{int(d[3]):02d}'!=date8:raise ValueError('track date differs from requested race day')
    h=next((h for h in soup.find_all('h3') if h.get_text().strip().startswith('馬場状態')),None)
    if h is None:raise ValueError('track state section missing')
    stamp=h.get_text(' ',strip=True);md=re.search(r'(\d{1,2})月\s*(\d{1,2})日',stamp)
    tm=re.search(r'(\d{1,2})時(?:\s*(\d{1,2})分)?',stamp)
    if not md or (not tm and '正午' not in stamp):raise ValueError('track as-of clock missing')
    year=int(d[1]);month,day=int(md[1]),int(md[2]);hour,minute=(int(tm[1]),int(tm[2] or 0)) if tm else (12,0)
    asof=datetime(year,month,day,hour,minute,tzinfo=JST)
    race_day=datetime.strptime(date8,'%Y%m%d').date()
    if asof.date()>race_day:asof=asof.replace(year=year-1)
    if asof>captured_at:raise ValueError('track as-of time is after capture')
    # Traverse only this section, stopping before cushion/moisture explanatory tables.
    strings=[]
    for tag in h.next_elements:
        if getattr(tag,'name',None) in {'h2','h3'}:break
        if isinstance(tag,str) and tag.strip():strings.append(tag.strip())
    block=' '.join(strings)
    going={}
    for surface in ['芝','ダート']:
        m=re.search(re.escape(surface)+r'\s*[:：]?\s*(稍重|不良|良|重)(?:\s|$)',block)
        if not m:raise ValueError('explicit surface going missing')
        going[surface]=m[1]
    age=(captured_at-asof).total_seconds()/3600
    return {'venue':venue,'raceDate':race_day.isoformat(),'sourceURL':url,'sourceSHA256':hashlib.sha256(text.encode()).hexdigest(),'sourceHashEncoding':'decoded UTF-8 response text','capturedAtJST':captured_at.isoformat(),'asOfJST':asof.isoformat(),'going':going,'ageHours':round(age,3),'eligibleForRaceInput':asof.date()==race_day and 0<=age<=6,'meaning':'Official published track state as of its timestamp; stale observations retained with eligibility false.'}

def fetch_track_observation(session,venue_code,date8):
    cachekey=(venue_code,date8);cached=_CACHE.get(cachekey)
    if cached and time.monotonic()-cached[0]<60:return cached[1]
    def fetch(url):
        response=session.get(url,timeout=10);response.raise_for_status()
        fetched=datetime.now(JST);enc=response.apparent_encoding or 'utf-8'
        return response.content.decode(enc,errors='replace'),fetched
    text,captured=fetch(BASE);soup=BeautifulSoup(text,'html.parser')
    try:observation=parse_track_observation(text,BASE,venue_code,date8,captured)
    except ValueError:
        venue=JRA_VENUE_MAP[venue_code]
        links=[urljoin(BASE,a['href']) for a in soup.select('a[href]') if a.get_text(strip=True)==venue+'競馬場']
        links=[url for url in links if urlparse(url).hostname=='www.jra.go.jp' and re.fullmatch(r'/keiba/baba/index\d*\.html',urlparse(url).path)]
        if not links:raise ValueError('official venue track link not observed')
        url=links[0];text,captured=fetch(url);observation=parse_track_observation(text,url,venue_code,date8,captured)
    _CACHE[cachekey]=(time.monotonic(),observation)
    return observation
