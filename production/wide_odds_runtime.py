from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS_DIR = REPO / "reports"
WIDE_ODDS_PATH = REPO / "WideOddsData.json"
URL = "https://race.netkeiba.com/api/api_get_jra_odds.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}


def _number(value):
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"0", "0.0", "-"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _integer(value):
    x = _number(value)
    return int(x) if x is not None else None


def _load_payload(path: Path = WIDE_ODDS_PATH) -> dict:
    if not path.exists():
        return {"schemaVersion": 1, "betType": "ワイド", "snapshots": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schemaVersion": 1, "betType": "ワイド", "snapshots": []}
    if isinstance(value, list):
        return {"schemaVersion": 1, "betType": "ワイド", "snapshots": value}
    if not isinstance(value, dict):
        return {"schemaVersion": 1, "betType": "ワイド", "snapshots": []}
    if not isinstance(value.get("snapshots"), list):
        value["snapshots"] = []
    return value


def _write_payload(payload: dict, path: Path = WIDE_ODDS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _date8(race_date: str | None) -> str | None:
    text = str(race_date or "").replace("-", "")
    return text if len(text) == 8 and text.isdigit() else None


def _minutes_to_post(race_date: str | None, start_time: str | None, captured_at: str | None) -> float | None:
    d8 = _date8(race_date)
    if d8 is None:
        return None
    try:
        post = datetime.strptime(f"{d8} {start_time}", "%Y%m%d %H:%M").replace(tzinfo=JST)
        captured = datetime.strptime(str(captured_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
        return round((post - captured).total_seconds() / 60.0, 2)
    except Exception:
        return None


def _archive_snapshot(snapshot: dict) -> Path | None:
    """Write an immutable per-race copy used for later forward validation."""
    d8 = _date8(snapshot.get("raceDate"))
    rid = str(snapshot.get("raceID") or "").strip()
    if d8 is None or not rid:
        return None

    path = REPORTS_DIR / d8 / "wide_odds" / f"{rid}.json"
    if path.exists():
        return path

    archived = dict(snapshot)
    archived["minutesToPost"] = _minutes_to_post(
        archived.get("raceDate"),
        archived.get("startTime"),
        archived.get("capturedAtJST"),
    )
    archived["frozenAtFirstSuccessfulCapture"] = True
    archived["purpose"] = "発走前ワイド実オッズを後から確定払戻と突合するための固定スナップショット"

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(archived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if path.exists():
        tmp.unlink(missing_ok=True)
        return path
    tmp.replace(path)
    return path


def fetch_wide_snapshot(
    race_id: str,
    *,
    race_date: str | None = None,
    venue: str | None = None,
    race_number: int | None = None,
    start_time: str | None = None,
    retries: int = 3,
) -> dict:
    race_id = str(race_id).strip()
    last_error: Exception | None = None

    for attempt in range(max(1, retries)):
        try:
            session = requests.Session()
            session.headers.update(HEADERS)
            referer = f"https://race.netkeiba.com/odds/index.html?race_id={race_id}&type=b5"
            response = session.get(
                URL,
                params={
                    "race_id": race_id,
                    "type": 5,
                    "action": "init",
                    "compress": 0,
                    "output": "json",
                },
                headers={**HEADERS, "Referer": referer},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            odds_root = data.get("odds") if isinstance(data.get("odds"), dict) else {}
            raw = odds_root.get("5") if isinstance(odds_root.get("5"), dict) else {}

            pairs = []
            for key, value in raw.items():
                key = str(key)
                if len(key) != 4 or not key.isdigit() or not isinstance(value, list) or len(value) < 2:
                    continue
                low = _number(value[0])
                high = _number(value[1])
                if low is None or high is None:
                    continue
                pairs.append(
                    {
                        "number1": int(key[:2]),
                        "number2": int(key[2:4]),
                        "oddsLow": low,
                        "oddsHigh": high,
                        "popularity": _integer(value[2]) if len(value) >= 3 else None,
                    }
                )

            pairs.sort(key=lambda x: (x["number1"], x["number2"]))
            if not pairs:
                raise RuntimeError(
                    f"no Wide pairs parsed: status={payload.get('status')!r} roots={list(odds_root.keys())}"
                )

            captured_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            return {
                "raceDate": race_date,
                "raceID": race_id,
                "venue": venue,
                "raceNumber": race_number,
                "startTime": start_time,
                "capturedAtJST": captured_at,
                "minutesToPost": _minutes_to_post(race_date, start_time, captured_at),
                "apiStatus": payload.get("status"),
                "officialDatetime": data.get("official_datetime"),
                "betType": "ワイド",
                "apiType": 5,
                "pairCount": len(pairs),
                "pairs": pairs,
                "frozenAtFirstSuccessfulCapture": True,
            }
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max(1, retries):
                time.sleep(2)

    raise RuntimeError(f"Wide odds fetch failed for {race_id}: {last_error!r}")


def first_snapshot_for_race(race_id: str, path: Path = WIDE_ODDS_PATH) -> dict | None:
    payload = _load_payload(path)
    for item in payload.get("snapshots", []):
        if str(item.get("raceID") or "") == str(race_id):
            return item
    return None


def record_first_wide_snapshot(
    *,
    race_id: str,
    race_date: str,
    venue: str | None = None,
    race_number: int | None = None,
    start_time: str | None = None,
    path: Path = WIDE_ODDS_PATH,
) -> dict:
    """Freeze the first accepted prediction-time Wide snapshot for a race.

    Keeping the first snapshot prevents a later run from silently replacing the
    odds that were actually available when the prediction was published. An
    immutable reports/YYYYMMDD/wide_odds/<raceID>.json copy is also written so
    historical forward validation never depends on the rotating root file.
    """
    payload = _load_payload(path)
    snapshots = [
        item
        for item in payload.get("snapshots", [])
        if str(item.get("raceDate") or "") == str(race_date)
    ]

    for item in snapshots:
        if str(item.get("raceID") or "") == str(race_id):
            _archive_snapshot(item)
            return item

    snapshot = fetch_wide_snapshot(
        race_id,
        race_date=race_date,
        venue=venue,
        race_number=race_number,
        start_time=start_time,
    )
    snapshots.append(snapshot)
    snapshots.sort(key=lambda x: (str(x.get("startTime") or ""), str(x.get("raceID") or "")))

    payload = {
        "schemaVersion": 1,
        "betType": "ワイド",
        "snapshotPolicy": "first prediction-time snapshot is frozen per race",
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "snapshots": snapshots,
    }
    _write_payload(payload, path)
    _archive_snapshot(snapshot)
    return snapshot
