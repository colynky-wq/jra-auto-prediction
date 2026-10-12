from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RACEDATA_PATH = REPO / "RaceData.json"
REPORTS_DIR = REPO / "reports"
TARGET_DATE8 = (
    os.environ.get("KEIBAAI_WIDE_DATE", "").strip()
    or datetime.now(JST).strftime("%Y%m%d")
)
URL = "https://race.netkeiba.com/api/api_get_jra_odds.html"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}


def number(value):
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"0", "0.0", "-"}:
        return None
    try:
        x = float(text)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def integer(value):
    x = number(value)
    return int(x) if x is not None else None


def wanted_date_text() -> str:
    if len(TARGET_DATE8) != 8 or not TARGET_DATE8.isdigit():
        raise RuntimeError(f"invalid target date: {TARGET_DATE8!r}")
    return f"{TARGET_DATE8[:4]}-{TARGET_DATE8[4:6]}-{TARGET_DATE8[6:8]}"


def load_races() -> list[dict]:
    if not RACEDATA_PATH.exists():
        return []
    try:
        data = json.loads(RACEDATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    wanted = wanted_date_text()
    return [r for r in data if str(r.get("raceDate") or "") == wanted]


def post_time(race: dict) -> datetime | None:
    try:
        return datetime.strptime(
            f"{TARGET_DATE8} {race.get('startTime')}",
            "%Y%m%d %H:%M",
        ).replace(tzinfo=JST)
    except Exception:
        return None


def fetch_wide_snapshot(race: dict) -> dict:
    rid = str(race.get("raceID") or "").zfill(12)
    s = requests.Session()
    s.headers.update(HEADERS)
    referer = f"https://race.netkeiba.com/odds/index.html?race_id={rid}&type=b5"
    r = s.get(
        URL,
        params={
            "race_id": rid,
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
        if low is None or high is None:
            continue
        pairs.append(
            {
                "number1": n1,
                "number2": n2,
                "oddsLow": low,
                "oddsHigh": high,
                "popularity": integer(value[2]) if len(value) >= 3 else None,
            }
        )

    pairs.sort(key=lambda x: (x["number1"], x["number2"]))
    if len(pairs) < 3:
        raise RuntimeError(
            f"Wide odds not ready race={rid}: status={payload.get('status')!r} pairs={len(pairs)}"
        )

    now = datetime.now(JST)
    post = post_time(race)
    minutes_to_post = None
    if post is not None:
        minutes_to_post = round((post - now).total_seconds() / 60.0, 2)

    return {
        "schemaVersion": 1,
        "raceDate": wanted_date_text(),
        "raceID": rid,
        "venue": race.get("venue"),
        "raceNumber": race.get("raceNumber"),
        "startTime": race.get("startTime"),
        "raceName": race.get("raceName"),
        "course": race.get("course"),
        "fieldSizeFromRaceData": len(race.get("horses") or []),
        "capturedAtJST": now.strftime("%Y-%m-%d %H:%M:%S"),
        "minutesToPost": minutes_to_post,
        "apiStatus": payload.get("status"),
        "officialDatetime": data.get("official_datetime"),
        "betType": "ワイド",
        "apiType": 5,
        "pairCount": len(pairs),
        "pairs": pairs,
        "frozenAtFirstSuccessfulCapture": True,
        "purpose": "実運用の発走前ワイド評価を後から結果と突合するための固定スナップショット",
    }


def write_once(path: Path, payload: dict) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if path.exists():
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(path)
    return True


def main() -> None:
    races = load_races()
    now = datetime.now(JST)
    out_dir = REPORTS_DIR / TARGET_DATE8 / "wide_odds"
    captured = 0
    waiting = 0
    skipped_after_post = 0

    for race in races:
        rid = str(race.get("raceID") or "").zfill(12)
        if not rid.strip("0"):
            continue
        out = out_dir / f"{rid}.json"
        if out.exists():
            continue

        post = post_time(race)
        # The snapshot is strictly pre-race. Never fill a missing snapshot after
        # the scheduled post time, because that would contaminate forward tests.
        if post is None or now >= post:
            skipped_after_post += 1
            continue

        try:
            payload = fetch_wide_snapshot(race)
        except Exception as exc:
            waiting += 1
            print("WIDE WAIT:", rid, repr(exc))
            continue

        if write_once(out, payload):
            captured += 1
            print(
                "WIDE FROZEN:", rid,
                "pairs=", payload["pairCount"],
                "minutes_to_post=", payload["minutesToPost"],
                "path=", out,
            )

    print("target:", TARGET_DATE8)
    print("RaceData races:", len(races))
    print("new frozen snapshots:", captured)
    print("waiting odds:", waiting)
    print("skipped because post time passed:", skipped_after_post)


if __name__ == "__main__":
    main()
