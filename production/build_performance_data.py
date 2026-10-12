from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS_DIR = REPO / "reports"
RACEDATA_PATH = REPO / "RaceData.json"
RESULTDATA_PATH = REPO / "ResultData.json"
OUT_PATH = REPO / "PerformanceData.json"


def load_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, list) else []
    except Exception:
        return []


def finite(value, default=np.nan) -> float:
    try:
        x = float(value)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def prototype_chaos(ai_top_probability_pct: float, ratios: list[float]) -> int:
    # Keep this exactly aligned with the current Swift UI prototype.
    p = float(ai_top_probability_pct)
    if p < 20:
        score = 5
    elif p < 28:
        score = 4
    elif p < 38:
        score = 3
    elif p < 50:
        score = 2
    else:
        score = 1

    strong = sum(1 for x in ratios if np.isfinite(x) and x >= 1.20)
    if strong >= 2:
        score = min(5, score + 1)
    return int(score)


def prototype_battle(horses: list[dict]) -> tuple[float, str]:
    # Keep this exactly aligned with the current Swift UI prototype.
    best_ratio = max((finite(h.get("aiMarketRatio"), 0.0) for h in horses), default=0.0)
    best_expected = max((finite(h.get("expectedIndex"), 0.0) for h in horses), default=0.0)
    best_win = max((finite(h.get("aiProbability"), 0.0) for h in horses), default=0.0)

    score = 0.0
    if best_ratio >= 1.20:
        score += 1.4
    elif best_ratio >= 1.10:
        score += 0.9
    elif best_ratio >= 1.05:
        score += 0.5

    if best_expected >= 100:
        score += 1.0
    elif best_expected >= 90:
        score += 0.6

    if best_win >= 35:
        score += 0.8
    elif best_win >= 25:
        score += 0.4

    if score >= 3:
        grade = "A"
    elif score >= 2:
        grade = "B"
    elif score >= 1:
        grade = "C"
    else:
        grade = "見送り"
    return float(score), grade


def row_from_horses(meta: dict, predicted: list[dict], results: list[dict]) -> dict | None:
    if not predicted or not results:
        return None

    result_by_no = {
        int(r.get("number")): r
        for r in results
        if str(r.get("number", "")).strip().isdigit()
    }

    horses = []
    for h in predicted:
        try:
            no = int(h.get("number"))
        except Exception:
            continue
        rr = result_by_no.get(no)
        if rr is None:
            continue
        finish = rr.get("finishPosition")
        try:
            finish = int(finish) if finish is not None else None
        except Exception:
            finish = None
        horses.append(
            {
                "number": no,
                "name": str(h.get("name") or ""),
                "aiProbability": finite(h.get("aiProbability")),
                "marketProbability": finite(h.get("marketProbability")),
                "aiMarketRatio": finite(h.get("aiMarketRatio")),
                "expectedIndex": finite(h.get("expectedIndex")),
                "tenMinOdds": finite(h.get("odds")),
                "finish": finish,
                "finalOdds": finite(rr.get("finalOdds")),
                "popularity": finite(rr.get("popularity")),
            }
        )

    if len(horses) < 2:
        return None
    winners = [h for h in horses if h.get("finish") == 1]
    if len(winners) != 1:
        return None

    ai_order = sorted(horses, key=lambda h: (-finite(h["aiProbability"], -1), h["number"]))
    market_order = sorted(horses, key=lambda h: (-finite(h["marketProbability"], -1), h["number"]))
    actual_order = sorted(
        [h for h in horses if h.get("finish") is not None],
        key=lambda h: (h["finish"], h["number"]),
    )
    if not actual_order:
        return None

    ai_rank = {h["number"]: i + 1 for i, h in enumerate(ai_order)}
    market_rank = {h["number"]: i + 1 for i, h in enumerate(market_order)}
    winner = winners[0]
    ai_top = ai_order[0]
    market_top = market_order[0]

    actual_top3 = {h["number"] for h in actual_order if h["finish"] <= 3}
    ai_top3 = {h["number"] for h in ai_order[:3]}
    overlap = len(actual_top3 & ai_top3)

    ratios = [finite(h["aiMarketRatio"]) for h in horses]
    chaos = prototype_chaos(finite(ai_top["aiProbability"], 0.0), ratios)
    battle_score, battle_grade = prototype_battle(horses)

    ai_top_finish = ai_top.get("finish")
    market_top_finish = market_top.get("finish")
    ai_top_won = ai_top_finish == 1
    market_top_won = market_top_finish == 1
    ai_top_top3 = ai_top_finish is not None and ai_top_finish <= 3

    reference_return = finite(ai_top.get("finalOdds"), 0.0) if ai_top_won else 0.0

    race_date = str(meta.get("raceDate") or meta.get("date") or "").replace("/", "-")
    if len(race_date) == 8 and race_date.isdigit():
        race_date = f"{race_date[:4]}-{race_date[4:6]}-{race_date[6:8]}"

    return {
        "raceDate": race_date,
        "raceID": str(meta.get("raceID") or ""),
        "venue": meta.get("venue"),
        "raceNumber": int(meta.get("raceNumber") or 0),
        "startTime": meta.get("startTime"),
        "raceName": meta.get("raceName"),
        "course": meta.get("course"),
        "fieldSize": len(horses),
        "prototypeBattleScore": round(battle_score, 4),
        "prototypeBattleGrade": battle_grade,
        "prototypeChaosLevel": chaos,
        "prototypeChaosStars": "★" * chaos + "☆" * (5 - chaos),
        "aiTopNumber": ai_top["number"],
        "aiTopName": ai_top["name"],
        "aiTopProbability": round(finite(ai_top["aiProbability"], 0.0), 3),
        "aiTopFinish": ai_top_finish,
        "aiTopWon": bool(ai_top_won),
        "aiTopTop3": bool(ai_top_top3),
        "marketTopNumber": market_top["number"],
        "marketTopName": market_top["name"],
        "marketTopFinish": market_top_finish,
        "marketTopWon": bool(market_top_won),
        "winnerNumber": winner["number"],
        "winnerName": winner["name"],
        "winnerAiRank": int(ai_rank[winner["number"]]),
        "winnerMarketRank": int(market_rank[winner["number"]]),
        "aiTop3ActualTop3Overlap": int(overlap),
        "referenceAiTopFlatReturnIndex": round(float(reference_return), 4),
    }


def from_historical_csv(path: Path) -> list[dict]:
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        print("skip historical csv:", path, repr(exc))
        return []

    required = {
        "date", "raceID", "venue", "raceNumber", "startTime", "raceName", "course",
        "number", "horseName", "marketProbabilityPct", "aiProbabilityPct", "aiMarketRatio",
        "expectedIndex", "finish", "finalOdds", "finalPopularity",
    }
    if not required.issubset(df.columns):
        print("skip historical csv missing columns:", path)
        return []

    rows = []
    for race_id, g in df.groupby("raceID", sort=False):
        first = g.iloc[0]
        predicted = []
        results = []
        for _, r in g.iterrows():
            predicted.append(
                {
                    "number": int(r["number"]),
                    "name": str(r["horseName"]),
                    "odds": finite(r.get("tenMinOdds")),
                    "marketProbability": finite(r["marketProbabilityPct"]),
                    "aiProbability": finite(r["aiProbabilityPct"]),
                    "aiMarketRatio": finite(r["aiMarketRatio"]),
                    "expectedIndex": finite(r["expectedIndex"]),
                }
            )
            results.append(
                {
                    "number": int(r["number"]),
                    "finishPosition": int(finite(r["finish"])) if np.isfinite(finite(r["finish"])) else None,
                    "finalOdds": finite(r["finalOdds"]),
                    "popularity": int(finite(r["finalPopularity"])) if np.isfinite(finite(r["finalPopularity"])) else None,
                }
            )
        meta = {
            "raceDate": str(first["date"]),
            "raceID": str(race_id),
            "venue": str(first["venue"]),
            "raceNumber": int(first["raceNumber"]),
            "startTime": str(first["startTime"]),
            "raceName": str(first["raceName"]),
            "course": str(first["course"]),
        }
        out = row_from_horses(meta, predicted, results)
        if out:
            rows.append(out)
    return rows


def from_live_json() -> list[dict]:
    races = load_json_list(RACEDATA_PATH)
    results = load_json_list(RESULTDATA_PATH)
    if not races or not results:
        return []
    result_by_id = {str(r.get("raceID")): r for r in results}
    rows = []
    for race in races:
        rid = str(race.get("raceID") or "")
        rr = result_by_id.get(rid)
        if rr is None:
            continue
        meta = dict(race)
        meta["raceDate"] = race.get("raceDate") or rr.get("raceDate")
        out = row_from_horses(meta, race.get("horses") or [], rr.get("horses") or [])
        if out:
            rows.append(out)
    return rows


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {
            "races": 0,
            "aiTopWins": 0,
            "aiTopWinRatePct": None,
            "aiTopTop3": 0,
            "aiTopTop3RatePct": None,
            "marketTopWins": 0,
            "marketTopWinRatePct": None,
            "meanWinnerAiRank": None,
            "meanTop3Overlap": None,
            "referenceAiTopFlatReturnIndex": None,
        }

    ai_wins = sum(bool(r.get("aiTopWon")) for r in rows)
    ai_top3 = sum(bool(r.get("aiTopTop3")) for r in rows)
    market_wins = sum(bool(r.get("marketTopWon")) for r in rows)
    winner_ranks = [finite(r.get("winnerAiRank")) for r in rows]
    overlaps = [finite(r.get("aiTop3ActualTop3Overlap")) for r in rows]
    returns = [finite(r.get("referenceAiTopFlatReturnIndex"), 0.0) for r in rows]

    return {
        "races": n,
        "aiTopWins": int(ai_wins),
        "aiTopWinRatePct": round(ai_wins / n * 100.0, 2),
        "aiTopTop3": int(ai_top3),
        "aiTopTop3RatePct": round(ai_top3 / n * 100.0, 2),
        "marketTopWins": int(market_wins),
        "marketTopWinRatePct": round(market_wins / n * 100.0, 2),
        "meanWinnerAiRank": round(float(np.mean(winner_ranks)), 2),
        "meanTop3Overlap": round(float(np.mean(overlaps)), 2),
        "referenceAiTopFlatReturnIndex": round(float(np.mean(returns)), 3),
    }


def write_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def main() -> None:
    all_rows: list[dict] = []
    historical_dates: set[str] = set()

    for csv_path in sorted(REPORTS_DIR.glob("20??????/all_predictions.csv")):
        rows = from_historical_csv(csv_path)
        all_rows.extend(rows)
        historical_dates.update(str(r.get("raceDate")) for r in rows)

    # Current live data fills the gap before the nightly all_predictions.csv exists.
    for row in from_live_json():
        if str(row.get("raceDate")) not in historical_dates:
            all_rows.append(row)

    # De-duplicate by race ID, preferring the latest row in the assembled list.
    by_id = {str(r.get("raceID")): r for r in all_rows if str(r.get("raceID") or "")}
    all_rows = list(by_id.values())
    all_rows.sort(key=lambda r: (str(r.get("raceDate")), str(r.get("startTime") or ""), str(r.get("raceID"))))

    today = datetime.now(JST).date()
    week_start = today - timedelta(days=6)

    def parse_date(row: dict):
        try:
            return datetime.strptime(str(row.get("raceDate")), "%Y-%m-%d").date()
        except Exception:
            return None

    today_rows = [r for r in all_rows if parse_date(r) == today]
    week_rows = [r for r in all_rows if parse_date(r) is not None and week_start <= parse_date(r) <= today]

    periods = [
        {"id": "today", "label": "今日", "startDate": today.isoformat(), "endDate": today.isoformat(), "metrics": summarize(today_rows)},
        {"id": "last7", "label": "直近7日", "startDate": week_start.isoformat(), "endDate": today.isoformat(), "metrics": summarize(week_rows)},
        {"id": "all", "label": "前向き検証 全期間", "startDate": min((r.get("raceDate") for r in all_rows), default=None), "endDate": max((r.get("raceDate") for r in all_rows), default=None), "metrics": summarize(all_rows)},
    ]

    grade_order = ["見送り", "C", "B", "A"]
    by_battle = []
    for grade in grade_order:
        rows = [r for r in all_rows if r.get("prototypeBattleGrade") == grade]
        by_battle.append({"grade": grade, "prototype": True, "metrics": summarize(rows)})

    by_chaos = []
    for level in range(1, 6):
        rows = [r for r in all_rows if int(r.get("prototypeChaosLevel") or 0) == level]
        by_chaos.append({
            "level": level,
            "stars": "★" * level + "☆" * (5 - level),
            "prototype": True,
            "metrics": summarize(rows),
        })

    daily = []
    for date_text in sorted({str(r.get("raceDate")) for r in all_rows}, reverse=True):
        rows = [r for r in all_rows if str(r.get("raceDate")) == date_text]
        daily.append({"raceDate": date_text, "metrics": summarize(rows)})

    payload = {
        "schemaVersion": 1,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "forwardValidation": True,
        "prototypeLabels": True,
        "notes": [
            "成績は発走前に保存した予測と確定結果を突合して集計します。",
            "勝負度・荒れ度は現在のアプリ表示と同じ試作ルールです。研究で検証した正式版へ置き換える予定です。",
            "参考単勝回収指数はAI1位を毎レース1単位買った仮定であり、馬券推奨や利益保証ではありません。",
        ],
        "periods": periods,
        "byBattleGrade": by_battle,
        "byChaosLevel": by_chaos,
        "daily": daily,
        "recentRaces": list(reversed(all_rows[-60:])),
    }

    write_json(OUT_PATH, payload)
    print("performance races:", len(all_rows))
    print("today completed:", len(today_rows))
    print("last7 completed:", len(week_rows))
    print("output:", OUT_PATH)


if __name__ == "__main__":
    main()
