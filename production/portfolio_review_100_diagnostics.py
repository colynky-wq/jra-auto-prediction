"""Frozen diagnostics for the first 100 verified formal T-10 races.

Evaluation only. This file never changes model, selection, ticket or stake rules.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import json, math, os, re
from pathlib import Path
from typing import Any
from portfolio_review_100 import (
    PROSPECTIVE_START, REVIEW_VERSION, SOURCE_VERSION, TARGET_RACES,
    attach_formal_evidence, race_key, read_json, summary,
)

VERSION = "portfolio-review-100-diagnostics-v1-20261031"
JSON_NAME = "PortfolioReview100Diagnostics.json"
MD_NAME = "PortfolioReview100Diagnostics.md"
ORDERS = {
    "battleGrade": ["S", "A", "B", "C", "見送り"],
    "chaosLevel": [1, 2, 3, 4, 5],
    "planType": ["win_single", "win_plus_wide", "wide_focus", "wide_plus_trio", "skip"],
    "selectionClass": ["selected", "skip"],
    "surface": ["芝", "ダ", "障害", "不明"],
    "distanceBand": ["<=1200m", "1300-1600m", "1700-2000m", "2100-2400m", "2500m+", "不明"],
    "fieldSizeBand": ["<=10頭", "11-13頭", "14-16頭", "17頭+", "不明"],
    "topHorsePopularityBand": ["1人気", "2-3人気", "4-6人気", "7人気+", "不明"],
    "topHorseAiProbabilityBand": ["<10%", "10-20%", "20-30%", "30-40%", "40%+", "不明"],
    "topHorseOddsBand": ["<3.0", "3.0-5.9", "6.0-11.9", "12.0-29.9", "30.0+", "不明"],
}


def as_int(v: Any) -> int | None:
    try:
        return None if isinstance(v, bool) else int(v)
    except (TypeError, ValueError):
        return None


def finite(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def should_generate(r: dict[str, Any]) -> bool:
    return (
        r.get("version") == REVIEW_VERSION and r.get("sourceVersion") == SOURCE_VERSION
        and r.get("first100CohortLockedByRule") is True
        and r.get("reviewWindowRaces") == TARGET_RACES
        and int(r.get("racesObserved") or 0) >= TARGET_RACES
        and int(r.get("remainingTo100") if r.get("remainingTo100") is not None else -1) == 0
        and r.get("reviewStatus") == "ready_for_short_term_review"
    )


def bands(course: Any, field: int, pop: int | None, prob: float | None, odds: float | None) -> dict[str, Any]:
    text = str(course or "")
    surface = "障害" if "障" in text else "芝" if "芝" in text else "ダ" if "ダ" in text else "不明"
    m = re.search(r"(\d{3,4})\s*m", text); dist = int(m.group(1)) if m else None
    db = "不明" if dist is None else "<=1200m" if dist <= 1200 else "1300-1600m" if dist <= 1600 else "1700-2000m" if dist <= 2000 else "2100-2400m" if dist <= 2400 else "2500m+"
    fb = "<=10頭" if field <= 10 else "11-13頭" if field <= 13 else "14-16頭" if field <= 16 else "17頭+"
    pb = "不明" if pop is None else "1人気" if pop == 1 else "2-3人気" if pop <= 3 else "4-6人気" if pop <= 6 else "7人気+"
    qb = "不明" if prob is None else "<10%" if prob < 10 else "10-20%" if prob < 20 else "20-30%" if prob < 30 else "30-40%" if prob < 40 else "40%+"
    ob = "不明" if odds is None else "<3.0" if odds < 3 else "3.0-5.9" if odds < 6 else "6.0-11.9" if odds < 12 else "12.0-29.9" if odds < 30 else "30.0+"
    return {"surface": surface, "distanceM": dist, "distanceBand": db, "fieldSize": field, "fieldSizeBand": fb,
            "topHorsePopularityBand": pb, "topHorseAiProbabilityBand": qb, "topHorseOddsBand": ob}


def snapshot(root: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    p = root / "reports" / str(row.get("raceDate", "")).replace("-", "") / "all_bet_v1" / "win" / f"{row.get('raceID')}.json"
    if not p.exists(): return None
    pred = read_json(p).get("prediction", {})
    hs = pred.get("horses") if pred.get("predictionTiming") == "live_10min" else None
    valid = [h for h in hs or [] if isinstance(h, dict) and finite(h.get("aiProbability")) is not None and as_int(h.get("number")) is not None]
    if not valid: return None
    rank = sorted(valid, key=lambda h: (-(finite(h.get("aiProbability")) or -1), as_int(h.get("number")) or 999))
    top = rank[0]; pop = as_int(top.get("popularity")); prob = finite(top.get("aiProbability")); odds = finite(top.get("odds"))
    return {**bands(pred.get("course"), len(hs), pop, prob, odds), "course": pred.get("course"),
            "topHorseNumber": as_int(top.get("number")), "topHorseName": top.get("name"), "topHorsePopularity": pop,
            "topHorseAiProbabilityPct": prob, "topHorseOdds": odds,
            "modelRankingNumbers": [as_int(h.get("number")) for h in rank], "modelTop3Numbers": [as_int(h.get("number")) for h in rank[:3]]}


def result_index(root: Path, cohort: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out = {}
    for day in sorted({str(r.get("raceDate") or "") for r in cohort}):
        p = root / "reports" / day.replace("-", "") / "ResultData.json"
        if not p.exists(): continue
        data = read_json(p); races = data if isinstance(data, list) else data.get("races") if isinstance(data, dict) else None
        for race in races or []:
            if isinstance(race, dict) and isinstance(race.get("raceID"), str): out[(day, race["raceID"])] = race
    return out


def enrich(root: Path, row: dict[str, Any], results: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    x = deepcopy(row); x["selectionClass"] = "skip" if row.get("planType") == "skip" else "selected"
    s = snapshot(root, row); x["diagnosticSnapshotAvailable"] = s is not None
    x.update(s or {"surface":"不明","distanceBand":"不明","fieldSizeBand":"不明","topHorsePopularityBand":"不明",
                   "topHorseAiProbabilityBand":"不明","topHorseOddsBand":"不明","topHorseNumber":None,"modelRankingNumbers":[],"modelTop3Numbers":[]})
    race = results.get((str(row.get("raceDate") or ""), str(row.get("raceID") or "")), {})
    norm = [(as_int(h.get("number")), as_int(h.get("finishPosition"))) for h in race.get("horses", []) if isinstance(h, dict)]
    winners = [n for n,f in norm if n is not None and f == 1]; actual3 = {n for n,f in norm if n is not None and f is not None and 1 <= f <= 3}
    top = x.get("topHorseNumber"); top_finish = next((f for n,f in norm if n == top), None); rank = x.get("modelRankingNumbers") or []; model3 = set(x.get("modelTop3Numbers") or [])
    wr = [rank.index(n)+1 for n in winners if n in rank]
    x.update({"resultAvailable": bool(winners), "topHorseWon": bool(winners) and top in winners,
              "topHorseTop3": top_finish is not None and top_finish <= 3,
              "winnerInModelTop3": bool(winners) and any(n in model3 for n in winners),
              "winnerPredictedRank": min(wr) if wr else None,
              "modelTop3ActualTop3Count": len(model3 & actual3) if winners else None})
    return x


def accuracy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rr = [r for r in rows if r.get("resultAvailable") is True]; n = len(rr)
    def rate(k): return round(k/n*100,2) if n else None
    w = sum(r.get("topHorseWon") is True for r in rr); t = sum(r.get("topHorseTop3") is True for r in rr); q = sum(r.get("winnerInModelTop3") is True for r in rr)
    ranks = [r["winnerPredictedRank"] for r in rr if isinstance(r.get("winnerPredictedRank"), int)]
    cap = [r["modelTop3ActualTop3Count"] for r in rr if isinstance(r.get("modelTop3ActualTop3Count"), int)]
    return {"resultRaces":n,"top1WinRaces":w,"top1WinRatePct":rate(w),"top1Top3Races":t,"top1Top3RatePct":rate(t),
            "winnerInModelTop3Races":q,"winnerInModelTop3RatePct":rate(q),
            "meanWinnerPredictedRank":round(sum(ranks)/len(ranks),3) if ranks else None,
            "meanActualTop3CapturedByModelTop3":round(sum(cap)/len(cap),3) if cap else None}


def diag_summary(rows):
    x = summary(rows); x.update(accuracy(rows)); return x


def breakdown(rows, field):
    groups = {}; [groups.setdefault(r.get(field), []).append(r) for r in rows]
    order = {v:i for i,v in enumerate(ORDERS.get(field, []))}
    return [{**diag_summary(groups[k]), field:k} for k in sorted(groups, key=lambda k:(order.get(k,999),str(k)))]


def tickets(rows):
    groups = {}
    for r in rows:
        if r.get("planType") == "skip": continue
        for leg in r.get("legs") or []:
            if isinstance(leg, dict): groups.setdefault(str(leg.get("betType") or "unknown"), []).append(leg)
    out=[]
    for kind, legs in sorted(groups.items()):
        settled=[x for x in legs if isinstance(x.get("hit"),bool) and isinstance(x.get("payoutYen"),int)]
        stake=sum(int(x.get("unitYen") or 0) for x in settled); ret=sum(int(x.get("payoutYen") or 0) for x in settled)
        out.append({"betType":kind,"tickets":len(legs),"settledTickets":len(settled),"hitTickets":sum(x.get("hit") is True for x in settled),
                    "stakeYen":stake,"returnYen":ret,"profitYen":ret-stake,"returnIndex":round(ret/stake,6) if stake else None})
    return out


def build_diagnostics(root: Path, review: dict[str, Any], cohort: list[dict[str, Any]]) -> dict[str, Any]:
    if not should_generate(review): raise ValueError("100R review is not ready")
    if len(cohort) != TARGET_RACES: raise ValueError("diagnostic cohort must be exactly 100")
    results = result_index(root, cohort)
    rows=[enrich(root,r,results) for r in cohort]; overall=diag_summary(rows)
    fields=["battleGrade","chaosLevel","planType","selectionClass","venue","surface","distanceBand","fieldSizeBand","topHorsePopularityBand","topHorseAiProbabilityBand","topHorseOddsBand"]
    bd={f:breakdown(rows,f) for f in fields}; skipped=[r for r in rows if r.get("planType")=="skip"]; reasons=Counter(str(r.get("reason") or "理由不明") for r in skipped)
    snap=sum(r.get("diagnosticSnapshotAvailable") is True for r in rows); res=sum(r.get("resultAvailable") is True for r in rows)
    def pick(field,val): return next((x for x in bd[field] if x.get(field)==val),None)
    return {"schemaVersion":1,"version":VERSION,"status":"research_evaluation_only","sourceReviewVersion":REVIEW_VERSION,"sourceRuleVersion":SOURCE_VERSION,
            "generatedFromReviewAtJST":review.get("generatedAtJST"),"cohort":{"prospectiveStartDate":PROSPECTIVE_START,"races":100,"first100CohortLockedByRule":True,"reviewWindowStartDate":review.get("reviewWindowStartDate"),"reviewWindowEndDate":review.get("reviewWindowEndDate")},
            "overall":overall,"modelAccuracy":accuracy(rows),"focusMetrics":{"battleS":pick("battleGrade","S"),"battleA":pick("battleGrade","A"),"wideFocus":pick("planType","wide_focus"),"widePlusTrio":pick("planType","wide_plus_trio"),"chaos5":pick("chaosLevel",5)},
            "breakdowns":bd,"ticketBreakdown":tickets(rows),"skipReasons":[{"reason":k,"races":v,"shareOfSkippedPct":round(v/max(1,len(skipped))*100,2)} for k,v in sorted(reasons.items(), key=lambda x:(-x[1],x[0]))],
            "qualityChecks":{"cohortExactly100":True,"allSelectedSettled":overall["resultsPendingSelectedRaces"]==0,"formalSnapshotCoverageRaces":snap,"formalSnapshotCoveragePct":round(snap/TARGET_RACES*100,2),"resultCoverageRaces":res,"resultCoveragePct":round(res/TARGET_RACES*100,2),
                             "missingFormalSnapshotRaces":[{"raceDate":r.get("raceDate"),"raceID":r.get("raceID")} for r in rows if not r.get("diagnosticSnapshotAvailable")],"missingResultRaces":[{"raceDate":r.get("raceDate"),"raceID":r.get("raceID")} for r in rows if not r.get("resultAvailable")]},
            "interpretationPolicy":{"purpose":"100Rは短期診断。予測・選別・買い目の問題候補を分解し、収益性の最終証明には使わない。","noThresholdTuning":"この100Rだけを見て現行v1を変更しない。改善案は別version/shadowで前向き比較する。","smallSampleWarning":"選択レース数・券種別的中数は100Rより小さいため回収率単独で採否を決めない。","nextMilestones":"300Rで候補ロジックを絞り、1000R前後以上で小さい優位性を本格評価する。"}}


def pct(v): return "-" if v is None else f"{float(v):.2f}%"
def ri(v): return "-" if v is None else f"{float(v)*100:.1f}%"

def table(rows,key):
    out=[f"| {key} | R | 選択 | 的中 | 損益 | 回収率 | 本命1着 | 本命3着内 | 勝馬Top3 |","|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows: out.append(f"| {r.get(key)} | {r['races']} | {r['selectedRaces']} | {r['hitAnyRaces']} | ¥{r['profitYen']:,} | {ri(r['returnIndex'])} | {pct(r['top1WinRatePct'])} | {pct(r['top1Top3RatePct'])} | {pct(r['winnerInModelTop3RatePct'])} |")
    return out


def render_markdown(d):
    o=d["overall"]; a=d["modelAccuracy"]
    lines=["# KeibaAI First-100 Formal T-10 Diagnostics","","> 最初の100Rだけを事前固定条件で診断する研究用レポート。現行Aは変更しません。","","## Overall","",
           f"- 選択 {o['selectedRaces']}R / 見送り {o['skippedRaces']}R / 的中 {o['hitAnyRaces']}R",f"- 投資 ¥{o['stakeYen']:,} / 払戻 ¥{o['returnYen']:,} / 損益 ¥{o['profitYen']:,} / 回収率 {ri(o['returnIndex'])}",
           f"- 本命1着率 {pct(a['top1WinRatePct'])} / 本命3着内率 {pct(a['top1Top3RatePct'])} / 勝馬Top3率 {pct(a['winnerInModelTop3RatePct'])}",f"- 勝馬平均予測順位 {a['meanWinnerPredictedRank']}","",
           "## Priority checks fixed before 100R","","- S vs A","- wide_focus vs wide_plus_trio","- Chaos 5","- 選択 vs 見送り","- 券種別","- 競馬場・芝ダート・距離・頭数・人気・AI勝率・オッズ帯",""]
    titles={"battleGrade":"勝負度","chaosLevel":"Chaos","planType":"プラン","selectionClass":"選択 vs 見送り","venue":"競馬場","surface":"芝/ダート","distanceBand":"距離帯","fieldSizeBand":"頭数帯","topHorsePopularityBand":"1位予測馬の人気帯","topHorseAiProbabilityBand":"1位予測馬のAI勝率帯","topHorseOddsBand":"1位予測馬の単勝オッズ帯"}
    for k,t in titles.items(): lines += [f"## {t}",""] + table(d["breakdowns"][k],k) + [""]
    lines += ["## 買い目券種","","| 券種 | 点数 | 的中点 | 損益 | 回収率 |","|---|---:|---:|---:|---:|"]
    for r in d["ticketBreakdown"]: lines.append(f"| {r['betType']} | {r['tickets']} | {r['hitTickets']} | ¥{r['profitYen']:,} | {ri(r['returnIndex'])} |")
    lines += ["","## 見送り理由","","| 理由 | R | 構成比 |","|---|---:|---:|"]+[f"| {r['reason']} | {r['races']} | {pct(r['shareOfSkippedPct'])} |" for r in d["skipReasons"]]
    lines += ["","## Interpretation","",f"- {d['interpretationPolicy']['purpose']}",f"- {d['interpretationPolicy']['noThresholdTuning']}",f"- {d['interpretationPolicy']['smallSampleWarning']}",f"- {d['interpretationPolicy']['nextMilestones']}",""]
    return "\n".join(lines)


def first100(root: Path):
    data=attach_formal_evidence(root,read_json(root/"PortfolioResearchData.json")); rows=[r for r in data.get("races",[]) if isinstance(r,dict) and r.get("scope")=="prospective" and r.get("raceDate","")>=PROSPECTIVE_START and r.get("formal10minEligible") is True]; rows.sort(key=race_key); return rows[:TARGET_RACES]


def generate(root: Path) -> tuple[bool,str]:
    review=read_json(root/"PortfolioReview100.json")
    if not should_generate(review): return False,"not_ready"
    data=build_diagnostics(root,review,first100(root))
    if data["qualityChecks"]["resultCoverageRaces"] != TARGET_RACES: return False,"results_pending"
    jp,mp=root/JSON_NAME,root/MD_NAME; jt=json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+"\n"; mt=render_markdown(data)+"\n"
    if jp.exists() and mp.exists() and jp.read_text(encoding="utf-8")==jt and mp.read_text(encoding="utf-8")==mt: return False,"unchanged"
    jp.write_text(jt,encoding="utf-8"); mp.write_text(mt,encoding="utf-8"); return True,"generated"


def main():
    root=Path(os.environ.get("KEIBAAI_ROOT",Path(__file__).resolve().parent.parent)); changed,status=generate(root); print(json.dumps({"changed":changed,"status":status},ensure_ascii=False))
if __name__ == "__main__": main()
