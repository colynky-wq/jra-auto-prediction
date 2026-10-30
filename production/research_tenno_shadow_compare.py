from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd

import research_market_independence as r
import live_monitor_dry_run as base
import live_monitor_topk as live
from feature_engine import add_features, add_rating
from horse_identity_runtime import assign_horse_keys

TARGET_DATE = pd.Timestamp("2026-11-01")
TARGET_DATE8 = "20261101"
TARGET_RID = "202605041111"


def train_c_final(final: pd.DataFrame) -> lgb.Booster:
    X = final[r.FEATURES_BC].astype(np.float32)
    y = (final["着順"].astype(float) == 1.0).astype(int).to_numpy()
    d = lgb.Dataset(
        X,
        label=y,
        feature_name=r.FEATURES_BC,
        free_raw_data=False,
    )
    return lgb.train(r.PARAMS, d, num_boost_round=r.FIXED_ROUNDS)


def prepare_current(hist: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    base.TARGET_DATE8 = TARGET_DATE8
    pre = hist.loc[hist["日付"] < TARGET_DATE].copy()
    pre = pre.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)

    races = base.fetch_schedule(TARGET_DATE8)
    target = [x for x in races if str(x.get("race_id")) == TARGET_RID]
    if len(target) != 1:
        raise RuntimeError(f"Tenno target count={len(target)}")
    race = base.fetch_meta(target[0])
    race["class"] = "OP"

    odds, payload = base.fetch_win_odds(TARGET_RID)
    entries, entry_time, entry_url = base.fetch_jra_entry_table(TARGET_RID, TARGET_DATE8, base.JST)
    print("TENNO_ENTRY_FETCHED", len(entries), entry_time, entry_url)

    hist_jockeys = sorted(set(pre["騎手"].dropna().astype(str)))
    hist_trainers = sorted(set(pre["調教師"].dropna().astype(str)))
    entries["騎手"] = [
        live._match_history_name_or_current(v, hist_jockeys, role="jockey", horse_no=int(no))
        for no, v in zip(entries["馬番"], entries["騎手_JRA"])
    ]
    entries["調教師"] = [
        live._match_history_name_or_current(v, hist_trainers, role="trainer", horse_no=int(no))
        for no, v in zip(entries["馬番"], entries["調教師_JRA"])
    ]
    entries["馬キー"] = assign_horse_keys(entries, pre, TARGET_DATE)

    active = set(entries["馬番"].astype(int))
    odds = odds.loc[odds["馬番"].astype(int).isin(active)].copy()
    current = entries.merge(odds, on="馬番", how="inner", validate="one_to_one")
    if len(current) != len(entries):
        raise RuntimeError(f"entry/odds mismatch entries={len(entries)} current={len(current)}")
    current["人気"] = current["単勝オッズ"].rank(ascending=True, method="min")

    cur = pd.DataFrame(index=np.arange(len(current)))
    cur["レースID"] = TARGET_RID
    cur["日付"] = TARGET_DATE
    for col in [
        "馬番", "馬名", "馬キー", "年齢", "斤量", "騎手", "調教師",
        "人気", "単勝オッズ", "馬体重", "増減",
    ]:
        cur[col] = current[col].to_numpy()
    cur["着順"] = np.nan
    cur["出典"] = "Tenno shadow research"
    cur["芝ダ"] = race["surface"]
    cur["距離"] = int(race["distance"])
    cur["クラス"] = "OP"
    cur["障害"] = False
    cur["タイム秒"] = np.nan
    cur["上がり3F"] = np.nan
    cur["初角"] = np.nan
    cur["頭数"] = len(cur)

    work = pd.concat([pre, cur], ignore_index=True)
    work["日付"] = pd.to_datetime(work["日付"], errors="coerce")
    work = work.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    work = add_rating(work)
    work = add_features(work)
    pred = (
        work.loc[
            (work["日付"].dt.normalize() == TARGET_DATE.normalize())
            & (work["レースID"].astype(str) == TARGET_RID)
        ]
        .copy().sort_values("馬番").reset_index(drop=True)
    )
    if len(pred) != len(cur):
        raise RuntimeError(f"prediction row mismatch {len(pred)} vs {len(cur)}")
    official_dt = str(payload.get("official_datetime") or payload.get("officialDatetime") or "")
    return pred, official_dt


def rank(p):
    return pd.Series(np.asarray(p)).rank(ascending=False, method="min").astype(int).to_numpy()


def main():
    hist = pd.read_parquet(r.HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    _, _, final = r.prepare_training_frames(hist)
    c_model = train_c_final(final)
    a_model = lgb.Booster(model_file=str(r.A_MODEL_PATH))

    pred, official_dt = prepare_current(hist)
    market = pred["市場確率"].to_numpy(dtype=float)
    pa = r.predict_a(a_model, pred)
    pc = r.predict_candidate(c_model, pred, kind="C")

    out = pred[["馬番", "馬名", "単勝オッズ", "人気"]].copy().reset_index(drop=True)
    out["市場勝率"] = market
    out["A勝率"] = pa
    out["C勝率"] = pc
    out["市場順位"] = rank(market)
    out["A順位"] = rank(pa)
    out["C順位"] = rank(pc)
    out["C市場順位差"] = out["市場順位"] - out["C順位"]
    out["A市場比"] = out["A勝率"] / out["市場勝率"]
    out["C市場比"] = out["C勝率"] / out["市場勝率"]
    out["shadowAlert"] = (
        (out["人気"] >= 6)
        & (out["C順位"] <= 5)
        & (out["C市場順位差"] >= 2)
    )

    result = {
        "raceId": TARGET_RID,
        "raceDate": TARGET_DATE.strftime("%Y-%m-%d"),
        "oddsOfficialDatetime": official_dt,
        "historyMaxDate": str(hist.loc[hist["日付"] < TARGET_DATE, "日付"].max()),
        "note": "A is formal fixed market-correction model. C is research-only odds-free ability shadow; it is not approved for betting recommendations.",
        "A_SpearmanVsMarket": float(pd.Series(out["A順位"]).corr(pd.Series(out["市場順位"]), method="spearman")),
        "C_SpearmanVsMarket": float(pd.Series(out["C順位"]).corr(pd.Series(out["市場順位"]), method="spearman")),
        "rows": out.sort_values("A順位").to_dict(orient="records"),
    }
    path = r.OUT_DIR / "tenno_shadow_compare.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("TENNO_SHADOW_BEGIN")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("TENNO_SHADOW_END")


if __name__ == "__main__":
    main()
