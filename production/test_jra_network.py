# ============================================================
# KeibaAI GitHub Actions
# JRA / netkeiba network access smoke test
#
# Purpose:
# - Confirm a GitHub-hosted runner can reach the race list page.
# - Confirm a race page can be fetched.
# - Confirm the JRA win-odds API returns parseable odds.
#
# This script does not write prediction data and does not modify RaceData.json.
# ============================================================

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests


JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}

DEFAULT_TEST_DATE = "20261011"
DEFAULT_TEST_RACE_ID = "202608040405"

TEST_DATE = os.environ.get("KEIBAAI_TEST_DATE", "").strip() or DEFAULT_TEST_DATE
TEST_RACE_ID = os.environ.get("KEIBAAI_TEST_RACE_ID", "").strip() or DEFAULT_TEST_RACE_ID


def fail(message: str) -> None:
    print()
    print("=" * 100)
    print("JRA NETWORK TEST: FAILED")
    print("=" * 100)
    print(message)
    sys.exit(1)


def safe_get(session: requests.Session, url: str, *, params=None, headers=None, timeout=30):
    merged = dict(HEADERS)
    if headers:
        merged.update(headers)

    try:
        response = session.get(
            url,
            params=params,
            headers=merged,
            timeout=timeout,
        )
    except Exception as exc:
        fail(f"GET failed: {url}\n{type(exc).__name__}: {exc}")

    print(f"HTTP {response.status_code} | {response.url}")

    if response.status_code != 200:
        fail(
            f"HTTP status was not 200: {response.status_code}\n"
            f"URL: {response.url}\n"
            f"Body preview: {response.text[:300]!r}"
        )

    return response


def main() -> None:
    print("=" * 100)
    print("KeibaAI GitHub-hosted JRA network test")
    print("time JST:", datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"))
    print("test date:", TEST_DATE)
    print("test race id:", TEST_RACE_ID)
    print("=" * 100)

    if not re.fullmatch(r"\d{8}", TEST_DATE):
        fail(f"Invalid KEIBAAI_TEST_DATE: {TEST_DATE!r}")

    if not re.fullmatch(r"\d{12}", TEST_RACE_ID):
        fail(f"Invalid KEIBAAI_TEST_RACE_ID: {TEST_RACE_ID!r}")

    session = requests.Session()

    # --------------------------------------------------------
    # 1. Race list page
    # --------------------------------------------------------
    print()
    print("[1] Race list page")

    race_list_url = "https://race.netkeiba.com/top/race_list_sub.html"
    race_list = safe_get(
        session,
        race_list_url,
        params={"kaisai_date": TEST_DATE},
    )

    race_list_text = race_list.content.decode("utf-8", errors="replace")
    race_ids = sorted(set(re.findall(r"race_id=(\d{12})", race_list_text)))

    print("race-list bytes:", len(race_list.content))
    print("race ids found:", len(race_ids))
    print("first race ids:", race_ids[:5])

    if len(race_list.content) < 1000:
        fail("Race-list response was unexpectedly small.")

    if not race_ids:
        fail("No 12-digit race IDs were found in the race-list response.")

    if TEST_RACE_ID in race_ids:
        print("configured race id is present in race list: YES")
    else:
        print("configured race id is present in race list: NO (continuing with explicit test race)")

    # --------------------------------------------------------
    # 2. Race entry page
    # --------------------------------------------------------
    print()
    print("[2] Race entry page")

    shutuba_url = "https://race.netkeiba.com/race/shutuba.html"
    shutuba = safe_get(
        session,
        shutuba_url,
        params={"race_id": TEST_RACE_ID},
    )

    shutuba_text = shutuba.content.decode("utf-8", errors="replace")

    print("entry-page bytes:", len(shutuba.content))
    print("contains race id:", TEST_RACE_ID in shutuba_text)
    print(
        "contains race-page marker:",
        any(
            marker in shutuba_text
            for marker in (
                "Shutuba_Table",
                "RaceTable01",
                "RaceName",
                "HorseList",
            )
        ),
    )

    if len(shutuba.content) < 5000:
        fail("Race entry page was unexpectedly small; cloud access may be blocked or redirected.")

    if not any(
        marker in shutuba_text
        for marker in (
            "Shutuba_Table",
            "RaceTable01",
            "RaceName",
            "HorseList",
        )
    ):
        fail("Expected race-page markers were not found.")

    # --------------------------------------------------------
    # 3. Win odds API
    # --------------------------------------------------------
    print()
    print("[3] JRA win-odds API")

    odds_url = "https://race.netkeiba.com/api/api_get_jra_odds.html"
    referer = f"https://race.netkeiba.com/odds/index.html?race_id={TEST_RACE_ID}"

    odds_response = safe_get(
        session,
        odds_url,
        params={
            "race_id": TEST_RACE_ID,
            "type": 1,
            "action": "init",
            "compress": 0,
        },
        headers={"Referer": referer},
    )

    try:
        payload = odds_response.json()
    except Exception as exc:
        fail(
            "Odds endpoint returned HTTP 200 but was not valid JSON.\n"
            f"{type(exc).__name__}: {exc}\n"
            f"Body preview: {odds_response.text[:300]!r}"
        )

    if not isinstance(payload, dict):
        fail(f"Odds JSON root is not a dict: {type(payload).__name__}")

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    odds_root = data.get("odds") if isinstance(data.get("odds"), dict) else {}
    win_odds = odds_root.get("1") if isinstance(odds_root.get("1"), dict) else {}

    parsed = []
    for horse_number, value in win_odds.items():
        if isinstance(value, list) and len(value) >= 1:
            parsed.append((str(horse_number), value[0]))

    print("API status:", payload.get("status"))
    print("official datetime:", data.get("official_datetime"))
    print("win-odds horses:", len(parsed))
    print("odds sample:", parsed[:5])

    if len(parsed) < 2:
        fail(
            "The odds API was reachable, but fewer than 2 win-odds rows were returned. "
            "This is not sufficient for production 10-minute prediction."
        )

    # --------------------------------------------------------
    # 4. Final summary
    # --------------------------------------------------------
    summary = {
        "ok": True,
        "tested_at_jst": datetime.now(JST).isoformat(),
        "test_date": TEST_DATE,
        "test_race_id": TEST_RACE_ID,
        "race_ids_found": len(race_ids),
        "entry_page_bytes": len(shutuba.content),
        "odds_horse_count": len(parsed),
        "odds_status": payload.get("status"),
        "official_datetime": data.get("official_datetime"),
    }

    print()
    print("=" * 100)
    print("JRA NETWORK TEST: SUCCESS")
    print("=" * 100)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print()
    print("GitHub-hosted runner can reach the race list, race page, and win-odds API.")
    print("No prediction files were modified.")


if __name__ == "__main__":
    main()
