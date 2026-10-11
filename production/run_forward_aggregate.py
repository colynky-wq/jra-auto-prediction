# ============================================================
# KeibaAI - forward result aggregation launcher
#
# Purpose
# - Keep aggregate_forward_results.py unchanged.
# - Replace only the race-result fetcher.
# - Resolve each JRA result URL from that race's routed official entry page.
# - Use JRA official results for finish positions / popularity.
# - Use the proven netkeiba win-odds API for final win odds.
#
# Why this version
# - A JRA result route suffix is race-specific.
# - Crawling only one result seed per venue can find only that one race.
# - The routed JRA entry page for EACH race contains its own result link,
#   so resolve the result URL race-by-race instead.
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

_RESULT_URL_CACHE: dict[str, str] = {}


# ============================================================
# 1. JRA result URL discovery
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
    best_replacements = 10**18

    for enc in encodings:
        if not enc:
            continue
        try:
            text = response.content.decode(enc, errors="replace")
        except Exception:
            continue

        score = sum(
            text.count(token)
            for token in ["着順", "馬番", "馬名", "レース結果", "出馬表"]
        )
        replacements = text.count("�")

        if score > best_score or (
            score == best_score and replacements < best_replacements
        ):
            best = text
            best_score = score
            best_replacements = replacements

    if best is None:
        return response.text
    return best


def _discover_result_url_for_race(race_id: str, date8: str) -> str:
    """Resolve the routed JRA accessS result URL for exactly one race.

    The crucial point is that route tokens are race-specific.  We therefore
    first resolve the official routed ENTRY page for this race, then read the
    same-race 'レース結果' accessS link from that page.
    """

    race_id = str(race_id).zfill(12)

    if race_id in _RESULT_URL_CACHE:
        return _RESULT_URL_CACHE[race_id]

    entry_url = entry_source.discover_entry_url(
        race_id,
        date8,
        SESSION,
    )

    if not entry_url:
        raise RuntimeError(
            f"JRA routed entry URL not found while resolving result: {race_id}"
        )

    r = SESSION.get(entry_url, timeout=30)
    print(
        "JRA result-link source HTTP:",
        r.status_code,
        "bytes:",
        len(r.content),
        "race:",
        race_id,
    )

    if r.status_code != 200:
        raise RuntimeError(
            f"JRA entry page HTTP {r.status_code} while resolving result: {race_id}"
        )

    text = _decode_response(r)
    candidates = _extract_result_urls(entry_url, text)

    exact = []
    for u in candidates:
        rid = _race_id_from_result_url(u, date8)
        if rid == race_id:
            exact.append(u)

    routed = [u for u in exact if _has_route_suffix(u)]

    if routed:
        result_url = routed[0]
        _RESULT_URL_CACHE[race_id] = result_url
        print(
            "JRA same-race routed result URL:",
            result_url,
        )
        return result_url

    print(
        "JRA result links seen from entry page:",
        len(candidates),
        "same-race:",
        len(exact),
        "routed same-race:",
        len(routed),
        "race:",
        race_id,
    )

    raise RuntimeError(
        f"same-race JRA routed result URL not found from entry page: {race_id}"
    )


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
        raise RuntimeError(
            f"JRA result table parse failed: {race_id}: {exc}"
        ) from exc

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
        no = pd.to_numeric(
            pd.Series([row[no_col]]),
            errors="coerce",
        ).iloc[0]

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
        raise RuntimeError(
            f"JRA result rows = 0: {race_id}"
        )

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

        print(
            "final win odds parsed:",
            len(result),
            "race:",
            race_id,
        )

        return result

    except Exception as exc:
        print(
            "final odds warning:",
            race_id,
            repr(exc),
        )
        return {}


# ============================================================
# 4. Hook used by aggregate_forward_results.py
# ============================================================


def fetch_result_table_jra(race_id: str) -> pd.DataFrame:
    race_id = str(race_id).zfill(12)

    url = _discover_result_url_for_race(
        race_id,
        agg.TARGET_DATE8,
    )

    r = SESSION.get(
        url,
        timeout=30,
    )

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
        raise RuntimeError(
            f"JRA result HTTP {r.status_code}: {race_id}"
        )

    text = _decode_response(r)
    d = _parse_jra_result_table(
        text,
        race_id,
    )

    final_odds = _fetch_final_win_odds(
        race_id
    )

    if final_odds:
        d["finalOdds"] = d["number"].map(
            final_odds
        )

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
