# ============================================================
# KeibaAI GitHub Actions - LIVE MONITOR DRY RUN
#
# Purpose
# - Fetch the target day's JRA race schedule from netkeiba.
# - Watch post times in JST.
# - When a flat race enters the formal 8-12 minute window, capture live win odds.
# - Fetch the JRA official entry table.
# - Rebuild the 37 fixed-model features and print the fixed LightGBM prediction.
#
# Safety
# - READ ONLY.
# - Does NOT modify RaceData.json.
# - Does NOT commit prediction files.
# - Does NOT change the model, history, or research-candidate thresholds.
#
# Modes
# - plan      : print the day's schedule and exit.
# - preflight : additionally confirm JRA official entry parsing for one flat race.
# - live      : poll the clock and predict only inside the 8-12 minute window.
# ============================================================

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from feature_engine import ALLF, add_features, add_rating
from jra_entry_source import fetch_entry_table as fetch_jra_entry_table
from dry_run_one_race import match_history_name


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
HISTORY_PATH = HERE / "production_history.parquet"
MODEL_PATH = HERE / "lightgbm_fixed_2025.txt"

TODAY8 = datetime.now(JST).strftime("%Y%m%d")
TARGET_DATE8 = os.environ.get("KEIBAAI_LIVE_DATE", "").strip() or TODAY8
MODE = os.environ.get("KEIBAAI_LIVE_MODE", "plan").strip().lower() or "plan"
TARGET_RACE_ID = os.environ.get("KEIBAAI_LIVE_RACE_ID", "").strip()
MAX_RUNTIME_MINUTES = int(os.environ.get("KEIBAAI_LIVE_MAX_MINUTES", "30").strip() or "30")
POLL_SECONDS = int(os.environ.get("KEIBAAI_LIVE_POLL_SECONDS", "20").strip() or "20")

WINDOW_EARLY_MINUTES = 12.0
WINDOW_LATE_MINUTES = 8.0

VENUE_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

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


def fail(message: str) -> None:
    print()
    print("=" * 110)
    print("KEIBAAI LIVE MONITOR DRY RUN: FAILED")
    print("=" * 110)
    print(message)
    sys.exit(1)


def now_jst() -> datetime:
    return datetime.now(JST)


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


def parse_course(text: str):
    s = re.sub(r"\s+", "", str(text))
    m = re.search(r"(芝|ダ|障)(\d{3,4})m", s)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def fetch_schedule(date8: str) -> list[dict]:
    url = "https://race.netkeiba.com/top/race_list_sub.html"
    r = SESSION.get(url, params={"kaisai_date": date8}, timeout=30)
    print("race-list HTTP:", r.status_code, "bytes:", len(r.content), "url:", r.url)
    if r.status_code != 200:
        fail(f"race list HTTP {r.status_code}")

    text = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")

    blocks = soup.select(".RaceList_DataItem")
    rows: dict[str, dict] = {}

    for block in blocks:
        a = block.find("a", href=re.compile(r"race_id=\d{12}"))
        if a is None:
            continue
        m = re.search(r"race_id=(\d{12})", str(a.get("href", "")))
        if not m:
            continue
        race_id = m.group(1)
        block_text = " ".join(block.stripped_strings)
        tm = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", block_text)
        if not tm:
            continue
        hhmm = f"{int(tm.group(1)):02d}:{tm.group(2)}"

        name_node = block.select_one(".ItemTitle, .RaceName")
        race_name = name_node.get_text(" ", strip=True) if name_node else a.get_text(" ", strip=True)
        surface, distance = parse_course(block_text)
        rows[race_id] = {
            "race_id": race_id,
            "venue": VENUE_MAP.get(race_id[4:6], race_id[4:6]),
            "race_no": int(race_id[-2:]),
            "start_time": hhmm,
            "race_name": race_name,
            "surface": surface,
            "distance": distance,
            "class": derive_class(block_text + " " + race_name),
            "raw_text": block_text,
        }

    # Fallback if CSS classes ever change: find race links and nearby text.
    if not rows:
        for a in soup.find_all("a", href=True):
            m = re.search(r"race_id=(\d{12})", str(a.get("href", "")))
            if not m:
                continue
            race_id = m.group(1)
            parent = a
            for _ in range(5):
                if parent is None:
                    break
                t = " ".join(parent.stripped_strings)
                tm = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", t)
                if tm:
                    surface, distance = parse_course(t)
                    rows[race_id] = {
                        "race_id": race_id,
                        "venue": VENUE_MAP.get(race_id[4:6], race_id[4:6]),
                        "race_no": int(race_id[-2:]),
                        "start_time": f"{int(tm.group(1)):02d}:{tm.group(2)}",
                        "race_name": a.get_text(" ", strip=True),
                        "surface": surface,
                        "distance": distance,
                        "class": derive_class(t),
                        "raw_text": t,
                    }
                    break
                parent = parent.parent

    result = list(rows.values())
    result.sort(key=lambda x: (x["start_time"], x["race_id"]))
    print("schedule races found:", len(result))
    return result


def fetch_meta(race: dict) -> dict:
    """Refresh race name/course/class from the race page when possible."""
    rid = race["race_id"]
    url = "https://race.netkeiba.com/race/shutuba.html"
    r = SESSION.get(url, params={"race_id": rid}, timeout=30)
    print("race-meta HTTP:", r.status_code, "bytes:", len(r.content), "race:", rid)
    if r.status_code != 200:
        return dict(race)

    text = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    full_text = " ".join(soup.stripped_strings)

    out = dict(race)
    name_node = soup.select_one(".RaceName")
    if name_node:
        out["race_name"] = name_node.get_text(" ", strip=True)

    data_node = soup.select_one(".RaceData01")
    data_text = data_node.get_text(" ", strip=True) if data_node else full_text
    surface, distance = parse_course(data_text)
    if surface:
        out["surface"] = surface
        out["distance"] = distance

    out["class"] = derive_class(full_text)
    return out


def fetch_win_odds(race_id: str) -> tuple[pd.DataFrame, dict]:
    url = "https://race.netkeiba.com/api/api_get_jra_odds.html"
    referer = f"https://race.netkeiba.com/odds/index.html?race_id={race_id}"
    r = SESSION.get(
        url,
        params={"race_id": race_id, "type": 1, "action": "init", "compress": 0},
        headers={**HEADERS, "Referer": referer},
        timeout=30,
    )
    print("odds HTTP:", r.status_code, "bytes:", len(r.content), "race:", race_id)
    if r.status_code != 200:
        raise RuntimeError(f"odds HTTP {r.status_code}")

    payload = r.json()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    odds_root = data.get("odds") if isinstance(data.get("odds"), dict) else {}
    win = odds_root.get("1") if isinstance(odds_root.get("1"), dict) else {}

    rows = []
    for k, v in win.items():
        if not isinstance(v, list) or not v:
            continue
        try:
            no = int(k)
            odd = float(v[0])
        except Exception:
            continue
        if np.isfinite(odd) and odd > 0:
            rows.append({"馬番": no, "単勝オッズ": odd})

    d = pd.DataFrame(rows).sort_values("馬番").reset_index(drop=True) if rows else pd.DataFrame(columns=["馬番", "単勝オッズ"])
    print("live win-odds horses:", len(d), "official_datetime:", data.get("official_datetime"))
    return d, payload


def load_history(target_date: pd.Timestamp) -> pd.DataFrame:
    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    hist = hist.loc[hist["日付"].dt.normalize() < target_date.normalize()].copy()
    if hist.empty:
        raise RuntimeError("history is empty")
    return hist


def predict_live_race(race: dict, hist: pd.DataFrame, target_date: pd.Timestamp) -> pd.DataFrame:
    race = fetch_meta(race)
    rid = race["race_id"]

    if race.get("surface") == "障" or "障害" in str(race.get("race_name", "")):
        raise RuntimeError("obstacle race is out of scope")
    if race.get("surface") not in {"芝", "ダ"} or not race.get("distance"):
        raise RuntimeError(f"course could not be resolved: {race}")

    odds, _ = fetch_win_odds(rid)
    if len(odds) < 2:
        raise RuntimeError("insufficient live win odds")

    entries, entry_time, entry_url = fetch_jra_entry_table(rid, TARGET_DATE8, JST)
    print("JRA official entry parsed:", len(entries), "fetched_at:", entry_time)
    print("JRA entry URL:", entry_url)

    active = set(odds["馬番"].astype(int))
    entries = entries.loc[entries["馬番"].astype(int).isin(active)].copy()
    entries = entries.sort_values("馬番").reset_index(drop=True)

    if len(entries) != len(odds):
        raise RuntimeError(f"entry/odds count mismatch: {len(entries)} / {len(odds)}")

    hist_jockeys = sorted(set(hist["騎手"].dropna().astype(str)))
    hist_trainers = sorted(set(hist["調教師"].dropna().astype(str)))
    entries["騎手"] = entries["騎手_JRA"].map(lambda x: match_history_name(x, hist_jockeys))
    entries["調教師"] = entries["調教師_JRA"].map(lambda x: match_history_name(x, hist_trainers))

    if entries["騎手"].isna().any() or entries["調教師"].isna().any():
        raise RuntimeError("jockey/trainer history-name matching failed")

    latest_key_df = (
        hist[["日付", "馬名", "馬キー"]]
        .dropna(subset=["馬名", "馬キー"])
        .sort_values("日付")
        .drop_duplicates("馬名", keep="last")
    )
    latest_key = dict(zip(latest_key_df["馬名"].astype(str), latest_key_df["馬キー"].astype(str)))
    entries["馬キー"] = [
        latest_key.get(name, f"{name}_{target_date.year - int(age)}")
        for name, age in zip(entries["馬名"], entries["年齢"])
    ]

    current = entries.merge(odds, on="馬番", how="inner", validate="one_to_one")
    current["人気"] = current["単勝オッズ"].rank(ascending=True, method="min")

    cur = pd.DataFrame(index=np.arange(len(current)))
    cur["レースID"] = rid
    cur["日付"] = target_date
    for col in ["馬番", "馬名", "馬キー", "年齢", "斤量", "騎手", "調教師", "人気", "単勝オッズ", "馬体重", "増減"]:
        cur[col] = current[col].to_numpy()
    cur["着順"] = np.nan
    cur["出典"] = "GitHubライブ監視ドライラン"
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
    work = add_rating(work)
    if len(work) != before_n:
        raise RuntimeError("add_rating changed row count")
    work = add_features(work)
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
    if pred[ALLF].isna().any().any():
        raise RuntimeError("37 features contain missing values")

    X = pred[ALLF].astype(np.float32)
    if not np.isfinite(X.to_numpy()).all():
        raise RuntimeError("37 features contain inf/-inf")

    model = lgb.Booster(model_file=str(MODEL_PATH))
    if list(model.feature_name()) != list(ALLF):
        raise RuntimeError("model feature order mismatch")

    tree = model.predict(X, raw_score=True, validate_features=True)
    market_logit = pred["市場ロジット"].to_numpy(dtype=float)
    final_logit = market_logit + tree
    p_raw = 1.0 / (1.0 + np.exp(-np.clip(final_logit, -50, 50)))
    ai = p_raw / p_raw.sum()
    market = pred["市場確率"].to_numpy(dtype=float)
    ratio = ai / market
    ev = ai * pred["単勝オッズ"].to_numpy(dtype=float) * 100

    out = pd.DataFrame({
        "馬番": pred["馬番"].astype(int),
        "馬名": pred["馬名"].astype(str),
        "単勝オッズ": pred["単勝オッズ"].astype(float),
        "市場勝率(%)": market * 100,
        "AI勝率(%)": ai * 100,
        "AI÷市場": ratio,
        "期待払戻指数": ev,
    })
    out["研究候補"] = (out["AI÷市場"] >= 1.20) & (out["期待払戻指数"] >= 100)

    print()
    print("-" * 110)
    print(rid, race["venue"], f"{race['race_no']}R", race["race_name"], f"{race['surface']}{race['distance']}m", race["class"])
    print(out.to_string(index=False, formatters={
        "単勝オッズ": "{:.1f}".format,
        "市場勝率(%)": "{:.3f}".format,
        "AI勝率(%)": "{:.3f}".format,
        "AI÷市場": "{:.4f}".format,
        "期待払戻指数": "{:.3f}".format,
    }))
    cand = out.loc[out["研究候補"], ["馬番", "馬名", "AI÷市場", "期待払戻指数"]]
    print("research candidates:", cand.to_dict("records"))
    print("RaceData.json modified: NO")
    print("prediction files modified: NO")
    print("-" * 110)
    return out


def race_post_datetime(date8: str, hhmm: str) -> datetime:
    d = datetime.strptime(date8 + " " + hhmm, "%Y%m%d %H:%M")
    return d.replace(tzinfo=JST)


def print_schedule(races: list[dict]) -> None:
    print()
    print("=" * 110)
    print("LIVE MONITOR SCHEDULE")
    print("=" * 110)
    for r in races:
        obstacle = r.get("surface") == "障" or "障害" in str(r.get("race_name", ""))
        mark = "SKIP-障害" if obstacle else "FLAT"
        print(
            r["race_id"],
            f"{r['venue']}{r['race_no']}R",
            r["start_time"],
            mark,
            r.get("race_name", ""),
            f"{r.get('surface') or '?'}{r.get('distance') or '?'}",
            r.get("class", ""),
        )


def main() -> None:
    print("=" * 110)
    print("KeibaAI LIVE MONITOR DRY RUN")
    print("JST:", now_jst().strftime("%Y-%m-%d %H:%M:%S"))
    print("target date:", TARGET_DATE8)
    print("mode:", MODE)
    print("window:", WINDOW_EARLY_MINUTES, "to", WINDOW_LATE_MINUTES, "minutes before post")
    print("READ ONLY: RaceData.json and prediction files will NOT be modified")
    print("=" * 110)

    if MODE not in {"plan", "preflight", "live"}:
        fail(f"invalid mode: {MODE}")
    if not re.fullmatch(r"\d{8}", TARGET_DATE8):
        fail(f"invalid target date: {TARGET_DATE8}")
    if TARGET_RACE_ID and not re.fullmatch(r"\d{12}", TARGET_RACE_ID):
        fail(f"invalid target race id: {TARGET_RACE_ID}")

    races = fetch_schedule(TARGET_DATE8)
    if TARGET_RACE_ID:
        races = [r for r in races if r["race_id"] == TARGET_RACE_ID]
    if not races:
        fail("no target races found")

    print_schedule(races)

    flat = [r for r in races if not (r.get("surface") == "障" or "障害" in str(r.get("race_name", "")))]
    if not flat:
        fail("no flat races found")

    if MODE == "plan":
        print()
        print("★★★★★ KEIBAAI LIVE MONITOR PLAN: SUCCESS")
        print("Schedule discovery works. No waiting, prediction, or file modification was performed.")
        return

    # Preflight validates future-day JRA official entry discovery without waiting for odds.
    if MODE == "preflight":
        target = flat[0]
        entries, fetched_at, entry_url = fetch_jra_entry_table(target["race_id"], TARGET_DATE8, JST)
        print()
        print("preflight race:", target["race_id"], target["venue"], f"{target['race_no']}R")
        print("JRA official entry rows:", len(entries))
        print("entry fetched at:", fetched_at)
        print("entry URL:", entry_url)
        if entries.empty:
            fail("preflight entry table was empty")
        print()
        print("★★★★★ KEIBAAI LIVE MONITOR PREFLIGHT: SUCCESS")
        print("Future-day JRA official entry discovery works. No prediction or file modification was performed.")
        return

    # Live mode must run on the actual target date.
    if now_jst().strftime("%Y%m%d") != TARGET_DATE8:
        fail("live mode target date must equal the current JST date")

    if os.environ.get("KEIBAAI_CAPTURE_PRERACE", "").lower() in {"true", "1"}:
        try:
            from pace_recording_events import save_schedule
            save_schedule(TARGET_DATE8, flat)
        except Exception as exc:print("RECORDING_SCHEDULE_SKIPPED", str(exc))
    target_date = pd.Timestamp(datetime.strptime(TARGET_DATE8, "%Y%m%d").date())
    hist = load_history(target_date)
    print("history rows:", f"{len(hist):,}", "latest:", hist["日付"].max())

    deadline = now_jst() + timedelta(minutes=MAX_RUNTIME_MINUTES)
    processed: set[str] = set()
    missed: set[str] = set()
    failures: list[tuple[str, str]] = []

    print()
    print("Live monitoring started. max runtime minutes:", MAX_RUNTIME_MINUTES, "poll seconds:", POLL_SECONDS)

    while now_jst() < deadline:
        now = now_jst()
        pending = 0

        for race in flat:
            rid = race["race_id"]
            if rid in processed or rid in missed:
                continue
            post = race_post_datetime(TARGET_DATE8, race["start_time"])
            remaining = (post - now).total_seconds() / 60.0

            if remaining < WINDOW_LATE_MINUTES:
                missed.add(rid)
                if os.environ.get("KEIBAAI_CAPTURE_PRERACE", "").lower() in {"true", "1"}:
                    from pace_recording_events import record_event
                    record_event(rid, TARGET_DATE8, "monitor_missed", "prediction window missed")
                print("MISSED window:", rid, f"remaining={remaining:.2f}m")
                continue

            if WINDOW_LATE_MINUTES <= remaining <= WINDOW_EARLY_MINUTES:
                print()
                print("=" * 110)
                print("TRIGGER:", rid, race["venue"], f"{race['race_no']}R", "post", race["start_time"], f"remaining={remaining:.2f}m")
                print("=" * 110)
                try:
                    predict_live_race(race, hist, target_date)
                    processed.add(rid)
                    print("TRIGGER RESULT: SUCCESS", rid)
                except Exception as exc:
                    failures.append((rid, f"{type(exc).__name__}: {exc}"))
                    if os.environ.get("KEIBAAI_CAPTURE_PRERACE", "").lower() in {"true", "1"}:
                        from pace_recording_events import record_event
                        record_event(rid, TARGET_DATE8, "monitor_failed", exc)
                    processed.add(rid)
                    print("TRIGGER RESULT: FAILED", rid, repr(exc))
                continue

            if remaining > WINDOW_EARLY_MINUTES:
                pending += 1

        if TARGET_RACE_ID and TARGET_RACE_ID in processed | missed:
            break
        if pending == 0 and all(r["race_id"] in processed | missed for r in flat):
            break
        time.sleep(max(5, POLL_SECONDS))

    print()
    print("=" * 110)
    print("LIVE MONITOR SUMMARY")
    print("=" * 110)
    print("processed:", sorted(processed))
    print("missed:", sorted(missed))
    print("failures:", failures)
    print("RaceData.json modified: NO")
    print("prediction files modified: NO")

    if failures:
        fail("one or more triggered predictions failed")

    print()
    print("★★★★★ KEIBAAI LIVE MONITOR DRY RUN: SUCCESS")
    print("The monitor stayed read-only and handled the requested live window without prediction failures.")


if __name__ == "__main__":
    main()
