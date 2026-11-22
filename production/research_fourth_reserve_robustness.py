"""Audit fourth-reserve stability and counterfactual trio returns."""
from __future__ import annotations

import hashlib
from itertools import combinations
import json

import numpy as np
import pandas as pd

from evaluate_triple_forward import saved_payouts
from formal_portfolio_capture import stamp
from research_fourth_reserve_gate import (
    HISTORY_COLUMNS, METHODS, REFERENCE, ROOT, historical, live, rank_at_daily_budget,
)
from research_longshot_t10 import git_history, load_audited
from triple_forward import validate

OUT = ROOT / "research/FourthReserveRobustness.json"


def group_quality(frame, threshold):
    selected = frame.loc[frame["jointCoverage"].ge(threshold)]
    count, events = len(selected), int(selected["newlyCovered"].sum())
    return {"races": len(frame), "adds": count, "newCoverage": events,
            "expectedNewCoverage": float(selected["jointCoverage"].sum()),
            "observedRate": events / count if count else None}


def robustness(frame, threshold):
    chosen = frame["jointCoverage"].ge(threshold)
    budget = chosen.groupby(frame["日付"]).sum()
    comparator = rank_at_daily_budget(frame, "fourthTop3", budget)
    outcomes = frame["newlyCovered"].astype(int)
    delta = outcomes * (chosen.astype(int) - comparator.astype(int))
    venue = frame["raceID"].astype(str).str[4:6]
    base = group_quality(frame, threshold)
    daily = {}
    for day, group in frame.groupby("日付"):
        summary = group_quality(group, threshold)
        summary["deltaVsFourthProbability"] = int(delta.loc[group.index].sum())
        daily[day.date().isoformat()] = summary
    total = int(delta.sum())
    return {
        **base,
        "deltaVsFourthProbability": total,
        "withoutEachDate": {day: total - values["deltaVsFourthProbability"] for day, values in daily.items()},
        "daily": daily,
        "venues": {v: {**group_quality(frame.loc[venue.eq(v)], threshold),
                       "deltaVsFourthProbability": int(delta.loc[venue.eq(v)].sum()),
                       "deltaExcludingVenue": total - int(delta.loc[venue.eq(v)].sum())}
                   for v in sorted(venue.unique())},
    }


def blocked_fetch(url):
    raise ValueError("offline study must use archived payout evidence")


def trio_result(root, race, podium):
    rid, day = race["raceID"], race["raceDate"].replace("-", "")
    folder = root / "reports" / day
    record_path = folder / "triple_v1/trio" / (rid + ".json")
    payout_path = folder / "triple_payouts_v1/trio" / (rid + ".json")
    if not record_path.exists() or not payout_path.exists():
        return None
    raw = record_path.read_bytes()
    record = json.loads(raw)
    validate(record)
    stored = record["prediction"]
    if stored != race:
        raise ValueError("trio prediction differs from audited T-10 bundle")
    payout = json.loads(payout_path.read_bytes())
    source = "https://race.netkeiba.com/race/result.html?race_id=" + rid
    if payout["sourceURL"] != source:
        raise ValueError("payout source identity mismatch")
    payments = saved_payouts(root, record, fetch=blocked_fetch)
    truth = "".join(f"{n:02d}" for n in sorted(podium))
    if set(payments) != {truth} or type(payments[truth]) is not int or payments[truth] <= 0:
        raise ValueError("trio payout disagrees with audit podium")
    provenance = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (record_path, payout_path)}
    return truth, payments[truth], provenance


def payout_study(root, races, frame, threshold):
    metrics = frame.set_index("raceID")
    selected = frame["jointCoverage"].ge(threshold)
    budget = selected.groupby(frame["日付"]).sum()
    masks = {"jointCoverage": selected, "fourthTop3": rank_at_daily_budget(frame, "fourthTop3", budget)}
    selections = {name: set(frame.loc[mask, "raceID"]) for name, mask in masks.items()}
    names = ("mainThree", *METHODS[:2], "fourEveryRace")
    rows, missing, hashes = [], [], {}
    for race, podium in races:
        rid = race["raceID"]
        payout = trio_result(root, race, podium)
        if payout is None:
            missing.append(rid)
            continue
        truth, pay, provenance = payout
        hashes.update(provenance)
        order = sorted(race["horses"], key=lambda h: (-h["aiTop3Probability"], h["number"]))
        main = [h["number"] for h in order[:3]]
        fourth = order[3]["number"]
        base = "".join(f"{n:02d}" for n in sorted(main))
        extra = {"".join(f"{n:02d}" for n in sorted((*pair, fourth))) for pair in combinations(main, 2)}
        if len(extra) != 3 or base in extra:
            raise ValueError("duplicate or incomplete trio tickets")
        sums = {}
        for name in names:
            add = name == "fourEveryRace" or (name in selections and rid in selections[name])
            tickets = {base} | extra if add else {base}
            sums[name] = {"stakeYen": len(tickets) * 100, "returnYen": pay if truth in tickets else 0,
                          "extraStakeYen": 300 if add else 0, "extraReturnYen": pay if add and truth in extra else 0,
                          "hit": truth in tickets}
        rows.append({"raceID": rid, "raceDate": race["raceDate"], "venue": race["venue"],
                     "selected": rid in selections["jointCoverage"],
                     "score": float(metrics.loc[rid, "jointCoverage"]), "payoutPer100": pay, "policies": sums})
    summary = {}
    for name in names:
        totals = {key: sum(row["policies"][name][key] for row in rows)
                  for key in ("stakeYen", "returnYen", "extraStakeYen", "extraReturnYen", "hit")}
        totals["returnPct"] = totals["returnYen"] / totals["stakeYen"] * 100 if totals["stakeYen"] else None
        totals["extraReturnPct"] = totals["extraReturnYen"] / totals["extraStakeYen"] * 100 if totals["extraStakeYen"] else None
        totals["profitYen"] = totals["returnYen"] - totals["stakeYen"]
        daily = {}
        for date in sorted({row["raceDate"] for row in rows}):
            group = [row["policies"][name] for row in rows if row["raceDate"] == date]
            daily[date] = {k: sum(v[k] for v in group) for k in ("stakeYen", "returnYen", "extraStakeYen", "extraReturnYen")}
        totals["daily"] = daily
        totals["extraExcludingEachDay"] = {
            date: {"stakeYen": totals["extraStakeYen"] - one["extraStakeYen"],
                   "returnYen": totals["extraReturnYen"] - one["extraReturnYen"]}
            for date, one in daily.items()
        }
        totals["venueExtra"] = {
            venue: {key: sum(row["policies"][name][key] for row in rows if row["venue"] == venue)
                    for key in ("extraStakeYen", "extraReturnYen")}
            for venue in sorted({row["venue"] for row in rows})
        }
        summary[name] = totals
    return {"perTicketYen": 100, "betType": "trio", "verifiedRaces": len(rows),
            "missingPayoutRaceIDs": missing, "sources": hashes, "summary": summary, "races": rows}


def main():
    audit_raw = (ROOT / "AlignedPortfolioReview.json").read_bytes()
    audit = json.loads(audit_raw)
    races, incomplete = load_audited(ROOT, audit)
    threshold = json.loads(REFERENCE.read_bytes())["thresholdsFrom2025Only"]["fourthGain"]
    frame = live(races, "aiTop3Probability")
    history, provenance = git_history("308570d685f6e8d182b4e001eaeba018395c701e")
    history = history.loc[:, HISTORY_COLUMNS].copy()
    history["日付"] = pd.to_datetime(history["日付"])
    past = historical(history.loc[history["日付"].ge("2026-01-01") & history["日付"].lt("2026-11-01")])
    result = {
        "auditSha256": hashlib.sha256(audit_raw).hexdigest(),
        "auditGeneratedAtJST": audit["generatedAtJST"], "incompletePodiumRaceIDs": incomplete,
        "historySource": provenance, "threshold": threshold,
        "T10FormalAI": robustness(frame, threshold), "FinalOdds2026JanOct": robustness(past, threshold),
        "trio": payout_study(ROOT, races, frame, threshold),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Written", OUT, flush=True)


if __name__ == "__main__":
    main()
