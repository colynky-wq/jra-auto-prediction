# KeibaAI research-only probe of the public live odds endpoint.
# It does not write production files or change any model.

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import requests

JST = timezone(timedelta(hours=9))
RACE_ID = os.environ.get("KEIBAAI_ODDS_PROBE_RACE_ID", "202605040506").strip()
URL = "https://race.netkeiba.com/api/api_get_jra_odds.html"
TYPES = [1, 3, 4, 5, 6, 7, 8]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}


def summarize_map(m: object) -> dict:
    if not isinstance(m, dict):
        return {"kind": type(m).__name__}
    keys = list(m.keys())
    samples = []
    for k in keys[:3]:
        v = m[k]
        samples.append({"key": k, "value": v})
    value_lengths = {}
    for v in m.values():
        n = len(v) if isinstance(v, list) else -1
        value_lengths[str(n)] = value_lengths.get(str(n), 0) + 1
    return {
        "count": len(m),
        "sample": samples,
        "valueLengthCounts": value_lengths,
    }


def main() -> None:
    print("KeibaAI all-odds API probe")
    print("JST:", datetime.now(JST).isoformat())
    print("race:", RACE_ID)
    print("research only / no production writes")

    s = requests.Session()
    s.headers.update(HEADERS)
    result = {}

    for api_type in TYPES:
        referer = f"https://race.netkeiba.com/odds/index.html?race_id={RACE_ID}&type=b{api_type}"
        r = s.get(
            URL,
            params={
                "race_id": RACE_ID,
                "type": api_type,
                "action": "init",
                "compress": 0,
                "output": "json",
            },
            headers={**HEADERS, "Referer": referer},
            timeout=30,
        )
        item = {"http": r.status_code, "bytes": len(r.content)}
        try:
            payload = r.json()
            item["status"] = payload.get("status")
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            item["official_datetime"] = data.get("official_datetime")
            odds = data.get("odds") if isinstance(data.get("odds"), dict) else {}
            item["oddsRootKeys"] = list(odds.keys())
            item["maps"] = {str(k): summarize_map(v) for k, v in odds.items()}
        except Exception as e:
            item["jsonError"] = repr(e)
            item["textPrefix"] = r.text[:300]
        result[str(api_type)] = item
        print("\nTYPE", api_type)
        print(json.dumps(item, ensure_ascii=False, indent=2)[:6000])

    print("\nFINAL_JSON")
    print(json.dumps({"raceID": RACE_ID, "types": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
