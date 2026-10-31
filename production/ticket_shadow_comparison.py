"""Compare frozen formal-A win-only PL baseline with prospective ticket shadow.

Research evaluation only.  The comparison uses exactly the races that already have an
immutable ``ticket_shadow_v1/prediction`` record and scores both probability systems
against later official results.  It never creates a betting rule, stake, recommendation,
or ROI and never changes the formal 100-race cohort.
"""
from __future__ import annotations

import argparse
from itertools import combinations, permutations
import json
import math
from pathlib import Path
from typing import Any

VERSION = "ticket-shadow-comparison-v1-20261031"
KINDS = ("wide", "quinella", "exacta", "trio", "trifecta")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(values: dict[int, float]) -> dict[int, float]:
    total = math.fsum(values.values())
    if not values or total <= 0 or any(not math.isfinite(v) or v <= 0 for v in values.values()):
        raise ValueError("positive finite weights required")
    return {k: v / total for k, v in values.items()}


def pair_distributions(weights: dict[int, float]) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], float]]:
    w = normalize(weights)
    exacta: dict[tuple[int, int], float] = {}
    quinella: dict[tuple[int, int], float] = {}
    for a, b in permutations(sorted(w), 2):
        p = w[a] * w[b] / (1.0 - w[a])
        exacta[a, b] = p
        key = tuple(sorted((a, b)))
        quinella[key] = quinella.get(key, 0.0) + p
    return exacta, quinella


def triple_distributions(weights: dict[int, float]) -> tuple[dict[tuple[int, int, int], float], dict[tuple[int, int, int], float]]:
    w = normalize(weights)
    trifecta: dict[tuple[int, int, int], float] = {}
    trio: dict[tuple[int, int, int], float] = {}
    for a, b, c in permutations(sorted(w), 3):
        p = w[a] * w[b] / (1.0 - w[a]) * w[c] / (1.0 - w[a] - w[b])
        trifecta[a, b, c] = p
        key = tuple(sorted((a, b, c)))
        trio[key] = trio.get(key, 0.0) + p
    return trifecta, trio


def wide_distribution(weights: dict[int, float], trio: dict[tuple[int, int, int], float] | None = None) -> dict[tuple[int, int], float]:
    w = normalize(weights)
    if trio is None:
        _, trio = triple_distributions(w)
    pairs = combinations(sorted(w), 2)
    return {pair: math.fsum(p for key, p in trio.items() if pair[0] in key and pair[1] in key) for pair in pairs}


def distributions(weights: dict[int, float]) -> dict[str, dict[tuple[int, ...], float]]:
    exacta, quinella = pair_distributions(weights)
    trifecta, trio = triple_distributions(weights)
    wide = wide_distribution(weights, trio)
    return {"wide": wide, "quinella": quinella, "exacta": exacta, "trio": trio, "trifecta": trifecta}


def rank_items(table: dict[tuple[int, ...], float]) -> list[tuple[tuple[int, ...], float]]:
    return sorted(table.items(), key=lambda item: (-item[1], item[0]))


def result_finish(result: dict[str, Any], expected_numbers: set[int]) -> tuple[str, tuple[int, int, int] | None]:
    horses = result.get("horses")
    if not isinstance(horses, list) or {h.get("number") for h in horses} != expected_numbers:
        return "entrant_changed", None
    positions: dict[int, list[int]] = {1: [], 2: [], 3: []}
    for horse in horses:
        if horse.get("resultStatus") in {"取消", "除外"}:
            return "entrant_changed", None
        p = horse.get("finishPosition")
        if p in positions and horse.get("resultStatus") == "確定":
            positions[p].append(int(horse["number"]))
    if any(len(positions[p]) > 1 for p in (1, 2, 3)):
        return "dead_heat_excluded", None
    if any(len(positions[p]) != 1 for p in (1, 2, 3)):
        return "results_pending_or_invalid", None
    return "matched", (positions[1][0], positions[2][0], positions[3][0])


def targets(finish: tuple[int, int, int]) -> dict[str, Any]:
    a, b, c = finish
    return {
        "wide": {tuple(sorted(x)) for x in combinations((a, b, c), 2)},
        "quinella": tuple(sorted((a, b))),
        "exacta": (a, b),
        "trio": tuple(sorted((a, b, c))),
        "trifecta": (a, b, c),
    }


def brier(table: dict[tuple[int, ...], float], positives: set[tuple[int, ...]]) -> float:
    return math.fsum((p - (1.0 if key in positives else 0.0)) ** 2 for key, p in table.items()) / len(table)


def one_model_metrics(dists: dict[str, dict[tuple[int, ...], float]], finish: tuple[int, int, int]) -> dict[str, dict[str, Any]]:
    truth = targets(finish)
    out: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        table = dists[kind]
        ranked = rank_items(table)
        if kind == "wide":
            positive = truth[kind]
            probs = [table[key] for key in positive]
            out[kind] = {
                "top1Hit": ranked[0][0] in positive,
                "top3Hit": any(key in positive for key, _ in ranked[:3]),
                "meanWinningPairProbability": math.fsum(probs) / len(probs),
                "brier": brier(table, positive),
                "bestWinningPairRank": min(i + 1 for i, (key, _) in enumerate(ranked) if key in positive),
            }
        else:
            key = truth[kind]
            p = table[key]
            rank = next(i + 1 for i, (candidate, _) in enumerate(ranked) if candidate == key)
            out[kind] = {
                "top1Hit": ranked[0][0] == key,
                "top3Hit": any(candidate == key for candidate, _ in ranked[:3]),
                "actualProbability": p,
                "negativeLogProbability": -math.log(p),
                "actualRank": rank,
            }
    return out


def comparison(root: Path, date8: str) -> dict[str, Any]:
    prediction_dir = root / "reports" / date8 / "ticket_shadow_v1" / "prediction"
    if not prediction_dir.is_dir():
        raise FileNotFoundError(prediction_dir)
    result_path = root / "reports" / date8 / "ResultData.json"
    results = load_json(result_path)
    result_index = {str(r["raceID"]): r for r in results if isinstance(r, dict) and r.get("raceID")}

    aggregate: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        aggregate[kind] = {
            "matchedRaces": 0,
            "currentATop1Hits": 0,
            "shadowTop1Hits": 0,
            "currentATop3Hits": 0,
            "shadowTop3Hits": 0,
            "shadowBetterRaces": 0,
            "currentABetterRaces": 0,
            "ties": 0,
            "currentAScoreSum": 0.0,
            "shadowScoreSum": 0.0,
            "currentARankSum": 0.0,
            "shadowRankSum": 0.0,
        }

    horse_summary = {"matchedRaces": 0, "currentATopWinnerHits": 0, "shadowTopWinnerHits": 0,
                     "sameTopHorseRaces": 0, "differentTopHorseRaces": 0}
    race_rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}

    paths = sorted(prediction_dir.glob("*.json"))
    for path in paths:
        record = load_json(path)
        formal = record.get("formalPrediction") or {}
        rid = str(formal.get("raceID") or path.stem)
        marginals = record.get("horseMarginals") or []
        numbers = {int(row["number"]) for row in marginals}
        result = result_index.get(rid)
        if result is None:
            state, finish = "results_missing", None
        else:
            state, finish = result_finish(result, numbers)
        status_counts[state] = status_counts.get(state, 0) + 1
        row_out: dict[str, Any] = {
            "raceID": rid,
            "venue": formal.get("venue"),
            "raceNumber": formal.get("raceNumber"),
            "startTime": formal.get("startTime"),
            "status": state,
        }
        if finish is None:
            race_rows.append(row_out)
            continue

        current_weights = normalize({int(row["number"]): float(row["win"]) for row in marginals})
        shadow_weights = normalize({int(row["number"]): float(row["fittedRankingWeight"]) for row in marginals})
        current_dists, shadow_dists = distributions(current_weights), distributions(shadow_weights)
        current_metrics, shadow_metrics = one_model_metrics(current_dists, finish), one_model_metrics(shadow_dists, finish)

        current_top = min(current_weights, key=lambda n: (-current_weights[n], n))
        shadow_top = min(shadow_weights, key=lambda n: (-shadow_weights[n], n))
        horse_summary["matchedRaces"] += 1
        horse_summary["currentATopWinnerHits"] += current_top == finish[0]
        horse_summary["shadowTopWinnerHits"] += shadow_top == finish[0]
        same = current_top == shadow_top
        horse_summary["sameTopHorseRaces"] += same
        horse_summary["differentTopHorseRaces"] += not same

        row_out.update(finish=list(finish), currentATopHorse=current_top, shadowTopHorse=shadow_top,
                       topHorseChanged=not same, betTypes={})
        for kind in KINDS:
            a, s = current_metrics[kind], shadow_metrics[kind]
            agg = aggregate[kind]
            agg["matchedRaces"] += 1
            agg["currentATop1Hits"] += int(a["top1Hit"])
            agg["shadowTop1Hits"] += int(s["top1Hit"])
            agg["currentATop3Hits"] += int(a["top3Hit"])
            agg["shadowTop3Hits"] += int(s["top3Hit"])
            if kind == "wide":
                a_score, s_score = float(a["brier"]), float(s["brier"])
                a_rank, s_rank = float(a["bestWinningPairRank"]), float(s["bestWinningPairRank"])
                if s_score < a_score - 1e-15:
                    agg["shadowBetterRaces"] += 1
                elif a_score < s_score - 1e-15:
                    agg["currentABetterRaces"] += 1
                else:
                    agg["ties"] += 1
            else:
                a_score, s_score = float(a["negativeLogProbability"]), float(s["negativeLogProbability"])
                a_rank, s_rank = float(a["actualRank"]), float(s["actualRank"])
                if s_score < a_score - 1e-15:
                    agg["shadowBetterRaces"] += 1
                elif a_score < s_score - 1e-15:
                    agg["currentABetterRaces"] += 1
                else:
                    agg["ties"] += 1
            agg["currentAScoreSum"] += a_score
            agg["shadowScoreSum"] += s_score
            agg["currentARankSum"] += a_rank
            agg["shadowRankSum"] += s_rank
            row_out["betTypes"][kind] = {"currentA": a, "shadow": s}
        race_rows.append(row_out)

    summaries = {}
    for kind, agg in aggregate.items():
        n = agg["matchedRaces"]
        score_name = "meanBrierLowerIsBetter" if kind == "wide" else "meanNegativeLogProbabilityLowerIsBetter"
        summaries[kind] = {
            "matchedRaces": n,
            "currentATop1Hits": agg["currentATop1Hits"],
            "shadowTop1Hits": agg["shadowTop1Hits"],
            "currentATop3Hits": agg["currentATop3Hits"],
            "shadowTop3Hits": agg["shadowTop3Hits"],
            "shadowBetterRaces": agg["shadowBetterRaces"],
            "currentABetterRaces": agg["currentABetterRaces"],
            "ties": agg["ties"],
            score_name: {
                "currentA": round(agg["currentAScoreSum"] / n, 8) if n else None,
                "shadow": round(agg["shadowScoreSum"] / n, 8) if n else None,
            },
            "meanActualTicketRank": {
                "currentA": round(agg["currentARankSum"] / n, 3) if n else None,
                "shadow": round(agg["shadowRankSum"] / n, 3) if n else None,
            },
        }

    matched = horse_summary["matchedRaces"]
    horse_summary["currentATopWinnerHitRatePct"] = round(horse_summary["currentATopWinnerHits"] / matched * 100, 2) if matched else None
    horse_summary["shadowTopWinnerHitRatePct"] = round(horse_summary["shadowTopWinnerHits"] / matched * 100, 2) if matched else None

    return {
        "schemaVersion": 1,
        "version": VERSION,
        "status": "research_evaluation_only",
        "raceDate": f"{date8[:4]}-{date8[4:6]}-{date8[6:]}",
        "cohortDefinition": "all immutable ticket_shadow_v1 prediction records for the date; no post-result race filtering",
        "shadowRecordsObserved": len(paths),
        "statusCounts": status_counts,
        "models": {
            "currentAComparison": "frozen formal-A Win marginals converted to the existing win-only Plackett-Luce ticket baseline",
            "shadow": "fittedRankingWeight frozen prospectively from formal Win/Top2/Top3 marginals",
        },
        "horseWinnerComparison": horse_summary,
        "betTypeComparison": summaries,
        "races": race_rows,
        "formalROIAvailable": False,
        "roiNote": "Shadow had no preregistered ticket-selection/stake rule. Do not reconstruct ROI after seeing results; compare probability quality and fixed top-k coverage only.",
        "limitations": [
            "One race day is a very small sample and cannot justify replacing current A.",
            "Current A and shadow share the same frozen formal Win/Top2/Top3 source models; this isolates the joint-ticket probability construction, not a new horse-strength model.",
            "Wide is scored with pairwise Brier score because three Wide pairs are simultaneously winners; other pools use negative log probability of the unique winning ticket.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = comparison(args.root, args.date)
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
