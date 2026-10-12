# ============================================================
# KeibaAI - running style research (research only)
#
# Uses the already leakage-safe pre-race feature 脚質初角, which is the
# smoothed historical mean of first-corner position rate. The current race's
# first-corner position is used only as the evaluation label.
#
# Evaluation: 2025 only. 2026 is excluded.
# Production models are not modified.
# ============================================================

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from feature_engine import add_features, add_rating


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
ARTIFACT_DIR = REPO / "running_style_artifacts"
TEST_START = pd.Timestamp("2025-01-01")
TEST_END = pd.Timestamp("2025-12-31")

EXPECTED_BASE_COLS = [
    "レースID", "日付", "馬番", "馬名", "馬キー", "年齢", "斤量", "騎手", "調教師",
    "着順", "人気", "単勝オッズ", "出典", "芝ダ", "距離", "クラス", "障害", "馬体重",
    "増減", "タイム秒", "上がり3F", "初角", "頭数",
]

STYLE_ORDER = ["逃げ", "先行", "差し", "追込"]


def fail(message: str) -> None:
    print("\n" + "=" * 100)
    print("KEIBAAI RUNNING STYLE RESEARCH: FAILED")
    print("=" * 100)
    print(message)
    sys.exit(1)


def classify_style(rate: float) -> str:
    if not np.isfinite(rate):
        return "不明"
    if rate <= 0.18:
        return "逃げ"
    if rate <= 0.38:
        return "先行"
    if rate <= 0.65:
        return "差し"
    return "追込"


def experience_group(n: float) -> str:
    if not np.isfinite(n) or n <= 0:
        return "初出走"
    if n <= 1:
        return "過去1走"
    if n <= 3:
        return "過去2-3走"
    return "過去4走以上"


def style_index(s: str) -> int:
    try:
        return STYLE_ORDER.index(s)
    except ValueError:
        return -99


def main() -> None:
    print("=" * 100)
    print("KeibaAI Running Style Research")
    print("JST:", datetime.now(JST).isoformat())
    print("evaluation: 2025 / 2026 DATA USED: NO / production modified: NO")
    print("=" * 100)

    if not HISTORY_PATH.exists():
        fail(f"history not found: {HISTORY_PATH}")

    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    if list(hist.columns) != EXPECTED_BASE_COLS:
        fail("production_history.parquet is not the expected 23-column schema")

    # Keep all history through 2025 so pre-race style is built only from prior races.
    work = hist.loc[hist["日付"] <= TEST_END].copy()
    work = work.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    if (work["日付"].dt.year >= 2026).any():
        fail("2026 row entered research frame")

    print("source rows:", f"{len(hist):,}", "latest:", hist["日付"].max())
    print("add_rating...")
    work = add_rating(work)
    print("add_features...")
    work = add_features(work)

    test = work.loc[
        (work["日付"] >= TEST_START)
        & (work["日付"] <= TEST_END)
        & (~work["障害"].fillna(False).astype(bool))
        & work["初角"].notna()
        & work["頭数"].notna()
        & (pd.to_numeric(work["頭数"], errors="coerce") >= 4)
    ].copy()

    if test.empty:
        fail("no 2025 rows with first-corner data")

    test["actualFirstCornerRate"] = (
        pd.to_numeric(test["初角"], errors="coerce")
        / pd.to_numeric(test["頭数"], errors="coerce")
    ).clip(0, 1)
    test["predictedStyleRate"] = pd.to_numeric(test["脚質初角"], errors="coerce").clip(0, 1)
    test = test.loc[
        np.isfinite(test["actualFirstCornerRate"])
        & np.isfinite(test["predictedStyleRate"])
    ].copy()

    test["predictedStyle"] = test["predictedStyleRate"].map(classify_style)
    test["actualStyle"] = test["actualFirstCornerRate"].map(classify_style)
    test["experience"] = pd.to_numeric(test["出走数"], errors="coerce").map(experience_group)
    test["absRateError"] = (test["predictedStyleRate"] - test["actualFirstCornerRate"]).abs()
    test["exactStyle"] = test["predictedStyle"] == test["actualStyle"]
    test["withinOneStyle"] = [
        abs(style_index(a) - style_index(b)) <= 1
        for a, b in zip(test["predictedStyle"], test["actualStyle"])
    ]

    overall = {
        "horses": int(len(test)),
        "races": int(test["レースID"].nunique()),
        "exactStyleAccuracy": float(test["exactStyle"].mean()),
        "withinOneStyleAccuracy": float(test["withinOneStyle"].mean()),
        "meanAbsoluteFirstCornerRateError": float(test["absRateError"].mean()),
        "medianAbsoluteFirstCornerRateError": float(test["absRateError"].median()),
    }

    by_experience = []
    for label in ["初出走", "過去1走", "過去2-3走", "過去4走以上"]:
        g = test.loc[test["experience"] == label]
        if g.empty:
            continue
        by_experience.append({
            "experience": label,
            "horses": int(len(g)),
            "exactStyleAccuracy": float(g["exactStyle"].mean()),
            "withinOneStyleAccuracy": float(g["withinOneStyle"].mean()),
            "meanAbsoluteFirstCornerRateError": float(g["absRateError"].mean()),
        })

    transition = []
    for pred in STYLE_ORDER:
        g = test.loc[test["predictedStyle"] == pred]
        if g.empty:
            continue
        row = {"predicted": pred, "horses": int(len(g))}
        for actual in STYLE_ORDER:
            row[actual] = float((g["actualStyle"] == actual).mean())
        transition.append(row)

    # Race-level composition check: how many front-position horses were expected vs observed.
    race_rows = []
    for race_id, g in test.groupby("レースID", sort=False):
        pred_front = int(g["predictedStyle"].isin(["逃げ", "先行"]).sum())
        actual_front = int(g["actualStyle"].isin(["逃げ", "先行"]).sum())
        pred_escape = int((g["predictedStyle"] == "逃げ").sum())
        actual_escape = int((g["actualStyle"] == "逃げ").sum())
        race_rows.append({
            "raceID": str(race_id),
            "date": pd.Timestamp(g["日付"].iloc[0]).strftime("%Y-%m-%d"),
            "fieldSize": int(len(g)),
            "predictedFrontCount": pred_front,
            "actualFrontCount": actual_front,
            "predictedEscapeCount": pred_escape,
            "actualEscapeCount": actual_escape,
            "frontCountAbsError": abs(pred_front - actual_front),
            "escapeCountAbsError": abs(pred_escape - actual_escape),
        })
    race_df = pd.DataFrame(race_rows)

    race_summary = {
        "races": int(len(race_df)),
        "meanFrontCountAbsError": float(race_df["frontCountAbsError"].mean()),
        "meanEscapeCountAbsError": float(race_df["escapeCountAbsError"].mean()),
        "frontCountExactRate": float((race_df["frontCountAbsError"] == 0).mean()),
        "escapeCountWithinOneRate": float((race_df["escapeCountAbsError"] <= 1).mean()),
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    export_cols = [
        "レースID", "日付", "馬番", "馬名", "頭数", "出走数",
        "predictedStyleRate", "predictedStyle", "actualFirstCornerRate", "actualStyle",
        "experience", "absRateError", "exactStyle", "withinOneStyle",
    ]
    test[export_cols].to_csv(
        ARTIFACT_DIR / "running_style_horses_2025.csv",
        index=False,
        encoding="utf-8-sig",
    )
    race_df.to_csv(
        ARTIFACT_DIR / "running_style_races_2025.csv",
        index=False,
        encoding="utf-8-sig",
    )

    result = {
        "generatedAtJST": datetime.now(JST).isoformat(),
        "researchOnly": True,
        "productionModelsModified": False,
        "data2026Used": False,
        "evaluation": "2025",
        "styleDefinition": {
            "escape": "historical first-corner rate <= 0.18",
            "front": "0.18 < rate <= 0.38",
            "stalker": "0.38 < rate <= 0.65",
            "closer": "rate > 0.65",
            "japaneseLabels": STYLE_ORDER,
        },
        "overall": overall,
        "byExperience": by_experience,
        "transition": transition,
        "raceComposition": race_summary,
        "recommendation": "Use predictedStyle as an app-facing tendency label, but show uncertainty for first-time/low-history horses. Do not call this a pace model yet.",
        "limitations": [
            "Only first-corner position is available in production_history.parquet.",
            "A true pace model needs richer corner positions and/or sectional pace information.",
            "Running style is a tendency and can change with draw, rider tactics, distance and race composition.",
        ],
    }
    with (ARTIFACT_DIR / "running_style_research_2025.json").open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    md = []
    md.append("# KeibaAI 脚質研究（2025）")
    md.append("")
    md.append("現行37特徴量にすでに含まれる `脚質初角` を、アプリ表示用の脚質ラベルに使えるか確認した研究です。")
    md.append("現在レースの初角は答え合わせにだけ使い、予測値は過去レースのみから作られています。")
    md.append("")
    md.append("## 全体")
    md.append("")
    md.append(f"- 対象: {overall['horses']:,}頭 / {overall['races']:,}レース")
    md.append(f"- 4分類完全一致: {100*overall['exactStyleAccuracy']:.2f}%")
    md.append(f"- 隣の脚質まで許容: {100*overall['withinOneStyleAccuracy']:.2f}%")
    md.append(f"- 初角率の平均絶対誤差: {overall['meanAbsoluteFirstCornerRateError']:.4f}")
    md.append("")
    md.append("## 経験別")
    md.append("")
    md.append("| 経験 | 頭数 | 4分類一致 | 隣まで許容 | 初角率平均誤差 |")
    md.append("|---|---:|---:|---:|---:|")
    for r in by_experience:
        md.append(
            f"| {r['experience']} | {r['horses']} | {100*r['exactStyleAccuracy']:.2f}% | "
            f"{100*r['withinOneStyleAccuracy']:.2f}% | {r['meanAbsoluteFirstCornerRateError']:.4f} |"
        )
    md.append("")
    md.append("## 結論")
    md.append("")
    md.append("まずは『逃げ・先行・差し・追込の傾向』として表示可能かを判断します。")
    md.append("ただし、現データは初角のみなので『ハイペース/スローペース』を確定的に表示する段階ではありません。")
    (ARTIFACT_DIR / "running_style_research_2025.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print("\nOVERALL")
    print(overall)
    print("\nBY EXPERIENCE")
    for row in by_experience:
        print(row)
    print("\nRACE COMPOSITION")
    print(race_summary)
    print("\nRESEARCH SUCCESS")
    print("artifact:", ARTIFACT_DIR)


if __name__ == "__main__":
    main()
