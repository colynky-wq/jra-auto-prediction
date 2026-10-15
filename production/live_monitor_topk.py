from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from horse_identity_runtime import assign_horse_keys

import live_monitor_dry_run as base
from topk_runtime import predict_top2_top3


HERE = Path(__file__).resolve().parent
TOP2_MODEL_PATH = HERE / "lightgbm_top2_fixed_2025.txt"
TOP3_MODEL_PATH = HERE / "lightgbm_top3_fixed_2025.txt"


def predict_live_race_topk(
    race: dict,
    hist: pd.DataFrame,
    target_date: pd.Timestamp,
) -> pd.DataFrame:
    """Parallel read-only live prediction with Win + Top2 + Top3 probabilities."""
    race = base.fetch_meta(race)
    rid = race["race_id"]

    if race.get("surface") == "障" or "障害" in str(race.get("race_name", "")):
        raise RuntimeError("obstacle race is out of scope")
    if race.get("surface") not in {"芝", "ダ"} or not race.get("distance"):
        raise RuntimeError(f"course could not be resolved: {race}")

    odds_requested = base.now_jst()
    odds, win_odds_payload = base.fetch_win_odds(rid)
    odds_received = base.now_jst()
    if len(odds) < 2:
        raise RuntimeError("insufficient live win odds")

    entries, entry_time, entry_url = base.fetch_jra_entry_table(
        rid,
        base.TARGET_DATE8,
        base.JST,
    )
    print("JRA official entry parsed:", len(entries), "fetched_at:", entry_time)
    print("JRA entry URL:", entry_url)
    research_entry_evidence = entries.attrs.get('allBetEntryEvidence')

    active = set(odds["馬番"].astype(int))
    entries = entries.loc[entries["馬番"].astype(int).isin(active)].copy()
    entries = entries.sort_values("馬番").reset_index(drop=True)

    if len(entries) != len(odds):
        raise RuntimeError(f"entry/odds count mismatch: {len(entries)} / {len(odds)}")

    hist_jockeys = sorted(set(hist["騎手"].dropna().astype(str)))
    hist_trainers = sorted(set(hist["調教師"].dropna().astype(str)))
    entries["騎手"] = entries["騎手_JRA"].map(
        lambda x: base.match_history_name(x, hist_jockeys)
    )
    entries["調教師"] = entries["調教師_JRA"].map(
        lambda x: base.match_history_name(x, hist_trainers)
    )

    if entries["騎手"].isna().any() or entries["調教師"].isna().any():
        raise RuntimeError("jockey/trainer history-name matching failed")

    entries["馬キー"] = assign_horse_keys(entries, hist, target_date)

    current = entries.merge(odds, on="馬番", how="inner", validate="one_to_one")
    current["人気"] = current["単勝オッズ"].rank(ascending=True, method="min")

    cur = pd.DataFrame(index=np.arange(len(current)))
    cur["レースID"] = rid
    cur["日付"] = target_date
    for col in [
        "馬番", "馬名", "馬キー", "年齢", "斤量", "騎手", "調教師",
        "人気", "単勝オッズ", "馬体重", "増減",
    ]:
        cur[col] = current[col].to_numpy()

    cur["着順"] = np.nan
    cur["出典"] = "GitHubライブ監視Top2Top3ドライラン"
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
    work = base.add_rating(work)
    if len(work) != before_n:
        raise RuntimeError("add_rating changed row count")
    work = base.add_features(work)
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
    if pred[base.ALLF].isna().any().any():
        raise RuntimeError("37 features contain missing values")

    X = pred[base.ALLF].astype(np.float32)
    if not np.isfinite(X.to_numpy()).all():
        raise RuntimeError("37 features contain inf/-inf")

    # Existing fixed win model: same calculation as live_monitor_dry_run.py.
    win_model = lgb.Booster(model_file=str(base.MODEL_PATH))
    if list(win_model.feature_name()) != list(base.ALLF):
        raise RuntimeError("win model feature order mismatch")

    win_tree = win_model.predict(X, raw_score=True, validate_features=True)
    market_logit = pred["市場ロジット"].to_numpy(dtype=float)
    final_logit = market_logit + win_tree
    win_raw = 1.0 / (1.0 + np.exp(-np.clip(final_logit, -50, 50)))
    ai_win = win_raw / win_raw.sum()

    market_win = pred["市場確率"].to_numpy(dtype=float)
    ratio = ai_win / market_win
    ev = ai_win * pred["単勝オッズ"].to_numpy(dtype=float) * 100.0

    topk = predict_top2_top3(
        X,
        market_win,
        TOP2_MODEL_PATH,
        TOP3_MODEL_PATH,
    )

    out = pd.DataFrame(
        {
            "馬番": pred["馬番"].astype(int),
            "馬名": pred["馬名"].astype(str),
            "年齢": pred["年齢"].astype(int),
            "馬キー": pred["馬キー"].astype(str),
            "単勝オッズ": pred["単勝オッズ"].astype(float),
            "市場勝率(%)": market_win * 100.0,
            "AI勝率(%)": ai_win * 100.0,
            "市場2着内率(%)": topk["market_top2"] * 100.0,
            "AI2着内率(%)": topk["ai_top2"] * 100.0,
            "市場3着内率(%)": topk["market_top3"] * 100.0,
            "AI3着内率(%)": topk["ai_top3"] * 100.0,
            "AI÷市場": ratio,
            "期待払戻指数": ev,
        }
    )
    out["研究候補"] = (out["AI÷市場"] >= 1.20) & (out["期待払戻指数"] >= 100)
    out.attrs['allBetEntryEvidence'] = research_entry_evidence
    out.attrs['winOddsSource'] = {'payload':win_odds_payload,
        'requestStartedAtJST':odds_requested.isoformat(),'receivedAtJST':odds_received.isoformat()}

    p1 = ai_win
    p2 = topk["ai_top2"]
    p3 = topk["ai_top3"]
    violation = (p1 > p2 + 1e-10) | (p2 > p3 + 1e-10)
    out["確率整合性OK"] = ~violation

    print()
    print("-" * 140)
    print(
        rid,
        race["venue"],
        f"{race['race_no']}R",
        race["race_name"],
        f"{race['surface']}{race['distance']}m",
        race["class"],
    )
    print(
        out.to_string(
            index=False,
            formatters={
                "単勝オッズ": "{:.1f}".format,
                "市場勝率(%)": "{:.3f}".format,
                "AI勝率(%)": "{:.3f}".format,
                "市場2着内率(%)": "{:.3f}".format,
                "AI2着内率(%)": "{:.3f}".format,
                "市場3着内率(%)": "{:.3f}".format,
                "AI3着内率(%)": "{:.3f}".format,
                "AI÷市場": "{:.4f}".format,
                "期待払戻指数": "{:.3f}".format,
            },
        )
    )

    print(
        "probability sums:",
        f"win={ai_win.sum():.12f}",
        f"top2={p2.sum():.12f}",
        f"top3={p3.sum():.12f}",
    )
    print("P(win) <= P(top2) <= P(top3) violations:", int(violation.sum()), "/", len(out))
    if violation.any():
        print(
            out.loc[
                violation,
                ["馬番", "馬名", "AI勝率(%)", "AI2着内率(%)", "AI3着内率(%)"],
            ].to_string(index=False)
        )

    cand = out.loc[out["研究候補"], ["馬番", "馬名", "AI÷市場", "期待払戻指数"]]
    print("research candidates:", cand.to_dict("records"))
    print("RaceData.json modified: NO")
    print("prediction files modified: NO")
    print("existing win model modified: NO")
    print("-" * 140)
    return out


def main() -> None:
    for path in (base.HISTORY_PATH, base.MODEL_PATH, TOP2_MODEL_PATH, TOP3_MODEL_PATH):
        if not path.exists():
            base.fail(f"Missing required file: {path}")

    print("Top2/Top3 parallel runtime enabled: READ ONLY")
    print("Existing live_monitor_dry_run.py remains unchanged.")
    base.predict_live_race = predict_live_race_topk
    base.main()


if __name__ == "__main__":
    main()
