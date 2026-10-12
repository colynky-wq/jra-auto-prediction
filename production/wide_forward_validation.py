from __future__ import annotations

import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS_DIR = REPO / "reports"
RESULT_URL = "https://race.netkeiba.com/race/result.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}


def _yen_values(value) -> list[int]:
    return [int(x.replace(",", "")) for x in re.findall(r"([0-9][0-9,]*)\s*円", str(value))]


def _numbers(value) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", str(value))]


def fetch_wide_payouts(race_id: str) -> dict[tuple[int, int], int]:
    """Return final Wide payout per 100 yen keyed by sorted horse-number pair."""
    r = requests.get(RESULT_URL, params={"race_id": str(race_id)}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.content.decode("utf-8", errors="replace")
    tables = pd.read_html(io.StringIO(html))

    for table in tables:
        t = table.copy()
        if t.shape[1] < 3:
            continue
        for _, row in t.iterrows():
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


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def evaluate_race(signal: dict, snapshot: dict, payouts: dict[tuple[int, int], int]) -> dict:
    odds_by_pair = {}
    for p in snapshot.get("pairs") or []:
        try:
            key = tuple(sorted((int(p["number1"]), int(p["number2"]))))
        except Exception:
            continue
        odds_by_pair[key] = p

    wide_eval = signal.get("wideEvaluation") or {}
    source_pairs = wide_eval.get("validationPairs") or wide_eval.get("pairs") or []

    evaluated = []
    for p in source_pairs:
        try:
            key = tuple(sorted((int(p["number1"]), int(p["number2"]))))
        except Exception:
            continue
        odds = odds_by_pair.get(key)
        if odds is None:
            continue
        payout = payouts.get(key, 0)
        ai_prob = float(p.get("aiWideProbability") or 0.0) / 100.0
        low = float(odds.get("oddsLow") or 0.0)
        high = float(odds.get("oddsHigh") or low)
        conservative_ev = ai_prob * low
        upper_ev = ai_prob * high
        evaluated.append({
            "number1": key[0],
            "number2": key[1],
            "aiWideProbabilityPct": round(ai_prob * 100.0, 3),
            "oddsLow": low,
            "oddsHigh": high,
            "conservativeExpectedValue": round(conservative_ev, 4),
            "upperExpectedValue": round(upper_ev, 4),
            "hit": bool(payout > 0),
            "payoutPer100Yen": int(payout),
            "returnIndexPer100Yen": round(payout / 100.0, 3) if payout else 0.0,
        })

    evaluated.sort(
        key=lambda x: (-x["conservativeExpectedValue"], -x["aiWideProbabilityPct"], x["number1"], x["number2"])
    )
    return {
        "raceID": str(signal.get("raceID") or snapshot.get("raceID") or ""),
        "raceDate": signal.get("raceDate"),
        "venue": signal.get("venue"),
        "raceNumber": signal.get("raceNumber"),
        "evaluatedPairs": len(evaluated),
        "validationScope": "all_pairs" if wide_eval.get("validationPairs") else "display_pairs_only",
        "pairs": evaluated,
    }


def build_date(date8: str) -> dict:
    day = REPORTS_DIR / date8
    signal_payload = _load_json(day / "SignalData.json", {})
    signals = signal_payload.get("races") if isinstance(signal_payload, dict) else []
    by_id = {str(r.get("raceID") or ""): r for r in (signals or [])}
    races = []

    for path in sorted((day / "wide_odds").glob("*.json")):
        snapshot = _load_json(path, {})
        rid = str(snapshot.get("raceID") or path.stem)
        signal = by_id.get(rid)
        if not signal or not bool(signal.get("eligible", False)):
            continue
        try:
            payouts = fetch_wide_payouts(rid)
        except Exception as exc:
            print("WIDE RESULT NOT READY:", rid, repr(exc))
            continue
        races.append(evaluate_race(signal, snapshot, payouts))

    payload = {
        "schemaVersion": 1,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "date": f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}",
        "meaning": "発走前に固定保存したワイド評価と、確定払戻を後から突合した前向き検証。",
        "races": races,
    }
    out = day / "WideValidation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    date8 = datetime.now(JST).strftime("%Y%m%d")
    payload = build_date(date8)
    print("date:", date8, "validated races:", len(payload["races"]))
