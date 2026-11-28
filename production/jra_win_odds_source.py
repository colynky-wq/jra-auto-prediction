"""Experimental read-only JRA official HTML *interim* win-odds adapter.

Uses only public JRA navigation tokens observed in an entry page. No invented
CNAME, authentication, private endpoint or bypass. Not scheduled in production
until terms and live response accuracy have been verified. All parser failures
are fail closed, and the source clock is never replaced by HTTP receipt time.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup

from jra_entry_source import ACCESS_D, JRA_HOST, HEADERS, discover_entry_url, decode_jra_entry_html
from win_odds_freshness import JST, validate_win_odds_freshness
from win_odds_retry import WinOddsFetchError

ACCESS_O = JRA_HOST + "/JRADB/accessO.html"
VENUES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}
ACTION_PATTERN = re.compile(
    r"""doAction\(\s*['"](/JRADB/accessO\.html)['"]\s*,\s*['"]([^'"]+)['"]""",
    re.I,
)
INTERIM_LABEL = re.compile(r"^(\d{1,2})時(\d{2})分現在オッズ$")
RACE_LABEL = re.compile(
    r"^(\d{4})年(\d{1,2})月(\d{1,2})日（.曜）"
    r"(\d+)回(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)"
    r"(\d+)日\s*(\d+)レース$"
)
ODDS_NUMBER = re.compile(r"^\d+\.\d$")
TOKEN = re.compile(r"^[A-Za-z0-9_/.-]{1,160}$")


class JRAWinOddsError(WinOddsFetchError):
    """Official page has insufficient, ambiguous, or wrong-race data."""


class JRAAccessRestricted(RuntimeError):
    """Stop rather than retry when the official site refuses automated access."""


def _require_http_ok(response, step: str) -> None:
    status = response.status_code
    if status in (401, 403, 429, 451) or 300 <= status < 400:
        raise JRAAccessRestricted(f"jra_{step}_access_restricted_http_{status}")
    if status != 200:
        raise JRAWinOddsError(f"jra_{step}_http_{status}")


def discover_win_odds_cname(entry_html: str) -> str:
    """Read the official public odds link from an observed race entry page."""
    tokens = {m.group(2) for m in ACTION_PATTERN.finditer(entry_html)}
    if len(tokens) != 1:
        raise JRAWinOddsError("expected_one_observed_jra_win_odds_navigation_token")
    token = tokens.pop()
    if not TOKEN.fullmatch(token):
        raise JRAWinOddsError("invalid_jra_navigation_token")
    return token


def _identity(label: str, race_id: str, date8: str) -> None:
    m = RACE_LABEL.fullmatch(label)
    if not m:
        raise JRAWinOddsError("unrecognized_jra_odds_race_header")
    y, month, day, meeting, venue, meeting_day, race_no = m.groups()
    observed = y + next(code for code, name in VENUES.items() if name == venue)
    observed += f"{int(meeting):02d}{int(meeting_day):02d}{int(race_no):02d}"
    observed_date = f"{y}{int(month):02d}{int(day):02d}"
    if observed != race_id or observed_date != date8:
        raise JRAWinOddsError("jra_win_odds_race_identity_mismatch")


def parse_jra_win_odds(html: str, race_id: str, date8: str,
                       received_at: datetime) -> tuple[pd.DataFrame, dict]:
    """Parse an interim win-odds page, with its original displayed minute."""
    soup = BeautifulSoup(html, "html.parser")
    headings = soup.select("h1")
    labels = [node.get_text(" ", strip=True) for h in headings
              for node in h.select("span.opt")
              if "単勝・複勝オッズ" in h.get_text(" ", strip=True)]
    if len(labels) != 1 or "単勝・複勝オッズ（馬番順）" not in headings[
            next(i for i, h in enumerate(headings)
                 if "単勝・複勝オッズ" in h.get_text(" ", strip=True))].get_text(" ", strip=True):
        raise JRAWinOddsError("expected_jra_win_odds_number_order_header")
    _identity(labels[0], race_id, date8)

    times = [node.get_text(" ", strip=True)
             for node in soup.select("div.cell.time strong")]
    if len(times) != 1 or any("最終" in t or "確定" in t for t in times):
        raise JRAWinOddsError("jra_odds_interim_time_missing_or_final")
    m = INTERIM_LABEL.fullmatch(times[0])
    if not m:
        raise JRAWinOddsError("ambiguous_jra_odds_update_time")
    hour, minute = map(int, m.groups())
    if hour > 23 or minute > 59:
        raise JRAWinOddsError("invalid_jra_odds_update_clock")
    if received_at.utcoffset() is None:
        raise JRAWinOddsError("jra_odds_receipt_timestamp_naive")
    try:
        source_at = datetime.strptime(date8 + f" {hour:02d}:{minute:02d}",
                                      "%Y%m%d %H:%M").replace(tzinfo=JST)
    except ValueError as exc:
        raise JRAWinOddsError("invalid_jra_odds_calendar_date") from exc

    tables = soup.select("table.basic.narrow-xy.tanpuku")
    if len(tables) != 1:
        raise JRAWinOddsError("expected_one_jra_tanpuku_table")
    rows = []
    for tr in tables[0].select("tr"):
        num = tr.select_one("td.num")
        win = tr.select_one("td.odds_tan")
        if num is None and win is None:
            continue
        if num is None or win is None:
            raise JRAWinOddsError("jra_odds_incomplete_row")
        n = num.get_text(" ", strip=True)
        value = win.get_text(" ", strip=True)
        if not re.fullmatch(r"\d{1,2}", n) or not 1 <= int(n) <= 18:
            raise JRAWinOddsError("invalid_jra_horse_number")
        if not ODDS_NUMBER.fullmatch(value) or float(value) < 1:
            raise JRAWinOddsError("invalid_or_unavailable_jra_win_odds")
        rows.append({"馬番": int(n), "単勝オッズ": float(value)})
    if (len(rows) < 2 or len(rows) != len({row["馬番"] for row in rows})):
        raise JRAWinOddsError("duplicate_or_missing_jra_win_odds")
    # The adapter translates the displayed source clock to a full timestamp;
    # sourceField retains the actual JRA HTML provenance in the validator.
    payload = {
        "sourceProvider": "jra_official_web",
        "sourceDisplayLabel": times[0],
        "sourceURL": ACCESS_O,
        "sourceRaceDateJST": date8,
        "sourceRaceID": race_id,
        "sourceTimePrecision": "minute",
        "sourcePageSha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "data": {"official_datetime": source_at.isoformat()},
    }
    evidence = validate_win_odds_freshness(payload, received_at)
    return (pd.DataFrame(rows).sort_values("馬番").reset_index(drop=True),
            {**payload, "preflightFreshnessEvidence": evidence})


def fetch_jra_win_odds(race_id: str, date8: str, *,
                       session: requests.Session | None = None,
                       clock=lambda: datetime.now(JST),
                       navigation_cache: dict[tuple[str, str], tuple[str, str]] | None = None,
                       ) -> tuple[pd.DataFrame, dict]:
    """Read one JRA HTML snapshot; cache only observed navigation within a run.

    The token is obtained from a real public entry link; it is never guessed.
    Caching avoids re-crawling the official navigation tree on each freshness
    retry. No login, redirect following, access-denial retry or bypass.
    """
    if not re.fullmatch(r"\d{12}", race_id) or not re.fullmatch(r"\d{8}", date8):
        raise JRAWinOddsError("invalid_jra_race_identity")
    session = session or requests.Session()
    session.headers.update(HEADERS)
    cache_key = (date8, race_id)
    cached = (navigation_cache.get(cache_key)
              if navigation_cache is not None else None)
    if cached is not None:
        entry_url, token = cached
        if (not entry_url.startswith(ACCESS_D + "?")
                or not TOKEN.fullmatch(token)):
            raise JRAWinOddsError("invalid_cached_jra_navigation")
    else:
        entry_url = discover_entry_url(race_id, date8, session,
                                       abort_on_restricted=True)
        if not entry_url or not entry_url.startswith(ACCESS_D + "?"):
            raise JRAWinOddsError("jra_entry_navigation_unavailable")
        try:
            response = session.get(entry_url, timeout=20, allow_redirects=False)
        except requests.exceptions.RequestException as exc:
            raise JRAWinOddsError("jra_entry_transport_failure") from exc
        _require_http_ok(response, "entry")
        try:
            raw_entry = decode_jra_entry_html(response.content)
        except UnicodeDecodeError as exc:
            raise JRAWinOddsError("jra_entry_html_decode_failed") from exc
        if any(message in raw_entry for message in (
                "アクセスが集中", "ご指定のページに接続できません")):
            raise JRAAccessRestricted("jra_entry_access_restricted_page")
        token = discover_win_odds_cname(raw_entry)
        if navigation_cache is not None:
            navigation_cache[cache_key] = (entry_url, token)

    try:
        response = session.post(ACCESS_O, data={"cname": token},
                                headers={"Referer": entry_url},
                                timeout=20, allow_redirects=False)
    except requests.exceptions.RequestException as exc:
        raise JRAWinOddsError("jra_odds_transport_failure") from exc
    received = clock()
    _require_http_ok(response, "odds")
    try:
        html = decode_jra_entry_html(response.content)
    except UnicodeDecodeError as exc:
        raise JRAWinOddsError("jra_odds_html_decode_failed") from exc
    if any(message in html for message in (
            "アクセスが集中", "ご指定のページに接続できません")):
        raise JRAAccessRestricted("jra_odds_access_restricted_page")
    return parse_jra_win_odds(html, race_id, date8, received)
