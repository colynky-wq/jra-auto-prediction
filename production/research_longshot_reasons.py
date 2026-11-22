"""Compare successful longshots with comparable starters before and after the race."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from research_conditional_longshot import past_races
from research_longshot_top3_v2 import OUTPUT as SPECIALIST
from train_longshot_top3 import HISTORY, ROOT, POPRANK_MIN, WINODDS_MIN

OUT = ROOT / "research/model_candidates/longshot_reasons_v1"
STRATA = ["oddsBand", "芝ダ", "fieldBand", "classBand"]
ODDS = [10, 15, 20, 30, 50, 75, 100, 150, 250, np.inf]
CASE_IDS = ["202607010305", "202609040603", "202609010902", "202608030603"]
PRE = {
    "previousClose": "前走で勝ち馬から0.5秒以内",
    "previousCloseAfterSixth": "前走6着以下で勝ち馬から0.5秒以内（前走6着以下同士）",
    "previousFastFinish": "前走の上がり3Fがレース内3位以内",
    "previousFront": "前走の最初の通過地点で前方3分の1以内",
    "sameConditionPlaced": "近5走に同じ芝・ダートかつ距離差200m以内で3着内（該当条件の経験馬同士）",
    "sameVenuePlaced": "近5走に同じ競馬場・同じ芝・ダートで3着内（該当条件の経験馬同士）",
    "shorter": "200m以上の距離短縮（距離差200m未満と比較）",
    "surfaceSwitch": "芝とダートの替わり",
    "weightDrop": "負担重量が前走より1kg以上軽い",
}
POST = {
    "todayFastFinish": "今回の上がり3Fがレース内3位以内",
    "todayFront": "今回の最初の通過地点で前方3分の1以内",
    "fastForCourse": "前半600mが同条件の2024年基準で速い3分の1",
}


def flag(condition, valid):
    return np.where(valid, condition.astype(float), np.nan)


def prepare(history):
    d = history.loc[(history["芝ダ"].isin(["芝", "ダ"])) & history["着順"].ge(1)].copy()
    d["日付"] = pd.to_datetime(d["日付"], errors="raise")
    d["レースID"] = d["レースID"].astype(str)
    d.sort_values(["馬キー", "日付", "レースID", "馬番"], inplace=True)
    if d["馬キー"].isna().any() or d.duplicated(["馬キー", "日付"]).any():
        raise ValueError("a horse must have at most one valid start per day")
    races = d.groupby("レースID", sort=False)
    d["venue"] = d["レースID"].str[4:6]
    winner = d["タイム秒"].where(d["着順"].eq(1)).groupby(d["レースID"], sort=False).transform("min")
    d["gap"] = (d["タイム秒"] - winner).round(1)
    d["gap"] = d["gap"].where(d["gap"].ge(0))
    d["lateRank"] = races["上がり3F"].rank(method="min", na_option="keep")
    d.loc[races["上がり3F"].transform("count") < d["頭数"], "lateRank"] = np.nan
    corner = d["初角"].between(1, d["頭数"]) & ~((d["venue"] == "04") & (d["芝ダ"] == "芝") & d["距離"].eq(1000))
    d["todayFront"] = flag(d["初角"].le(np.ceil(d["頭数"]/3)), corner)
    prior = d.groupby("馬キー", sort=False)
    previous = prior[["着順", "gap", "lateRank", "距離", "芝ダ", "斤量", "todayFront"]].shift()
    d["prevFinish"], d["prevGap"], d["prevLateRank"] = previous["着順"], previous["gap"], previous["lateRank"]
    d["previousClose"] = flag(d["prevGap"].le(.5), d["prevGap"].notna())
    d["previousCloseAfterSixth"] = flag(d["prevGap"].le(.5), d["prevGap"].notna() & d["prevFinish"].ge(6))
    d["previousFastFinish"] = flag(d["prevLateRank"].le(3), d["prevLateRank"].notna())
    d["previousFront"] = previous["todayFront"]
    diff = d["距離"] - previous["距離"]
    d["shorter"] = flag(diff.le(-200), diff.notna() & diff.lt(200))
    d["surfaceSwitch"] = flag(d["芝ダ"].ne(previous["芝ダ"]), previous["芝ダ"].notna())
    d["weightDrop"] = flag(d["斤量"].sub(previous["斤量"]).le(-1), d["斤量"].notna() & previous["斤量"].notna())
    distance_seen = pd.Series(False, index=d.index)
    venue_seen = distance_seen.copy()
    distance_hit, venue_hit = distance_seen.copy(), venue_seen.copy()
    for step in range(1, 6):
        past = prior[["芝ダ", "距離", "venue", "着順"]].shift(step)
        same_surface = d["芝ダ"].eq(past["芝ダ"])
        same_distance = same_surface & d["距離"].sub(past["距離"]).abs().le(200)
        same_venue = same_surface & d["venue"].eq(past["venue"])
        placed = past["着順"].le(3)
        distance_seen |= same_distance
        venue_seen |= same_venue
        distance_hit |= same_distance & placed
        venue_hit |= same_venue & placed
    d["sameConditionPlaced"] = flag(distance_hit, distance_seen)
    d["sameVenuePlaced"] = flag(venue_hit, venue_seen)
    d["todayFastFinish"] = flag(d["lateRank"].le(3), d["lateRank"].notna())
    return d


def laps():
    records = []
    for path in sorted((ROOT / "research/laps").glob("*/RaceLapData.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["races"]:
            valid = row.get("validation", {})
            if row.get("first600mSeconds") is None or not all(valid.get(k) is True for k in
                    ("headingMatches", "distanceMatches", "winnerTimeMatches", "historyWinnerTimeMatches")):
                continue
            records.append({"レースID": row["raceID"], "日付": pd.Timestamp(row["raceDate"]),
                            "track": row["venue"], "芝ダ": row["surface"], "距離": row["distanceM"],
                            "going": row["going"], "first600": row["first600mSeconds"], "source": row.get("sourceURL")})
    df = pd.DataFrame(records)
    if df.duplicated(["レースID", "日付", "芝ダ", "距離"]).any():
        raise ValueError("ambiguous archived lap records")
    group = ["track", "芝ダ", "距離", "going"]
    refs = df.loc[df["日付"].dt.year.eq(2024)].groupby(group)["first600"].agg(
        paceCut=lambda a: a.quantile(1/3), referenceRaces="size").reset_index()
    recent = df.loc[df["日付"].dt.year.ge(2025)].merge(refs, on=group, how="left", validate="many_to_one")
    recent["fastForCourse"] = flag(recent["first600"].lt(recent["paceCut"]), recent["referenceRaces"].ge(15))
    return recent


def rate_comparison(data, feature, strata=STRATA):
    """Overlap weighting uses the smaller side's count within every stratum."""
    known = data.dropna(subset=[feature])
    g = known.groupby(strata + [feature], observed=True)["placed"].agg(["size", "sum"]).unstack(feature)
    if not all((metric, value) in g for metric in ("size", "sum") for value in (0., 1.)):
        return None
    matched = g.dropna()
    weight = matched["size"].min(axis=1).to_numpy()
    a = (matched["sum"][1.] / matched["size"][1.]).to_numpy()
    b = (matched["sum"][0.] / matched["size"][0.]).to_numpy()
    rates = [100 * float(np.average(x, weights=weight)) for x in (a, b)]
    return {"yesN": int((known[feature] == 1).sum()), "noN": int((known[feature] == 0).sum()),
            "matchedStrata": len(matched), "overlapWeight": int(weight.sum()),
            "adjustedYesPct": rates[0], "adjustedNoPct": rates[1], "deltaPp": rates[0] - rates[1]}


def examples(candidates):
    values = []
    for rid in CASE_IDS:
        race = candidates.loc[candidates["レースID"].eq(rid)]
        pool = race.loc[race["馬名"].eq("トコトコクマチ")] if rid == CASE_IDS[-1] else race.loc[race["placed"]]
        if pool.empty:
            continue
        row = pool.sort_values("単勝オッズ", ascending=False).iloc[0]
        def numeric(key):
            return float(row[key]) if pd.notna(row[key]) else None
        values.append({"raceID": rid, "date": row["日付"].date().isoformat(), "name": row["馬名"],
                       "course": f"{row['芝ダ']}{int(row['距離'])}m", "field": int(row["頭数"]),
                       "finish": int(row["着順"]), "popularity": int(row["人気"]), "odds": float(row["単勝オッズ"]),
                       "prevFinish": numeric("prevFinish"), "prevGap": numeric("prevGap"),
                       "prevLateRank": numeric("prevLateRank"), "early": numeric("初角"),
                       "last600": numeric("上がり3F"), "lateRank": numeric("lateRank"),
                       "paceFirst600": numeric("first600"), "paceCut": numeric("paceCut"),
                       "fastForCourse": numeric("fastForCourse"), "source": row["source"] if pd.notna(row["source"]) else None})
    return values


def markdown(report):
    lines = ["# 人気薄が3着以内に来た条件", "",
             "6番人気以下・最終単勝10倍以上。結果を照合できた平地レースで、同程度の単勝オッズ、芝・ダート、頭数帯、クラス内の好走率を比較。",
             "同一条件で両方の馬がいる組を集計し、少ない方の頭数で重み付けした。各要因は個別に比較したため、率の差は足し合わせられない。", "",
             "| 年 | レース | 対象馬 | 3着以内 |", "|---|---:|---:|---:|"]
    for year, summary in report["periods"].items():
        lines.append(f"| {year} | {summary['races']:,} | {summary['horses']:,} | {summary['placed']:,}（{summary['ratePct']:.2f}%） |")
    lines += ["", "| 最終単勝オッズ | 対象馬 | 3着内率 |", "|---|---:|---:|"]
    for group in report["oddsGroups"]:
        lines.append(f"| {group['band']} | {group['horses']:,} | {group['ratePct']:.2f}% |")
    for name, fields in (("発走前に分かる条件", PRE), ("レース後に分かる展開", POST)):
        lines += ["", f"## {name}", "", "| 条件 | 2025年 条件あり / なし | 2026年 条件あり / なし |",
                  "|---|---:|---:|"]
        for key, label in fields.items():
            cells = []
            for summary in report["periods"].values():
                r = summary["factors"][key]
                cells.append(f"{r['adjustedYesPct']:.2f}% / {r['adjustedNoPct']:.2f}%（{r['deltaPp']:+.2f}pt、比較重み {r['overlapWeight']:,}）" if r else "—")
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "## 実例", ""]
    for r in report["examples"]:
        text = (f"- {r['date']} {r['course']} {r['name']}。{r['popularity']}番人気・{r['odds']:.1f}倍で{r['finish']}着。"
                f"前走着順 {r['prevFinish']}、前走の勝ち馬との秒差 {r['prevGap']}。今回の最初の通過順位 {r['early']} / {r['field']}頭、上がり3F {r['last600']}秒（{r['lateRank']}位）。")
        if r["source"]:
            text += f" [JRA公式成績]({r['source']})"
        lines.append(text)
    lines += ["", "前半ペースの比較はJRAのラップが揃い、同競馬場・芝ダート・距離・馬場状態で2024年に15レース以上あった条件が対象。",
              "上がりや通過順は当日の走りを説明する材料。発走前の特徴には前走以前の値を使用する。",
              "過去の最終オッズに基づく観察結果。騎乗の意図、不利の有無、その馬が好走した因果関係の判定には映像や追加の記録が必要。", ""]
    return "\n".join(lines)


def main():
    digest = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    meta = json.loads((SPECIALIST / "research.json").read_text(encoding="utf-8"))
    if digest != meta["sourceHistorySha256"]:
        raise ValueError("historical source differs from earlier KeibaAI studies")
    hist = pd.read_parquet(HISTORY)
    hist["日付"] = pd.to_datetime(hist["日付"])
    recent = hist.loc[hist["日付"].dt.year.isin([2025, 2026])]
    race_ids = set(past_races(recent)["raceID"])
    data = prepare(hist)
    cohort = data.loc[data["レースID"].isin(race_ids) & data["人気"].ge(POPRANK_MIN) & data["単勝オッズ"].ge(WINODDS_MIN)].copy()
    cohort = cohort.merge(laps(), on=["レースID", "日付", "芝ダ", "距離"], how="left", validate="many_to_one")
    cohort["placed"] = cohort["着順"].le(3)
    cohort["fieldBand"] = pd.cut(cohort["頭数"], [0, 9, 13, 16, 18])
    cohort["oddsBand"] = pd.cut(cohort["単勝オッズ"], ODDS, right=False)
    cohort["classBand"] = cohort["クラス"].replace({"2勝": "2・3勝", "3勝": "2・3勝"})
    report = {"version": "longshot-reasons-v1-20261123", "historySha256": digest, "researchOnly": True, "periods": {}}
    for year, d in cohort.groupby(cohort["日付"].dt.year):
        factors = {k: rate_comparison(d, k) for k in (*PRE, *POST)}
        report["periods"][str(year)] = {"races": int(d["レースID"].nunique()), "horses": len(d),
                                         "placed": int(d["placed"].sum()), "ratePct": 100*float(d["placed"].mean()),
                                         "factors": factors, "paceRaces": int(d.loc[d["fastForCourse"].notna(), "レースID"].nunique())}
    groups = pd.cut(cohort["単勝オッズ"], [10, 20, 50, 100, np.inf], labels=["10〜20倍未満", "20〜50倍未満", "50〜100倍未満", "100倍以上"], right=False)
    report["oddsGroups"] = [{"band": str(band), "horses": len(group), "ratePct": 100*float(group["placed"].mean())}
                            for band, group in cohort.groupby(groups, observed=True)]
    report["examples"] = examples(cohort)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "research.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    (OUT / "README.md").write_text(markdown(report), encoding="utf-8")
    print("LONGSHOT_REASONS_OK", len(cohort), cohort["placed"].sum(), flush=True)


if __name__ == "__main__":
    main()
