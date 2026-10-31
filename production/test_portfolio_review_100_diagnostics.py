from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio_review_100_diagnostics as d

b=d.bands('芝1600m',13,1,40.0,2.5)
assert b['surface']=='芝' and b['distanceM']==1600 and b['distanceBand']=='1300-1600m'
assert b['fieldSizeBand']=='11-13頭' and b['topHorsePopularityBand']=='1人気'

review={
 'version':d.REVIEW_VERSION,'sourceVersion':d.SOURCE_VERSION,'first100CohortLockedByRule':True,
 'reviewWindowRaces':100,'racesObserved':100,'remainingTo100':0,'reviewStatus':'ready_for_short_term_review',
 'generatedAtJST':'2026-11-01T16:30:00+09:00','reviewWindowStartDate':'2026-10-24','reviewWindowEndDate':'2026-11-01'}
assert d.should_generate(review)
assert not d.should_generate({**review,'reviewWindowRaces':99,'remainingTo100':1})
assert not d.should_generate({**review,'reviewStatus':'target_reached_results_pending'})

with TemporaryDirectory() as td:
 root=Path(td); cohort=[]; results_by_day={}
 for i in range(100):
  day='2026-10-24' if i<50 else '2026-11-01'; rid=f'R{i:03d}'
  plan='skip' if i%4==0 else ('wide_focus' if i%2==0 else 'wide_plus_trio')
  selected=plan!='skip'; hit=selected and i%7==0
  legs=[] if not selected else [{'betType':'wide','unitYen':100,'hit':hit,'payoutYen':300 if hit else 0}]
  cohort.append({'scope':'prospective','ruleVersion':d.SOURCE_VERSION,'raceDate':day,'raceID':rid,'venue':'東京' if i%2==0 else '京都','raceNumber':i%12+1,'startTime':'12:00','chaosLevel':5 if selected else 3,'battleGrade':'S' if i%3==0 else 'A','planType':plan,'reason':'条件不足' if not selected else 'ok','legs':legs,'settled':selected,'hitAny':hit if selected else None,'profitable':hit if selected else None,'torigami':False if selected else None,'totalStakeYen':100 if selected else 0,'returnYen':(300 if hit else 0) if selected else None})
  p=root/'reports'/day.replace('-','')/'all_bet_v1'/'win'; p.mkdir(parents=True,exist_ok=True)
  horses=[{'number':n,'name':f'H{n}','popularity':n,'odds':1.5+n,'aiProbability':40.0 if n==1 else (30.0 if n==2 else 20.0 if n==3 else max(1.0,10.0-n/2))} for n in range(1,14)]
  (p/f'{rid}.json').write_text(json.dumps({'prediction':{'predictionTiming':'live_10min','course':'芝1600m' if i%2==0 else 'ダ1400m','horses':horses}},ensure_ascii=False),encoding='utf-8')
  winner=1 if i%2==0 else 2
  second=2 if winner==1 else 1
  results_by_day.setdefault(day,[]).append({'raceID':rid,'raceDate':day,'horses':[{'number':winner,'finishPosition':1},{'number':second,'finishPosition':2},{'number':3,'finishPosition':3}]})
 for day,races in results_by_day.items():
  p=root/'reports'/day.replace('-',''); p.mkdir(parents=True,exist_ok=True)
  (p/'ResultData.json').write_text(json.dumps(races,ensure_ascii=False),encoding='utf-8')
 out=d.build_diagnostics(root,review,cohort)
 assert out['overall']['races']==100
 assert out['qualityChecks']['formalSnapshotCoverageRaces']==100
 assert out['qualityChecks']['resultCoverageRaces']==100
 assert out['modelAccuracy']['top1WinRaces']==50, out['modelAccuracy']
 assert out['modelAccuracy']['winnerInModelTop3Races']==100
 assert out['modelAccuracy']['meanWinnerPredictedRank']==1.5
 assert sum(x['races'] for x in out['breakdowns']['selectionClass'])==100
 assert len(out['ticketBreakdown'])==1 and out['ticketBreakdown'][0]['betType']=='wide'
 md=d.render_markdown(out)
 assert '本命1着率' in md and '勝馬Top3率' in md and '選択 vs 見送り' in md
 # Missing one result must be visible and must fail the final 100R output gate.
 last_day='2026-11-01'; rp=root/'reports'/last_day.replace('-','')/'ResultData.json'
 races=json.loads(rp.read_text(encoding='utf-8')); rp.write_text(json.dumps(races[:-1],ensure_ascii=False),encoding='utf-8')
 out99=d.build_diagnostics(root,review,cohort)
 assert out99['qualityChecks']['resultCoverageRaces']==99
 # generate() must not write final reports while results are incomplete.
 (root/'PortfolioReview100.json').write_text(json.dumps(review),encoding='utf-8')
 (root/'PortfolioResearchData.json').write_text(json.dumps({'races':cohort},ensure_ascii=False),encoding='utf-8')
 d.first100=lambda _root: cohort
 changed,status=d.generate(root)
 assert (changed,status)==(False,'results_pending')
 assert not (root/d.JSON_NAME).exists() and not (root/d.MD_NAME).exists()
print('portfolio 100-race enhanced diagnostics tests OK')
