"""Score separately frozen no-current-odds forecasts using applied JRA history.

Never reconstruct a prediction, change official cohort counts, or fetch results
over the network. The existing full-meeting history updater is authoritative
for this research-only path, including races without a formal prediction.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re

import pandas as pd

from market_shrink_shadow import JST, fingerprint
from no_current_odds_independent import VERSION, DIRECTORY
from train_no_current_odds_challenger import MODEL_PATH, REPORT_PATH, HISTORY, ROOT
from evaluate_no_current_odds_2026_history import verified_candidate

REPORT = ROOT / "research" / "NoCurrentOddsIndependentForwardReview.json"
ACTIVATION_DATE = "2026-11-25"
EVENTS = {"win": ("winPct", 1), "top2": ("top2Pct", 2), "top3": ("top3Pct", 3)}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def jst(value):
    dt = datetime.fromisoformat(str(value))
    require(dt.utcoffset() == timedelta(hours=9), "JST offset required")
    return dt


def validate_record(v, path, root, meta):
    day, rid = v["raceDate"], v["raceID"]
    require(re.fullmatch(r"\d{12}", rid) and day >= ACTIVATION_DATE
            and path == root / "reports" / day.replace("-", "") / DIRECTORY / (rid + ".json"),
            "archive identity or activation mismatch")
    require(v.get("version") == VERSION and v.get("schemaVersion") == 1
            and v.get("status") == "independent_prospectively_frozen_not_approved"
            and v.get("researchOnly") is True and v.get("currentOddsUsed") is False
            and all(v.get(k) is False for k in ("formalRecommendationsEnabled",
                "formalPredictionAIChanged", "candidatePromotionApproved", "autoBetEnabled")),
            "invalid research policy")
    require(v.get("recordFingerprint") == fingerprint({
        k: x for k, x in v.items() if k != "recordFingerprint"}), "record digest mismatch")
    require(v.get("modelVersion") == meta["version"] and v.get("modelSha256") == meta["modelSha256"]
            and re.fullmatch(r"[0-9a-f]{64}", v["featureInputSha256"]), "model or feature provenance mismatch")
    post = datetime.fromisoformat(day + "T" + v["startTime"]).replace(tzinfo=JST)
    captured, recorded = jst(v["capturedAtJST"]), jst(v["recordedAtJST"])
    require(post - timedelta(minutes=12) <= captured <= recorded <= post - timedelta(minutes=8),
            "not a frozen T10 research record")
    e = v["sourceEntryEvidence"]
    require(e["raceDate"] == day and e["raceID"] == rid
            and jst(e["capturedAtJST"]) == captured and jst(e["scheduledPostJST"]) == post
            and re.fullmatch(r"[0-9a-f]{64}", e["sourceSHA256"]), "entry provenance mismatch")
    roster = e["runners"]
    require(len({r["number"] for r in roster}) == len(roster)
            and all(type(r["number"]) is int and 1 <= r["number"] <= 18
                    and type(r["withdrawn"]) is bool for r in roster), "invalid source roster")
    active = {r["number"]: r for r in roster if not r["withdrawn"]}
    candidates = v["candidateHorses"]
    require(len(active) >= 3 and len(candidates) == len(active)
            and {c["number"] for c in candidates} == set(active), "candidate roster mismatch")
    for c in candidates:
        require(c["name"] == active[c["number"]]["name"], "candidate name mismatch")
        p = [c["winPct"], c["top2Pct"], c["top3Pct"]]
        require(all(type(x) in (float, int) and math.isfinite(x) for x in p)
                and 0 <= p[0] <= p[1] <= p[2] <= 100.00000001, "invalid candidate probability")
    for event, (key, rank) in EVENTS.items():
        require(abs(sum(c[key] for c in candidates) - 100*rank) < 0.0001,
                "candidate probability sum mismatch: " + event)
    return post, active


def verified_day(history, day, receipt, post):
    """An applied full-meeting report and exact day coverage are mandatory."""
    date8 = day.replace("-", "")
    ids = receipt.get("raceListSource", {}).get("raceIDs", [])
    source = receipt.get("raceListSource", {})
    require(receipt.get("status") == "applied" and receipt.get("applyRequested") is True
            and receipt.get("targetDate") == date8 and receipt.get("newLatestDate") == day
            and receipt.get("duplicateRaceHorseKeys") == 0
            and source.get("method") == "jra_official_calendar_v1"
            and source.get("targetDate") == date8 and jst(receipt["generatedAtJST"]) > post,
            "unverified applied history receipt")
    d = history.loc[pd.to_datetime(history["日付"]).dt.strftime("%Y-%m-%d") == day]
    require(ids and len(ids) == len(set(ids)) == receipt["addedRaceCount"]
            and set(d["レースID"].astype(str)) == set(ids)
            and len(d) == receipt["newRows"] and not d.duplicated(["レースID", "馬番"]).any()
            and d["出典"].eq("JRA公式更新").all(), "applied history coverage mismatch")
    return d


def score_race(v, active, d):
    expected = {n: (re.sub(r"\s+", "", r["name"]), int(r["age"])) for n,r in active.items()}
    observed = {int(n): (re.sub(r"\s+", "", str(name)), int(age))
                for n,name,age in d[["馬番","馬名","年齢"]].itertuples(index=False,name=None)}
    require(len(d) == len(expected) and observed == expected
            and not d["馬番"].duplicated().any() and d["頭数"].eq(len(d)).all(),
            "result roster differs (including late scratches)")
    rank = pd.to_numeric(d["着順"], errors="coerce")
    require(rank.notna().all() and rank.between(1,len(d)).all()
            and (rank == rank.astype(int)).all()
            and all((rank == k).sum() == 1 for k in (1,2,3)),
            "incomplete or ambiguous result podium")
    finish = dict(zip(d["馬番"].astype(int), rank.astype(int)))
    scores = {}
    for event,(key,k) in EVENTS.items():
        p = [(c["number"], c[key]/100) for c in v["candidateHorses"]]
        selected = max(p, key=lambda x:(x[1],-x[0]))[0]
        scores[event] = {"raceBrier": sum((prob - int(finish[n] <= k))**2 for n,prob in p)/len(p),
                         "topPickHit": int(finish[selected] <= k)}
    return scores


def evaluate(root, history, meta):
    root = Path(root)
    reports = []
    seen = set()
    for path in sorted((root/"reports").glob(f"*/{DIRECTORY}/*.json")):
        v = json.loads(path.read_text(encoding="utf-8"))
        post, active = validate_record(v,path,root,meta)
        day,rid = v["raceDate"], v["raceID"]
        require((day,rid) not in seen, "duplicate prediction")
        seen.add((day,rid))
        row = {"raceDate":day,"raceID":rid,"predictionFingerprint":v["recordFingerprint"]}
        receipt_path = root/"reports"/"history_updates"/(day.replace("-","")+".json")
        if not receipt_path.is_file():
            row["state"] = "awaiting_applied_history"
        else:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            try:
                d = verified_day(history,day,receipt,post)
                row["events"] = score_race(v,active,d.loc[d["レースID"].astype(str)==rid])
                row["state"] = "matched"
                row["historyReceiptFingerprint"] = fingerprint(receipt)
            except (ValueError, KeyError, TypeError) as exc:
                row["state"] = "excluded_result_identity_or_quality"
                row["reason"] = str(exc)
        reports.append(row)
    matched = [r for r in reports if r["state"] == "matched"]
    counts = {"frozenRaces":len(reports),"matchedRaces":len(matched),
              "awaitingHistory":sum(r["state"]=="awaiting_applied_history" for r in reports),
              "excludedRaces":sum(r["state"]=="excluded_result_identity_or_quality" for r in reports)}
    by_event = {}
    for e in EVENTS:
        n=len(matched)
        by_event[e] = {"races":n,"equalRaceBrier":sum(r["events"][e]["raceBrier"] for r in matched)/n if n else None,
                       "topPickHits":sum(r["events"][e]["topPickHit"] for r in matched),
                       "topPickHitRate":sum(r["events"][e]["topPickHit"] for r in matched)/n if n else None}
    return {"schemaVersion":1,"version":"independent-no-odds-forward-review-v1",
            "status":"descriptive_only" if len(matched)>=30 else "insufficient_prospective_evidence",
            "researchOnly":True,"formalCohortChanged":False,"formalPredictionAIChanged":False,
            "candidatePromotionApproved":False,"autoBetEnabled":False,
            "modelVersion":meta["version"],"modelSha256":meta["modelSha256"],
            "activationDate":ACTIVATION_DATE,"counts":counts,"byEvent":by_event,"races":reports,
            "interpretation":"Separate outcome-selected cohort of failed odds captures; no formal or market superiority or betting claim."}


def main():
    meta=json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    verified_candidate(meta,MODEL_PATH.read_bytes())
    history=pd.read_parquet(HISTORY)
    result=evaluate(ROOT,history,meta)
    result["historySha256"]=hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print("INDEPENDENT_NO_ODDS_REVIEW",json.dumps(result["counts"]))


if __name__=="__main__":
    main()
