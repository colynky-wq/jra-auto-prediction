from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

import live_monitor_dry_run as base
from feature_engine import ALLF, add_features, add_rating
from horse_identity_runtime import assign_horse_keys


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
A_MODEL_PATH = HERE / "lightgbm_fixed_2025.txt"
OUT_DIR = REPO / "market_independence_artifacts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TRAIN_START = pd.Timestamp("2014-01-01")
TRAIN_END = pd.Timestamp("2024-12-31")
VALID_START = pd.Timestamp("2025-01-01")
VALID_END = pd.Timestamp("2025-12-31")
FINAL_END = pd.Timestamp("2025-12-31")
TARGET_DATE = pd.Timestamp("2026-09-27")
TARGET_DATE8 = "20260927"
TARGET_RID = "202606040911"
FIXED_ROUNDS = 212
SEED = 20261009

FEATURES_BC = [f for f in ALLF if f != "市場ロジット"]

PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.9,
    "lambda_l2": 10.0,
    "verbosity": -1,
    "num_threads": 2,
    "seed": SEED,
    "feature_fraction_seed": SEED,
    "deterministic": True,
    "force_col_wise": True,
}


def sigmoid(x):
    x = np.asarray(x, dtype=float)
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-9, 1.0 - 1e-9)
    return np.log(p / (1.0 - p))


def normalize_race_total(frame: pd.DataFrame, p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1.0 - 1e-12)
    z = logit(p)
    out = np.full(len(frame), np.nan, dtype=float)
    for _, pos in frame.groupby("レースID", sort=False).indices.items():
        pos = np.asarray(pos, dtype=int)
        zz = z[pos]
        lo, hi = -30.0, 30.0
        for _ in range(80):
            mid = (lo + hi) / 2.0
            s = float(sigmoid(zz + mid).sum())
            if s < 1.0:
                lo = mid
            else:
                hi = mid
        out[pos] = sigmoid(zz + (lo + hi) / 2.0)
    return out


def logloss(y, p):
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1.0 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def brier(y, p):
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return float(np.mean((y - p) ** 2))


def winner_rank_stats(frame: pd.DataFrame, prob: np.ndarray) -> dict:
    temp = frame[["レースID", "着順"]].copy()
    temp["p"] = np.asarray(prob, dtype=float)
    ranks = []
    for _, g in temp.groupby("レースID", sort=False):
        winner = g[g["着順"] == 1]
        if len(winner) != 1:
            continue
        order = g.sort_values("p", ascending=False)
        rank = int(np.where(order.index.to_numpy() == winner.index[0])[0][0]) + 1
        ranks.append(rank)
    if not ranks:
        return {"races": 0}
    a = np.asarray(ranks, dtype=int)
    return {
        "races": int(len(a)),
        "top1": float(np.mean(a <= 1)),
        "top3": float(np.mean(a <= 3)),
        "top5": float(np.mean(a <= 5)),
        "meanWinnerRank": float(a.mean()),
        "medianWinnerRank": float(np.median(a)),
    }


def market_rank_correlation(frame: pd.DataFrame, prob: np.ndarray) -> float:
    vals = []
    temp = frame[["レースID", "市場確率"]].copy()
    temp["p"] = np.asarray(prob, dtype=float)
    for _, g in temp.groupby("レースID", sort=False):
        if len(g) < 3:
            continue
        mr = g["市場確率"].rank(ascending=False, method="average")
        pr = g["p"].rank(ascending=False, method="average")
        c = mr.corr(pr, method="spearman")
        if pd.notna(c):
            vals.append(float(c))
    return float(np.mean(vals)) if vals else float("nan")


def evaluate(frame: pd.DataFrame, p: np.ndarray) -> dict:
    y = (frame["着順"].astype(float) == 1.0).astype(int).to_numpy()
    return {
        "rows": int(len(frame)),
        "races": int(frame["レースID"].nunique()),
        "logloss": logloss(y, p),
        "brier": brier(y, p),
        "winnerRank": winner_rank_stats(frame, p),
        "meanSpearmanVsMarket": market_rank_correlation(frame, p),
        "meanRaceProbabilitySum": float(
            pd.Series(np.asarray(p, dtype=float), index=frame.index)
            .groupby(frame["レースID"])
            .sum()
            .mean()
        ),
    }


def train_candidate(train: pd.DataFrame, final: pd.DataFrame, *, kind: str):
    X_train = train[FEATURES_BC].astype(np.float32)
    X_final = final[FEATURES_BC].astype(np.float32)
    y_train = (train["着順"].astype(float) == 1.0).astype(int).to_numpy()
    y_final = (final["着順"].astype(float) == 1.0).astype(int).to_numpy()

    kwargs_train = {}
    kwargs_final = {}
    if kind == "B":
        kwargs_train["init_score"] = train["市場ロジット"].to_numpy(dtype=float)
        kwargs_final["init_score"] = final["市場ロジット"].to_numpy(dtype=float)
    elif kind != "C":
        raise ValueError(kind)

    dtrain = lgb.Dataset(
        X_train,
        label=y_train,
        feature_name=FEATURES_BC,
        free_raw_data=False,
        **kwargs_train,
    )
    model_train = lgb.train(PARAMS, dtrain, num_boost_round=FIXED_ROUNDS)

    dfinal = lgb.Dataset(
        X_final,
        label=y_final,
        feature_name=FEATURES_BC,
        free_raw_data=False,
        **kwargs_final,
    )
    model_final = lgb.train(PARAMS, dfinal, num_boost_round=FIXED_ROUNDS)
    model_final.save_model(str(OUT_DIR / f"model_{kind}_fixed212.txt"))
    return model_train, model_final


def predict_candidate(model, frame: pd.DataFrame, *, kind: str) -> np.ndarray:
    X = frame[FEATURES_BC].astype(np.float32)
    raw = model.predict(X, raw_score=True, validate_features=True)
    if kind == "B":
        p0 = sigmoid(frame["市場ロジット"].to_numpy(dtype=float) + raw)
    elif kind == "C":
        p0 = sigmoid(raw)
    else:
        raise ValueError(kind)
    return normalize_race_total(frame, p0)


def predict_a(model, frame: pd.DataFrame) -> np.ndarray:
    X = frame[ALLF].astype(np.float32)
    raw = model.predict(X, raw_score=True, validate_features=True)
    p0 = sigmoid(frame["市場ロジット"].to_numpy(dtype=float) + raw)
    return normalize_race_total(frame, p0)


def prepare_training_frames(hist: pd.DataFrame):
    work = hist.loc[
        (hist["日付"] >= TRAIN_START) & (hist["日付"] <= FINAL_END)
    ].copy()
    work = work.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    work = add_rating(work)
    work = add_features(work)

    eligible = (
        work["対象"].fillna(False).astype(bool)
        & work["着順"].notna()
        & work["市場確率"].notna()
        & (~work["障害"].fillna(False).astype(bool))
    )
    work = work.loc[eligible].copy().reset_index(drop=True)

    all_needed = list(dict.fromkeys(ALLF + FEATURES_BC))
    numeric = work[all_needed].apply(pd.to_numeric, errors="coerce")
    finite = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
    work = work.loc[finite].copy().reset_index(drop=True)

    train = work.loc[work["日付"] <= TRAIN_END].copy().reset_index(drop=True)
    valid = work.loc[
        (work["日付"] >= VALID_START) & (work["日付"] <= VALID_END)
    ].copy().reset_index(drop=True)
    final = work.loc[work["日付"] <= FINAL_END].copy().reset_index(drop=True)
    return train, valid, final


def norm(x):
    s = unicodedata.normalize("NFKC", str(x or ""))
    s = re.sub(r"[\s・\.．]", "", s)
    s = re.sub(r"^(美浦|栗東)", "", s)
    return s


def resolve_abbrev(raw, candidates):
    n = norm(raw)
    if not n:
        return str(raw)
    scored = []
    for c in candidates:
        cn = norm(c)
        if n == cn:
            scored.append((10000 + len(cn), c))
        elif cn.startswith(n) or n.startswith(cn):
            scored.append((5000 + min(len(n), len(cn)), c))
        elif n in cn or cn in n:
            scored.append((1000 + min(len(n), len(cn)), c))
    if scored:
        scored.sort(reverse=True)
        return scored[0][1]
    return str(raw)


def historical_entry_table(race_id: str, hist: pd.DataFrame) -> pd.DataFrame:
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    r = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ja"},
        timeout=30,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.content.decode("utf-8", errors="replace"), "html.parser")
    horse_rows = soup.select("tr.HorseList")

    hist_jockeys = sorted(set(hist["騎手"].dropna().astype(str)))
    hist_trainers = sorted(set(hist["調教師"].dropna().astype(str)))
    rows = []
    for tr in horse_rows:
        items = [x.strip() for x in tr.stripped_strings if str(x).strip()]
        if len(items) < 10:
            continue
        sex_idx = None
        for i, x in enumerate(items):
            if re.fullmatch(r"(牡|牝|せん|セン|セ)\s*\d+", x):
                sex_idx = i
                break
        if sex_idx is None or sex_idx < 2 or sex_idx + 6 >= len(items):
            continue
        no = None
        for x in items[: min(4, len(items))]:
            if re.fullmatch(r"\d{1,2}", x):
                v = int(x)
                if 1 <= v <= 18:
                    no = v
        if no is None:
            continue
        name = items[sex_idx - 1]
        age = int(re.search(r"\d+", items[sex_idx]).group())
        try:
            carried = float(re.search(r"\d+(?:\.\d+)?", items[sex_idx + 1]).group())
        except Exception:
            continue
        jockey_raw = items[sex_idx + 2]
        aff = items[sex_idx + 3]
        trainer_raw = items[sex_idx + 4]
        if aff not in {"美浦", "栗東"}:
            continue
        jockey = resolve_abbrev(jockey_raw, hist_jockeys)
        trainer = resolve_abbrev(trainer_raw, hist_trainers)
        bw_token = items[sex_idx + 5]
        diff_token = items[sex_idx + 6]
        mbw = re.search(r"\d{3}", bw_token)
        bw = float(mbw.group()) if mbw else np.nan
        mdiff = re.search(r"([+-]?\d+)", diff_token)
        diff = float(mdiff.group(1)) if mdiff and "前計不" not in diff_token else np.nan
        rows.append(
            {
                "馬番": no,
                "馬名": name,
                "年齢": age,
                "斤量": carried,
                "騎手": jockey,
                "調教師": trainer,
                "馬体重": bw,
                "増減": diff,
            }
        )
    d = pd.DataFrame(rows)
    d = d.drop_duplicates("馬番", keep="first").sort_values("馬番").reset_index(drop=True)
    if len(d) != 16:
        raise RuntimeError(f"historical entry rows expected 16 got {len(d)}")
    return d


def prepare_sprinters(hist: pd.DataFrame) -> pd.DataFrame:
    base.TARGET_DATE8 = TARGET_DATE8
    pre = hist.loc[hist["日付"] < TARGET_DATE].copy()
    pre = pre.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)

    races = base.fetch_schedule(TARGET_DATE8)
    target = [r for r in races if str(r.get("race_id")) == TARGET_RID]
    if len(target) != 1:
        raise RuntimeError(f"target race count={len(target)}")
    race = base.fetch_meta(target[0])
    race["class"] = "OP"

    odds, _ = base.fetch_win_odds(TARGET_RID)
    entries = historical_entry_table(TARGET_RID, pre)
    entries["馬キー"] = assign_horse_keys(entries, pre, TARGET_DATE)
    current = entries.merge(odds, on="馬番", how="inner", validate="one_to_one")
    current["人気"] = current["単勝オッズ"].rank(ascending=True, method="min")

    cur = pd.DataFrame(index=np.arange(len(current)))
    cur["レースID"] = TARGET_RID
    cur["日付"] = TARGET_DATE
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
    cur["出典"] = "market-independence historical replay"
    cur["芝ダ"] = race["surface"]
    cur["距離"] = int(race["distance"])
    cur["クラス"] = race["class"]
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
        .copy()
        .sort_values("馬番")
        .reset_index(drop=True)
    )
    if len(pred) != 16:
        raise RuntimeError(f"target rows={len(pred)}")
    return pred


def rank_series(p):
    return pd.Series(p).rank(ascending=False, method="min").astype(int).to_numpy()


def main():
    print("=" * 120)
    print("KeibaAI market-independence A/B/C experiment")
    print("A: existing fixed market correction model")
    print("B: market init_score only; market_logit removed from features")
    print("C: no market init_score and no market feature")
    print("2026 used for training: NO")
    print("fixed boosting rounds for B/C:", FIXED_ROUNDS)
    print("=" * 120)

    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    print("source latest:", hist["日付"].max(), "rows:", len(hist))

    train, valid, final = prepare_training_frames(hist)
    print("train through 2024:", len(train), "rows", train["レースID"].nunique(), "races")
    print("2025 validation:", len(valid), "rows", valid["レースID"].nunique(), "races")
    print("final through 2025:", len(final), "rows", final["レースID"].nunique(), "races")

    model_a = lgb.Booster(model_file=str(A_MODEL_PATH))
    model_b_train, model_b_final = train_candidate(train, final, kind="B")
    model_c_train, model_c_final = train_candidate(train, final, kind="C")

    market_valid = valid["市場確率"].to_numpy(dtype=float)
    pa_valid = predict_a(model_a, valid)
    pb_valid = predict_candidate(model_b_train, valid, kind="B")
    pc_valid = predict_candidate(model_c_train, valid, kind="C")

    metrics = {
        "protocol": {
            "train": "2014-01-01..2024-12-31",
            "validation": "2025-01-01..2025-12-31",
            "finalTrain": "2014-01-01..2025-12-31",
            "fixedRoundsBC": FIXED_ROUNDS,
            "note": "B/C use fixed 212 rounds; 2025 is not used to tune B/C. A's existing 212 rounds were historically selected using 2025, so A has a selection advantage on this validation year.",
        },
        "2025": {
            "market": evaluate(valid, market_valid),
            "A": evaluate(valid, pa_valid),
            "B": evaluate(valid, pb_valid),
            "C": evaluate(valid, pc_valid),
        },
    }

    target = prepare_sprinters(hist)
    pa = predict_a(model_a, target)
    pb = predict_candidate(model_b_final, target, kind="B")
    pc = predict_candidate(model_c_final, target, kind="C")
    market = target["市場確率"].to_numpy(dtype=float)

    result_rows = hist.loc[hist["レースID"].astype(str) == TARGET_RID, ["馬番", "着順"]].copy()
    result_rows = result_rows.drop_duplicates("馬番", keep="last")
    finish_map = {
        int(r["馬番"]): int(r["着順"])
        for _, r in result_rows.dropna(subset=["着順"]).iterrows()
    }

    comp = target[["馬番", "馬名", "単勝オッズ", "人気"]].copy()
    comp["実着順"] = comp["馬番"].astype(int).map(finish_map)
    comp["市場勝率"] = market
    comp["A勝率"] = pa
    comp["B勝率"] = pb
    comp["C勝率"] = pc
    comp["市場順位"] = rank_series(market)
    comp["A順位"] = rank_series(pa)
    comp["B順位"] = rank_series(pb)
    comp["C順位"] = rank_series(pc)

    def target_stats(prob, rank_col):
        rs = []
        for finish in [1, 2, 3]:
            g = comp.loc[comp["実着順"] == finish]
            if len(g) == 1:
                r = g.iloc[0]
                rs.append(
                    {
                        "finish": finish,
                        "horseNo": int(r["馬番"]),
                        "horseName": str(r["馬名"]),
                        "rank": int(r[rank_col]),
                        "probability": float(prob[int(r.name)]),
                    }
                )
        return rs

    metrics["sprinters"] = {
        "marketSpearman": float(pd.Series(comp["市場順位"]).corr(pd.Series(comp["市場順位"]), method="spearman")),
        "A_SpearmanVsMarket": float(pd.Series(comp["A順位"]).corr(pd.Series(comp["市場順位"]), method="spearman")),
        "B_SpearmanVsMarket": float(pd.Series(comp["B順位"]).corr(pd.Series(comp["市場順位"]), method="spearman")),
        "C_SpearmanVsMarket": float(pd.Series(comp["C順位"]).corr(pd.Series(comp["市場順位"]), method="spearman")),
        "actualTop3Market": target_stats(market, "市場順位"),
        "actualTop3A": target_stats(pa, "A順位"),
        "actualTop3B": target_stats(pb, "B順位"),
        "actualTop3C": target_stats(pc, "C順位"),
    }

    metrics_path = OUT_DIR / "market_independence_metrics.json"
    table_path = OUT_DIR / "sprinters_abc.csv"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    comp.sort_values("C順位").to_csv(table_path, index=False)

    print("MARKET_INDEPENDENCE_METRICS_BEGIN")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("MARKET_INDEPENDENCE_METRICS_END")
    print("SPRINTERS_ABC_BEGIN")
    print(comp.sort_values("C順位").to_json(orient="records", force_ascii=False))
    print("SPRINTERS_ABC_END")
    print("artifacts:", OUT_DIR)


if __name__ == "__main__":
    main()
