import Foundation

struct KeibaAIRecordingStatusData: Decodable {
    let generatedAtJST: String
    let recordingStartDate: String
    let counts: [String: Int]
    let races: [KeibaAIRecordingStatusRow]
    let emptyMessage: String
    let warnings: [String]?
    let monitorWarnings: [String]?
    let monitoring: KeibaAIMonitoringHealth?

    var isTargetCountUnconfirmed: Bool {
        if let health = monitoring, health.needsAttention,
           !races.contains(where: { $0.raceDate == health.raceDate }) { return true }
        return races.isEmpty && !(warnings ?? []).isEmpty
    }

    var otherWarnings: [String] {
        (warnings ?? []).filter { !(monitorWarnings ?? []).contains($0) }
    }
}

struct KeibaAIMonitoringHealth: Decodable {
    let raceDate: String
    let runID: Int
    let status: String
    let label: String
    let message: String
    let needsAttention: Bool
    let observedAtJST: String
    let detailsURL: String

    var safeDetailsURL: URL? {
        guard runID > 0,
              detailsURL == "https://github.com/colynky-wq/jra-auto-prediction/actions/runs/\(runID)" else { return nil }
        return URL(string: detailsURL)
    }
}

struct KeibaAIRecordingStatusRow: Decodable, Identifiable {
    var id: String { raceID }
    let raceID: String
    let raceDate: String
    let venue: String
    let raceNumber: Int
    let startTime: String?
    let recordingState: String
    let recordingLabel: String
    let resultLabel: String
    let reason: String
    let issues: [String]
    let lastAttemptAtJST: String?
    let needsAttention: Bool
}
