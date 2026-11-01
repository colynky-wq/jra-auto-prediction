"""KeibaAI portfolio v2 challengers (research shadow only).

The frozen v1 portfolio remains untouched. V2 was defined only after the
2026-11-01 race day finished, therefore every date before PROSPECTIVE_START is
post-hoc replay and can never be used as forward evidence for V2.

V2 isolates two hypotheses instead of tuning thresholds to the observed result:
1. high-chaos battle-grade S holdout (A-only), and
2. high-chaos Wide set selection where every selected Wide leg contains at
   least one horse from the frozen formal T-10 Top3, while the two-leg set
   maximizes distinct Top3 coverage and preserves the existing v1 probability/
   EV gates.
A combined challenger is also recorded so interaction can be measured later.
"""
from __future__ import annotations

from datetime import datetime
from itertools import combinations
import json
import os
from pathlib import Path
from typing import Any

import portfolio_shadow as v1

VERSION = "portfolio-shadow-v2-20261101"
PROSPECTIVE_START = "2026-11-02"
VARIANTS = ("battle_a_only", "top3_coverage", "combined")


def _scope(day: str) -> str:
    return "prospective" if day >= PROSPECTIVE_START else "posthoc_replay"


def _formal_top3_from_record(record: dict[str, Any], rid: str, day: str) -> tuple[int, ...]:
    prediction = record.get("prediction") if isinstance(record, dict) else None
    if not isinstance(prediction, dict):
        return ()
    if str(prediction.get("raceID") or "") != rid or str(prediction.get("raceDate") or "") != day:
        return ()
    if prediction.get("predictionTiming") != "live_10min" or prediction.get("captureQuality") != "formal_10min":
        return ()
    ranked: list[tuple[int, float]] = []
    for horse in prediction.get("horses") or []:
        try:
            number = int(horse["number"])
        except (KeyError, TypeError, ValueError):
            continue
        probability = v1.finite(horse.get("aiProbability"))
        if probability is None:
            continue
        ranked.append((number, probability))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return tuple(number for number, _ in ranked[:3]) if len(ranked) >= 3 else ()


def formal_top3(folder: Path, rid: str, day: str) -> tuple[int, ...]:
    path = folder / "all_bet_v1" / "win" / f"{rid}.json"
    if not path.exists():
        return ()
    try:
        return _formal_top3_from_record(v1.read_json(path), rid, day)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ()


def _pair_numbers(pair: dict[str, Any]) -> tuple[int, int] | None:
    try:
        a, b = sorted((int(pair["number1"]), int(pair["number2"])))
    except (KeyError, TypeError, ValueError):
        return None
    return (a, b) if a != b else None


def _wide_leg_numbers(leg: dict[str, Any]) -> tuple[int, int] | None:
    """Decode the fixed-width Wide candidate id without substring ambiguity."""
    candidate_id = str(leg.get("candidateID") or "")
    if len(candidate_id) != 4 or not candidate_id.isdigit():
        return None
    a, b = int(candidate_id[:2]), int(candidate_id[2:])
    if a <= 0 or b <= 0 or a == b:
        return None
    return tuple(sorted((a, b)))


def choose_wides_top3_coverage(
    rid: str,
    signal: dict[str, Any],
    payouts: dict[tuple[str, int, int], dict[str, Any]],
    top3: tuple[int, ...],
    min_probability: float,
    min_ev: float,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Choose Wide legs with strict formal-Top3 membership and maximum coverage.

    Every selected leg must contain at least one frozen formal T-10 Top3 horse.
    Only pre-race fields are used for selection. Probability/EV gates are identical
    to frozen v1. Practical rank remains the tie-breaker after Top3 coverage rather
    than being the primary objective.
    """
    if not top3 or limit <= 0:
        return []
    evaluation = signal.get("wideEvaluation") or {}
    if evaluation.get("status") != "live_odds_connected":
        return []

    top3_set = set(top3)
    candidates: list[dict[str, Any]] = []
    for pair in evaluation.get("pairs", []):
        nums = _pair_numbers(pair)
        if nums is None or not (set(nums) & top3_set):
            continue
        leg = v1.wide_leg(rid, pair, payouts)
        if not leg or leg["probabilityPct"] < min_probability or leg["expectedMultiplierLow"] < min_ev:
            continue
        rank = pair.get("practicalRank") if type(pair.get("practicalRank")) is int else 10**9
        score = v1.finite(pair.get("practicalScore")) or 0.0
        candidates.append({"leg": leg, "numbers": nums, "rank": rank, "score": score})

    if not candidates:
        return []
    take = min(limit, len(candidates))

    def key(group: tuple[dict[str, Any], ...]):
        covered = set()
        for item in group:
            covered.update(set(item["numbers"]) & top3_set)
        ids = tuple(sorted(item["leg"]["candidateID"] for item in group))
        return (
            -len(covered),
            sum(item["rank"] for item in group),
            -sum(item["score"] for item in group),
            -sum(item["leg"]["probabilityPct"] for item in group),
            ids,
        )

    chosen = min(combinations(candidates, take), key=key)
    return [item["leg"] for item in sorted(chosen, key=lambda item: item["leg"]["candidateID"])]


def _base(rid: str, day: str, signal: dict[str, Any], variant: str) -> dict[str, Any]:
    chaos = signal.get("chaos") or {}
    battle = signal.get("battle") or {}
    return {
        "raceDate": day,
        "raceID": rid,
        "venue": signal.get("venue"),
        "raceNumber": signal.get("raceNumber"),
        "raceName": signal.get("raceName"),
        "startTime": signal.get("startTime"),
        "chaosLevel": chaos.get("level") if type(chaos.get("level")) is int else None,
        "chaosStars": chaos.get("stars"),
        "battleGrade": battle.get("grade") if isinstance(battle.get("grade"), str) else None,
        "scope": _scope(day),
        "ruleVersion": VERSION,
        "variant": variant,
    }


def _skip(base: dict[str, Any], reason: str) -> dict[str, Any]:
    return {**base, "planType": "skip", "planLabel": "見送り", "reason": reason, "legs": [], **v1.evaluate_plan([])}


def _relabel_v1(plan: dict[str, Any], day: str, variant: str) -> dict[str, Any]:
    return {**plan, "scope": _scope(day), "ruleVersion": VERSION, "variant": variant}


def build_variant_plan(
    variant: str,
    rid: str,
    day: str,
    rows: dict[tuple[str, str], dict[str, Any]],
    signal: dict[str, Any],
    payouts: dict[tuple[str, int, int], dict[str, Any]],
    top3: tuple[int, ...],
) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown v2 variant: {variant}")
    base = _base(rid, day, signal, variant)
    level = base["chaosLevel"]
    grade = base["battleGrade"]

    # The v2 hypotheses concern only high-chaos conversion. Keep v1 exactly as-is
    # elsewhere so any future difference is attributable to the declared change.
    if level not in {4, 5}:
        return _relabel_v1(v1.build_race_plan(rid, day, rows, signal, payouts), day, variant)

    if signal.get("eligible") is not True:
        return _skip(base, "正式シグナルが対象外です")
    if grade not in v1.ALLOWED_BATTLE:
        return _skip(base, "勝負度がC以下のため研究プランを作りません")
    if grade not in v1.STRONG_BATTLE:
        return _skip(base, "混戦では勝負度A以上だけを研究対象にします")
    if variant in {"battle_a_only", "combined"} and grade == "S":
        return _skip(base, "v2検証: 高混戦の勝負度SをAと分離してホールドします")

    if variant == "battle_a_only":
        return _relabel_v1(v1.build_race_plan(rid, day, rows, signal, payouts), day, variant)

    if not top3:
        return _skip(base, "v2検証: 正式10分前Top3証拠を確認できないためホールドします")
    wides = choose_wides_top3_coverage(
        rid,
        signal,
        payouts,
        top3,
        v1.HIGH_CHAOS_WIDE_MIN_PROB_PCT,
        v1.HIGH_CHAOS_WIDE_MIN_EV,
        limit=2,
    )
    if not wides:
        return _skip(base, "v2検証: 正式Top3を含むワイド候補が既存の確率・期待値条件を満たしません")

    trio = v1.choose_candidate(rows.get((rid, "trio")), v1.TRIO_MIN_PROB_PCT, v1.TRIO_MIN_EV)
    legs = list(wides)
    if trio is not None and len(legs) < 3:
        legs.append(trio)
        plan_type, label = "wide_plus_trio", "ワイド＋3連複"
    else:
        plan_type, label = "wide_focus", "ワイド中心"

    top3_set = set(top3)
    covered: set[int] = set()
    for leg in wides:
        nums = _wide_leg_numbers(leg)
        if nums is not None:
            covered.update(set(nums) & top3_set)
    return {
        **base,
        "formalTop3Numbers": list(top3),
        "wideTop3CoveredNumbers": sorted(covered),
        "planType": plan_type,
        "planLabel": label,
        "reason": "v2検証: v1の確率・期待値ゲートを維持し、各ワイドに正式Top3を必須化した上で2点全体のTop3被覆を優先します",
        "legs": legs,
        **v1.evaluate_plan(legs),
    }


def summarize(races: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    selected = [r for r in races if r["scope"] == scope and r["planType"] != "skip"]
    settled = [r for r in selected if r["settled"]]
    stake = sum(r["totalStakeYen"] for r in settled)
    returned = sum(r["returnYen"] for r in settled)
    return {
        "racesSeen": sum(r["scope"] == scope for r in races),
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


def build(root: Path) -> dict[str, Any]:
    reports = root / "reports"
    by_variant: dict[str, list[dict[str, Any]]] = {variant: [] for variant in VARIANTS}
    warnings: list[str] = []
    for folder in sorted(reports.glob("20??????")):
        day8 = folder.name
        bet_path = folder / "BetResearchData.json"
        signal_path = folder / "SignalData.json"
        if not bet_path.exists() or not signal_path.exists():
            continue
        day = v1.day_iso(day8)
        try:
            bets = v1.read_json(bet_path)
            signals = v1.read_json(signal_path)
            if bets.get("status") != "reference_display_only" or signals.get("schemaVersion") != 2:
                raise ValueError("unexpected source schema/status")
            rows = v1.race_rows(bets)
            signal_index = v1.signal_rows(signals)
            validation = v1.read_json(folder / "WideValidation.json") if (folder / "WideValidation.json").exists() else None
            payouts = v1.wide_payout_index(validation)
            for rid, signal in sorted(signal_index.items(), key=lambda x: (str(x[1].get("startTime", "")), x[0])):
                if signal.get("raceDate") != day:
                    continue
                top3 = formal_top3(folder, rid, day)
                for variant in VARIANTS:
                    by_variant[variant].append(build_variant_plan(variant, rid, day, rows, signal, payouts, top3))
        except Exception as exc:
            warnings.append(f"{day8}: {type(exc).__name__}: {exc}")

    generated = datetime.now(v1.JST).isoformat(timespec="seconds")
    variants = {}
    for variant, races in by_variant.items():
        variants[variant] = {
            "posthocReplay": summarize(races, "posthoc_replay"),
            "prospective": summarize(races, "prospective"),
            "races": races,
        }
    return {
        "schemaVersion": 1,
        "version": VERSION,
        "status": "research_shadow_only",
        "formalRecommendationsEnabled": False,
        "autoBetEnabled": False,
        "stakeSizingEnabled": False,
        "generatedAtJST": generated,
        "prospectiveStartDate": PROSPECTIVE_START,
        "baselineRuleVersion": v1.VERSION,
        "variantDefinitions": {
            "battle_a_only": "高混戦のみSをホールドしAだけを選択。券種・閾値・ワイド順位はv1のまま。",
            "top3_coverage": "高混戦のA/Sを維持し、v1と同じ確率・期待値ゲート通過後、各ワイドに正式T-10 Top3を最低1頭必須化し、2点全体のTop3被覆を最大化。",
            "combined": "高混戦のA-onlyと厳格Top3被覆を同時適用。",
        },
        "evidencePolicy": "2026-11-01以前はルール作成前のpost-hoc replay。V2の性能証拠に数えない。2026-11-02以降だけprospectiveとして扱う。",
        "selectionPolicy": "選択は保存済み発走前SignalData・BetResearchData・formal T-10 win sidecarのみを使用。着順・的中・払戻は選択後の評価にだけ使用。",
        "unchangedThresholds": {
            "highChaosWideMinProbabilityPct": v1.HIGH_CHAOS_WIDE_MIN_PROB_PCT,
            "highChaosWideMinExpectedMultiplier": v1.HIGH_CHAOS_WIDE_MIN_EV,
            "trioMinProbabilityPct": v1.TRIO_MIN_PROB_PCT,
            "trioMinExpectedMultiplier": v1.TRIO_MIN_EV,
        },
        "variants": variants,
        "warnings": warnings,
        "meaning": "v1を変更せず、S分離とTop3整合の効果を独立に前向き比較する研究専用V2。",
    }


def main() -> None:
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    data = build(root)
    v1.write_json(root / "PortfolioShadowV2.json", data)
    all_races = [race for value in data["variants"].values() for race in value["races"]]
    source_days = [r["raceDate"].replace("-", "") for r in all_races]
    if source_days:
        newest = max(source_days)
        v1.write_json(root / "reports" / newest / "PortfolioShadowV2.json", data)
    print(json.dumps({
        "version": VERSION,
        "prospectiveStartDate": PROSPECTIVE_START,
        "variants": {name: value["prospective"] for name, value in data["variants"].items()},
        "warnings": data["warnings"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
