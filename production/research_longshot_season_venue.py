"""Chronological tests of racecourse/season calibration for longshot probabilities."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.sparse import csr_matrix

from research_longshot_position import add_top3_scores, top_score
from research_longshot_top3_v2 import OUTPUT as SPECIALIST
from train_longshot_top3 import HISTORY, ROOT, logit, sigmoid
from train_top2_top3_models import binary_logloss

OUT = ROOT / "research/model_candidates/longshot_season_venue_v1"
VENUES = dict(zip([f"{i:02}" for i in range(1, 11)],
                  ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"), strict=True))
SEASONS = ("冬", "春", "夏", "秋")
MODES = ("standard", "venue", "season", "both", "interaction")
PENALTY = {"standard": 20., "venue": 75., "season": 75., "venueSeason": 150.}
SEED = 20261123


def context(data):
    d = data.copy()
    d["venue"] = d["レースID"].astype(str).str[4:6].map(VENUES)
    d["season"] = pd.to_datetime(d["日付"]).dt.month.map(lambda m: SEASONS[(m % 12) // 3])
    if d[["venue", "season"]].isna().any().any():
        raise ValueError("unknown venue or season")
    d["venueSeason"] = d["venue"] + "/" + d["season"]
    d["oddsBand"] = pd.cut(d["単勝オッズ"], [10,15,20,30,50,75,100,150,250,np.inf], right=False).astype(str)
    d["fieldBand"] = pd.cut(d["頭数"], [0,9,13,16,18]).astype(str)
    d["distanceBand"] = pd.cut(d["距離"], [0,1400,1800,2200,np.inf]).astype(str)
    d["popularityBand"] = pd.cut(d["人気"], [5,8,12,18]).astype(str)
    return d


def market_data(history):
    d = history.loc[history["芝ダ"].isin(["芝", "ダ"]) & history["着順"].notna()
                    & history["単勝オッズ"].gt(0) & history["人気"].ge(1)].copy().reset_index(drop=True)
    g = d.groupby("レースID", sort=False)
    full = g["馬番"].transform("size").eq(d["頭数"]) & g["馬番"].transform("nunique").eq(d["頭数"]) \
        & d["頭数"].ge(3)
    for rank in (1, 2, 3):
        full &= d["着順"].eq(rank).groupby(d["レースID"], sort=False).transform("sum").eq(1)
    d = d.loc[full].reset_index(drop=True)
    odds = 1/d["単勝オッズ"]
    d["市場確率"] = odds/odds.groupby(d["レースID"], sort=False).transform("sum")
    # The existing helper also checks a 36-column schema. Only its market
    # computation is needed here, with the same odds and cohort rules.
    from train_top2_top3_models import plackett_luce_top2_top3
    d["marketTop3"] = plackett_luce_top2_top3(d)[1]
    d = d.loc[d["人気"].ge(6) & d["単勝オッズ"].ge(10)].copy()
    d["win"] = d["着順"].eq(1).astype(int)
    d["top3"] = d["着順"].le(3).astype(int)
    return context(d)


def fit(frame, event, base, mode, *, controls):
    """Penalized additive corrections to log odds, learned on older records."""
    blocks = list(controls)
    if mode in ("venue", "both", "interaction"):
        blocks.append("venue")
    if mode in ("season", "both", "interaction"):
        blocks.append("season")
    if mode == "interaction":
        blocks.append("venueSeason")
    string_columns = {name: frame[name].astype(str) for name in blocks}
    columns = [(name, value) for name in blocks for value in sorted(set(string_columns[name]))]
    x = csr_matrix(np.column_stack([np.ones(len(frame), dtype=bool)] + [
        string_columns[name].eq(value).to_numpy() for name, value in columns]), dtype=float)
    penalty = np.asarray([1.] + [PENALTY.get(name, PENALTY["standard"]) for name, _ in columns])
    z = logit(frame[base])
    y = frame[event].to_numpy(dtype=float)

    def objective(coef):
        v = z + x @ coef
        loss = (np.logaddexp(0., v)-y*v).sum() + .5*np.dot(penalty, coef**2)
        jac = x.T @ (sigmoid(v)-y) + penalty*coef
        return loss, jac

    result = minimize(objective, np.zeros(x.shape[1]), method="L-BFGS-B", jac=True,
                      options={"maxiter": 300, "ftol": 1e-11})
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError(f"calibration failed for {mode}: {result.message}")
    terms = {}
    for (name, value), coefficient in zip(columns, result.x[1:], strict=True):
        terms.setdefault(name, {})[value] = float(coefficient)
    return {"intercept": float(result.x[0]), "terms": terms}


def apply(frame, base, model):
    result = logit(frame[base]) + model["intercept"]
    for name, values in model["terms"].items():
        result = result + frame[name].astype(str).map(values).fillna(0).to_numpy(dtype=float)
    return sigmoid(result)


def date_interval(frame, delta):
    cluster = pd.DataFrame({"day": frame["日付"].to_numpy(), "delta": delta}).groupby("day")["delta"].agg(["sum", "size"])
    if len(cluster) < 20:
        return None
    rng = np.random.default_rng(SEED)
    keys = rng.integers(0, len(cluster), size=(1500,len(cluster)))
    value = cluster["sum"].to_numpy()[keys].sum(axis=1)/cluster["size"].to_numpy()[keys].sum(axis=1)
    return np.quantile(value, [.025,.975]).tolist()


def compare(frame, event, baseline, candidate):
    y = frame[event].to_numpy()
    a, b = (candidate-y)**2, (baseline-y)**2
    picks = frame.assign(baseP=baseline, candidateP=candidate)
    before, after = top_score(picks, "baseP"), top_score(picks, "candidateP")
    return {"horses": len(frame), "races": len(before), "baselineBrier": float(b.mean()), "candidateBrier": float(a.mean()),
            "deltaBrier95": date_interval(frame, a-b), "baselineLogLoss": binary_logloss(y,baseline),
            "candidateLogLoss": binary_logloss(y,candidate), "basePickHits": int(before[event].sum()),
            "candidatePickHits": int(after[event].sum()), "changedSelections": int((before["馬番"]!=after["馬番"]).sum())}


def compare_race_wins(frame, baseline, candidate):
    """Exactly one runner can win in this cohort, so sum runner win probabilities."""
    r = frame.assign(baseline=baseline, candidate=candidate).groupby("レースID", as_index=False).agg(
        {"日付": "first", "win": "sum", "baseline": "sum", "candidate": "sum"})
    if not r["win"].isin([0, 1]).all() or not r[["baseline", "candidate"]].stack().between(0, 1).all():
        raise ValueError("invalid race-level longshot win probability")
    y, p0, p1 = (r[key].to_numpy() for key in ("win", "baseline", "candidate"))
    loss0, loss1 = (p0-y)**2, (p1-y)**2
    return {"races": len(r), "actualLongshotWinPct": 100*float(y.mean()),
            "baselineBrier": float(loss0.mean()), "candidateBrier": float(loss1.mean()),
            "deltaBrier95": date_interval(r, loss1-loss0),
            "baselineLogLoss": binary_logloss(y,p0), "candidateLogLoss": binary_logloss(y,p1)}


def group_summary(frame, event, baseline):
    d = frame.assign(base=baseline, residual=frame[event].to_numpy()-baseline)
    out = {}
    for name in ("venue", "season", "venueSeason"):
        groups = {}
        for key, group in d.groupby(name, sort=True):
            if len(group) < 350 or group["日付"].nunique() < 6:
                continue
            rates = {"horses": len(group), "races": group["レースID"].nunique(),
                     "actualPct": 100*float(group[event].mean()), "expectedPct": 100*float(group["base"].mean()),
                     "deltaPp": 100*float(group["residual"].mean())}
            if event == "win":
                rates["racesWithLongshotWinPct"] = 100*float(group.groupby("レースID")[event].max().mean())
            interval = date_interval(group, group["residual"].to_numpy())
            rates["intervalPp"] = [x*100 for x in interval] if interval is not None else None
            groups[key] = rates
        out[name] = groups
    return out


def market_study(data, event, base):
    year = data["日付"].dt.year
    older, tune = data.loc[year.le(2023)], data.loc[year.eq(2024)]
    controls = ("oddsBand", "popularityBand", "芝ダ", "fieldBand", "distanceBand", "クラス")
    projections = {m: fit(older, event, base, m, controls=controls) for m in MODES}
    costs = {m: binary_logloss(tune[event], apply(tune,base,p)) for m,p in projections.items()}
    mode = min(MODES, key=costs.get)
    train = data.loc[year.le(2024)]
    core = fit(train, event, base, "standard", controls=controls)
    selected = core if mode == "standard" else fit(train, event, base, mode, controls=controls)
    report = {"modelSelectedOn2024": mode, "validationLogLoss": costs, "periods": {}, "groupSummary": {}}
    if event == "win":
        report["raceWinPeriods"] = {}
    for y in (2025, 2026):
        observed = data.loc[year.eq(y)]
        p = apply(observed,base,core)
        q = apply(observed,base,selected)
        report["periods"][str(y)] = compare(observed,event,p,q)
        if event == "win":
            report["raceWinPeriods"][str(y)] = compare_race_wins(observed,p,q)
        report["groupSummary"][str(y)] = group_summary(observed,event,p)
    return report, selected


def v2_study(dated, mode):
    a,b = context(dated[2025]),context(dated[2026])
    if a["日付"].max()>=b["日付"].min():
        raise ValueError("V2 contextual calibration must precede evaluation")
    baseline = b["v2Top3"].to_numpy()
    base_calibration = fit(a,"top3","v2Top3","standard",controls=())
    candidate = base_calibration if mode=="standard" else fit(a,"top3","v2Top3",mode,controls=())
    probability, recalibrated = apply(b,"v2Top3",candidate), apply(b,"v2Top3",base_calibration)
    return {"mode":mode,"versusFrozenV2":compare(b,"top3",baseline,probability),
            "versusInterceptOnlyV2":compare(b,"top3",recalibrated,probability)}, candidate


def reliable(row):
    interval = row["deltaBrier95"]
    return (interval is not None and interval[1] < 0
            and row["candidateBrier"] < row["baselineBrier"]
            and row["candidateLogLoss"] < row["baselineLogLoss"])


def markdown(report):
    labels = {"standard":"競馬場・季節の補正なし", "venue":"競馬場", "season":"四季", "both":"競馬場と四季", "interaction":"競馬場・四季・組合せ"}
    lines = ["# 穴馬が勝ちやすい競馬場と季節の検証", "",
             "6番人気以下・最終単勝10倍以上。過去データの勝利と3着内を別々に調べた。冬は12〜2月、あとは3カ月ごと。",
             "最終オッズ・人気帯・芝ダート・頭数・距離帯・クラスを基準側でも補正し、競馬場と季節による上積みを比較。",
             "2014〜2023年で学習、2024年に補正方式を選択。2024年末までで再学習し、2025・2026年を評価。", "",
             "| 指標 | 2024年に選んだ補正 | 年 | 確率誤差Brier 基準→補正 | 1頭選択の的中 基準→補正 |",
             "|---|---|---:|---:|---:|"]
    for field,label in (("win","1着"),("top3","3着内")):
        data = report[field]
        for year,row in data["periods"].items():
            lines.append(f"| {label} | {labels[data['modelSelectedOn2024']]} | {year} | {row['baselineBrier']:.7f} → {row['candidateBrier']:.7f} | {row['basePickHits']} → {row['candidatePickHits']} / {row['races']}R |")
    lines += ["", "レース全体で穴馬が勝つ確率も比較した。頭単位のチェックとは別の指標で、レースは同じ。", "",
              "| 年 | 穴馬が勝ったレース率 | レース単位の確率誤差Brier 基準→補正 |",
              "|---|---:|---:|"]
    for year,row in report["win"]["raceWinPeriods"].items():
        lines.append(f"| {year} | {row['actualLongshotWinPct']:.2f}% | {row['baselineBrier']:.6f} → {row['candidateBrier']:.6f} |")
    lines += ["", "## 場所・季節別に見た穴馬の勝利", "",
              "補正差は、穴馬1頭あたりの実測勝率と、競馬場・季節を入れずに推定した勝率の差。レースの割合は、穴馬がいたレースのうち穴馬が勝った割合。", ""]
    for group, label in (("venue","競馬場"),("season","季節")):
        lines += [f"### {label}", "", "| 区分 | 2025 穴馬勝利レース率 | 2026 同率 | 補正差 2025 / 2026 |",
                  "|---|---:|---:|---:|"]
        left,right = (report["win"]["groupSummary"][y][group] for y in ("2025","2026"))
        for key in sorted(left.keys()&right.keys()):
            a,b=left[key],right[key]
            lines.append(f"| {key} | {a['racesWithLongshotWinPct']:.1f}%（{a['races']}R） | {b['racesWithLongshotWinPct']:.1f}%（{b['races']}R） | {a['deltaPp']:+.2f} / {b['deltaPp']:+.2f}pt |")
        lines.append("")
    lines += ["### 複数年で同じ方向を示した組み合わせの例", "",
              "| 競馬場・季節 | 2025 穴馬勝利レース率 | 2026 同率 | 補正差 2025 / 2026 |",
              "|---|---:|---:|---:|"]
    left,right = (report["win"]["groupSummary"][y]["venueSeason"] for y in ("2025","2026"))
    pairs = [(key, left[key],right[key]) for key in left.keys()&right.keys()
             if left[key]["deltaPp"]*right[key]["deltaPp"]>0]
    pairs.sort(key=lambda item:(-min(abs(item[1]["deltaPp"]),abs(item[2]["deltaPp"])),item[0]))
    for key,a,b in pairs[:4]:
        lines.append(f"| {key} | {a['racesWithLongshotWinPct']:.1f}%（{a['races']}R） | {b['racesWithLongshotWinPct']:.1f}%（{b['races']}R） | {a['deltaPp']:+.2f} / {b['deltaPp']:+.2f}pt |")
    lines += ["", "多数の区分から見つけた事後の例。組み合わせ単位では日数が少なく、採用判定には使っていない。", ""]
    v = report["v2"]["versusFrozenV2"]
    lines += ["## 既存の穴馬V2に加えた場合", "",
              "3着内の補正方式は2024年の市場ベース比較で決定。V2用係数は2025年で算出し、2026年を評価した。",
              f"V2の1頭選択は {v['basePickHits']} → {v['candidatePickHits']} / {v['races']}R、Brierは {v['baselineBrier']:.7f} → {v['candidateBrier']:.7f}。",
              "開催ごとの補正は同じレースの穴馬すべてに同じ値を加えるため、穴馬同士の順位は基本的に維持され、確率の水準が変わる。",
              "判定では、比較対象よりBrier・対数損失が下がり、開催日単位の95%区間も改善側にあるかを確認した。",
              f"1着の補正を実測比較へ進める条件 {'達成' if report['decision']['winRaceContextPassed'] else '未達'}。既存V2に加える条件 {'達成' if report['decision']['top3V2ContextPassed'] else '未達'}。",
              "発走10分前の実測で同じ傾向になるかは未検証。学習した補正係数は研究用に保存。", ""]
    return "\n".join(lines)


def main():
    source = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    meta = json.loads((SPECIALIST / "research.json").read_text(encoding="utf-8"))
    if source != meta["sourceHistorySha256"]:
        raise ValueError("history must match frozen specialist comparison")
    hist = pd.read_parquet(HISTORY)
    hist["日付"] = pd.to_datetime(hist["日付"])
    print("FIT_RACE_CONTEXT",len(hist),flush=True)
    data = market_data(hist)
    win,win_coefficients = market_study(data,"win","市場確率")
    top3,top3_coefficients = market_study(data,"top3","marketTop3")
    print("REPLAY_SPECIALIST_V2",win["modelSelectedOn2024"],top3["modelSelectedOn2024"],flush=True)
    v2,v2_coefficients = v2_study(add_top3_scores(hist,None,meta),top3["modelSelectedOn2024"])
    decision = {"winRaceContextPassed": win["modelSelectedOn2024"]!="standard"
                and all(reliable(row) for row in win["raceWinPeriods"].values()),
                "top3V2ContextPassed": v2["mode"]!="standard" and reliable(v2["versusFrozenV2"])
                and reliable(v2["versusInterceptOnlyV2"])}
    report = {"version":"longshot-season-venue-v1-20261123","researchOnly":True,"historySha256":source,
              "win":win,"top3":top3,"v2":v2,"decision":decision}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"research.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    (OUT/"coefficients.json").write_text(json.dumps({"winMarket":win_coefficients,"top3Market":top3_coefficients,
            "top3V2":v2_coefficients},ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    (OUT/"README.md").write_text(markdown(report),encoding="utf-8")
    print("LONGSHOT_CONTEXT_OK",json.dumps({"win":win["periods"],"top3":top3["periods"],"v2":v2},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
