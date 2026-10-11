# ============================================================
# KeibaAI - forward result aggregation launcher
#
# Purpose
# - Keep aggregate_forward_results.py unchanged.
# - Replace only the race-result fetcher with a robust version.
# - Prefer the static db.netkeiba.com race page.
# - Decode Japanese HTML safely and parse the result table directly.
# - Fall back to the original race.netkeiba.com parser if needed.
# ============================================================

from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

import aggregate_forward_results as agg


ORIGINAL_FETCH_RESULT_TABLE = agg.fetch_result_table


# ============================================================
# 1. Common helpers
# ============================================================


def _normalize_header(value) -> str:
    s = str(value)
    s = re.sub(r"\s+", "", s)
    s = s.replace("\u3000", "")
    return s.strip()


def _find_result_table_from_tables(tables):
    for raw in tables:
        t = agg.flatten_columns(raw)
        cols = list(t.columns)
        normalized = [_normalize_header(x) for x in cols]

        has_finish = any("着順" in x for x in normalized)
        has_number = any("馬番" in x for x in normalized)
        has_name = any("馬名" in x for x in normalized)

        if has_finish and has_number and has_name:
            return t

    return None


def _find_col_normalized(columns, wanted_tokens):
    for c in columns:
        n = _normalize_header(c)
        if any(token in n for token in wanted_tokens):
            return c
    return None


def _parse_result_dataframe(result: pd.DataFrame, race_id: str) -> pd.DataFrame:
    finish_col = _find_col_normalized(result.columns, ("着順",))
    no_col = _find_col_normalized(result.columns, ("馬番",))
    name_col = _find_col_normalized(result.columns, ("馬名",))
    odds_col = _find_col_normalized(result.columns, ("単勝オッズ", "単勝"))
    pop_col = _find_col_normalized(result.columns, ("人気",))

    if not all([finish_col, no_col, name_col]):
        raise RuntimeError(
            f"required result columns missing: {race_id}: {list(result.columns)}"
        )

    rows = []

    for _, row in result.iterrows():
        no = pd.to_numeric(pd.Series([row[no_col]]), errors="coerce").iloc[0]
        if not np.isfinite(no):
            continue

        finish, status = agg.parse_finish(row[finish_col])

        final_odds = np.nan
        final_pop = np.nan

        if odds_col is not None:
            final_odds = pd.to_numeric(
                pd.Series([row[odds_col]]), errors="coerce"
            ).iloc[0]

        if pop_col is not None:
            final_pop = pd.to_numeric(
                pd.Series([row[pop_col]]), errors="coerce"
            ).iloc[0]

        rows.append(
            {
                "raceID": race_id,
                "number": int(no),
                "resultHorseName": str(row[name_col]).strip(),
                "finish": finish,
                "resultStatus": status,
                "finalOdds": final_odds,
                "finalPopularity": final_pop,
            }
        )

    d = pd.DataFrame(rows)

    if d.empty:
        raise RuntimeError(f"no result rows parsed: {race_id}")

    return (
        d.sort_values("number")
        .drop_duplicates("number", keep="last")
        .reset_index(drop=True)
    )


# ============================================================
# 2. Encoding detection
# ============================================================


def _decode_japanese_html(response) -> tuple[str, str, int]:
    """Choose the decoding that makes the result-table Japanese readable.

    db.netkeiba pages have historically varied in charset metadata.  Rather
    than trusting one declaration, try the likely encodings and score the
    decoded text using the labels that must appear on a completed result page.
    """

    candidates = []

    for enc in [
        response.encoding,
        response.apparent_encoding,
        "utf-8",
        "cp932",
        "shift_jis",
        "euc_jp",
    ]:
        if not enc:
            continue
        key = str(enc).lower().replace("-", "_")
        if key not in candidates:
            candidates.append(key)

    required_tokens = ["着順", "馬番", "馬名", "人気", "単勝"]

    best_text = None
    best_encoding = None
    best_score = -1
    best_replacements = 10**18

    for enc in candidates:
        try:
            text = response.content.decode(enc, errors="replace")
        except Exception:
            continue

        score = sum(text.count(token) for token in required_tokens)
        replacements = text.count("�")

        if score > best_score or (
            score == best_score and replacements < best_replacements
        ):
            best_text = text
            best_encoding = enc
            best_score = score
            best_replacements = replacements

    if best_text is None:
        raise RuntimeError("could not decode result HTML")

    print(
        "result DB decoding:",
        best_encoding,
        "score:",
        best_score,
        "replacement_chars:",
        best_replacements,
    )

    return best_text, str(best_encoding), int(best_score)


# ============================================================
# 3. Direct HTML result-table parser
# ============================================================


def _cell_text(cell) -> str:
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()


def _parse_static_table_from_html(text: str, race_id: str) -> pd.DataFrame:
    soup = BeautifulSoup(text, "html.parser")

    tables = list(soup.find_all("table"))
    candidate_tables = []

    for table in tables:
        table_text = _normalize_header(table.get_text(" ", strip=True))
        score = sum(
            token in table_text
            for token in ("着順", "馬番", "馬名", "人気", "単勝")
        )
        if score >= 3:
            candidate_tables.append((score, table))

    candidate_tables.sort(key=lambda x: x[0], reverse=True)

    if not candidate_tables:
        raise RuntimeError("static result table not found in HTML")

    table = candidate_tables[0][1]

    # Header indexes when available.
    header_cells = table.find_all("th")
    header_texts = [_normalize_header(_cell_text(c)) for c in header_cells]

    def header_index(tokens):
        for i, value in enumerate(header_texts):
            if any(token in value for token in tokens):
                return i
        return None

    idx_finish = header_index(("着順",))
    idx_number = header_index(("馬番",))
    idx_name = header_index(("馬名",))
    idx_pop = header_index(("人気",))
    idx_odds = header_index(("単勝オッズ", "単勝"))

    rows = []

    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue

        values = [_cell_text(c) for c in cells]

        # Standard db.netkeiba result-table layout:
        # 0 着順 / 1 枠番 / 2 馬番 / 3 馬名 / ... / 9 人気 / 10 単勝オッズ
        # Use those positions if the page did not expose useful <th> indexes.
        fi = idx_finish if idx_finish is not None and idx_finish < len(values) else 0
        ni = idx_number if idx_number is not None and idx_number < len(values) else 2
        nmi = idx_name if idx_name is not None and idx_name < len(values) else 3
        pi = idx_pop if idx_pop is not None and idx_pop < len(values) else 9
        oi = idx_odds if idx_odds is not None and idx_odds < len(values) else 10

        if max(fi, ni, nmi) >= len(values):
            continue

        no_match = re.search(r"\d+", values[ni])
        if not no_match:
            continue

        horse_no = int(no_match.group())
        if horse_no < 1 or horse_no > 18:
            continue

        horse_name = values[nmi].strip()
        if not horse_name:
            continue

        finish, status = agg.parse_finish(values[fi])

        final_pop = np.nan
        final_odds = np.nan

        if pi < len(values):
            final_pop = pd.to_numeric(
                pd.Series([values[pi]]), errors="coerce"
            ).iloc[0]

        if oi < len(values):
            final_odds = pd.to_numeric(
                pd.Series([values[oi]]), errors="coerce"
            ).iloc[0]

        rows.append(
            {
                "raceID": race_id,
                "number": horse_no,
                "resultHorseName": horse_name,
                "finish": finish,
                "resultStatus": status,
                "finalOdds": final_odds,
                "finalPopularity": final_pop,
            }
        )

    d = pd.DataFrame(rows)

    if d.empty:
        raise RuntimeError("static result HTML table produced 0 rows")

    d = (
        d.sort_values("number")
        .drop_duplicates("number", keep="last")
        .reset_index(drop=True)
    )

    return d


# ============================================================
# 4. Static db.netkeiba source
# ============================================================


def fetch_result_table_static(race_id: str) -> pd.DataFrame:
    race_id = str(race_id).zfill(12)
    url = f"https://db.netkeiba.com/race/{race_id}/"

    response = agg.SESSION.get(
        url,
        headers={
            **agg.HEADERS,
            "Referer": "https://db.netkeiba.com/",
        },
        timeout=30,
    )

    print(
        "result DB HTTP:",
        response.status_code,
        "bytes:",
        len(response.content),
        "race:",
        race_id,
        "url:",
        response.url,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"db result HTTP {response.status_code}: {race_id}"
        )

    text, encoding_used, score = _decode_japanese_html(response)

    # First choice: direct BeautifulSoup parsing. This avoids pandas/lxml
    # charset guesses, which caused mojibake on GitHub-hosted runners.
    try:
        parsed = _parse_static_table_from_html(text, race_id)
        print(
            "result source: db.netkeiba.com direct HTML",
            "encoding:",
            encoding_used,
            "rows:",
            len(parsed),
            "race:",
            race_id,
        )
        return parsed
    except Exception as direct_error:
        print(
            "STATIC DIRECT PARSE WARNING:",
            race_id,
            repr(direct_error),
        )

    # Second choice: pandas after *explicit* correct decoding.
    try:
        tables = pd.read_html(io.StringIO(text))
    except Exception as exc:
        raise RuntimeError(
            f"db result table parse failed: {race_id}: {exc}; "
            f"encoding={encoding_used}; score={score}"
        ) from exc

    result = _find_result_table_from_tables(tables)

    if result is None:
        headers = []
        for raw in tables[:10]:
            try:
                headers.append(list(agg.flatten_columns(raw).columns))
            except Exception:
                pass

        raise RuntimeError(
            f"db race result table not found: {race_id}; "
            f"encoding={encoding_used}; score={score}; "
            f"table_headers={headers[:5]}"
        )

    parsed = _parse_result_dataframe(result, race_id)

    print(
        "result source: db.netkeiba.com pandas",
        "encoding:",
        encoding_used,
        "rows:",
        len(parsed),
        "race:",
        race_id,
    )

    return parsed


# ============================================================
# 5. Public fetch hook used by aggregate_forward_results.py
# ============================================================


def robust_fetch_result_table(race_id: str) -> pd.DataFrame:
    """Static DB first; original race.netkeiba parser as fallback."""

    first_error = None

    try:
        return fetch_result_table_static(race_id)
    except Exception as exc:
        first_error = exc
        print(
            "STATIC RESULT WARNING:",
            str(race_id),
            repr(exc),
        )

    print(
        "Falling back to race.netkeiba.com result page:",
        str(race_id),
    )

    try:
        return ORIGINAL_FETCH_RESULT_TABLE(race_id)
    except Exception as second_error:
        raise RuntimeError(
            f"both result sources failed: {race_id}; "
            f"static={first_error}; race_page={second_error}"
        ) from second_error


agg.fetch_result_table = robust_fetch_result_table


if __name__ == "__main__":
    agg.main()
