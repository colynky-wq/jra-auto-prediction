# ============================================================
# KeibaAI - forward result aggregation launcher
#
# Purpose
# - Keep aggregate_forward_results.py unchanged.
# - Replace only the race-result fetcher.
# - Use JRA official result pages for finish positions / popularity.
# - Use the already-proven netkeiba win-odds API for final win odds.
#
# This avoids relying on HTML result tables from race.netkeiba.com or
# db.netkeiba.com, which can return bot/simplified pages to GitHub runners.
# ============================================================

from __future__ import annotations

import html as html_lib
import io
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

import aggregate_forward_results as agg
import jra_entry_source as entry_source


JRA_HOST = "https://www.jra.go.jp"
ACCESS_S = JRA_HOST + "/JRADB/accessS.html"

# Historical seeds used only to bootstrap the already-completed 2026-10-11
# forward report. From one routed result page we crawl the same-day JRA links.
KNOWN_RESULT_SEEDS = {
    "20261011": [
        ACCESS_S
        + "?CNAME="
        + "pw01sde0105202604040520261011%2F0C",
        ACCESS_S
        + "?CNAME="
        + "pw01sde0108202604040520261011%2FEA",
    ],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

_RESULT_URL_MAP: dict[str, str] | None = None


# ============================================================
# 1. JRA routed-result URL discovery
# ============================================================


def _get_cname(url: str) -> str:
    try:
        q = parse_qs(urlparse(url).query)
        return unquote(q.get("CNAME", [""])[0] or "")
    except Exception:
        return ""


def _has_route_suffix(url: str) -> bool:
    return bool(re.search(r"/[0-9A-Za-z]{2}$", _get_cname(url)))


def _race_id_from_result_url(url: str, date8: str) -> str | None:
    cname = _get_cname(url)
    if not cname:
        return None

    m = re.search(
        r"pw01sde01"
        r"(\d{2})"   # venue
        r"(\d{4})"   # year
        r"(\d{2})"   # meeting
        r"(\d{2})"   # day
        r"(\d{2})"   # race no
        r"(\d{8})",  # calendar date
        cname,
    )
    if not m:
        return None

    venue, year, meeting, day, race_no, found_date = m.groups()
    if found_date != date8:
        return None

    return year + venue + meeting + day + race_no


def _extract_result_urls(page_url: str, text: str) -> list[str]:
    soup = BeautifulSoup(text, "html.parser")
    urls = []

    for a in soup.find_all("a", href=True):
        href = str(a.get("href", ""))
        if "accessS.html" not in href or "pw01sde01" not in href:
            continue
        urls.append(urljoin(page_url, href))

    raw = html_lib.unescape(text)
    for m in re.findall(
        r'[^"\']*accessS\.html\?CNAME=pw01sde01[^"\']+',
        raw,
    ):
        value = m.strip()
        if value.startswith("http"):
            urls.append(value)
        else:
            pos = value.find("accessS.html")
            if pos >= 0:
                urls.append(urljoin(ACCESS_S, value[pos:]))

    return list(dict.fromkeys(urls))


def _prefer_routed(existing: str | None, candidate: str) -> str:
    if not existing:
        return candidate
    if _has_route_suffix(candidate) and not _has_route_suffix(existing):
        return candidate
    return existing


def _decode_response(response) -> str:
    encodings = [
        response.encoding,
        response.apparent_encoding,
        "utf-8",
        "cp932",
        "shift_jis",
    ]

    best = None
    best_score = -1

    for enc in encodings:
        if not enc:
            continue
        try:
            text = response.content.decode(enc, errors="replace")
        except Exception:
            continue
        score = sum(text.count(token) for token in ["着順", "馬番", "馬名", "レース結果"])
        if score > best_score:
            best = text
            best_score = score

    if best is None:
        return response.text
    return best


def _result_seeds_from_entry_pages(date8: str, target_ids: list[str]) -> list[str]:
    """Try to discover result-page links from routed JRA entry pages.

    This is the generic path for future dates.  The live-monitor entry source
    already knows how to enter the JRA routed navigation tree.  A routed entry
    page contains menu/navigation links to accessS result pages.
    """

    seeds = []
    representatives = {}
    for rid in target_ids:
        representatives.setdefault(rid[4:6], rid)

    for rid in representatives.values():
        try:
            entry_url = entry_source.discover_entry_url(rid, date8, SESSION)
            if not entry_url:
                continue

            r = SESSION.get(entry_url, timeout=20)
            print(
                "result seed via entry HTTP:",
                r.status_code,
                "bytes:",
                len(r.content),
                "race:",
                rid,
            )
            if r.status_code != 200:
                continue

            text = _decode_response(r)
            for u in _extract_result_urls(entry_url, text):
                if _race_id_from_result_url(u, date8):
                    seeds.append(u)
        except Exception as exc:
            print("result seed via entry warning:", rid, repr(exc))

    return list(dict.fromkeys(seeds))


def _discover_result_url_map(date8: str, target_ids: list[str]) -> dict[str, str]:
    target_set = set(target_ids)
    queue = list(KNOWN_RESULT_SEEDS.get(date8, []))

    dynamic = _result_seeds_from_entry_pages(date8, target_ids)
    for u in dynamic:
        if u not in queue:
            queue.append(u)

    if not queue:
        raise RuntimeError(
            f"no routed JRA result seed could be discovered for {date8}"
        )

    print("JRA result discovery seeds:", len(queue), "date:", date8)

    visited = set()
    url_map: dict[str, str] = {}
    max_pages = 80

    while queue and len(visited) < max_pages:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)

        try:
            r = SESSION.get(page_url, timeout=20)
            print(
                "JRA result discovery HTTP:",
                r.status_code,
                "bytes:",
                len(r.content),
                "routed:",
                _has_route_suffix(page_url),
            )
            if r.status_code != 200:
                continue

            text = _decode_response(r)
            found = [page_url] + _extract_result_urls(page_url, text)

            for u in found:
                rid = _race_id_from_result_url(u, date8)
                if rid:
                    url_map[rid] = _prefer_routed(url_map.get(rid), u)

            if target_set.issubset(
                {rid for rid, u in url_map.items() if _has_route_suffix(u)}
            ):
                break

            for u in found:
                rid = _race_id_from_result_url(u, date8)
                if (
                    rid
                    and _has_route_suffix(u)
                    and u not in visited
                    and u not in queue
                ):
                    queue.append(u)

        except Exception as exc:
            print("JRA result discovery warning:", repr(exc))

    routed = {
        rid: url
        for rid, url in url_map.items()
        if _has_route_suffix(url)
    }

    print(
        "JRA routed result URLs discovered:",
        len(routed),
        "/ targets:",
        len(target_set),
    )

    missing = sorted(target_set - set(routed))
    if missing:
        print("JRA result URLs still missing:", missing)

    return routed


def _ensure_result_url_map() -> dict[str, str]:
    global _RESULT_URL_MAP
    if _RESULT_URL_MAP is not None:
        return _RESULT_URL_MAP

    predictions = agg.load_predictions()
    target_ids = sorted(
        {
            agg.normalize_race_id(r.get("raceID", ""))
            for r in predictions
            if re.fullmatch(
                r"\d{12}",
                agg.normalize_race_id(r.get("raceID", "")),
            )
        }
    )

    _RESULT_URL_MAP = _discover_result_url_map(
        agg.TARGET_DATE8,
        target_ids,
    )
    return _RESULT_URL_MAP


# ============================================================
# 2. JRA result-table parser
# ============================================================


def _normalize_header(value) -> str:
    return re.sub(r"\s+", "", str(value)).replace("\u3000", "").strip()


def _find_col(columns, tokens):
    for c in columns:
        n = _normalize_header(c)
        if any(token in n for token in tokens):
            return c
    return None


def _parse_jra_result_table(text: str, race_id: str) -> pd.DataFrame:
    try:
        tables = pd.read_html(io.StringIO(text))
    except Exception as exc:
        raise RuntimeError(f"JRA result table parse failed: {race_id}: {exc}") from exc

    result = None

    for raw in tables:
        t = agg.flatten_columns(raw)
        cols = list(t.columns)
        if (
            _find_col(cols, ("着順",)) is not None
            and _find_col(cols, ("馬番",)) is not None
            and _find_col(cols, ("馬名",)) is not None
        ):
            result = t
            break

    if result is None:
        headers = []
        for raw in tables[:10]:
            try:
                headers.append(list(agg.flatten_columns(raw).columns))
            except Exception:
                pass
        raise RuntimeError(
            f"JRA result table not found: {race_id}; headers={headers[:5]}"
        )

    finish_col = _find_col(result.columns, ("着順",))
    no_col = _find_col(result.columns, ("馬番",))
    name_col = _find_col(result.columns, ("馬名",))
    pop_col = _find_col(result.columns, ("単勝人気", "人気"))

    rows = []

    for _, row in result.iterrows():
        no = pd.to_numeric(pd.Series([row[no_col]]), errors="coerce").iloc[0]
        if not np.isfinite(no):
            continue

        horse_no = int(no)
        if horse_no < 1 or horse_no > 18:
            continue

        finish, status = agg.parse_finish(row[finish_col])
        popularity = np.nan
        if pop_col is not None:
            popularity = pd.to_numeric(
                pd.Series([row[pop_col]]),
                errors="coerce",
            ).iloc[0]

        rows.append(
            {
                "raceID": race_id,
                "number": horse_no,
                "resultHorseName": str(row[name_col]).strip(),
                "finish": finish,
                "resultStatus": status,
                "finalOdds": np.nan,
                "finalPopularity": popularity,
            }
        )

    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError(f"JRA result rows = 0: {race_id}")

    return (
        d.sort_values("number")
        .drop_duplicates("number", keep="last")
        .reset_index(drop=True)
    )


# ============================================================
# 3. Final win odds from the proven odds API
# ============================================================


def _fetch_final_win_odds(race_id: str) -> dict[int, float]:
    url = "https://race.netkeiba.com/api/api_get_jra_odds.html"
    referer = f"https://race.netkeiba.com/odds/index.html?race_id={race_id}"

    try:
        r = SESSION.get(
            url,
            params={
                "race_id": race_id,
                "type": 1,
                "action": "init",
                "compress": 0,
            },
            headers={**HEADERS, "Referer": referer},
            timeout=30,
        )
        print(
            "final-odds HTTP:",
            r.status_code,
            "bytes:",
            len(r.content),
            "race:",
            race_id,
        )
        if r.status_code != 200:
            return {}

        payload = r.json()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        odds_root = data.get("odds") if isinstance(data.get("odds"), dict) else {}
        win = odds_root.get("1") if isinstance(odds_root.get("1"), dict) else {}

        result = {}
        for k, v in win.items():
            if not isinstance(v, list) or not v:
                continue
            try:
                no = int(k)
                odd = float(v[0])
            except Exception:
                continue
            if np.isfinite(odd) and odd > 0:
                result[no] = odd

        print("final win odds parsed:", len(result), "race:", race_id)
        return result

    except Exception as exc:
        print("final odds warning:", race_id, repr(exc))
        return {}


# ============================================================
# 4. Hook used by aggregate_forward_results.py
# ============================================================


def fetch_result_table_jra(race_id: str) -> pd.DataFrame:
    race_id = str(race_id).zfill(12)
    url_map = _ensure_result_url_map()
    url = url_map.get(race_id)

    if not url:
        raise RuntimeError(f"JRA routed result URL not found: {race_id}")

    r = SESSION.get(url, timeout=30)
    print(
        "JRA result HTTP:",
        r.status_code,
        "bytes:",
        len(r.content),
        "race:",
        race_id,
        "url:",
        url,
    )
    if r.status_code != 200:
        raise RuntimeError(f"JRA result HTTP {r.status_code}: {race_id}")

    text = _decode_response(r)
    d = _parse_jra_result_table(text, race_id)

    final_odds = _fetch_final_win_odds(race_id)
    if final_odds:
        d["finalOdds"] = d["number"].map(final_odds)

    print(
        "result source: JRA official",
        "rows:",
        len(d),
        "final odds:",
        int(d["finalOdds"].notna().sum()),
        "race:",
        race_id,
    )

    return d


agg.fetch_result_table = fetch_result_table_jra


if __name__ == "__main__":
    agg.main()
