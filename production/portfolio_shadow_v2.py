"""KeibaAI portfolio shadow V2 challengers (research only).

V1 remains frozen.  V2 starts prospective scoring on 2026-11-02 and keeps
three independent high-chaos challengers:
- battle_a_only: grade A only, frozen V1 ticket ranking
- top3_coverage: grades A/S, Wide set maximizes formal T-10 Top3 coverage
- combined: grade A only + Top3 coverage

Selection is based only on saved pre-race probability/odds/signal evidence.
Settlement fields are attached after the ticket set has been selected.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from itertools import combinations
import json
import math
import os
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))
VERSION = "portfolio-shadow-v2-20261101"
V1_VERSION = "portfolio-shadow-v1-20261019"
PROSPECTIVE_START = "2026-11-02"
UNIT_YEN = 100

CHALLENGERS = ("battle_a_only", "top3_coverage", "combined")
VARIANTS = CHALLENGERS
ALLOWED_BATTLE = {"S", "A", "B"}
STRONG_BATTLE = {"S", "A"}

WIN_MIN_PROB_PCT = 18.0
WIN_MIN_EV = 1.05
MEDIUM_WIN_MIN_PROB_PCT = 15.0
MEDIUM_WIN_MIN_EV = 1.03
WIDE_MIN_PROB_PCT = 7.0
WIDE_MIN_EV = 1.05
HIGH_CHAOS_WIDE_MIN_PROB_PCT = 5.0
HIGH_CHAOS_WIDE_MIN_EV = 1.10
TRIO_MIN_PROB_PCT = 4.0
TRIO_MIN_EV = 1.10


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def day_iso(day8: str) -> str:
    return f"{day8[:4]}-{day8[4:6]}-{day8[6:8]}"


def race_rows(bet_data: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in bet_data.get("races", []):
        kind, rid = row.get("betType"), row.get("raceID")
        if not isinstance(kind, str) or not isinstance(rid, str) or rid.startswith("WIN5-"):
            continue
        key = (rid, kind)
        if key in out:
            raise ValueError(f"duplicate bet research row: {key}")
        out[key] = row
    return out


def signal_rows(signal_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for race in signal_data.get("races", []):
        rid = race.get("raceID")
        if not isinstance(rid, str):
            continue
        if rid in out:
            raise ValueError(f"duplicate signal race: {rid}")
        out[rid] = race
    return out


def candidate_pre_leg(kind: str, candidate: dict[str, Any]) -> dict[str, Any] | None:
    p = finite(candidate.get("probabilityPct"))
    ev = finite(candidate.get("expectedMultiplierLow"))
    low = finite(candidate.get("oddsLow"))
    high = finite(candidate.get("oddsHigh"))
    unit = candidate.get("unitYen", UNIT_YEN)
    if p is None or ev is None or low is None or p < 0 or low < 1:
        return None
    if type(unit) is not int or unit != UNIT_YEN:
        return None
    return {
        "betType": kind,
        "candidateID": str(candidate.get("id", "")),
        "label": str(candidate.get("label", "")),
        "probabilityPct": p,
        "oddsLow": low,
        "oddsHigh": high,
        "expectedMultiplierLow": ev,
        "unitYen": UNIT_YEN,
    }


def choose_candidate_pre(row: dict[str, Any] | None, min_probability: float, min_ev: float) -> dict[str, Any] | None:
    if not row or row.get("status") not in {"ready", "awaiting_results", "matched", "payouts_pending"}:
        return None
    valid: list[dict[str, Any]] = []
    for candidate in row.get("candidates", []):
        leg = candidate_pre_leg(str(row.get("betType")), candidate)
        if leg and leg["probabilityPct"] >= min_probability and leg["expectedMultiplierLow"] >= min_ev:
            valid.append(leg)
    valid.sort(key=lambda x: (-x["probabilityPct"], -x["expectedMultiplierLow"], x["candidateID"]))
    return valid[0] if valid else None


def wide_pre_leg(pair: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
    p = finite(pair.get("aiWideProbability"))
    ev = finite(pair.get("conservativeExpectedValue"))
    low = finite(pair.get("wideOddsLow"))
    high = finite(pair.get("wideOddsHigh"))
    try:
        n1, n2 = int(pair["number1"]), int(pair["number2"])
    except (KeyError, TypeError, ValueError):
        return None
    a, b = sorted((n1, n2))
    if p is None or ev is None or low is None or high is None or p < 0 or not (1 <= low <= high):
        return None
    name1, name2 = str(pair.get("name1", "")), str(pair.get("name2", ""))
    names = {n1: name1, n2: name2}
    score = finite(pair.get("practicalScore")) or 0.0
    rank = pair.get("practicalRank") if type(pair.get("practicalRank")) is int else 10**9
    leg = {
        "betType": "wide",
        "candidateID": f"{a:02d}{b:02d}",
        "label": f"{a}番 {names.get(a, '')} − {b}番 {names.get(b, '')}",
        "numbers": [a, b],
        "probabilityPct": p,
        "oddsLow": low,
        "oddsHigh": high,
        "expectedMultiplierLow": ev,
        "unitYen": UNIT_YEN,
    }
    key = (rank, -score, -p, leg["candidateID"])
    return key, leg


def eligible_wides(
    signal: dict[str, Any],
    min_probability: float,
    min_ev: float,
    anchor: int | None = None,
) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    evaluation = signal.get("wideEvaluation") or {}
    if evaluation.get("status") != "live_odds_connected":
        return []
    out: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for pair in evaluation.get("pairs", []):
        item = wide_pre_leg(pair)
        if item is None:
            continue
        key, leg = item
        if leg["probabilityPct"] < min_probability or leg["expectedMultiplierLow"] < min_ev:
            continue
        if anchor is not None and anchor not in set(leg["numbers"]):
            continue
        out.append((key, leg))
    out.sort(key=lambda item: item[0])
    return out


def choose_wides_practical(
    signal: dict[str, Any],
    min_probability: float,
    min_ev: float,
    anchor: int | None = None,
    limit: int = 2,
) -> list[dict[str, Any]]:
    return [leg for _, leg in eligible_wides(signal, min_probability, min_ev, anchor)[:limit]]


def choose_wides_top3(
    signal: dict[str, Any],
    top3: list[int],
    min_probability: float,
    min_ev: float,
    limit: int = 2,
) -> list[dict[str, Any]]:
    items = eligible_wides(signal, min_probability, min_ev)
    if not items:
        return []
    size = min(limit, len(items))
    top = set(top3)
    best_key: tuple[Any, ...] | None = None
    best: list[dict[str, Any]] | None = None
    for combo in combinations(items, size):
        covered: set[int] = set()
        quality: list[tuple[Any, ...]] = []
        legs: list[dict[str, Any]] = []
        for key, leg in combo:
            covered.update(set(leg["numbers"]) & top)
            quality.append(key)
            legs.append(leg)
        quality.sort()
        combo_key = (-len(covered), tuple(quality), tuple(sorted(x["candidateID"] for x in legs)))
        if best_key is None or combo_key < best_key:
            best_key, best = combo_key, legs
    return sorted(best or [], key=lambda x: x["candidateID"])


def formal_top3(path: Path, expected_race_id: str) -> list[int] | None:
    if not path.exists():
        return None
    data = read_json(path)
    if data.get("betType") != "win":
        return None
    prediction = data.get("prediction")
    if not isinstance(prediction, dict):
        return None
    if str(prediction.get("raceID")) != expected_race_id:
        return None
    if prediction.get("predictionTiming") != "live_10min" or prediction.get("captureQuality") != "formal_10min":
        return None
    ranked: list[tuple[float, int]] = []
    for horse in prediction.get("horses", []):
        if not isinstance(horse, dict) or type(horse.get("number")) is not int:
            continue
        p = finite(horse.get("aiProbability"))
        if p is None or p < 0:
            continue
        ranked.append((p, int(horse["number"])))
    if len(ranked) < 3:
        return None
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [number for _, number in ranked[:3]]


def anchor_number(win_leg: dict[str, Any] | None) -> int | None:
    if not win_leg:
        return None
    try:
        return int(win_leg.get("candidateID", ""))
    except (TypeError, ValueError):
        return None


def ordinary_settlement(rows: dict[tuple[str, str], dict[str, Any]], rid: str, leg: dict[str, Any]) -> tuple[bool | None, int | None]:
    row = rows.get((rid, str(leg["betType"])))
    if not row:
        return None, None
    for candidate in row.get("candidates", []):
        if str(candidate.get("id", "")) != leg["candidateID"]:
            continue
        hit = candidate.get("hit")
        payout = candidate.get("payoutYen")
        return (hit if isinstance(hit, bool) else None, payout if type(payout) is int else None)
    return None, None


def wide_settlement_index(validation: dict[str, Any] | None) -> dict[tuple[str, int, int], tuple[bool | None, int | None]]:
    out: dict[tuple[str, int, int], tuple[bool | None, int | None]] = {}
    if not validation:
        return out
    for race in validation.get("races", []):
        rid = race.get("raceID")
        if not isinstance(rid, str):
            continue
        for pair in race.get("pairs", []):
            try:
                a, b = sorted((int(pair["number1"]), int(pair["number2"])))
            except (KeyError, TypeError, ValueError):
                continue
            hit = pair.get("hit")
            payout = pair.get("payoutPer100Yen")
            out[(rid, a, b)] = (
                hit if isinstance(hit, bool) else None,
                payout if type(payout) is int else None,
            )
    return out


def attach_settlement(
    rid: str,
    legs: list[dict[str, Any]],
    rows: dict[tuple[str, str], dict[str, Any]],
    wide_results: dict[tuple[str, int, int], tuple[bool | None, int | None]],
) -> list[dict[str, Any]]:
    settled: list[dict[str, Any]] = []
    for raw in legs:
        leg = deepcopy(raw)
        if leg["betType"] == "wide":
            nums = leg.get("numbers") or []
            if len(nums) == 2:
                a, b = sorted((int(nums[0]), int(nums[1])))
                hit, payout = wide_results.get((rid, a, b), (None, None))
            else:
                hit, payout = None, None
        else:
            hit, payout = ordinary_settlement(rows, rid, leg)
        leg["hit"], leg["payoutYen"] = hit, payout
        settled.append(leg)
    return settled


def evaluate_plan(legs: list[dict[str, Any]]) -> dict[str, Any]:
    stake = sum(int(x["unitYen"]) for x in legs)
    settled = bool(legs) and all(x.get("hit") is not None and x.get("payoutYen") is not None for x in legs)
    if not settled:
        return {
            "totalStakeYen": stake, "settled": False, "returnYen": None, "profitYen": None,
            "hitAny": None, "profitable": None, "torigami": None,
        }
    returned = sum(int(x["payoutYen"]) for x in legs)
    any_hit = any(bool(x["hit"]) for x in legs)
    profit = returned - stake
    return {
        "totalStakeYen": stake, "settled": True, "returnYen": returned, "profitYen": profit,
        "hitAny": any_hit, "profitable": profit > 0, "torigami": any_hit and profit < 0,
    }


def top3_metrics(legs: list[dict[str, Any]], top3: list[int] | None) -> dict[str, Any]:
    if top3 is None:
        return {
            "formalTop3": None,
            "selectedWideTop3CoverageCount": None,
            "selectedWideTop3CoveredNumbers": None,
        }
    covered: set[int] = set()
    top = set(top3)
    for leg in legs:
        if leg.get("betType") == "wide":
            covered.update(set(leg.get("numbers") or []) & top)
    return {
        "formalTop3": top3,
        "selectedWideTop3CoverageCount": len(covered),
        "selectedWideTop3CoveredNumbers": sorted(covered),
    }


def build_challenger_plan(
    challenger: str,
    rid: str,
    day: str,
    rows: dict[tuple[str, str], dict[str, Any]],
    signal: dict[str, Any],
    wide_results: dict[tuple[str, int, int], tuple[bool | None, int | None]],
    top3: list[int] | None,
) -> dict[str, Any]:
    if challenger not in CHALLENGERS:
        raise ValueError(challenger)
    chaos, battle = signal.get("chaos") or {}, signal.get("battle") or {}
    level = chaos.get("level") if type(chaos.get("level")) is int else None
    grade = battle.get("grade") if isinstance(battle.get("grade"), str) else None
    base = {
        "raceDate": day,
        "raceID": rid,
        "venue": signal.get("venue"),
        "raceNumber": signal.get("raceNumber"),
        "raceName": signal.get("raceName"),
        "startTime": signal.get("startTime"),
        "chaosLevel": level,
        "chaosStars": chaos.get("stars"),
        "battleGrade": grade,
        "challenger": challenger,
        "scope": "prospective" if day >= PROSPECTIVE_START else "posthoc_replay",
        "ruleVersion": VERSION,
    }

    def finalize(plan_type: str, label: str, reason: str, pre_legs: list[dict[str, Any]]) -> dict[str, Any]:
        legs = attach_settlement(rid, pre_legs, rows, wide_results)
        return {
            **base,
            "planType": plan_type,
            "planLabel": label,
            "reason": reason,
            "legs": legs,
            **evaluate_plan(legs),
            **top3_metrics(legs, top3),
        }

    def skipped(reason: str) -> dict[str, Any]:
        return {
            **base,
            "planType": "skip",
            "planLabel": "見送り",
            "reason": reason,
            "legs": [],
            **evaluate_plan([]),
            **top3_metrics([], top3),
        }

    if signal.get("eligible") is not True or level not in {1, 2, 3, 4, 5}:
        return skipped("正式シグナルが対象外、または荒れ度を確認できません")
    if grade not in ALLOWED_BATTLE:
        return skipped("勝負度がC以下のため研究プランを作りません")

    win_row, trio_row = rows.get((rid, "win")), rows.get((rid, "trio"))

    if level <= 2:
        win = choose_candidate_pre(win_row, WIN_MIN_PROB_PCT, WIN_MIN_EV)
        if not win:
            return skipped("堅め想定でも単勝の確率・期待値条件を満たしません")
        return finalize("win_single", "単勝1点", "v1継承：堅め想定＋勝負度B以上", [win])

    if level == 3:
        win = choose_candidate_pre(win_row, MEDIUM_WIN_MIN_PROB_PCT, MEDIUM_WIN_MIN_EV)
        anchor = anchor_number(win)
        if not win or anchor is None:
            return skipped("中間タイプですが主軸にできる単勝候補がありません")
        wides = choose_wides_practical(signal, WIDE_MIN_PROB_PCT, WIDE_MIN_EV, anchor=anchor, limit=2)
        if not wides:
            return skipped("主軸馬と組めるワイド候補が期待値条件を満たしません")
        return finalize("win_plus_wide", "単勝＋ワイド", "v1継承：単勝主軸＋同馬ワイド", [win] + wides)

    if grade not in STRONG_BATTLE:
        return skipped("混戦では勝負度A以上だけを研究対象にします")
    if challenger in {"battle_a_only", "combined"} and grade == "S":
        return skipped("V2 challenger: 混戦Sは保留し、Aのみ前向き比較します")

    needs_top3 = challenger in {"top3_coverage", "combined"}
    if needs_top3 and top3 is None:
        return skipped("V2 challenger: 正式10分前Top3証拠がないためfail-closedで保留します")

    if needs_top3:
        wides = choose_wides_top3(
            signal, top3 or [], HIGH_CHAOS_WIDE_MIN_PROB_PCT, HIGH_CHAOS_WIDE_MIN_EV, limit=2
        )
    else:
        wides = choose_wides_practical(
            signal, HIGH_CHAOS_WIDE_MIN_PROB_PCT, HIGH_CHAOS_WIDE_MIN_EV, limit=2
        )
    if not wides:
        return skipped("混戦向けワイドの確率・期待値条件を満たしません")

    trio = choose_candidate_pre(trio_row, TRIO_MIN_PROB_PCT, TRIO_MIN_EV)
    pre_legs = list(wides)
    if trio is not None and len(pre_legs) < 3:
        pre_legs.append(trio)
        return finalize(
            "wide_plus_trio",
            "ワイド＋3連複",
            "V2高混戦比較：ワイド2点以内＋v1据え置き3連複ゲート",
            pre_legs,
        )
    return finalize(
        "wide_focus",
        "ワイド中心",
        "V2高混戦比較：ワイド2点以内。3連複条件は未達",
        pre_legs,
    )


def summarize(rows: list[dict[str, Any]], challenger: str, scope: str) -> dict[str, Any]:
    cohort = [r for r in rows if r["challenger"] == challenger and r["scope"] == scope]
    selected = [r for r in cohort if r["planType"] != "skip"]
    settled = [r for r in selected if r["settled"]]
    stake = sum(int(r["totalStakeYen"]) for r in settled)
    returned = sum(int(r["returnYen"]) for r in settled)
    return {
        "racesSeen": len(cohort),
        "selectedRaces": len(selected),
        "settledSelectedRaces": len(settled),
        "hitAnyRaces": sum(r["hitAny"] is True for r in settled),
        "profitableRaces": sum(r["profitable"] is True for r in settled),
        "torigamiRaces": sum(r["torigami"] is True for r in settled),
        "stakeYen": stake,
        "returnYen": returned,
        "profitYen": returned - stake,
        "returnIndex": round(returned / stake, 6) if stake else None,
    }


def high_chaos_grade_summary(rows: list[dict[str, Any]], challenger: str, scope: str, grade: str) -> dict[str, Any]:
    cohort = [
        r for r in rows
        if r["challenger"] == challenger and r["scope"] == scope
        and r.get("chaosLevel") in {4, 5} and r.get("battleGrade") == grade
    ]
    selected = [r for r in cohort if r["planType"] != "skip"]
    settled = [r for r in selected if r["settled"]]
    stake = sum(int(r["totalStakeYen"]) for r in settled)
    returned = sum(int(r["returnYen"]) for r in settled)
    coverage = [r["selectedWideTop3CoverageCount"] for r in selected if r["selectedWideTop3CoverageCount"] is not None]
    return {
        "racesSeen": len(cohort),
        "selectedRaces": len(selected),
        "settledSelectedRaces": len(settled),
        "hitAnyRaces": sum(r["hitAny"] is True for r in settled),
        "profitableRaces": sum(r["profitable"] is True for r in settled),
        "stakeYen": stake,
        "returnYen": returned,
        "returnIndex": round(returned / stake, 6) if stake else None,
        "meanSelectedWideFormalTop3Coverage": round(sum(coverage) / len(coverage), 3) if coverage else None,
    }


def build(root: Path) -> dict[str, Any]:
    reports = root / "reports"
    rows_out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for folder in sorted(reports.glob("20??????")):
        day8 = folder.name
        day = day_iso(day8)
        bet_path, signal_path = folder / "BetResearchData.json", folder / "SignalData.json"
        if not bet_path.exists() or not signal_path.exists():
            continue
        try:
            bets, signals = read_json(bet_path), read_json(signal_path)
            if bets.get("status") != "reference_display_only" or signals.get("schemaVersion") != 2:
                raise ValueError("unexpected source schema/status")
            rows = race_rows(bets)
            signal_index = signal_rows(signals)
            validation_path = folder / "WideValidation.json"
            validation = read_json(validation_path) if validation_path.exists() else None
            wide_results = wide_settlement_index(validation)
            for rid, signal in sorted(signal_index.items(), key=lambda x: (str(x[1].get("startTime", "")), x[0])):
                if signal.get("raceDate") != day:
                    continue
                top3 = formal_top3(folder / "all_bet_v1" / "win" / f"{rid}.json", rid)
                for challenger in CHALLENGERS:
                    rows_out.append(build_challenger_plan(challenger, rid, day, rows, signal, wide_results, top3))
        except Exception as exc:
            warnings.append(f"{day8}: {type(exc).__name__}: {exc}")

    variants: dict[str, Any] = {}
    for challenger in CHALLENGERS:
        races = [r for r in rows_out if r["challenger"] == challenger]
        variants[challenger] = {
            "posthocReplay": summarize(rows_out, challenger, "posthoc_replay"),
            "prospective": summarize(rows_out, challenger, "prospective"),
            "highChaosByGrade": {
                scope: {
                    grade: high_chaos_grade_summary(rows_out, challenger, scope, grade)
                    for grade in ("A", "S")
                }
                for scope in ("posthoc_replay", "prospective")
            },
            "races": races,
        }

    return {
        "schemaVersion": 1,
        "version": VERSION,
        "status": "research_shadow_only",
        "formalRecommendationsEnabled": False,
        "autoBetEnabled": False,
        "stakeSizingEnabled": False,
        "generatedAtJST": datetime.now(JST).isoformat(timespec="seconds"),
        "baselineRuleVersion": V1_VERSION,
        "prospectiveStartDate": PROSPECTIVE_START,
        "evidencePolicy": "2026-11-01以前はV2定義後の再生であり、V2前向き成績には数えない。",
        "selectionPolicy": (
            "選択は保存済み事前情報だけで行い、hit/payout/return/profitは選択完了後に評価用として付与する。"
        ),
        "variantDefinitions": {
            "battle_a_only": "高混戦だけSを保留しAのみ。券種選択はv1据え置き。",
            "top3_coverage": "高混戦A/Sで正式10分前Top3の被覆数を最大化するワイド2点以内。",
            "combined": "高混戦Aのみ＋正式10分前Top3被覆ワイド。",
        },
        "unchangedThresholds": {
            "highChaosWideMinProbabilityPct": HIGH_CHAOS_WIDE_MIN_PROB_PCT,
            "highChaosWideMinExpectedMultiplier": HIGH_CHAOS_WIDE_MIN_EV,
            "trioMinProbabilityPct": TRIO_MIN_PROB_PCT,
            "trioMinExpectedMultiplier": TRIO_MIN_EV,
        },
        "variants": variants,
        "warnings": warnings,
        "meaning": "v1を変更せず、S分離とTop3ワイド被覆を独立に前向き比較するV2 shadow。",
    }


def material(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    copied = deepcopy(value)
    copied.pop("generatedAtJST", None)
    return copied


def write_if_material_change(path: Path, value: dict[str, Any]) -> bool:
    if path.exists():
        prior = read_json(path)
        if material(prior) == material(value):
            return False
    write_json(path, value)
    return True


def main() -> None:
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    data = build(root)
    changed_root = write_if_material_change(root / "PortfolioShadowV2.json", data)
    source_days = sorted({r["raceDate"].replace("-", "") for v in data["variants"].values() for r in v["races"]})
    changed_day = False
    if source_days:
        newest = source_days[-1]
        changed_day = write_if_material_change(root / "reports" / newest / "PortfolioShadowV2.json", data)
    print(json.dumps({
        "version": VERSION,
        "prospectiveStartDate": PROSPECTIVE_START,
        "changed": changed_root or changed_day,
        "variants": {k: {"posthocReplay": v["posthocReplay"], "prospective": v["prospective"]} for k, v in data["variants"].items()},
        "warnings": data["warnings"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
