"""Parse official race-level sectionals, kept outside the fixed win model."""
from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from horse_profile_runtime import JRA_VENUE_MAP

METHOD_VERSION = "jra-official-race-laps-v1"


class LapParseError(ValueError):
    pass


def validate_source_url(url: str, race_id: str, date8: str) -> None:
    parsed = urlparse(url)
    hosts = {"www.jra.go.jp", "jra.go.jp", "www.jra.jp", "jra.jp", "own.jra.jp"}
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.path != "/JRADB/accessS.html":
        raise LapParseError("not an official desktop JRA result URL")
    datetime.strptime(date8, "%Y%m%d")
    if not re.fullmatch(r"\d{12}", race_id) or race_id[4:6] not in JRA_VENUE_MAP:
        raise LapParseError("invalid JRA race ID")
    token = unquote(parse_qs(parsed.query).get("CNAME", [""])[0])
    expected = race_id[4:6] + race_id[:4] + race_id[6:] + date8
    if not re.fullmatch(r"pw01sde(?:01|10)" + re.escape(expected) + r"/[0-9A-Fa-f]{2}", token):
        raise LapParseError("result URL does not match requested race and date")


def segment_distances(distance: int, lap_count: int) -> list[int]:
    if distance <= 0 or distance % 100:
        raise LapParseError("unsupported distance")
    first = distance % 200 or 200
    segments = [first] + [200] * ((distance - first) // 200)
    if len(segments) != lap_count:
        raise LapParseError("lap count does not match race distance")
    return segments


def _time_seconds(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2})\.(\d)", value.strip())
    if not match or int(match[2]) >= 60:
        raise LapParseError("invalid winner time")
    return round(int(match[1]) * 60 + int(match[2]) + int(match[3]) / 10, 1)


def _decimal_times(value: str) -> list[float]:
    value = unicodedata.normalize("NFKC", value)
    parts = re.split(r"\s*[-−－–—ー]\s*", value.strip())
    if not parts or any(not re.fullmatch(r"\d{1,2}\.\d", part) for part in parts):
        raise LapParseError("invalid or missing official lap sequence")
    times = [float(part) for part in parts]
    if any(time <= 0 or time >= 60 for time in times):
        raise LapParseError("invalid lap duration")
    return times


def parse_official_laps(html: str, race_id: str, date8: str, source_url: str) -> dict:
    validate_source_url(source_url, race_id, date8)
    soup = BeautifulSoup(html, "html.parser")
    headings = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    heading = next((text for text in headings if "レース結果" in text and "年" in text), "")
    date = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", heading)
    meeting = re.search(r"(\d+)回\s*(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)\s*(\d+)日\s*(\d+)レース", heading)
    expected_venue = JRA_VENUE_MAP[race_id[4:6]]
    if not date or not meeting:
        raise LapParseError("race heading identity missing")
    found_date = f"{int(date[1]):04d}{int(date[2]):02d}{int(date[3]):02d}"
    identity = (int(meeting[1]), meeting[2], int(meeting[3]), int(meeting[4]))
    expected = (int(race_id[6:8]), expected_venue, int(race_id[8:10]), int(race_id[10:12]))
    if found_date != date8 or identity != expected:
        raise LapParseError("HTML heading does not match requested race")
    text = " ".join(soup.stripped_strings)
    course = re.search(r"コース\s*[:：]\s*([\d,]+)\s*メートル\s*[（(]([^）)]+)[）)]", text)
    if not course:
        raise LapParseError("course metadata missing")
    distance = int(course[1].replace(",", ""))
    detail = course[2]
    context = text[max(0, course.start()-350):course.end()]
    if "障害" in context or "芝→ダート" in detail:
        raise LapParseError("obstacle races are outside the lap sidecar scope")
    surface = "ダ" if "ダート" in detail else "芝" if "芝" in detail else None
    if not surface:
        raise LapParseError("surface metadata missing")
    lap_text = last_text = None
    for th in soup.find_all("th"):
        label = th.get_text("", strip=True)
        td = th.find_next_sibling("td")
        if td is None:
            continue
        if label == "ハロンタイム":
            lap_text = td.get_text(" ", strip=True)
        elif label == "上り":
            last_text = td.get_text(" ", strip=True)
    if lap_text is None:
        raise LapParseError("official lap table missing or result not final")
    lap_times = _decimal_times(lap_text)
    lengths = segment_distances(distance, len(lap_times))
    winner_time = None
    for tr in soup.find_all("tr"):
        place = tr.select_one("td.place")
        time = tr.select_one("td.time")
        if place is not None and time is not None and place.get_text(strip=True) == "1":
            winner_time = _time_seconds(time.get_text(strip=True))
            break
    if winner_time is None:
        raise LapParseError("final winner time missing")
    total = round(sum(lap_times), 1)
    if abs(total - winner_time) > 0.3 + 1e-9:
        raise LapParseError("lap total and winner time disagree")
    last3 = round(sum(lap_times[-3:]), 1) if len(lengths) >= 3 and sum(lengths[-3:]) == 600 else None
    official_last = re.search(r"3F\s+(\d{1,2}\.\d)", last_text or "")
    if official_last is None or last3 is None or abs(last3 - float(official_last[1])) > 0.3 + 1e-9:
        raise LapParseError("lap tail and official race 3F disagree")
    cumulative, elapsed, covered = [], 0.0, 0
    for length, time in zip(lengths, lap_times):
        covered += length
        elapsed = round(elapsed + time, 1)
        cumulative.append({"distanceM": covered, "seconds": elapsed})
    exact = {point["distanceM"]: point["seconds"] for point in cumulative}
    early = next((point for point in cumulative if point["distanceM"] >= 600), None)
    going = re.search(r"(?:芝|ダート)\s*(稍重|不良|良|重)", text)
    return {
        "raceID": race_id, "raceDate": datetime.strptime(date8, "%Y%m%d").strftime("%Y-%m-%d"),
        "venue": expected_venue, "raceNumber": int(race_id[10:12]),
        "surface": surface, "distanceM": distance, "courseDetail": detail,
        "going": going[1] if going else None, "sourceURL": source_url,
        "source": "JRA official race-level sectionals", "methodVersion": METHOD_VERSION,
        "lapDistancesM": lengths, "lapTimesSeconds": lap_times, "cumulativeTimes": cumulative,
        "winnerTimeSeconds": winner_time, "lapTotalSeconds": total,
        "raceLast600mSeconds": last3, "first600mSeconds": exact.get(600), "first1000mSeconds": exact.get(1000),
        "earlyRecordedDistanceM": early["distanceM"] if early else None,
        "earlyRecordedTimeSeconds": early["seconds"] if early else None,
        "validation": {"headingMatches": True, "distanceMatches": True,
                       "winnerTimeMatches": True, "raceLast600mMatches": True},
    }
