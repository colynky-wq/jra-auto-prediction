"""Independent dated WIN5 payout documents; never substitutes five win odds."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup
from triple_forward import JST,digest,stamp,request_bytes
from live_bundle_publisher import atomic


class PayoutNotReady(ValueError):
    pass


def yen(text):
    text=re.sub(r'\s+','',text).replace(',','')
    match=re.fullmatch(r'(?:(\d+)億)?(?:(\d+)万)?(\d+)?円',text)
    if not match or not any(match.groups()):
        raise PayoutNotReady('unconfirmed published WIN5 payout')
    return int(match[1] or 0)*100_000_000+int(match[2] or 0)*10_000+int(match[3] or 0)


def payout_document(raw,day,race_ids):
    soup=BeautifulSoup(raw,'html.parser')
    title=soup.title.get_text() if soup.title else ''
    date=datetime.strptime(day,'%Y-%m-%d')
    if not re.search(rf'{date.year}年\s*{date.month}月\s*{date.day}日',title):
        raise ValueError('dated WIN5 payout document mismatch')
    tables=soup.select('table.win5raceresult2')
    if len(tables)!=1:
        raise ValueError('unique WIN5 target/result table required')
    table=tables[0]
    links=table.select('a[href*="race_id="]')
    actual_ids=[]
    for link in links:
        match=re.search(r'race_id=(\d{12})(?:[&#]|$)',link['href'])
        if not match:
            raise ValueError('invalid WIN5 target identity')
        actual_ids.append(match[1])
    if actual_ids!=list(race_ids) or len(set(actual_ids))!=5:
        raise ValueError('WIN5 payout target/order mismatch')
    winner_rows=[row for row in table.select('tr') if row.find('td') and row.find('td').get_text(strip=True)=='勝ち馬']
    if len(winner_rows)!=1:
        raise PayoutNotReady('WIN5 winners unavailable')
    cells=winner_rows[0].find_all('td',recursive=False)[1:]
    if len(cells)!=5:
        raise ValueError('WIN5 winner leg count mismatch')
    winners=[]
    for cell in cells:
        nodes=cell.select('.RaceWaku'); names=cell.select('a[href*="/horse/"]')
        if len(nodes)!=1 or len(names)!=1:
            raise PayoutNotReady('WIN5 single-winner result unavailable or tied')
        text=nodes[0].get_text(strip=True)
        if not text.isdigit() or not 1<=int(text)<=18:
            raise ValueError('invalid WIN5 winning horse')
        winners.append({'number':int(text),'name':names[0].get_text(strip=True)})
    def field(label):
        values=[]
        for node in soup.find_all(['th','td','dt']):
            if node.get_text(strip=True)!=label:
                continue
            if node.name=='dt':
                value=node.find_next_sibling('dd')
            else:
                value=node.find_next_sibling('td')
            if value:
                values.append(value.get_text('',strip=True))
        if len(set(values))!=1:
            raise PayoutNotReady('WIN5 '+label+' unavailable')
        return values[0]
    ticket_text=field('的中票数').replace(',','')
    ticket_match=re.fullmatch(r'(\d+)票',ticket_text)
    if not ticket_match:
        raise PayoutNotReady('WIN5 winning ticket count unconfirmed')
    tickets=int(ticket_match[1])
    amount=field('払戻金')
    if tickets==0:
        if amount not in {'的中なし','的中者なし','0円'}:
            raise ValueError('WIN5 no-winner payout disagreement')
        payment=0
    else:
        payment=yen(amount)
        if payment<=0:
            raise ValueError('positive payout required for winning WIN5 tickets')
    return {'raceDate':day,'raceIDs':actual_ids,'winners':winners,'winningTicketCount':tickets,
            'payoutPer100Yen':payment,'payoutStatus':'paid' if tickets else 'carryover_no_winning_tickets'}


def saved_payout(root,record,score,fetch=request_bytes,clock=lambda:datetime.now(JST)):
    plan=record['plan'];day=plan['raceDate'];ids=[r['race_id'] for r in plan['schedule']]
    path=Path(root)/'reports'/day.replace('-','')/'win5_payouts_v1/session.json'
    post=max(stamp(day+'T'+r['start_time']) for r in plan['schedule'])
    if clock()<post:
        raise PayoutNotReady('WIN5 final leg has not started')
    url='https://race.netkeiba.com/top/win5.html?date='+day.replace('-','')
    if path.exists():
        value=json.loads(path.read_text());fingerprint=value.pop('fingerprint')
        if (digest(value)!=fingerprint or value['recordFingerprint']!=record['recordFingerprint']
                or value['sourceURL']!=url or not post<=stamp(value['retrievedAtJST'])<=clock()):
            raise ValueError('WIN5 payout cache identity/time mismatch')
        document=value['payout']
    else:
        raw=fetch(url);document=payout_document(raw,day,ids)
        value={'recordFingerprint':record['recordFingerprint'],'sourceURL':url,
               'sourceFingerprint':hashlib.sha256(raw).hexdigest(),'retrievedAtJST':clock().isoformat(),
               'payout':document}
        value['fingerprint']=digest(value)
        # A mismatch must not poison a persistent result cache.
        verify_winners(record,score,document)
        atomic(path,(json.dumps(value,ensure_ascii=False)+'\n').encode())
    verify_winners(record,score,document)
    return document


def verify_winners(record,score,document):
    plan=record['plan']
    if (document['raceDate']!=plan['raceDate'] or document['raceIDs']!=[r['race_id'] for r in plan['schedule']]
            or len(document['winners'])!=5):
        raise ValueError('WIN5 payout identity mismatch')
    amount,tickets=document['payoutPer100Yen'],document['winningTicketCount']
    if (type(amount) is not int or type(tickets) is not int or amount<0 or tickets<0
            or (tickets>0 and (amount<=0 or document['payoutStatus']!='paid'))
            or (tickets==0 and (amount!=0 or document['payoutStatus']!='carryover_no_winning_tickets'))):
        raise ValueError('WIN5 published payout/ticket state mismatch')
    for leg,number,actual in zip(record['predictions'],score['winningOutcome'],document['winners']):
        names={h['number']:h['name'] for h in leg['prediction']['horses']}
        if type(actual['number']) is not int or actual['number']!=int(number) or names.get(actual['number'])!=actual['name']:
            raise ValueError('independent final result/WIN5 payout winner mismatch')
