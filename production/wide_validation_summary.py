from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS_DIR = REPO / "reports"
OUT_PATH = REPORTS_DIR / "WideForwardSummary.json"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _flatten(payloads: list[dict]) -> list[dict]:
    rows = []
    for payload in payloads:
        date = str(payload.get("date") or "")
        for race in payload.get("races") or []:
            rid = str(race.get("raceID") or "")
            for pair in race.get("pairs") or []:
                row = dict(pair)
                row["date"] = date
                row["raceID"] = rid
                row["venue"] = race.get("venue")
                row["raceNumber"] = race.get("raceNumber")
                rows.append(row)
    return rows


def _max_losing_streak(rows: list[dict]) -> int:
    best = 0
    cur = 0
    for row in rows:
        if bool(row.get("hit")):
            cur = 0
        else:
            cur += 1
            best = max(best, cur)
    return best


def _rank_key(row: dict, ranking: str):
    if ranking == "ev":
        return (
            -float(row.get("conservativeExpectedValue") or 0.0),
            -float(row.get("aiWideProbabilityPct") or 0.0),
            int(row.get("number1") or 0),
            int(row.get("number2") or 0),
        )
    return (
        -float(row.get("practicalScore") or row.get("kellyFractionPct") or 0.0),
        -float(row.get("aiWideProbabilityPct") or 0.0),
        -float(row.get("conservativeExpectedValue") or 0.0),
        int(row.get("number1") or 0),
        int(row.get("number2") or 0),
    )


def _strategy(
    rows: list[dict],
    *,
    threshold: float,
    top_n_per_race: int | None,
    ranking: str,
) -> dict:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (str(row.get("date") or ""), str(row.get("raceID") or ""))
        grouped.setdefault(key, []).append(row)

    selected = []
    for key in sorted(grouped):
        eligible = [
            row for row in grouped[key]
            if float(row.get("conservativeExpectedValue") or 0.0) >= threshold
        ]
        eligible.sort(key=lambda row: _rank_key(row, ranking))
        if top_n_per_race is not None:
            eligible = eligible[:top_n_per_race]
        for rank, row in enumerate(eligible, start=1):
            selected.append({**row, "strategyRank": rank})

    selected.sort(
        key=lambda r: (
            str(r.get("date") or ""),
            str(r.get("raceID") or ""),
            int(r.get("strategyRank") or 9999),
        )
    )

    stake = len(selected) * 100
    returned = sum(int(r.get("payoutPer100Yen") or 0) for r in selected)
    hits = sum(1 for r in selected if bool(r.get("hit")))

    race_keys = sorted({(str(r.get("date") or ""), str(r.get("raceID") or "")) for r in selected})
    race_hit = 0
    for key in race_keys:
        if any(
            bool(r.get("hit"))
            for r in selected
            if (str(r.get("date") or ""), str(r.get("raceID") or "")) == key
        ):
            race_hit += 1

    predicted = [float(r.get("aiWideProbabilityPct") or 0.0) for r in selected]
    scores = [float(r.get("practicalScore") or 0.0) for r in selected]

    return {
        "ranking": ranking,
        "thresholdConservativeEV": threshold,
        "topNPerRace": top_n_per_race,
        "bets": len(selected),
        "races": len(race_keys),
        "hits": hits,
        "hitRatePct": round(hits / len(selected) * 100.0, 3) if selected else None,
        "averagePredictedHitProbabilityPct": round(sum(predicted) / len(predicted), 3) if predicted else None,
        "averagePracticalScore": round(sum(scores) / len(scores), 3) if scores else None,
        "raceHitRatePct": round(race_hit / len(race_keys) * 100.0, 3) if race_keys else None,
        "stakeYen": stake,
        "returnYen": returned,
        "returnIndex": round(returned / stake, 4) if stake else None,
        "maxConsecutiveLosingBets": _max_losing_streak(selected),
    }


def summarize_validation_payloads(payloads: list[dict]) -> dict:
    rows = _flatten(payloads)
    dates = sorted({str(r.get("date") or "") for r in rows if str(r.get("date") or "")})
    race_count = len({(str(r.get("date") or ""), str(r.get("raceID") or "")) for r in rows})

    strategies = {
        # New practical ranking: balances hit probability and value.
        "practical_top1_EV100": _strategy(rows, threshold=1.00, top_n_per_race=1, ranking="practical"),
        "practical_top1_EV105": _strategy(rows, threshold=1.05, top_n_per_race=1, ranking="practical"),
        "practical_top1_EV110": _strategy(rows, threshold=1.10, top_n_per_race=1, ranking="practical"),
        "practical_top2_EV105": _strategy(rows, threshold=1.05, top_n_per_race=2, ranking="practical"),
        "practical_top2_EV110": _strategy(rows, threshold=1.10, top_n_per_race=2, ranking="practical"),
        # Old EV-only ordering retained as an honest baseline comparison.
        "ev_top1_EV100_baseline": _strategy(rows, threshold=1.00, top_n_per_race=1, ranking="ev"),
        "ev_top1_EV110_baseline": _strategy(rows, threshold=1.10, top_n_per_race=1, ranking="ev"),
        "ev_top2_EV110_baseline": _strategy(rows, threshold=1.10, top_n_per_race=2, ranking="ev"),
        "all_EV110_reference": _strategy(rows, threshold=1.10, top_n_per_race=None, ranking="ev"),
    }

    if race_count < 30:
        evidence = "too_early"
    elif race_count < 100:
        evidence = "early"
    elif race_count < 300:
        evidence = "building"
    else:
        evidence = "review_ready"

    return {
        "schemaVersion": 2,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": (
            "発走前に固定したワイド候補を確定払戻で検証し、"
            "実戦優先度（的中率＋期待値）とEVだけの順位を比較する集計。"
        ),
        "practicalRankingDefinition": (
            "AI同時3着内確率と保守的ワイドオッズから正のKelly比率を計算。"
            "同じ期待値なら、より的中確率の高い組み合わせを上位にする。"
        ),
        "dates": dates,
        "validatedRaces": race_count,
        "evaluatedPairs": len(rows),
        "evidenceStatus": evidence,
        "ticketPlanAutoEnable": False,
        "ticketPlanNote": (
            "実戦優先度は候補の並べ替え専用です。Kelly値を購入金額に直接使用しません。"
            "十分な実運用件数、回収率、的中率、連敗、期間安定性を確認してから別途判断します。"
        ),
        "strategies": strategies,
    }


def load_all_payloads() -> list[dict]:
    payloads = []
    for path in sorted(REPORTS_DIR.glob("20??????/WideValidation.json")):
        obj = _load_json(path, {})
        if isinstance(obj, dict):
            payloads.append(obj)
    return payloads


def main() -> None:
    payload = summarize_validation_payloads(load_all_payloads())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("validated races:", payload["validatedRaces"])
    print("evaluated pairs:", payload["evaluatedPairs"])
    print("evidence:", payload["evidenceStatus"])
    print("output:", OUT_PATH)


if __name__ == "__main__":
    main()
