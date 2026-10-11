# ============================================================
# KeibaAI - forward result aggregator
#
# Purpose
# - Read the currently published RaceData.json predictions.
# - Confirm they belong to the requested JRA date.
# - Fetch completed race results from netkeiba.
# - Join finish positions / final win odds to the 10-minute predictions.
# - Aggregate research-candidate performance automatically.
#
# Outputs
#   reports/YYYYMMDD/predictions_snapshot.json
#   reports/YYYYMMDD/all_predictions.csv
#   reports/YYYYMMDD/candidates.csv
#   reports/YYYYMMDD/summary.json
#   reports/YYYYMMDD/summary.md
#   reports/forward_candidate_ledger.csv
#
# Research-candidate rule is NOT changed:
#   AI/market >= 1.20 AND expected index >= 100
# ============================================================

from __future__ import annotations

import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RACEDATA_PATH = REPO / "RaceData.json"
REPORTS_DIR = REPO / "reports"
LEDGER_PATH = REPORTS_DIR / "forward_candidate_ledger.csv"

TARGET_DATE8 = (
    os.environ.get("KEIBAAI_AGGREGATE_DATE", "").strip()
    or datetime.now(JST).strftime("%Y%m%d")
)

CANDIDATE_RATIO_MIN = 1.20
CANDIDATE_EXPECTED_MIN = 100.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


class SkipAggregation(RuntimeError):
    pass


def fail(message: str) -> None:
    print()
    print("=" * 110)
    print("KEIBAAI FORWARD AGGREGATION: FAILED")
    print("=" * 110)
    print(message)
    sys.exit(1)


def normalize_race_id(x) -> str:
    s = str(x).strip()
    s = re.sub(r"\.0$", "", s)
    return s.zfill(12)


def load_predictions() -> list[dict]:
    if not RACEDATA_PATH.exists():
        fail(f"RaceData.json not found: {RACEDATA_PATH}")

    with RACEDATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list) or not data:
        fail("RaceData.json is empty or is not a list")

    return data


def fetch_day_race_ids(date8: str) -> set[str]:
    url = "https://race.netkeiba.com/top/race_list_sub.html"
    r = SESSION.get(url, params={"kaisai_date": date8}, timeout=30)
    print("race-list HTTP:", r.status_code, "bytes:", len(r.content), "url:", r.url)
    if r.status_code != 200:
        raise RuntimeError(f"race list HTTP {r.status_code}")

    text = r.content.decode("utf-8", errors="replace")
    ids = set(re.findall(r"race_id=(\d{12})", text))
    print("race ids for target date:", len(ids))
    return ids


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        cols = []
        for tup in out.columns:
            parts = [str(x).strip() for x in tup if str(x).strip() and str(x) != "nan"]
            cols.append(" ".join(dict.fromkeys(parts)))
        out.columns = cols
    else:
        out.columns = [str(x).strip() for x in out.columns]
    return out


def find_col(columns, *, exact=(), contains=()):
    cols = list(columns)
    for wanted in exact:
        for c in cols:
            if c == wanted:
                return c
    for wanted in contains:
        for c in cols:
            if wanted in c:
                return c
    return None


def parse_finish(value):
    s = str(value).strip()
    m = re.match(r"^(\d+)", s)
    if m:
        return int(m.group(1)), "確定"
    if not s or s.lower() == "nan":
        return np.nan, "不明"
    return np.nan, s


def fetch_result_table(race_id: str) -> pd.DataFrame:
    url = "https://race.netkeiba.com/race/result.html"
    r = SESSION.get(url, params={"race_id": race_id}, timeout=30)
    print("result HTTP:", r.status_code, "bytes:", len(r.content), "race:", race_id)
    if r.status_code != 200:
        raise RuntimeError(f"result HTTP {r.status_code}: {race_id}")

    html = r.content.decode("utf-8", errors="replace")

    try:
        tables = pd.read_html(io.StringIO(html))
    except Exception as e:
        raise RuntimeError(f"result table parse failed: {race_id}: {e}") from e

    result = None
    for raw in tables:
        t = flatten_columns(raw)
        cols = list(t.columns)
        if (
            find_col(cols, exact=("着順",), contains=("着順",))
            and find_col(cols, exact=("馬番",), contains=("馬番",))
            and find_col(cols, exact=("馬名",), contains=("馬名",))
        ):
            result = t
            break

    if result is None:
        raise RuntimeError(f"race result table not found: {race_id}")

    finish_col = find_col(result.columns, exact=("着順",), contains=("着順",))
    no_col = find_col(result.columns, exact=("馬番",), contains=("馬番",))
    name_col = find_col(result.columns, exact=("馬名",), contains=("馬名",))
    odds_col = find_col(
        result.columns,
        exact=("単勝オッズ", "単勝"),
        contains=("単勝オッズ", "単勝"),
    )
    pop_col = find_col(result.columns, exact=("人気",), contains=("人気",))

    if not all([finish_col, no_col, name_col]):
        raise RuntimeError(f"required result columns missing: {race_id}: {list(result.columns)}")

    rows = []
    for _, row in result.iterrows():
        no = pd.to_numeric(pd.Series([row[no_col]]), errors="coerce").iloc[0]
        if not np.isfinite(no):
            continue

        finish, status = parse_finish(row[finish_col])
        final_odds = np.nan
        final_pop = np.nan

        if odds_col is not None:
            final_odds = pd.to_numeric(pd.Series([row[odds_col]]), errors="coerce").iloc[0]
        if pop_col is not None:
            final_pop = pd.to_numeric(pd.Series([row[pop_col]]), errors="coerce").iloc[0]

        rows.append(
            {
                "raceID": race_id,
                "number": int(no),
                "resultHorseName": str(row[name_col]).strip(),
                "finish": finish,
                "resultStatus": status,
                "finalOdds": final_odds,
                "finalPopularity": final_pop,
            }
        )

    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError(f"no result rows parsed: {race_id}")

    d = d.sort_values("number").drop_duplicates("number", keep="last").reset_index(drop=True)
    return d


def candidate_bucket(n: int) -> str:
    if n <= 0:
        return "0"
    if n == 1:
        return "1"
    if n == 2:
        return "2"
    return "3+"


def safe_rate(num: int, den: int):
    if den <= 0:
        return None
    return float(num / den * 100.0)


def maybe_round(x, n=3):
    if x is None:
        return None
    try:
        if not np.isfinite(float(x)):
            return None
        return round(float(x), n)
    except Exception:
        return None


def main() -> None:
    print("=" * 110)
    print("KeibaAI Forward Result Aggregator")
    print("JST:", datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"))
    print("target date:", TARGET_DATE8)
    print("candidate rule: AI/market >= 1.20 AND expected index >= 100")
    print("=" * 110)

    if not re.fullmatch(r"\d{8}", TARGET_DATE8):
        fail(f"invalid target date: {TARGET_DATE8}")

    predictions = load_predictions()
    prediction_race_ids = [normalize_race_id(r.get("raceID", "")) for r in predictions]
    prediction_race_ids = [x for x in prediction_race_ids if re.fullmatch(r"\d{12}", x)]

    if not prediction_race_ids:
        fail("RaceData.json contains no valid 12-digit race IDs")

    try:
        day_ids = fetch_day_race_ids(TARGET_DATE8)
    except Exception as e:
        fail(str(e))

    overlap = sorted(set(prediction_race_ids) & day_ids)
    print("RaceData races:", len(set(prediction_race_ids)))
    print("target-date overlap:", len(overlap))

    if not overlap:
        print()
        print("SKIP: current RaceData.json does not belong to the target date.")
        print("No report files were changed.")
        return

    if set(prediction_race_ids) != set(overlap):
        fail(
            "RaceData.json is mixed/stale for the requested date. "
            f"RaceData={len(set(prediction_race_ids))}, overlap={len(overlap)}"
        )

    flat_rows = []
    for race in predictions:
        rid = normalize_race_id(race.get("raceID", ""))
        if rid not in day_ids:
            continue

        horses = race.get("horses") or []
        candidate_count = sum(
            1
            for h in horses
            if float(h.get("aiMarketRatio", np.nan)) >= CANDIDATE_RATIO_MIN
            and float(h.get("expectedIndex", np.nan)) >= CANDIDATE_EXPECTED_MIN
        )

        for h in horses:
            ratio = pd.to_numeric(pd.Series([h.get("aiMarketRatio")]), errors="coerce").iloc[0]
            expected = pd.to_numeric(pd.Series([h.get("expectedIndex")]), errors="coerce").iloc[0]
            is_candidate = bool(
                np.isfinite(ratio)
                and np.isfinite(expected)
                and float(ratio) >= CANDIDATE_RATIO_MIN
                and float(expected) >= CANDIDATE_EXPECTED_MIN
            )

            flat_rows.append(
                {
                    "date": TARGET_DATE8,
                    "raceID": rid,
                    "venue": race.get("venue"),
                    "raceNumber": race.get("raceNumber"),
                    "startTime": race.get("startTime"),
                    "raceName": race.get("raceName"),
                    "course": race.get("course"),
                    "number": int(h.get("number")),
                    "horseName": str(h.get("name")),
                    "tenMinOdds": pd.to_numeric(pd.Series([h.get("odds")]), errors="coerce").iloc[0],
                    "marketProbabilityPct": pd.to_numeric(pd.Series([h.get("marketProbability")]), errors="coerce").iloc[0],
                    "aiProbabilityPct": pd.to_numeric(pd.Series([h.get("aiProbability")]), errors="coerce").iloc[0],
                    "aiMarketRatio": ratio,
                    "expectedIndex": expected,
                    "researchCandidate": is_candidate,
                    "candidateCountInRace": int(candidate_count),
                    "candidateRaceBucket": candidate_bucket(candidate_count),
                }
            )

    pred_df = pd.DataFrame(flat_rows)
    if pred_df.empty:
        fail("no prediction rows were built")

    result_frames = []
    result_failures = []

    for rid in sorted(pred_df["raceID"].unique()):
        try:
            result_frames.append(fetch_result_table(str(rid)))
        except Exception as e:
            print("RESULT ERROR:", rid, repr(e))
            result_failures.append((str(rid), str(e)))

    if result_failures:
        fail(
            "some published races do not have parseable completed results yet:\n"
            + "\n".join(f"{rid}: {msg}" for rid, msg in result_failures)
        )

    results = pd.concat(result_frames, ignore_index=True)
    merged = pred_df.merge(results, on=["raceID", "number"], how="left", validate="one_to_one")

    if merged["resultStatus"].isna().any():
        bad = merged.loc[merged["resultStatus"].isna(), ["raceID", "number", "horseName"]]
        fail("prediction/result merge missing rows:\n" + bad.to_string(index=False))

    # Name check: normalize spaces only; horse number is the authoritative join key.
    merged["nameMatch"] = (
        merged["horseName"].astype(str).str.replace(r"\s+", "", regex=True)
        == merged["resultHorseName"].astype(str).str.replace(r"\s+", "", regex=True)
    )

    merged["win"] = merged["finish"].eq(1)
    merged["top3"] = merged["finish"].le(3) & merged["finish"].notna()
    merged["oddsChangePct"] = np.where(
        merged["tenMinOdds"].gt(0) & merged["finalOdds"].gt(0),
        (merged["finalOdds"] / merged["tenMinOdds"] - 1.0) * 100.0,
        np.nan,
    )
    merged["oddsMove"] = np.select(
        [
            merged["oddsChangePct"] < -0.1,
            merged["oddsChangePct"] > 0.1,
            merged["oddsChangePct"].notna(),
        ],
        ["短縮", "上昇", "ほぼ同じ"],
        default="不明",
    )

    candidates = merged.loc[merged["researchCandidate"]].copy()

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------
    candidate_races = int(candidates["raceID"].nunique()) if not candidates.empty else 0
    candidate_horses = int(len(candidates))
    wins = int(candidates["win"].sum()) if not candidates.empty else 0
    top3 = int(candidates["top3"].sum()) if not candidates.empty else 0

    odds_valid = candidates.loc[candidates["oddsChangePct"].notna()].copy()
    shortened = int((odds_valid["oddsChangePct"] < -0.1).sum())
    drifted = int((odds_valid["oddsChangePct"] > 0.1).sum())
    unchanged = int((odds_valid["oddsChangePct"].abs() <= 0.1).sum())

    groups = []
    if not candidates.empty:
        for bucket in ["1", "2", "3+"]:
            g = candidates.loc[candidates["candidateRaceBucket"].eq(bucket)].copy()
            if g.empty:
                continue
            groups.append(
                {
                    "candidateCountBucket": bucket,
                    "races": int(g["raceID"].nunique()),
                    "candidateHorses": int(len(g)),
                    "wins": int(g["win"].sum()),
                    "top3": int(g["top3"].sum()),
                    "winRatePct": maybe_round(safe_rate(int(g["win"].sum()), len(g))),
                    "top3RatePct": maybe_round(safe_rate(int(g["top3"].sum()), len(g))),
                    "averageFinish": maybe_round(g["finish"].mean()),
                    "averageOddsChangePct": maybe_round(g["oddsChangePct"].mean()),
                }
            )

    summary = {
        "date": TARGET_DATE8,
        "generatedAtJST": datetime.now(JST).isoformat(),
        "candidateRule": {
            "aiMarketRatioMin": CANDIDATE_RATIO_MIN,
            "expectedIndexMin": CANDIDATE_EXPECTED_MIN,
        },
        "publishedRaces": int(merged["raceID"].nunique()),
        "publishedHorses": int(len(merged)),
        "candidateRaces": candidate_races,
        "candidateHorses": candidate_horses,
        "wins": wins,
        "top3": top3,
        "winRatePct": maybe_round(safe_rate(wins, candidate_horses)),
        "top3RatePct": maybe_round(safe_rate(top3, candidate_horses)),
        "oddsMovement": {
            "validCandidates": int(len(odds_valid)),
            "shortened": shortened,
            "drifted": drifted,
            "unchanged": unchanged,
            "averageChangePct": maybe_round(odds_valid["oddsChangePct"].mean()),
            "medianChangePct": maybe_round(odds_valid["oddsChangePct"].median()),
        },
        "byCandidateCountInRace": groups,
    }

    # --------------------------------------------------------
    # Write date report
    # --------------------------------------------------------
    day_dir = REPORTS_DIR / TARGET_DATE8
    day_dir.mkdir(parents=True, exist_ok=True)

    with (day_dir / "predictions_snapshot.json").open("w", encoding="utf-8") as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2, allow_nan=False)

    merged.sort_values(["startTime", "raceID", "number"]).to_csv(
        day_dir / "all_predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )
    candidates.sort_values(["startTime", "raceID", "number"]).to_csv(
        day_dir / "candidates.csv",
        index=False,
        encoding="utf-8-sig",
    )

    with (day_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, allow_nan=False)

    # --------------------------------------------------------
    # Cumulative candidate ledger (upsert by date/race/horse)
    # --------------------------------------------------------
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ledger_cols = [
        "date", "raceID", "venue", "raceNumber", "startTime", "raceName", "course",
        "number", "horseName", "tenMinOdds", "finalOdds", "oddsChangePct", "oddsMove",
        "marketProbabilityPct", "aiProbabilityPct", "aiMarketRatio", "expectedIndex",
        "candidateCountInRace", "candidateRaceBucket", "finish", "resultStatus", "win", "top3",
    ]
    new_ledger = candidates[ledger_cols].copy()

    if LEDGER_PATH.exists():
        old = pd.read_csv(LEDGER_PATH, dtype={"date": str, "raceID": str})
        combined = pd.concat([old, new_ledger], ignore_index=True)
    else:
        combined = new_ledger

    if not combined.empty:
        combined["date"] = combined["date"].astype(str)
        combined["raceID"] = combined["raceID"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(12)
        combined = (
            combined.sort_values(["date", "raceID", "number"])
            .drop_duplicates(["date", "raceID", "number"], keep="last")
            .reset_index(drop=True)
        )
    combined.to_csv(LEDGER_PATH, index=False, encoding="utf-8-sig")

    # --------------------------------------------------------
    # Markdown summary
    # --------------------------------------------------------
    lines = []
    lines.append(f"# KeibaAI Forward Summary {TARGET_DATE8}")
    lines.append("")
    lines.append("研究用のフォワード検証集計です。実賭け判断には使用しません。")
    lines.append("")
    lines.append("## 全体")
    lines.append("")
    lines.append(f"- 公開レース: {summary['publishedRaces']}")
    lines.append(f"- 研究候補が出たレース: {summary['candidateRaces']}")
    lines.append(f"- 研究候補: {summary['candidateHorses']}頭")
    lines.append(f"- 1着: {summary['wins']}頭")
    lines.append(f"- 3着以内: {summary['top3']}頭")
    lines.append(f"- 1着率: {summary['winRatePct'] if summary['winRatePct'] is not None else '-'}%")
    lines.append(f"- 3着内率: {summary['top3RatePct'] if summary['top3RatePct'] is not None else '-'}%")
    lines.append("")
    lines.append("## 10分前オッズ → 最終オッズ")
    lines.append("")
    lines.append(f"- オッズ短縮: {shortened}頭")
    lines.append(f"- オッズ上昇: {drifted}頭")
    lines.append(f"- ほぼ同じ: {unchanged}頭")
    lines.append(f"- 平均変化率: {summary['oddsMovement']['averageChangePct']}%")
    lines.append(f"- 中央変化率: {summary['oddsMovement']['medianChangePct']}%")
    lines.append("")
    lines.append("## 1レース内の研究候補数別")
    lines.append("")
    lines.append("| 候補数 | レース数 | 候補頭数 | 1着 | 3着以内 | 1着率 | 3着内率 | 平均着順 | 平均オッズ変化 |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for g in groups:
        lines.append(
            f"| {g['candidateCountBucket']} | {g['races']} | {g['candidateHorses']} | {g['wins']} | {g['top3']} | "
            f"{g['winRatePct']}% | {g['top3RatePct']}% | {g['averageFinish']} | {g['averageOddsChangePct']}% |"
        )

    lines.append("")
    lines.append("## 研究候補")
    lines.append("")
    lines.append("| レース | 馬番 | 馬名 | 10分前 | 最終 | 変化 | AI÷市場 | 期待指数 | 着順 |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|")
    for _, r in candidates.sort_values(["startTime", "raceID", "number"]).iterrows():
        finish_text = "-" if pd.isna(r["finish"]) else str(int(r["finish"]))
        final_text = "-" if pd.isna(r["finalOdds"]) else f"{float(r['finalOdds']):.1f}"
        change_text = "-" if pd.isna(r["oddsChangePct"]) else f"{float(r['oddsChangePct']):+.1f}%"
        lines.append(
            f"| {r['venue']}{int(r['raceNumber'])}R | {int(r['number'])} | {r['horseName']} | "
            f"{float(r['tenMinOdds']):.1f} | {final_text} | {change_text} | "
            f"{float(r['aiMarketRatio']):.3f} | {float(r['expectedIndex']):.1f} | {finish_text} |"
        )

    (day_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    print("=" * 110)
    print("KEIBAAI FORWARD AGGREGATION: SUCCESS")
    print("=" * 110)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("report dir:", day_dir)
    print("candidate ledger:", LEDGER_PATH)


if __name__ == "__main__":
    main()
