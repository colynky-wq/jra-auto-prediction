"""Audit the saved T-10 cohort and optionally reconstruct V2 in a read-only replay."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
from io import BytesIO
import json
from math import comb
from pathlib import Path
import subprocess

import formal_portfolio_capture as capture
from inspect_frozen_win_probabilities import check, official_podium, paired_race_uncertainty, prob
from longshot_top3_v2_runtime import entry_horses, model_spec, qualifying_horses

ROOT = Path(__file__).resolve().parents[1]
MODEL_FIELDS = {"market": "marketTop3Probability", "formalAI": "aiTop3Probability",
                "V2Replay": "v2Top3Probability"}
BANDS = (("10+", 10, float("inf")), ("10–<20", 10, 20),
         ("20–<50", 20, 50), ("50+", 50, float("inf")))


def load_audited(root, audit):
    aligned = audit["aligned"]
    check(audit["projectionState"] == "verified" and aligned["countsAreCurrent"] is True
          and audit.get("cohortLockRecoveryRequired") is not True, "audit not current")
    excluded = {(row["raceDate"], row["raceID"]) for row in audit["quality"]["resultExclusions"]}
    check(len(audit["races"]) == aligned["racesObserved"], "audit race count mismatch")
    seen, races, missing_podium = set(), [], []
    for row in audit["races"]:
        day, rid = row["raceDate"], row["raceID"]
        check((day, rid) not in seen, "duplicate race")
        seen.add((day, rid))
        if row["resultState"] != "matched" or (day, rid) in excluded:
            continue
        bundle = capture.load(root, day, rid)
        check(bundle["fingerprint"] == row["inputFingerprint"], "audit input fingerprint differs")
        race = bundle["prediction"]
        check(race["predictionTiming"] == "live_10min" and race["captureQuality"] == "formal_10min",
              "nonformal prediction")
        capture.window(day, race["startTime"], race["capturedAtJST"])
        horses = entry_horses(race)
        podium = official_podium(row["actualFinish"], [h["number"] for h in horses], 3, rid)
        if podium is None:
            missing_podium.append(rid)
            continue
        races.append((race, podium))
    check(len(races) + len(missing_podium) == aligned["matchedResultRaces"], "matched audit count differs")
    return races, missing_podium


def compare(races, source_names):
    rows = {}
    for band, low, high in BANDS:
        picks, horses_count, outcomes = [], 0, 0
        forecasts, errors, paired_brier = defaultdict(float), defaultdict(float), defaultdict(list)
        for race, podium in races:
            horses = [h for h in qualifying_horses(race["horses"]) if low <= h["odds"] < high]
            if not horses:
                continue
            horses_count += len(horses)
            outcomes += sum(h["number"] in podium for h in horses)
            numbers, race_losses = {}, {}
            for name in source_names:
                field = MODEL_FIELDS[name]
                favorite = max(horses, key=lambda h: (h[field], -h["number"]))
                numbers[name] = favorite["number"]
                probabilities = [prob(h[field], field) for h in horses]
                loss = sum((p - (h["number"] in podium)) ** 2
                           for p, h in zip(probabilities, horses, strict=True))
                forecasts[name] += sum(probabilities)
                errors[name] += loss
                race_losses[name] = loss / len(horses)
            for name in source_names[1:]:
                paired_brier[name].append(race_losses[name] - race_losses["market"])
            picks.append((numbers, podium))
        summaries = {}
        for name in source_names:
            item = {"hits": sum(numbers[name] in podium for numbers, podium in picks),
                    "expectedHorseHits": round(forecasts[name], 4),
                    "horseBrier": round(errors[name] / horses_count, 8) if horses_count else None}
            if name != "market":
                model_only = sum(n[name] in p and n["market"] not in p for n, p in picks)
                market_only = sum(n[name] not in p and n["market"] in p for n, p in picks)
                discordant = model_only + market_only
                exact_p = min(1.0, 2 * sum(comb(discordant, n)
                                           for n in range(min(model_only, market_only) + 1))
                              / (2 ** discordant))
                item.update({"differentPickRaces": sum(n[name] != n["market"] for n, _ in picks),
                             "modelOnlyHits": model_only, "marketOnlyHits": market_only,
                             "hitDifferenceExactP": exact_p,
                             "raceBrierDeltaVsMarket": paired_race_uncertainty(paired_brier[name])})
            summaries[name] = item
        rows[band] = {"racesWithCandidate": len(picks), "horses": horses_count,
                      "actualPodiumHorses": outcomes, "models": summaries}
    return rows


def replay_v2(races, history, history_by_date):
    import numpy as np
    import pandas as pd
    from feature_engine import ALLF, add_features, add_rating
    from live_monitor_topk import TOP2_MODEL_PATH, TOP3_MODEL_PATH
    from longshot_top3_v2_runtime import score_race
    from topk_runtime import predict_top2_top3

    dates = pd.to_datetime(history["日付"])
    days = defaultdict(list)
    failures = defaultdict(list)
    errors = []
    rebuilt = []
    feature_names = [f for f in ALLF if f != "市場ロジット"]
    for race, podium in races:
        days[race["raceDate"]].append((race, podium))

    for day, items in sorted(days.items()):
        date = pd.Timestamp(day)
        if day in history_by_date:
            past = history_by_date[day]
            predate = past.loc[pd.to_datetime(past["日付"]) < date]
        else:
            predate = history.loc[dates < date]
        headers = history.loc[dates == date, ["レースID", "馬番", "馬キー", "調教師", "芝ダ", "距離", "クラス"]]
        current, available = [], []
        for race, podium in items:
            rid = race["raceID"]
            header = headers.loc[headers["レースID"].eq(rid)].dropna().set_index("馬番")
            horses = sorted(race["horses"], key=lambda h: h["number"])
            numbers = [h["number"] for h in horses]
            if not header.index.is_unique or not set(numbers).issubset(header.index):
                failures["missingTrainerOrRaceHeader"].append(rid)
                continue
            header = header.loc[numbers]
            if header["馬キー"].tolist() != [h["horseKey"] for h in horses]:
                failures["horseIdentityMismatch"].append(rid)
                continue
            if not all(header[c].nunique() == 1 for c in ("芝ダ", "距離", "クラス")):
                failures["inconsistentRaceHeader"].append(rid)
                continue
            surface, distance, race_class = header.iloc[0][["芝ダ", "距離", "クラス"]]
            if race["course"] != f"{surface}{int(distance)}m":
                failures["raceCourseMismatch"].append(rid)
                continue
            for h, trainer in zip(horses, header["調教師"], strict=True):
                current.append({"レースID": rid, "日付": date, "馬番": h["number"], "馬名": h["name"],
                                "馬キー": h["horseKey"], "年齢": h["age"], "斤量": h["carriedWeight"],
                                "騎手": h["jockey"], "調教師": trainer, "人気": h["popularity"],
                                "単勝オッズ": h["odds"], "馬体重": h["bodyWeight"], "増減": h["bodyWeightChange"],
                                "芝ダ": surface, "距離": int(distance), "クラス": race_class, "障害": False,
                                "出典": "GitHubライブ監視Top2Top3ドライラン", "頭数": len(horses),
                                "着順": np.nan, "タイム秒": np.nan, "上がり3F": np.nan, "初角": np.nan})
            available.append((dict(race, horses=horses), podium,
                              {"surface": surface, "distance": int(distance), "class": race_class}))
        if not current:
            continue
        print(f"Replaying {day}: {len(available)} races", flush=True)
        work = pd.concat([predate, pd.DataFrame(current)], ignore_index=True)
        work = work.sort_values(["日付", "レースID", "馬番"], ignore_index=True)
        work = add_features(add_rating(work))
        today = work.loc[work["日付"].eq(date)]
        for race, podium, context in available:
            rid = race["raceID"]
            frame = today.loc[today["レースID"].eq(rid)]
            horses = race["horses"]
            check(frame["馬番"].tolist() == [h["number"] for h in horses], "replay runner mismatch")
            topk = predict_top2_top3(frame[ALLF].astype(np.float32), frame["市場確率"].to_numpy(),
                                     TOP2_MODEL_PATH, TOP3_MODEL_PATH)
            error = max(abs(value * 100 - horse["aiTop3Probability"])
                        for value, horse in zip(topk["ai_top3"], horses, strict=True))
            errors.append((rid, round(float(error), 8)))
            if error > 0.000500001:
                failures["formalAIReproductionMismatch"].append(rid)
                continue
            inputs = {"featureNames": feature_names, "horseNumbers": frame["馬番"].tolist(),
                      "rows": frame[feature_names].astype(float).to_numpy().tolist()}
            scored = score_race(race, inputs, context, predate)
            p = {h["number"]: h["top3ProbabilityPct"] for h in scored["horses"]}
            scored_horses = [dict(h, v2Top3Probability=p.get(h["number"])) for h in horses]
            rebuilt.append((dict(race, horses=scored_horses), podium))

    return {
        "type": "retrospective_reconstruction_not_prospectively_stored",
        "model": model_spec(),
        "currentResultFieldsUsed": ["trainer", "raceClass", "surface", "distance"],
        "inputHistoricalOutcomeCutoff": "strictly before each race date",
        "coverageRaces": len(rebuilt), "dates": sorted({race["raceDate"] for race, _ in rebuilt}),
        "formalAIComparedRaces": len(errors), "formalAITolerancePctPoints": 0.0005,
        "formalAIReproductionMaxErrorsPctPoints": dict(errors), "excludedRaceIDs": dict(failures),
        "comparison": compare(rebuilt, ("market", "formalAI", "V2Replay")),
    }


def git_history(ref):
    import pandas as pd

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)

    commit = git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    at = git("show", "-s", "--format=%cI", commit).decode().strip()
    raw = git("show", commit + ":production/production_history.parquet")
    return pd.read_parquet(BytesIO(raw)), {
        "commit": commit, "commitAt": at, "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--header-archive", metavar="GIT_REF", help="updated archive for race headers")
    parser.add_argument("--history-by-date", metavar="DATE=GIT_REF", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = (ROOT / "AlignedPortfolioReview.json").read_bytes()
    audit = json.loads(raw)
    races, missing_podium = load_audited(ROOT, audit)
    result = {
        "auditGeneratedAtJST": audit["generatedAtJST"], "auditSha256": hashlib.sha256(raw).hexdigest(),
        "matchedAuditRaces": audit["aligned"]["matchedResultRaces"], "evaluatedRaces": len(races),
        "missingPodiumRaceIDs": missing_podium, "dates": sorted({race["raceDate"] for race, _ in races}),
        "criterion": "formal T-10 popularity rank >=6 and win odds >=10",
        "prospectivelyStoredV2Captures": sum(1 for _ in ROOT.glob("reports/*/longshot_top3_v2/*.json")),
        "comparison": compare(races, ("market", "formalAI")),
    }
    if args.replay:
        import pandas as pd

        if args.header_archive:
            history, header_source = git_history(args.header_archive)
        else:
            raw_history = (ROOT / "production/production_history.parquet").read_bytes()
            history = pd.read_parquet(BytesIO(raw_history))
            header_source = {"sha256": hashlib.sha256(raw_history).hexdigest()}
        snapshots, by_date, sources = {}, {}, {}
        first_captures = {}
        for race, _ in races:
            day = race["raceDate"]
            at = capture.stamp(race["capturedAtJST"])
            first_captures[day] = min(first_captures.get(day, at), at)
        for item in args.history_by_date:
            day, ref = item.split("=", 1)
            check(day in first_captures and day not in by_date, "invalid history date")
            if ref not in snapshots:
                snapshots[ref] = git_history(ref)
            by_date[day], sources[day] = snapshots[ref]
            check(capture.stamp(sources[day]["commitAt"]) < first_captures[day],
                  "archived history commit is later than capture")
        result["V2Replay"] = replay_v2(races, history, by_date)
        result["V2Replay"]["raceHeaderArchive"] = header_source
        result["V2Replay"]["priorHistoryArchivesByDate"] = sources
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
