KeibaAI 馬プロフィールの世代照合（2026-10-13）

この版は、馬名と生年が一致する馬だけを表示します。同名候補の最終出走日順で選ぶ動作は廃止しました。
予測・大穴比較・脚質・参考隊列の各入口に同じ照合処理を適用します。
識別情報が不足する場合、同じレース日・ID・馬番・馬名の保存済み識別情報を使います。
対応する記録がなければ不足を説明し、別の世代を推測して表示しません。
過去の予測値を再計算する変更は含みません。

推奨手順
1. ZIPを別フォルダへ展開し、KeibaAI.xcodeprojを開きます。
2. 必要に応じて、自分の開発用Teamと実行先を選びます。
3. ⌘Bでビルド後、アプリで馬名を開き、生年を確認します。
4. 保存済み予算、大穴比較、研究記録状況一覧を確認します。

既存プロジェクトへの手動反映
・ContentView.swiftとKeibaAI_NewDesignView.swiftを同梱版に置換します。
・KeibaAIModels.swift、KeibaAIHorseIdentity.swift、KeibaAIHorseProfiles.swiftを同じアプリのターゲットに追加します。
・HorseとRaceの定義はKeibaAIModels.swiftへ移動しています。同じ定義を二重に残さないでください。
・KeibaAIApp.swift、既存のAssets、RaceData.jsonは元の同梱データを維持しています。
・自分のTeam、Bundle ID、既存の署名設定は必要に応じて維持してください。

GitHubのios/KeibaAIに同じアプリのソースを保存しています。
自動検証はXcode 27による署名なしのiOSシミュレータ向けビルドと、実際の照合・取得・キャッシュ処理のSwift試験です。
実機のタップ操作や署名を伴うインストールは、自動ビルドとは別に確認が必要です。
