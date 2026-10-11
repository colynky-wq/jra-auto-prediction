# ============================================================
# KeibaAI - forward result aggregation launcher v2
#
# Purpose
# - Keep the existing aggregation logic unchanged.
# - Strengthen JRA result-URL discovery.
# - Parse routed CNAME tokens that JRA embeds in HTML/JavaScript even when
#   they are not normal <a href="..."> links.
# - For the already-completed 2026-10-11 replay, provide exact routed
#   result URLs as a historical safety net so today's report can be built
#   deterministically.
# ============================================================

from __future__ import annotations

import html as html_lib
import re
from urllib.parse import unquote, urlencode

import aggregate_forward_results as agg
import run_forward_aggregate as base


# ============================================================
# 1. Historical 2026-10-11 routed result URLs
#
# These are used only for the completed replay date.  Future dates continue
# to use automatic discovery below.
# ============================================================

KNOWN_RESULT_ROUTES_20261011 = {
    # Tokyo 5R-12R
    "202605040405": "0C",
    "202605040406": "C1",
    "202605040407": "76",
    "202605040408": "2B",
    "202605040409": "E0",
    "202605040410": "D5",
    "202605040411": "8A",
    "202605040412": "3F",

    # Kyoto 5R-12R
    "202608040405": "EA",
    "202608040406": "9F",
    "202608040407": "54",
    "202608040408": "09",
    "202608040409": "BE",
    "202608040410": "B3",
    "202608040411": "68",
    "202608040412": "1D",
}


def _historical_result_url(race_id: str) -> str | None:
    race_id = str(race_id).zfill(12)

    if agg.TARGET_DATE8 != "20261011":
        return None

    suffix = KNOWN_RESULT_ROUTES_20261011.get(race_id)
    if not suffix:
        return None

    year = race_id[0:4]
    venue = race_id[4:6]
    meeting = race_id[6:8]
    day = race_id[8:10]
    race_no = race_id[10:12]

    cname = (
        "pw01sde01"
        + venue
        + year
        + meeting
        + day
        + race_no
        + agg.TARGET_DATE8
        + "/"
        + suffix
    )

    return base.ACCESS_S + "?" + urlencode({"CNAME": cname})


# ============================================================
# 2. Enhanced result-link extraction
# ============================================================

ORIGINAL_EXTRACT_RESULT_URLS = base._extract_result_urls


def enhanced_extract_result_urls(page_url: str, text: str) -> list[str]:
    """Extract JRA accessS result URLs from links AND embedded route tokens.

    JRA pages can keep navigation state inside scripts/forms rather than
    ordinary anchors.  The previous extractor only accepted explicit
    accessS.html links, which can therefore return zero candidates even when
    routed CNAME values are present in the source.
    """

    urls = list(ORIGINAL_EXTRACT_RESULT_URLS(page_url, text))

    raw_variants = []

    raw = html_lib.unescape(text)
    raw_variants.append(raw)

    try:
        raw_variants.append(unquote(raw))
    except Exception:
        pass

    # Exact routed result CNAME shape:
    # pw01sde01 + venue(2) + year(4) + meeting(2) + day(2)
    # + race(2) + calendar-date(8) + /XX
    token_pattern = re.compile(
        r"pw01sde01\d{20}(?:(?:%2[fF])|/)[0-9A-Fa-f]{2}"
    )

    for source in raw_variants:
        for match in token_pattern.findall(source):
            cname = unquote(match)
            url = base.ACCESS_S + "?" + urlencode({"CNAME": cname})
            urls.append(url)

    # De-duplicate while preserving order.
    urls = list(dict.fromkeys(urls))

    if urls:
        routed = sum(1 for u in urls if base._has_route_suffix(u))
        print(
            "JRA enhanced result links:",
            len(urls),
            "routed:",
            routed,
        )

    return urls


base._extract_result_urls = enhanced_extract_result_urls


# ============================================================
# 3. Result fetch hook
# ============================================================

ORIGINAL_FETCH_RESULT_TABLE_JRA = base.fetch_result_table_jra


def fetch_result_table_v2(race_id: str):
    race_id = str(race_id).zfill(12)

    historical_url = _historical_result_url(race_id)

    if historical_url:
        print(
            "JRA historical routed result URL:",
            historical_url,
        )

        r = base.SESSION.get(
            historical_url,
            timeout=30,
        )

        print(
            "JRA result HTTP:",
            r.status_code,
            "bytes:",
            len(r.content),
            "race:",
            race_id,
        )

        if r.status_code != 200:
            raise RuntimeError(
                f"JRA result HTTP {r.status_code}: {race_id}"
            )

        text = base._decode_response(r)
        d = base._parse_jra_result_table(
            text,
            race_id,
        )

        final_odds = base._fetch_final_win_odds(
            race_id
        )

        if final_odds:
            d["finalOdds"] = d["number"].map(
                final_odds
            )

        print(
            "result source: JRA official historical route",
            "rows:",
            len(d),
            "final odds:",
            int(d["finalOdds"].notna().sum()),
            "race:",
            race_id,
        )

        return d

    # Future/current dates: use automatic discovery, now with the enhanced
    # embedded-CNAME extractor installed above.
    return ORIGINAL_FETCH_RESULT_TABLE_JRA(race_id)


agg.fetch_result_table = fetch_result_table_v2


if __name__ == "__main__":
    agg.main()
