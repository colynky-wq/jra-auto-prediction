import Foundation

@main
struct RecordingStatusChecks {
    static func check(_ condition: Bool, _ message: String) {
        if !condition { fatalError(message) }
    }

    static func decode(_ object: [String: Any]) throws -> KeibaAIRecordingStatusData {
        try JSONDecoder().decode(KeibaAIRecordingStatusData.self,
                                 from: JSONSerialization.data(withJSONObject: object))
    }

    static func main() throws {
        let original: [String: Any] = ["generatedAtJST": "2026-10-17 09:00:00", "recordingStartDate": "2026-10-13",
                                       "counts": ["totalRaces": 0, "failed": 0], "races": [Any](), "emptyMessage": "初回待ち"]
        let legacy = try decode(original)
        check(legacy.monitoring == nil && legacy.warnings == nil, "old payload remains readable")
        check(!legacy.isTargetCountUnconfirmed, "old normal empty state")

        var health: [String: Any] = ["raceDate": "2026-10-17", "runID": 123, "status": "startup_failed",
                                     "label": "自動監視の開始に失敗", "message": "開催確認を完了できませんでした。",
                                     "needsAttention": true, "observedAtJST": "2026-10-17 09:00:00",
                                     "detailsURL": "https://github.com/colynky-wq/jra-auto-prediction/actions/runs/123"]
        var modern = original
        modern["monitoring"] = health
        modern["warnings"] = ["監視エラー", "索引に不整合があります。"]
        modern["monitorWarnings"] = ["監視エラー"]
        let failed = try decode(modern)
        check(failed.isTargetCountUnconfirmed, "unknown target count must not appear as confirmed zero")
        check(failed.counts["failed"] == 0 && failed.races.isEmpty, "global failure does not invent failed races")
        check(failed.otherWarnings == ["索引に不整合があります。"], "global warning is not duplicated")
        check(failed.monitoring?.safeDetailsURL != nil, "trusted run link")

        health["detailsURL"] = "https://example.invalid/actions/runs/123"
        modern["monitoring"] = health
        check(try decode(modern).monitoring?.safeDetailsURL == nil, "untrusted link is not exposed")

        modern["races"] = [["raceID": "202605040601", "raceDate": "2026-10-17", "venue": "東京", "raceNumber": 1,
                            "recordingState": "saved", "recordingLabel": "予測保存済み", "resultLabel": "結果待ち",
                            "reason": "保存しました。", "issues": [String](), "needsAttention": false]]
        modern["counts"] = ["totalRaces": 1, "saved": 1, "failed": 0]
        let recorded = try decode(modern)
        check(recorded.races.count == 1 && recorded.races[0].recordingState == "saved", "saved row survives global failure")
        check(!recorded.isTargetCountUnconfirmed, "known real race count stays available")
        modern["races"] = [["raceID": "202605040501", "raceDate": "2026-10-16", "venue": "東京", "raceNumber": 1,
                            "recordingState": "saved", "recordingLabel": "予測保存済み", "resultLabel": "照合済み",
                            "reason": "保存しました。", "issues": [String](), "needsAttention": false]]
        check(try decode(modern).isTargetCountUnconfirmed, "older race rows do not confirm a failed new monitor day")

        var fixtures = original
        fixtures["races"] = [
            ["raceID": "saved", "raceDate": "2026-10-17", "venue": "試験", "raceNumber": 1,
             "recordingState": "saved", "recordingLabel": "予測保存済み", "resultLabel": "結果待ち",
             "reason": "試験", "issues": [String](), "needsAttention": false],
            ["raceID": "held", "raceDate": "2026-10-17", "venue": "試験", "raceNumber": 2,
             "recordingState": "held", "recordingLabel": "履歴不足で保留", "resultLabel": "結果待ち",
             "reason": "試験", "issues": [String](), "needsAttention": false],
            ["raceID": "failed", "raceDate": "2026-10-17", "venue": "試験", "raceNumber": 3,
             "recordingState": "failed", "recordingLabel": "保存失敗", "resultLabel": "未照合",
             "reason": "試験", "issues": [String](), "needsAttention": true],
            ["raceID": "missing", "raceDate": "2026-10-18", "venue": "試験", "raceNumber": 1,
             "recordingState": "missing", "recordingLabel": "記録なし", "resultLabel": "未照合",
             "reason": "試験", "issues": [String](), "needsAttention": true],
            ["raceID": "invalid", "raceDate": "2026-10-18", "venue": "試験", "raceNumber": 2,
             "recordingState": "invalid", "recordingLabel": "記録不整合", "resultLabel": "未照合",
             "reason": "試験", "issues": [String](), "needsAttention": true],
            ["raceID": "waiting", "raceDate": "2026-10-18", "venue": "試験", "raceNumber": 3,
             "recordingState": "waiting", "recordingLabel": "記録待ち", "resultLabel": "結果待ち",
             "reason": "試験", "issues": [String](), "needsAttention": false]
        ]
        fixtures["counts"] = ["totalRaces": 6, "saved": 1, "held": 1, "needsAttention": 3]
        let board = try decode(fixtures)
        check(board.recordingDateOptions == ["すべて", "2026-10-18", "2026-10-17"], "distinct dates newest first")
        check(board.filteredRecordingRows(selectedDate: "すべて", attentionOnly: false).count == 6, "all states stay in the unfiltered list")
        check(board.filteredRecordingRows(selectedDate: "2026-10-17", attentionOnly: false).map(\.raceID) == ["saved", "held", "failed"], "date filter keeps original order and held records")
        check(board.filteredRecordingRows(selectedDate: "すべて", attentionOnly: true).map(\.raceID) == ["failed", "missing", "invalid"], "attention filter uses actual race attention")
        check(board.filteredRecordingRows(selectedDate: "2026-10-17", attentionOnly: true).map(\.raceID) == ["failed"], "date and attention compose")
        check(board.normalizedRecordingDate("2026-10-16") == "すべて", "removed date resets to all")
        check(board.filteredRecordingRows(selectedDate: "2026-10-16", attentionOnly: false).count == 6, "removed date cannot hide new records")
        check(board.normalizedRecordingDate("2026-10-17") == "2026-10-17", "still-available selection survives refresh")
        check(board.counts["totalRaces"] == 6 && board.races.count == 6, "filtering never changes global counts or records")
        check(legacy.recordingDateOptions == ["すべて"] && legacy.normalizedRecordingDate("2026-10-17") == "すべて", "empty response has a valid picker selection")
        check(legacy.filteredRecordingRows(selectedDate: "すべて", attentionOnly: true).isEmpty, "empty filter remains empty")
        print("RECORDING_FILTER_OK: date, attention, combined filters, removed-date recovery, preserved totals")
        print("RECORDING_STATUS_OK: legacy decoding, monitor failure, real counts, warnings, trusted links")
    }
}
