from __future__ import annotations

import json
from datetime import datetime

import build_signal_data as base
import wide_odds_runtime as wide


SIGNAL_VERSION = "keibaai-signals-v2-wide"
RACEDATA_PATH = base.RACEDATA_PATH
OUT_PATH = base.OUT_PATH
REPORTS_DIR = base.REPORTS_DIR
WIDE_ODDS_PATH = wide.WIDE_ODDS_PATH


def _load_races() -> list[dict]:
    if not RACEDATA_PATH.exists():
        return []
    value = json.loads(RACEDATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise RuntimeError("RaceData.json root must be a list")
    return value


def _load_wide_by_race() -> dict[str, dict]:
    payload = wide._load_payload(WIDE_ODDS_PATH)
    out: dict[str, dict] = {}
    for item in payload.get("snapshots", []):
        rid = str(item.get("raceID") or "")
        if rid and rid not in out:
            out[rid] = item
    return out


def _all_wide_pairs(race: dict) -> list[dict]:
    """Rebuild every horse pair for validation, not only the app's top-20 list."""
    horses = [dict(h) for h in (race.get("horses") or [])]
    if len(horses) < 3:
        return []

    numbers: list[int] = []
    market_t3: list[float] = []
    ai_t3: list[float] = []
    for h in horses:
        try:
            numbers.append(int(h.get("number")))
        except Exception:
            return []
        market_t3.append(base.prob_pct(h.get("marketTop3Probability")))
        ai_t3.append(base.prob_pct(h.get("aiTop3Probability")))

    market_t3 = base.normalized(market_t3, 3.0)
    ai_t3 = base.normalized(ai_t3, 3.0)
    market_pair = base.maximum_entropy_top3_pair_probabilities(market_t3)
    ai_pair = base.maximum_entropy_top3_pair_probabilities(ai_t3)

    pairs = []
    for i in range(len(horses)):
        for j in range(i + 1, len(horses)):
            mp, ap = market_pair[i][j], ai_pair[i][j]
            edge = ap - mp
            ratio = ap / max(mp, 1e-9)
            pairs.append(
                {
                    "number1": numbers[i],
                    "name1": str(horses[i].get("name") or ""),
                    "number2": numbers[j],
                    "name2": str(horses[j].get("name") or ""),
                    "marketWideProbability": round(mp * 100.0, 3),
                    "aiWideProbability": round(ap * 100.0, 3),
                    "probabilityEdgePct": round(edge * 100.0, 3),
                    "aiMarketProbabilityRatio": round(ratio, 4),
                }
            )

    pairs.sort(
        key=lambda x: (
            -x["probabilityEdgePct"],
            -x["aiWideProbability"],
            x["number1"],
            x["number2"],
        )
    )
    return pairs


def _enrich_with_wide_odds(signal: dict, snapshot: dict | None) -> dict:
    signal = dict(signal)
    signal["signalVersion"] = SIGNAL_VERSION
    if not signal.get("eligible"):
        return signal

    wide_eval = dict(signal.get("wideEvaluation") or {})
    all_pairs = [dict(x) for x in (wide_eval.get("pairs") or [])]

    if not snapshot:
        wide_eval["status"] = "probability_only"
        wide_eval["oddsConnected"] = False
        wide_eval["validationPairCount"] = len(all_pairs)
        wide_eval["note"] = "発走前ワイド実オッズの保存待ち。現時点は同時3着内確率の比較のみです。"
        wide_eval["pairs"] = all_pairs[:20]
        wide_eval["validationPairs"] = all_pairs
        signal["wideEvaluation"] = wide_eval
        signal["ticketPlan"] = {
            "status": "wide_odds_pending",
            "enabled": False,
            "message": "ワイド実オッズが保存されるまで金額配分は行いません。",
        }
        return signal

    odds_map = {}
    for row in snapshot.get("pairs", []):
        try:
            key = tuple(sorted((int(row.get("number1")), int(row.get("number2")))))
        except Exception:
            continue
        odds_map[key] = row

    connected = 0
    positive_candidates = 0
    for row in all_pairs:
        key = tuple(sorted((int(row["number1"]), int(row["number2"]))))
        odds = odds_map.get(key)
        if not odds:
            continue
        low = base.finite(odds.get("oddsLow"))
        high = base.finite(odds.get("oddsHigh"))
        if low is None or high is None:
            continue
        p = max(0.0, min(1.0, float(row.get("aiWideProbability") or 0.0) / 100.0))
        ev_low = p * low
        ev_high = p * high
        row["wideOddsLow"] = round(low, 2)
        row["wideOddsHigh"] = round(high, 2)
        row["conservativeExpectedValue"] = round(ev_low, 3)
        row["upperExpectedValue"] = round(ev_high, 3)
        row["oddsPopularity"] = odds.get("popularity")
        if ev_low >= 1.10:
            row["valueBand"] = "高め候補"
            positive_candidates += 1
        elif ev_low >= 1.00:
            row["valueBand"] = "候補"
            positive_candidates += 1
        elif ev_high >= 1.00:
            row["valueBand"] = "境界"
        else:
            row["valueBand"] = "低め"
        connected += 1

    all_pairs.sort(
        key=lambda x: (
            -(x.get("conservativeExpectedValue") if x.get("conservativeExpectedValue") is not None else -1.0),
            -float(x.get("aiWideProbability") or 0.0),
            int(x.get("number1") or 0),
            int(x.get("number2") or 0),
        )
    )

    wide_eval.update(
        {
            "status": "live_odds_connected" if connected else "probability_only",
            "oddsConnected": bool(connected),
            "capturedAtJST": snapshot.get("capturedAtJST"),
            "officialDatetime": snapshot.get("officialDatetime"),
            "connectedPairCount": connected,
            "validationPairCount": len(all_pairs),
            "positiveCandidateCount": positive_candidates,
            "note": (
                "AI同時3着内確率×発走前ワイド表示オッズの参考期待値。"
                "下限オッズを使う値を保守的期待値として表示し、実運用成績で検証します。"
            ),
            # App-facing list is intentionally compact.
            "pairs": all_pairs[:20],
            # Forward validator uses all combinations so high-EV pairs are not
            # lost just because their probability-edge rank is below 20.
            "validationPairs": all_pairs,
        }
    )
    signal["wideEvaluation"] = wide_eval
    signal["ticketPlan"] = {
        "status": "forward_validation",
        "enabled": False,
        "message": (
            "実ワイドオッズ接続済み。ただし前向き実運用データが十分に蓄積するまでは"
            "金額配分を有効化しません。"
        ),
    }
    return signal


def main() -> None:
    races = _load_races()
    if not races:
        print("RaceData.json missing or empty; nothing to publish.")
        return

    wide_by_race = _load_wide_by_race()
    output = []
    for race in races:
        built = base.build_race_signal(race)
        if built is None:
            continue
        if built.get("eligible"):
            wide_eval = dict(built.get("wideEvaluation") or {})
            wide_eval["pairs"] = _all_wide_pairs(race)
            built["wideEvaluation"] = wide_eval
        rid = str(race.get("raceID") or "")
        output.append(_enrich_with_wide_odds(built, wide_by_race.get(rid)))

    output.sort(
        key=lambda r: (
            str(r.get("raceDate") or ""),
            str(r.get("startTime") or ""),
            str(r.get("raceID") or ""),
        )
    )

    payload = {
        "schemaVersion": 2,
        "signalVersion": SIGNAL_VERSION,
        "generatedAtJST": datetime.now(base.JST).strftime("%Y-%m-%d %H:%M:%S"),
        "formalSignals": True,
        "researchBasis": {
            "chaosBattleCutoffs": "2024 fixed; evaluated on untouched 2025",
            "wideProbability": "fixed-size Top3 maximum-entropy joint distribution",
            "wideOdds": "first prediction-time live Wide snapshot per race",
            "wideValidationScope": "all horse pairs; app displays top 20 only",
            "forwardValidation": "2026 live data is not used to fit cutoffs or models",
        },
        "races": output,
    }
    base.write_json(OUT_PATH, payload)

    by_date: dict[str, list[dict]] = {}
    for row in output:
        d = str(row.get("raceDate") or "").replace("-", "")
        if len(d) == 8 and d.isdigit():
            by_date.setdefault(d, []).append(row)
    for d, rows in by_date.items():
        day_payload = dict(payload)
        day_payload["races"] = rows
        base.write_json(REPORTS_DIR / d / "SignalData.json", day_payload)

    connected = sum(bool((x.get("wideEvaluation") or {}).get("oddsConnected")) for x in output)
    print("signal version:", SIGNAL_VERSION)
    print("race signals:", len(output))
    print("wide odds connected races:", connected)
    print("output:", OUT_PATH)


if __name__ == "__main__":
    main()
