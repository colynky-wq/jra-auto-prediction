from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS_DIR = REPO / "reports"
SIGNAL_ROOT = REPO / "SignalData.json"
RESULT_URL = "https://race.netkeiba.com/race/result.html"
TARGET_DATE8 = (
    os.environ.get("KEIBAAI_WIDE_VALIDATE_DATE", "").strip()
    or datetime.now(JST).strftime("%Y%m%d")
)
DELAY_MINUTES = int(os.environ.get("KEIBAAI_WIDE_VALIDATE_DELAY_MINUTES", "5") or "5")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _yen_values(value) -> list[int]:
    return [int(x.replace(",", "")) for x in re.findall(r"([0-9][0-9,]*)\s*円", str(value))]


def _numbers(value) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", str(value))]


def fetch_wide_payouts(race_id: str) -> dict[tuple[int, int], int]:
    r = requests.get(
        RESULT_URL,
        params={"race_id": str(race_id)},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    html = r.content.decode("utf-8", errors="replace")
    tables = pd.read_html(io.StringIO(html))

    for table in tables:
        if table.shape[1] < 3:
            continue
        for _, row in table.iterrows():
            if str(row.iloc[0]).strip() != "ワイド":
                continue
            nums = _numbers(row.iloc[1])
            yen = _yen_values(row.iloc[2])
            if len(nums) < 2 or not yen:
                continue
            pairs = []
            for i in range(0, len(nums) - 1, 2):
                a, b = sorted((nums[i], nums[i + 1]))
                pairs.append((a, b))
            if len(pairs) != len(yen):
                raise RuntimeError(
                    f"Wide payout parse mismatch race={race_id}: pairs={pairs} payouts={yen}"
                )
            return dict(zip(pairs, yen))

    raise RuntimeError(f"Wide payout row not found: {race_id}")


def _signal_payload() -> dict:
    day = REPORTS_DIR / TARGET_DATE8 / "SignalData.json"
    value = _load_json(day, {})
    if isinstance(value, dict) and value.get("races"):
        return value
    value = _load_json(SIGNAL_ROOT, {})
    return value if isinstance(value, dict) else {}


def _post_time(signal: dict) -> datetime | None:
    try:
        return datetime.strptime(
            f"{TARGET_DATE8} {signal.get('startTime')}",
            "%Y%m%d %H:%M",
        ).replace(tzinfo=JST)
    except Exception:
        return None


def _result_ready_by_time(signal: dict) -> bool:
    post = _post_time(signal)
    if post is None:
        return False
    return datetime.now(JST) >= post + timedelta(minutes=DELAY_MINUTES)


def evaluate_race(signal: dict, snapshot: dict, payouts: dict[tuple[int, int], int]) -> dict:
    odds_by_pair = {}
    for p in snapshot.get("pairs") or []:
        try:
            key = tuple(sorted((int(p["number1"]), int(p["number2"]))))
        except Exception:
            continue
        odds_by_pair[key] = p

    evaluated = []
    for p in ((signal.get("wideEvaluation") or {}).get("pairs") or []):
        try:
            key = tuple(sorted((int(p["number1"]), int(p["number2"]))))
        except Exception:
            continue
        odds = odds_by_pair.get(key)
        if odds is None:
            continue
        payout = int(payouts.get(key, 0) or 0)
        ai_prob = max(0.0, min(1.0, float(p.get("aiWideProbability") or 0.0) / 100.0))
        low = float(odds.get("oddsLow") or 0.0)
        high = float(odds.get("oddsHigh") or low)
        ev_low = ai_prob * low
        ev_high = ai_prob * high
        evaluated.append(
            {
                "number1": key[0],
                "name1": p.get("name1"),
                "number2": key[1],
                "name2": p.get("name2"),
                "marketWideProbabilityPct": p.get("marketWideProbability"),
                "aiWideProbabilityPct": round(ai_prob * 100.0, 3),
                "probabilityEdgePct": p.get("probabilityEdgePct"),
                "oddsLow": low,
                "oddsHigh": high,
                "conservativeExpectedValue": round(ev_low, 4),
                "upperExpectedValue": round(ev_high, 4),
                "hit": bool(payout > 0),
                "payoutPer100Yen": payout,
                "returnIndexPer100Yen": round(payout / 100.0, 3) if payout else 0.0,
            }
        )

    evaluated.sort(
        key=lambda x: (
            -x["conservativeExpectedValue"],
            -x["aiWideProbabilityPct"],
            x["number1"],
            x["number2"],
        )
    )

    return {
        "raceDate": signal.get("raceDate"),
        "raceID": str(signal.get("raceID") or snapshot.get("raceID") or ""),
        "venue": signal.get("venue"),
        "raceNumber": signal.get("raceNumber"),
        "startTime": signal.get("startTime"),
        "raceName": signal.get("raceName"),
        "course": signal.get("course"),
        "chaosStars": ((signal.get("chaos") or {}).get("stars")),
        "chaosLevel": ((signal.get("chaos") or {}).get("level")),
        "battleGrade": ((signal.get("battle") or {}).get("grade")),
        "snapshotCapturedAtJST": snapshot.get("capturedAtJST"),
        "minutesToPost": snapshot.get("minutesToPost"),
        "evaluationScope": "SignalData wideEvaluation pairs only (current v1 is top 20)",
        "evaluatedPairs": len(evaluated),
        "pairs": evaluated,
    }


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    day = REPORTS_DIR / TARGET_DATE8
    signal_payload = _signal_payload()
    signals = signal_payload.get("races") if isinstance(signal_payload, dict) else []
    by_id = {str(r.get("raceID") or ""): r for r in (signals or [])}

    out_path = day / "WideValidation.json"
    old = _load_json(out_path, {})
    existing = old.get("races") if isinstance(old, dict) else []
    by_validated = {
        str(r.get("raceID") or ""): r
        for r in (existing or [])
        if str(r.get("raceID") or "")
    }

    snapshots = sorted((day / "wide_odds").glob("*.json"))
    added = 0
    waiting = 0

    for path in snapshots:
        snapshot = _load_json(path, {})
        rid = str(snapshot.get("raceID") or path.stem)
        if rid in by_validated:
            continue
        signal = by_id.get(rid)
        if not signal or not bool(signal.get("eligible", False)):
            continue
        if not _result_ready_by_time(signal):
            continue
        try:
            payouts = fetch_wide_payouts(rid)
        except Exception as exc:
            waiting += 1
            print("WIDE RESULT WAIT:", rid, repr(exc))
            continue
        by_validated[rid] = evaluate_race(signal, snapshot, payouts)
        added += 1
        print("WIDE VALIDATED:", rid, "pairs=", by_validated[rid]["evaluatedPairs"])

    races = list(by_validated.values())
    races.sort(
        key=lambda r: (
            str(r.get("startTime") or ""),
            str(r.get("venue") or ""),
            int(r.get("raceNumber") or 0),
            str(r.get("raceID") or ""),
        )
    )
    payload = {
        "schemaVersion": 1,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "date": f"{TARGET_DATE8[:4]}-{TARGET_DATE8[4:6]}-{TARGET_DATE8[6:8]}",
        "meaning": "発走前に固定したAIワイド評価と実オッズを、レース後の確定ワイド払戻と突合した実運用検証。",
        "ticketPlanAutoEnable": False,
        "races": races,
    }
    if races or snapshots:
        _write_json(out_path, payload)

    print("target:", TARGET_DATE8)
    print("snapshots:", len(snapshots))
    print("validated races:", len(races))
    print("new validations:", added)
    print("waiting result:", waiting)
    print("output:", out_path)


if __name__ == "__main__":
    main()
