"""Read-only comparator for independently captured pre-race odds.

This program does NOT scrape JRA, call network APIs, or assert a source is licensed.
It only checks provenance *claims*, timestamps, identity and numeric differences
for observations lawfully supplied by the operator. A supplied file is not proof
of source authenticity. No stake/selection/model files are modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from odds_integrity_audit import parse_jst, read_json

ALLOWED_SOURCES = {"jra_official_observation", "licensed_jravan_export",
                   "licensed_odds_provider"}
QUALITY_GATE = "independent_source_evidence_pending"
ODDS_STATUS = {"pre_post", "open"}


def minutes_between(a, b):
    if a is None or b is None:
        return None
    return round((a-b).total_seconds()/60, 3)


def normalize_quote(value):
    try:
        v = float(value)
        return v if math.isfinite(v) and v >= 1 else None
    except (TypeError, ValueError):
        return None


def build_map(rows, *, wide=False):
    quotes = {}
    errors = []
    for item in rows:
        try:
            if wide:
                a, b = int(item["number1"]), int(item["number2"])
                key = tuple(sorted((a, b)))
                lo = normalize_quote(item.get("oddsLow"))
                hi = normalize_quote(item.get("oddsHigh"))
                if a==b or lo is None or hi is None or lo>hi:
                    raise ValueError("bad wide quote")
                val = (lo, hi)
            else:
                key = int(item["number"])
                val = normalize_quote(item.get("odds"))
                if val is None:
                    raise ValueError("bad win quote")
            if key in quotes:
                errors.append("duplicate_quote")
            quotes[key] = val
        except (ValueError, TypeError, KeyError, AttributeError):
            errors.append("invalid_quote")
    return quotes, sorted(set(errors))


def compare_observation(snapshot, win_record, formal_wide, *,
                        max_source_age_minutes=5.0):
    """Compare an explicit independent-source observation against frozen T-10.

    The maximum allowed quote age is a **research criterion**, not a provider SLA.
    A structurally accepted observation is NOT authenticated as independent.
    """
    errors=[]
    prediction=win_record.get("prediction", {})
    ids={int(x["number"]) for x in prediction.get("horses", [])}
    record_id=str(prediction.get("raceID", ""))
    for key in ("raceDate", "raceID", "startTime"):
        if str(snapshot.get(key))!=str(prediction.get(key)):
            errors.append(f"identity_mismatch_{key}")
    for key in ("raceDate", "raceID", "startTime"):
        if str(formal_wide.get(key))!=str(prediction.get(key)):
            errors.append(f"formal_input_mismatch_{key}")
    if snapshot.get("source") not in ALLOWED_SOURCES:
        errors.append("unknown_source_claim")
    if snapshot.get("status") not in ODDS_STATUS:
        errors.append("not_observed_before_post")
    if not isinstance(snapshot.get("sourceReference"),str) or not snapshot["sourceReference"].strip():
        errors.append("missing_source_reference")
    if snapshot.get("permissionConfirmed") is not True:
        errors.append("permission_not_confirmed")

    observed=parse_jst(snapshot.get("observedAtJST"))
    asof=parse_jst(snapshot.get("asOfJST"))
    try:
        post=parse_jst(f"{prediction['raceDate']}T{prediction['startTime']}:00")
    except (KeyError, TypeError, ValueError):
        post=None
    minutes_before=minutes_between(post,observed)
    quote_age=minutes_between(observed,asof)
    if observed is None or minutes_before is None or not 8 <= minutes_before <=12:
        errors.append("outside_8_to_12_minute_window")
    if quote_age is None or not 0 <= quote_age <= max_source_age_minutes:
        errors.append("independent_quote_not_recently_confirmed")

    win, win_errors=build_map(snapshot.get("winOdds") or [])
    wide,wide_errors=build_map(snapshot.get("wideOdds") or [],wide=True)
    formalwin,fwerrs=build_map(
        [{"number":h["number"],"odds":h["odds"]}
         for h in prediction.get("horses", [])],wide=False)
    formalwide,fwe=build_map(formal_wide.get("pairs") or [],wide=True)
    errors.extend("win_"+x for x in win_errors)
    errors.extend("wide_"+x for x in wide_errors)
    errors.extend("formal_win_"+x for x in fwerrs)
    errors.extend("formal_wide_"+x for x in fwe)
    if not win and not wide:
        errors.append("no_comparable_quotes")
    if any(n not in ids for n in win):
        errors.append("outside_entrant_win_number")
    if any(a not in ids or b not in ids for a,b in wide):
        errors.append("outside_entrant_wide_pair")
    if any(a>=b for a,b in wide):
        errors.append("invalid_wide_pair_key")
    missing_win=sorted(ids-set(win))
    expected_wide={tuple(sorted((a,b))) for a in ids for b in ids if a<b}
    missing_wide=len(expected_wide-set(wide))

    win_comparison=[]
    for n in sorted(set(win)&set(formalwin)):
        old,other=formalwin[n],win[n]
        win_comparison.append({"number":n,"frozenOdds":old,"independentOdds":other,
                               "ratio":round(other/old,4),"absoluteDelta":round(other-old,3)})
    wide_comparison=[]
    for a,b in sorted(set(wide)&set(formalwide)):
        old,other=formalwide[a,b],wide[a,b]
        wide_comparison.append({"pair":f"{a:02d}{b:02d}",
            "frozenOddsLow":old[0],"frozenOddsHigh":old[1],
            "independentOddsLow":other[0],"independentOddsHigh":other[1],
            "lowRatio":round(other[0]/old[0],4),
            "highRatio":round(other[1]/old[1],4)})
    ref=snapshot.get("sourceReference")
    if snapshot.get("source")=="jra_official_observation" and not (isinstance(ref,str) and ref.startswith("https://www.jra.go.jp/")):
        # Warning only, because JRA official pages may use other valid JRA hosts.
        source_warning="Check JRA source hostname and capture evidence manually"
    else:
        source_warning=None

    return {
        "raceID":record_id,"sourceClaim":snapshot.get("source"),
        "sourceAuthenticityVerified":False,
        "sourceReference":snapshot.get("sourceReference"),
        "observedMinutesBeforePost":minutes_before,
        "claimedQuoteAgeMinutes":quote_age,
        "frozenT10Timestamp":prediction.get("capturedAtJST"),
        "independentObservationTime":snapshot.get("observedAtJST"),
        "independentQuoteAsOf":snapshot.get("asOfJST"),
        "comparisonStatus":(
            "structurally_comparable_not_authenticated"
            if not errors else "rejected"
        ),
        "researchRuleMaxQuoteAgeMinutes":max_source_age_minutes,
        "errors":sorted(set(errors)),
        "warnings":[source_warning] if source_warning else [],
        "winCoverage":{"provided":len(win),"expected":len(ids),
                       "missingNumbers":missing_win},
        "wideCoverage":{"provided":len(wide),"expected":len(expected_wide),
                        "missingPairs":missing_wide},
        "winComparison":win_comparison,
        "wideComparison":wide_comparison,
        "monetizableEdgeValidated":False,
    }


def run_audit(root, independent_dir, *, max_source_age_minutes=5.0):
    aligned=read_json(root/"AlignedPortfolioReview.json")
    if (aligned.get("projectionState")!="verified" or
            aligned.get("aligned",{}).get("countsAreCurrent") is not True):
        raise ValueError("aligned cohort not verified/current")
    result={"schemaVersion":1,"auditMode":"offline_independent_odds",
            "networkRequests":0,"predictionFilesChanged":0,
            "independentDir":str(independent_dir),
            "researchRuleMaxQuoteAgeMinutes":max_source_age_minutes,
            "totalAlignedRaces":len(aligned["races"]),
            "observationFilesPresent":0,"comparableObservations":0,
            "rejectedObservations":0,"fullyCoveredWinObservations":0,
            "fullyCoveredWideObservations":0,
            "unobservedRaces":0,"qualityGate":QUALITY_GATE,
            "results":[]}
    for race in aligned["races"]:
        date=str(race["raceDate"]).replace("-","")
        rid=str(race["raceID"])
        independent_file=independent_dir/date/f"{rid}.json"
        if not independent_file.exists():
            result["unobservedRaces"]+=1
            continue
        result["observationFilesPresent"]+=1
        freeze=root/"reports"/date
        try:
            snap=read_json(independent_file)
            model=read_json(freeze/"all_bet_v1"/"win"/f"{rid}.json")
            wide=read_json(freeze/"wide_odds_live10"/f"{rid}.json")
            diff=compare_observation(snap,model,wide,
                max_source_age_minutes=max_source_age_minutes)
            diff["observationSha256"]=hashlib.sha256(independent_file.read_bytes()).hexdigest()
        except (ValueError,TypeError,KeyError,json.JSONDecodeError,OSError) as e:
            diff={"raceID":rid,"comparisonStatus":"rejected",
                  "errors":["file_parse_or_processing_error"],"detail":str(e)[:160]}
        if diff["comparisonStatus"]=="rejected":
            result["rejectedObservations"]+=1
        else:
            result["comparableObservations"]+=1
            win=diff["winCoverage"]
            wide=diff["wideCoverage"]
            result["fullyCoveredWinObservations"]+=+(win["provided"]==win["expected"])
            result["fullyCoveredWideObservations"]+=+(wide["provided"]==wide["expected"])
        result["results"].append(diff)
    # This remains a separate audit gate; does not promote archival research EV to truth.
    result["qualityGate"]=(
        "structural_comparison_available_provenance_unverified"
        if result["comparableObservations"] else QUALITY_GATE
    )
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("."))
    parser.add_argument("--independent-dir",type=Path,
                        default=Path("research_independent_odds"))
    parser.add_argument("--max-source-age-minutes",type=float,default=5.0)
    args=parser.parse_args()
    out=run_audit(args.root,args.independent_dir,
                  max_source_age_minutes=args.max_source_age_minutes)
    print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
