"""Identification-only metadata for legacy app predictions; never a forecast."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd

from horse_identity_runtime import IDENTITY_METHOD_VERSION, identity_mismatch_mask, key_identity
from horse_profile_runtime import JRA_VENUE_MAP

ROOT = Path(__file__).resolve().parent.parent
COLUMNS = ["レースID", "日付", "馬番", "馬名", "馬キー", "年齢", "障害"]
JST = timezone(timedelta(hours=9))


def build_archives(history, limit=30, as_of_date=None):
    if limit < 1:
        raise ValueError("positive archive limit required")
    day = pd.Timestamp(as_of_date or datetime.now(JST).date()).normalize()
    d = history[COLUMNS].copy()
    d["日付"] = pd.to_datetime(d["日付"], errors="coerce").dt.normalize()
    rid = d["レースID"].astype(str)
    d = d[d["日付"].notna() & d["日付"].le(day) & ~d["障害"].fillna(False).astype(bool)
          & rid.str.fullmatch(r"\d{12}") & rid.str[4:6].isin(JRA_VENUE_MAP)].copy()
    dates = sorted(d["日付"].unique())[-limit:]
    d = d[d["日付"].isin(dates)].copy()
    invalid = identity_mismatch_mask(d)
    invalid |= pd.Series([key_identity(name, key) is None for name, key in d[["馬名", "馬キー"]].itertuples(index=False, name=None)], index=d.index, dtype=bool)
    numbers = pd.to_numeric(d["馬番"], errors="coerce")
    invalid |= numbers.isna() | ~numbers.between(1, 18) | numbers.mod(1).ne(0)
    invalid |= d.duplicated(["レースID", "馬番"], keep=False)
    out = {}
    for date in dates:
        rows = d[d["日付"] == date]
        safe = rows[~invalid.loc[rows.index]].sort_values(["レースID", "馬番"])
        races = []
        for race_id, group in safe.groupby("レースID", sort=False):
            races.append({"raceID": str(race_id), "horses": [
                {"number": int(n), "name": str(name), "age": int(age), "horseKey": str(key)}
                for n, name, age, key in group[["馬番", "馬名", "年齢", "馬キー"]].itertuples(index=False, name=None)
            ]})
        stamp = pd.Timestamp(date).strftime("%Y-%m-%d")
        out[stamp] = {"schemaVersion": 1, "raceDate": stamp, "identityMethodVersion": IDENTITY_METHOD_VERSION,
                      "source": "production/production_history.parquet", "races": races,
                      "unresolvedRows": int(invalid.loc[rows.index].sum())}
    return out


def write_archives(payloads, root):
    changed = 0
    for day, payload in payloads.items():
        path = Path(root) / day.replace("-", "") / "HorseIdentityData.json"
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if path.exists() and path.read_text() == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(text)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        changed += 1
    return changed


def main():
    history = pd.read_parquet(ROOT / "production/production_history.parquet", columns=COLUMNS)
    payloads = build_archives(history)
    changed = write_archives(payloads, ROOT / "reports")
    print("IDENTITY_ARCHIVES", len(payloads), "changed", changed,
          "unresolved", sum(p["unresolvedRows"] for p in payloads.values()))


if __name__ == "__main__":
    main()
