"""Compare longshot outcomes across verified JRA race-day going categories."""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from research_longshot_position import add_top3_scores
from research_longshot_season_venue import (
    apply, compare, compare_race_wins, context, date_interval, fit, market_data, reliable,
)
from research_longshot_top3_v2 import OUTPUT as SPECIALIST
from train_longshot_top3 import HISTORY, ROOT

OUT = ROOT / "research/model_candidates/longshot_track_condition_v1"
KEYS = ["レースID", "日付", "芝ダ", "距離"]
GOING = ("良", "稍重", "重", "不良")
SOURCE_CHECKS = ("headingMatches", "distanceMatches", "winnerTimeMatches", "historyWinnerTimeMatches")
RACE_CONTROLS = ("oddsBand", "popularityBand", "芝ダ", "fieldBand", "distanceBand", "クラス", "venue", "season")


def archive_going(root):
    rows = []
    digest = hashlib.sha256()
    for path in sorted((root / "research/laps").glob("*/RaceLapData.json")):
        raw = path.read_bytes()
        digest.update(path.parent.name.encode() + hashlib.sha256(raw).digest())
        for race in json.loads(raw)["races"]:
            checks = race.get("validation", {})
            if race.get("going") not in GOING or not all(checks.get(k) is True for k in SOURCE_CHECKS):
                continue
            rows.append({"レースID": race["raceID"], "日付": pd.Timestamp(race["raceDate"]),
                         "芝ダ": race["surface"], "距離": race["distanceM"], "going": race["going"],
                         "weather": race.get("weather")})
    conditions = pd.DataFrame(rows)
    if conditions.duplicated(KEYS).any():
        raise ValueError("conflicting going records for one race")
    return conditions, digest.hexdigest()


def attach(frame, going):
    out = frame.merge(going, on=KEYS, how="inner", validate="many_to_one")
    out["surfaceGoing"] = out["芝ダ"] + "/" + out["going"]
    return out


def grouped(frame, expected_win, expected_top3):
    d = frame.assign(winResidual=frame["win"].to_numpy()-expected_win,
                     top3Residual=frame["top3"].to_numpy()-expected_top3)
    stats = {}
    for key, sub in d.groupby(["芝ダ", "going"], sort=True):
        race_outcomes = sub.groupby("レースID")["win"].max()
        row = {"races": len(race_outcomes), "raceWins": int(race_outcomes.sum()), "horses":len(sub),
               "wins": int(sub["win"].sum()), "placed": int(sub["top3"].sum()),
               "raceWinPct": 100*float(race_outcomes.mean()), "horseTop3Pct": 100*float(sub["top3"].mean()),
               "uniqueDays": sub["日付"].nunique()}
        for event in ("win", "top3"):
            delta = sub[f"{event}Residual"]
            limits = date_interval(sub, delta.to_numpy())
            row[f"{event}DiffPp"] = 100*float(delta.mean())
            row[f"{event}DiffIntervalPp"] = [100*x for x in limits] if limits is not None else None
        stats["/".join(key)] = row
    return stats


def v2_trials(dated, conditions):
    train, test = (attach(context(dated[y]), conditions) for y in (2025,2026))
    if train["日付"].max() >= test["日付"].min():
        raise ValueError("going adjustment would train on evaluation data")
    # The going-only variant leaves venue or season adjustments out of V2.
    base = fit(train,"top3","v2Top3","standard",controls=())
    candidate = fit(train,"top3","v2Top3","standard",controls=("surfaceGoing",))
    p = apply(test,"v2Top3",candidate)
    return {"againstFrozenV2":compare(test,"top3",test["v2Top3"].to_numpy(),p),
            "againstInterceptOnlyV2":compare(test,"top3",apply(test,"v2Top3",base),p),
            "calibrationRaces":train["レースID"].nunique()},candidate


def markdown(report):
    lines=["# 馬場状態と穴馬の好走率", "",
           "6番人気以下・最終単勝10倍以上。JRAのレース成績で馬場状態を確認でき、履歴と照合できた平地のみ。",
           "表の勝利率は対象の穴馬が勝ったレースの割合。3着内率は穴馬1頭あたりの割合。", "",
           "| 芝・ダートと馬場 | 2025年 勝利率・3着内率 | 2026年 勝利率・3着内率 | 3着内の補正後の差 2025・2026 |",
           "|---|---:|---:|---:|"]
    groups = report["groups"]
    def cell(one):
        return f"{one['raceWinPct']:.1f}%・{one['horseTop3Pct']:.1f}%（{one['races']}R）" if one else "—"
    for surface in ("芝","ダ"):
        for going in GOING:
            key=f"{surface}/{going}"
            left,right = (groups[y].get(key) for y in ("2025","2026"))
            diffs=" / ".join(f"{x['top3DiffPp']:+.2f}pt" if x else "—" for x in (left,right))
            lines.append(f"| {key} | {cell(left)} | {cell(right)} | {diffs} |")
    lines += ["", "差は同じオッズ・人気帯、競馬場、季節、距離帯、クラスなどを考慮した基準予測との比較。",
              "2024年の馬場記録がある分で補正を学習し、2025・2026年の同じレースで既存の基準と比較。",
              "データの少ない区分は率が上下しやすい。天候は保存記録から照合できた数を示し、雨や晴れを馬場状態から推測しない。", "",
              "| 年 | 馬場状態付き | 検証対象 | 天候付き |", "|---|---:|---:|---:|"]
    for yr,row in report["coverage"].items():
        lines.append(f"| {yr} | {row['withGoing']}R | {row['eligible']}R | {row['withWeather']}R |")
    lines += ["", "## 予測モデル比較", "", "| 予測 | 年 | Brier 基準→馬場補正 |", "|---|---:|---:|"]
    for y,result in report["heldOut"].items():
        for key,label in (("raceWin","穴馬が勝つレース"),("top3","穴馬の3着内")):
            p=result[key]
            lines.append(f"| {label} | {y} | {p['baselineBrier']:.6f} → {p['candidateBrier']:.6f} |")
    v = report["v2"]["againstFrozenV2"]
    lines += ["", f"既存V2との比較（2025年で補正、2026年評価）は {v['basePickHits']} → {v['candidatePickHits']} / {v['races']}R。Brier {v['baselineBrier']:.6f} → {v['candidateBrier']:.6f}。",
              f"V2の研究候補採用条件 {'達成' if report['v2ContextPassed'] else '未達'}。",
              "過去検証の馬場はレース後の公式成績から照合。発走10分前の時点で利用できた馬場状態での実測は別途確認が必要。", ""]
    return "\n".join(lines)


def main():
    source = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    meta = json.loads((SPECIALIST / "research.json").read_text(encoding="utf-8"))
    if source != meta["sourceHistorySha256"]:
        raise ValueError("history differs from the frozen V2 study")
    h=pd.read_parquet(HISTORY)
    h["日付"]=pd.to_datetime(h["日付"])
    print("READ_VERIFIED_GOING",flush=True)
    going,digest=archive_going(ROOT)
    d=market_data(h.loc[h["日付"].dt.year.ge(2024)])
    joined=attach(d,going)
    report={"version":"longshot-track-condition-v1-20261123","researchOnly":True,"historySha256":source,
            "lapArchiveSha256":digest,"coverage":{},"groups":{},"heldOut":{}}
    for year in (2024,2025,2026):
        old=d.loc[d["日付"].dt.year.eq(year)]
        new=joined.loc[joined["日付"].dt.year.eq(year)]
        report["coverage"][str(year)]={"eligible":old["レースID"].nunique(),"withGoing":new["レースID"].nunique(),
            "withWeather":new.loc[new["weather"].notna(),"レースID"].nunique()}
    train=joined.loc[joined["日付"].dt.year.eq(2024)]
    pairs={}
    for event,base in (("win","市場確率"),("top3","marketTop3")):
        pairs[event]=(fit(train,event,base,"standard",controls=RACE_CONTROLS),
                      fit(train,event,base,"standard",controls=RACE_CONTROLS+("surfaceGoing",)))
    for year in (2025,2026):
        test=joined.loc[joined["日付"].dt.year.eq(year)]
        w0,w1=(apply(test,"市場確率",m) for m in pairs["win"])
        p0,p1=(apply(test,"marketTop3",m) for m in pairs["top3"])
        report["groups"][str(year)]=grouped(test,w0,p0)
        report["heldOut"][str(year)]={"raceWin":compare_race_wins(test,w0,w1),"top3":compare(test,"top3",p0,p1)}
    print("REPLAY_FROZEN_V2",report["coverage"],flush=True)
    report["v2"],coef=v2_trials(add_top3_scores(h,None,meta),going)
    report["v2ContextPassed"]=all(reliable(report["v2"][k]) for k in ("againstFrozenV2","againstInterceptOnlyV2"))
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT / "research.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    (OUT / "coefficients.json").write_text(json.dumps({"market2024":pairs,"v2From2025":coef},ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    (OUT / "README.md").write_text(markdown(report),encoding="utf-8")
    print("LONGSHOT_GOING_OK",report["v2ContextPassed"],flush=True)


if __name__ == "__main__":
    main()
