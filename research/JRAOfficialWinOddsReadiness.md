# JRA公式の単勝オッズ取得：切替条件（2026-11-24）

## 作業状況
- `production/jra_win_odds_source.py`: 公開出馬表の画面遷移から観測したCNAMEのみを使い、`JRADB/accessO.html` の発売中「単勝・複勝オッズ（馬番順）」を読み取る試験実装。
- 馬番・レースID・日付を照合し、時刻はJRA画面の「○時○分現在オッズ」から生成する。HTTP受信時刻を元データ更新時刻として代用しない。
- 出走馬全頭照合は従来の `live_monitor_topk.py` で維持。欠損、取消状態不明、最終オッズ、30分超過、未来時刻、日時の矛盾を拒否する。
- `KEIBAAI_ODDS_SOURCE=jra_official_web` と `KEIBAAI_ALLOW_EXPERIMENTAL_JRA_ODDS=1` の**両方がセットされた場合のみ**JRA試験取得を選択。標準の本番ジョブは従来のnetkeiba APIのまま。JRA試験取得時は再試行間隔を60秒とする。
- 研究比較 `LongshotTop3V2ForwardReview` の鮮度検証ではJRA由来の時刻ラベルと証拠を認識する。過去の凍結記録は更新しない。

## 利用条件について
JRA公式には無料の閲覧画面と更新時刻が存在する。ただし、JRAの一般利用案内でオッズ情報の**自動反復取得を明示的に許可する記述は確認できていない**。`robots.txt` の禁止指定がないことや第三者の実装例は、積極的な許諾の根拠ではない。JRA-VAN Data Lab.の有料情報配信は利用しない。ログイン・有料契約・CAPTCHA回避・専用APIの非公開エンドポイントは対象外。

## 本番切替のためのゲート
1. JRAの公開利用条件と負荷に配慮した自動取得の取り扱いを確認すること（特別な個別許可の取得はしない）。
2. 次回開催日に、発走12～8分前のJRA公開ページで当該レースの単勝全頭、馬番、画面に表示される更新時刻を確認すること。
3. JRA時刻の意味・表示精度・販売中状態を検証し、時刻が受信時点および正式予測保存時点の両方で0～1800秒以内であること。
4. 公式出馬表とオッズの馬番・取消状態が一致すること。差があれば正式予測停止。
5. 上記条件が満たされてから本番の `KEIBAAI_ODDS_SOURCE` をJRAへ切替。未達なら従来のオッズ鮮度ゲートを緩めない。

## 参照
- https://www.jra.go.jp/faq/pop02/1_1.html
- https://www.jra.go.jp/use/
- https://github.com/Npieju/nr-odds-view （外部の検証例。利用許諾の証明ではない）
- https://github.com/colynky-wq/jra-auto-prediction/issues/165
