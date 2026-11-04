import Foundation

@main
struct SimulatorLearningChecks {
    static func main() throws {
        let decoder = JSONDecoder()
        let root = URL(fileURLWithPath: CommandLine.arguments[1])
        let learning = try decoder.decode(SimulatorLearningReport.self, from: Data(contentsOf: root.appendingPathComponent("RaceSimulatorLearning.json")))
        let recording = try decoder.decode(SimulatorRecordingReport.self, from: Data(contentsOf: root.appendingPathComponent("RaceSimulatorRecordingStatus.json")))
        precondition(learning.settledProspectiveRaces >= 0)
        precondition(recording.counts.values.reduce(0, +) == recording.races.count)
        precondition(Set(recording.races.map(\.raceID)).count == recording.races.count)
        if learning.settledProspectiveRaces == 0 {
            precondition(learning.activeModelID == "race-simulator-v02-baseline")
            precondition(recording.liveVerification.status == "awaiting_live_race")
        }
        let missing = #"{"key":"surface:dirt","label":"ダート","races":0,"days":0,"status":"insufficient_data"}"#
        let condition = try decoder.decode(SimulatorConditionComparison.self, from: Data(missing.utf8))
        precondition(condition.relativeImprovement == nil)
        precondition(condition.stateLabel == "データ不足・判断保留")
        let rollback = try decoder.decode(SimulatorLearningDecision.self, from: Data(#"{"status":"rollback"}"#.utf8))
        precondition(rollback.label.contains("旧設定に復帰"))
        print("SIMULATOR_LEARNING_CONTRACT_OK: actual backend JSON, missing metrics and rollback labels")
    }
}
