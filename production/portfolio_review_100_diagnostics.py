"""Frozen first-100 formal T-10 diagnostic table for portfolio shadow v1.

Evaluation-only: never changes predictions, selection thresholds, ticket rules,
or stake sizing. Diagnostics are emitted only after the preregistered first-100
formal T-10 cohort is complete and every selected race in that cohort is settled.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from portfolio_review_100 import (
    PROSPECTIVE_START,
    REVIEW_VERSION,
    SOURCE_VERSION,
    TARGET_RACES,
    attach_formal_evidence,
    race_key,
    read_json,
    summary,
)

VERSION = "portfolio-review-100-diagnostics-v1-20261031"
JSON_NAME = "PortfolioReview100Diagnostics.json"
MD_NAME = "PortfolioReview100Diagnostics.md"

BATTLE_ORDER = ["S", "A", "B", "C", "見送り"]
CHAOS_ORDER = [1, 2, 3, 4, 5]
PLAN_ORDER = ["win_single", "win_plus_wide", "wide_focus", "wide_plus_trio", "skip"]
SURFACE_ORDER = ["芝", "ダ", "障害", "不明"]
DISTANCE_ORDER = ["<=1200m", "1400-1600m", "1700-2000m", "2100-2400m", "2500m+", "不明"]
FIELD_ORDER = ["<=10頭", "11-13頭", "14-16頭", "17頭+", "不明"]
POPULARITY_ORDER = ["1人気", "2-3人気", "4-6人気", "7人気+", "不明"]
PROB_ORDER = ["<10%", "10-20%", "20-30%", "30-40%", "40%+", "不明"]
ODDS_ORDER = ["<3.0", "3.0-5.9", "6.0-11.9", "12.0-29.9", "30.0+", "不明"]


def finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def should_generate(review: dict[str, Any]) -> bool:
    return (
        review.get("version") == REVIEW_VERSION
        and review.get("sourceVersion") == SOURCE_VERSION
        and review.get("first100CohortLockedByRule") is True
        and review.get("reviewWindowRaces") == TARGET_RACES
        and int(review.get("racesObserved") or 0) >= TARGET_RACES
        and int(review.get("remainingTo100") if review.get("remainingTo100") is not None else -1) == 0
        and review.get("reviewStatus") == "ready_for_short_term_review"
    )


def parse_course(value: Any) -> tuple[str, int | None]:
    text = str(value or "")
    if "障" in text:
        surface = "障害"
    elif "芝" in text:
        surface = "芝"
    elif "ダ" in text:
        surface = "ダ"
    else:
        surface = "不明"
    match = re.search(r"(\d{3,4})\s*m", text)
    return surface, int(match.group(1)) if match else None


def distance_band(distance: int | None) -> str:
    if distance is None:
        return "不明"
    if distance <= 1200:
        return "<=1200m"
    if distance <= 1600:
        return "1400-1600m"
    if distance <= 2000:
        return "1700-2000m"
    if distance <= 2400:
        return "2100-2400m"
    return "2500m+"


def field_band(size: int | None) -> str:
    if size is None:
        return "不明"
    if size <= 10:
        return "<=10頭"
    if size <= 13:
        return "11-13頭"
    if size <= 16:
        return "14-16頭"
    return "17頭+"


def popularity_band(value: int | None) -> str:
    if value is None:
        return "不明"
    if value == 1:
        return "1人気"
    if value <= 3:
        return "2-3人気"
    if value <= 6:
        return "4-6人気"
    return "7人気+"


def probability_band(value: float | None) -> str:
    if value is None:
        return "不明"
    if value < 10:
        return "<10%"
    if value < 20:
        return "10-20%"
    if value < 30:
        return "20-30%"
    if value < 40:
        return "30-40%"
    return "40%+"


def odds_band(value: float | None) -> str:
    if value is None:
        return "不明"
    if value < 3:
        return "<3.0"
    if value < 6:
        return "3.0-5.9"
    if value < 12:
        return "6.0-11.9"
    if value < 30:
        return "12.0-29.9"
    return "30.0+"


def load_formal_win_snapshot(root: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    day = str(row.get("raceDate") or "").replace("-", "")
    rid = str(row.get("raceID") or "")
    path = root / "reports" / day / "all_bet_v1" / "win" / f"{rid}.json"
    if not path.exists():
        return None
    data = read_json(path)
    pred = data.get("prediction")
    if not isinstance(pred, dict) or pred.get("predictionTiming") != "live_10min":
        return None
    horses = pred.get("horses")
    if not isinstance(horses, list) or not horses:
        return None
    valid = [h for h in horses if isinstance(h, dict) and finite(h.get("aiProbability")) is not None]
    if not valid:
        return None
    top = max(valid, key=lambda h: (finite(h.get("aiProbability")) or -1.0, -int(h.get("number") or 999)))
    surface, distance = parse_course(pred.get("course"))
    popularity = top.get("popularity") if type(top.get("popularity")) is int else None
    ai_prob = finite(top.get("aiProbability"))
    odds = finite(top.get("odds"))
    return {
        "course": pred.get("course"),
        "surface": surface,
        "distanceM": distance,
        "distanceBand": distance_band(distance),
        "fieldSize": len(horses),
        "fieldSizeBand": field_band(len(horses)),
        "topHorseNumber": top.get("number"),
        "topHorseName": top.get("name"),
        "topHorsePopularity": popularity,
        "topHorsePopularityBand": popularity_band(popularity),
        "topHorseAiProbabilityPct": ai_prob,
        "topHorseAiProbabilityBand": probability_band(ai_prob),
        "topHorseOdds": odds,
        "topHorseOddsBand": odds_band(odds),
    }


def enrich(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(row)
    snap = load_formal_win_snapshot(root, row)
    out["diagnosticSnapshotAvailable"] = snap is not None
    if snap:
        out.update(snap)
    else:
        out.update({
            "course": None,
            "surface": "不明",
            "distanceM": None,
            "distanceBand": "不明",
            "fieldSize": None,
            "fieldSizeBand": "不明",
            "topHorseNumber": None,
            "topHorseName": None,
            "topHorsePopularity": None,
            "topHorsePopularityBand": "不明",
            "topHorseAiProbabilityPct": None,
            "topHorseAiProbabilityBand": "不明",
            "topHorseOdds": None,
            "topHorseOddsBand": "不明",
        })
    return out


def breakdown(rows: list[dict[str, Any]], field: str, order: list[Any] | None = None) -> list[dict[str, Any]]:
    groups: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row.get(field), []).append(row)
    rank = {value: index for index, value in enumerate(order or [])}
    keys = sorted(groups, key=lambda value: (rank.get(value, len(rank)), str(value)))
    result = []
    for key in keys:
        item = summary(groups[key])
        item[field] = key
        result.append(item)
    return result


def ticket_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("planType") == "skip":
            continue
        for leg in row.get("legs") or []:
            if isinstance(leg, dict):
                groups.setdefault(str(leg.get("betType") or "unknown"), []).append(leg)
    out = []
    for kind in sorted(groups):
        legs = groups[kind]
        settled = [x for x in legs if isinstance(x.get("hit"), bool) and type(x.get("payoutYen")) is int]
        stake = sum(int(x.get("unitYen") or 0) for x in settled)
        returned = sum(int(x.get("payoutYen") or 0) for x in settled)
        out.append({
            "betType": kind,
            "tickets": len(legs),
            "settledTickets": len(settled),
            "hitTickets": sum(x.get("hit") is True for x in settled),
            "stakeYen": stake,
            "returnYen": returned,
            "profitYen": returned - stake,
            "returnIndex": round(returned / stake, 6) if stake else None,
        })
    return out


def focus_metrics(breakdowns: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    def get(rows: list[dict[str, Any]], field: str, value: Any) -> dict[str, Any] | None:
        return next((x for x in rows if x.get(field) == value), None)
    return {
        "battleS": get(breakdowns["battleGrade"], "battleGrade", "S"),
        "battleA": get(breakdowns["battleGrade"], "battleGrade", "A"),
        "wideFocus": get(breakdowns["planType"], "planType", "wide_focus"),
        "widePlusTrio": get(breakdowns["planType"], "planType", "wide_plus_trio"),
        "chaos5": get(breakdowns["chaosLevel"], "chaosLevel", 5),
    }


def build_diagnostics(root: Path, review: dict[str, Any], cohort: list[dict[str, Any]]) -> dict[str, Any]:
    if not should_generate(review):
        raise ValueError("100R review is not ready for frozen short-term diagnostics")
    if len(cohort) != TARGET_RACES:
        raise ValueError("diagnostic cohort must contain exactly first 100 formal races")
    enriched = [enrich(root, row) for row in cohort]
    breakdowns = {
        "battleGrade": breakdown(enriched, "battleGrade", BATTLE_ORDER),
        "chaosLevel": breakdown(enriched, "chaosLevel", CHAOS_ORDER),
        "planType": breakdown(enriched, "planType", PLAN_ORDER),
        "venue": breakdown(enriched, "venue"),
        "surface": breakdown(enriched, "surface", SURFACE_ORDER),
        "distanceBand": breakdown(enriched, "distanceBand", DISTANCE_ORDER),
        "fieldSizeBand": breakdown(enriched, "fieldSizeBand", FIELD_ORDER),
        "topHorsePopularityBand": breakdown(enriched, "topHorsePopularityBand", POPULARITY_ORDER),
        "topHorseAiProbabilityBand": breakdown(enriched, "topHorseAiProbabilityBand", PROB_ORDER),
        "topHorseOddsBand": breakdown(enriched, "topHorseOddsBand", ODDS_ORDER),
    }
    skipped = [r for r in enriched if r.get("planType") == "skip"]
    skip_reasons = Counter(str(r.get("reason") or "理由不明") for r in skipped)
    available = sum(r.get("diagnosticSnapshotAvailable") is True for r in enriched)
    overall = summary(enriched)
    return {
        "schemaVersion": 1,
        "version": VERSION,
        "status": "research_evaluation_only",
        "sourceReviewVersion": REVIEW_VERSION,
        "sourceRuleVersion": SOURCE_VERSION,
        "generatedFromReviewAtJST": review.get("generatedAtJST"),
        "cohort": {
            "prospectiveStartDate": PROSPECTIVE_START,
            "races": TARGET_RACES,
            "first100CohortLockedByRule": True,
            "reviewWindowStartDate": review.get("reviewWindowStartDate"),
            "reviewWindowEndDate": review.get("reviewWindowEndDate"),
        },
        "overall": overall,
        "focusMetrics": focus_metrics(breakdowns),
        "breakdowns": breakdowns,
        "ticketBreakdown": ticket_breakdown(enriched),
        "skipReasons": [
            {"reason": reason, "races": count, "shareOfSkippedPct": round(count / max(1, len(skipped)) * 100, 2)}
            for reason, count in sorted(skip_reasons.items(), key=lambda x: (-x[1], x[0]))
        ],
        "qualityChecks": {
            "cohortExactly100": True,
            "allSelectedSettled": overall["resultsPendingSelectedRaces"] == 0,
            "formalSnapshotCoverageRaces": available,
            "formalSnapshotCoveragePct": round(available / TARGET_RACES * 100, 2),
            "missingFormalSnapshotRaces": [
                {"raceDate": r.get("raceDate"), "raceID": r.get("raceID")}
                for r in enriched if r.get("diagnosticSnapshotAvailable") is not True
            ],
        },
        "interpretationPolicy": {
            "purpose": "100Rは短期診断。予測・選別・買い目の問題候補を分解するために使い、収益性の最終証明には使わない。",
            "noThresholdTuning": "この100Rの結果だけを見てportfolio-shadow-v1の閾値・選定・買い目ルールを変更しない。改善案は別version/shadowで前向き比較する。",
            "smallSampleWarning": "選択レース数・券種別的中数は100Rよりかなり小さいため、回収率単独で採否を決めない。",
            "nextMilestones": "300Rで候補ロジックの絞り込み、1000R前後以上で小さい優位性の本格評価を想定する。",
        },
    }


def _fmt_pct(value: Any) -> str:
    return "-" if value is None else f"{float(value):.2f}%"


def _fmt_ri(value: Any) -> str:
    return "-" if value is None else f"{float(value) * 100:.1f}%"


def render_table(rows: list[dict[str, Any]], key: str) -> list[str]:
    lines = [
        f"| {key} | R | 選択 | 的中 | 投資 | 払戻 | 損益 | 回収率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.get(key)} | {row.get('races', 0)} | {row.get('selectedRaces', 0)} | "
            f"{row.get('hitAnyRaces', 0)} | ¥{row.get('stakeYen', 0):,} | ¥{row.get('returnYen', 0):,} | "
            f"¥{row.get('profitYen', 0):,} | {_fmt_ri(row.get('returnIndex'))} |"
        )
    return lines


def render_markdown(data: dict[str, Any]) -> str:
    overall = data["overall"]
    lines = [
        "# KeibaAI First-100 Formal T-10 Diagnostics",
        "",
        "> 事前固定した最初の100Rだけを診断する研究用レポートです。現行Aの閾値・予測・買い目は変更しません。",
        "",
        "## Overall",
        "",
        f"- 対象: {overall['races']}R",
        f"- 選択: {overall['selectedRaces']}R / 見送り: {overall['skippedRaces']}R",
        f"- 的中: {overall['hitAnyRaces']}R",
        f"- 投資: ¥{overall['stakeYen']:,} / 払戻: ¥{overall['returnYen']:,} / 損益: ¥{overall['profitYen']:,}",
        f"- 回収率: {_fmt_ri(overall['returnIndex'])}",
        f"- Formal snapshot coverage: {data['qualityChecks']['formalSnapshotCoveragePct']:.2f}%",
        "",
        "## Priority checks fixed before 100R",
        "",
        "- S vs A: 勝負度が実際の選択品質を正しく序列化できているか。",
        "- wide_focus vs wide_plus_trio: ワイド中心化と3連複追加条件を分離して確認。",
        "- Chaos 5: 選択集中が有効か、単に高分散を拾っているだけか。",
        "- Selected vs skipped dimensions: 競馬場・馬場・距離・頭数・1位予測馬の人気/AI確率/オッズ帯を比較。",
        "- Ticket type: 実際に購入候補となった券種単位で投資・払戻・的中を確認。",
        "",
    ]
    labels = [
        ("battleGrade", "勝負度"),
        ("chaosLevel", "Chaos"),
        ("planType", "プラン"),
        ("venue", "競馬場"),
        ("surface", "芝/ダート"),
        ("distanceBand", "距離帯"),
        ("fieldSizeBand", "頭数帯"),
        ("topHorsePopularityBand", "1位予測馬の人気帯"),
        ("topHorseAiProbabilityBand", "1位予測馬のAI勝率帯"),
        ("topHorseOddsBand", "1位予測馬の単勝オッズ帯"),
    ]
    for key, title in labels:
        lines += [f"## {title}", ""]
        lines += render_table(data["breakdowns"][key], key)
        lines.append("")
    lines += [
        "## 買い目券種",
        "",
        "| 券種 | 点数 | 確定 | 的中点 | 投資 | 払戻 | 損益 | 回収率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in data["ticketBreakdown"]:
        lines.append(
            f"| {row['betType']} | {row['tickets']} | {row['settledTickets']} | {row['hitTickets']} | "
            f"¥{row['stakeYen']:,} | ¥{row['returnYen']:,} | ¥{row['profitYen']:,} | {_fmt_ri(row['returnIndex'])} |"
        )
    lines += ["", "## 見送り理由", "", "| 理由 | R | 構成比 |", "|---|---:|---:|"]
    for row in data["skipReasons"]:
        lines.append(f"| {row['reason']} | {row['races']} | {_fmt_pct(row['shareOfSkippedPct'])} |")
    lines += [
        "",
        "## Interpretation",
        "",
        f"- {data['interpretationPolicy']['purpose']}",
        f"- {data['interpretationPolicy']['noThresholdTuning']}",
        f"- {data['interpretationPolicy']['smallSampleWarning']}",
        f"- {data['interpretationPolicy']['nextMilestones']}",
        "",
    ]
    return "\n".join(lines)


def first100_formal_cohort(root: Path) -> list[dict[str, Any]]:
    source = read_json(root / "PortfolioResearchData.json")
    checked = attach_formal_evidence(root, source)
    rows = [
        row for row in checked.get("races", [])
        if isinstance(row, dict)
        and row.get("scope") == "prospective"
        and row.get("raceDate", "") >= PROSPECTIVE_START
        and row.get("formal10minEligible") is True
    ]
    rows.sort(key=race_key)
    return rows[:TARGET_RACES]


def generate(root: Path) -> tuple[bool, str]:
    review = read_json(root / "PortfolioReview100.json")
    if not should_generate(review):
        return False, "not_ready"
    cohort = first100_formal_cohort(root)
    data = build_diagnostics(root, review, cohort)
    json_path = root / JSON_NAME
    md_path = root / MD_NAME
    json_text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    md_text = render_markdown(data) + "\n"
    before_json = json_path.read_text(encoding="utf-8") if json_path.exists() else None
    before_md = md_path.read_text(encoding="utf-8") if md_path.exists() else None
    if before_json == json_text and before_md == md_text:
        return False, "unchanged"
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    return True, "generated"


def main() -> None:
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    changed, status = generate(root)
    print(json.dumps({"changed": changed, "status": status}, ensure_ascii=False))


if __name__ == "__main__":
    main()
