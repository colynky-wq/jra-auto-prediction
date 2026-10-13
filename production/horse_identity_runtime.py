"""Resolve a horse by exact normalized name AND birth year, never name alone."""
from __future__ import annotations

import math
import re

from horse_profile_runtime import normalize_horse_name

IDENTITY_METHOD_VERSION = "exact-name-birth-year-v1"


def birth_year(race_date, age) -> int:
    if isinstance(age, bool):
        raise ValueError("invalid horse age")
    try:
        value = float(age)
        year = int(str(race_date)[:4])
    except (TypeError, ValueError):
        raise ValueError("horse age and race year are required") from None
    if not math.isfinite(value) or not value.is_integer() or not 1 <= value <= 40 or not 1900 <= year <= 2200:
        raise ValueError("invalid horse age or race year")
    return year - int(value)


def key_identity(name, key) -> tuple[str, int] | None:
    match = re.fullmatch(r"(.+)_([12]\d{3})", str(key))
    normalized = normalize_horse_name(name)
    if not match or not normalized or normalize_horse_name(match[1]) != normalized:
        return None
    return normalized, int(match[2])


def history_key_index(history) -> dict[tuple[str, int], str]:
    keys = {}
    for name, key in history[["馬名", "馬キー"]].dropna().drop_duplicates().itertuples(index=False, name=None):
        identity = key_identity(name, key)
        if identity is None:
            continue
        if identity in keys and keys[identity] != str(key):
            raise ValueError("ambiguous horse keys for the same name and birth year")
        keys[identity] = str(key)
    return keys


def resolve_horse_key(name, age, race_date, keys=None) -> str:
    normalized = normalize_horse_name(name)
    if not normalized:
        raise ValueError("horse name is required")
    identity = normalized, birth_year(race_date, age)
    return (keys or {}).get(identity, f"{normalized}_{identity[1]}")


def assign_horse_keys(entries, history, race_date) -> list[str]:
    keys = history_key_index(history)
    return [resolve_horse_key(name, age, race_date, keys)
            for name, age in entries[["馬名", "年齢"]].itertuples(index=False, name=None)]


def resolve_runner_key(runner, race_date, keys) -> str | None:
    """Missing identity stays unknown; a debut must not borrow an old namesake."""
    name = runner["name"]
    supplied = runner.get("horseKey")
    if runner.get("age") is not None:
        key = resolve_horse_key(name, runner["age"], race_date, keys)
        if supplied is not None and key_identity(name, supplied) != key_identity(name, key):
            raise ValueError("runner age and horse key disagree")
        return key
    identity = key_identity(name, supplied) if supplied is not None else None
    if supplied is not None and identity is None:
        raise ValueError("invalid runner horse key")
    return keys.get(identity, str(supplied)) if identity is not None else None


def identity_mismatch_mask(history):
    """Audit stored ages against birth years without changing any history rows."""
    import pandas as pd

    years = pd.to_datetime(history["日付"], errors="coerce").dt.year
    ages = pd.to_numeric(history["年齢"], errors="coerce")
    suffixes = pd.to_numeric(history["馬キー"].astype(str).str.extract(r"_([12]\d{3})$", expand=False), errors="coerce")
    return years.isna() | ages.isna() | ~ages.between(1, 40) | ages.mod(1).ne(0) | suffixes.isna() | suffixes.ne(years - ages)
