# KeibaAI 券種別最高精度化ロードマップ — 2026-10-31

## 結論

正式A（市場補正Winモデル）は変更しない。Cは市場非依存shadowとして維持する。
券種確率は単勝確率の単純積では作らない。

## 実装済み基盤

- Win: 正式A + shadow C
- Top2/Top3: 固定専用LightGBMを本番live runtimeで利用済み
- 単勝/複勝/枠連/馬連/馬単: 発走前実オッズ初回保存基盤あり
- ワイド: 既存研究基盤あり
- 3連複/3連単: 発走前全組合せ実オッズ初回保存基盤あり
- 全券種横断shadow selectorあり。formalRecommendation=false / autoBet=false

## 今回追加

`production/ticket_probability_runtime.py`

Win + 専用Top2 + 専用Top3の3種類の周辺確率に同時に整合するrace-local ranking distributionを構成し、以下の同時確率を生成する。

- Wide: 2頭が同時にTop3
- Quinella: 2頭が順不同で1,2着
- Exacta: 1着→2着
- Trio: 3頭が順不同でTop3
- Trifecta: 1→2→3着

これは専用券種モデルの最終形ではなく、単勝確率だけのPlackett-Luce baselineから一段進んだresearch bridge。正式採用にはOOSで既存baselineを上回ることが必要。

## 評価順序

1. 確率精度: log loss / Brier / calibration / top-k
2. 市場との独立性: rank disagreement / outsider recall
3. 券種別Chaos: Win / Place / Pair / Order / Triple uncertaintyを分離
4. 収益性: 保存済み発走前実オッズのみを使用。確定後オッズによる発見結果は正式採否に使わない
5. 前向き検証: ruleVersion固定後に新規レースのみで評価

## 採用ゲート

- 同一OOS期間で既存baselineより確率指標が改善
- calibration悪化なし
- 十分なサンプル数
- 発走前時刻証拠のある実オッズで期待値/ROIを別評価
- 結果を見た後の閾値変更は新versionへ分離

## Chaos方針

Chaosを共通の一律回避条件にはしない。

- WinChaos: 1着不確実性
- PlaceChaos: Top3不確実性
- PairChaos: 2頭同時イベント不確実性
- OrderChaos: 1-2着順不確実性
- TripleChaos: Top3組合せ/順序不確実性

券種ごとの確率分布entropy・上位候補へのmass集中度・A/C disagreementを使い、券種ごとに別々に評価する。

## 本番保護

この研究は `keibaai-ticket-specific-model-research` branchだけで実施する。main、正式A、正式10分前検証、既存Portfolio Shadow v1のルールは変更しない。
