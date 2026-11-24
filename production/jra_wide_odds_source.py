"""Read a JRA Wide odds page for research using its public race navigation."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from itertools import combinations
import hashlib
import json
import re
import sys

from bs4 import BeautifulSoup
import requests

from jra_entry_source import (
    ACCESS_D, HEADERS, JRAEntryAccessRestricted, _race_id_from_url, discover_entry_url,
)
from jra_win_odds_source import (
    ACCESS_O, ACTION_PATTERN, INTERIM_LABEL, ODDS_NUMBER, TOKEN,
    JRAAccessRestricted, JRAWinOddsError, _identity, _require_http_ok,
)
from win_odds_freshness import JST, validate_win_odds_freshness


class JRAWideOddsError(ValueError):
    """The official page is incomplete, ambiguous or outside the capture policy."""


def checked_page(response, step):
    _require_http_ok(response, step)
    try:
        html = response.content.decode(response.apparent_encoding or "cp932")
    except (LookupError, UnicodeError) as exc:
        raise JRAWideOddsError("JRA page encoding not recognized") from exc
    if any(msg in html for msg in ("アクセスが集中", "ご指定のページに接続できません")):
        raise JRAAccessRestricted(f"jra_{step}_access_restricted_page")
    return html


def verify_header(soup, title, race_id, date8):
    headers = [h for h in soup.select("h1") if h.get_text(strip=True).startswith(title)]
    if len(headers) != 1 or len(labels := headers[0].select("span.opt")) != 1:
        raise JRAWideOddsError(f"expected exactly one {title} race header")
    _identity(labels[0].get_text(" ", strip=True), race_id, date8)


def link_token(soup, selector, label):
    matches = [ACTION_PATTERN.search(a["onclick"]) for a in soup.select(selector)
               if a.get_text(strip=True) == label]
    if len(matches) != 1 or matches[0] is None or not TOKEN.fullmatch(matches[0][2]):
        raise JRAWideOddsError(f"expected exactly one official race link for {label}")
    return matches[0][2]


def horse_number(node):
    value = node.get_text(strip=True) if node is not None else ""
    if not re.fullmatch(r"[0-9]{1,2}", value) or not 1 <= int(value) <= 18:
        raise JRAWideOddsError("invalid or missing horse number")
    return int(value)


def entry_numbers(soup):
    tables = [table for table in soup.select("table") if table.select_one("td.num + td.horse")]
    if len(tables) != 1:
        raise JRAWideOddsError("expected exactly one entry table")
    rows = tables[0].select("td.num + td.horse")
    declared = [horse_number(cell.find_previous_sibling("td")) for cell in rows]
    if len(set(declared)) != len(declared):
        raise JRAWideOddsError("duplicate entrant in JRA entry table")
    active = [number for number, cell in zip(declared, rows, strict=True)
              if not re.search(r"(?:出走取消|競走除外|^取消$|^除外$)", cell.get_text(" ", strip=True))
              and not {"cancel", "cancelled", "exclude", "excluded"}.intersection(cell.parent.get("class", []))]
    return active


def wide_pairs(soup, expected_numbers):
    numbers = list(expected_numbers)
    if (len(numbers) < 3 or len(numbers) != len(set(numbers))
            or any(type(n) is not int or not 1 <= n <= 18 for n in numbers)):
        raise JRAWideOddsError("invalid active entrant roster")
    expected = set(combinations(sorted(numbers), 2))
    by_pair = {}
    captions = set()
    for table in soup.select("table.basic.narrow-xy.wide"):
        a = horse_number(table.find("caption", recursive=False))
        if a in captions:
            raise JRAWideOddsError("duplicate Wide table caption")
        captions.add(a)
        for row in table.select("tbody tr"):
            b = horse_number(row.select_one('th[scope="row"]'))
            cell = row.select_one("td.odds")
            # A displayed void pair is allowed only outside the active roster.
            key = (a, b)
            if key not in expected and (a not in numbers or b not in numbers):
                text = cell.get_text(strip=True) if cell is not None else ""
                if text in {"", "-", "--", "―", "取消", "除外", "出走取消", "競走除外"}:
                    continue
            if a >= b or key not in expected or key in by_pair or cell is None:
                raise JRAWideOddsError("unexpected or duplicate Wide pair")
            lo, hi = (cell.select(f"span.{klass}") for klass in ("min", "max"))
            if len(lo) != 1 or len(hi) != 1:
                raise JRAWideOddsError("missing or ambiguous Wide odds bounds")
            low, high = lo[0].get_text(strip=True), hi[0].get_text(strip=True)
            if not ODDS_NUMBER.fullmatch(low) or not ODDS_NUMBER.fullmatch(high):
                raise JRAWideOddsError("unavailable Wide odds bound")
            low, high = float(low), float(high)
            if not 1 <= low <= high:
                raise JRAWideOddsError("reversed or invalid Wide odds bounds")
            by_pair[key] = {"number1": a, "number2": b, "oddsLow": low, "oddsHigh": high}
    if set(by_pair) != expected:
        raise JRAWideOddsError("Wide odds do not cover the complete active entry roster")
    return [by_pair[key] for key in sorted(by_pair)]


def parse_wide_page(html, race_id, date8, expected_numbers, received_at, *, inspect_final=False):
    soup = BeautifulSoup(html, "html.parser")
    verify_header(soup, "ワイドオッズ（馬番順）", race_id, date8)
    scheduled = [cell.get_text(strip=True).removeprefix("発走時刻：")
                 for cell in soup.select("div.date_line div.cell.time")
                 if cell.get_text(strip=True).startswith("発走時刻：")]
    updates = [cell.get_text(strip=True) for cell in soup.select("div.refresh_line div.cell.time")]
    if len(scheduled) != 1 or not (match := re.fullmatch(r"([0-9]{1,2})時([0-9]{2})分", scheduled[0])):
        raise JRAWideOddsError("missing or ambiguous scheduled post time")
    if len(updates) != 1:
        raise JRAWideOddsError("missing or ambiguous Wide source time label")
    if received_at.utcoffset() is None:
        raise JRAWideOddsError("receipt timestamp must include a timezone")
    received = received_at.astimezone(JST)
    day = datetime.strptime(date8, "%Y%m%d").date()
    post = datetime(day.year, day.month, day.day, int(match[1]), int(match[2]), tzinfo=JST)
    display = updates[0]
    metadata = {
        "schemaVersion": 1,
        "researchOnly": True,
        "sourceProvider": "jra_official_web",
        "sourceURL": ACCESS_O,
        "sourceDisplayLabel": display,
        "sourcePageSha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "sourceHashEncoding": "decoded UTF-8 response text",
        "sourceRaceID": race_id,
        "sourceRaceDateJST": date8,
        "raceID": race_id,
        "raceDate": day.isoformat(),
        "scheduledPostJST": post.isoformat(),
        "receivedAtJST": received.isoformat(),
    }
    if display == "最終オッズ" and inspect_final:
        metadata.update(status="final_inspection", freshnessEvidence=None)
    else:
        update = INTERIM_LABEL.fullmatch(display)
        if update is None:
            raise JRAWideOddsError("explicit interim update time required; final odds cannot qualify")
        if not timedelta(minutes=8) <= post - received <= timedelta(minutes=12):
            raise JRAWideOddsError("Wide receipt is outside the T-12 to T-8 research window")
        as_of = post.replace(hour=int(update[1]), minute=int(update[2]))
        source = {
            **metadata, "sourceTimePrecision": "minute",
            "data": {"official_datetime": as_of.isoformat()},
        }
        evidence = validate_win_odds_freshness(source, received)
        metadata.update(status="verified_interim", freshnessEvidence=evidence)

    pairs = wide_pairs(soup, expected_numbers)
    return {**metadata, "pairCount": len(pairs), "horseNumbers": sorted(expected_numbers), "pairs": pairs}


def fetch_jra_wide_odds(race_id, date8, *, entry_url=None, session=None,
                        clock=lambda: datetime.now(JST), inspect_final=False):
    """Read the entry page, its race-specific odds page and the linked Wide tab."""
    if not re.fullmatch(r"[0-9]{12}", race_id) or not re.fullmatch(r"[0-9]{8}", date8):
        raise JRAWideOddsError("invalid JRA race identity")
    if session is None:
        with requests.Session() as client:
            return fetch_jra_wide_odds(race_id, date8, entry_url=entry_url, session=client,
                                      clock=clock, inspect_final=inspect_final)
    session.headers.update(HEADERS)
    if entry_url is None:
        entry_url = discover_entry_url(race_id, date8, session, abort_on_restricted=True)
    if (not entry_url or not entry_url.startswith(ACCESS_D + "?")
            or _race_id_from_url(entry_url, date8) != race_id):
        raise JRAWideOddsError("observed JRA entry URL does not match race and date")
    entry = checked_page(session.get(entry_url, timeout=20, allow_redirects=False), "entry")
    soup = BeautifulSoup(entry, "html.parser")
    verify_header(soup, "出馬表", race_id, date8)
    numbers = entry_numbers(soup)
    token = link_token(soup, "li.odds a[onclick]", "オッズ")
    odds_response = session.post(ACCESS_O, data={"cname": token}, headers={"Referer": entry_url},
                                 timeout=20, allow_redirects=False)
    soup = BeautifulSoup(checked_page(odds_response, "odds"), "html.parser")
    verify_header(soup, "単勝・複勝オッズ（馬番順）", race_id, date8)
    token = link_token(soup, "ul.nav.pills a[onclick]", "ワイド")
    wide_response = session.post(ACCESS_O, data={"cname": token}, headers={"Referer": ACCESS_O},
                                 timeout=20, allow_redirects=False)
    received_at = clock()
    html = checked_page(wide_response, "wide_odds")
    snapshot = parse_wide_page(html, race_id, date8, numbers, received_at, inspect_final=inspect_final)
    return {
        **snapshot, "sourceEntryURL": entry_url,
        "sourceEntryPageSha256": hashlib.sha256(entry.encode("utf-8")).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    parser.add_argument("--entry-url")
    parser.add_argument("--inspect-final", action="store_true", help="permit a rejected-as-live final page diagnostic")
    args = parser.parse_args()
    try:
        with redirect_stdout(sys.stderr):
            snapshot = fetch_jra_wide_odds(args.race_id, args.date, entry_url=args.entry_url,
                                          inspect_final=args.inspect_final)
    except (ValueError, requests.RequestException, JRAWinOddsError, JRAAccessRestricted,
            JRAEntryAccessRestricted) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
