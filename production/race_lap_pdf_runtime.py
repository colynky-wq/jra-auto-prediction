"""Read the two-column JRA official reports, validating every flat race."""
from __future__ import annotations

from datetime import datetime
import io
import math
import re
import unicodedata
from urllib.parse import urlparse

import pdfplumber

from horse_profile_runtime import JRA_VENUE_MAP
from race_lap_runtime import LapParseError, segment_distances

METHOD_VERSION = "jra-official-report-laps-v1"
VENUE_SLUGS = dict(zip(JRA_VENUE_MAP, ["sapporo", "hakodate", "fukushima", "niigata", "tokyo", "nakayama", "chukyo", "kyoto", "hanshin", "kokura"]))
NUMERIC_FONTS = {"FutoMinA101Pr6N-Bold", "RyuminPr6N-Light", "FutoGoB101Pr6N-Bold", "FutoGoB101Pr5-Bold"}


def validate_report_url(url: str, race_id: str, date8: str) -> None:
    p = urlparse(url)
    datetime.strptime(date8, "%Y%m%d")
    if not re.fullmatch(r"\d{12}", race_id) or race_id[4:6] not in VENUE_SLUGS:
        raise LapParseError("invalid JRA race ID")
    year = race_id[:4]
    expected = f"/datafile/seiseki/report/{year}/{year}-{int(race_id[6:8])}{VENUE_SLUGS[race_id[4:6]]}{int(race_id[8:10])}.pdf"
    if (p.scheme != "https" or p.hostname != "www.jra.go.jp" or p.path != expected
            or p.query or p.fragment or date8[:4] != year):
        raise LapParseError("report URL does not match race identity")


def restore_numeric_glyph(text: str, font: str) -> str:
    """JRA report fonts use these CID glyphs for 0..9; never map other fonts.

    Checked against rendered 2024/2025 reports and independent HTML results.
    Unknown glyphs stay unknown and cause required numeric fields to fail.
    """
    match = re.fullmatch(r"\(cid:(\d+)\)", text)
    if match and 9872 <= int(match[1]) <= 9881:
        if font.split("+")[-1] not in NUMERIC_FONTS:
            raise LapParseError("unsupported numeric PDF font")
        return str(int(match[1]) - 9872)
    return text


def parse_report_half(text: str, source_url: str, expected: dict, page: int, column: int) -> dict:
    text = unicodedata.normalize("NFKC", text)
    lines = text.splitlines()
    head = next((line for line in lines if "競走" in line and "月" in line and "年" in line), "")
    compact = re.sub(r"\s+", "", head)
    # Five-digit report serial precedes the month; do not join it to the date.
    compact = re.sub(r"^\d{5}", "", compact)
    match = re.search(r"(\d{1,2})月(\d{1,2})日.*?(稍重|不良|良|重)\((\d{4})年(\d+)(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)\)第(\d+)日第(\d+)競走", compact)
    if not match:
        raise LapParseError("report race heading missing")
    month, day, going, year, meeting, venue, meeting_day, race_no = match.groups()
    date8 = f"{int(year):04d}{int(month):02d}{int(day):02d}"
    venue_code = next(key for key, value in JRA_VENUE_MAP.items() if value == venue)
    race_id = f"{int(year):04d}{venue_code}{int(meeting):02d}{int(meeting_day):02d}{int(race_no):02d}"
    validate_report_url(source_url, race_id, date8)
    if race_id != expected["raceID"] or date8 != expected["date8"]:
        raise LapParseError("PDF heading disagrees with saved history")
    distance_match = re.search(r"([\d,]+)\(cid:\d+\)$", compact)
    if not distance_match:
        raise LapParseError("report distance missing")
    distance = int(distance_match[1].replace(",", ""))
    start = lines.index(head)
    course_line = " ".join(lines[start:start+3])
    course = re.search(r"\(([^()]*(?:芝|ダート)[^()]*)\)", course_line)
    if not course or "障害" in course_line:
        raise LapParseError("flat course metadata missing")
    detail = re.sub(r"\s+", "", course[1])
    surface = "ダ" if "ダート" in detail else "芝"
    if distance != expected["distanceM"] or surface != expected["surface"]:
        raise LapParseError("PDF course disagrees with saved history")
    lap_line = next((line for line in lines if line.startswith("ハロンタイム")), "")
    sequence = re.sub(r"\s+", "", lap_line.removeprefix("ハロンタイム"))
    pieces = re.split(r"[―－−–—-]", sequence)
    if not pieces or any(not re.fullmatch(r"\d{1,2}\.\d", part) for part in pieces):
        raise LapParseError("official report lap sequence missing or undecodable")
    laps = list(map(float, pieces))
    if any(not 0 < time < 60 for time in laps):
        raise LapParseError("invalid report lap time")
    lengths = segment_distances(distance, len(laps))
    last_line = next((line for line in lines if "上り" in line), "")
    last = re.search(r"3F\s*(\d{1,2}\.\d)", last_line)
    if last is None or abs(sum(laps[-3:])-float(last[1])) > .300000001:
        raise LapParseError("report race 3F and lap tail disagree")
    # The two record clocks precede the finish table. Exclude that line,
    # then take the first finish clock, rather than a course record.
    if "中央レコード" not in text:
        raise LapParseError("finish table boundary missing")
    finish_table = text.split("中央レコード", 1)[1].split("\n", 1)[1]
    clock_pattern = r"(?:(\d):)?(\d{2})\.(\d)" if distance <= 1000 else r"(\d):(\d{2})\.(\d)"
    clock = re.search(clock_pattern, finish_table)
    if clock is None or int(clock[2]) >= 60:
        raise LapParseError("report winner clock missing")
    winner = int(clock[1] or 0)*60 + int(clock[2]) + int(clock[3])/10
    history_winner = expected["winnerTimeSeconds"]
    if not math.isfinite(history_winner) or abs(winner-history_winner) > .050000001:
        raise LapParseError("report winner clock and saved history disagree")
    total = round(sum(laps), 1)
    if abs(total-winner) > .300000001:
        raise LapParseError("report lap total and winner clock disagree")
    cumulative, covered, elapsed = [], 0, 0.0
    for length, time in zip(lengths, laps):
        covered += length
        elapsed = round(elapsed+time, 1)
        cumulative.append({"distanceM": covered, "seconds": elapsed})
    exact = {point["distanceM"]: point["seconds"] for point in cumulative}
    early = next(point for point in cumulative if point["distanceM"] >= 600)
    return {"raceID": race_id, "raceDate": datetime.strptime(date8, "%Y%m%d").date().isoformat(),
            "venue": venue, "raceNumber": int(race_no), "surface": surface, "distanceM": distance,
            "courseDetail": detail, "going": going, "sourceURL": source_url,
            "source": "JRA official race report PDF", "sourcePage": page, "sourceColumn": column,
            "methodVersion": METHOD_VERSION, "lapDistancesM": lengths, "lapTimesSeconds": laps,
            "cumulativeTimes": cumulative, "winnerTimeSeconds": round(winner, 1), "lapTotalSeconds": total,
            "raceLast600mSeconds": round(sum(laps[-3:]), 1), "first600mSeconds": exact.get(600),
            "first1000mSeconds": exact.get(1000), "earlyRecordedDistanceM": early["distanceM"],
            "earlyRecordedTimeSeconds": early["seconds"], "validation": {
                "headingMatches": True, "distanceMatches": True, "winnerTimeMatches": True,
                "raceLast600mMatches": True, "historyWinnerTimeMatches": True}}


def parse_official_report(data: bytes, source_url: str, expected: list[dict]) -> tuple[list[dict], list[dict]]:
    wanted = {int(row["raceID"][-2:]): row for row in expected}
    records, failures, seen = [], [], set()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            for char in page.chars:
                char["text"] = restore_numeric_glyph(char["text"], char["fontname"])
            for column, (left, right) in enumerate([(0, page.width/2), (page.width/2, page.width)], 1):
                text = page.crop((left, 0, right, page.height)).extract_text() or ""
                heading = re.search(r"第\s*(\d+)\s*競走", unicodedata.normalize("NFKC", text))
                if not heading or int(heading[1]) not in wanted:
                    continue
                number = int(heading[1])
                row = wanted[number]
                if number in seen:
                    raise LapParseError("duplicate report race heading")
                seen.add(number)
                try:
                    records.append(parse_report_half(text, source_url, row, page_no, column))
                except (ValueError, IndexError) as exc:
                    failures.append({"raceID": row["raceID"], "reason": str(exc)})
            page.close()
    for number in sorted(wanted.keys()-seen):
        failures.append({"raceID": wanted[number]["raceID"], "reason": "report race section missing"})
    return records, failures
