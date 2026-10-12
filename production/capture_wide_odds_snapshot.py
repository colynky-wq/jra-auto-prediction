# KeibaAI research-only Wide odds snapshot recorder.
# Validates and stores pre-race Wide odds without touching production files.

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

JST = timezone(timedelta(hours=9))
RACE_ID = os.environ.get("KEIBAAI_WIDE_RACE_ID", "202605040508").strip()
OUT = Path(os.environ.get("KEIBAAI_WIDE_OUT", "wide_odds_artifacts/wide_snapshot.json"))
URL = "https://race.netkeiba.com/api/api_get_jra_odds.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}


def number(value):
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"0", "0.0", "-"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def integer(value):
    x = number(value)
    return int(x) if x is not None else None


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS)
    referer = f"https://race.netkeiba.com/odds/index.html?race_id={RACE_ID}&type=b5"
    r = s.get(
        URL,
        params={
            "race_id": RACE_ID,
            "type": 5,
            "action": "init",
            "compress": 0,
            "output": "json",
        },
        headers={**HEADERS, "Referer": referer},
        timeout=30,
    )
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    odds_root = data.get("odds") if isinstance(data.get("odds"), dict) else {}
    raw = odds_root.get("5") if isinstance(odds_root.get("5"), dict) else {}

    pairs = []
    for key, value in raw.items():
        key = str(key)
        if len(key) != 4 or not key.isdigit() or not isinstance(value, list) or len(value) < 2:
            continue
        n1, n2 = int(key[:2]), int(key[2:4])
        low = number(value[0])
        high = number(value[1])
        popularity = integer(value[2]) if len(value) >= 3 else None
        pairs.append({
            "number1": n1,
            "number2": n2,
            "oddsLow": low,
            "oddsHigh": high,
            "popularity": popularity,
        })

    pairs.sort(key=lambda x: (x["number1"], x["number2"]))
    result = {
        "raceID": RACE_ID,
        "capturedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "apiStatus": payload.get("status"),
        "officialDatetime": data.get("official_datetime"),
        "betType": "ワイド",
        "apiType": 5,
        "pairCount": len(pairs),
        "pairs": pairs,
        "researchOnly": True,
    }
    if not pairs:
        raise RuntimeError(f"no Wide pairs parsed: status={payload.get('status')!r} root={list(odds_root.keys())}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("race:", RACE_ID)
    print("status:", result["apiStatus"])
    print("official:", result["officialDatetime"])
    print("pairs:", len(pairs))
    print("sample:", json.dumps(pairs[:5], ensure_ascii=False))
    print("output:", OUT)


if __name__ == "__main__":
    main()
