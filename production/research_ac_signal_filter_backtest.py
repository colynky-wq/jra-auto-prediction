from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from research_ac_betting_backtest import prepare_frame, settle, row_record

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SIGNAL_PATH = REPO / "SignalData.json"
OUT_DIR = REPO / "market_independence_artifacts"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _race_id(d: dict[str, Any]) -> str | None:
    for k in ("raceID", "raceId", "race_id"):
        v = d.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _timestamp(d: dict[str, Any]) -> str:
    for k in ("capturedAtJST", "generatedAtJST", "updatedAtJST", "updatedAt", "generatedAt", "capturedAt"):
        v = d.get(k)
        if v is not None:
            return str(v)
    return ""


def collect_signal_records(obj: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        rid = _race_id(obj)
        elig = obj.get("eligible")
        if rid is not None and isinstance(elig, bool):
            out.append({
                "raceID": rid,
                "eligible": elig,
                "excludedReason": obj.get("excludedReason"),
                "timestamp": _timestamp(obj),
                "raceDate": obj.get("raceDate") or obj.get("date"),
                "venue": obj.get("venue"),
                "raceNo": obj.get("raceNo") or obj.get("raceNumber"),
            })
        for v in obj.values():
            collect_signal_records(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_signal_records(v, out)


def load_signal_map() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    raw = json.loads(SIGNAL_PATH.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    collect_signal_records(raw, records)
    latest: dict[str, dict[str, Any]] = {}
    for i, rec in enumerate(records):
        rec = dict(rec)
        rec["_order"] = i
        rid = rec["raceID"]
        prev = latest.get(rid)
        if prev is None:
            latest[rid] = rec
            continue
        # Prefer a lexicographically later ISO-like timestamp where available; otherwise later file occurrence.
        pk = (str(prev.get("timestamp") or ""), int(prev.get("_order", -1)))
        rk = (str(rec.get("timestamp") or ""), int(rec.get("_order", -1)))
        if rk >= pk:
            latest[rid] = rec
    return latest, records


def build_strategies(comp: pd.DataFrame) -> tuple[dict[str, list[dict]], dict[str, int]]:
    strategies: dict[str, list[dict]] = {
        "A_top1_only": [],
        "C_top1_only": [],
        "AC_union_top1": [],
        "AC_agree_top1_only": [],
        "AC_disagree_buy_both": [],
        "C_divergent_outsider": [],
        "A_top1_confirmed_by_Ctop3": [],
        "C_top1_confirmed_by_Atop3": [],
    }
    agree = disagree = races = 0
    for rid, g0 in comp.groupby("レースID", sort=False):
        g = g0.copy()
        if len(g[g["着順"].astype(float) == 1.0]) != 1:
            continue
        races += 1
        a1 = g.sort_values(["A順位", "馬番"]).iloc[0]
        c1 = g.sort_values(["C順位", "馬番"]).iloc[0]
        ar, cr = row_record(a1), row_record(c1)
        strategies["A_top1_only"].append(ar)
        strategies["C_top1_only"].append(cr)
        if int(a1["馬番"]) == int(c1["馬番"]):
            agree += 1
            strategies["AC_union_top1"].append(ar)
            strategies["AC_agree_top1_only"].append(ar)
        else:
            disagree += 1
            strategies["AC_union_top1"].extend([ar, cr])
            strategies["AC_disagree_buy_both"].extend([ar, cr])
        if int(a1["C順位"]) <= 3:
            strategies["A_top1_confirmed_by_Ctop3"].append(ar)
        if int(c1["A順位"]) <= 3:
            strategies["C_top1_confirmed_by_Atop3"].append(cr)
        q = g[(g["C順位"] <= 3) & (g["A順位"] >= 6) & (pd.to_numeric(g["単勝オッズ"], errors="coerce") >= 10.0)]
        for _, r in q.iterrows():
            strategies["C_divergent_outsider"].append(row_record(r))
    return strategies, {"races": races, "agreeTop1Races": agree, "disagreeTop1Races": disagree}


def summary_by_name(strategies: dict[str, list[dict]]) -> dict[str, dict]:
    return {name: settle(name, rows) for name, rows in strategies.items()}


def main() -> None:
    comp = prepare_frame()
    signal_map, raw_signal_records = load_signal_map()
    race_ids = set(comp["レースID"].astype(str).unique())

    excluded_ids = {rid for rid, rec in signal_map.items() if rec.get("eligible") is False and rid in race_ids}
    eligible_ids = {rid for rid, rec in signal_map.items() if rec.get("eligible") is True and rid in race_ids}
    missing_ids = race_ids - set(signal_map.keys())

    before_strat, before_cohort = build_strategies(comp)
    # User request: remove only races explicitly judged ineligible/avoid. Races without a stored signal are retained.
    filtered = comp[~comp["レースID"].astype(str).isin(excluded_ids)].copy()
    after_strat, after_cohort = build_strategies(filtered)

    before = summary_by_name(before_strat)
    after = summary_by_name(after_strat)
    comparison = []
    for name in before:
        b, a = before[name], after[name]
        comparison.append({
            "strategy": name,
            "before": {k: b[k] for k in ("racesBet", "bets", "hits", "stakeYen", "returnYen", "profitYen", "returnIndex", "roiPct")},
            "afterExcludeIneligible": {k: a[k] for k in ("racesBet", "bets", "hits", "stakeYen", "returnYen", "profitYen", "returnIndex", "roiPct")},
            "deltaProfitYen": a["profitYen"] - b["profitYen"],
            "deltaReturnIndexPoints": ((a["returnIndex"] or 0) - (b["returnIndex"] or 0)) * 100,
        })

    reason_counts: dict[str, int] = {}
    excluded_examples = []
    for rid in sorted(excluded_ids):
        rec = signal_map[rid]
        reason = str(rec.get("excludedReason") or "(none)")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if len(excluded_examples) < 50:
            excluded_examples.append({k: rec.get(k) for k in ("raceID", "raceDate", "venue", "raceNo", "eligible", "excludedReason", "timestamp")})

    result = {
        "protocol": {
            "period": "2026-01-01 through production_history max date",
            "avoidDefinition": "SignalData latest stored race-level eligible == false",
            "filterPolicy": "remove only explicitly ineligible races; retain races with no stored SignalData judgment",
            "betType": "win; 100 yen per selection",
            "odds": "final historical win odds; hindsight replay, not formal live 10-minute odds",
        },
        "signalCoverage": {
            "rawSignalRecordsWithRaceIDAndEligible": len(raw_signal_records),
            "uniqueSignalRaceIDs": len(signal_map),
            "comparisonRaceIDs": len(race_ids),
            "explicitEligibleInComparison": len(eligible_ids),
            "explicitIneligibleInComparison": len(excluded_ids),
            "missingSignalInComparison": len(missing_ids),
            "ineligiblePctOfComparison": len(excluded_ids) / len(race_ids) * 100 if race_ids else None,
            "excludedReasonCounts": dict(sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
        "cohortBefore": before_cohort,
        "cohortAfterExcludeIneligible": after_cohort,
        "strategyComparison": comparison,
        "excludedExamples": excluded_examples,
    }
    out = OUT_DIR / "ac_betting_exclude_signal_ineligible_2026.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AC_SIGNAL_FILTER_BACKTEST_BEGIN")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("AC_SIGNAL_FILTER_BACKTEST_END")
    print("artifact", out)


if __name__ == "__main__":
    main()
