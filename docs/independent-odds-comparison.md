# KeibaAI｜独立オッズ照合（研究専用・未統合）

## 現在の状態
このPRは **本番の予測・買い目・購入金額・データ収集スケジュールを変更しません**。
`production/independent_odds_audit.py` はネットワーク通信を行わず、適法に入手した独立オッズのJSONファイルをローカルで読み、既存の発走約10分前の固定記録と比較します。

JRA公式サイトは最新オッズを閲覧できますが、公式ページの情報を自動的に再配布・保存できる許諾があるとは限りません。JRA-VANの速報オッズを使用する場合も契約条件に従ってください。未確認の非公開APIにはアクセスせず、許諾のあるデータ取得方法を別途検証してください。

## 利用方法

```sh
python -m unittest discover -s tests -p 'test_independent_odds_audit.py' -v
python production/independent_odds_audit.py --root . > independent-odds-audit.json
```

標準の入力ディレクトリは `research_independent_odds/YYYYMMDD/RACEID.json`。このディレクトリは `.gitignore` に登録されているため、保存ファイルを公開リポジトリにコミットしません。**提供元のデータを無断転載しない**でください。

このパスに独立した観測記録がなければ、`qualityGate=independent_source_evidence_pending`、`unobservedRaces=105` となります。既存netkeibaオッズを独立データとしてコピーして入力してはいけません。

## 独立オッズ観測JSON仕様

下記は**架空のサンプル**であり、実測値やJRA認証済みデータではありません。

```json
{
  "schemaVersion": 1,
  "source": "licensed_odds_provider",
  "sourceReference": "provider-receipt-id-example",
  "permissionConfirmed": true,
  "raceDate": "2026-11-15",
  "raceID": "SYNTHETIC_RACE_ID",
  "startTime": "16:15",
  "status": "pre_post",
  "observedAtJST": "2026-11-15T16:04:00+09:00",
  "asOfJST": "2026-11-15T16:03:00+09:00",
  "winOdds": [{"number": 1, "odds": 4.2}],
  "wideOdds": [{"number1": 1, "number2": 2, "oddsLow": 5.5, "oddsHigh": 6.0}]
}
```

- `observedAtJST`: 実際に観測・保存された時刻（発走8〜12分前のみ構造的に適格）。
- `asOfJST`: データ提供元によって確認可能なオッズ有効時刻。欠損または観測から5分超の場合は検証対象外（この5分は研究上の暫定基準で、各事業者のSLAを意味しません）。
- `source`: `jra_official_observation`、`licensed_jravan_export`、`licensed_odds_provider` のいずれか。入力した名称自体は真正性を証明しません。
- `sourceReference`: 入手元と記録を照合できる参照ID。実際の提供元へのアクセスを証明するものではありません。
- `permissionConfirmed`: 利用権限を利用者が確認したことの申告。プログラムが法的な許諾を認証するものではありません。
- `winOdds`・`wideOdds`: 馬番は出馬表の馬番、ワイドは順不同の組合せ。すべて揃わなくても入力済みの組合せのみ比較し、欠損件数を報告します。
- `status`: `pre_post` または `open`。確定オッズや払戻データを発走前データに見せかけることは禁止です。

## 出力と品質ゲート

結果は `independent-odds-audit.json` に記録されます。

- 入力がゼロの場合、正式なオッズ優位性は **未検証**。
- 入力に時刻・馬番・値の不整合があれば `rejected`。
- 入力が条件を満たすと `structurally_comparable_not_authenticated`。構造が適格なだけで、**本当に独立したJRA/許諾済みデータであることは保証しません**。
- 本番予測と突き合わせた単勝・ワイドの数値差を表示します。
- 固定済みの参考ワイド候補について、独立オッズに置き換えた場合の研究用期待回収倍率 `repricedFrozenWideResearchTickets` を計算します。過去のモデル予測確率をそのまま使うため、実際に利益があったことや確率校正が済んだことを意味しません。
- `sourceAuthenticityVerified=false`、`monetizableEdgeValidated=false` は、真の出典証明が未確立な限り解除しません。
- 最新の単勝オッズを取得できても、ワイド全ペアの信頼性が検証できるとは限りません。券種別に検証します。

## 本番移行前の必要条件

1. 独立ソースの利用権限・時刻定義・真正性を確認
2. 同一レース、同一時点、同一馬番で単勝およびワイドのオッズを取得
3. 馬番一致・時刻条件・オッズ更新差・研究用期待値の差の再現性を確認
4. 複数開催日にわたる前向き検証を実施
5. 確率校正を別系列で検証する。現行の固定系列は変更しない

GitHub Actionsは独立ソースなしでも**監査ジョブが成功**します。「テスト成功」は「独立した最新オッズ取得に成功」ではありません。
