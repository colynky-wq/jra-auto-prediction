"""Read frozen research inputs and existing results; write reference UI data only.

No network, predictions, training, observations, wagers or changes to rules.
The single ordinary Result Sync writer publishes this projection every 5 min.
"""
from datetime import datetime
import heapq
import json
import math
from pathlib import Path

import all_bet_baseline as single
import all_bet_forward as added
import research_triple_baseline as triple_model
import triple_forward as triple
import wide_axis_rules as wide
import win5_research as win5
from evaluate_all_bet_forward import build as validation
from live_bundle_publisher import atomic
from all_bet_recording_coverage import expected_races, missing_reason

JST=added.JST
VERSION='bet-research-reference-v1-20261015'
CATALOG=[
    ('win','単勝','1着になる馬を当てる','既存のAI勝率から比較',added.START),
    ('place','複勝','発売条件に応じて2着または3着まで','保存した勝率を着順の組合せに換算',added.START),
    ('frame_quinella','枠連','1・2着の枠の組合せ。順番は問わない','同じ枠どうしも含めた比較',added.START),
    ('quinella','馬連','1・2着の馬の組合せ。順番は問わない','保存した勝率を着順の組合せに換算',added.START),
    ('wide','ワイド','選んだ2頭がともに3着以内','保存された穴軸・相手の固定評価',wide.START),
    ('exacta','馬単','1・2着の馬を順番どおりに当てる','保存した勝率を着順の組合せに換算',added.START),
    ('trio','3連複','1～3着の3頭の組合せ。順番は問わない','保存した勝率を使う比較基準',triple.START),
    ('trifecta','3連単','1～3着の3頭を順番どおりに当てる','保存した勝率を使う比較基準',triple.START),
    ('support','応援馬券','同じ馬の単勝と複勝を同額ずつ組み合わせる','単勝100円＋複勝100円の計200円',added.START),
    ('win5','WIN5','対象5レースすべての1着を当てる','5レース共通の締切前に別記録',added.START),
]
LABELS={
    'awaiting_results':'予測保存済み・結果待ち','matched':'結果・払戻を照合済み',
    'held':'条件不足で保留','payouts_pending':'確定払戻待ち','entrant_changed':'出走馬変更のため保留',
    'dead_heat_excluded':'同着のため集計から除外','invalid':'記録・結果の対応を要確認',
    'missing_record':'初回記録を未確認','no_candidate':'条件に合う候補なし','failed':'記録処理の確認が必要',
    'ready':'発走前の予測保存済み','no_target':'公式対象なし',
}


def read(path):
    return json.loads(path.read_text())


def deny_network(*_):
    raise OSError('reference projection never fetches source data')


def checked_results(path,day,now):
    values=triple_model.load_rows(path) if path.exists() else []
    if any(r['raceDate']!=day for r in values):
        raise ValueError('result cache day mismatch')
    if any(added.stamp(r['resultUpdatedAtJST'])>now for r in values):
        raise ValueError('future result cache')
    return triple_model.index_unique(values)


def counts(rows):
    return {'records':sum(r['hasRecord'] for r in rows),'saved':sum(r['isSaved'] for r in rows),
            'matched':sum(r['status']=='matched' for r in rows),
            'held':sum(r['status']=='held' for r in rows),
            'awaitingResults':sum(r['status'] in {'awaiting_results','ready'} for r in rows),
            'payoutsPending':sum(r['status']=='payouts_pending' for r in rows),
            'excluded':sum(r['status'] in {'entrant_changed','dead_heat_excluded'} for r in rows),
            'missingRecords':sum(r['status']=='missing_record' for r in rows),
            'invalid':sum(r['status'] in {'invalid','failed'} for r in rows),
            'noCandidate':sum(r['status']=='no_candidate' for r in rows)}


def row_for(race,kind,state,record=None,reason=None):
    recorded=record.get('recordedAtJST') if record else None
    return {'id':kind+':'+race['raceDate']+':'+race['raceID'],'betType':kind,
            'raceDate':race['raceDate'],'raceID':race['raceID'],'venue':race.get('venue',''),
            'raceNumber':race.get('raceNumber'),'raceName':race.get('raceName',''),
            'startTime':race.get('startTime'),'status':state,'statusLabel':LABELS[state],
            'reason':reason or LABELS[state], 'hasRecord':record is not None,
            'isSaved':bool(record and state not in {'held','invalid','failed'}),
            'recordedAtJST':recorded,'recordFingerprint':record.get('recordFingerprint') if record else None,
            'candidates':[]}


def candidate(key,names,ai,market,low=None,high=None,unit=100):
    return {'id':key,'label':' → '.join(names),'probabilityPct':ai*100,
            'marketProbabilityPct':market*100 if market is not None else None,
            'oddsLow':low,'oddsHigh':high,'expectedMultiplierLow':ai*low if low is not None else None,
            'expectedMultiplierHigh':ai*high if high is not None else None,
            'unitYen':unit,'hit':None,'payoutYen':None}


def freeze_candidates(record,kind):
    race=record['prediction'];horses=race['horses'];names={h['number']:h['name'] for h in horses}
    if kind in single.TYPES:
        ai,market=(single.probabilities(horses,k,kind,record.get('entryEvidence')) for k in ('aiProbability','marketProbability'))
        odds=record['odds']
    else:
        index=1 if kind=='trio' else 0
        ai,market=({single.outcome_key(t):p for t,p in triple_model.distributions(horses,k)[index].items()}
                   for k in ('aiProbability','marketProbability'))
        odds={k:{'low':v,'high':v} for k,v in record['odds'].items()}
    out=[]
    for key in sorted(ai,key=lambda k:(-ai[k],k))[:3]:
        numbers=[int(key[i:i+2]) for i in range(0,len(key),2)]
        if kind=='frame_quinella':
            frames=single.frame_map(record['entryEvidence'],horses)
            labels=[f'{n}枠（'+ '・'.join(names[i] for i in sorted(frames) if frames[i]==n)+'）' for n in numbers]
        else:
            labels=[f'{n}番 {names[n]}' for n in numbers]
        value=candidate(key,labels,ai[key],market[key],odds[key]['low'],odds[key]['high'])
        value['label']=(' → ' if kind in {'exacta','trifecta'} else ' − ').join(labels)
        out.append(value)
    return out


def best_win5(record):
    """Exact top-three of a factorized distribution, without expanding millions."""
    vectors=[]
    for leg in record['predictions']:
        horses=leg['prediction']['horses']
        weights=single.win_weights(horses,'aiProbability')
        vectors.append(sorted(horses,key=lambda h:(-weights[h['number']],h['number'])))
    def make(indices):
        ai=market=1.;labels=[];numbers=[]
        for index,vector,leg in zip(indices,vectors,record['predictions']):
            h=vector[index];r=leg['prediction'];numbers.append(h['number'])
            ai*=single.win_weights(r['horses'],'aiProbability')[h['number']]
            market*=single.win_weights(r['horses'],'marketProbability')[h['number']]
            labels.append(f"{r['venue']}{r['raceNumber']}R：{h['number']}番 {h['name']}")
        value=candidate(single.outcome_key(numbers),labels,ai,market)
        return ai,value
    first=(0,)*5;seen={first};p,_=make(first);heap=[(-p,first)];out=[]
    while heap and len(out)<3:
        _,indices=heapq.heappop(heap);_,value=make(indices);out.append(value)
        for i in range(5):
            next_indices=list(indices);next_indices[i]+=1;next_indices=tuple(next_indices)
            if next_indices in seen or next_indices[i]>=len(vectors[i]):continue
            seen.add(next_indices);p,_=make(next_indices);heapq.heappush(heap,(-p,next_indices))
    return out


def wide_row(root,path,clock):
    value=read(path);data=dict(value);fingerprint=data.pop('recordFingerprint')
    if (wide.fingerprint(data)!=fingerprint or value['ruleVersion']!=wide.RULE_VERSION
            or value['raceID']!=path.stem or value['raceDate'].replace('-','')!=path.parent.parent.name):
        raise ValueError('invalid Wide frozen record')
    post=added.stamp(value['raceDate']+'T'+value['startTime']);recorded=added.stamp(value['recordedAtJST'])
    e=value['evaluation'];captured=added.stamp(e['capturedAtJST'])
    if not wide.START<=recorded<post or not captured<=recorded<=clock() or captured.date()!=post.date():
        raise ValueError('invalid Wide saved times')
    race={**value,'horses':value['entrants']};single.roster(race['horses'])
    state='no_candidate' if e['status']=='no_candidate' else 'awaiting_results'
    if e['status'] not in {'ready','no_candidate'} or e['ruleVersion']!=wide.RULE_VERSION:
        raise ValueError('invalid Wide saved evaluation')
    if state=='no_candidate':return row_for(race,'wide',state,value,e['message'])
    results=checked_results(path.parent.parent/'ResultData.json',race['raceDate'],clock())
    actual=results.get((race['raceDate'],race['raceID']));state,finish=single.result_state(race,actual)
    if actual and added.stamp(actual['resultUpdatedAtJST'])>clock():raise ValueError('future Wide result')
    payouts={}
    if state=='matched':
        cache=path.parent.parent/'WideValidation.json'
        state='payouts_pending'
        if cache.exists():
            found=[r for r in read(cache)['races'] if r['raceID']==race['raceID']]
            if len(found)>1:raise ValueError('duplicate Wide payout race')
            if found:
                r=found[0];all_pairs=r['pairs'];entrants=single.roster(race['horses'])
                if r.get('validationScope')!='all_pairs':
                    raise ValueError('Wide payout scope mismatch')
                snapshot=added.stamp(r['snapshotCapturedAtJST'])
                if snapshot.date()!=post.date() or snapshot>=post:
                    raise ValueError('Wide payout snapshot time mismatch')
                keys={}
                for p in all_pairs:
                    a,b=int(p['number1']),int(p['number2']);key=tuple(sorted((a,b)))
                    expected_names={entrants.get(a),entrants.get(b)}
                    actual_names={p.get('name1'),p.get('name2')}
                    if (a==b or key in keys or a not in entrants or b not in entrants
                            or expected_names!=actual_names):
                        raise ValueError('Wide payout entrant mismatch')
                    keys[key]=p
                if len(keys)!=math.comb(len(entrants),2):raise ValueError('incomplete Wide payout coverage')
                for p in e['pairs']:
                    k=tuple(sorted((int(p['number1']),int(p['number2']))))
                    got=keys.get(k)
                    if got is None:raise ValueError('missing Wide payout pair')
                    hit=set(k)<=set(finish)
                    if (type(got['payoutPer100Yen']) is not int or got['payoutPer100Yen']<0
                            or type(got.get('hit')) is not bool or got['hit'] is not hit
                            or (got['payoutPer100Yen']>0)!=hit):
                        raise ValueError('Wide payout/finish disagreement')
                    payouts[single.outcome_key(k)]=(hit,got['payoutPer100Yen'])
                state='matched'
    out=row_for(race,'wide',state,value)
    if state not in {'invalid','entrant_changed','dead_heat_excluded'}:
        for p in e['pairs']:
            ai=single.finite(p['probabilityPct'])/100;low=single.finite(p['oddsLow']);high=single.finite(p['oddsHigh'])
            if not 0<=ai<=1 or not 1<=low<=high:raise ValueError('invalid Wide candidate')
            key=single.outcome_key((p['number1'],p['number2']))
            c=candidate(key,[f"{p['number1']}番 {p['name1']}",f"{p['number2']}番 {p['name2']}"],ai,None,low,high)
            if key in payouts:c['hit'],c['payoutYen']=payouts[key]
            out['candidates'].append(c)
    return out


def build(root,clock=lambda:datetime.now(JST)):
    root=Path(root);now=clock();rows=[];warnings=[];records={}
    current=validation(root,fetch=deny_network,clock=clock)
    states={(r['raceDate'],r['raceID'],r['betType']):r for r in current['races']}
    for family,kinds,check in [('all_bet_v1',set(single.TYPES),added.validate),('triple_v1',set(triple.TYPES),triple.validate)]:
        for path in sorted((root/'reports').glob('*/'+family+'/*/*.json')):
            kind=path.parent.name
            if kind not in kinds:continue
            try:
                record=check(read(path));race=record['prediction'];day=race['raceDate']
                if path.stem!=race['raceID'] or path.parent.parent.parent.name!=day.replace('-','') or added.stamp(record['recordedAtJST'])>now:
                    raise ValueError('research record path/time mismatch')
                key=(day,race['raceID'],kind)
                checked_results(root/'reports'/day.replace('-','')/'ResultData.json',day,now)
                if family=='triple_v1':
                    # The existing evaluator is read-only when payout network
                    # is disabled, so its original result guards stay in force.
                    from evaluate_triple_forward import build as triple_validation
                    if 'triple_states' not in records:
                        v=triple_validation(root,fetch=deny_network,clock=clock)
                        records['triple_states']={(r['raceDate'],r['raceID'],r['betType']):r for r in v['races']}
                    found=records['triple_states'].get(key,{})
                else:found=states.get(key,{})
                state=found.get('status','invalid')
                reason=None
                if state=='held':reason='発売条件・公式出馬表・実オッズのいずれかを確認できず、初回記録を保留しています。'
                out=row_for(race,kind,state,record,reason)
                if record['status']=='ready' and state not in {'invalid','entrant_changed','dead_heat_excluded'}:
                    out['candidates']=freeze_candidates(record,kind)
                    if state=='matched':
                        folder='all_bet_payouts_v1' if family=='all_bet_v1' else 'triple_payouts_v1'
                        payouts=read(root/'reports'/day.replace('-','')/folder/kind/(race['raceID']+'.json'))['payouts']
                        for c in out['candidates']:c.update(hit=c['id'] in payouts,payoutYen=payouts.get(c['id'],0))
                rows.append(out);records[key]=record
            except (OSError,ValueError,KeyError,TypeError,OverflowError):
                day=datetime.strptime(path.parent.parent.parent.name,'%Y%m%d').date().isoformat()
                out=row_for({'raceDate':day,'raceID':path.stem},kind,'invalid');out['hasRecord']=True;rows.append(out)
    # Equal Win+Place package uses the same two actual frozen inputs.
    for key,record in list(records.items()):
        if not isinstance(key,tuple) or key[2]!='win':continue
        place=records.get((key[0],key[1],'place'))
        if place is None:continue
        race=record['prediction'];found=states.get((key[0],key[1],'support'),{})
        state=found.get('status','invalid');out=row_for(race,'support',state,record)
        if (record['status']==place['status']=='ready' and added.digest(race)==added.digest(place['prediction'])
                and state not in {'invalid','entrant_changed','dead_heat_excluded','held'}):
            pa,pm=(single.probabilities(race['horses'],k,'place',place['entryEvidence']) for k in ('aiProbability','marketProbability'))
            wa,wm=(single.probabilities(race['horses'],k,'win') for k in ('aiProbability','marketProbability'))
            names={f"{h['number']:02}":h['name'] for h in race['horses']}
            for n in sorted(pa,key=lambda k:(-pa[k],k))[:3]:
                c=candidate(n,[f'{int(n)}番 {names[n]}'],pa[n],pm[n],unit=200)
                c['expectedMultiplierLow']=(wa[n]*record['odds'][n]['low']+pa[n]*place['odds'][n]['low'])/2
                c['expectedMultiplierHigh']=(wa[n]*record['odds'][n]['high']+pa[n]*place['odds'][n]['high'])/2
                c['winProbabilityPct']=wa[n]*100
                if state=='matched':
                    base=root/'reports'/key[0].replace('-','')/'all_bet_payouts_v1'
                    w,p=(read(base/kind/(key[1]+'.json'))['payouts'] for kind in ('win','place'))
                    c.update(hit=n in p,payoutYen=w.get(n,0)+p.get(n,0))
                out['candidates'].append(c)
        rows.append(out)
    for path in sorted((root/'reports').glob('*/wide_axis_v1/*.json')):
        try:rows.append(wide_row(root,path,clock))
        except (OSError,ValueError,KeyError,TypeError,OverflowError):
            day=datetime.strptime(path.parent.parent.name,'%Y%m%d').date().isoformat()
            out=row_for({'raceDate':day,'raceID':path.stem},'wide','invalid');out['hasRecord']=True;rows.append(out)
    win_states={r['raceDate']:r for r in current['win5']['races']}
    for path in sorted((root/'reports').glob('*/win5_v1/session.json')):
        day=datetime.strptime(path.parent.parent.name,'%Y%m%d').date().isoformat()
        try:
            checked_results(path.parent.parent/'ResultData.json',day,now)
            record=win5.validate(read(path))
            if record['plan']['raceDate']!=day or added.stamp(record['recordedAtJST'])>now:raise ValueError('WIN5 path/time mismatch')
            found=win_states.get(day,{});state=found.get('status','invalid')
            if state=='probability_matched':state='payouts_pending'
            race={'raceDate':day,'raceID':'WIN5-'+day.replace('-',''),'raceName':'WIN5 対象5レース',
                  'startTime':record['plan']['deadlineJST'][11:16]}
            out=row_for(race,'win5',state,record)
            out['deadlineJST']=record['plan']['deadlineJST']
            if record['status']=='ready' and state not in {'invalid','entrant_changed','dead_heat_excluded'}:
                out['candidates']=best_win5(record)
                if state=='matched':
                    truth=''.join(found['winningOutcome']);payout=found['payout']['payoutPer100Yen']
                    if found['payout']['payoutStatus']=='carryover_no_winning_tickets':
                        out['reason']='的中票がなく、発表された払戻は0円（キャリーオーバー）です。'
                    for c in out['candidates']:c.update(hit=c['id']==truth,payoutYen=payout if c['id']==truth else 0)
            rows.append(out)
        except (OSError,ValueError,KeyError,TypeError,OverflowError):
            out=row_for({'raceDate':day,'raceID':'WIN5-'+day.replace('-','')},'win5','invalid');out['hasRecord']=True;rows.append(out)
    for session in current['win5']['sessions']:
        day=session['raceDate']
        if any(r['betType']=='win5' and r['raceDate']==day for r in rows):continue
        state='no_target' if session.get('hasTarget') is False and session['status']=='no_official_win5_target' else 'failed'
        rows.append(row_for({'raceDate':day,'raceID':'WIN5-'+day.replace('-','')},'win5',state,
                            reason='JRAの当日対象表を確認しました。' if state=='no_target' else '昼の記録処理が完了した根拠を確認できません。'))
    # Scheduled-but-failed forecasts are also omissions; future races still wait.
    expected, coverage_warnings = expected_races(root,min(c[4] for c in CATALOG),now)
    warnings.extend(coverage_warnings)
    for race in expected:
        for kind,_,_,_,start in CATALOG:
            if kind=='win5' or added.stamp(race['raceDate']+'T'+race['startTime'])<start:continue
            if not any(r['raceDate']==race['raceDate'] and r['raceID']==race['raceID'] and r['betType']==kind for r in rows):
                rows.append(row_for(race,kind,'missing_record',reason=missing_reason(root,race,now)))
    rows.sort(key=lambda r:(r['raceDate'],r['startTime'] or '',r['raceID'],r['betType']))
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('duplicate projected research row')
    all_counts={kind:counts([r for r in rows if r['betType']==kind]) for kind,_,_,_,_ in CATALOG}
    if current['warnings'] or current['win5']['warnings']:warnings.append('一部の研究記録・集計の対応に確認が必要です。')
    attention=bool(warnings) or any(r['status'] in {'invalid','failed','missing_record'} for r in rows)
    return {'schemaVersion':1,'version':VERSION,'status':'reference_display_only',
            'referenceDisplayEnabled':True,'formalRecommendationsEnabled':False,'autoBetEnabled':False,
            'stakeSizingEnabled':False,'generatedAtJST':now.isoformat(),'raceDate':now.date().isoformat(),
            'collectionStatus':'attention_required' if attention else 'collecting',
            'availableDates':sorted({r['raceDate'] for r in rows},reverse=True),
            'catalog':[{'id':kind,'label':label,'description':description,'methodNote':method,
                        'recordingStartDate':start.date().isoformat(),'countUnit':'開催日' if kind=='win5' else 'レース',
                        'cumulative':all_counts[kind]} for kind,label,description,method,start in CATALOG],
            'races':rows,'warnings':warnings,
            'meaning':'研究中の参考候補です。追加5券種・3連複・3連単・WIN5はAI確率順の最大3候補。おすすめや購入指示への採用は検証中です。'}


def main():
    root=Path(__file__).resolve().parent.parent;value=build(root)
    rows=value['races'];days=sorted({r['raceDate'] for r in rows}|{value['raceDate']})
    for day in days:
        dated={**value,'raceDate':day,'races':[r for r in rows if r['raceDate']==day]}
        atomic(root/'reports'/day.replace('-','')/'BetResearchData.json',(json.dumps(dated,ensure_ascii=False,indent=2)+'\n').encode())
    value['races']=[r for r in rows if r['raceDate']==value['raceDate']]
    atomic(root/'BetResearchData.json',(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode())
    print('BET_RESEARCH_REFERENCE_DISPLAY',len(value['catalog']),'types',len(rows),'rows',value['collectionStatus'])


if __name__=='__main__':main()
