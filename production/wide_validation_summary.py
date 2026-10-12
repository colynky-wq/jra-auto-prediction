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
            for rank, pair in enumerate(race.get("pairs") or [], start=1):
                row = dict(pair)
                row["date"] = date
                row["raceID"] = rid
                row["venue"] = race.get("venue")
                row["raceNumber"] = race.get("raceNumber")
                row["pairRankByConservativeEV"] = rank
                rows.append(row)
    rows.sort(
        key=lambda x: (
            str(x.get("date") or ""),
            str(x.get("raceID") or ""),
            int(x.get("pairRankByConservativeEV") or 9999),
        )
    )
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


def _strategy(rows: list[dict], *, threshold: float, top_n_per_race: int | None) -> dict:
    selected = []
    used_per_race: dict[tuple[str, str], int] = {}

    for row in rows:
        ev = float(row.get("conservativeExpectedValue") or 0.0)
        if ev < threshold:
            continue
        key = (str(row.get("date") or ""), str(row.get("raceID") or ""))
        used = used_per_race.get(key, 0)
        if top_n_per_race is not None and used >= top_n_per_race:
            continue
        selected.append(row)
        used_per_race[key] = used + 1

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

    return {
        "thresholdConservativeEV": threshold,
        "topNPerRace": top_n_per_race,
        "bets": len(selected),
        "races": len(race_keys),
        "hits": hits,
        "hitRatePct": round(hits / len(selected) * 100.0, 3) if selected else None,
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
        "all_EV100": _strategy(rows, threshold=1.00, top_n_per_race=None),
        "all_EV105": _strategy(rows, threshold=1.05, top_n_per_race=None),
        "all_EV110": _strategy(rows, threshold=1.10, top_n_per_race=None),
        "all_EV120": _strategy(rows, threshold=1.20, top_n_per_race=None),
        "top1_EV100": _strategy(rows, threshold=1.00, top_n_per_race=1),
        "top1_EV110": _strategy(rows, threshold=1.10, top_n_per_race=1),
        "top2_EV110": _strategy(rows, threshold=1.10, top_n_per_race=2),
    }

    # Deliberately conservative: this status is informational only. It never
    # enables ticket-plan money allocation automatically.
    if race_count < 30:
        evidence = "too_early"
    elif race_count < 100:
        evidence = "early"
    elif race_count < 300:
        evidence = "building"
    else:
        evidence = "review_ready"

    return {
        "schemaVersion": 1,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "発走前に固定したワイド期待値を、実際の確定払戻で前向き検証する集計。",
        "dates": dates,
        "validatedRaces": race_count,
        "evaluatedPairs": len(rows),
        "evidenceStatus": evidence,
        "ticketPlanAutoEnable": False,
        "ticketPlanNote": "件数や回収率だけで自動的に馬券プランを有効化しません。十分な実運用件数と安定性を確認してから別途判断します。",
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
