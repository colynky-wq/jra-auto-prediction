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
 
## Mac上の自動受信（実装済み・取得元接続は未設定）

正当に利用可能なデータ提供ソフトが `licensed_odds_drop/YYYYMMDD/RACEID.json` に上記の形式のJSONを書き出せる場合、KeibaAIの保存済み予測との照合用に自動取込できます。取得元を決めずに特定サイトをクローリングするものではありません。

```bash
# まず1回だけ確認（新規の独立オッズがなければ取り込み件数0）
python production/independent_odds_dropwatch.py --root . --source-dir licensed_odds_drop --accepted-dir research_independent_odds

# 許可された提供元のエクスポート先を監視（30秒間隔でローカルのみ確認）
python production/independent_odds_dropwatch.py --root . --source-dir licensed_odds_drop --accepted-dir research_independent_odds --watch --poll-seconds 30

# 自動で取り込んだ記録を105レースの正式保存記録と照合
python production/independent_odds_audit.py --root . --independent-dir research_independent_odds
```

`--watch`はMacのターミナルが動作している間に限って実行されます。実際の外部データソフトとの接続、Mac起動時の自動起動、契約・認証は**未設定**です。個別の許可されたエクスポート形式が判明したら、保存前の書式変換アダプターを別に実装します。

取り込む際の検査：

- 12桁のレースIDと開催日フォルダー、対応する凍結済みレース入力が存在
- 発走8〜12分前に観測され、申告元データ時刻が観測から0〜5分以内
- レースID、開始時刻、馬番・組合せ、単勝・ワイドオッズ範囲が整合
- `permissionConfirmed=true` かつ `sourceReference` がある
- 取得元データが追記されても既に受理した同一レースの観測ファイルは**上書きしない**
- 元データは `licensed_odds_drop/`、受理データは `research_independent_odds/` に保存し、両者ともGit管理対象外

**重要**：`permissionConfirmed`は入力データの自己申告であり、契約、出所、真正性を証明しません。実データ0件の状態でもプログラムのテストは成功します。過去の確定オッズを発走前の観測データとして入力してはいけません。

## 情報源の選定（2026-11-17時点）

- **JRA-VAN Data Lab. / JV-Link**：Windows対応。REST APIではなくWindows用ローカルコンポーネント。契約済みWindows環境の別プロセスから、許された範囲のデータをエクスポートする設計が必要
  - https://developer.jra-van.jp/t/topic/614
  - https://jra-van.jp/info/rule.html
- **KeiBa-ODDS-API（Team-Nave）**：Macにも対応し、単勝・ワイドを含む複数券種のリアルタイムオッズを案内。公表個人向け価格は14,300円/月。ただし料金とデータ利用条件がKeibaAIの用途に合致するか、契約前に直接確認すること
  - https://www.team-nave.com/system/jp/products/kboddsapi/
- **JRA公式Web**：オッズの閲覧機能は確認できるが、機械的な収集・保存・二次利用に必要な条件は未確認。許可されない取得方法を実装しない
  - https://www.jra.go.jp/news/202512/121802.html

選定前の現状は、オッズ源の自動取得が**未接続**であり、期待値の優位性は引き続き未検証です。
