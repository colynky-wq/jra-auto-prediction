"""Enrich JRA result rows with passing position, final 3F and race-level laps.

Shadow/research-only data path. This module does not alter formal prediction
inputs. It monkey-patches the already-used JRA result table parser so the live
ResultData publisher can persist details that are present on the completed JRA
result page.
"""
from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd

import run_forward_aggregate as base
import aggregate_forward_results as agg
from race_lap_runtime import parse_official_laps

_INSTALLED = False
_ORIGINAL = None


def _norm(value) -> str:
    return re.sub(r"\s+", "", str(value)).replace("\u3000", "").strip()


def _find_col(columns, tokens):
    for col in columns:
        n = _norm(col)
        if any(token in n for token in tokens):
            return col
    return None


def _number(value):
    try:
        x = float(value)
        return int(x) if np.isfinite(x) else None
    except Exception:
        return None


def _last3f(value):
    try:
        text = str(value).strip()
        m = re.search(r"(\d{1,2}\.\d)", text)
        if not m:
            return np.nan
        x = float(m.group(1))
        return x if 20.0 <= x <= 60.0 else np.nan
    except Exception:
        return np.nan


def enrich_result_dataframe(text: str, race_id: str, parsed: pd.DataFrame) -> pd.DataFrame:
    """Add passingOrder and last3F from the official result table when present."""
    out = parsed.copy()
    out["passingOrder"] = None
    out["last3F"] = np.nan

    try:
        tables = pd.read_html(io.StringIO(text))
    except Exception:
        return out

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
        return out

    no_col = _find_col(result.columns, ("馬番",))
    passing_col = _find_col(result.columns, ("通過",))
    last3_col = _find_col(result.columns, ("上り", "上がり"))
    if no_col is None:
        return out

    detail = {}
    for _, row in result.iterrows():
        no = _number(row[no_col])
        if no is None or not 1 <= no <= 18:
            continue
        passing = None
        if passing_col is not None:
            raw = str(row[passing_col]).strip()
            if raw and raw.lower() != "nan":
                passing = raw
        l3 = _last3f(row[last3_col]) if last3_col is not None else np.nan
        detail[no] = (passing, l3)

    if not detail:
        return out

    out["passingOrder"] = out["number"].map(lambda n: detail.get(int(n), (None, np.nan))[0])
    out["last3F"] = out["number"].map(lambda n: detail.get(int(n), (None, np.nan))[1])
    return out


def _enriched_parser(text: str, race_id: str) -> pd.DataFrame:
    parsed = _ORIGINAL(text, race_id)
    enriched = enrich_result_dataframe(text, race_id, parsed)

    # The result URL is already resolved and cached by the live JRA fetch path.
    # Reuse the same HTML to parse official race-level laps without another request.
    try:
        source_url = base._RESULT_URL_CACHE.get(str(race_id).zfill(12))
        date8 = str(agg.TARGET_DATE8)
        if source_url:
            enriched.attrs["raceLap"] = parse_official_laps(
                text, str(race_id).zfill(12), date8, source_url
            )
    except Exception as exc:
        enriched.attrs["raceLapError"] = repr(exc)
    enriched.attrs["resultDetailVersion"] = 2
    return enriched


def install() -> None:
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    _ORIGINAL = base._parse_jra_result_table
    base._parse_jra_result_table = _enriched_parser
    _INSTALLED = True


install()
