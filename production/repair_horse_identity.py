"""One verified correction; no fetching, prediction, or broad history rewriting."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from horse_identity_runtime import identity_mismatch_mask

RACE_ID = "202605040405"
NAME = "アインザッツ"
OLD_KEY = "アインザッツ_2013"
NEW_KEY = "アインザッツ_2024"
OFFICIAL_URL = "https://www.jra.go.jp/JRADB/accessU.html?CNAME=pw01dud002024106178%2FC1"


def correct_verified_row(history):
    corrected = history.copy()
    mask = (corrected["レースID"].astype(str).eq(RACE_ID)
            & corrected["馬名"].eq(NAME)
            & pd.to_datetime(corrected["日付"]).eq(pd.Timestamp("2026-10-11"))
            & corrected["年齢"].eq(2)
            & corrected["出典"].eq("JRA公式更新"))
    if int(mask.sum()) != 1:
        raise ValueError("verified entrant must match exactly one stored row")
    original = corrected.loc[mask, "馬キー"].iloc[0]
    if original not in {OLD_KEY, NEW_KEY}:
        raise ValueError("verified entrant has an unexpected key")
    corrected.loc[mask, "馬キー"] = NEW_KEY
    if identity_mismatch_mask(corrected).any():
        raise ValueError("another age/birth-year mismatch needs separate investigation")
    return corrected, original == OLD_KEY, int(corrected.index[mask][0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, default=Path(__file__).parent / "production_history.parquet")
    parser.add_argument("--report", type=Path, default=Path(__file__).parent.parent / "reports/history_updates/horse_identity_20261013.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    before_bytes = args.history.read_bytes()
    before = pd.read_parquet(args.history)
    corrected, changed, index = correct_verified_row(before)
    if not changed:
        print("Verified identity is already corrected; no files changed")
        return
    if not args.apply:
        print("Verified one-row correction; use --apply to save")
        return
    if args.report.exists():
        raise ValueError("correction evidence already exists; refusing to overwrite")
    tmp = args.history.with_suffix(".identity.tmp.parquet")
    try:
        corrected.to_parquet(tmp, index=False, compression="zstd")
        reread = pd.read_parquet(tmp)
        pd.testing.assert_frame_equal(reread, corrected)
        pd.testing.assert_frame_equal(before.drop(columns="馬キー"), reread.drop(columns="馬キー"))
        changed_cells = before["馬キー"].ne(reread["馬キー"])
        assert changed_cells.sum() == 1 and changed_cells.iloc[index]
        report = {
            "schemaVersion": 1, "correctionDate": "2026-10-13", "changedRows": 1,
            "changedColumns": ["馬キー"], "raceID": RACE_ID, "horseNumber": int(before.at[index, "馬番"]),
            "horseName": NAME, "raceDate": "2026-10-11", "age": 2,
            "oldHorseKey": OLD_KEY, "newHorseKey": NEW_KEY,
            "officialHorseID": "2024106178", "officialBirthDate": "2024-05-01", "sourceURL": OFFICIAL_URL,
            "beforeHistorySHA256": hashlib.sha256(before_bytes).hexdigest(),
            "afterHistorySHA256": hashlib.sha256(tmp.read_bytes()).hexdigest(),
            "rowCount": len(before), "columnCount": len(before.columns),
            "otherCellsAndDtypesUnchanged": True, "modelWeightsChanged": False, "savedPredictionsChanged": False,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        # Retain the proof before publishing the corrected history.
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(args.history)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
