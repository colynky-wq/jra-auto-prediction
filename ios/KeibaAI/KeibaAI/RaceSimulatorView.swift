import SwiftUI
import Foundation
import Combine

private struct SimBatch: Decodable {
    let schemaVersion: String
    let generatedAt: String
    let status: String
    let settings: SimBatchSettings
    let summary: SimBatchSummary
    let races: [SimRace]
}

private struct SimBatchSettings: Decodable {
    let runsPerRace: Int
    let baseSeed: Int
    let dtSeconds: Double
}

private struct SimBatchSummary: Decodable {
    let inputRaces: Int
    let simulatedRaces: Int
    let skippedRaces: Int
    let errorRaces: Int
}

private struct SimRace: Decodable, Identifiable {
    var id: String { raceID }

    let raceID: String
    let raceName: String?
    let racecourse: String?
    let track: String?
    let distanceMeters: Double
    let pace: String
    let paceSource: String
    let raceDate: String?
    let raceNumber: Int?
    let startTime: String?
    let venue: String?
    let inputQuality: SimInputQuality
    let independence: SimIndependence
    let settings: SimEngineSettings
    let horses: [SimHorse]
    let comparisonWithPredictionAI: SimComparison
    let replay: SimReplay
}

private struct SimInputQuality: Decodable {
    let formationStatus: String?
    let formationCoveragePct: Double?
    let paceForecastStatus: String?
    let styleKnownHorses: Int
    let styleTotalHorses: Int
    let styleCoveragePct: Double
}

private struct SimIndependence: Decodable {
    let usesPredictionAIForMotion: Bool
    let usesMarketProbabilityForMotion: Bool
    let usesOddsForMotion: Bool
    let strengthSource: String
}

private struct SimEngineSettings: Decodable {
    let runs: Int
    let seed: Int
    let dtSeconds: Double
}

private struct SimHorse: Decodable, Identifiable {
    var id: Int { horseNumber }

    let horseNumber: Int
    let horseName: String
    let style: String
    let independentStrength: Double
    let simulationWinProbability: Double
    let simulationTop2Probability: Double
    let simulationTop3Probability: Double
    let expectedFinish: Double
    let earlyLeadProbability: Double
    let top3At75Probability: Double
    let averageFinishTimeSeconds: Double
    let averageBlockedSeconds: Double
    let averageLaneChanges: Double
    let rankBands: [SimRankBand]
}

private struct SimRankBand: Decodable, Identifiable {
    var id: Double { progress }
    let progress: Double
    let p20Rank: Double
    let medianRank: Double
    let p80Rank: Double
}

private struct SimComparison: Decodable {
    let available: Bool
    let note: String?
    let reason: String?
    let horses: [SimComparisonHorse]?
}

private struct SimComparisonHorse: Decodable, Identifiable {
    var id: Int { horseNumber }

    let horseNumber: Int
    let horseName: String
    let predictionAIWinProbabilityNormalized: Double
    let simulationWinProbability: Double
    let delta: Double
    let label: String
}

private struct SimReplay: Decodable {
    let kind: String
    let seed: Int
    let dtSeconds: Double
    let durationSeconds: Double
    let frames: [SimFrame]
    let note: String?
}

private struct SimFrame: Decodable, Identifiable {
    var id: Double { t }
    let t: Double
    let horses: [SimFrameHorse]
}

private struct SimFrameHorse: Decodable, Identifiable {
    var id: Int { horseNumber }

    let horseNumber: Int
    let progress: Double
    let positionMeters: Double
    let lane: Double
    let speedMps: Double
    let rank: Int
    let gapMeters: Double
    let blocked: Bool
}

@MainActor
private final class KeibaAIRaceSimulatorStore: ObservableObject {
    @Published private(set) var races: [Race] = []
    @Published private(set) var simulations: [String: SimRace] = [:]
    @Published private(set) var batch: SimBatch?
    @Published private(set) var sourceMessage = "未読み込み"
    @Published private(set) var isLoading = false
    @Published private(set) var recordingReport: SimulatorRecordingReport?
    @Published private(set) var learningReport: SimulatorLearningReport?
    @Published private(set) var recordingError: String?
    @Published private(set) var learningError: String?

    private let rawMain = "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/"

    func simulation(for raceID: String) -> SimRace? {
        simulations[raceID]
    }

    func load(force: Bool = false) async {
        if isLoading || (!force && !races.isEmpty && batch != nil) { return }
        isLoading = true
        defer { isLoading = false }

        do {
            let raceData: [Race] = try await fetch(rawMain + "RaceData.json")
            races = raceData.sorted {
                if $0.raceDate != $1.raceDate {
                    return ($0.raceDate ?? "") > ($1.raceDate ?? "")
                }
                if $0.venue != $1.venue {
                    return venueOrder($0.venue) < venueOrder($1.venue)
                }
                return $0.raceNumber < $1.raceNumber
            }

            let simulationData: SimBatch = try await fetch(rawMain + "RaceSimulationData.json")
            guard simulationData.schemaVersion == "race-simulator-v0.2-batch" else {
                throw URLError(.cannotParseResponse)
            }

            batch = simulationData
            simulations = Dictionary(uniqueKeysWithValues: simulationData.races.map { ($0.raceID, $0) })
            sourceMessage = "RaceSimulator v0.2・フレーム取得済"
        } catch {
            simulations = [:]
            batch = nil
            sourceMessage = races.isEmpty ? "RaceData取得失敗" : "RaceSimulator v0.2取得待ち"
        }
        async let recording: Void = loadRecording()
        async let learning: Void = loadLearning()
        _ = await (recording, learning)
    }

    private func loadRecording() async {
        do {
            recordingReport = try await fetch(rawMain + "RaceSimulatorRecordingStatus.json")
            recordingError = nil
        } catch {
            recordingReport = nil
            recordingError = "記録状況を取得できませんでした。更新して再確認してください。"
        }
    }

    private func loadLearning() async {
        do {
            learningReport = try await fetch(rawMain + "RaceSimulatorLearning.json")
            learningError = nil
        } catch {
            learningReport = nil
            learningError = "改善状況を取得できませんでした。更新して再確認してください。"
        }
    }

    private func fetch<T: Decodable>(_ raw: String) async throws -> T {
        guard var components = URLComponents(string: raw) else {
            throw URLError(.badURL)
        }
        components.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
        guard let url = components.url else {
            throw URLError(.badURL)
        }

        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 20
        request.setValue("KeibaAI-RaceSimulator/0.2", forHTTPHeaderField: "User-Agent")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func venueOrder(_ venue: String) -> Int {
        ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"].firstIndex(of: venue) ?? 99
    }
}

struct KeibaAIRaceSimulatorScreen: View {
    @StateObject private var store = KeibaAIRaceSimulatorStore()

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Label("RaceSimulator v0.2", systemImage: "waveform.path.ecg")
                        .font(.headline)
                    Text("予測AI・オッズ・人気を走行計算に入れず、独立能力・脚質・位置取りから展開を計算します。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("馬券判断は予測AIを主軸にし、展開シミュは追い風・向かい風の確認に使います。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(store.sourceMessage)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                Section {
                    NavigationLink {
                        SimulatorOperationsScreen(store: store)
                    } label: {
                        Label("記録状況・継続改善", systemImage: "checklist")
                    }
                    .accessibilityIdentifier("sim-learning-link")
                    Text(store.learningReport?.stateLabel ?? (store.learningError ?? "改善状況を取得中…"))
                        .font(.caption).foregroundStyle(.secondary)
                }

                Section("レース") {
                    ForEach(store.races) { race in
                        NavigationLink {
                            KeibaAIRaceSimulatorDestination(race: race, store: store)
                        } label: {
                            RaceSimRow(
                                race: race,
                                ready: store.simulation(for: race.raceID) != nil,
                                recordingLabel: store.recordingReport?.races.first(where: { $0.raceID == race.raceID })?.label
                            )
                        }
                        .accessibilityIdentifier("sim-race-\(race.raceID)")
                    }
                }
            }
            .navigationTitle("展開シミュ")
            .refreshable { await store.load(force: true) }
        }
        .task { await store.load() }
    }
}

private struct SimulatorOperationsScreen: View {
    @ObservedObject var store: KeibaAIRaceSimulatorStore
    @State private var selectedDay = ""

    var body: some View {
        List {
            Section("実開催の確認") {
                if let verification = store.recordingReport?.liveVerification ?? store.learningReport?.liveVerification {
                    Label(verification.message, systemImage: verification.status == "verified" ? "checkmark.circle" : "clock")
                        .accessibilityIdentifier("sim-live-verification")
                } else {
                    Text(store.recordingError ?? "実開催の確認状況を取得中…")
                }
            }
            Section("学習・採用") {
                if let report = store.learningReport {
                    Text(report.stateLabel).font(.headline)
                    LabeledContent("結果照合済み", value: "\(report.settledProspectiveRaces)レース")
                    Text("学習は\(report.policy.min_training_races)レース以上。その後の別の\(report.policy.min_validation_races)レース・\(report.policy.min_validation_days)開催日以上で検証します。")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("芝・ダート・距離別の件数が足りない場合は検証を続け、悪化防止条件を満たした設定だけを採用します。")
                        .font(.caption).foregroundStyle(.secondary)
                    if let decision = report.latestDecision { Text(decision.label).font(.subheadline) }
                    if let recent = report.decisionHistory?.last { Text("直近の判定：\(recent.label)").font(.caption) }
                    DisclosureGroup("設定の識別情報") {
                        Text("採用中：\(report.activeModelID)").font(.caption).textSelection(.enabled)
                        Text("検証中：\(report.candidateModelID ?? "なし")").font(.caption).textSelection(.enabled)
                    }
                    Text("最終更新：\(report.generatedAt)").font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text(store.learningError ?? "改善状況を取得中…")
                }
            }
            Section("採用後の監視") {
                if let report = store.learningReport {
                    if let monitoring = report.monitoring {
                        Text("旧設定も発走前に並行保存し、同じレースで比較しています。")
                        LabeledContent("確認した期間", value: "\(monitoring.checkedCohorts)区間")
                        if let decision = report.latestMonitorDecision { Text(decision.label).font(.caption) }
                    } else {
                        Text("新設定の採用後に、旧設定との継続比較を開始します。")
                    }
                    Text("\(report.policy.min_monitor_races ?? 30)レース・\(report.policy.min_monitor_days ?? 2)開催日以上の区間ごとに判定します。誤差が\(Int((report.policy.rollback_relative_degradation ?? 0.05) * 100))%以上増え、統計条件と期間後半の悪化も確認した場合は旧設定に戻します。")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("監視状況は改善データの取得後に表示します。")
                }
            }
            Section("条件別の検証") {
                if let report = store.learningReport, !report.conditionRows.isEmpty {
                    ForEach(report.conditionRows) { row in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(row.label).font(.subheadline.bold())
                            Text("\(row.races)レース・\(row.days)開催日 ／ \(row.stateLabel)")
                                .font(.caption).foregroundStyle(row.status == "regressed" ? Color.red : Color.secondary)
                            if let improvement = row.relativeImprovement {
                                Text(String(format: "総合誤差の改善率 %.1f%%", improvement * 100)).font(.caption)
                            }
                        }.accessibilityIdentifier("sim-condition-\(row.key)")
                    }
                } else {
                    Text("候補と現行設定の比較データは蓄積待ちです。")
                }
            }
            Section("各レースの記録") {
                if let report = store.recordingReport {
                    let day = selectedDay.isEmpty ? report.targetRaceDate : selectedDay
                    Picker("対象日", selection: Binding(get: { day }, set: { selectedDay = $0 })) {
                        ForEach(report.availableDates ?? [report.targetRaceDate], id: \.self) { date in
                            Text(date).tag(date)
                        }
                    }
                    if !(report.scheduleDates?.contains(day) ?? report.expectedScheduleAvailable) {
                        Text("予定一覧を取得できていないため、取得済みレースの一覧です。開催全体の保存率は未確定です。")
                            .font(.caption).foregroundStyle(.orange)
                    }
                    ForEach(report.issues, id: \.self) { Text($0).font(.caption).foregroundStyle(.orange) }
                    ForEach(report.rows(for: day)) { row in
                        VStack(alignment: .leading, spacing: 4) {
                            Text("\(row.venue ?? "会場未確認") \(row.raceNumber.map { "\($0)R" } ?? "") \(row.startTime ?? "")")
                                .font(.subheadline.bold())
                            if let name = row.raceName { Text(name).font(.caption) }
                            Text(row.label).font(.subheadline)
                                .foregroundStyle(["failed", "missed", "held"].contains(row.state) ? Color.orange : Color.primary)
                            Text(row.reason).font(.caption).foregroundStyle(.secondary)
                        }.accessibilityIdentifier("sim-recording-\(row.raceID)")
                    }
                    Text("最終更新：\(report.generatedAt)").font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text(store.recordingError ?? "記録状況を取得中…")
                }
            }
        }
        .navigationTitle("記録状況・継続改善")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await store.load(force: true) }
        .toolbar {
            Button("更新") { Task { await store.load(force: true) } }
                .disabled(store.isLoading).accessibilityIdentifier("sim-learning-refresh")
        }
    }
}

private struct RaceSimRow: View {
    let race: Race
    let ready: Bool
    let recordingLabel: String?

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(race.venue) \(race.raceNumber)R")
                    .font(.subheadline.bold())
                Text(race.raceName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                if let recordingLabel {
                    Text(recordingLabel).font(.caption2).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Image(systemName: ready ? "checkmark.circle.fill" : "clock")
                .foregroundStyle(ready ? Color.green : Color.secondary)
        }
    }
}

private struct KeibaAIRaceSimulatorDestination: View {
    let race: Race
    @ObservedObject var store: KeibaAIRaceSimulatorStore

    var body: some View {
        Group {
            if let sim = store.simulation(for: race.raceID) {
                RaceSimDetail(race: race, sim: sim, batch: store.batch)
            } else if store.isLoading {
                ProgressView("v0.2を取得中…")
            } else {
                ContentUnavailableView(
                    "v0.2生成待ち",
                    systemImage: "clock",
                    description: Text("\(race.venue) \(race.raceNumber)R のフレームデータはまだありません。")
                )
                .accessibilityIdentifier("sim-pending")
            }
        }
        .navigationTitle("\(race.venue) \(race.raceNumber)R")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct RaceSimDetail: View {
    let race: Race
    let sim: SimRace
    let batch: SimBatch?

    @State private var elapsed = 0.0
    @State private var playing = false
    @State private var speed = 1.0
    @State private var selectedHorse: Int?
    @State private var overlappingHorses: [Int] = []
    @State private var showHorseChoice = false

    private let timer = Timer.publish(every: 1.0 / 30.0, on: .main, in: .common).autoconnect()

    private var duration: Double {
        max(sim.replay.durationSeconds, sim.replay.frames.last?.t ?? 1.0, 1.0)
    }

    private var progress: Double {
        min(1.0, max(0.0, elapsed / duration))
    }

    private var framePair: (SimFrame, SimFrame, Double)? {
        let frames = sim.replay.frames
        guard let first = frames.first else { return nil }
        if elapsed <= first.t { return (first, first, 0.0) }
        guard let last = frames.last else { return nil }
        if elapsed >= last.t { return (last, last, 0.0) }

        var low = 0
        var high = frames.count - 1
        while high - low > 1 {
            let middle = (low + high) / 2
            if frames[middle].t <= elapsed {
                low = middle
            } else {
                high = middle
            }
        }

        let width = max(frames[high].t - frames[low].t, 0.0001)
        let fraction = (elapsed - frames[low].t) / width
        return (frames[low], frames[high], fraction)
    }

    private var currentStates: [SimFrameHorse] {
        guard let pair = framePair else { return [] }
        let nextByNumber = Dictionary(uniqueKeysWithValues: pair.1.horses.map { ($0.horseNumber, $0) })

        return pair.0.horses.map { current in
            guard let next = nextByNumber[current.horseNumber] else { return current }
            let fraction = pair.2
            func lerp(_ a: Double, _ b: Double) -> Double {
                a + (b - a) * fraction
            }
            return SimFrameHorse(
                horseNumber: current.horseNumber,
                progress: lerp(current.progress, next.progress),
                positionMeters: lerp(current.positionMeters, next.positionMeters),
                lane: lerp(current.lane, next.lane),
                speedMps: lerp(current.speedMps, next.speedMps),
                rank: fraction < 0.5 ? current.rank : next.rank,
                gapMeters: lerp(current.gapMeters, next.gapMeters),
                blocked: fraction < 0.5 ? current.blocked : next.blocked
            )
        }
        .sorted { $0.rank < $1.rank }
    }

    private var aiRanked: [Horse] {
        race.horses.sorted {
            if $0.aiProbability != $1.aiProbability {
                return $0.aiProbability > $1.aiProbability
            }
            return $0.number < $1.number
        }
    }

    private var aiTop3: [Horse] {
        Array(aiRanked.prefix(3))
    }

    private var simRanked: [SimHorse] {
        sim.horses.sorted {
            if $0.simulationWinProbability != $1.simulationWinProbability {
                return $0.simulationWinProbability > $1.simulationWinProbability
            }
            return $0.expectedFinish < $1.expectedFinish
        }
    }

    private var holeCandidate: SimHorse? {
        let aiTopNumbers = Set(aiTop3.map(\.number))
        return simRanked.prefix(3).first { !aiTopNumbers.contains($0.horseNumber) }
    }

    private var phase: String {
        if progress < 0.05 { return "スタート" }
        if progress < 0.25 { return "序盤" }
        if progress < 0.60 { return "中盤" }
        if progress < 0.82 { return "3〜4角" }
        if progress < 0.995 { return "直線" }
        return "ゴール"
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                headerCard
                overallJudgmentCard
                replayCard
                liveOrderCard
                resultCard
                qualityCard
            }
            .padding(16)
        }
        .accessibilityIdentifier("sim-detail")
        .background(Color(uiColor: .systemGroupedBackground))
        .onDisappear { playing = false }
        .confirmationDialog("重なっている馬を選択", isPresented: $showHorseChoice, titleVisibility: .visible) {
            ForEach(overlappingHorses, id: \.self) { number in
                Button("#\(number) \(horseName(number))") {
                    selectedHorse = number
                }
                .accessibilityIdentifier("sim-choose-horse-\(number)")
            }
            Button("キャンセル", role: .cancel) { }
        } message: {
            Text("馬が重なっています。確認したい馬番を選んでください。")
        }
        .onReceive(timer) { _ in
            guard playing else { return }
            elapsed = min(duration, elapsed + (1.0 / 30.0) * speed)
            if elapsed >= duration {
                playing = false
            }
        }
    }

    private var headerCard: some View {
        card {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(race.venue) \(race.raceNumber)R ・ \(race.startTime)")
                        .font(.caption.bold())
                        .foregroundStyle(.blue)
                    Text(race.raceName)
                        .font(.title2.bold())
                    Text(sim.track ?? race.course)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text("RaceSimulator v0.2")
                    .font(.caption.bold())
                    .padding(7)
                    .background(.blue.opacity(0.10), in: Capsule())
            }

            Text("フレーム \(sim.replay.frames.count)枚 / \(String(format: "%.1f", sim.replay.dtSeconds))秒刻み / \(sim.replay.kind)")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var overallJudgmentCard: some View {
        card {
            HStack {
                Label("総合判断", systemImage: "checkmark.circle.fill")
                    .font(.title3.bold())
                Spacer()
                Text("予測AI主軸")
                    .font(.caption.bold())
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(.blue.opacity(0.10), in: Capsule())
            }

            Text("迷ったときはここを優先します。予測AIの順位を主軸にし、v0.2展開シミュは追い風・向かい風として加味します。")
                .font(.caption)
                .foregroundStyle(.secondary)

            ForEach(aiTop3) { horse in
                let aiRank = aiRank(for: horse.number) ?? 0
                let simRank = simRank(for: horse.number)
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("#\(horse.number) \(horse.name)")
                            .font(.headline)
                            .lineLimit(1)
                        Spacer()
                        Text(judgmentTitle(aiRank: aiRank, simRank: simRank))
                            .font(.caption.bold())
                            .foregroundStyle(judgmentColor(aiRank: aiRank, simRank: simRank))
                    }
                    HStack(spacing: 8) {
                        Text("AI \(aiRank)位")
                            .font(.caption.bold())
                        Text(String(format: "AI勝率 %.1f%%", horse.aiProbability))
                            .font(.caption.monospacedDigit())
                        Text(simRank.map { "展開 \($0)位" } ?? "展開 対象外")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(10)
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("sim-judgment-\(horse.number)")
                .background(
                    judgmentColor(aiRank: aiRank, simRank: simRank).opacity(0.07),
                    in: RoundedRectangle(cornerRadius: 12)
                )
            }

            if let holeCandidate {
                Divider()
                Label("穴候補（参考）", systemImage: "sparkles")
                    .font(.subheadline.bold())
                    .foregroundStyle(.orange)
                Text("#\(holeCandidate.horseNumber) \(holeCandidate.horseName) — 展開シミュでは上位ですが、予測AI上位3には入っていません。本命へ自動昇格はさせません。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var replayCard: some View {
        card {
            HStack {
                Label("v0.2 フレーム再生", systemImage: "play.circle.fill")
                    .font(.title3.bold())
                Spacer()
                Text(phase)
                    .font(.subheadline.bold())
                    .foregroundStyle(.orange)
            }

            Text("丸いコースは表示用の模式図です。馬の前後位置・レーン・速度・順位・先頭との差・詰まりはv0.2が出力したフレーム値を使っています。実GPSではありません。")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("馬番を押すと詳細を表示します。馬が重なっている場合は選択メニューから馬番を選べます。")
                .font(.caption)
                .foregroundStyle(.secondary)

            GeometryReader { proxy in
                ZStack {
                    RoundedRectangle(cornerRadius: 22)
                        .fill(Color(uiColor: .secondarySystemBackground))
                    Ellipse()
                        .stroke(Color.brown.opacity(0.25), lineWidth: 48)
                        .padding(28)
                    Ellipse()
                        .stroke(Color.green.opacity(0.68), lineWidth: 32)
                        .padding(28)
                    Ellipse()
                        .stroke(
                            Color.white.opacity(0.75),
                            style: StrokeStyle(lineWidth: 1.5, dash: [7, 6])
                        )
                        .padding(28)

                    ForEach(currentStates) { state in
                        marker(state, size: proxy.size)
                    }
                }
            }
            .frame(height: 320)

            HStack {
                Button {
                    if elapsed >= duration { elapsed = 0.0 }
                    playing.toggle()
                } label: {
                    Label(playing ? "一時停止" : "再生", systemImage: playing ? "pause.fill" : "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("sim-play-pause")

                Button {
                    playing = false
                    elapsed = 0.0
                } label: {
                    Label("最初へ", systemImage: "backward.end.fill")
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("sim-reset")

                Spacer()
                Text(String(format: "%.1f / %.1f秒", elapsed, duration))
                    .font(.caption.monospacedDigit())
                    .accessibilityIdentifier("sim-elapsed")
            }

            Slider(
                value: $elapsed,
                in: 0.0...duration,
                onEditingChanged: { editing in
                    if editing { playing = false }
                }
            )
            .accessibilityLabel("レース再生位置")
            .accessibilityIdentifier("sim-seek")

            Picker("速度", selection: $speed) {
                Text("0.5×").tag(0.5)
                Text("1×").tag(1.0)
                Text("2×").tag(2.0)
                Text("4×").tag(4.0)
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("sim-speed")

            if let selectedHorse,
               let state = currentStates.first(where: { $0.horseNumber == selectedHorse }) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("#\(selectedHorse) \(horseName(selectedHorse))")
                        .font(.subheadline.bold())
                    HStack(spacing: 8) {
                        Text("\(state.rank)位")
                        Text(String(format: "%.1fm/s", state.speedMps))
                        Text(String(format: "差 %.1fm", state.gapMeters))
                    }
                    if state.blocked {
                        Label("詰まり", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                }
                .font(.caption)
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("sim-selected-horse")
            }
        }
    }

    private func markerPoint(_ state: SimFrameHorse, size: CGSize) -> CGPoint {
        let angle = -Double.pi / 2.0 + state.progress * Double.pi * 2.0
        let laneOffset = CGFloat((state.lane - 2.5) * 4.0)
        let radiusX = max(CGFloat(24.0), size.width / 2.0 - 55.0 - laneOffset)
        let radiusY = max(CGFloat(24.0), size.height / 2.0 - 55.0 - laneOffset * 0.45)
        return CGPoint(
            x: size.width / 2.0 + CGFloat(cos(angle)) * radiusX,
            y: size.height / 2.0 + CGFloat(sin(angle)) * radiusY
        )
    }

    @ViewBuilder
    private func marker(_ state: SimFrameHorse, size: CGSize) -> some View {
        let point = markerPoint(state, size: size)
        Button {
            playing = false
            let candidates = currentStates.filter { other in
                let otherPoint = markerPoint(other, size: size)
                return abs(otherPoint.x - point.x) < 36 && abs(otherPoint.y - point.y) < 48
            }.map(\.horseNumber).sorted()
            if candidates.count > 1 {
                overlappingHorses = candidates
                showHorseChoice = true
            } else {
                selectedHorse = state.horseNumber
            }
        } label: {
            VStack(spacing: 1) {
                ZStack {
                    Circle()
                        .fill(horseColor(state.horseNumber))
                        .frame(
                            width: selectedHorse == state.horseNumber ? 34 : 29,
                            height: selectedHorse == state.horseNumber ? 34 : 29
                        )
                    Text("\(state.horseNumber)")
                        .font(.caption.bold())
                        .foregroundStyle(.white)
                }
                Text("\(state.rank)位")
                    .font(.system(size: 9, weight: .bold))
                    .padding(.horizontal, 4)
                    .background(.ultraThinMaterial, in: Capsule())
                if state.blocked {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 9))
                        .foregroundStyle(.orange)
                }
            }
        }
        .buttonStyle(.plain)
        .position(point)
        .animation(.linear(duration: 0.06), value: elapsed)
        .accessibilityLabel("\(state.horseNumber)番 \(horseName(state.horseNumber))、\(state.rank)位")
        .accessibilityValue(String(format: "位置 %.1fm、速度 %.1fm/s、先頭との差 %.1fm、%@", state.positionMeters, state.speedMps, state.gapMeters, state.blocked ? "詰まり" : "進路あり"))
        .accessibilityIdentifier("sim-horse-\(state.horseNumber)")
    }

    private var liveOrderCard: some View {
        card {
            HStack {
                Text("現在位置")
                    .font(.title3.bold())
                Spacer()
                Text(paceLabel(sim.pace))
                    .font(.caption.bold())
                    .foregroundStyle(.orange)
            }

            ForEach(Array(currentStates.prefix(8))) { state in
                HStack {
                    Text("\(state.rank)位  #\(state.horseNumber) \(horseName(state.horseNumber))")
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                    Spacer()
                    Text(String(format: "%.1fm/s", state.speedMps))
                        .font(.caption.monospacedDigit())
                    Text(String(format: "+%.1fm", state.gapMeters))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                    if state.blocked {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                }
            }
        }
    }

    private var resultCard: some View {
        card {
            Text("Monte Carlo 展開評価")
                .font(.title3.bold())
            Text("代表フレーム1本の着順ではなく、\(sim.settings.runs)回の試行結果です。最終判断は上の『総合判断』を優先してください。")
                .font(.caption)
                .foregroundStyle(.secondary)

            ForEach(Array(simRanked.prefix(8))) { horse in
                HStack {
                    Text("\(simRank(for: horse.horseNumber) ?? 0). #\(horse.horseNumber) \(horse.horseName)")
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                    Text(horse.style)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Spacer()
                    VStack(alignment: .trailing) {
                        Text(String(format: "1着 %.1f%% / 3着内 %.1f%%", horse.simulationWinProbability * 100.0, horse.simulationTop3Probability * 100.0))
                            .font(.caption.monospacedDigit())
                        Text(String(format: "詰まり %.1f秒 / 進路変更 %.1f回", horse.averageBlockedSeconds, horse.averageLaneChanges))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private var qualityCard: some View {
        card {
            Label("v0.2 品質・独立性", systemImage: "checkmark.shield.fill")
                .font(.headline)
            Text(String(format: "脚質カバー %.1f%%（%d/%d頭）", sim.inputQuality.styleCoveragePct, sim.inputQuality.styleKnownHorses, sim.inputQuality.styleTotalHorses))
                .font(.subheadline)
            Text("ペース: \(paceLabel(sim.pace)) / \(sim.paceSource)")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("独立能力: \(sim.independence.strengthSource)")
                .font(.caption)
                .foregroundStyle(.secondary)
            Label("予測AI・市場確率・オッズは走行計算に不使用", systemImage: "lock.shield")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.green)
            if let generatedAt = batch?.generatedAt {
                Text("生成: \(generatedAt)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            content()
        }
        .padding(18)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }

    private func horseName(_ number: Int) -> String {
        sim.horses.first { $0.horseNumber == number }?.horseName ?? "#\(number)"
    }

    private func aiRank(for number: Int) -> Int? {
        aiRanked.firstIndex { $0.number == number }.map { $0 + 1 }
    }

    private func simRank(for number: Int) -> Int? {
        simRanked.firstIndex { $0.horseNumber == number }.map { $0 + 1 }
    }

    private func judgmentTitle(aiRank: Int, simRank: Int?) -> String {
        if aiRank == 1 {
            if let simRank, simRank <= 3 { return "高信頼本命" }
            if let simRank, simRank <= 5 { return "本命・軽い注意" }
            return "注意付き本命"
        }
        if let simRank, simRank <= 3 { return "相手有力" }
        if let simRank, simRank <= 5 { return "相手候補" }
        return "AI評価優先・展開注意"
    }

    private func judgmentColor(aiRank: Int, simRank: Int?) -> Color {
        if let simRank, simRank <= 3 { return .green }
        if aiRank == 1 && (simRank == nil || simRank! > 5) { return .orange }
        if let simRank, simRank > 5 { return .orange }
        return .blue
    }

    private func horseColor(_ number: Int) -> Color {
        Color(
            hue: Double((number * 47) % 360) / 360.0,
            saturation: 0.72,
            brightness: 0.84
        )
    }

    private func paceLabel(_ pace: String) -> String {
        if pace == "FAST" { return "ハイ" }
        if pace == "SLOW" { return "スロー" }
        return "平均"
    }
}
