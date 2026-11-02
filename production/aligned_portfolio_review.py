"""Read-only acceptance, disjoint cohorts and preregistered portfolio diagnostics.

Never fetch data, capture a prediction, select a live ticket, or alter v1 rules.
"""
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib
import json
import os
from pathlib import Path

import formal_portfolio_capture as capture
import portfolio_shadow as portfolio
from portfolio_review_100 import summary, race_key, validate_source
from formal_10min_evidence import inspect_formal_10min_evidence

VERSION='aligned-portfolio-review-v1'
JSON_NAME='AlignedPortfolioReview.json'
MD_NAME='AlignedPortfolioReview.md'
PROTOCOL_NAME='ALIGNED_PORTFOLIO_PROTOCOL.json'
PROTOCOL_SHA256='55ee84560014f5f4dec94996da103855c0d6a926f386973934922e894a293d79'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def protocol():
    path=Path(__file__).parent/PROTOCOL_NAME
    if hashlib.sha256(path.read_bytes()).hexdigest()!=PROTOCOL_SHA256:
        raise ValueError('registered analysis protocol changed; create a new version')
    data=read(path)
    if data['startDate']!=capture.START_DATE or data['capturePolicy']!=capture.POLICY:
        raise ValueError('capture/protocol contract mismatch')
    for name,sha in data['sourceHashes'].items():
        if hashlib.sha256((path.parent/name).read_bytes()).hexdigest()!=sha:
            raise ValueError('registered prediction/selection code changed: '+name)
    return data


def validate_bundle(root,day,rid,now,registered):
    """Recheck the exact input and decision binding, not just file existence."""
    import all_bet_forward as added
    import triple_forward as triple
    import build_bet_research_data as reference
    import build_signal_data_v2 as signals
    record=capture.load(root,day,rid);race=record['prediction']
    if day<capture.START_DATE or capture.stamp(record['recordedAtJST'])<registered:
        raise ValueError('capture predates registered aligned cohort')
    if capture.stamp(record['recordedAtJST'])>now:
        raise ValueError('future portfolio capture')
    checked=inspect_formal_10min_evidence(root,race)
    if not checked['eligible'] or checked.get('portfolioInputsVerified') is not True:
        raise ValueError('complete formal evidence not verified')
    folder=Path(root)/'reports'/day.replace('-','')
    rows={};sources={}
    for kind,family,validator in [('win','all_bet_v1',added.validate),('trio','triple_v1',triple.validate)]:
        source=validator(read(folder/family/kind/(rid+'.json')));sources[kind]=source
        if source['betType']!=kind or capture.digest(source['prediction'])!=capture.digest(race):
            raise ValueError('pool prediction binding mismatch')
        for key in ['predictionAvailableAtJST','receivedAtJST','recordedAtJST']:
            t=capture.window(day,race['startTime'],source[key])
            if t>capture.stamp(record['recordedAtJST']):raise ValueError('pool captured after plan')
        rows[(rid,kind)]={'betType':kind,'status':source['status'],
                         'candidates':reference.freeze_candidates(source,kind) if source['status']=='ready' else []}
    odds=read(folder/'wide_odds_live10'/(rid+'.json'))
    if any(odds.get(k)!=race.get(k) for k in ['raceDate','raceID','startTime']):
        raise ValueError('Wide identity mismatch')
    t=capture.window(day,race['startTime'],odds['capturedAtJST'])
    if t>capture.stamp(record['recordedAtJST']) or odds.get('frozenAtFirstSuccessfulCapture') is not True:
        raise ValueError('Wide captured after plan or not frozen')
    numbers={h['number'] for h in race['horses']}
    pairs={(min(p['number1'],p['number2']),max(p['number1'],p['number2'])) for p in odds['pairs']}
    if pairs!={(a,b) for a in numbers for b in numbers if a<b} or len(pairs)!=len(odds['pairs']):
        raise ValueError('Wide entrant pair coverage mismatch')
    signal=signals.build_live_race_signal(race,odds)
    if signal!=record['signal']:
        raise ValueError('saved signal not derived from bound T-10 inputs')
    if portfolio.build_race_plan(rid,day,rows,signal,{})!=record['plan']:
        raise ValueError('saved plan not derived from bound T-10 inputs')
    return record,sources


def wide_results(root,race,finish):
    path=Path(root)/'reports'/race['raceDate'].replace('-','')/'WideValidation.json'
    if not path.exists():return {}
    matches=[r for r in read(path)['races'] if r['raceID']==race['raceID']]
    if not matches:return {}
    if len(matches)!=1:raise ValueError('duplicate Wide payout race')
    row=matches[0];names={h['number']:h['name'] for h in race['horses']}
    if row.get('raceDate')!=race['raceDate'] or row.get('validationScope')!='all_pairs':
        raise ValueError('Wide result identity/scope mismatch')
    out={}
    for p in row['pairs']:
        a,b=sorted([p['number1'],p['number2']]);cid=f'{a:02}{b:02}'
        truth={a,b}<=set(finish);pay=p['payoutPer100Yen']
        if (a==b or cid in out or names.get(p['number1'])!=p.get('name1')
                or names.get(p['number2'])!=p.get('name2') or type(p['hit']) is not bool
                or p['hit']!=truth or type(pay) is not int or pay<0 or (pay>0)!=truth):
            raise ValueError('Wide finish/payout/entrant disagreement')
        out[cid]=(truth,pay)
    expected={f'{a:02}{b:02}' for a in names for b in names if a<b}
    if set(out)!=expected:raise ValueError('incomplete Wide payout coverage')
    return out


def pool_results(root,race,source,kind,finish,now):
    family='all_bet_payouts_v1' if kind=='win' else 'triple_payouts_v1'
    p=Path(root)/'reports'/race['raceDate'].replace('-','')/family/kind/(race['raceID']+'.json')
    if not p.exists():return None
    raw=read(p);unsigned={k:v for k,v in raw.items() if k!='fingerprint'}
    if (capture.digest(unsigned)!=raw.get('fingerprint') or raw['recordFingerprint']!=source['recordFingerprint']
            or raw.get('sourceURL')!='https://race.netkeiba.com/race/result.html?race_id='+race['raceID']
            or not capture.stamp(race['raceDate']+'T'+race['startTime'])<=capture.stamp(raw['retrievedAtJST'])<=now):
        raise ValueError('invalid bound payout receipt')
    truth=f'{finish[0]:02}' if kind=='win' else ''.join(f'{n:02}' for n in sorted(finish))
    payout=raw['payouts']
    if set(payout)!={truth} or type(payout[truth]) is not int or payout[truth]<=0:
        raise ValueError('pool finish/payout disagreement')
    return payout


def settle(root,record,sources,now,common):
    import all_bet_baseline as model
    import build_bet_research_data as reference
    race=record['prediction'];day,rid=race['raceDate'],race['raceID']
    plan=deepcopy(record['plan'])
    refs=portfolio.choose_wides(rid,record['signal'],{},common['minProbabilityPct'],common['minExpectedMultiplier'],limit=common['maxPairs'])
    folder=Path(root)/'reports'/day.replace('-','')
    actual=reference.checked_results(folder/'ResultData.json',day,now).get((day,rid))
    state,finish=model.result_state(race,actual)
    finishmap={h['number']:h.get('finishPosition') for h in actual['horses']} if actual else {}
    if state=='invalid':raise ValueError('invalid final result identity/finish')
    if state=='matched':
        wides=wide_results(root,race,finish) if refs or any(l['betType']=='wide' for l in plan['legs']) else {}
        needed={l['betType'] for l in plan['legs']}-{'wide'}
        pools={k:pool_results(root,race,sources[k],k,finish,now) for k in needed}
        for leg in plan['legs']+refs:
            cid=leg['candidateID'];kind=leg['betType']
            if kind=='wide':value=wides.get(cid)
            else:
                table=pools[kind];value=(cid in table,table.get(cid,0)) if table is not None else None
            if value is not None:leg['hit'],leg['payoutYen']=value
    plan.update(portfolio.evaluate_plan(plan['legs']))
    return {**plan,'inputFingerprint':record['fingerprint'],'inputRecordedAtJST':record['recordedAtJST'],
            'resultState':state,'actualFinish':finishmap,'commonWideReference':refs,
            'referenceSettled':state=='matched' and all(type(l.get('hit')) is bool and type(l.get('payoutYen')) is int for l in refs)}


def tickets(rows,kind=None,reference=False):
    return [l for r in rows for l in (r['commonWideReference'] if reference else r['legs']) if kind is None or l['betType']==kind]


def ticket_summary(legs):
    settled=[l for l in legs if type(l.get('hit')) is bool and type(l.get('payoutYen')) is int]
    stake=sum(l['unitYen'] for l in settled);pay=sum(l['payoutYen'] for l in settled)
    return {'committedStakeYen':sum(l['unitYen'] for l in legs),'tickets':len(legs),'settledTickets':len(settled),'pendingTickets':len(legs)-len(settled),
            'hitTickets':sum(l['hit'] for l in settled),'stakeYen':stake,'returnYen':pay,'profitYen':pay-stake,
            'returnIndex':round(pay/stake,6) if stake else None,
            'sumPredictedHits':round(sum(l['probabilityPct']/100 for l in settled),6)}


def diagnostics(rows):
    selected=[r for r in rows if r['planType']!='skip'];skipped=[r for r in rows if r['planType']=='skip']
    refs=lambda group:{'races':len(group),'racesWithCandidates':sum(bool(r['commonWideReference']) for r in group),
                       'fullySettledRaces':sum(r['referenceSettled'] for r in group),**ticket_summary(tickets(group,reference=True))}
    battle=[];cross=[]
    for b in ['S','A']:
        group=[r for r in rows if r['battleGrade']==b]
        battle.append({'battleGrade':b,**summary(group),'wide':ticket_summary(tickets(group,'wide'))})
        for c in [1,2,3,4,5]:cross.append({'battleGrade':b,'chaosLevel':c,**summary([r for r in group if r['chaosLevel']==c])})
    settled=[r for r in selected if r['settled']]
    paid=sorted([r for r in settled if r['returnYen']>0],key=lambda r:(-r['returnYen'],race_key(r)))
    concentration=[]
    for n in [0,1,2]:
        excluded={(r['raceDate'],r['raceID']) for r in paid[:n]}
        concentration.append({'removedLargestReturnRaces':n,'excludedRaceIDs':[r['raceID'] for r in paid[:n]],
                              **summary([r for r in settled if (r['raceDate'],r['raceID']) not in excluded])})
    combined=[r for r in rows if r['planType']=='wide_plus_trio']
    return {'sameWideRuleSelected':refs(selected),'sameWideRuleSkipped':refs(skipped),
            'battleWide':battle,'battleChaos':cross,'returnConcentration':concentration,
            'trioIncrement':ticket_summary(tickets(combined,'trio')),
            'wideOnlySameCombinedRaces':ticket_summary(tickets(combined,'wide')),
            'byDay':[{'raceDate':d,**summary([r for r in rows if r['raceDate']==d])} for d in sorted({r['raceDate'] for r in rows})]}


def lock_windows(rows, prior):
    """Once full, retain membership even if an input later vanishes or arrives late."""
    locks=deepcopy(prior.get('cohortLocks',{}));lookup={(r['raceDate'],r['raceID']):r for r in rows}
    windows={};used=set();problems=[]
    for name in ['first100','confirmation101to200']:
        keys=locks.get(name)
        if keys is None:
            candidates=[r for r in rows if (r['raceDate'],r['raceID']) not in used][:100]
            keys=[{'raceDate':r['raceDate'],'raceID':r['raceID'],'inputFingerprint':r['inputFingerprint']} for r in candidates]
            if len(keys)==100:locks[name]=keys
        current=[]
        for key in keys:
            ident=(key['raceDate'],key['raceID']);used.add(ident);r=lookup.get(ident)
            if r is None or r['inputFingerprint']!=key['inputFingerprint']:
                problems.append({'code':'locked_cohort_input_missing_or_changed','raceDate':ident[0],'raceID':ident[1]})
            else:current.append(r)
        windows[name]=current
    return windows,locks,problems


def beta_decision(observed,result_matched,acceptance_complete,issues,pending_selected):
    reasons=[]
    if observed<300:reasons.append('aligned_capture_count_below_300')
    if result_matched<300:reasons.append('aligned_result_count_below_300')
    if not acceptance_complete:reasons.append('first_live_acceptance_incomplete')
    if issues:reasons.append('aligned_integrity_issues')
    if pending_selected:reasons.append('selected_settlement_pending')
    return {'minimumRaces':300,'verifiedAlignedRaces':observed,'matchedResultRaces':result_matched,
            'eligibleForResearchBeta':not reasons,'blockReasons':reasons,
            'automaticEnable':False,'profitabilityProven':False}


def legacy_review(root):
    source=Path(root)/'PortfolioResearchData.json'
    if not source.exists():return {'status':'unavailable','racesObserved':None,'first100':None}
    data=read(source);validate_source(data)
    rows=[]
    for row in data['races']:
        if row.get('scope')=='prospective' and row['raceDate']<capture.START_DATE:
            if inspect_formal_10min_evidence(root,row,require_portfolio=False)['eligible']:rows.append(row)
    rows.sort(key=race_key)
    return {'status':'legacy_win_only_evidence','racesObserved':len(rows),'first100':summary(rows[:100]),
            'notIncludedInAlignedCount':True,'notProofOfAlignedPortfolio':True}


def build(root,now=None):
    from all_bet_recording_coverage import expected_races
    root=Path(root);now=now or datetime.now(capture.JST);p=protocol()
    registered=capture.stamp(p['registeredAtJST']);issues=[];valid=[];known=set()
    for path in sorted((root/'reports').glob('*/'+capture.FOLDER+'/*.json')):
        day=datetime.strptime(path.parent.parent.name,'%Y%m%d').date().isoformat();rid=path.stem
        if day<capture.START_DATE:continue
        known.add((day,rid))
        try:
            record,sources=validate_bundle(root,day,rid,now,registered)
            try:row=settle(root,record,sources,now,p['commonWideReference'])
            except (OSError,ValueError,KeyError,TypeError) as exc:
                issues.append({'code':'settlement_integrity','raceDate':day,'raceID':rid,'detail':str(exc)})
                row={**deepcopy(record['plan']),'inputFingerprint':record['fingerprint'],'inputRecordedAtJST':record['recordedAtJST'],
                     'resultState':'invalid','actualFinish':{},'commonWideReference':[],'referenceSettled':False}
            valid.append(row)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            issues.append({'code':'invalid_capture','raceDate':day,'raceID':rid,'detail':str(exc)})
    expected,warnings=expected_races(root,capture.stamp(capture.START_DATE+'T00:00:00+09:00'),now)
    # Also discover a published win sidecar if the daily display was lost.
    for path in sorted((root/'reports').glob('*/all_bet_v1/win/*.json')):
        if path.parent.parent.parent.name<capture.START_DATE.replace('-',''):continue
        try:expected.append(read(path)['prediction'])
        except (OSError,ValueError,KeyError,TypeError):issues.append({'code':'invalid_win_sidecar','path':str(path.relative_to(root))})
    for r in expected:
        key=(r['raceDate'],r['raceID'])
        if key in known:continue
        if capture.stamp(r['raceDate']+'T'+r['startTime'])-timedelta(minutes=8)<now:
            issues.append({'code':'capture_missing_after_window','raceDate':key[0],'raceID':key[1]});known.add(key)
    issues += [{'code':'schedule_integrity','detail':w} for w in warnings]
    valid.sort(key=race_key)
    captured=valid[0] if valid else None
    completed=next((r for r in valid if r['planType']!='skip' and r['settled'] and r['resultState']=='matched'),None)
    matched=sum(r['resultState']=='matched' for r in valid)
    total=summary(valid)
    prior=read(root/JSON_NAME) if (root/JSON_NAME).exists() else {}
    windows,locks,lock_issues=lock_windows(valid,prior);issues+=lock_issues
    first=windows['first100'];confirmation=windows['confirmation101to200']
    complete=captured is not None and completed is not None
    events=[]
    for name,row in [('first_capture_verified',captured),('first_selected_settlement_verified',completed)]:
        if row:events.append({'event':name,'key':name+':'+row['inputFingerprint'],'raceDate':row['raceDate'],'raceID':row['raceID']})
    decision=beta_decision(len(valid),matched,complete,issues,total['resultsPendingSelectedRaces'])
    for milestone in p['milestones']:
        if len(valid)>=milestone:
            events.append({'event':'aligned_capture_milestone','key':p['version']+':milestone:'+str(milestone),'races':milestone})
    if decision['eligibleForResearchBeta']:
        events.append({'event':'beta_research_ready','key':p['version']+':beta_research_ready'})
    return {'schemaVersion':1,'version':VERSION,'inputPolicy':capture.POLICY,
            'generatedAtJST':now.astimezone(capture.JST).isoformat(timespec='seconds'),'startDate':capture.START_DATE,
            'protocolVersion':p['version'],'protocolFingerprint':PROTOCOL_SHA256,
            'formalRecommendationsEnabled':False,'autoBetEnabled':False,'stakeSizingEnabled':False,
            'legacy':legacy_review(root),
            'aligned':{'racesObserved':len(valid),'matchedResultRaces':matched,'overall':total,
                       'first100':{'raceIDs':[r['raceID'] for r in first],'complete':len(first)==100,**summary(first),'diagnostics':diagnostics(first)},
                       'confirmation101to200':{'raceIDs':[r['raceID'] for r in confirmation],'complete':len(confirmation)==100,**summary(confirmation),'diagnostics':diagnostics(confirmation)}},
            'quality':{'status':'attention_required' if issues else 'collecting' if valid else 'waiting_for_first_capture',
                       'fullInputEvidenceRequired':True,'issues':issues,'invalidCaptureCount':sum(i['code']=='invalid_capture' for i in issues),
                       'missingCaptureCount':sum(i['code']=='capture_missing_after_window' for i in issues),
                       'resultExclusions':[{'raceDate':r['raceDate'],'raceID':r['raceID'],'reason':r['resultState']} for r in valid if r['resultState'] in ['entrant_changed','dead_heat_excluded']]},
            'acceptance':{'status':'complete' if complete else 'awaiting_first_settlement' if captured else 'awaiting_first_capture',
                          'firstCaptureVerified':captured is not None,'firstSelectedSettlementVerified':completed is not None,
                          'restartAndPublicationLiveVerified':False,'restartAndPublicationEvidence':'synthetic CI coverage only; live session restart is not inferred'},
            'betaDecision':decision,
            'cohortLocks':locks,'notificationEvents':events,'races':valid,
            'interpretation':'Legacy, aligned, post-hoc reconstruction and shadow variants must not be combined. ROI is descriptive; 300 races permits research beta review only.'}


def render(d):
    legacy=d['legacy'];a=d['aligned'];o=a['overall'];b=d['betaDecision']
    def roi(x):return '-' if x is None else f'{100*x:.2f}%'
    return '\n'.join(['# KeibaAI 入力整合系列の検証・監視','',
        f"- 旧系列（単勝T-10証拠のみ）：{legacy['racesObserved']}R。新系列に加算しません。",
        f"- 新系列（{d['startDate']}以降の一体記録）：{a['racesObserved']}R、結果照合済み {a['matchedResultRaces']}R。",
        f"- 選択 {o['selectedRaces']}R／確定選択 {o['settledSelectedRaces']}R／結果・払戻待ち {o['resultsPendingSelectedRaces']}R。",
        f"- 確定分：投資 {o['stakeYen']}円、払戻 {o['returnYen']}円、回収率 {roi(o['returnIndex'])}。未確定を0円扱いしません。",
        f"- 初回実レース確認：{d['acceptance']['status']}。実際の再起動・公開の確認は別項目です。",
        f"- 品質：{d['quality']['status']}、確認事項 {len(d['quality']['issues'])}件。",
        f"- β研究レビュー条件：入力整合300R以上・結果照合300R以上・初回精算確認・未解決の整合性問題なし・選択精算待ちなし。現在 {b['eligibleForResearchBeta']}。",
        f"- 登録済み仮説：{d['protocolVersion']}。参加ゲート、S/A、Chaos×Battleと利益集中、3連複の増分を固定条件で比較。",
        '- v1の重み・購入条件・金額を変更せず、最初100Rと101〜200Rを分離します。',
        '- 次開催の実データ未取得を、正常稼働や検証完了と解釈しません。',''])


def refresh(root,now=None):
    from live_bundle_publisher import atomic
    root=Path(root);data=build(root,now);target=root/JSON_NAME
    def material(v):return {k:x for k,x in v.items() if k!='generatedAtJST'}
    if target.exists() and material(read(target))==material(data) and (root/MD_NAME).exists() and (root/MD_NAME).read_text()==render(data):return False,read(target)
    atomic(target,(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode())
    atomic(root/MD_NAME,render(data).encode())
    return True,data


def main():
    root=Path(os.environ.get('KEIBAAI_ROOT',Path(__file__).resolve().parent.parent))
    changed,data=refresh(root)
    print(json.dumps({'changed':changed,'alignedRacesObserved':data['aligned']['racesObserved'],
                      'quality':data['quality']['status'],'acceptance':data['acceptance']['status'],
                      'betaDecision':data['betaDecision']},ensure_ascii=False))


if __name__=='__main__':main()
