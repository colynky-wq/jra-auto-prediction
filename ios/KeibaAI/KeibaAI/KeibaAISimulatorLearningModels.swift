import Foundation

struct SimulatorLiveVerification: Decodable {
    let status: String
    let matchedRaces: Int
    let message: String
}

struct SimulatorRecordingReport: Decodable {
    let generatedAt: String
    let targetRaceDate: String
    let expectedScheduleAvailable: Bool
    let counts: [String: Int]
    let races: [SimulatorRecordingRow]
    let issues: [String]
    let liveVerification: SimulatorLiveVerification
    let history: [SimulatorRecordingRow]?
    let availableDates: [String]?
    let scheduleDates: [String]?

    func rows(for day: String) -> [SimulatorRecordingRow] {
        day == targetRaceDate ? races : (history ?? []).filter { $0.raceDate == day }
    }
}

struct SimulatorRecordingRow: Decodable, Identifiable {
    var id: String { raceID }
    let raceID: String
    let raceDate: String
    let venue: String?
    let raceNumber: Int?
    let raceName: String?
    let startTime: String?
    let state: String
    let label: String
    let reason: String
    let durableConfirmed: Bool
}

struct SimulatorLearningReport: Decodable {
    let generatedAt: String
    let status: String
    let activeModelID: String
    let candidateModelID: String?
    let settledProspectiveRaces: Int
    let policy: SimulatorLearningPolicy
    let conditionComparisons: [SimulatorConditionComparison]?
    let monitoring: SimulatorMonitoring?
    let latestMonitorDecision: SimulatorLearningDecision?
    let latestDecision: SimulatorLearningDecision?
    let decisionHistory: [SimulatorLearningDecision]?
    let liveVerification: SimulatorLiveVerification?

    var conditionRows: [SimulatorConditionComparison] {
        if let rows = conditionComparisons, !rows.isEmpty { return rows }
        return latestDecision?.conditionCoverage ?? []
    }

    var stateLabel: String {
        switch status {
        case "candidate_in_forward_validation": return "別レースで候補を検証中"
        case "validated_model_active": return "検証した設定を採用中"
        case "awaiting_forward_training_data": return "発走前記録の蓄積待ち"
        default: return "状態確認中"
        }
    }
}

struct SimulatorLearningPolicy: Decodable {
    let min_training_races: Int
    let min_validation_races: Int
    let min_validation_days: Int
    let min_condition_races: Int?
    let min_condition_days: Int?
    let min_monitor_races: Int?
    let min_monitor_days: Int?
    let rollback_relative_degradation: Double?
}

struct SimulatorMonitoring: Decodable {
    let activeModelID: String
    let referenceModelID: String
    let checkedCohorts: Int
}

struct SimulatorLearningDecision: Decodable {
    let status: String
    let validationRaces: Int?
    let races: Int?
    let conditionCoverage: [SimulatorConditionComparison]?
    let relativeImprovement: Double?
    let relativeDegradation: Double?

    var label: String {
        switch status {
        case "promote": return "別レースの検証を通過して採用"
        case "reject": return "採用条件を満たさず見送り"
        case "rollback": return "継続した悪化を確認し、旧設定に復帰"
        case "monitor_pass": return "採用後の監視を継続"
        case "awaiting_monitor_results": return "採用後の比較結果を蓄積中"
        case "awaiting_validation": return "検証件数・条件別データを蓄積中"
        default: return "状態確認中"
        }
    }
}

struct SimulatorConditionComparison: Decodable, Identifiable {
    var id: String { key }
    let key: String
    let label: String
    let races: Int
    let days: Int
    let status: String
    let relativeImprovement: Double?

    var stateLabel: String {
        switch status {
        case "pass": return "悪化防止条件を通過"
        case "regressed": return "悪化あり・採用見送り対象"
        case "sufficient": return "件数充足・判定待ち"
        default: return "データ不足・判断保留"
        }
    }
}
