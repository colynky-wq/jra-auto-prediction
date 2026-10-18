# ============================================================
# KeibaAI - production history updater
#
# Purpose
# - Append one completed JRA date to production_history.parquet.
# - Keep the fixed model unchanged.
# - Preserve the existing 23-column history schema exactly.
# - Fetch completed results from JRA official result pages.
# - Fetch final win odds from the already-proven netkeiba odds API.
#
# Safety
# - Default is DRY RUN. The parquet is replaced only when
#   KEIBAAI_HISTORY_APPLY=true.
# - The old history prefix must remain byte-for-value equivalent as a
#   DataFrame after append/readback checks.
# - Any missing race/result/required field causes the run to fail BEFORE
#   production_history.parquet is replaced.
# - Re-running an already-complete date is idempotent and exits safely.
# ============================================================

from __future__ import annotations

import io
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from horse_identity_runtime import assign_horse_keys
import requests
from bs4 import BeautifulSoup
from history_update_source import fetch_day, target_date


# ============================================================
# 1. Paths / configuration
# ============================================================

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
REPORT_ROOT = REPO / "reports" / "history_updates"

NOW_JST = datetime.now(JST)
TARGET_DATE8 = target_date(
    os.environ.get("KEIBAAI_HISTORY_DATE", "").strip(),
    os.environ.get("KEIBAAI_HISTORY_RUN_CREATED_AT", "").strip(),
    os.environ.get("GITHUB_EVENT_NAME", ""), NOW_JST,
)
RACE_LIST_EVIDENCE = {}
APPLY = os.environ.get("KEIBAAI_HISTORY_APPLY", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

BASE_COLS = [
    "レースID",
    "日付",
    "馬番",
    "馬名",
    "馬キー",
    "年齢",
    "斤量",
    "騎手",
    "調教師",
    "着順",
    "人気",
    "単勝オッズ",
    "出典",
    "芝ダ",
    "距離",
    "クラス",
    "障害",
    "馬体重",
    "増減",
    "タイム秒",
    "上がり3F",
    "初角",
    "頭数",
]

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

JRA_HOST = "https://www.jra.go.jp"
ACCESS_S = JRA_HOST + "/JRADB/accessS.html"

# The completed 2026-10-11 date is our first history migration day.
# These exact JRA route suffixes make the first update deterministic.
# Future dates use automatic JRA route discovery below.
KNOWN_RESULT_ROUTES = {
    "20261011": {
        # Tokyo 1R-12R
        "202605040401": "38",
        "202605040402": "ED",
        "202605040403": "A2",
        "202605040404": "57",
        "202605040405": "0C",
        "202605040406": "C1",
        "202605040407": "76",
        "202605040408": "2B",
        "202605040409": "E0",
        "202605040410": "D5",
        "202605040411": "8A",
        "202605040412": "3F",
        # Kyoto 1R-12R
        "202608040401": "16",
        "202608040402": "CB",
        "202608040403": "80",
        "202608040404": "35",
        "202608040405": "EA",
        "202608040406": "9F",
        "202608040407": "54",
        "202608040408": "09",
        "202608040409": "BE",
        "202608040410": "B3",
        "202608040411": "68",
        "202608040412": "1D",
    },
}


# ============================================================
# 2. Basic helpers
# ============================================================

def fail(message: str) -> None:
    print()
    print("=" * 110)
    print("KEIBAAI PRODUCTION HISTORY UPDATE: FAILED")
    print("=" * 110)
    print(message)
    sys.exit(1)


def compact_name(x) -> str:
    if pd.isna(x):
        return ""
    s = unicodedata.normalize("NFKC", str(x))
    s = re.sub(r"^[☆★△▲◇]", "", s)
    s = re.sub(r"[\s・\.．]", "", s)
    return s.strip()


def match_history_name(current_name, history_values):
    """Same matching rule used by the production forward predictor."""
    cn = compact_name(current_name)
    if not cn:
        return None

    candidates = []
    for hv in history_values:
        hn = compact_name(hv)
        if not hn:
            continue
        if cn == hn:
            candidates.append((100000 + len(hn), hv))
        elif cn.startswith(hn):
            candidates.append((50000 + len(hn), hv))
        elif hn in cn:
            candidates.append((10000 + len(hn), hv))

    if not candidates:
        return None
    candidates.sort(key=lambda z: z[0], reverse=True)
    return candidates[0][1]


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        cols = []
        for tup in out.columns:
            parts = [
                str(x).strip()
                for x in tup
                if str(x).strip() and str(x).strip().lower() != "nan"
            ]
            cols.append(" ".join(dict.fromkeys(parts)))
        out.columns = cols
    else:
        out.columns = [str(x).strip() for x in out.columns]
    return out


def norm_header(value) -> str:
    return re.sub(r"[\s\u3000]+", "", str(value)).strip()


def find_col(columns, *tokens):
    for col in columns:
        n = norm_header(col)
        if any(token in n for token in tokens):
            return col
    return None


def decode_response(response) -> str:
    candidates = [
        response.encoding,
        response.apparent_encoding,
        "utf-8",
        "cp932",
        "shift_jis",
        "euc_jp",
    ]
    best_text = None
    best_score = -10**9
    for enc in candidates:
        if not enc:
            continue
        try:
            text = response.content.decode(enc, errors="replace")
        except Exception:
            continue
        score = sum(
            text.count(token) * 100
            for token in ["着順", "馬番", "馬名", "レース結果", "コーナー通過順位"]
        ) - text.count("�")
        if score > best_score:
            best_score = score
            best_text = text
    return best_text if best_text is not None else response.text


def parse_finish(value):
    s = str(value).strip()
    m = re.match(r"^(\d+)", s)
    if m:
        return float(int(m.group(1))), "確定"
    if not s or s.lower() == "nan":
        return np.nan, "不明"
    return np.nan, s


def parse_time_seconds(value):
    s = str(value).strip()
    m = re.match(r"^(\d+):(\d{2})(?:\.(\d+))?$", s)
    if not m:
        return np.nan
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    fraction = float("0." + m.group(3)) if m.group(3) else 0.0
    return float(minutes * 60 + seconds + fraction)


def parse_first_corner(value):
    s = str(value)
    nums = re.findall(r"\d+", s)
    if not nums:
        return np.nan
    return float(int(nums[0]))


def parse_body_weight(value):
    s = str(value).strip().replace("＋", "+").replace("－", "-")
    m_weight = re.search(r"(?<!\d)(\d{3})(?!\d)", s)
    weight = float(m_weight.group(1)) if m_weight else np.nan
    m_diff = re.search(r"\(([+-]?\d+)\)", s)
    diff = float(m_diff.group(1)) if m_diff else np.nan
    return weight, diff


def parse_age(value):
    m = re.search(r"(\d+)", str(value))
    return int(m.group(1)) if m else None


def derive_class(text: str) -> str:
    s = str(text)
    if "新馬" in s or "メイクデビュー" in s:
        return "新馬"
    if "未勝利" in s:
        return "未勝利"
    if "1勝クラス" in s or "1勝" in s:
        return "1勝"
    if "2勝クラス" in s or "2勝" in s:
        return "2勝"
    if "3勝クラス" in s or "3勝" in s:
        return "3勝"
    return "OP"


def parse_race_meta(text: str) -> dict:
    soup = BeautifulSoup(text, "html.parser")
    all_text = " ".join(soup.stripped_strings)

    course_match = re.search(
        r"コース\s*[:：]\s*([\d,]+)\s*メートル\s*[（(]([^）)]+)[）)]",
        all_text,
    )
    if not course_match:
        raise RuntimeError("JRA result course metadata not found")

    distance = int(course_match.group(1).replace(",", ""))
    course_detail = course_match.group(2)
    context_start = max(0, course_match.start() - 350)
    class_context = all_text[context_start:course_match.end()]

    obstacle = "障害" in class_context or "芝→ダート" in course_detail
    if obstacle:
        surface = "障"
    elif "ダート" in course_detail:
        surface = "ダ"
    elif "芝" in course_detail:
        surface = "芝"
    else:
        raise RuntimeError(f"surface not resolved: {course_detail}")

    return {
        "芝ダ": surface,
        "距離": float(distance),
        "クラス": derive_class(class_context),
        "障害": bool(obstacle),
    }


# ============================================================
# 3. Race list / JRA result URL
# ============================================================

def fetch_day_race_ids(date8: str) -> list[str]:
    global RACE_LIST_EVIDENCE
    race_ids, RACE_LIST_EVIDENCE = fetch_day(SESSION, date8)
    print("race ids for target date:", len(race_ids))
    return race_ids


def build_known_result_url(race_id: str, date8: str, suffix: str) -> str:
    race_id = str(race_id).zfill(12)
    cname = (
        "pw01sde01"
        + race_id[4:6]
        + race_id[0:4]
        + race_id[6:8]
        + race_id[8:10]
        + race_id[10:12]
        + date8
        + "/"
        + suffix
    )
    return ACCESS_S + "?" + urlencode({"CNAME": cname})


def resolve_result_url(race_id: str, date8: str) -> str:
    known = KNOWN_RESULT_ROUTES.get(date8, {}).get(race_id)
    if known:
        return build_known_result_url(race_id, date8, known)

    # Future dates: reuse the enhanced automatic result-route discovery
    # already used by the forward-result aggregator.
    os.environ["KEIBAAI_AGGREGATE_DATE"] = date8
    try:
        import run_forward_aggregate_v2 as result_v2

        historical = result_v2._historical_result_url(race_id)
        if historical:
            return historical
        return result_v2.base._discover_result_url_for_race(race_id, date8)
    except Exception as exc:
        raise RuntimeError(
            f"JRA result URL auto-discovery failed: {race_id}: {exc}"
        ) from exc


# ============================================================
# 4. Final win odds
# ============================================================

def fetch_final_win_odds(race_id: str) -> dict[int, float]:
    url = "https://race.netkeiba.com/api/api_get_jra_odds.html"
    referer = f"https://race.netkeiba.com/odds/index.html?race_id={race_id}"
    r = SESSION.get(
        url,
        params={
            "race_id": race_id,
            "type": 1,
            "action": "init",
            "compress": 0,
        },
        headers={**HEADERS, "Referer": referer},
        timeout=30,
    )
    print("final-odds HTTP:", r.status_code, "bytes:", len(r.content), "race:", race_id)
    if r.status_code != 200:
        raise RuntimeError(f"final odds HTTP {r.status_code}: {race_id}")

    payload = r.json()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    odds_root = data.get("odds") if isinstance(data.get("odds"), dict) else {}
    win = odds_root.get("1") if isinstance(odds_root.get("1"), dict) else {}

    result = {}
    for k, v in win.items():
        if not isinstance(v, list) or not v:
            continue
        try:
            no = int(k)
            odd = float(v[0])
        except Exception:
            continue
        if np.isfinite(odd) and odd > 0:
            result[no] = odd

    print("final win odds parsed:", len(result), "race:", race_id)
    if not result:
        raise RuntimeError(f"final win odds empty: {race_id}")
    return result


# ============================================================
# 5. Parse one official JRA completed race into the 23-column base schema
# ============================================================

def parse_result_rows(text: str, race_id: str) -> pd.DataFrame:
    meta = parse_race_meta(text)

    try:
        tables = pd.read_html(io.StringIO(text))
    except Exception as exc:
        raise RuntimeError(f"JRA result table parse failed: {race_id}: {exc}") from exc

    result = None
    for raw in tables:
        t = flatten_columns(raw)
        cols = list(t.columns)
        if (
            find_col(cols, "着順") is not None
            and find_col(cols, "馬番") is not None
            and find_col(cols, "馬名") is not None
        ):
            result = t
            break

    if result is None:
        raise RuntimeError(f"JRA result table not found: {race_id}")

    finish_col = find_col(result.columns, "着順")
    no_col = find_col(result.columns, "馬番")
    name_col = find_col(result.columns, "馬名")
    age_col = find_col(result.columns, "性齢")
    carried_col = find_col(result.columns, "負担重量")
    jockey_col = find_col(result.columns, "騎手名", "騎手")
    time_col = find_col(result.columns, "タイム")
    corner_col = find_col(result.columns, "コーナー通過順位")
    last3f_col = find_col(result.columns, "推定上り")
    body_col = find_col(result.columns, "馬体重")
    trainer_col = find_col(result.columns, "調教師名", "調教師")
    pop_col = find_col(result.columns, "単勝人気", "人気")

    required = {
        "着順": finish_col,
        "馬番": no_col,
        "馬名": name_col,
        "性齢": age_col,
        "負担重量": carried_col,
        "騎手": jockey_col,
        "馬体重": body_col,
        "調教師": trainer_col,
        "人気": pop_col,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise RuntimeError(
            f"required JRA result columns missing {race_id}: {missing}; columns={list(result.columns)}"
        )

    final_odds = fetch_final_win_odds(race_id)
    rows = []

    for _, row in result.iterrows():
        no = pd.to_numeric(pd.Series([row[no_col]]), errors="coerce").iloc[0]
        if not np.isfinite(no):
            continue
        horse_no = int(no)
        if horse_no < 1 or horse_no > 18:
            continue

        finish, status = parse_finish(row[finish_col])
        status_text = str(status)
        if "取消" in status_text or "除外" in status_text:
            continue

        horse_name = str(row[name_col]).strip()
        age = parse_age(row[age_col])
        carried = pd.to_numeric(pd.Series([row[carried_col]]), errors="coerce").iloc[0]
        popularity = pd.to_numeric(pd.Series([row[pop_col]]), errors="coerce").iloc[0]
        body_weight, weight_diff = parse_body_weight(row[body_col])

        if not horse_name or horse_name.lower() == "nan":
            raise RuntimeError(f"horse name missing: {race_id} horse {horse_no}")
        if age is None:
            raise RuntimeError(f"age missing: {race_id} horse {horse_no}")
        if not np.isfinite(carried):
            raise RuntimeError(f"carried weight missing: {race_id} horse {horse_no}")
        if not np.isfinite(popularity):
            raise RuntimeError(f"popularity missing: {race_id} horse {horse_no}")
        if horse_no not in final_odds:
            raise RuntimeError(f"final odds missing: {race_id} horse {horse_no}")

        raw_time = row[time_col] if time_col is not None else np.nan
        raw_corner = row[corner_col] if corner_col is not None else np.nan
        raw_last3f = row[last3f_col] if last3f_col is not None else np.nan

        rows.append(
            {
                "レースID": race_id,
                "馬番": horse_no,
                "馬名": horse_name,
                "年齢": int(age),
                "斤量": float(carried),
                "騎手_JRA": str(row[jockey_col]).strip(),
                "調教師_JRA": str(row[trainer_col]).strip(),
                "着順": finish,
                "人気": float(popularity),
                "単勝オッズ": float(final_odds[horse_no]),
                "出典": "JRA公式更新",
                "芝ダ": meta["芝ダ"],
                "距離": meta["距離"],
                "クラス": meta["クラス"],
                "障害": meta["障害"],
                "馬体重": body_weight,
                "増減": weight_diff,
                "タイム秒": parse_time_seconds(raw_time),
                "上がり3F": pd.to_numeric(
                    pd.Series([raw_last3f]), errors="coerce"
                ).iloc[0],
                "初角": parse_first_corner(raw_corner),
                "結果状態": status_text,
            }
        )

    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError(f"JRA result rows = 0: {race_id}")

    if d["馬番"].duplicated().any():
        raise RuntimeError(f"duplicate horse number in result: {race_id}")

    d["頭数"] = int(len(d))
    return d.sort_values("馬番").reset_index(drop=True)


def fetch_completed_race(race_id: str, date8: str) -> pd.DataFrame:
    url = resolve_result_url(race_id, date8)
    print("JRA result URL:", url)
    r = SESSION.get(url, timeout=30)
    print("JRA result HTTP:", r.status_code, "bytes:", len(r.content), "race:", race_id)
    if r.status_code != 200:
        raise RuntimeError(f"JRA result HTTP {r.status_code}: {race_id}")
    text = decode_response(r)
    if "成績が確定していません" in text:
        raise RuntimeError(f"JRA result not finalized: {race_id}")
    return parse_result_rows(text, race_id)


# ============================================================
# 6. Build target-date rows with history-compatible names / horse keys
# ============================================================

def build_target_rows(hist: pd.DataFrame, race_ids: list[str], date8: str) -> pd.DataFrame:
    hist_jockeys = sorted(set(hist["騎手"].dropna().astype(str)))
    hist_trainers = sorted(set(hist["調教師"].dropna().astype(str)))

    target_date = pd.Timestamp(datetime.strptime(date8, "%Y%m%d").date())
    all_rows = []
    failures = []

    for i, race_id in enumerate(race_ids, start=1):
        print()
        print("-" * 110)
        print(f"[{i}/{len(race_ids)}]", race_id)
        print("-" * 110)
        try:
            d = fetch_completed_race(race_id, date8)
            all_rows.append(d)
            print(
                "parsed:", len(d),
                "surface:", d["芝ダ"].iloc[0],
                "distance:", d["距離"].iloc[0],
                "class:", d["クラス"].iloc[0],
            )
        except Exception as exc:
            failures.append((race_id, f"{type(exc).__name__}: {exc}"))
            print("ERROR:", race_id, repr(exc))

    if failures:
        print()
        print("Failed races:")
        for race_id, message in failures:
            print(" ", race_id, message)
        raise RuntimeError(f"{len(failures)} race(s) failed; history will NOT be updated")

    current = pd.concat(all_rows, ignore_index=True)
    current["日付"] = target_date

    current["騎手"] = current["騎手_JRA"].map(
        lambda x: match_history_name(x, hist_jockeys) or compact_name(x)
    )
    current["調教師"] = current["調教師_JRA"].map(
        lambda x: match_history_name(x, hist_trainers) or compact_name(x)
    )

    current["馬キー"] = assign_horse_keys(current, hist, target_date)

    current = current.drop(columns=["騎手_JRA", "調教師_JRA", "結果状態"])
    current = current[BASE_COLS].copy()

    # Enforce the exact 23-column production dtypes.
    current["レースID"] = current["レースID"].astype(str)
    current["日付"] = pd.to_datetime(current["日付"])
    current["馬番"] = current["馬番"].astype("int64")
    current["馬名"] = current["馬名"].astype(object)
    current["馬キー"] = current["馬キー"].astype(object)
    current["年齢"] = current["年齢"].astype("int64")
    current["斤量"] = current["斤量"].astype("float64")
    current["騎手"] = current["騎手"].astype(object)
    current["調教師"] = current["調教師"].astype(object)
    for col in [
        "着順", "人気", "単勝オッズ", "距離", "馬体重", "増減", "タイム秒", "上がり3F", "初角"
    ]:
        current[col] = pd.to_numeric(current[col], errors="coerce").astype("float64")
    current["出典"] = current["出典"].astype(object)
    current["芝ダ"] = current["芝ダ"].astype(object)
    current["クラス"] = current["クラス"].astype(object)
    current["障害"] = current["障害"].astype(bool)
    current["頭数"] = current["頭数"].astype("int64")

    return current.sort_values(["レースID", "馬番"]).reset_index(drop=True)


# ============================================================
# 7. Validation / save
# ============================================================

def main() -> None:
    print("=" * 110)
    print("KeibaAI Production History Update")
    print("JST:", NOW_JST.strftime("%Y-%m-%d %H:%M:%S"))
    print("target date:", TARGET_DATE8)
    print("apply changes:", APPLY)
    print("history:", HISTORY_PATH)
    print("=" * 110)

    if not re.fullmatch(r"\d{8}", TARGET_DATE8):
        fail(f"invalid target date: {TARGET_DATE8}")
    if not HISTORY_PATH.exists():
        fail(f"history file not found: {HISTORY_PATH}")

    target_date = pd.Timestamp(datetime.strptime(TARGET_DATE8, "%Y%m%d").date())
    if target_date.date() > NOW_JST.date():
        fail("future history target is not accepted")

    hist = pd.read_parquet(HISTORY_PATH)
    missing = [c for c in BASE_COLS if c not in hist.columns]
    if missing:
        fail(f"history missing required columns: {missing}")
    if list(hist.columns) != BASE_COLS:
        fail(f"history column order changed: {list(hist.columns)}")

    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    if hist["日付"].isna().any():
        fail("history contains invalid dates")

    old_rows = len(hist)
    old_races = hist["レースID"].astype(str).nunique()
    old_latest = hist["日付"].max().normalize()
    old_dups = int(hist.duplicated(["レースID", "馬番"]).sum())

    print("old rows:", f"{old_rows:,}")
    print("old races:", f"{old_races:,}")
    print("old latest:", old_latest)
    print("old duplicate raceID+horseNo:", old_dups)

    if old_dups:
        fail("existing history already contains duplicate raceID+horseNo")

    existing_target = hist.loc[hist["日付"].dt.normalize() == target_date]
    if not existing_target.empty:
        print()
        print("target date is already present in production history")
        print("rows:", len(existing_target))
        print("races:", existing_target["レースID"].astype(str).nunique())
        print("★★★★★ KEIBAAI PRODUCTION HISTORY UPDATE: ALREADY UPDATED")
        return

    if target_date <= old_latest:
        fail(
            f"target date {target_date.date()} is not later than history latest {old_latest.date()}"
        )

    race_ids = fetch_day_race_ids(TARGET_DATE8)
    if not race_ids:
        schedule = REPO / "reports" / TARGET_DATE8 / "PaceRecordingSchedule.json"
        if schedule.exists() and json.loads(schedule.read_text(encoding="utf-8")).get("races"):
            fail("official calendar says no meeting but saved monitoring schedule has races")
        print()
        print("Official JRA monthly calendar confirms no scheduled meeting:",
              json.dumps(RACE_LIST_EVIDENCE, ensure_ascii=False))
        print("★★★★★ KEIBAAI PRODUCTION HISTORY UPDATE: SKIP")
        return

    current = build_target_rows(hist, race_ids, TARGET_DATE8)

    new_race_count = current["レースID"].nunique()
    new_dups = int(current.duplicated(["レースID", "馬番"]).sum())
    print()
    print("=" * 110)
    print("TARGET-DATE VALIDATION")
    print("=" * 110)
    print("expected races:", len(race_ids))
    print("parsed races:", new_race_count)
    print("new rows:", len(current))
    print("duplicate raceID+horseNo:", new_dups)
    print("flat rows:", int((~current["障害"]).sum()))
    print("obstacle rows:", int(current["障害"].sum()))
    print("final odds missing:", int(current["単勝オッズ"].isna().sum()))
    print("popularity missing:", int(current["人気"].isna().sum()))
    print("horse key missing:", int(current["馬キー"].isna().sum()))

    if new_race_count != len(race_ids):
        fail(f"race count mismatch: parsed={new_race_count} expected={len(race_ids)}")
    if new_dups:
        fail("new target rows contain duplicate raceID+horseNo")
    if current["単勝オッズ"].isna().any():
        fail("new target rows contain missing final win odds")
    if current["人気"].isna().any():
        fail("new target rows contain missing popularity")
    if current["馬キー"].isna().any():
        fail("new target rows contain missing horse key")

    combined = pd.concat([hist, current], ignore_index=True)

    # Old history must remain exactly unchanged as the prefix.
    pd.testing.assert_frame_equal(
        hist.reset_index(drop=True),
        combined.iloc[:old_rows].reset_index(drop=True),
        check_dtype=True,
        check_exact=True,
        check_names=True,
    )

    combined_dups = int(combined.duplicated(["レースID", "馬番"]).sum())
    if combined_dups:
        fail(f"combined history contains duplicates: {combined_dups}")

    new_latest = combined["日付"].max().normalize()
    if new_latest != target_date:
        fail(f"combined latest date mismatch: {new_latest} != {target_date}")

    tmp_path = Path("/tmp/keibaai_production_history_next.parquet")
    combined.to_parquet(tmp_path, index=False, compression="zstd")
    loaded = pd.read_parquet(tmp_path)

    pd.testing.assert_frame_equal(
        combined.reset_index(drop=True),
        loaded.reset_index(drop=True),
        check_dtype=True,
        check_exact=True,
        check_names=True,
    )

    info = {
        "targetDate": TARGET_DATE8,
        "generatedAtJST": datetime.now(JST).isoformat(),
        "applyRequested": APPLY,
        "oldRows": int(old_rows),
        "newRows": int(len(current)),
        "combinedRows": int(len(combined)),
        "oldRaceCount": int(old_races),
        "addedRaceCount": int(new_race_count),
        "combinedRaceCount": int(combined["レースID"].astype(str).nunique()),
        "oldLatestDate": old_latest.strftime("%Y-%m-%d"),
        "newLatestDate": new_latest.strftime("%Y-%m-%d"),
        "duplicateRaceHorseKeys": int(combined_dups),
        "parquetBytes": int(tmp_path.stat().st_size),
        "status": "validated",
        "raceListSource": RACE_LIST_EVIDENCE,
    }

    print()
    print("=" * 110)
    print("FINAL VALIDATION")
    print("=" * 110)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("readback exact match: True")
    print("old history unchanged: True")

    if not APPLY:
        print()
        print("★★★★★ KEIBAAI PRODUCTION HISTORY UPDATE: DRY RUN SUCCESS")
        print("production_history.parquet was NOT modified.")
        print("Run again with apply_changes=true after reviewing this output.")
        return

    # Replace only after every validation above has passed.
    tmp_path.replace(HISTORY_PATH)

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    info["status"] = "applied"
    report_path = REPORT_ROOT / f"{TARGET_DATE8}.json"
    report_path.write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print()
    print("production history replaced:", HISTORY_PATH)
    print("history update report:", report_path)
    print("★★★★★ KEIBAAI PRODUCTION HISTORY UPDATE: APPLIED SUCCESS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")
