# ============================================================
# KeibaAI - forward result aggregation launcher
#
# Purpose
# - Keep aggregate_forward_results.py unchanged.
# - Replace only the race-result fetcher with a more robust version.
# - Prefer the static db.netkeiba.com race page because the newer
#   race.netkeiba.com result page can return HTTP 200 without an HTML
#   result table to GitHub-hosted runners.
# ============================================================

from __future__ import annotations

import io

import numpy as np
import pandas as pd

import aggregate_forward_results as agg


ORIGINAL_FETCH_RESULT_TABLE = agg.fetch_result_table


def _find_result_table_from_tables(tables):
    for raw in tables:
        t = agg.flatten_columns(raw)
        cols = list(t.columns)
        if (
            agg.find_col(cols, exact=("着順",), contains=("着順",))
            and agg.find_col(cols, exact=("馬番",), contains=("馬番",))
            and agg.find_col(cols, exact=("馬名",), contains=("馬名",))
        ):
            return t
    return None


def _parse_result_dataframe(result: pd.DataFrame, race_id: str) -> pd.DataFrame:
    finish_col = agg.find_col(result.columns, exact=("着順",), contains=("着順",))
    no_col = agg.find_col(result.columns, exact=("馬番",), contains=("馬番",))
    name_col = agg.find_col(result.columns, exact=("馬名",), contains=("馬名",))
    odds_col = agg.find_col(
        result.columns,
        exact=("単勝オッズ", "単勝"),
        contains=("単勝オッズ", "単勝"),
    )
    pop_col = agg.find_col(result.columns, exact=("人気",), contains=("人気",))

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

    # db.netkeiba.com is a traditional static HTML page.  Feeding the raw
    # bytes to pandas/lxml lets the page's own charset declaration be used.
    try:
        tables = pd.read_html(io.BytesIO(response.content))
    except Exception as exc:
        raise RuntimeError(
            f"db result table parse failed: {race_id}: {exc}"
        ) from exc

    result = _find_result_table_from_tables(tables)

    if result is None:
        # Diagnostic: show the table headers we actually received.
        headers = []
        for raw in tables[:10]:
            try:
                headers.append(list(agg.flatten_columns(raw).columns))
            except Exception:
                pass

        raise RuntimeError(
            f"db race result table not found: {race_id}; "
            f"table_headers={headers[:5]}"
        )

    parsed = _parse_result_dataframe(result, race_id)

    print(
        "result source: db.netkeiba.com",
        "rows:",
        len(parsed),
        "race:",
        race_id,
    )

    return parsed


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
