# RaceSimulator v0.1

KeibaAI の既存「着順・勝率予測AI」と独立して、レース中の位置取り・ペース・詰まり・進路変更・スタミナ配分を Monte Carlo でシミュレーションする最小実装です。

## 最重要ルール

RaceSimulator の動作計算には次を使いません。

- `aiProbability`
- `marketProbability`
- `odds`
- `popularity`

`aiProbability` はシミュレーションが終了した**後**に、既存予測AIとの意見差を表示するためだけに読みます。したがって既存AIの確率やオッズを書き換えても、同じ seed・同じ展開入力なら RaceSimulator の回答は変わりません。この独立性は unit test で固定しています。

## v0.1 が使う情報

- `RaceData.json`: raceID、距離、馬番、馬名
- `AppResearchData.json`: 展開ペース (`formation.pace/paceRaw`)、各馬の脚質、過去の平均コーナー位置
- オプションの独立 strength JSON: 将来の market-independent C model などから渡す能力値

独立 strength が無い場合は全馬を能力 1.0 とし、**純粋な展開差だけ**を比較します。この neutral mode の勝率を、そのまま本番の馬券勝率として扱わないでください。

## シミュレーション状態

各 Monte Carlo run では 0.5 秒ごと（変更可）に以下を更新します。

- 前後位置 (m)
- 速度 (m/s)
- スタミナ
- 内外の仮想 lane
- 前方ブロック
- 進路変更
- 脚質ごとの前半/中盤/4角/終盤の力配分
- SLOW / MID / FAST のペース影響

## 出力

`RaceSimulationData.json` に以下を出します。

- シミュレーション1着率 / 2着内率 / 3着内率
- 平均着順
- 25%地点で先頭にいる確率
- 75%地点で3番手以内にいる確率
- 平均ブロック時間
- 平均進路変更回数
- 25 / 50 / 75 / 90 / 100% 地点の順位レンジ (p20 / median / p80)
- 既存予測AIとの post-hoc 差分（AGREE / SIMULATOR_HIGHER / PREDICTOR_HIGHER）

## 実行例

```bash
python3 production/race_simulator_v01.py \
  --race-data RaceData.json \
  --research-data AppResearchData.json \
  --race-id 2026-10-25-KYOTO-11 \
  --runs 1000 \
  --output RaceSimulationData.json
```

独立能力値をつなぐ場合:

```bash
python3 production/race_simulator_v01.py \
  --race-data RaceData.json \
  --research-data AppResearchData.json \
  --race-id 2026-10-25-KYOTO-11 \
  --strengths-json IndependentStrengths.json \
  --runs 1000 \
  --output RaceSimulationData.json
```

strength JSON は raceID -> 馬番 -> score の形を受け付けます。内部で平均 1.0 に正規化します。

```json
{
  "2026-10-25-KYOTO-11": {
    "1": 1.08,
    "2": 0.97,
    "3": 1.02
  }
}
```

## 次の段階

1. market-independent C model から当日各馬の strength を生成する adapter を接続する。
2. 過去レースのラップ・通過順位で style/pace/スタミナ係数を較正する。
3. 実レース結果と 25/50/75/90% 地点の予測を照合して、展開AI単体の精度を評価する。
4. iOS 側で `RaceSimulationData.json` を読み、コース上の馬アイコンをアニメーション表示する。
5. 既存AIと RaceSimulator の不一致を入力にした「最終統合AI」を**別レイヤー**として学習する。RaceSimulator 自体には既存AI出力を逆流させない。
