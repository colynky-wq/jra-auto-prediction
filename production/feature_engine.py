# ============================================================
# KeibaAI Portable Feature Engine
#
# 固定LightGBMと同じ37特徴量を再計算する。
# Colab / Google Drive依存なし。
# 自動生成ファイル。
# ============================================================

import numpy as np
import pandas as pd
from collections import deque

# 固定LightGBMの特徴量順
ALLF = ['1着率', 'log間隔', 'クラス値f', 'クラス差', '乗り替わり', '内枠先行', '出走数c', '前走上がり差f', '前走人気f', '前走着順率f', '前走秒差f', '同芝ダ回数c', '同芝ダ平均', '同距離帯回数c', '同距離帯平均', '増減f', '増減絶対', '市場ロジット', '平均着順', '年齢', '斤量', '斤量変化', '昇級', '枠位置', '点数差', '脚質初角', '芝ダ替わり', '調教師log数', '調教師勝率', '調教師複勝率', '距離変化', '降級', '頭数f', '馬体重f', '騎手log数', '騎手勝率', '騎手複勝率']


def _prior_mean(x, keys, prior=0.5, m=2):          # 過去の平均（回数が少ないうちは平均に寄せる）
    xv = x.fillna(0); has = x.notna().astype(float)
    s = xv.groupby(keys).cumsum() - xv; c = has.groupby(keys).cumsum() - has
    return (s + prior * m) / (c + m), c


def _trailing(d, key, days=365):                    # 過去365日の勝ち・複勝（当日は含めない）
    n_, w_, p_ = np.zeros(len(d)), np.zeros(len(d)), np.zeros(len(d))
    t_all = d["日付"].values.astype("datetime64[D]").astype("int64")
    W, P = d["勝ち"].values, d["複勝"].values
    for k, pos in d.groupby(key).indices.items():
        t = t_all[pos]; cw = np.r_[0, np.cumsum(W[pos])]; cp = np.r_[0, np.cumsum(P[pos])]
        lo = np.searchsorted(t, t - days, side="left"); hi = np.searchsorted(t, t, side="left")
        n_[pos] = hi - lo; w_[pos] = cw[hi] - cw[lo]; p_[pos] = cp[hi] - cp[lo]
    return n_, w_, p_


def add_rating(df, K=32, START=1500.0):
    keys = df["馬キー"].values
    rank = df["着順"].values.astype(float)
    n = len(df)
    pre_rate = np.full(n, np.nan); pre_avg = np.full(n, np.nan); pre_win = np.full(n, np.nan); pre_n = np.zeros(n)
    R, H = {}, {}
    for rid, pos in df.groupby("レースID", sort=False).indices.items():
        ks = keys[pos]; rk = rank[pos]
        r0 = np.array([R.get(k, START) for k in ks])
        pre_rate[pos] = r0                                  # ① 先に「結果を知る前の値」を記録
        for p, k in zip(pos, ks):
            h = H.get(k)
            if h:
                pre_avg[p] = np.mean(h); pre_win[p] = np.mean([x == 1 for x in h]); pre_n[p] = len(h)
        ok = ~np.isnan(rk); m = ok.sum()                    # ② そのあとで、結果を反映して更新
        if m >= 2:
            ro, rkk = r0[ok], rk[ok]
            E = 1 / (1 + 10 ** ((ro[None, :] - ro[:, None]) / 400)); np.fill_diagonal(E, 0)
            A = (rkk[:, None] < rkk[None, :]) + 0.5 * (rkk[:, None] == rkk[None, :]); np.fill_diagonal(A, 0)
            new = ro + K * (A.sum(1) / (m - 1) - E.sum(1) / (m - 1))
            for k, v in zip(ks[ok], new):
                R[k] = v
        for k, r in zip(ks[ok], rk[ok]):
            H.setdefault(k, deque(maxlen=5)).append(r)
    df["能力点数"] = pre_rate; df["過去5走平均着順"] = pre_avg; df["過去5走1着率"] = pre_win; df["出走数"] = pre_n
    return df


def add_features(d):
    d["勝ち"] = (d["着順"] == 1).astype(int); d["複勝"] = (d["着順"] <= 3).astype(int)
    g = d.groupby("レースID")
    d["勝ち馬との秒差"] = d["タイム秒"] - g["タイム秒"].transform("min")
    d["上がり差"] = d["上がり3F"] - g["上がり3F"].transform("min")
    d["着順率"] = ((d["着順"] - 1) / (d["頭数"] - 1)).clip(0, 1)
    d["初角率"] = (d["初角"] / d["頭数"]).clip(0, 1)
    d["旧"] = d["出典"].eq("旧データ")
    h = d.groupby("馬キー")
    d["間隔日数"] = (d["日付"] - h["日付"].shift(1)).dt.days
    for c, nm in [("着順率", "前走着順率"), ("勝ち馬との秒差", "前走秒差"), ("上がり差", "前走上がり差"), ("人気", "前走人気"),
                  ("距離", "前走距離"), ("芝ダ", "前走芝ダ"), ("騎手", "前走騎手"), ("斤量", "前走斤量"), ("旧", "前走旧")]:
        d[nm] = h[c].shift(1)

    # G1 休み明け / G2 前走の内容 / G3 馬体重
    d["log間隔"] = np.log1p(d["間隔日数"]).fillna(np.log1p(30))
    d["前走着順率f"] = d["前走着順率"].fillna(0.5); d["前走秒差f"] = d["前走秒差"].clip(upper=5).fillna(1.0)
    d["前走上がり差f"] = d["前走上がり差"].clip(upper=3).fillna(0.5)
    d["前走人気f"] = np.log(d["前走人気"].clip(lower=1)).fillna(np.log(8))
    d["馬体重f"] = d["馬体重"].fillna(d["馬体重"].median()); d["増減f"] = d["増減"].fillna(0).clip(-30, 30)
    d["増減絶対"] = d["増減f"].abs()

    # G4 距離・芝ダの適性
    d["距離帯"] = d["距離"] // 200
    d["同芝ダ平均"], c1 = _prior_mean(d["着順率"], [d["馬キー"], d["芝ダ"]])
    d["同距離帯平均"], c2 = _prior_mean(d["着順率"], [d["馬キー"], d["芝ダ"], d["距離帯"]])
    d["同芝ダ回数c"], d["同距離帯回数c"] = c1.clip(upper=5), c2.clip(upper=5)
    d["距離変化"] = (d["距離"] - d["前走距離"]).fillna(0) / 100.0
    d["芝ダ替わり"] = ((d["芝ダ"] != d["前走芝ダ"]) & d["前走芝ダ"].notna()).astype(float)

    # G5 騎手・調教師の調子（過去365日。履歴が薄い間は平均に寄せる。期間ごとの名前で集計）
    for key in ["騎手", "調教師"]:
        n_, w_, p_ = _trailing(d, key)
        d[key + "勝率"] = (w_ + 30 * 0.07) / (n_ + 30); d[key + "複勝率"] = (p_ + 30 * 0.21) / (n_ + 30)
        d[key + "log数"] = np.log1p(n_)
    d["乗り替わり"] = ((d["騎手"] != d["前走騎手"]) & d["前走騎手"].notna() & (d["旧"] == d["前走旧"])).astype(float)

    # G6 枠順・脚質
    d["枠位置"] = (d["馬番"] / d["頭数"]).clip(0, 1)
    d["脚質初角"], _ = _prior_mean(d["初角率"], [d["馬キー"]], prior=0.5, m=1)
    d["内枠先行"] = (0.5 - d["脚質初角"]) * (0.5 - d["枠位置"]); d["頭数f"] = d["頭数"]

    # G7 クラスの変化
    d["クラス値"] = d["クラス"].map({"新馬": 0, "未勝利": 0, "1勝": 1, "2勝": 2, "3勝": 3, "OP": 4})
    d["前走クラス値"] = d.groupby("馬キー")["クラス値"].shift(1)
    d["クラス差"] = (d["クラス値"] - d["前走クラス値"]).fillna(0)
    d["昇級"] = (d["クラス差"] > 0).astype(float); d["降級"] = (d["クラス差"] < 0).astype(float)
    d["斤量変化"] = (d["斤量"] - d["前走斤量"]).fillna(0).clip(-6, 6); d["クラス値f"] = d["クラス値"].fillna(1)

    # F1 の部品と、市場の確率（オッズから控除を除き、レース内で合計1にそろえる）
    d["平均着順"] = d["過去5走平均着順"].fillna(8); d["1着率"] = d["過去5走1着率"].fillna(0)
    d["出走数c"] = d["出走数"].clip(upper=5)
    d["斤量"] = d["斤量"].fillna(55)
    d["対象"] = (~d["障害"].fillna(False).astype(bool)) & d["単勝オッズ"].notna() & d["人気"].notna()   # 平地で、オッズのある馬
    sub = d[d["対象"]]
    inv = 1 / sub["単勝オッズ"]
    mk = inv / inv.groupby(sub["レースID"]).transform("sum")
    d["市場確率"] = np.nan; d.loc[sub.index, "市場確率"] = mk
    d["市場ロジット"] = np.log(d["市場確率"] / (1 - d["市場確率"]))
    d["点数差"] = np.nan
    d.loc[sub.index, "点数差"] = sub["能力点数"] - sub.groupby("レースID")["能力点数"].transform("mean")
    for c in ALLF:                                   # 空欄は0で埋める（市場まわりは対象外の行だけ空欄のまま）
        if c not in ("市場ロジット", "点数差"):
            d[c] = d[c].fillna(0)
    d.loc[d["対象"], ["市場ロジット", "点数差"]] = d.loc[d["対象"], ["市場ロジット", "点数差"]].fillna(0)
    return d

