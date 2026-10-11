# ============================================================
# KeibaAI - JRA official race-entry source
#
# Reproduces the URL-discovery and entry-table parsing pattern used by the
# successful 2026-10-11 Colab forward run.  In particular, JRA accessD URLs
# can require an extra route suffix (for example /B6); constructing only the
# bare CNAME may return HTTP 200 but no race table.
# ============================================================

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup


JRA_HOST = "https://www.jra.go.jp"
ACCESS_D = JRA_HOST + "/JRADB/accessD.html"

# Exact seed used by the successful 2026-10-11 Colab run.  It is intentionally
# retained for the historical replay test because that run discovered the
# 2026-10-11 entry URLs by crawling JRA's accessD links from this page.
ORIGINAL_20261011_SEED = (
    ACCESS_D
    + "?CNAME="
    + "pw01dde0105202604030320261010%2FB6"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}


def _make_direct_entry_url(race_id: str, date8: str) -> str:
    rid = str(race_id).zfill(12)
    year = rid[0:4]
    venue = rid[4:6]
    meeting = rid[6:8]
    day = rid[8:10]
    race_no = rid[10:12]

    cname = (
        "pw01dde01"
        + venue
        + year
        + meeting
        + day
        + race_no
        + date8
    )

    return ACCESS_D + "?" + urlencode({"CNAME": cname})


def _race_id_from_url(url: str, date8: str) -> str | None:
    try:
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        cname = q.get("CNAME", [None])[0]
        if not cname:
            return None

        cname = unquote(cname)
        m = re.search(
            r"pw01dde01"
            r"(\d{2})"
            r"(\d{4})"
            r"(\d{2})"
            r"(\d{2})"
            r"(\d{2})"
            r"(\d{8})",
            cname,
        )
        if not m:
            return None

        venue = m.group(1)
        year = m.group(2)
        meeting = m.group(3)
        day = m.group(4)
        race_no = m.group(5)
        found_date8 = m.group(6)

        if found_date8 != date8:
            return None

        return year + venue + meeting + day + race_no

    except Exception:
        return None


def _extract_accessd_urls(page_url: str, text: str) -> list[str]:
    soup = BeautifulSoup(text, "html.parser")
    found_urls = [page_url]

    for a in soup.find_all("a", href=True):
        href = str(a.get("href", ""))
        if "accessD.html" not in href or "pw01dde01" not in href:
            continue
        found_urls.append(urljoin(page_url, href))

    raw = html_lib.unescape(text)
    patterns = re.findall(
        r'[^"\']*accessD\.html\?CNAME=pw01dde01[^"\']+',
        raw,
    )

    for p in patterns:
        p = p.strip()
        if p.startswith("http"):
            u = p
        else:
            pos = p.find("accessD.html")
            if pos < 0:
                continue
            u = urljoin(ACCESS_D, p[pos:])
        found_urls.append(u)

    # Preserve order while removing exact duplicates.
    return list(dict.fromkeys(found_urls))


def discover_entry_url(
    race_id: str,
    date8: str,
    session: requests.Session,
) -> str | None:
    """Discover the routed JRA accessD URL for one race.

    The successful Colab code crawled links from a seed page rather than
    assuming the bare CNAME URL itself contained the entry table.
    """

    target = str(race_id).zfill(12)
    direct = _make_direct_entry_url(target, date8)

    queue = [ORIGINAL_20261011_SEED, direct]
    visited: set[str] = set()
    url_map: dict[str, str] = {}
    max_pages = 16

    while queue and len(visited) < max_pages:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)

        try:
            r = session.get(page_url, timeout=20)
            print(
                "JRA discovery HTTP:",
                r.status_code,
                "bytes:",
                len(r.content),
                "url:",
                page_url,
            )
            if r.status_code != 200:
                continue

            enc = r.apparent_encoding or "shift_jis"
            text = r.content.decode(enc, errors="replace")
            found_urls = _extract_accessd_urls(page_url, text)

            for u in found_urls:
                rid = _race_id_from_url(u, date8)
                if rid:
                    url_map[rid] = u

            if target in url_map:
                print("JRA discovered target URL:", url_map[target])
                return url_map[target]

            for u in found_urls:
                rid = _race_id_from_url(u, date8)
                if rid and u not in visited and u not in queue:
                    queue.append(u)

        except Exception as e:
            print("JRA discovery warning:", repr(e))
            continue

    print("JRA discovered race URLs:", len(url_map))
    if target in url_map:
        return url_map[target]

    return None


def _parse_entry_table(
    text: str,
    race_id: str,
) -> pd.DataFrame:
    soup = BeautifulSoup(text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("tableなし")

    rows = []

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        uma_raw = tds[1].get_text(" ", strip=True)
        um = re.search(r"\d+", uma_raw)
        if not um:
            continue

        uma = int(um.group())
        if uma < 1 or uma > 18:
            continue

        info_td = tds[2]
        info_text = re.sub(
            r"\s+",
            " ",
            info_td.get_text(" ", strip=True),
        )

        links = [
            a.get_text(" ", strip=True)
            for a in info_td.find_all("a")
            if a.get_text(" ", strip=True)
        ]

        horse_name = links[0] if links else ""

        if not horse_name:
            m_name = re.match(
                r"^(.*?)\s*\d+(?:\.\d+)?\s*\(\s*\d+番人気",
                info_text,
            )
            if m_name:
                horse_name = m_name.group(1).strip()

        if not horse_name:
            continue

        m_weight = re.search(
            r"(\d{3})\s*kg\s*\(\s*([+-]?\d+)\s*\)",
            info_text,
        )

        if m_weight:
            body_weight = int(m_weight.group(1))
            diff = int(m_weight.group(2))
        else:
            body_weight = np.nan
            diff = np.nan

        trainer_full = None
        affiliation_node = info_td.find(
            string=re.compile(r"\(\s*(美浦|栗東)\s*\)")
        )

        if affiliation_node is not None:
            trainer_a = affiliation_node.find_previous("a")
            if trainer_a is not None and info_td in trainer_a.parents:
                trainer_full = trainer_a.get_text(" ", strip=True)

        sex_td = tds[3]
        sex_text = re.sub(
            r"\s+",
            " ",
            sex_td.get_text(" ", strip=True),
        )

        m_age = re.search(
            r"(牡|牝|せん|セン|セ)\s*(\d+)",
            sex_text,
        )
        if not m_age:
            raise RuntimeError(
                f"性齢解析失敗 馬番{uma}: {sex_text}"
            )
        age = int(m_age.group(2))

        m_kg = re.search(
            r"(\d+(?:\.\d+)?)\s*kg",
            sex_text,
        )
        if not m_kg:
            raise RuntimeError(
                f"斤量解析失敗 馬番{uma}: {sex_text}"
            )
        carried_weight = float(m_kg.group(1))

        jockey_links = [
            a.get_text(" ", strip=True)
            for a in sex_td.find_all("a")
            if a.get_text(" ", strip=True)
        ]

        if jockey_links:
            jockey_full = jockey_links[-1]
        else:
            jockey_full = re.sub(
                r"^.*?\d+(?:\.\d+)?\s*kg",
                "",
                sex_text,
            ).strip()

        rows.append(
            {
                "レースID": str(race_id).zfill(12),
                "馬番": uma,
                "馬名": horse_name,
                "年齢": age,
                "斤量": carried_weight,
                "騎手_JRA": jockey_full,
                "調教師_JRA": trainer_full,
                "馬体重": body_weight,
                "増減": diff,
            }
        )

    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError("出走馬解析0頭")

    return (
        d.sort_values("馬番")
        .drop_duplicates("馬番", keep="last")
        .reset_index(drop=True)
    )


def fetch_entry_table(
    race_id: str,
    date8: str,
    jst,
):
    """Return (DataFrame, fetch_time, actual_url), matching Colab contract."""

    session = requests.Session()
    session.headers.update(HEADERS)

    discovered = discover_entry_url(race_id, date8, session)
    direct = _make_direct_entry_url(race_id, date8)

    urls: list[str] = []
    if discovered:
        urls.append(discovered)
    if direct not in urls:
        urls.append(direct)

    last_error = None

    for url in urls:
        try:
            r = session.get(url, timeout=20)
            fetch_time = pd.Timestamp(datetime.now(jst))

            print("JRA entry URL:", url)
            print("JRA entry HTTP:", r.status_code, "bytes:", len(r.content))

            if r.status_code != 200:
                last_error = f"HTTP={r.status_code}"
                continue

            enc = r.apparent_encoding or "shift_jis"
            text = r.content.decode(enc, errors="replace")
            d = _parse_entry_table(text, race_id)

            print("JRA official entry parsed:", len(d))
            return d, fetch_time, url

        except Exception as e:
            last_error = str(e)
            print("JRA entry parse warning:", last_error)

    raise RuntimeError(
        f"JRA出馬表取得失敗: {race_id} / {last_error}"
    )
