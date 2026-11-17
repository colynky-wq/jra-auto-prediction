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

**発走前の証拠の保護（今回追加）**：`--watch` 実行時は、ファイルに申告された `observedAtJST` がMacの実際の受信時刻から180秒以内かを追加確認します。20秒より未来の観測や、3分以上前の観測は研究用フォルダーへ取り込みません。一回のみの確認でも `--prospective` を付けると同じ検査を適用します。

通常の一回実行では過去のデータも研究用として読めますが、受信結果は `prospectiveClockCheckPassed=false` と明記し、発走前に観測された実績であると誤って扱いません。時計のチェックに通っても、出所の真正性やデータの新鮮さを独立して証明できるわけではありません。Macの日時設定が正しいことが前提です。

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

## CSV形式からの全自動取込（取得元を導入した後）

提供元がCSVしか出力できない場合も手作業で馬番やオッズを書き換える必要はありません。提供元側で自動生成される次のCSVファイルを取り込みます。

- 取得元CSV：`licensed_odds_csv/YYYYMMDD/RACEID.csv`
- 変換候補JSON：`licensed_odds_drop/YYYYMMDD/RACEID.json`
- 時刻・馬番検査後の研究用観測：`research_independent_odds/YYYYMMDD/RACEID.json`
- 同じ場所の `RACEID.receipt.json` に、受信日時・SHA256・発走前のローカル時刻検査結果を記録（出所の真正性を証明するものではない）

CSVのヘッダーは以下の14列です。

```csv
raceDate,raceID,startTime,source,sourceReference,permissionConfirmed,status,observedAtJST,asOfJST,betType,number1,number2,oddsLow,oddsHigh
2026-11-15,202608050412,16:25,licensed_odds_provider,EXAMPLE-RECEIPT,true,pre_post,2026-11-15T16:14:00+09:00,2026-11-15T16:13:00+09:00,win,1,,4.2,
2026-11-15,202608050412,16:25,licensed_odds_provider,EXAMPLE-RECEIPT,true,pre_post,2026-11-15T16:14:00+09:00,2026-11-15T16:13:00+09:00,wide,1,2,5.5,6.0
```

上の値は架空の書式サンプルです。独立した実測オッズでも、JRAからの正式な許諾済みデータでもありません。

- すべての行でレースID・予定発走時刻・出典・観測日時・有効時刻が一致すること
- 自動変換は発走8〜12分前の観測だけに限定。**早朝のCSVを先に固定して直前の更新を妨げない**
- データ提供元の有効時刻は観測から5分以内。両時刻は明示的なタイムゾーン付き
- 不正な馬番、重複した組合せ、欠損・矛盾したワイドオッズ、同一ファイルの混在時刻は拒否
- 既に受け入れたレースの直前観測は上書きしない。CSV自体は提供元が更新できます

## Macでの一括運転（取得元接続は別途必要）

```bash
# レポジトリのルートで、1回だけ動作確認
python3.11 production/independent_odds_bridge.py --root .

# ターミナルで継続運転。CSV→JSON変換→発走前チェック→研究用保存が一体動作
python3.11 production/independent_odds_bridge.py --root . --watch --poll-seconds 30

# launchd設定のプレビュー（ファイル作成も起動もしない）
python3.11 ops/mac_independent_odds_launchd.py --repo-root .

# 利用するデータ提供元が確定したMacでのみ実行：
# launchd用設定ファイルを作成するが、まだ自動起動しない
python3.11 ops/mac_independent_odds_launchd.py --repo-root . --write

# 明示的に自動起動を許可する場合（上記コマンドで案内される）
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/local.keibaai.independent-odds-research.plist"

# 停止する場合
launchctl bootout gui/$(id -u) "$HOME/Library/LaunchAgents/local.keibaai.independent-odds-research.plist"
```

Python 3.11以上と、Mac上のレポジトリ・正規データ提供元が必要です。システムへの起動登録は**チャット上からは実行していません**。利用者が `--write` と `launchctl bootstrap` を明示的に実行したときだけ有効になります。

いずれのコマンドも外部サイトを自動スクレイピングしません。取得元や利用許諾は、実際に導入するサービスの契約条件に従って確認してください。また、同じレースを一度保存した後の再取得・後日のオッズ上書きはしません。

## 時計の検査と比較時刻の意味

独立オッズの受理条件は、発走8〜12分前、オッズ元時刻は観測から5分以内、ローカルMacへの取り込みは観測から180秒以内（未来時刻は20秒までのずれを許容）です。

正式予測の時刻と独立観測の時刻差は最大2分以内に限定しました。これを超えるデータでは、「同じ時刻の市場とAIの差」を主張できないため、比較対象から除外します。

厳密なJSON読み取りでは重複キー・非有限数値・小数の馬番を拒否します。外部データのURLがnetkeiba自身を示す場合は、独立情報源として扱いません。自動生成した `receipt.json` では保存内容のSHA256を検証できますが、データ提供元の権利や真正性を保証するものではありません。

**現在の実測値は0件です。** CSV経路とMac自動監視は準備段階であり、無許諾のデータ収集や契約手続きを自動実行したものではありません。

## 追加監査の結論（2026-11-18）

独立オッズの入出力について、以下の対策を追加しました。

- 許可済みCSVに含まれる対象レース・開始時刻・出典・元データ時刻の一致を検査。朝のオッズが直前オッズの固定枠を占有する問題を防止
- 取得後のオッズ保存ファイルとローカルの受信日時・SHA256を `RACEID.receipt.json` で結び付ける
- オフラインで読み込んだ過去の保存ファイルと、Macで観測時刻から180秒以内に受け入れたファイルを**異なる品質ラベル**に分類
- 独立した情報源の観測時刻と既存AIの発走前予測時刻は2分以内の差だけ比較
- 重複JSONキー、非有限数値、真偽値や小数の馬番、netkeibaの同一情報を「独立」と偽る参照URLなどを拒否
- CSV・JSONの外部提供元データと研究出力はすべてGitの追跡対象外
- Macの長時間監視では、変化のない状態ログの毎30秒出力を抑制。変化があればすぐ出力し、無変化なら最大1時間おき
- `local_clock_evidence_provenance_unverified` は保存ファイルとローカル時刻の整合だけを示し、**出所・利用許諾・真正性・AIの利益を認証しません**
- `historical_reference_only_provenance_unverified` は過去の参考比較専用。リアルタイム取得に成功した証拠ではありません

Macへの自動起動登録も外部データの契約・取得許可・認証を代行するものではなく、利用者の明示操作が必要です。データ提供元が未接続の現在は実測値0件のままです。
