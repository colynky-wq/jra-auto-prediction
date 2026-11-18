> **更新（2026-11-18 13時台 JST）:** ユーザーが追加費用0円を指定したため、この文書のTeam-Nave選定は**撤回・過去の検討記録**となりました。現在有効な判断は `docs/independent-odds-zero-cost-plan.md` と `research/independent_odds_provider_selection.json` を参照してください。Team-Nave / JRA-VANの契約、購入、接続は実施しません。

# KeibaAI 独立オッズ提供元の選定（2026-11-18）

## 結論

**Mac単独運用の技術上の第一候補を Team-Nave「KeiBa-ODDS-API」に決定**する。ソフト・仕様の適合確認と、個人契約・利用許可の確定は別段階。

- ステータス：`vendor_selected_pending_contract`（**契約・稼働は未完了**）
- 対象：中央競馬の単勝 `TAN` とワイド `WIDE`
- 対応：Windows/Macintosh/Linux。HTTPS の公開仕様によりPOSTで問い合わせ
- 公式指定の問い合わせ先：`https://api.gamble-os.net/systems/get-odds`
- 認証：`tncid`（会員ID）・`tncpw`（パスワード）。APIヘッダーやURL/ログに値を保存しない
- パラメータ：`gkindcd=100`、`opdt=YYYYMMDD`、`rcs`（競馬場2桁）、`rno`（レース2桁）、`kind=TAN/WIDE`、`opt=ALL`
- 個人利用料金：GAMBLE-OS 14,300円（税込）/月（2026-11-18調査時点に公開された料金）。利用料金には複数APIが含まれ、KeibaAIの実験用途だけに価値があるか費用対効果を確認
- 利用制限：1ライセンスで同時問い合わせ1件・1端末、毎日6:00〜8:00メンテナンス。Macだけから順序立てて問い合わせ、GitHub Actionsはアクセス元として使わない
- 提供元が明示する注意事項：取得漏れ、更新遅延、IPAT・JRA側仕様変更。**リアルタイム精度を保証しない**
- 用途条件：IPAT購入者向け、個人非営利契約。独立記録をAI解析・ローカル保存・研究に利用できるかは、契約条件と事業者回答で確認が必要

## なぜ「最新のみ」ではなく「履歴すべて」か

公開例では `results.tan` に複数のJSON文字列が入る構造で、各オッズに `rlsdttm=YYYYMMDDHHMM` が付く。`opt=ALL` を使い、観測時刻より未来でない最も新しい**時刻付き**の単勝・ワイドの記録を別々に特定する。

最新値のみを使うと `rlsdttm=LAST-ODDS` になり得る（公式の既知の挙動）。**実際の更新時刻として扱ってはいけない**。発売終了後の最新値は最終オッズとなるため、研究対象時点を過ぎた値を使うのも禁止。

元のモデル記録にある `startTime` や `capturedAtJST` と照合し、過去の朝オッズや発走後のオッズをT-10記録として採用しない。

**注意：** サイト掲載の単勝サンプル `TAN.json` と公式の問合せ仕様は確認できた。ワイドの公開サンプルリンク `WIDE.json` は外部参照で取得できず、**ワイドの返却配列・キー名・欠損処理は契約前に実レスポンスか提供元の仕様書で検証すること**。推測のパーサーで本番接続しない。

## 他の候補を主役にしない理由

| 候補 | コスト・制約 | 評価 |
|---|---|---|
| Team-Nave KeiBa-ODDS-API | 14,300円/月、Mac可、単勝・ワイド・履歴 | **Mac単独運用の第一候補** |
| JRA-VAN Data Lab. | 2,090円/月、JV-LinkはWindows専用 | Windows環境を常時用意するなら**低コスト代替**。Macに直接つなぐ公開HTTP APIではない |
| JRA公式Web | 閲覧可 | 自動収集・継続的保存・研究用再利用の許諾が未確認。候補から除外せず、無条件に導入もしない |
| 既存のnetkeiba | 既存の取得は可能 | 現行の時刻差の検証対象そのものなので**独立ソースではない** |

## 契約・実稼働前に確認すること（全て必須）

1. Macで自作PythonスクリプトからAPIへPOSTし、単勝・ワイド履歴を取得・時刻付きでローカル保存・解析できるか
2. 個人契約での個人的なAI研究・アプリ内表示・機械学習検証・ローカル保存の許諾範囲（再配布や一般公開はしない）
3. `WIDE` のJSON項目名、配列の形、時刻 `rlsdttm`、馬番ペアの表現
4. APIの更新周期・発走直前の遅延・トラフィック制限（同時1件以外の間隔制限）・取得エラー時の再試行条件
5. 利用者がIPAT加入者であることが必須条件か、またGAMBLE-OS契約1つでAPI利用可か
6. 期限、キャンセル・自動継続課金、試用やサンプルの可否

## 実装・検証ゲート

- PR #115の研究用オッズ受入器をそのまま利用。ベンダー応答→`licensed_odds_drop/YYYYMMDD/RACEID.json` に変換するアダプターは**出所許可とWIDE構造確認が済んだ後**に有効化する
- `TAN` と `WIDE` を同じレース・同じ時刻帯で収集し、リリース時刻が申告値として残っていることを条件にする
- `TAN` と `WIDE` は別々に品質ゲートを通し、片方だけ新鮮でも両券種の期待値を検証済みとしない
- レースID変換は `rid[4:6]` の競馬場コードと末尾2桁レース番号を用い、保存済みの venue/raceNumber と突き合わせる
- Macに存在しない認証情報やライセンスを仮定せず、**契約・支払い・アカウント作成・ネットワーク呼出しを実行しない**
- 実測取得までは `independent_source_evidence_pending`、正式な自動購入・モデル差替えは停止継続

## 公式参照

- サービス／料金：https://www.team-nave.com/system/jp/products/kboddsapi/
- API仕様・POST・パラメータ：https://www.team-nave.com/system/jp/products/kboddsapi/help_function.html
- 利用制限：https://www.team-nave.com/system/jp/products/kboddsapi/help_limit.html
- 履歴取得可能範囲とLAST-ODDS：https://www.team-nave.com/system/jp/products/kboddsapi/help_attention.html
- 利用手順とサンプル：https://www.team-nave.com/system/jp/products/kboddsapi/help_setup.html
- JRA-VAN価格：https://jra-van.jp/dlb/
- JV-LinkのWindows仕様：https://developer.jra-van.jp/t/topic/614
