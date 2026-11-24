"""Independent pre-race research when the current odds feed is unavailable.

Never manufacture market odds or a formal prediction. The separate archive
does not belong to the first-formal-capture cohort or app recommendations.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

from horse_identity_runtime import assign_horse_keys
import live_monitor_dry_run as base
from market_shrink_shadow import JST, fingerprint, _write_first
from no_current_odds_shadow import verify_model
from train_no_current_odds_challenger import FEATURES, probability_triplet

VERSION = "no-current-odds-independent-v1-20261124"
DIRECTORY = "no_current_odds_independent_v1"


def feature_matrix(race, entries, hist, target_date):
    """Use only prior-day history and active official entry fields."""
    from live_monitor_topk import _match_history_name_or_current

    rid = str(race["race_id"])
    if hist.empty or not (pd.to_datetime(hist["日付"]).dt.normalize()
                          < target_date.normalize()).all():
        raise ValueError("history must strictly precede target race day")
    entries = entries.copy().sort_values("馬番").reset_index(drop=True)
    if len(entries) < 3 or entries["馬番"].duplicated().any():
        raise ValueError("invalid active roster")
    hist_jockeys = sorted(set(hist["騎手"].dropna().astype(str)))
    hist_trainers = sorted(set(hist["調教師"].dropna().astype(str)))
    entries["騎手"] = [
        _match_history_name_or_current(
            current,
            hist_jockeys,
            role="jockey",
            horse_no=int(horse_no),
        )
        for horse_no, current in zip(entries["馬番"], entries["騎手_JRA"])
    ]
    entries["調教師"] = [
        _match_history_name_or_current(
            current,
            hist_trainers,
            role="trainer",
            horse_no=int(horse_no),
        )
        for horse_no, current in zip(entries["馬番"], entries["調教師_JRA"])
    ]

    if entries["騎手"].isna().any() or entries["調教師"].isna().any():
        raise RuntimeError("internal jockey/trainer fallback failed")

    entries["馬キー"] = assign_horse_keys(entries, hist, target_date)


    # Explicitly discard any current odds/popularity present in the entry frame.
    current = entries[["馬番", "馬名", "馬キー", "年齢", "斤量", "騎手",
                       "調教師", "馬体重", "増減"]].copy()
    current["単勝オッズ"] = np.nan
    current["人気"] = np.nan
    cur = pd.DataFrame(index=np.arange(len(current)))
    cur["レースID"] = rid
    cur["日付"] = target_date
    for col in [
        "馬番", "馬名", "馬キー", "年齢", "斤量", "騎手", "調教師",
        "人気", "単勝オッズ", "馬体重", "増減",
    ]:
        cur[col] = current[col].to_numpy()

    cur["着順"] = np.nan
    cur["出典"] = "GitHubライブ監視Top2Top3ドライラン"
    cur["芝ダ"] = race["surface"]
    cur["距離"] = int(race["distance"])
    cur["クラス"] = race["class"]
    cur["障害"] = False
    cur["タイム秒"] = np.nan
    cur["上がり3F"] = np.nan
    cur["初角"] = np.nan
    cur["頭数"] = len(cur)

    work = pd.concat([hist, cur], ignore_index=True)
    work["日付"] = pd.to_datetime(work["日付"], errors="coerce")
    work = work.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)

    before_n = len(work)
    work = base.add_rating(work)
    if len(work) != before_n:
        raise RuntimeError("add_rating changed row count")
    work = base.add_features(work)
    if len(work) != before_n:
        raise RuntimeError("add_features changed row count")

    pred = (
        work.loc[
            (work["日付"].dt.normalize() == target_date.normalize())
            & (work["レースID"].astype(str) == rid)
        ]
        .copy()
        .sort_values("馬番")
        .reset_index(drop=True)
    )

    if len(pred) != len(cur):
        raise RuntimeError("prediction row count mismatch")

    # The legacy engine computes this non-market feature only on rows with
    # odds. Reconstruct its exact mathematical definition on active entrants.
    pred["点数差"] = pred["能力点数"] - pred["能力点数"].mean()
    X = pred.loc[:, list(FEATURES)].astype(np.float32)
    if not np.isfinite(X.to_numpy()).all():
        raise ValueError("non-finite independent features")
    X["レースID"] = rid
    return X, pred["馬番"].astype(int).tolist()


def archive_path(root, day, rid):
    if not re.fullmatch(r"\d{12}", rid):
        raise ValueError("invalid race ID")
    return Path(root) / "reports" / day.replace("-", "") / DIRECTORY / (rid + ".json")


def _time(value):
    t = datetime.fromisoformat(str(value))
    if t.utcoffset() != timedelta(hours=9):
        raise ValueError("JST timestamp required")
    return t


def build(race, entries, evidence, hist, target_date, meta, model, *, clock):
    day = target_date.strftime("%Y-%m-%d")
    rid = str(race["race_id"])
    archive_path(Path("."), day, rid)
    post = datetime.fromisoformat(day + "T" + str(race["start_time"])).replace(tzinfo=JST)
    if (not isinstance(evidence, dict) or evidence.get("raceID") != rid
            or evidence.get("raceDate") != day
            or _time(evidence["scheduledPostJST"]) != post
            or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("sourceSHA256", "")))):
        raise ValueError("official entry evidence mismatch")
    captured = _time(evidence["capturedAtJST"])
    if not post - timedelta(minutes=12) <= captured <= clock() <= post - timedelta(minutes=8):
        raise ValueError("independent T10 window passed")
    if (meta.get("finalRefitThrough") != "2025-12-31"
            or meta.get("candidatePromotionApproved") is not False
            or meta.get("features") != list(FEATURES)):
        raise ValueError("unapproved research model contract")
    runners = evidence["runners"]
    roster = {r["number"]: r for r in runners}
    numbers = entries["馬番"].astype(int).tolist()
    if (len(roster) != len(runners) or len(numbers) != len(set(numbers))
            or set(numbers) != set(roster)
            or any(type(r.get("withdrawn")) is not bool for r in runners)):
        raise ValueError("incomplete official roster")
    for n, name, age in entries[["馬番", "馬名", "年齢"]].itertuples(index=False, name=None):
        if str(name) != roster[int(n)]["name"] or int(age) != roster[int(n)]["age"]:
            raise ValueError("entry identity mismatch")
    active = entries.loc[entries["馬番"].isin(
        [n for n, r in roster.items() if not r["withdrawn"]])]
    X, numbers = feature_matrix(race, active, hist, target_date)
    probs = probability_triplet(X, model)
    recorded = clock()
    if not captured <= recorded <= post - timedelta(minutes=8):
        raise ValueError("independent T10 window passed during calculation")
    result = {
        "version": VERSION, "schemaVersion": 1, "researchOnly": True,
        "status": "independent_prospectively_frozen_not_approved",
        "formalRecommendationsEnabled": False, "formalPredictionAIChanged": False,
        "candidatePromotionApproved": False, "autoBetEnabled": False,
        "raceDate": day, "raceID": rid, "startTime": race["start_time"],
        "capturedAtJST": captured.isoformat(), "recordedAtJST": recorded.isoformat(),
        "currentOddsUsed": False, "sourceEntryEvidence": evidence,
        "modelVersion": meta["version"], "modelSha256": meta["modelSha256"],
        "featureInputSha256": fingerprint({"featureNames": list(FEATURES),
            "horseNumbers": numbers, "rows": X[list(FEATURES)].astype(float).values.tolist()}),
        "candidateHorses": [{"number": n, "name": roster[n]["name"],
            "winPct": round(float(probs["win"][i])*100, 8),
            "top2Pct": round(float(probs["top2"][i])*100, 8),
            "top3Pct": round(float(probs["top3"][i])*100, 8)}
            for i, n in enumerate(numbers)],
    }
    result["recordFingerprint"] = fingerprint(result)
    return result


def record_best_effort(root, race, hist, target_date, *, clock=base.now_jst):
    """Call once per race, only following a retryable odds failure."""
    try:
        day, rid = target_date.strftime("%Y-%m-%d"), str(race["race_id"])
        path = archive_path(root, day, rid)
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if (saved.get("version") != VERSION or saved.get("raceID") != rid
                    or saved.get("raceDate") != day
                    or saved.get("recordFingerprint") != fingerprint({
                        k: v for k, v in saved.items() if k != "recordFingerprint"})):
                raise ValueError("independent archive identity/digest mismatch")
            return {"status": "already_frozen", "path": str(path)}
        post = datetime.fromisoformat(day + "T" + str(race["start_time"])).replace(tzinfo=JST)
        if not post - timedelta(minutes=12) <= clock() <= post - timedelta(minutes=8):
            raise ValueError("independent T10 window passed")
        meta, model = verify_model()
        entries, _, _ = base.fetch_jra_entry_table(rid, day.replace("-", ""), JST)
        value = build(race, entries, entries.attrs.get("allBetEntryEvidence"),
                      hist, target_date, meta, model, clock=clock)
        return {"status": "prospectively_frozen", "path": str(_write_first(path, value))}
    except Exception as exc:
        return {"status": "error_no_formal_impact", "reason": str(exc)[:200]}
