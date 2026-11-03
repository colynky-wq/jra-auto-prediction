import SwiftUI
import Foundation
import Combine

// MARK: - Canonical RaceSimulator JSON

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

private enum IntegratedTone {
    case strong
    case caution
    case normal
    case value
}

private struct IntegratedJudgment: Identifiable {
    var id: Int { horseNumber }
    let horseNumber: Int
    let horseName: String
    let aiRank: Int
    let aiProbability: Double
    let simRank: Int?
    let title: String
    let detail: String
    let tone: IntegratedTone
}

@MainActor
private final class KeibaAIRaceSimulatorStore: ObservableObject {
    @Published private(set) var races: [Race] = []
    @Published private(set) var simulations: [String: SimRace] = [:]
    @Published private(set) var batch: SimBatch?
    @Published private(set) var sourceMessage = "未読み込み"
    @Published private(set) var isLoading = false

    private let rawMain = "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/"
    private let rawFeature = "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/feature/race-simulator-v0-1/"

    func simulation(for raceID: String) -> SimRace? { simulations[raceID] }

    func load(force: Bool = false) async {
        if isLoading || (!force && !races.isEmpty && batch != nil) { return }
        isLoading = true
        defer { isLoading = false }

        do {
            let value: [Race] = try await fetch(rawMain + "RaceData.json")
            races = value.sorted {
                if $0.raceDate != $1.raceDate { return ($0.raceDate ?? "") > ($1.raceDate ?? "") }
                if $0.venue != $1.venue { return venueOrder($0.venue) < venueOrder($1.venue) }
                return $0.raceNumber < $1.raceNumber
            }
        } catch {
            races = []
            sourceMessage = "RaceData取得失敗"
            return
        }

        for url in [rawMain + "RaceSimulationData.json", rawFeature + "RaceSimulationData.json"] {
            do {
                let value: SimBatch = try await fetch(url)
                guard value.schemaVersion == "race-simulator-v0.1-batch" else { continue }
                batch = value
                simulations = Dictionary(uniqueKeysWithValues: value.races.map { ($0.raceID, $0) })
                sourceMessage = url.contains("/main/") ? "正式シミュレーション取得済" : "検証ブランチのシミュレーション取得済"
                return
            } catch {
                continue
            }
        }

        batch = nil
        simulations = [:]
        sourceMessage = "シミュレーション生成待ち"
    }

    private func fetch<T: Decodable>(_ raw: String) async throws -> T {
        guard var components = URLComponents(string: raw) else { throw URLError(.badURL) }
        components.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
        guard let url = components.url else { throw URLError(.badURL) }

        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 15
        request.setValue("KeibaAI-RaceSimulator/0.3", forHTTPHeaderField: "User-Agent")

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

// MARK: - Simulator UI

struct KeibaAIRaceSimulatorScreen: View {
    @StateObject private var store = KeibaAIRaceSimulatorStore()

    var body: some View {
        NavigationSplitView {
            List {
                Section {
                    Label("判断は予測AIが主軸", systemImage: "scope")
                        .font(.headline)
                    Text("展開シミュは『能力予想を置き換えるもの』ではなく、追い風・向かい風を確認する補助評価です。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(store.sourceMessage)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                Section("レース") {
                    ForEach(store.races) { race in
                        NavigationLink {
                            KeibaAIRaceSimulatorDestination(race: race, store: store)
                        } label: {
                            KeibaAIRaceSimulatorRaceRow(
                                race: race,
                                hasSimulation: store.simulation(for: race.raceID) != nil
                            )
                        }
                    }
                }
            }
            .navigationTitle("展開シミュ")
            .refreshable { await store.load(force: true) }
        } detail: {
            ContentUnavailableView(
                "レースを選択",
                systemImage: "hand.tap",
                description: Text("一覧から見たいレースをタップしてください。")
            )
        }
        .task {
            await store.load()
        }
    }
}

private struct KeibaAIRaceSimulatorDestination: View {
    let race: Race
    @ObservedObject var store: KeibaAIRaceSimulatorStore

    var body: some View {
        Group {
            if let sim = store.simulation(for: race.raceID) {
                KeibaAIRaceSimulatorDetail(race: race, sim: sim, batch: store.batch)
                    .id(sim.raceID)
            } else if store.isLoading {
                ProgressView("シミュレーションを取得中…")
            } else {
                ContentUnavailableView(
                    "シミュレーション生成待ち",
                    systemImage: "clock",
                    description: Text("\(race.venue) \(race.raceNumber)R の正式RaceSimulator出力がまだ公開されていません。")
                )
            }
        }
        .navigationTitle("\(race.venue) \(race.raceNumber)R")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct KeibaAIRaceSimulatorRaceRow: View {
    let race: Race
    let hasSimulation: Bool

    private var title: String {
        race.venue + " " + String(race.raceNumber) + "R"
    }

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline)
                    .fontWeight(.bold)
                Text(race.raceName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Image(systemName: hasSimulation ? "checkmark.circle.fill" : "clock")
                .foregroundStyle(hasSimulation ? Color.green : Color.secondary)
        }
    }
}

private struct KeibaAIRaceSimulatorDetail: View {
    let race: Race
    let sim: SimRace
    let batch: SimBatch?

    @State private var replayProgress = 0.0
    @State private var isReplayPlaying = false
    @State private var replaySpeed = 1.0

    private let replayTimer = Timer.publish(every: 1.0 / 30.0, on: .main, in: .common).autoconnect()

    private var aiRanked: [Horse] {
        race.horses.sorted {
            if $0.aiProbability != $1.aiProbability { return $0.aiProbability > $1.aiProbability }
            return $0.number < $1.number
        }
    }

    private var simRanked: [SimHorse] {
        sim.horses.sorted {
            if $0.simulationWinProbability != $1.simulationWinProbability {
                return $0.simulationWinProbability > $1.simulationWinProbability
            }
            return $0.expectedFinish < $1.expectedFinish
        }
    }

    private var aiTop3: [Horse] { Array(aiRanked.prefix(3)) }
    private var simTop3: [SimHorse] { Array(simRanked.prefix(3)) }

    private var integratedJudgments: [IntegratedJudgment] {
        let simRanks = Dictionary(uniqueKeysWithValues: simRanked.enumerated().map { ($0.element.horseNumber, $0.offset + 1) })

        return Array(aiRanked.prefix(3)).enumerated().map { index, horse in
            let aiRank = index + 1
            let simRank = simRanks[horse.number]

            if aiRank == 1 {
                if let simRank, simRank <= 3 {
                    return IntegratedJudgment(
                        horseNumber: horse.number,
                        horseName: horse.name,
                        aiRank: aiRank,
                        aiProbability: horse.aiProbability,
                        simRank: simRank,
                        title: "高信頼本命",
                        detail: "予測AI 1位に加え、展開シミュでも上位。能力評価と展開評価がそろっています。",
                        tone: .strong
                    )
                }
                if let simRank, simRank <= 5 {
                    return IntegratedJudgment(
                        horseNumber: horse.number,
                        horseName: horse.name,
                        aiRank: aiRank,
                        aiProbability: horse.aiProbability,
                        simRank: simRank,
                        title: "本命・軽い注意",
                        detail: "予測AI 1位を維持。展開面は最上位ではないので、相手を広げる材料として見ます。",
                        tone: .normal
                    )
                }
                return IntegratedJudgment(
                    horseNumber: horse.number,
                    horseName: horse.name,
                    aiRank: aiRank,
                    aiProbability: horse.aiProbability,
                    simRank: simRank,
                    title: "注意付き本命",
                    detail: "能力評価は最上位。ただし展開シミュでは下位なので、過信せず展開リスクを意識します。",
                    tone: .caution
                )
            }

            if let simRank, simRank <= 3 {
                return IntegratedJudgment(
                    horseNumber: horse.number,
                    horseName: horse.name,
                    aiRank: aiRank,
                    aiProbability: horse.aiProbability,
                    simRank: simRank,
                    title: "相手有力",
                    detail: "予測AI上位に加えて展開も向きそう。相手候補として信頼度を上げます。",
                    tone: .strong
                )
            }

            if let simRank, simRank <= 5 {
                return IntegratedJudgment(
                    horseNumber: horse.number,
                    horseName: horse.name,
                    aiRank: aiRank,
                    aiProbability: horse.aiProbability,
                    simRank: simRank,
                    title: "相手候補",
                    detail: "予測AI評価を優先しつつ、展開面も大きな逆風ではありません。",
                    tone: .normal
                )
            }

            return IntegratedJudgment(
                horseNumber: horse.number,
                horseName: horse.name,
                aiRank: aiRank,
                aiProbability: horse.aiProbability,
                simRank: simRank,
                title: "AI評価優先・展開注意",
                detail: "能力評価を理由に候補へ残しますが、展開面は割引材料として扱います。",
                tone: .caution
            )
        }
    }

    private var holeCandidate: SimHorse? {
        let aiTopNumbers = Set(aiTop3.map(\.number))
        return simTop3.first { !aiTopNumbers.contains($0.horseNumber) }
    }

    private var commonTop3Count: Int {
        let aiSet = Set(aiTop3.map(\.number))
        return simTop3.filter { aiSet.contains($0.horseNumber) }.count
    }

    private var replayDuration: Double {
        max(sim.horses.map(\.averageFinishTimeSeconds).max() ?? 90.0, 1.0)
    }

    private var replayElapsedSeconds: Double {
        replayProgress * replayDuration
    }

    private var replayPhase: String {
        switch replayProgress {
        case ..<0.03: return "スタート"
        case ..<0.22: return "序盤"
        case ..<0.45: return "向正面"
        case ..<0.68: return "3〜4角"
        case ..<0.86: return "直線入口"
        case ..<0.995: return "直線"
        default: return "ゴール"
        }
    }

    private var replayLeaders: [SimHorse] {
        sim.horses.sorted {
            let leftProgress = replayCourseProgress(for: $0)
            let rightProgress = replayCourseProgress(for: $1)
            if abs(leftProgress - rightProgress) > 0.0001 { return leftProgress > rightProgress }
            return $0.expectedFinish < $1.expectedFinish
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                headerCard
                overallJudgmentCard
                replayCard
                resultCard
                positionCard
                trafficCard
                comparisonCard
                qualityCard
            }
            .padding(18)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("\(race.venue) \(race.raceNumber)R")
        .navigationBarTitleDisplayMode(.inline)
        .onReceive(replayTimer) { _ in
            advanceReplay()
        }
    }

    private var headerCard: some View {
        card {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(race.venue) \(race.raceNumber)R ・ \(race.startTime)")
                        .font(.caption)
                        .fontWeight(.bold)
                        .foregroundStyle(.blue)
                    Text(race.raceName)
                        .font(.title2)
                        .fontWeight(.bold)
                    Text(sim.track ?? race.course)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 4) {
                    Text("KeibaAI v0.3")
                        .font(.caption)
                        .fontWeight(.bold)
                        .padding(7)
                        .background(.blue.opacity(0.10), in: Capsule())
                    Text("AI-first integration")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Label("判断ルール", systemImage: "checkmark.seal.fill")
                    .font(.headline)
                    .foregroundStyle(.blue)
                Text("馬券の最終判断は予測AIを主軸にします。展開シミュは『追い風・向かい風』を確認する補助評価で、シミュ順位だけを理由に本命を逆転させません。")
                    .font(.subheadline.weight(.semibold))
                Text("予測AI = どの馬が強いか / 展開シミュ = その強さを発揮しやすいか")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(12)
            .background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))

            if let generated = batch?.generatedAt {
                Text("シミュレーション生成: \(generated)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var overallJudgmentCard: some View {
        card {
            HStack(alignment: .firstTextBaseline) {
                Label("総合判断", systemImage: "checkmark.circle.fill")
                    .font(.title3.bold())
                Spacer()
                Text("予測AI主軸")
                    .font(.caption.bold())
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(.blue.opacity(0.10), in: Capsule())
            }

            Text("迷ったときは、この欄を優先してください。新しい合成勝率は作らず、予測AI順位を維持したまま展開リスクだけを加味しています。")
                .font(.caption)
                .foregroundStyle(.secondary)

            ForEach(integratedJudgments) { judgment in
                VStack(alignment: .leading, spacing: 7) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("#\(judgment.horseNumber) \(judgment.horseName)")
                            .font(.headline)
                            .lineLimit(1)
                        Spacer()
                        Text(judgment.title)
                            .font(.caption.bold())
                            .foregroundStyle(judgmentColor(judgment.tone))
                    }

                    HStack(spacing: 8) {
                        Text("AI \(judgment.aiRank)位")
                            .font(.caption.bold())
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(.blue.opacity(0.10), in: Capsule())
                        Text(String(format: "AI勝率 %.1f%%", judgment.aiProbability))
                            .font(.caption.monospacedDigit())
                        Text(judgment.simRank.map { "展開 \($0)位" } ?? "展開 対象外")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }

                    Text(judgment.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(12)
                .background(judgmentColor(judgment.tone).opacity(0.07), in: RoundedRectangle(cornerRadius: 14))
            }

            if let hole = holeCandidate {
                Divider()
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Label("穴候補（参考）", systemImage: "sparkles")
                            .font(.subheadline.bold())
                            .foregroundStyle(.orange)
                        Spacer()
                        if let rank = simRank(for: hole.horseNumber) {
                            Text("展開 \(rank)位")
                                .font(.caption.bold())
                        }
                    }
                    Text("#\(hole.horseNumber) \(hole.horseName)")
                        .font(.headline)
                    Text("展開シミュでは上位ですが、予測AI上位3には入っていません。本命への自動昇格ではなく、ワイド・複勝などの相手候補を検討する材料です。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if sim.independence.strengthSource == "neutral-equal-strength" {
                Label("現在の展開シミュは能力差を同一にした neutral mode。AI順位より強く扱わないでください。", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.orange)
            }
        }
    }

    private var replayCard: some View {
        card {
            HStack(alignment: .firstTextBaseline) {
                Label("レース展開を再生", systemImage: "play.circle.fill")
                    .font(.title3.bold())
                Spacer()
                Text(replayPhase)
                    .font(.subheadline.bold())
                    .foregroundStyle(.orange)
            }

            Text("これは代表的な展開の可視化です。実映像やGPS軌跡ではなく、馬の能力順位そのものを示す画面でもありません。")
                .font(.caption)
                .foregroundStyle(.secondary)

            GeometryReader { proxy in
                ZStack {
                    RoundedRectangle(cornerRadius: 22)
                        .fill(Color(uiColor: .secondarySystemBackground))

                    Ellipse()
                        .stroke(Color.brown.opacity(0.28), lineWidth: 42)
                        .padding(26)

                    Ellipse()
                        .stroke(Color.green.opacity(0.70), lineWidth: 28)
                        .padding(26)

                    Ellipse()
                        .stroke(
                            Color.white.opacity(0.72),
                            style: StrokeStyle(lineWidth: 1.5, dash: [7, 6])
                        )
                        .padding(26)

                    Path { path in
                        let x = proxy.size.width / 2
                        path.move(to: CGPoint(x: x - 15, y: 18))
                        path.addLine(to: CGPoint(x: x - 15, y: 61))
                    }
                    .stroke(
                        Color.primary.opacity(0.75),
                        style: StrokeStyle(lineWidth: 3, dash: [4, 3])
                    )

                    Text("START / GOAL")
                        .font(.system(size: 9, weight: .bold, design: .rounded))
                        .foregroundStyle(.secondary)
                        .position(x: proxy.size.width / 2 + 29, y: 18)

                    ForEach(sim.horses) { horse in
                        replayHorseMarker(horse, size: proxy.size)
                    }
                }
            }
            .frame(height: 320)

            HStack(spacing: 10) {
                Button {
                    if replayProgress >= 0.999 { replayProgress = 0 }
                    isReplayPlaying.toggle()
                } label: {
                    Label(isReplayPlaying ? "一時停止" : "再生", systemImage: isReplayPlaying ? "pause.fill" : "play.fill")
                        .frame(minWidth: 82)
                }
                .buttonStyle(.borderedProminent)

                Button {
                    resetReplay()
                } label: {
                    Label("最初へ", systemImage: "backward.end.fill")
                }
                .buttonStyle(.bordered)

                Spacer()

                VStack(alignment: .trailing, spacing: 1) {
                    Text(String(format: "%.1f / %.1f秒", replayElapsedSeconds, replayDuration))
                        .font(.caption.monospacedDigit())
                    Text("進行目安 \(Int((replayProgress * sim.distanceMeters).rounded())) / \(Int(sim.distanceMeters.rounded()))m")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }

            Slider(
                value: $replayProgress,
                in: 0...1,
                onEditingChanged: { editing in
                    if editing { isReplayPlaying = false }
                }
            )
            .accessibilityLabel("レース再生位置")

            Picker("再生速度", selection: $replaySpeed) {
                Text("0.5×").tag(0.5)
                Text("1×").tag(1.0)
                Text("2×").tag(2.0)
                Text("4×").tag(4.0)
            }
            .pickerStyle(.segmented)

            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("現在の先頭グループ")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                    Text(replayLeaders.prefix(3).map { "#\($0.horseNumber) \($0.horseName)" }.joined(separator: "  /  "))
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(2)
                }
                Spacer()
                Text(paceLabel(sim.pace))
                    .font(.caption.bold())
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(.orange.opacity(0.12), in: Capsule())
            }
        }
    }

    @ViewBuilder
    private func replayHorseMarker(_ horse: SimHorse, size: CGSize) -> some View {
        let point = replayPoint(for: horse, size: size)
        let rank = interpolatedMedianRank(for: horse, progress: replayProgress)

        VStack(spacing: 2) {
            ZStack {
                Circle()
                    .fill(horseColor(horse.horseNumber))
                    .frame(width: 30, height: 30)
                    .shadow(radius: 2, y: 1)
                Text("\(horse.horseNumber)")
                    .font(.caption.bold().monospacedDigit())
                    .foregroundStyle(.white)
            }
            Text(String(format: "%.1f位", rank))
                .font(.system(size: 9, weight: .semibold, design: .rounded))
                .padding(.horizontal, 4)
                .padding(.vertical, 1)
                .background(.ultraThinMaterial, in: Capsule())
        }
        .position(point)
        .animation(.linear(duration: 0.08), value: replayProgress)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(horse.horseNumber)番 \(horse.horseName)、推定\(String(format: "%.1f", rank))位")
    }

    private var resultCard: some View {
        card {
            HStack {
                Text("展開だけで見た結果")
                    .font(.title3.bold())
                Spacer()
                Text(paceLabel(sim.pace))
                    .font(.subheadline.bold())
                    .foregroundStyle(.orange)
            }

            Text("ここは最終予想ではありません。詰まり・進路変更・スタミナ配分を含むMonte Carlo \(sim.settings.runs)回の展開評価です。")
                .font(.caption)
                .foregroundStyle(.secondary)

            ForEach(Array(simRanked.prefix(8).enumerated()), id: \.element.id) { index, horse in
                HStack {
                    Text("\(index + 1). #\(horse.horseNumber) \(horse.horseName)")
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                    Text(horse.style)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Spacer()
                    VStack(alignment: .trailing) {
                        Text(String(format: "展開1着 %.1f%% / 3着内 %.1f%%", horse.simulationWinProbability * 100, horse.simulationTop3Probability * 100))
                            .font(.caption.monospacedDigit())
                        Text(String(format: "平均 %.2f着", horse.expectedFinish))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                if index < min(7, simRanked.count - 1) { Divider() }
            }

            if sim.independence.strengthSource == "neutral-equal-strength" {
                Text("重要: 全馬の独立能力値を1.0にした neutral mode のため、ここに出る順位は『展開だけの差』です。")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.orange)
            }
        }
    }

    private var positionCard: some View {
        card {
            Text("位置取りレンジ")
                .font(.title3.bold())
            Text("各地点の中央値。括弧内は20〜80%レンジです。")
                .font(.caption)
                .foregroundStyle(.secondary)

            ForEach(simRanked.prefix(6)) { horse in
                VStack(alignment: .leading, spacing: 5) {
                    Text("#\(horse.horseNumber) \(horse.horseName)")
                        .font(.subheadline.bold())
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(horse.rankBands) { band in
                                VStack(spacing: 2) {
                                    Text(progressLabel(band.progress))
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                    Text(String(format: "%.1f位", band.medianRank))
                                        .font(.caption.bold().monospacedDigit())
                                    Text(String(format: "%.1f–%.1f", band.p20Rank, band.p80Rank))
                                        .font(.caption2.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                                .padding(8)
                                .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
                            }
                        }
                    }
                }
            }
        }
    }

    private var trafficCard: some View {
        card {
            Text("詰まり・進路変更")
                .font(.title3.bold())
            ForEach(simRanked.prefix(6)) { horse in
                HStack {
                    Text("#\(horse.horseNumber) \(horse.horseName)")
                        .font(.subheadline)
                        .lineLimit(1)
                    Spacer()
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(String(format: "詰まり %.1f秒", horse.averageBlockedSeconds))
                            .font(.caption.monospacedDigit())
                        Text(String(format: "進路変更 %.1f回", horse.averageLaneChanges))
                            .font(.caption.monospacedDigit())
                    }
                }
            }
        }
    }

    private var comparisonCard: some View {
        let alignmentLabel = commonTop3Count == 3 ? "高整合" : (commonTop3Count == 2 ? "整合" : (commonTop3Count == 1 ? "一部整合" : "乖離大"))
        let disagreements = (sim.comparisonWithPredictionAI.horses ?? []).prefix(4)

        return card {
            HStack {
                Text("AIと展開の詳細比較")
                    .font(.title3.bold())
                Spacer()
                Text(alignmentLabel)
                    .font(.caption.bold())
                    .foregroundStyle(commonTop3Count >= 2 ? .green : .orange)
            }

            Text("この比較は原因確認用です。最終判断そのものは上の『総合判断』を見てください。")
                .font(.caption)
                .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 6) {
                Text("予測AI 上位3（主軸）")
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
                ForEach(Array(aiTop3.enumerated()), id: \.element.id) { index, horse in
                    Text("\(index + 1)位  #\(horse.number) \(horse.name)  \(String(format: "%.1f%%", horse.aiProbability))")
                        .font(.caption)
                }
            }

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Text("展開シミュ 上位3（補助）")
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
                ForEach(Array(simTop3.enumerated()), id: \.element.id) { index, horse in
                    Text("\(index + 1)位  #\(horse.horseNumber) \(horse.horseName)  \(String(format: "%.1f%%", horse.simulationWinProbability * 100))")
                        .font(.caption)
                }
            }

            if !disagreements.isEmpty {
                Divider()
                Text("意見差が大きい馬")
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
                ForEach(Array(disagreements)) { row in
                    HStack {
                        Text("#\(row.horseNumber) \(row.horseName)")
                            .font(.caption)
                        Spacer()
                        Text(comparisonLabel(row.label))
                            .font(.caption.bold())
                            .foregroundStyle(row.delta > 0 ? .green : .orange)
                        Text(String(format: "%+.1fpt", row.delta * 100))
                            .font(.caption.monospacedDigit())
                    }
                }
            }
        }
    }

    private var qualityCard: some View {
        card {
            Label("入力品質", systemImage: "gauge.with.dots.needle.67percent")
                .font(.headline)
            Text(String(format: "脚質カバー %.1f%%（%d/%d頭）", sim.inputQuality.styleCoveragePct, sim.inputQuality.styleKnownHorses, sim.inputQuality.styleTotalHorses))
                .font(.subheadline)
            Text("ペース: \(paceLabel(sim.pace)) / source: \(sim.paceSource)")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("能力値: \(sim.independence.strengthSource)")
                .font(.caption)
                .foregroundStyle(.secondary)
            if sim.paceSource == "neutral-mid-fallback" {
                Text("現在のペース予測は未検証のため、勝手に推定せずMIDを中立値として使用しています。")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
    }

    @ViewBuilder
    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) { content() }
            .padding(18)
            .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }

    private func simRank(for horseNumber: Int) -> Int? {
        simRanked.firstIndex { $0.horseNumber == horseNumber }.map { $0 + 1 }
    }

    private func judgmentColor(_ tone: IntegratedTone) -> Color {
        switch tone {
        case .strong: return .green
        case .caution: return .orange
        case .normal: return .blue
        case .value: return .purple
        }
    }

    private func advanceReplay() {
        guard isReplayPlaying else { return }
        let increment = (1.0 / 30.0) * replaySpeed / replayDuration
        replayProgress = min(1.0, replayProgress + increment)
        if replayProgress >= 0.9999 {
            replayProgress = 1.0
            isReplayPlaying = false
        }
    }

    private func resetReplay() {
        isReplayPlaying = false
        replayProgress = 0
    }

    private func replayCourseProgress(for horse: SimHorse) -> Double {
        let horseDuration = max(horse.averageFinishTimeSeconds, 1.0)
        let elapsed = replayElapsedSeconds
        let base = min(1.0, max(0.0, elapsed / horseDuration))
        guard base > 0, base < 1 else { return base }

        let rank = interpolatedMedianRank(for: horse, progress: base)
        let field = Double(max(sim.horses.count, 1))
        let middleRank = (field + 1.0) / 2.0
        let rankOffset = ((middleRank - rank) / field) * 0.075 * sin(.pi * base)
        return min(1.0, max(0.0, base + rankOffset))
    }

    private func replayPoint(for horse: SimHorse, size: CGSize) -> CGPoint {
        let progress = replayCourseProgress(for: horse)
        let angle = (-Double.pi / 2.0) + (progress * Double.pi * 2.0)
        let laneJitter = CGFloat((horse.horseNumber % 5) - 2) * 2.5
        let radiusX = max(24, size.width / 2 - 52 + laneJitter)
        let radiusY = max(24, size.height / 2 - 52 + laneJitter * 0.45)

        return CGPoint(
            x: size.width / 2 + cos(angle) * radiusX,
            y: size.height / 2 + sin(angle) * radiusY
        )
    }

    private func interpolatedMedianRank(for horse: SimHorse, progress: Double) -> Double {
        let bands = horse.rankBands.sorted { $0.progress < $1.progress }
        guard let first = bands.first else { return horse.expectedFinish }
        if progress <= first.progress { return first.medianRank }
        guard let last = bands.last else { return first.medianRank }
        if progress >= last.progress { return last.medianRank }

        for index in 1..<bands.count {
            let lower = bands[index - 1]
            let upper = bands[index]
            guard progress <= upper.progress else { continue }
            let width = max(upper.progress - lower.progress, 0.0001)
            let fraction = (progress - lower.progress) / width
            return lower.medianRank + (upper.medianRank - lower.medianRank) * fraction
        }
        return last.medianRank
    }

    private func horseColor(_ number: Int) -> Color {
        let hue = Double((number * 47) % 360) / 360.0
        return Color(hue: hue, saturation: 0.72, brightness: 0.84)
    }

    private func paceLabel(_ raw: String) -> String {
        raw == "FAST" ? "ハイ" : (raw == "SLOW" ? "スロー" : "平均")
    }

    private func progressLabel(_ progress: Double) -> String {
        progress >= 0.999 ? "ゴール" : "\(Int((progress * 100).rounded()))%"
    }

    private func comparisonLabel(_ raw: String) -> String {
        switch raw {
        case "SIMULATOR_HIGHER": return "展開↑"
        case "PREDICTOR_HIGHER": return "AI↑"
        default: return "一致"
        }
    }
}
