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
        print("RECORDING_STATUS_OK: legacy decoding, monitor failure, real counts, warnings, trusted links")
    }
}
