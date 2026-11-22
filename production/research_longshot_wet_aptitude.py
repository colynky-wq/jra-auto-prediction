"""Test whether horses' prior wet-track results improve the frozen V2 model."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from research_longshot_position import add_top3_scores
from research_longshot_season_venue import apply, compare, context, fit
from research_longshot_top3_v2 import OUTPUT as SPECIALIST
from research_longshot_track_condition import archive_going, attach
from train_longshot_top3 import HISTORY, ROOT

OUT = ROOT / "research/LongshotWetAptitudeValidation.json"
HORSE_SURFACE = ["馬キー", "芝ダ"]
DAY = HORSE_SURFACE + ["日付"]


def profile(history, going):
    rows = attach(history, going)
    rows = rows.loc[rows["芝ダ"].isin(["芝", "ダ"]) & rows["頭数"].gt(1) & rows["馬キー"].notna()].copy()
    valid = rows["着順"].between(1, rows["頭数"]) & rows["人気"].between(1, rows["頭数"])
    # Past normalized positions gained versus past market rank, adjusted for field size.
    gain = ((rows["人気"] - rows["着順"]) / (rows["頭数"] - 1)).where(valid)
    wet = rows["going"].ne("良")
    rows["wetCount"], rows["dryCount"] = (wet & valid).astype(int), (~wet & valid).astype(int)
    rows["wetGain"], rows["dryGain"] = gain.where(wet, 0).fillna(0), gain.where(~wet, 0).fillna(0)
    cols = ["wetCount", "dryCount", "wetGain", "dryGain"]
    daily = rows.groupby(DAY, as_index=False, sort=True)[cols].sum()
    # Aggregate by calendar day before summing, so the entire current day is excluded.
    daily[cols] = daily.groupby(HORSE_SURFACE, sort=False)[cols].cumsum() - daily[cols]
    return daily


def enrich(scored, going, prior):
    rows = attach(context(scored), going).merge(prior, on=DAY, how="left", validate="many_to_one")
    if rows[["wetCount", "dryCount"]].isna().any().any():
        raise ValueError("missing start-day historical profile")
    eligible = rows["going"].ne("良") & rows["wetCount"].ge(1) & rows["dryCount"].ge(1)
    delta = rows["wetGain"] / (rows["wetCount"] + 3) - rows["dryGain"] / (rows["dryCount"] + 3)
    rows["wetExperience"] = np.where(eligible, "hasBoth", "skip")
    rows["wetAptitude"] = np.select(
        [eligible & delta.ge(.05), eligible & delta.le(-.05), eligible],
        ["above", "below", "near"], default="skip")
    return rows


def main():
    source_hash = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    meta = json.loads((SPECIALIST / "research.json").read_text())
    if source_hash != meta["sourceHistorySha256"]:
        raise ValueError("history differs from the frozen V2 research")
    h = pd.read_parquet(HISTORY)
    h["日付"] = pd.to_datetime(h["日付"])
    going, archive_hash = archive_going(ROOT)
    prior = profile(h, going)
    print("Replaying frozen V2 final-odds models", flush=True)
    scored = add_top3_scores(h, None, meta)
    train, test = (enrich(scored[y], going, prior) for y in (2025, 2026))
    if train["日付"].max() >= test["日付"].min():
        raise ValueError("overlapping train/test dates")
    controls = ("surfaceGoing", "wetExperience")
    reference = fit(train, "top3", "v2Top3", "standard", controls=controls)
    adjustment = fit(train, "top3", "v2Top3", "standard", controls=controls + ("wetAptitude",))
    wet_races = test.loc[test["going"].ne("良")]
    subsets = {"all": test, "wet": wet_races}
    for surface in ("芝", "ダ"):
        subsets[surface] = wet_races.loc[wet_races["芝ダ"].eq(surface)]
    data = {}
    for key, frame in subsets.items():
        ref = apply(frame, "v2Top3", reference)
        adjusted = apply(frame, "v2Top3", adjustment)
        # Isolate the aptitude term's contribution on horses with sufficient history.
        candidate = np.where(frame["wetAptitude"].ne("skip"), adjusted, ref)
        data[key] = {
            "frozenV2": compare(frame, "top3", frame["v2Top3"].to_numpy(), candidate),
            "vsGoingAndExperience": compare(frame, "top3", ref, candidate),
            "horsesWithBothConditions": int(frame["wetAptitude"].ne("skip").sum()),
            "racesWithBothConditions": frame.loc[frame["wetAptitude"].ne("skip"), "レースID"].nunique(),
            "aptitudeGroups": {str(k): {"horses": len(g), "actualTop3": int(g["top3"].sum()),
                                       "expectedTop3UnderControl": float(apply(g, "v2Top3", reference).sum())}
                               for k, g in frame.groupby("wetAptitude") if k != "skip"},
        }
    report = {
        "type": "retrospective_final_odds_going_by_official_result",
        "historySha256": source_hash, "lapArchiveSha256": archive_hash,
        "method": "prior-day same-surface wet vs dry normalized positions gained against market rank; per-condition shrinkage 3",
        "wetGoing": ["稍重", "重", "不良"],
        "trainingYear": 2025, "testYear": 2026, "trainingRaces": train["レースID"].nunique(),
        "data": data, "reference": reference, "adjustment": adjustment,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Written", OUT, flush=True)


if __name__ == "__main__":
    main()
