from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS_DIR = REPO / "reports"
OUT_PATH = REPORTS_DIR / "TicketPlanShadow.json"

RULE_VERSION = "wide-ticket-shadow-v1-20261012"
BOOTSTRAP_SEED = 20261012
BOOTSTRAP_REPS = 2000

# Frozen before a meaningful forward sample exists. Do not tune these rules
# against later results without creating a new rule version and restarting the
# evaluation clock. This prevents choosing thresholds after seeing outcomes.
PROFILES = {
    "安定型候補": {
        "description": "1レース最大1点。保守的期待値1.10以上のみ。",
        "thresholdEV": 1.10,
        "topNPerRace": 1,
    },
    "バランス型候補": {
        "description": "1レース最大2点。保守的期待値1.10以上のみ。",
        "thresholdEV": 1.10,
        "topNPerRace": 2,
    },
    "高期待値型候補": {
        "description": "1レース最大3点。保守的期待値1.20以上のみ。大穴型とは別物。",
        "thresholdEV": 1.20,
        "topNPerRace": 3,
    },
}

# Even when every gate passes, the script only changes status to review_candidate.
# It never enables actual money allocation automatically.
REVIEW_GATES = {
    "minValidatedRaces": 300,
    "minSelectedBets": 150,
    "minOverallReturnIndex": 1.00,
    "minBootstrap95LowerReturnIndex": 1.00,
    "minRecentHalfReturnIndex": 0.95,
}


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_rows() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(REPORTS_DIR.glob("20??????/WideValidation.json")):
        payload = _load_json(path, {})
        date = str(payload.get("date") or path.parent.name)
        for race in payload.get("races") or []:
            rid = str(race.get("raceID") or "")
            for rank, pair in enumerate(race.get("pairs") or [], start=1):
                row = dict(pair)
                row["date"] = date
                row["raceID"] = rid
                row["venue"] = race.get("venue")
                row["raceNumber"] = race.get("raceNumber")
                row["rankByConservativeEV"] = rank
                rows.append(row)
    rows.sort(
        key=lambda r: (
            str(r.get("date") or ""),
            str(r.get("raceID") or ""),
            int(r.get("rankByConservativeEV") or 9999),
        )
    )
    return rows


def select_profile(rows: list[dict], rule: dict) -> list[dict]:
    threshold = float(rule["thresholdEV"])
    top_n = int(rule["topNPerRace"])
    selected: list[dict] = []
    used: dict[tuple[str, str], int] = {}
    for row in rows:
        try:
            ev = float(row.get("conservativeExpectedValue") or 0.0)
        except Exception:
            continue
        if ev < threshold:
            continue
        key = (str(row.get("date") or ""), str(row.get("raceID") or ""))
        count = used.get(key, 0)
        if count >= top_n:
            continue
        selected.append(row)
        used[key] = count + 1
    return selected


def return_index(rows: list[dict]) -> float | None:
    if not rows:
        return None
    stake = 100 * len(rows)
    returned = sum(int(r.get("payoutPer100Yen") or 0) for r in rows)
    return returned / stake if stake else None


def max_losing_streak(rows: list[dict]) -> int:
    best = 0
    cur = 0
    for row in rows:
        if bool(row.get("hit")):
            cur = 0
        else:
            cur += 1
            best = max(best, cur)
    return best


def bootstrap_race_ci(rows: list[dict], reps: int = BOOTSTRAP_REPS) -> tuple[float | None, float | None]:
    by_race: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (str(row.get("date") or ""), str(row.get("raceID") or ""))
        by_race.setdefault(key, []).append(row)
    groups = list(by_race.values())
    if len(groups) < 2:
        return None, None

    rng = random.Random(BOOTSTRAP_SEED)
    samples: list[float] = []
    n = len(groups)
    for _ in range(reps):
        draw: list[dict] = []
        for _ in range(n):
            draw.extend(groups[rng.randrange(n)])
        ri = return_index(draw)
        if ri is not None:
            samples.append(ri)
    if not samples:
        return None, None
    samples.sort()
    lo_idx = max(0, int(0.025 * (len(samples) - 1)))
    hi_idx = min(len(samples) - 1, int(0.975 * (len(samples) - 1)))
    return samples[lo_idx], samples[hi_idx]


def recent_half(rows: list[dict]) -> list[dict]:
    race_keys = []
    seen = set()
    for row in rows:
        key = (str(row.get("date") or ""), str(row.get("raceID") or ""))
        if key not in seen:
            seen.add(key)
            race_keys.append(key)
    if not race_keys:
        return []
    keep = set(race_keys[len(race_keys) // 2 :])
    return [
        r for r in rows
        if (str(r.get("date") or ""), str(r.get("raceID") or "")) in keep
    ]


def evaluate_profile(all_rows: list[dict], rule: dict) -> dict:
    selected = select_profile(all_rows, rule)
    race_keys = {
        (str(r.get("date") or ""), str(r.get("raceID") or ""))
        for r in selected
    }
    hits = sum(bool(r.get("hit")) for r in selected)
    stake = 100 * len(selected)
    returned = sum(int(r.get("payoutPer100Yen") or 0) for r in selected)
    ri = returned / stake if stake else None
    lo, hi = bootstrap_race_ci(selected)
    recent_ri = return_index(recent_half(selected))

    gates = {
        "validatedRaces": len(race_keys) >= REVIEW_GATES["minValidatedRaces"],
        "selectedBets": len(selected) >= REVIEW_GATES["minSelectedBets"],
        "overallReturnIndex": ri is not None and ri >= REVIEW_GATES["minOverallReturnIndex"],
        "bootstrap95Lower": lo is not None and lo >= REVIEW_GATES["minBootstrap95LowerReturnIndex"],
        "recentHalfReturnIndex": recent_ri is not None and recent_ri >= REVIEW_GATES["minRecentHalfReturnIndex"],
    }
    ready = all(gates.values())

    return {
        "rule": rule,
        "selectedRaces": len(race_keys),
        "selectedBets": len(selected),
        "hits": hits,
        "hitRatePct": round(hits / len(selected) * 100.0, 3) if selected else None,
        "stakeYenAt100PerBet": stake,
        "returnYenAt100PerBet": returned,
        "returnIndex": round(ri, 4) if ri is not None else None,
        "bootstrap95ReturnIndex": {
            "lower": round(lo, 4) if lo is not None else None,
            "upper": round(hi, 4) if hi is not None else None,
            "resamplingUnit": "race",
            "repetitions": BOOTSTRAP_REPS,
        },
        "recentHalfReturnIndex": round(recent_ri, 4) if recent_ri is not None else None,
        "maxConsecutiveLosingBets": max_losing_streak(selected),
        "reviewGates": gates,
        "status": "review_candidate" if ready else "data_collecting",
    }


def build_payload(rows: list[dict]) -> dict:
    total_races = len({
        (str(r.get("date") or ""), str(r.get("raceID") or ""))
        for r in rows
    })
    profiles = {
        name: evaluate_profile(rows, rule)
        for name, rule in PROFILES.items()
    }
    return {
        "schemaVersion": 1,
        "ruleVersion": RULE_VERSION,
        "rulesFrozenAtJST": "2026-10-12",
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "将来の馬券プラン候補を、後から都合よく閾値変更せず実運用データで検証するための影運用。",
        "validatedRacesAvailable": total_races,
        "reviewGates": REVIEW_GATES,
        "autoBetEnabled": False,
        "moneyAllocationEnabled": False,
        "important": (
            "review_candidateになっても自動で馬券プランを有効化しません。"
            "データ漏洩、時点、期間安定性を人手で再確認してから別版として判断します。"
        ),
        "profiles": profiles,
    }


def main() -> None:
    payload = build_payload(load_rows())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("rule version:", RULE_VERSION)
    print("validated races available:", payload["validatedRacesAvailable"])
    for name, result in payload["profiles"].items():
        print(name, result["status"], "bets=", result["selectedBets"], "RI=", result["returnIndex"])
    print("money allocation enabled:", payload["moneyAllocationEnabled"])
    print("output:", OUT_PATH)


if __name__ == "__main__":
    main()
