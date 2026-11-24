# KeibaAI external morning watchdog (optional; not deployed)

目的: GitHub の schedule イベント自体が数時間発生しない場合に備え、別サービスから既存の監視ワークフローの開始を確認する。GitHub のランナー待ち・障害を解消するものではない。

- JST 土・日・月の 07:00–08:45、15分間隔（最大8回/日、週24回）。
- GitHub の同日 main ブランチの監視ワークフローのみ確認。実行中・待機中・成功済み、または既に手動/外部起動済みなら起動しない。ページング不足や HTTP エラー時も起動しない。
- 同時起動競合を完全に排除する分散ロックではない。GitHub 側の既存 production concurrency による単一書き込みを維持する。
- 既存の開催日確認と T-12～T-8 正式取得条件を維持。外部起動は対象 JST 日を指定し、翌日への遅延実行や17:30直前以降の開始を禁止する。通常の手動起動の挙動は変えない。
- 既存 cron、予測モデル、オッズ取得元、購入機能は変更しない。Worker は JRA へのアクセスを行わない。
- Cloudflare 側では公開 HTTP エンドポイントを作らず、ENABLE_DISPATCH=false から開始する。

## 初回導入（所有者の設定が必要）

1. Cloudflare Workers Free アカウントを用意し、無料枠と利用条件を確認する。
2. GitHub の fine-grained PAT を対象リポジトリだけに限定し、Actions read 権限で観測を開始する。実際の起動を承認する場合に限り Actions read/write が必要。期限と最小権限を設定し、トークンを会話・コード・コミットに貼らない。
3. Wrangler を公式の手順で認証し、このフォルダから `npx wrangler deploy`（初期設定は dry run）。Cloudflare の Secret に `GITHUB_TOKEN` を登録する（`npx wrangler secret put GITHUB_TOKEN`、対話入力）。
4. Worker のログで dry_run_would_dispatch / active_or_queued 等を確認する。必要な場合だけ Cloudflare の環境変数 ENABLE_DISPATCH を true に変更する。再デプロイ時の設定は false に戻る可能性があるので確認する。
5. 停止時は ENABLE_DISPATCH=false、Cron Trigger 無効化、必要に応じ PAT 失効。既存 GitHub cron は残る。

## Cloudflare ダッシュボードからの Git 連携（iPhone 対応）

既存 Worker に Git repository を接続する場合は main、Path / Root directory は ops/cloudflare-watchdog、Build command は空欄、Deploy command は npx wrangler deploy、Preview builds は無効にする。Build watch paths の Include paths は ops/cloudflare-watchdog/* のみに限定し、Exclude paths は空欄とする。競馬AIの予測・結果データは頻繁に更新されるため、無関係なコミットによる再ビルドと無料枠消費を防ぐ。

初回ビルドは Git 連携後のコミットで開始する。Deployments の Git 由来の成功記録と Settings の Cron triggers が 0,15,30,45 22-23 * * FRI,SAT,SUN（UTC）であることを確認する。ENABLE_DISPATCH=false の状態を維持し、実行用 GitHub トークンはビルド用の環境変数ではなく Worker の実行時 Secret に登録する。初回の実稼働検証が完了するまで dispatch を有効にしない。

## 合格条件

ローカル: `node --test ops/cloudflare-watchdog/worker.test.mjs`、`python production/test_live_session_slots.py -v`（リポジトリルート）。
実環境: 稼働中/待機中の重複起動がない、非開催日の監視なし、JST 当日/期限チェック、起動時刻の改善、正式予測の完全性と利用枠内での動作。CPU 制限等を含む Cloudflare 実稼働は未確認。特殊な開催曜日は既存の日次 GitHub cron に依存する。

公式資料:
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/workers/configuration/secrets/
- https://docs.github.com/en/rest/actions/workflows
- https://docs.github.com/en/rest/actions/workflow-runs
