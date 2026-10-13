# ============================================================
# KeibaAI GitHub Actions - one race production dry run
#
# Replays one already-published 10-minute prediction with:
# - frozen 10-minute win odds from RaceData.json
# - the SAME JRA official entry-table parser used by the 2026-10-11
#   Colab forward run
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
from urllib.parse import urlencode

import lightgbm as lgb
import numpy as np
import pandas as pd

from horse_identity_runtime import assign_horse_keys
import requests
from bs4 import BeautifulSoup

from feature_engine import ALLF, add_features, add_rating


# ============================================================
# 1. Paths / test target
# ============================================================

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

JRA_HOST = "https://www.jra.go.jp"
ACCESS_D = JRA_HOST + "/JRADB/accessD.html"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.8",
        "Cache-Control": "no-cache",
    }
)


# ============================================================
# 2. Helpers
# ============================================================

def jst_now() -> datetime:
    return datetime.now(JST)


def fail(message: str) -> None:
    print()
    print("=" * 110)
    print("KEIBAAI ONE-RACE DRY RUN: FAILED")
    print("=" * 110)
    print(message)
    sys.exit(1)


def compact_name(x) -> str:
    """Exact name normalizer used by the 2026-10-11 forward notebook."""
    if pd.isna(x):
        return ""

    s = unicodedata.normalize("NFKC", str(x))

    # Apprentice/display symbols used on JRA pages.
    s = re.sub(r"^[☆★△▲◇]", "", s)

    # Spaces, Japanese middle dot, and dots.
    s = re.sub(r"[\s・\.．]", "", s)

    return s.strip()


def match_history_name(current_name, history_values):
    """Exact matching rule used by the 2026-10-11 forward notebook."""
    cn = compact_name(current_name)

    if not cn:
        return None

    candidates = []

    for hv in history_values:
        hn = compact_name(hv)

        if not hn:
            continue

        # Exact match.
        if cn == hn:
            candidates.append((100000 + len(hn), hv))
            continue

        # Prefix-style abbreviation matching.
        if cn.startswith(hn):
            candidates.append((50000 + len(hn), hv))
            continue

        # Foreign-name abbreviation matching.
        if hn in cn:
            candidates.append((10000 + len(hn), hv))

    if not candidates:
        return None

    candidates.sort(key=lambda z: z[0], reverse=True)
    return candidates[0][1]


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


# ============================================================
# 3. JRA official entry URL
# ============================================================

def make_direct_entry_url(race_id: str) -> str:
    """Same fallback URL construction used by the formal Colab forward run."""
    rid = str(race_id).zfill(12)

    year = rid[0:4]
    venue = rid[4:6]
    meeting = rid[6:8]
    day = rid[8:10]
    race_no = rid[10:12]

    cname = (
        "pw01dde01"
        + venue
        + year
        + meeting
        + day
        + race_no
        + TEST_DATE8
    )

    return ACCESS_D + "?" + urlencode({"CNAME": cname})


# ============================================================
# 4. JRA official entry parser
# ============================================================

def fetch_entry_table(race_id: str):
    """Port of the JRA official parser used by the formal 10-minute run."""
    url = make_direct_entry_url(race_id)
    last_error = None

    try:
        r = SESSION.get(url, timeout=20)
        fetch_time = jst_now()

        print("JRA entry URL:", url)
        print("JRA entry HTTP:", r.status_code, "bytes:", len(r.content))

        if r.status_code != 200:
            raise RuntimeError(f"HTTP={r.status_code}")

        enc = r.apparent_encoding or "shift_jis"
        text = r.content.decode(enc, errors="replace")

        soup = BeautifulSoup(text, "html.parser")
        table = soup.find("table")

        if table is None:
            raise RuntimeError("tableなし")

        rows = []

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")

            if len(tds) < 4:
                continue

            # ----------------------------------------------------
            # Horse number
            # ----------------------------------------------------
            uma_raw = tds[1].get_text(" ", strip=True)
            um = re.search(r"\d+", uma_raw)

            if not um:
                continue

            uma = int(um.group())

            if uma < 1 or uma > 18:
                continue

            # ----------------------------------------------------
            # Horse information cell
            # ----------------------------------------------------
            info_td = tds[2]
            info_text = re.sub(
                r"\s+",
                " ",
                info_td.get_text(" ", strip=True),
            )

            links = [
                a.get_text(" ", strip=True)
                for a in info_td.find_all("a")
                if a.get_text(" ", strip=True)
            ]

            horse_name = links[0] if links else ""

            if not horse_name:
                m_name = re.match(
                    r"^(.*?)\s*\d+(?:\.\d+)?\s*\(\s*\d+番人気",
                    info_text,
                )

                if m_name:
                    horse_name = m_name.group(1).strip()

            if not horse_name:
                continue

            # ----------------------------------------------------
            # Body weight / change
            # ----------------------------------------------------
            m_weight = re.search(
                r"(\d{3})\s*kg\s*\(\s*([+-]?\d+)\s*\)",
                info_text,
            )

            if m_weight:
                body_weight = int(m_weight.group(1))
                diff = int(m_weight.group(2))
            else:
                body_weight = np.nan
                diff = np.nan

            # ----------------------------------------------------
            # Trainer
            # ----------------------------------------------------
            trainer_full = None

            affiliation_node = info_td.find(
                string=re.compile(r"\(\s*(美浦|栗東)\s*\)")
            )

            if affiliation_node is not None:
                trainer_a = affiliation_node.find_previous("a")

                if trainer_a is not None and info_td in trainer_a.parents:
                    trainer_full = trainer_a.get_text(" ", strip=True)

            # ----------------------------------------------------
            # Sex/age, carried weight, jockey
            # ----------------------------------------------------
            sex_td = tds[3]
            sex_text = re.sub(
                r"\s+",
                " ",
                sex_td.get_text(" ", strip=True),
            )

            m_age = re.search(
                r"(牡|牝|せん|セン|セ)\s*(\d+)",
                sex_text,
            )

            if not m_age:
                raise RuntimeError(
                    f"性齢解析失敗 馬番{uma}: {sex_text}"
                )

            age = int(m_age.group(2))

            m_kg = re.search(
                r"(\d+(?:\.\d+)?)\s*kg",
                sex_text,
            )

            if not m_kg:
                raise RuntimeError(
                    f"斤量解析失敗 馬番{uma}: {sex_text}"
                )

            carried_weight = float(m_kg.group(1))

            jockey_links = [
                a.get_text(" ", strip=True)
                for a in sex_td.find_all("a")
                if a.get_text(" ", strip=True)
            ]

            if jockey_links:
                jockey_full = jockey_links[-1]
            else:
                jockey_full = re.sub(
                    r"^.*?\d+(?:\.\d+)?\s*kg",
                    "",
                    sex_text,
                ).strip()

            rows.append(
                {
                    "レースID": race_id,
                    "馬番": uma,
                    "馬名": horse_name,
                    "年齢": age,
                    "斤量": carried_weight,
                    "騎手_JRA": jockey_full,
                    "調教師_JRA": trainer_full,
                    "馬体重": body_weight,
                    "増減": diff,
                }
            )

        d = pd.DataFrame(rows)

        if d.empty:
            raise RuntimeError("出走馬解析0頭")

        d = (
            d.sort_values("馬番")
            .drop_duplicates("馬番", keep="last")
            .reset_index(drop=True)
        )

        return d, fetch_time, url

    except Exception as e:
        last_error = str(e)

    raise RuntimeError(
        f"JRA出馬表取得失敗: {race_id} / {last_error}"
    )


# ============================================================
# 5. Known formal inputs for the default replay race
#    (diagnostic only; prediction never uses these constants)
# ============================================================

KNOWN_DEFAULT_INPUTS = {
    1: ("ブラックレジェンド", "今村聖", 452.0, 4.0),
    2: ("ココロヅヨサ", "北村友一", 458.0, -4.0),
    3: ("キタノシャカール", "松若風馬", 428.0, -4.0),
    4: ("ミヤジオズ", "田口貫", 502.0, 12.0),
    5: ("ブルーストレイル", "城戸義政", 470.0, 6.0),
    6: ("ルファーダ", "幸英明", 494.0, 0.0),
    7: ("アルビオン", "西塚洸", 462.0, -4.0),
    8: ("コルヴァス", "坂井瑠星", 510.0, 16.0),
    9: ("クリュギスト", "松山弘平", 506.0, 8.0),
    10: ("ジャスティンダラス", "高杉吏", 538.0, 18.0),
    11: ("ゴールデンスラム", "小沢大仁", 492.0, 6.0),
    12: ("メイショウオグマ", "国分恭介", 494.0, 10.0),
    13: ("ダイメイクマモト", "太宰啓介", 482.0, 6.0),
    14: ("レザーアンドレース", "田山旺", 472.0, 0.0),
    15: ("ブレイジングスター", "酒井学", 488.0, 0.0),
    16: ("ハイロード", "斎藤新", 482.0, 32.0),
}


def check_known_default_inputs(entries: pd.DataFrame) -> None:
    if TEST_DATE8 != DEFAULT_DATE or TEST_RACE_ID != DEFAULT_RACE_ID:
        return

    mismatches = []

    for _, row in entries.sort_values("馬番").iterrows():
        no = int(row["馬番"])
        expected = KNOWN_DEFAULT_INPUTS.get(no)

        if expected is None:
            mismatches.append(f"馬番{no}: known input missing")
            continue

        exp_name, exp_jockey, exp_weight, exp_diff = expected

        if str(row["馬名"]) != exp_name:
            mismatches.append(
                f"馬番{no} 馬名 {row['馬名']!r} != {exp_name!r}"
            )

        if str(row["騎手"]) != exp_jockey:
            mismatches.append(
                f"馬番{no} 騎手 {row['騎手']!r} != {exp_jockey!r}"
            )

        weight = pd.to_numeric(pd.Series([row["馬体重"]]), errors="coerce").iloc[0]
        diff = pd.to_numeric(pd.Series([row["増減"]]), errors="coerce").iloc[0]

        if not np.isfinite(weight) or abs(float(weight) - exp_weight) > 1e-12:
            mismatches.append(
                f"馬番{no} 馬体重 {row['馬体重']!r} != {exp_weight}"
            )

        if not np.isfinite(diff) or abs(float(diff) - exp_diff) > 1e-12:
            mismatches.append(
                f"馬番{no} 増減 {row['増減']!r} != {exp_diff}"
            )

    print("formal-input replay check:", "OK" if not mismatches else "MISMATCH")

    if mismatches:
        for x in mismatches[:30]:
            print("  ", x)
        fail("JRA official entry inputs differ from the saved formal Kyoto 6R inputs")


# ============================================================
# 6. Main replay
# ============================================================

def main() -> None:
    print("=" * 110)
    print("KeibaAI GitHub Actions - ONE RACE AI DRY RUN")
    print("entry source: JRA OFFICIAL (same parser as formal Colab run)")
    print("JST:", jst_now().strftime("%Y-%m-%d %H:%M:%S"))
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

    # Production history is forward-safe; for replay we still explicitly
    # exclude the replay date and anything after it.
    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    hist = hist.loc[hist["日付"].dt.normalize() < test_date].copy()

    print("history:", f"{len(hist):,}", "rows; latest:", hist["日付"].max())

    entries, entry_time, entry_url = fetch_entry_table(TEST_RACE_ID)

    # Keep only the horses that existed in the frozen 10-minute snapshot.
    active_numbers = {
        int(h["number"])
        for h in race["horses"]
    }

    entries = (
        entries.loc[
            entries["馬番"].astype(int).isin(active_numbers)
        ]
        .sort_values("馬番")
        .reset_index(drop=True)
    )

    expected_count = len(race["horses"])

    print("JRA official entry parsed:", len(entries), "/", expected_count)
    print("entry fetched at:", entry_time)
    print("entry URL:", entry_url)

    if len(entries) != expected_count:
        fail(
            f"JRA entry count mismatch: {len(entries)} / {expected_count}"
        )

    frozen_names = {
        int(h["number"]): str(h["name"])
        for h in race["horses"]
    }

    for _, row in entries.iterrows():
        no = int(row["馬番"])
        expected_name = frozen_names.get(no)

        if expected_name is None or str(row["馬名"]) != expected_name:
            fail(
                f"JRA entry / frozen horse-name mismatch: "
                f"馬番{no} {row['馬名']!r} / expected={expected_name!r}"
            )

    # Same historical name lists and matching logic as the formal run.
    hist_jockeys = sorted(set(hist["騎手"].dropna().astype(str)))
    hist_trainers = sorted(set(hist["調教師"].dropna().astype(str)))

    entries["騎手"] = entries["騎手_JRA"].map(
        lambda x: match_history_name(x, hist_jockeys)
    )

    entries["調教師"] = entries["調教師_JRA"].map(
        lambda x: match_history_name(x, hist_trainers)
    )

    if entries["騎手"].isna().any():
        print(
            entries.loc[
                entries["騎手"].isna(),
                ["馬番", "馬名", "騎手_JRA"],
            ].to_string(index=False)
        )
        fail("騎手表記変換失敗")

    if entries["調教師"].isna().any():
        print(
            entries.loc[
                entries["調教師"].isna(),
                ["馬番", "馬名", "調教師_JRA"],
            ].to_string(index=False)
        )
        fail("調教師表記変換失敗")

    # Same horse-key logic as the formal run.
    entries["馬キー"] = assign_horse_keys(entries, hist, test_date)

    # Diagnostic guard for the known Kyoto 6R replay.
    check_known_default_inputs(entries)

    # Frozen 10-minute win odds already published to RaceData.json.
    frozen = pd.DataFrame(race["horses"])[["number", "name", "odds"]].copy()
    frozen.columns = ["馬番", "RaceData馬名", "単勝オッズ"]

    current = entries.merge(
        frozen,
        on="馬番",
        how="inner",
        validate="one_to_one",
    )

    if len(current) != expected_count:
        fail("JRA entry / frozen-odds merge count mismatch")

    if not (
        current["馬名"].astype(str).values
        == current["RaceData馬名"].astype(str).values
    ).all():
        fail("Horse names differ between JRA official entry and frozen RaceData")

    # The saved snapshot's popularity is equivalent to win-odds rank here.
    # The default replay race has no tied odds.
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

    # ------------------------------------------------------------
    # Build today's 23-column row set exactly like formal run
    # ------------------------------------------------------------
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
    cur["出典"] = "当日予測_JRA公式_10分前"
    cur["芝ダ"] = surface
    cur["距離"] = distance
    cur["クラス"] = race_class
    cur["障害"] = False
    cur["タイム秒"] = np.nan
    cur["上がり3F"] = np.nan
    cur["初角"] = np.nan
    cur["頭数"] = len(cur)

    if len(cur) != expected_count:
        fail("Current input row count mismatch")

    # ------------------------------------------------------------
    # History + today -> rating -> 37 features
    # ------------------------------------------------------------
    work = pd.concat([hist, cur], ignore_index=True)
    work["日付"] = pd.to_datetime(work["日付"], errors="coerce")
    work = (
        work.sort_values(["日付", "レースID", "馬番"])
        .reset_index(drop=True)
    )

    print("feature input:", f"{len(work):,}")

    before_rating_n = len(work)
    print("add_rating...")
    work = add_rating(work)

    if len(work) != before_rating_n:
        fail("add_rating changed row count")

    print("add_features...")
    work = add_features(work)

    if len(work) != before_rating_n:
        fail("add_features changed row count")

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

    # ------------------------------------------------------------
    # Fixed LightGBM
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # Compare with already-published RaceData.json
    # ------------------------------------------------------------
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

    # RaceData values are rounded for the app, so allow only rounding-sized
    # differences. Do NOT widen these tolerances to hide input differences.
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
    print("entry source: JRA official")
    print("37 features finite: True")
    print("fixed LightGBM: OK")
    print("research-candidate match:", candidate_ok)
    print("RaceData.json modified: NO")
    print("prediction files modified: NO")

    if not numeric_ok:
        fail(
            "Prediction values differ from the already-published 10-minute "
            "RaceData beyond rounding tolerance"
        )

    if not candidate_ok:
        fail(
            "Research-candidate set differs from the already-published RaceData"
        )


if __name__ == "__main__":
    main()
