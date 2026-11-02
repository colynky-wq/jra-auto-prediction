# RaceSimulator v0.1 — current KeibaAI adapter

2026-11-03時点の本番JSON形式に合わせるための adapter です。

- `RaceData.json` の距離は `course`（例: `ダ1400m`）から取得。
- `AppResearchData.json` は top-level `races` を参照。
- 各馬の脚質は `styleTendency.dominantStyle` と `distributionPct` を利用。
- 初角位置は `initialPositionHistory.meanRelativePosition` を当該頭数で概算順位に変換。
- 現在の `formation.paceForecast.status=unavailable` は尊重し、未検証ペースを生成せず `MID` を中立値にする。この場合 `paceSource=neutral-mid-fallback` と出力。
- `aiProbability` / `marketProbability` / `odds` / `popularity` は動作計算に入れない。`aiProbability` は終了後の比較だけに使う。

実行:

```bash
python3 production/race_simulator_keibaai_adapter.py \
  --race-data RaceData.json \
  --research-data AppResearchData.json \
  --race-id 202608041101 \
  --runs 1000 \
  --output RaceSimulationData.json
```

独立能力モデルを接続するときだけ `--strengths-json` を追加します。未接続時は `neutral-equal-strength` なので、出力は「展開だけの差」であり本番の最終勝率ではありません。
