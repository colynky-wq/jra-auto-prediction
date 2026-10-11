# ============================================================
# KeibaAI GitHub Actions - one race production dry run
#
# Uses:
# - frozen 10-minute odds and expected output from RaceData.json
# - live race-entry page from netkeiba
# - production_history.parquet (23 columns)
# - portable feature_engine.py
# - fixed LightGBM model
#
# Read-only: does NOT modify RaceData.json or prediction files.
# ============================================================

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from feature_engine import ALLF, add_features, add_rating

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
MODEL_PATH = HERE / "lightgbm_fixed_2025.txt"
RACEDATA_PATH = REPO / "RaceData.json"

DEFAULT_DATE = "20261011"
DEFAULT_RACE_ID = "202608040406"  # Kyoto 6R; known research-candidate race
TEST_DATE8 = os.environ.get("KEIBAAI_DRYRUN_DATE", "").strip() or DEFAULT_DATE
TEST_RACE_ID = os.environ.get("KEIBAAI_DRYRUN_RACE_ID", "").strip() or DEFAULT_RACE_ID

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}


def fail(message: str) -> None:
    print()
    print("=" * 110)
    print("KEIBAAI ONE-RACE DRY RUN: FAILED")
    print("=" * 110)
    print(message)
    sys.exit(1)


def norm(value) -> str:
    s = unicodedata.normalize("NFKC", str(value or ""))
    s = re.sub(r"[\s\u3000]+", "", s)
    s = s.replace("▲", "").replace("△", "").replace("☆", "").replace("★", "")
    return s.strip()


def history_match(value, allowed: set[str], label: str) -> str:
    """Map current-page jockey/trainer spelling to one historical spelling."""
    if value in allowed:
        return value

    target = norm(value)
    exact = [x for x in allowed if norm(x) == target]
    if len(exact) == 1:
        return exact[0]

    # Conservative fallback for abbreviated foreign names.
    near = [x for x in allowed if norm(x).startswith(target) or target.startswith(norm(x))]
    near = [x for x in near if abs(len(norm(x)) - len(target)) <= 4]
    if len(near) == 1:
        return near[0]

    raise RuntimeError(
        f"{label} name could not be matched uniquely: {value!r} / candidates={near[:10]}"
    )


def load_expected_race() -> dict:
    with open(RACEDATA_PATH, "r", encoding="utf-8") as f:
        races = json.load(f)

    for race in races:
        if str(race.get("raceID")) == TEST_RACE_ID:
            return race

    fail(f"RaceData.json does not contain race {TEST_RACE_ID}")
    raise AssertionError


def derive_class(race_name: str) -> str:
    name = str(race_name)
    if "新馬" in name:
        return "新馬"
    if "未勝利" in name:
        return "未勝利"
    if "1勝" in name:
        return "1勝"
    if "2勝" in name:
        return "2勝"
    if "3勝" in name:
        return "3勝"
    return "OP"


def td_classes(td) -> set[str]:
    return {str(x).lower() for x in (td.get("class") or [])}


def latest_historical_age(hist: pd.DataFrame, horse_name: str, test_date: pd.Timestamp):
    """Fallback only when the current HTML does not expose sex/age text.

    JRA horse age advances by calendar year, so a prior observed age can be
    moved forward by the difference in calendar years.
    """
    d = (
        hist.loc[
            hist["馬名"].astype(str).eq(str(horse_name)),
            ["日付", "年齢"],
        ]
        .dropna(subset=["日付", "年齢"])
        .sort_values("日付")
    )

    if d.empty:
        return None

    last = d.iloc[-1]
    last_date = pd.Timestamp(last["日付"])
    last_age = int(last["年齢"])
    return last_age + int(test_date.year - last_date.year)


def parse_entry_page(
    expected_race: dict,
    hist: pd.DataFrame,
    test_date: pd.Timestamp,
) -> pd.DataFrame:
    """Parse only the actual shutuba table.

    Important: netkeiba pages can contain other `tr.HorseList` rows (for odds
    widgets etc.).  Selecting `tr.HorseList` globally can therefore pick a row
    that has horse name and odds but no sex/age or carried weight.  We first
    select the shutuba table and only then inspect horse rows inside it.
    """

    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={TEST_RACE_ID}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    print("entry HTTP:", r.status_code, "bytes:", len(r.content))

    if r.status_code != 200 or len(r.content) < 5000:
        fail(f"Could not fetch usable entry page: HTTP {r.status_code}")

    soup = BeautifulSoup(
        r.content.decode("utf-8", errors="replace"),
        "html.parser",
    )

    table = soup.select_one("table.Shutuba_Table") or soup.select_one("table.RaceTable01")
    if table is None:
        fail("Could not find Shutuba_Table / RaceTable01 in entry page")

    horse_rows = table.select("tr.HorseList")
    if not horse_rows:
        horse_rows = table.select("tr")

    print("entry table rows:", len(horse_rows))

    expected_by_name = {
        norm(h["name"]): h
        for h in expected_race["horses"]
    }

    rows = []
    age_history_fallbacks = []

    for tr in horse_rows:
        anchors = tr.find_all("a")

        expected = None
        horse_name = None

        for a in anchors:
            text = a.get_text(" ", strip=True)
            key = norm(text)
            if key in expected_by_name:
                horse_name = str(expected_by_name[key]["name"])
                expected = expected_by_name[key]
                break

        if expected is None:
            continue

        tds = tr.find_all("td")
        cell_texts = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip() for td in tds]
        row_text = " | ".join(cell_texts)

        # ----------------------------------------------------
        # Age
        # ----------------------------------------------------
        age = None

        # Prefer a dedicated sex/age cell such as `牡3` / `牝4`.
        for td, text in zip(tds, cell_texts):
            m = re.fullmatch(r"(?:牡|牝|セ|騸)\s*(\d+)", text)
            if m:
                age = int(m.group(1))
                break

            classes = td_classes(td)
            if any("barei" in c or "age" in c for c in classes):
                m = re.search(r"(?:牡|牝|セ|騸)?\s*(\d+)", text)
                if m:
                    age = int(m.group(1))
                    break

        if age is None:
            age = latest_historical_age(hist, horse_name, test_date)
            if age is not None:
                age_history_fallbacks.append(horse_name)

        if age is None:
            raise RuntimeError(
                f"Age could not be parsed or recovered from history: {horse_name} / {row_text[:400]}"
            )

        # ----------------------------------------------------
        # Jockey / trainer
        # ----------------------------------------------------
        jockey = None
        trainer = None
        jockey_td_index = None

        for a in anchors:
            href = str(a.get("href") or "").lower()
            text = a.get_text(" ", strip=True)

            if text and jockey is None and "jockey" in href:
                jockey = text
                parent_td = a.find_parent("td")
                if parent_td in tds:
                    jockey_td_index = tds.index(parent_td)

            if text and trainer is None and "trainer" in href:
                trainer = text

        # Class-based fallback.
        if not jockey or not trainer:
            for idx, (td, text) in enumerate(zip(tds, cell_texts)):
                classes = td_classes(td)

                if not jockey and any("jockey" in c for c in classes):
                    jockey = text
                    jockey_td_index = idx

                if not trainer and any("trainer" in c for c in classes):
                    trainer = text

        if not jockey or not trainer:
            raise RuntimeError(
                f"Jockey/trainer could not be parsed: {horse_name} / "
                f"jockey={jockey!r} trainer={trainer!r} / {row_text[:400]}"
            )

        # ----------------------------------------------------
        # Carried weight
        # ----------------------------------------------------
        carried = None

        # The carried-weight cell is normally before the jockey cell.
        search_indices = range(len(tds))
        if jockey_td_index is not None:
            search_indices = range(jockey_td_index - 1, -1, -1)

        for idx in search_indices:
            text = cell_texts[idx]
            if re.fullmatch(r"(?:4[5-9]|5\d|6[0-5])(?:\.\d)?", text):
                carried = float(text)
                break

        # Dedicated weight-class fallback, but reject 3-digit body weights.
        if carried is None:
            for td, text in zip(tds, cell_texts):
                classes = td_classes(td)
                if any("weight" in c for c in classes):
                    if re.fullmatch(r"(?:4[5-9]|5\d|6[0-5])(?:\.\d)?", text):
                        carried = float(text)
                        break

        if carried is None:
            raise RuntimeError(
                f"Carried weight could not be parsed: {horse_name} / {row_text[:400]}"
            )

        # ----------------------------------------------------
        # Body weight / change
        # ----------------------------------------------------
        body_weight = np.nan
        body_change = np.nan

        for text in cell_texts:
            m = re.search(r"(?<!\d)(\d{3})\s*\(\s*([+-]?\d+)\s*\)", text)
            if m:
                body_weight = float(m.group(1))
                body_change = float(m.group(2))
                break

        rows.append(
            {
                "馬番": int(expected["number"]),
                "馬名": horse_name,
                "年齢": int(age),
                "斤量": float(carried),
                "騎手_raw": str(jockey),
                "調教師_raw": str(trainer),
                "馬体重": body_weight,
                "増減": body_change,
            }
        )

    if age_history_fallbacks:
        print(
            "age recovered from pre-race history:",
            len(age_history_fallbacks),
            "/",
            age_history_fallbacks[:10],
        )

    df = pd.DataFrame(rows)

    if df.empty:
        fail("No expected horses were parsed from the shutuba table")

    df = df.sort_values("馬番").reset_index(drop=True)
    expected_count = len(expected_race["horses"])

    print("entry parsed:", len(df), "/", expected_count)

    if len(df) != expected_count:
        found = df[["馬番", "馬名"]].to_dict("records")
        fail(
            f"Entry count mismatch. parsed={len(df)} expected={expected_count} found={found}"
        )

    if df["馬番"].duplicated().any():
        fail("Duplicate horse numbers in parsed entry table")

    return df


def main() -> None:
    print("=" * 110)
    print("KeibaAI GitHub Actions - ONE RACE AI DRY RUN")
    print("JST:", datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"))
    print("date:", TEST_DATE8)
    print("race:", TEST_RACE_ID)
    print("mode: READ ONLY / RaceData.json is NOT modified")
    print("=" * 110)

    if not re.fullmatch(r"\d{8}", TEST_DATE8):
        fail(f"Invalid date: {TEST_DATE8}")

    if not re.fullmatch(r"\d{12}", TEST_RACE_ID):
        fail(f"Invalid race id: {TEST_RACE_ID}")

    for path in (HISTORY_PATH, MODEL_PATH, RACEDATA_PATH):
        if not path.exists():
            fail(f"Missing required file: {path}")

    race = load_expected_race()
    test_date = pd.Timestamp(datetime.strptime(TEST_DATE8, "%Y%m%d").date())

    print("race name:", race["raceName"])
    print("course:", race["course"])
    print("frozen 10-min odds horses:", len(race["horses"]))

    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    hist = hist.loc[hist["日付"].dt.normalize() < test_date].copy()

    print("history:", f"{len(hist):,}", "rows; latest:", hist["日付"].max())

    entries = parse_entry_page(
        expected_race=race,
        hist=hist,
        test_date=test_date,
    )

    hist_jockeys = set(hist["騎手"].dropna().astype(str))
    hist_trainers = set(hist["調教師"].dropna().astype(str))

    entries["騎手"] = entries["騎手_raw"].map(
        lambda x: history_match(x, hist_jockeys, "jockey")
    )

    entries["調教師"] = entries["調教師_raw"].map(
        lambda x: history_match(x, hist_trainers, "trainer")
    )

    latest_key = (
        hist.sort_values(["日付", "レースID", "馬番"])
        .dropna(subset=["馬名", "馬キー"])
        .drop_duplicates("馬名", keep="last")
        .set_index("馬名")["馬キー"]
        .astype(str)
        .to_dict()
    )

    entries["馬キー"] = [
        latest_key.get(name, f"{name}_{test_date.year - int(age)}")
        for name, age in zip(entries["馬名"], entries["年齢"])
    ]

    frozen = pd.DataFrame(race["horses"])[["number", "name", "odds"]].copy()
    frozen.columns = ["馬番", "RaceData馬名", "単勝オッズ"]

    current = entries.merge(
        frozen,
        on="馬番",
        how="inner",
        validate="one_to_one",
    )

    if len(current) != len(race["horses"]):
        fail("Entry/odds merge count mismatch")

    if not (
        current["馬名"].astype(str).values
        == current["RaceData馬名"].astype(str).values
    ).all():
        fail("Horse names differ between live entry page and frozen RaceData")

    current["人気"] = current["単勝オッズ"].rank(
        ascending=True,
        method="min",
    )

    course = str(race["course"])
    m = re.search(r"(芝|ダ|障)\s*(\d+)\s*m", course)
    if not m:
        fail(f"Course parse failed: {course}")

    surface = m.group(1)
    distance = int(m.group(2))

    if surface == "障":
        fail("Obstacle race is out of scope")

    race_class = derive_class(race["raceName"])

    cur = pd.DataFrame(index=np.arange(len(current)))
    cur["レースID"] = TEST_RACE_ID
    cur["日付"] = test_date

    for col in [
        "馬番",
        "馬名",
        "馬キー",
        "年齢",
        "斤量",
        "騎手",
        "調教師",
        "人気",
        "単勝オッズ",
        "馬体重",
        "増減",
    ]:
        cur[col] = current[col].to_numpy()

    cur["着順"] = np.nan
    cur["出典"] = "当日予測_GitHub_DRYRUN"
    cur["芝ダ"] = surface
    cur["距離"] = distance
    cur["クラス"] = race_class
    cur["障害"] = False
    cur["タイム秒"] = np.nan
    cur["上がり3F"] = np.nan
    cur["初角"] = np.nan
    cur["頭数"] = len(cur)

    work = pd.concat([hist, cur], ignore_index=True)
    work["日付"] = pd.to_datetime(work["日付"], errors="coerce")
    work = work.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)

    print("feature input:", f"{len(work):,}")
    print("add_rating...")
    work = add_rating(work)
    print("add_features...")
    work = add_features(work)

    pred = (
        work.loc[
            (work["日付"].dt.normalize() == test_date)
            & (work["レースID"].astype(str) == TEST_RACE_ID)
        ]
        .copy()
        .sort_values("馬番")
        .reset_index(drop=True)
    )

    if len(pred) != len(cur):
        fail(f"Prediction-row count mismatch: {len(pred)} / {len(cur)}")

    if int(pred["対象"].fillna(False).sum()) != len(pred):
        fail("Some horses became prediction-ineligible")

    if int(pred[ALLF].isna().sum().sum()) != 0:
        fail("37 model features contain missing values")

    X = pred[ALLF].astype(np.float32)

    if not np.isfinite(X.to_numpy()).all():
        fail("37 model features contain inf/-inf")

    model = lgb.Booster(model_file=str(MODEL_PATH))

    if list(model.feature_name()) != list(ALLF):
        fail("Model feature order differs from feature_engine.ALLF")

    tree = model.predict(
        X,
        raw_score=True,
        validate_features=True,
    )

    market_logit = pred["市場ロジット"].to_numpy(dtype=float)
    final_logit = market_logit + tree

    p_raw = 1.0 / (
        1.0
        + np.exp(
            -np.clip(
                final_logit,
                -50,
                50,
            )
        )
    )

    ai = p_raw / p_raw.sum()
    market = pred["市場確率"].to_numpy(dtype=float)
    ratio = ai / market
    ev = ai * pred["単勝オッズ"].to_numpy(dtype=float) * 100

    result = pd.DataFrame(
        {
            "馬番": pred["馬番"].astype(int),
            "馬名": pred["馬名"].astype(str),
            "単勝オッズ": pred["単勝オッズ"].astype(float),
            "市場勝率(%)": market * 100,
            "AI勝率(%)": ai * 100,
            "AI÷市場": ratio,
            "期待払戻指数": ev,
            "研究候補": (ratio >= 1.20) & (ev >= 100),
        }
    )

    expected = pd.DataFrame(race["horses"])[
        [
            "number",
            "name",
            "marketProbability",
            "aiProbability",
            "aiMarketRatio",
            "expectedIndex",
        ]
    ].copy()

    expected.columns = [
        "馬番",
        "馬名_expected",
        "市場_expected",
        "AI_expected",
        "ratio_expected",
        "EV_expected",
    ]

    cmp = result.merge(
        expected,
        on="馬番",
        how="left",
        validate="one_to_one",
    )

    cmp["AI差pt"] = (cmp["AI勝率(%)"] - cmp["AI_expected"]).abs()
    cmp["ratio差"] = (cmp["AI÷市場"] - cmp["ratio_expected"]).abs()
    cmp["EV差"] = (cmp["期待払戻指数"] - cmp["EV_expected"]).abs()

    print()
    print("=" * 110)
    print("DRY-RUN PREDICTION (research only; frozen 10-minute odds)")
    print("=" * 110)

    display_cols = [
        "馬番",
        "馬名",
        "単勝オッズ",
        "市場勝率(%)",
        "AI勝率(%)",
        "AI÷市場",
        "期待払戻指数",
        "研究候補",
    ]

    print(
        result[display_cols]
        .round(
            {
                "市場勝率(%)": 3,
                "AI勝率(%)": 3,
                "AI÷市場": 4,
                "期待払戻指数": 3,
            }
        )
        .to_string(index=False)
    )

    ai_max = float(cmp["AI差pt"].max())
    ratio_max = float(cmp["ratio差"].max())
    ev_max = float(cmp["EV差"].max())

    expected_candidates = set(
        expected.loc[
            (expected["ratio_expected"] >= 1.20)
            & (expected["EV_expected"] >= 100),
            "馬番",
        ].astype(int)
    )

    actual_candidates = set(
        result.loc[result["研究候補"], "馬番"].astype(int)
    )

    print()
    print("comparison with published RaceData.json")
    print("AI probability max diff (percentage points):", ai_max)
    print("AI/market max diff:", ratio_max)
    print("expected-index max diff:", ev_max)
    print("expected research candidates:", sorted(expected_candidates))
    print("actual research candidates  :", sorted(actual_candidates))

    # RaceData values are rounded for the app, so allow only rounding-sized differences.
    numeric_ok = (
        ai_max <= 0.002
        and ratio_max <= 0.0002
        and ev_max <= 0.002
    )

    candidate_ok = actual_candidates == expected_candidates

    print()
    print("=" * 110)

    if numeric_ok and candidate_ok:
        print("★★★★★ KEIBAAI ONE-RACE DRY RUN: SUCCESS")
    else:
        print("KEIBAAI ONE-RACE DRY RUN: MISMATCH")

    print("=" * 110)
    print("race:", TEST_RACE_ID, race["raceName"], race["course"])
    print("horses:", len(result))
    print("37 features finite: True")
    print("fixed LightGBM: OK")
    print("research-candidate match:", candidate_ok)
    print("RaceData.json modified: NO")
    print("prediction files modified: NO")

    if not numeric_ok:
        fail(
            "Prediction values differ from the already-published 10-minute RaceData beyond rounding tolerance"
        )

    if not candidate_ok:
        fail("Research-candidate set differs from the already-published RaceData")


if __name__ == "__main__":
    main()
