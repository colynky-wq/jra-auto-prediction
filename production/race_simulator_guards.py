"""Condition coverage and prospective paired post-adoption monitoring."""
from __future__ import annotations
import random
import statistics
from dataclasses import replace

from race_simulator_calibration import digest

GROUP_LABELS = {'surface:turf':'芝', 'surface:dirt':'ダート',
    'distance:short':'短距離（1400m以下）', 'distance:mile':'マイル（1401〜1800m）',
    'distance:middle':'中距離（1801〜2200m）', 'distance:long':'長距離（2201m以上）'}


def conditions(race, distance):
    text = str(race.get('course') or race.get('track') or '')
    if '障' in text or '障害' in str(race.get('raceName') or ''):
        raise ValueError('obstacle race outside simulator scope')
    surface = 'dirt' if 'ダ' in text else 'turf' if '芝' in text else 'unknown'
    if surface == 'unknown': raise ValueError('surface unavailable before capture')
    band = 'short' if distance <= 1400 else 'mile' if distance <= 1800 else 'middle' if distance <= 2200 else 'long'
    return {'surface':surface, 'distanceBand':band, 'distanceMeters':distance}


def groups(row):
    c = row['record'].get('conditions') or {}
    surface, band = c.get('surface'), c.get('distanceBand')
    return {f'surface:{surface}', f'distance:{band}', f'combined:{surface}:{band}'}


def coverage(rows, policy):
    result=[]
    for key,label in GROUP_LABELS.items():
        selected=[r for r in rows if key in groups(r)]
        days=len({r['record']['raceDate'] for r in selected})
        sufficient=len(selected)>=policy.min_condition_races and days>=policy.min_condition_days
        result.append({'key':key,'label':label,'races':len(selected),'days':days,
                       'status':'sufficient' if sufficient else 'insufficient_data'})
    return result


def coverage_ready(rows, policy):
    if policy.min_condition_races == 0: return True
    return all(x['status']=='sufficient' for x in coverage(rows,policy))


def paired_lower(deltas, rows, draws, alpha=.05):
    rng=random.Random(digest([r['record']['recordSHA256'] for r in rows]))
    samples=sorted(statistics.mean(rng.choices(deltas,k=len(deltas))) for _ in range(draws))
    return samples[int(alpha*(len(samples)-1))]


def comparisons(rows, reference_id, active_id, policy):
    keys=list(GROUP_LABELS)
    keys+=sorted({g for row in rows for g in groups(row) if g.startswith('combined:')})
    result=[]
    for key in keys:
        selected=[r for r in rows if key in groups(r)]
        days=len({r['record']['raceDate'] for r in selected})
        enough=(len(selected)>=max(1,policy.min_condition_races) and days>=policy.min_condition_days)
        parts=key.split(':')
        label=GROUP_LABELS.get(key)
        if label is None:label=GROUP_LABELS.get('surface:'+parts[1],'不明')+' × '+GROUP_LABELS.get('distance:'+parts[2],'不明')
        item={'key':key,'label':label,
              'races':len(selected),'days':days,'status':'insufficient_data'}
        if selected:
            old=[r['evaluation']['scores'][reference_id] for r in selected]
            new=[r['evaluation']['scores'][active_id] for r in selected]
            before=statistics.mean(x['loss'] for x in old);after=statistics.mean(x['loss'] for x in new)
            relative=(before-after)/max(before,1e-12)
            item.update(beforeLoss=before,afterLoss=after,relativeImprovement=relative,
                metricChanges={k:statistics.mean(b[k]-a[k] for a,b in zip(old,new) if a[k] is not None and b[k] is not None)
                    for k in ('winnerBrier','normalizedRankMAE','winnerTimeMAPE','first600MAPE','last600MAPE')
                    if any(a[k] is not None and b[k] is not None for a,b in zip(old,new))})
            if enough:
                regression=(relative < -policy.max_condition_regression or
                    any(item['metricChanges'].get(k,0)>policy.max_condition_regression for k in ('winnerBrier','normalizedRankMAE')))
                item['status']='regressed' if regression else 'pass'
        result.append(item)
    return result


def monitor_decision(records, monitoring, current, reference, policy):
    from race_simulator_learning import timestamp
    policy=replace(policy,**monitoring.get('policy',{}))
    if monitoring['activeModelID'] != current['modelID'] or monitoring['referenceModelID'] != reference['modelID']:
        raise ValueError('post-adoption monitor model mismatch')
    used=set(monitoring.get('checkedRaceIDs',[]));eligible=[]
    for row in records:
        r=row['record'];scores=row['evaluation']['scores']
        if r['raceID'] in used or timestamp(r['recordedAt']) <= timestamp(monitoring['adoptedAt']):continue
        if r['activeModelID'] != current['modelID'] or r.get('monitorModelID') != reference['modelID']:continue
        if not {current['modelID'],reference['modelID']} <= scores.keys():continue
        for model in (current,reference):
            if r['forecasts'][model['modelID']]['model'] != model: raise ValueError('monitor forecast model changed')
        eligible.append(row)
        if len(eligible)>=policy.min_monitor_races and len({x['record']['raceDate'] for x in eligible})>=policy.min_monitor_days:break
    info={'status':'awaiting_monitor_results','races':len(eligible),
          'days':len({x['record']['raceDate'] for x in eligible}),'activeModelID':current['modelID'],
          'referenceModelID':reference['modelID'],'checkedCohorts':monitoring.get('checkedCohorts',0)}
    if len(eligible)<policy.min_monitor_races or info['days']<policy.min_monitor_days:return info
    checks=comparisons(eligible,reference['modelID'],current['modelID'],policy)
    # Fixed, disjoint cohorts avoid repeatedly testing a rolling sample. The
    # multiplicity adjustment includes the whole cohort and eligible subgroups.
    sufficient=[c for c in checks if c['status']!='insufficient_data']
    alpha=.05/(1+len(sufficient));reasons=[]
    def harm(rows):
        old=[r['evaluation']['scores'][reference['modelID']]['loss'] for r in rows]
        new=[r['evaluation']['scores'][current['modelID']]['loss'] for r in rows]
        delta=[b-a for a,b in zip(old,new)]
        relative=statistics.mean(delta)/max(statistics.mean(old),1e-12)
        lower=paired_lower(delta,rows,policy.bootstrap_draws,alpha)
        sustained=statistics.mean(delta[len(delta)//2:])>0
        return relative>=policy.rollback_relative_degradation and lower>0 and sustained,relative,lower
    bad,relative,lower=harm(eligible)
    if bad:reasons.append('overall_sustained_degradation')
    for c in sufficient:
        selected=[r for r in eligible if c['key'] in groups(r)]
        subgroup_bad,_,_=harm(selected)
        if subgroup_bad:reasons.append('condition_degradation:'+c['key'])
    info.update(status='rollback' if reasons else 'monitor_pass',reasons=reasons,
        checkedRaceIDs=[r['record']['raceID'] for r in eligible],relativeDegradation=relative,
        pairedLowerMeanDegradation=lower,bootstrapAlpha=alpha,conditionComparisons=checks)
    return info
