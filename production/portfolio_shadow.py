"""KeibaAI composite ticket portfolio research (shadow only).

This module never changes predictions, formal signals, or stake sizing.  It reads
already-frozen pre-race research projections and creates a deterministic research
portfolio per race.  The rules are versioned and fixed before prospective use.

Dates before PROSPECTIVE_START are development/backfill only.  They may be used
for diagnostics but must not be reported as prospective performance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))
VERSION = "portfolio-shadow-v1-20261019"
PROSPECTIVE_START = "2026-10-24"
UNIT_YEN = 100
ALLOWED_BATTLE = {"S", "A", "B"}
STRONG_BATTLE = {"S", "A"}

# Preregistered research thresholds. They are not fitted by maximizing old returns.
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
        kind = row.get("betType")
        rid = row.get("raceID")
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


def wide_payout_index(validation: dict[str, Any] | None) -> dict[tuple[str, int, int], dict[str, Any]]:
    out: dict[tuple[str, int, int], dict[str, Any]] = {}
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
            out[(rid, a, b)] = pair
    return out


def candidate_leg(kind: str, candidate: dict[str, Any]) -> dict[str, Any] | None:
    p = finite(candidate.get("probabilityPct"))
    ev = finite(candidate.get("expectedMultiplierLow"))
    low = finite(candidate.get("oddsLow"))
    if p is None or ev is None or low is None or p < 0 or low < 1:
        return None
    unit = candidate.get("unitYen", UNIT_YEN)
    if type(unit) is not int or unit != UNIT_YEN:
        return None
    return {
        "betType": kind,
        "candidateID": str(candidate.get("id", "")),
        "label": str(candidate.get("label", "")),
        "probabilityPct": p,
        "oddsLow": low,
        "oddsHigh": finite(candidate.get("oddsHigh")),
        "expectedMultiplierLow": ev,
        "unitYen": UNIT_YEN,
        "hit": candidate.get("hit") if isinstance(candidate.get("hit"), bool) else None,
        "payoutYen": candidate.get("payoutYen") if type(candidate.get("payoutYen")) is int else None,
    }


def wide_leg(rid: str, pair: dict[str, Any], payouts: dict[tuple[str, int, int], dict[str, Any]]) -> dict[str, Any] | None:
    p = finite(pair.get("aiWideProbability"))
    ev = finite(pair.get("conservativeExpectedValue"))
    low = finite(pair.get("wideOddsLow"))
    high = finite(pair.get("wideOddsHigh"))
    try:
        a, b = sorted((int(pair["number1"]), int(pair["number2"])))
    except (KeyError, TypeError, ValueError):
        return None
    if p is None or ev is None or low is None or high is None or p < 0 or not (1 <= low <= high):
        return None
    result = payouts.get((rid, a, b), {})
    payout = result.get("payoutPer100Yen")
    hit = result.get("hit")
    return {
        "betType": "wide",
        "candidateID": f"{a:02d}{b:02d}",
        "label": f"{a}番 {pair.get('name1') if int(pair.get('number1', -1)) == a else pair.get('name2')} − "
                 f"{b}番 {pair.get('name2') if int(pair.get('number2', -1)) == b else pair.get('name1')}",
        "probabilityPct": p,
        "oddsLow": low,
        "oddsHigh": high,
        "expectedMultiplierLow": ev,
        "unitYen": UNIT_YEN,
        "hit": hit if isinstance(hit, bool) else None,
        "payoutYen": payout if type(payout) is int else None,
    }


def choose_candidate(row: dict[str, Any] | None, min_probability: float, min_ev: float) -> dict[str, Any] | None:
    if not row or row.get("status") not in {"ready", "awaiting_results", "matched", "payouts_pending"}:
        return None
    legs = [candidate_leg(str(row.get("betType")), c) for c in row.get("candidates", [])]
    valid = [x for x in legs if x and x["probabilityPct"] >= min_probability and x["expectedMultiplierLow"] >= min_ev]
    valid.sort(key=lambda x: (-x["probabilityPct"], -x["expectedMultiplierLow"], x["candidateID"]))
    return valid[0] if valid else None


def choose_wides(rid: str, signal: dict[str, Any], payouts: dict[tuple[str, int, int], dict[str, Any]],
                 min_probability: float, min_ev: float, anchor: int | None = None, limit: int = 2) -> list[dict[str, Any]]:
    evaluation = signal.get("wideEvaluation") or {}
    if evaluation.get("status") != "live_odds_connected":
        return []
    out = []
    for pair in evaluation.get("pairs", []):
        leg = wide_leg(rid, pair, payouts)
        if not leg or leg["probabilityPct"] < min_probability or leg["expectedMultiplierLow"] < min_ev:
            continue
        if anchor is not None:
            try:
                nums = {int(pair["number1"]), int(pair["number2"])}
            except (KeyError, TypeError, ValueError):
                continue
            if anchor not in nums:
                continue
        score = finite(pair.get("practicalScore")) or 0.0
        rank = pair.get("practicalRank") if type(pair.get("practicalRank")) is int else 10**9
        out.append((rank, -score, -leg["probabilityPct"], leg["candidateID"], leg))
    out.sort(key=lambda x: x[:4])
    return [x[-1] for x in out[:limit]]


def anchor_number(win_leg: dict[str, Any] | None) -> int | None:
    if not win_leg:
        return None
    key = win_leg.get("candidateID", "")
    try:
        return int(key)
    except (TypeError, ValueError):
        return None


def evaluate_plan(legs: list[dict[str, Any]]) -> dict[str, Any]:
    stake = sum(int(x["unitYen"]) for x in legs)
    settled = bool(legs) and all(x.get("hit") is not None and x.get("payoutYen") is not None for x in legs)
    if not settled:
        return {"totalStakeYen": stake, "settled": False, "returnYen": None, "profitYen": None,
                "hitAny": None, "profitable": None, "torigami": None}
    returned = sum(int(x["payoutYen"]) for x in legs)
    any_hit = any(bool(x["hit"]) for x in legs)
    profit = returned - stake
    return {"totalStakeYen": stake, "settled": True, "returnYen": returned, "profitYen": profit,
            "hitAny": any_hit, "profitable": profit > 0, "torigami": any_hit and profit < 0}


def build_race_plan(rid: str, day: str, rows: dict[tuple[str, str], dict[str, Any]], signal: dict[str, Any],
                    payouts: dict[tuple[str, int, int], dict[str, Any]]) -> dict[str, Any]:
    chaos = signal.get("chaos") or {}
    battle = signal.get("battle") or {}
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
        "scope": "prospective" if day >= PROSPECTIVE_START else "development_backfill",
        "ruleVersion": VERSION,
    }

    def skipped(reason: str) -> dict[str, Any]:
        return {**base, "planType": "skip", "planLabel": "見送り", "reason": reason, "legs": [], **evaluate_plan([])}

    if signal.get("eligible") is not True or level not in {1, 2, 3, 4, 5}:
        return skipped("正式シグナルが対象外、または荒れ度を確認できません")
    if grade not in ALLOWED_BATTLE:
        return skipped("勝負度がC以下のため研究プランを作りません")

    win_row = rows.get((rid, "win"))
    trio_row = rows.get((rid, "trio"))

    if level <= 2:
        win = choose_candidate(win_row, WIN_MIN_PROB_PCT, WIN_MIN_EV)
        if not win:
            return skipped("堅め想定でも単勝の確率・期待値条件を満たしません")
        legs = [win]
        return {**base, "planType": "win_single", "planLabel": "単勝1点", "reason": "堅め想定＋勝負度B以上。単勝の確率と期待値に余裕があります", "legs": legs, **evaluate_plan(legs)}

    if level == 3:
        win = choose_candidate(win_row, MEDIUM_WIN_MIN_PROB_PCT, MEDIUM_WIN_MIN_EV)
        anchor = anchor_number(win)
        if not win or anchor is None:
            return skipped("中間タイプですが主軸にできる単勝候補がありません")
        wides = choose_wides(rid, signal, payouts, WIDE_MIN_PROB_PCT, WIDE_MIN_EV, anchor=anchor, limit=2)
        if not wides:
            return skipped("主軸馬と組めるワイド候補が期待値条件を満たしません")
        legs = [win] + wides
        return {**base, "planType": "win_plus_wide", "planLabel": "単勝＋ワイド", "reason": "中間タイプ。単勝を主軸に、同じ馬の3着内をワイドで補います", "legs": legs, **evaluate_plan(legs)}

    if grade not in STRONG_BATTLE:
        return skipped("混戦では勝負度A以上だけを研究対象にします")
    wides = choose_wides(rid, signal, payouts, HIGH_CHAOS_WIDE_MIN_PROB_PCT, HIGH_CHAOS_WIDE_MIN_EV, limit=2)
    if not wides:
        return skipped("混戦向けワイドの確率・期待値条件を満たしません")
    trio = choose_candidate(trio_row, TRIO_MIN_PROB_PCT, TRIO_MIN_EV)
    legs = list(wides)
    if trio is not None and len(legs) < 3:
        legs.append(trio)
        plan_type = "wide_plus_trio"
        label = "ワイド＋3連複"
        reason = "混戦＋勝負度A以上。ワイドを主軸に、上位3頭の3連複を少量比較します"
    else:
        plan_type = "wide_focus"
        label = "ワイド中心"
        reason = "混戦＋勝負度A以上。3連複条件は満たさないためワイドだけに絞ります"
    return {**base, "planType": plan_type, "planLabel": label, "reason": reason, "legs": legs, **evaluate_plan(legs)}


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
        "returnIndex": round(returned / stake, 6) if stake else None,
    }


def build(root: Path) -> dict[str, Any]:
    reports = root / "reports"
    races: list[dict[str, Any]] = []
    warnings: list[str] = []
    for folder in sorted(reports.glob("20??????")):
        day8 = folder.name
        bet_path = folder / "BetResearchData.json"
        signal_path = folder / "SignalData.json"
        if not bet_path.exists() or not signal_path.exists():
            continue
        day = day_iso(day8)
        try:
            bets = read_json(bet_path)
            signals = read_json(signal_path)
            if bets.get("status") != "reference_display_only" or signals.get("schemaVersion") != 2:
                raise ValueError("unexpected source schema/status")
            rows = race_rows(bets)
            signal_index = signal_rows(signals)
            validation = read_json(folder / "WideValidation.json") if (folder / "WideValidation.json").exists() else None
            payouts = wide_payout_index(validation)
            for rid, signal in sorted(signal_index.items(), key=lambda x: (str(x[1].get("startTime", "")), x[0])):
                if signal.get("raceDate") != day:
                    continue
                races.append(build_race_plan(rid, day, rows, signal, payouts))
        except Exception as exc:
            warnings.append(f"{day8}: {type(exc).__name__}: {exc}")

    generated = datetime.now(JST).isoformat(timespec="seconds")
    rules = {
        "lowChaos": {"levels": [1, 2], "plan": "win_single", "battle": sorted(ALLOWED_BATTLE),
                     "winMinProbabilityPct": WIN_MIN_PROB_PCT, "winMinExpectedMultiplier": WIN_MIN_EV},
        "mediumChaos": {"levels": [3], "plan": "win_plus_wide", "battle": sorted(ALLOWED_BATTLE),
                        "winMinProbabilityPct": MEDIUM_WIN_MIN_PROB_PCT, "winMinExpectedMultiplier": MEDIUM_WIN_MIN_EV,
                        "wideMinProbabilityPct": WIDE_MIN_PROB_PCT, "wideMinExpectedMultiplier": WIDE_MIN_EV, "wideMaxPairs": 2},
        "highChaos": {"levels": [4, 5], "plan": "wide_focus_or_plus_trio", "battle": sorted(STRONG_BATTLE),
                      "wideMinProbabilityPct": HIGH_CHAOS_WIDE_MIN_PROB_PCT, "wideMinExpectedMultiplier": HIGH_CHAOS_WIDE_MIN_EV,
                      "wideMaxPairs": 2, "trioMinProbabilityPct": TRIO_MIN_PROB_PCT, "trioMinExpectedMultiplier": TRIO_MIN_EV},
    }
    return {
        "schemaVersion": 1,
        "version": VERSION,
        "status": "research_display_only",
        "formalRecommendationsEnabled": False,
        "autoBetEnabled": False,
        "stakeSizingEnabled": False,
        "generatedAtJST": generated,
        "prospectiveStartDate": PROSPECTIVE_START,
        "developmentDataPolicy": "Dates before prospectiveStartDate are development/backfill only and cannot prove performance.",
        "selectionPolicy": "Rules are fixed before prospectiveStartDate; selection never reads finish, hit, payout or return fields.",
        "rules": rules,
        "developmentBackfill": summarize(races, "development_backfill"),
        "prospective": summarize(races, "prospective"),
        "races": races,
        "warnings": warnings,
        "meaning": "荒れ度×勝負度×券種の複合プラン研究。表示・検証専用で、購入指示や自動配分には使わない。",
    }


def main() -> None:
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    data = build(root)
    write_json(root / "PortfolioResearchData.json", data)
    source_days = [r["raceDate"].replace("-", "") for r in data["races"]]
    if source_days:
        newest = max(source_days)
        write_json(root / "reports" / newest / "PortfolioResearchData.json", data)
    print(json.dumps({"version": VERSION, "development": data["developmentBackfill"], "prospective": data["prospective"], "warnings": data["warnings"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
