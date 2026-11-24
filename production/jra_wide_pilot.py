"""One-race-per-day research capture and settlement of the original tickets."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import math
from pathlib import Path

from aligned_portfolio_review import read, wide_results
from all_bet_baseline import race_key, result_state
from all_bet_forward import write_first
from build_bet_research_data import checked_results
from formal_portfolio_capture import digest, stamp, window
from jra_wide_odds_source import fetch_jra_wide_odds
from jra_wide_research_archive import verify_snapshot
from live_bundle_publisher import atomic
from longshot_top3_v2_runtime import path_for as longshot_path
from research_longshot_wide_odds_replay import frozen_coherent_field, prices
from research_wide_four_way import MODELS, POLICIES, select_cards
from train_longshot_top3 import ROOT, odds_underdog
from win_odds_freshness import JST, MAX_AGE


VERSION = "jra-wide-pilot-v1"
CAPTURE_NAME = "JRAWidePilotCapture.json"
SETTLED_NAME = "JRAWidePilotSettlement.json"
MODEL_PATH = ROOT / "research/model_candidates/wide_jra_pilot_v1/models.json"
MODEL_SHA256 = "7ad2d888ca48d8c817342eceb7541ed817b66ab39974b522b6d68c686e9887f4"
PLAN_KEYS = {f"{model}/{policy}" for model in MODELS for policy in POLICIES}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jra-wide-pilot")


def frozen_models():
    raw = MODEL_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MODEL_SHA256:
        raise ValueError("JRA Wide pilot model hash mismatch")
    return json.loads(raw)


def public_cards(cards, numbers):
    return {
        f"{model}/{policy}": {
            "probabilityAnyHit": float(chance),
            "tickets": [
                {"a": int(numbers[i][0]), "b": int(numbers[i][1]), "probability": probability}
                for i, probability in tickets
            ],
        }
        for (model, policy), (chance, tickets) in cards.items()
    }


def validate_capture(record, day):
    unsigned = {key: value for key, value in record.items() if key != "fingerprint"}
    race = record["prediction"]
    rday, rid, start = race_key(race)
    if (record.get("version") != VERSION or record.get("researchOnly") is not True
            or record.get("modelSha256") != MODEL_SHA256 or record.get("fingerprint") != digest(unsigned)
            or rday != day or race.get("captureQuality") != "formal_10min"
            or race.get("predictionTiming") != "live_10min"
            or record["sourcePredictionSha256"] != digest(race)):
        raise ValueError("invalid JRA Wide pilot capture identity")
    clocks = [window(day, start, value) for value in (
        race["capturedAtJST"], record["longshotRecordedAtJST"], record["recordedAtJST"],
    )]
    if clocks != sorted(clocks):
        raise ValueError("invalid JRA Wide pilot capture chronology")
    numbers = {h["number"] for h in race["horses"]}
    sets = [record["probabilityCards"]]
    quote = record["officialWide"]
    if quote is not None:
        if (record["quoteStatus"] != "verifiedAtDecision"
                or stamp(quote["scheduledPostJST"]) != stamp(day + "T" + start)
                or set(quote["horseNumbers"]) != numbers
                or verify_snapshot(quote, day.replace("-", ""), rid, clocks[-1]) != record["quoteAgeSeconds"]):
            raise ValueError("official Wide quote differs from pilot capture")
        sets.append(record["oddsFilteredCards"])
    elif (record["quoteStatus"] not in {"missingEntryURL", "fetchFailed", "quoteInvalid"}
          or record["oddsFilteredCards"] is not None or record["quoteAgeSeconds"] is not None):
        raise ValueError("missing official Wide is not eligible for price selection")
    for index, bundle in enumerate(sets):
        if bundle.keys() != PLAN_KEYS:
            raise ValueError("four JRA Wide research plans required")
        for plan in bundle.values():
            tickets = plan["tickets"]
            planned = [(t["a"], t["b"]) for t in tickets]
            if (len(planned) != len(set(planned)) or len(planned) not in ({3} if index == 0 else {0, 3})
                    or any(a >= b or a not in numbers or b not in numbers for a, b in planned)
                    or not math.isfinite(plan["probabilityAnyHit"]) or not 0 <= plan["probabilityAnyHit"] <= 1
                    or any(not math.isfinite(t["probability"]) or not 0 <= t["probability"] <= 1 for t in tickets)):
                raise ValueError("invalid frozen Wide research plan")
    return record


def capture(root, race, entry_url, *, clock=lambda: datetime.now(JST), fetch=fetch_jra_wide_odds):
    day, rid, start = race_key(race)
    date8 = day.replace("-", "")
    directory = root / "reports" / date8
    destination = directory / CAPTURE_NAME
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".jra_wide_pilot.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "captureInProgress"
        if destination.is_file():
            validate_capture(read(destination), day)
            return "alreadyFrozenToday"
        now = clock()
        window(day, start, now.isoformat())
        if (race.get("predictionTiming") != "live_10min"
                or race.get("captureQuality") != "formal_10min" or stamp(race["capturedAtJST"]) > now):
            return "ineligiblePrediction"
        input_path = longshot_path(root, day, rid)
        if not input_path.is_file():
            return "missingLongshotInput"
        saved = read(input_path)
        if stamp(saved["recordedAtJST"]) > now:
            raise ValueError("longshot record is from the future")
        horses = sorted(race["horses"], key=lambda h: h["number"])
        longshots = odds_underdog([h["popularity"] for h in horses], [h["odds"] for h in horses])
        if len(horses) < 8 or not longshots.any():
            return "ineligibleField"
        field, numbers = frozen_coherent_field(race, horses, longshots, saved)
        models = frozen_models()
        baseline = public_cards(select_cards(field, models, numbers), numbers)
        deadline = stamp(day + "T" + start) - timedelta(minutes=8) + timedelta(microseconds=1)
        quote = None
        status = "missingEntryURL"
        reason = None
        if entry_url:
            remaining = (deadline - clock()).total_seconds()
            if remaining <= 0:
                return "deadlineMissed"
            try:
                quote = fetch(rid, date8, entry_url=entry_url, clock=clock,
                              request_timeout=min(8, remaining / 4))
            except Exception as exc:
                status = "fetchFailed"
                reason = type(exc).__name__

        age = None
        filtered = None
        if quote is not None:
            try:
                decision = clock()
                age = verify_snapshot(quote, date8, rid, decision)
                odds = prices(quote)
                if (stamp(quote["scheduledPostJST"]) != stamp(day + "T" + start)
                        or set(odds) != set(map(tuple, numbers))):
                    raise ValueError("official Wide roster or start differs")
                filtered = public_cards(select_cards(field, models, numbers, odds), numbers)
                status = "verifiedAtDecision"
            except (ValueError, KeyError, TypeError) as exc:
                quote, age, filtered = None, None, None
                status, reason = "quoteInvalid", str(exc)

        now = clock()
        if now >= deadline:
            return "deadlineMissed"
        if quote is not None:
            try:
                age = verify_snapshot(quote, date8, rid, now)
            except ValueError as exc:
                quote, age, filtered = None, None, None
                status, reason = "quoteInvalid", str(exc)
        record = {
            "version": VERSION, "researchOnly": True, "recordedAtJST": now.isoformat(),
            "prediction": race, "sourcePredictionSha256": digest(race),
            "longshotRecordedAtJST": saved["recordedAtJST"], "longshotFingerprint": saved["recordFingerprint"],
            "modelSha256": MODEL_SHA256, "probabilityCards": baseline,
            "officialWide": quote, "quoteStatus": status, "quoteReason": reason,
            "quoteAgeSeconds": age, "oddsFilteredCards": filtered,
        }
        record["fingerprint"] = digest(record)
        validate_capture(record, day)
        if quote is not None:
            deadline = min(deadline, stamp(quote["freshnessEvidence"]["sourceUpdatedAtJST"]) + MAX_AGE
                           + timedelta(microseconds=1))
        return status if write_first(destination, record, deadline, clock) else "deadlineMissed"


def schedule(root, race, entry_url):
    def worker():
        try:
            state = capture(root, race, entry_url)
            print("JRA_WIDE_PILOT_CAPTURE", race["raceID"], state, flush=True)
        except Exception as exc:
            print("WARNING: JRA_WIDE_PILOT_CAPTURE", race["raceID"], type(exc).__name__, str(exc), flush=True)

    return _executor.submit(worker)


def settle(root, date8, now):
    directory = root / "reports" / date8
    path = directory / CAPTURE_NAME
    if not path.is_file():
        return None
    day = datetime.strptime(date8, "%Y%m%d").date().isoformat()
    record = validate_capture(read(path), day)
    race = record["prediction"]
    rid = race["raceID"]
    saved = read(longshot_path(root, day, rid))
    if saved.get("recordFingerprint") != record["longshotFingerprint"]:
        raise ValueError("pilot and first longshot capture differ")
    actual = checked_results(directory / "ResultData.json", day, now).get((day, rid))
    state, finish = result_state(race, actual)
    if state != "matched":
        return {"state": state}
    payouts = wide_results(root, race, finish)
    if not payouts:
        return {"state": "awaitingPayout"}

    report = {"version": VERSION, "researchOnly": True, "raceDate": day, "raceID": rid,
              "state": "settled", "captureFingerprint": record["fingerprint"],
              "quoteStatus": record["quoteStatus"], "groups": {}}
    for name in ("probabilityCards", "oddsFilteredCards"):
        if record[name] is None:
            continue
        outcomes = {}
        for label, plan in record[name].items():
            pairs = [payouts[f"{t['a']:02}{t['b']:02}"] for t in plan["tickets"]]
            stake = 100 * len(pairs)
            payout = sum(value for _, value in pairs)
            outcomes[label] = {
                "stakeYen": stake, "payoutYen": payout, "hits": sum(hit for hit, _ in pairs),
                "returnPct": 100 * payout / stake if stake else None,
                "torigami": 0 < payout < stake, "skipped": not pairs,
            }
        report["groups"][name] = outcomes
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    args = parser.parse_args()
    date8 = datetime.strptime(args.date, "%Y%m%d").strftime("%Y%m%d")
    if date8 != args.date:
        parser.error("date must use YYYYMMDD")
    result = settle(ROOT, date8, datetime.now(JST))
    print("JRA_WIDE_PILOT_SETTLEMENT", result["state"] if result else "noCapture")
    if result and result["state"] == "settled":
        atomic(ROOT / "reports" / date8 / SETTLED_NAME,
               (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
