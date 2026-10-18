"""Verified JRA calendar inputs for completed-day history updates.

Race IDs come from the published program, including obstacles, never from
predictions or from an assumed twelve races. An unreadable calendar is an
error, not evidence that no meeting took place.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
import hashlib
import re
import time

from bs4 import BeautifulSoup
import requests

JST = timezone(timedelta(hours=9))
VENUES = {name: f"{i:02d}" for i, name in enumerate(
    ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"], 1)}


def target_date(explicit, run_created_at, event_name, now):
    if explicit:
        day = datetime.strptime(explicit, "%Y%m%d").date()
        if day.strftime("%Y%m%d") != explicit:
            raise ValueError("history target must be YYYYMMDD")
        return explicit
    if event_name in {"schedule", "workflow_dispatch"}:
        # created_at belongs to the original run, including retries after midnight.
        created = datetime.fromisoformat(run_created_at.replace("Z", "+00:00"))
        if created.tzinfo is None or created > now:
            raise ValueError("scheduled history run creation time is invalid")
        return created.astimezone(JST).strftime("%Y%m%d")
    return now.astimezone(JST).strftime("%Y%m%d")


def fetch(session, url, evidence):
    errors = []
    for attempt in range(1, 4):
        try:
            response = session.get(url, timeout=30)
            print("history-calendar HTTP:", response.status_code, "bytes:", len(response.content),
                  "attempt:", attempt, "url:", url, flush=True)
            evidence.append({"url": url, "attempt": attempt, "httpStatus": response.status_code,
                             "bytes": len(response.content),
                             "sha256": hashlib.sha256(response.content).hexdigest()})
            response.raise_for_status()
            if not response.content:
                raise ValueError("empty official calendar response")
            return response
        except (requests.RequestException, ValueError) as exc:
            errors.append(str(exc))
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"official calendar unavailable: {url}: {errors}")


def calendar_meetings(payload, date8):
    day = datetime.strptime(date8, "%Y%m%d").date()
    if (not isinstance(payload, list) or len(payload) != 1
            or int(payload[0]["month"]) != day.month):
        raise ValueError("official calendar month mismatch")
    rows = payload[0]["data"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("official calendar is empty")
    by_day = {}
    for row in rows:
        number = int(row["date"])
        if not 1 <= number <= calendar.monthrange(day.year, day.month)[1] or number in by_day:
            raise ValueError("official calendar has invalid or duplicate dates")
        info = row["info"]
        if not isinstance(info, list) or len(info) != 1 or not isinstance(info[0]["race"], list):
            raise ValueError("official calendar meeting schema is invalid")
        names = [item["name"] for item in info[0]["race"]]
        if any(not re.fullmatch(r"[1-9]\d*回(?:" + "|".join(VENUES) + r")", n) for n in names):
            raise ValueError("official calendar meeting name is invalid")
        if len(names) != len(set(names)):
            raise ValueError("duplicate official calendar meetings")
        by_day[number] = names
    # The monthly calendar omits inactive days. Require entries across the
    # month before accepting an omitted date as a non-meeting day.
    if not any(n <= 7 and names for n, names in by_day.items()) or not any(
            n >= 22 and names for n, names in by_day.items()):
        raise ValueError("official monthly calendar coverage is incomplete")
    return by_day.get(day.day, [])


def program_race_ids(text, date8, meetings):
    day = datetime.strptime(date8, "%Y%m%d").date()
    soup = BeautifulSoup(text, "html.parser")
    headings = [re.sub(r"\s+", "", h.get_text()) for h in soup.select("h1")]
    if not any(h.startswith(f"{day.year}年{day.month}月{day.day}日") and "競馬番組" in h for h in headings):
        raise ValueError("official program date mismatch")
    found, ids = [], []
    for table in soup.select("#program_list table"):
        caption = table.find("caption")
        match = re.fullmatch(r"(\d+)回(" + "|".join(VENUES) + r")(\d+)日",
                             re.sub(r"\s+", "", caption.get_text()) if caption else "")
        if not match:
            raise ValueError("official program meeting identity is missing")
        meeting, venue, meeting_day = match.groups()
        if not 1 <= int(meeting) <= 99 or not 1 <= int(meeting_day) <= 99:
            raise ValueError("official program meeting numbers are invalid")
        found.append(f"{int(meeting)}回{venue}")
        numbers = []
        for row in table.select("tbody tr"):
            number = row.select_one("th.num")
            match_number = re.fullmatch(r"(\d+)レース", re.sub(r"\s+", "", number.get_text()) if number else "")
            if not match_number or not row.select_one("td.name"):
                raise ValueError("official program race row is incomplete")
            numbers.append(int(match_number.group(1)))
        if not numbers or numbers != list(range(1, len(numbers) + 1)) or len(numbers) > 12:
            raise ValueError("official program race numbers are incomplete")
        ids.extend(f"{day.year}{VENUES[venue]}{int(meeting):02d}{int(meeting_day):02d}{n:02d}" for n in numbers)
    if sorted(found) != sorted(meetings) or len(ids) != len(set(ids)):
        raise ValueError("official program and monthly calendar disagree")
    return sorted(ids)


def fetch_day(session, date8):
    day = datetime.strptime(date8, "%Y%m%d").date()
    evidence = []
    month_url = f"https://www.jra.go.jp/keiba/common/calendar/json/{date8[:6]}.json"
    meetings = calendar_meetings(fetch(session, month_url, evidence).json(), date8)
    if not meetings:
        return [], {"method": "jra_official_calendar_v1", "targetDate": date8,
                    "status": "no_scheduled_meetings", "requests": evidence}
    program_url = f"https://www.jra.go.jp/keiba/calendar{day.year}/{day.year}/{day.month}/{date8[4:]}.html"
    response = fetch(session, program_url, evidence)
    ids = program_race_ids(response.content.decode("cp932", errors="strict"), date8, meetings)
    return ids, {"method": "jra_official_calendar_v1", "targetDate": date8,
                 "status": "scheduled_results_require_confirmation", "meetings": meetings,
                 "raceIDs": ids, "requests": evidence}
