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
        guard var c = URLComponents(string: raw) else { throw URLError(.badURL) }
        c.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
        guard let url = c.url else { throw URLError(.badURL) }
        var req = URLRequest(url: url)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        req.timeoutInterval = 15
        req.setValue("KeibaAI-RaceSimulator/0.2", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else { throw URLError(.badServerResponse) }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func venueOrder(_ venue: String) -> Int {
        ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"].firstIndex(of: venue) ?? 99
    }
}

// MARK: - Simulator UI

struct KeibaAIRaceSimulatorScreen: View {
    @StateObject private var store = KeibaAIRaceSimulatorStore()
    @State private var selectedID: String?

    private var selectedRace: Race? {
        store.races.first { $0.raceID == selectedID } ?? store.races.first
    }

    var body: some View {
        NavigationSplitView {
            List {
                Section {
                    Label("予測AIから独立", systemImage: "lock.shield")
                        .font(.headline)
                    Text("動作計算にAI勝率・市場確率・オッズ・人気を使用しません。AIとの比較はシミュレーション完了後だけです。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(store.sourceMessage)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Section("レース") {
                    ForEach(store.races) { race in
                        Button {
                            selectedID = race.raceID
                        } label: {
                            KeibaAIRaceSimulatorRaceRow(
                                race: race,
                                hasSimulation: store.simulation(for: race.raceID) != nil
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .navigationTitle("展開シミュ")
            .refreshable { await store.load(force: true) }
        } detail: {
            if let race = selectedRace, let sim = store.simulation(for: race.raceID) {
                KeibaAIRaceSimulatorDetail(race: race, sim: sim, batch: store.batch)
                    .id(sim.raceID)
            } else if store.isLoading {
                ProgressView("シミュレーションを取得中…")
            } else if let race = selectedRace {
                ContentUnavailableView(
                    "シミュレーション生成待ち",
                    systemImage: "clock",
                    description: Text("\(race.venue) \(race.raceNumber)R の正式RaceSimulator出力がまだ公開されていません。")
                )
            } else {
                ContentUnavailableView("レースデータなし", systemImage: "tray")
            }
        }
        .task {
            await store.load()
            if selectedID == nil { selectedID = store.races.first?.raceID }
        }
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

    private var simTop3: [SimHorse] { Array(sim.horses.prefix(3)) }
    private var aiTop3: [Horse] { Array(race.horses.sorted { $0.aiProbability > $1.aiProbability }.prefix(3)) }

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
            let lp = replayCourseProgress(for: $0)
            let rp = replayCourseProgress(for: $1)
            if abs(lp - rp) > 0.0001 { return lp > rp }
            return $0.expectedFinish < $1.expectedFinish
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                headerCard
                replayCard
                resultCard
                positionCard
                trafficCard
                integrationCard
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
                    Text("KeibaAI v0.2")
                        .font(.caption)
                        .fontWeight(.bold)
                        .padding(7)
                        .background(.blue.opacity(0.10), in: Capsule())
                    Text("Replay UI")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            Divider()
            Label("独立性チェック済み", systemImage: "checkmark.shield.fill")
                .font(.headline)
                .foregroundStyle(.green)
            Text("AI勝率: \(sim.independence.usesPredictionAIForMotion ? "使用" : "未使用") / 市場確率: \(sim.independence.usesMarketProbabilityForMotion ? "使用" : "未使用") / オッズ: \(sim.independence.usesOddsForMotion ? "使用" : "未使用")")
                .font(.subheadline)
            if let generated = batch?.generatedAt {
                Text("生成: \(generated)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
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

            Text("各馬の位置取りレンジと平均走破時間から、展開の流れを連続表示します。実映像やGPS軌跡ではなく、RaceSimulatorの代表的な展開を可視化したものです。")
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
                    if replayProgress >= 0.999 {
                        replayProgress = 0
                    }
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
                Text("シミュレーターの答え").font(.title3.bold())
                Spacer()
                Text(paceLabel(sim.pace)).font(.subheadline.bold()).foregroundStyle(.orange)
            }
            Text("詰まり・進路変更・スタミナ配分を含むMonte Carlo \(sim.settings.runs)回の結果です。")
                .font(.caption).foregroundStyle(.secondary)
            ForEach(Array(sim.horses.prefix(8).enumerated()), id: \.offset) { index, horse in
                HStack {
                    Text("\(index + 1). #\(horse.horseNumber) \(horse.horseName)").font(.subheadline.weight(.semibold)).lineLimit(1)
                    Text(horse.style).font(.caption2).foregroundStyle(.secondary)
                    Spacer()
                    VStack(alignment: .trailing) {
                        Text(String(format: "1着 %.1f%% / 3着内 %.1f%%", horse.simulationWinProbability * 100, horse.simulationTop3Probability * 100))
                            .font(.caption.monospacedDigit())
                        Text(String(format: "平均 %.2f着", horse.expectedFinish)).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if index < min(7, sim.horses.count - 1) { Divider() }
            }
            if sim.independence.strengthSource == "neutral-equal-strength" {
                Text("重要: 現在は全馬の独立能力値を1.0にした neutral mode。これは『展開だけの差』であり、本番の最終勝率ではありません。")
                    .font(.caption.weight(.semibold)).foregroundStyle(.orange)
            }
        }
    }

    private var positionCard: some View {
        card {
            Text("位置取りレンジ").font(.title3.bold())
            Text("各地点の中央値。括弧内は20〜80%レンジです。")
                .font(.caption).foregroundStyle(.secondary)
            ForEach(sim.horses.prefix(6)) { horse in
                VStack(alignment: .leading, spacing: 5) {
                    Text("#\(horse.horseNumber) \(horse.horseName)").font(.subheadline.bold())
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(horse.rankBands) { band in
                                VStack(spacing: 2) {
                                    Text(progressLabel(band.progress)).font(.caption2).foregroundStyle(.secondary)
                                    Text(String(format: "%.1f位", band.medianRank)).font(.caption.bold().monospacedDigit())
                                    Text(String(format: "%.1f–%.1f", band.p20Rank, band.p80Rank)).font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
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
            Text("詰まり・進路変更").font(.title3.bold())
            ForEach(sim.horses.prefix(6)) { horse in
                HStack {
                    Text("#\(horse.horseNumber) \(horse.horseName)").font(.subheadline).lineLimit(1)
                    Spacer()
                    Text(String(format: "詰まり %.1f秒", horse.averageBlockedSeconds)).font(.caption.monospacedDigit())
                    Text(String(format: "進路変更 %.1f回", horse.averageLaneChanges)).font(.caption.monospacedDigit())
                }
            }
        }
    }

    private var integrationCard: some View {
        let aiSet = Set(aiTop3.map(\.number))
        let common = simTop3.filter { aiSet.contains($0.horseNumber) }
        let label = common.count == 3 ? "高整合" : (common.count == 2 ? "整合" : (common.count == 1 ? "一部整合" : "乖離大"))
        let disagreements = (sim.comparisonWithPredictionAI.horses ?? []).prefix(4)
        return card {
            HStack {
                Text("最終統合").font(.title3.bold())
                Spacer()
                Text(label).font(.caption.bold()).foregroundStyle(common.count >= 2 ? .green : .orange)
            }
            Text("ここで初めて既存予測AIとRaceSimulatorを照合します。展開計算への逆流はありません。")
                .font(.subheadline).foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("予測AI 上位3").font(.caption.bold()).foregroundStyle(.secondary)
                    ForEach(aiTop3) { horse in
                        Text("#\(horse.number) \(horse.name)  \(String(format: "%.1f%%", horse.aiProbability))").font(.caption)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .leading, spacing: 4) {
                    Text("展開シミュ 上位3").font(.caption.bold()).foregroundStyle(.secondary)
                    ForEach(simTop3) { horse in
                        Text("#\(horse.horseNumber) \(horse.horseName)  \(String(format: "%.1f%%", horse.simulationWinProbability * 100))").font(.caption)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
            Text(common.isEmpty ? "共通上位なし。AI本命に展開上の逆風がないか要確認。" : "共通上位: " + common.map { "#\($0.horseNumber) \($0.horseName)" }.joined(separator: "、"))
                .font(.subheadline.weight(.semibold))
            if !disagreements.isEmpty {
                Divider()
                Text("意見差が大きい馬").font(.caption.bold()).foregroundStyle(.secondary)
                ForEach(Array(disagreements)) { row in
                    HStack {
                        Text("#\(row.horseNumber) \(row.horseName)").font(.caption)
                        Spacer()
                        Text(comparisonLabel(row.label)).font(.caption.bold()).foregroundStyle(row.delta > 0 ? .green : .orange)
                        Text(String(format: "%+.1fpt", row.delta * 100)).font(.caption.monospacedDigit())
                    }
                }
            }
            Text("統合表示は研究用です。既存AIの確率や馬券金額を自動で書き換えません。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var qualityCard: some View {
        card {
            Label("入力品質", systemImage: "gauge.with.dots.needle.67percent").font(.headline)
            Text(String(format: "脚質カバー %.1f%%（%d/%d頭）", sim.inputQuality.styleCoveragePct, sim.inputQuality.styleKnownHorses, sim.inputQuality.styleTotalHorses))
                .font(.subheadline)
            Text("ペース: \(paceLabel(sim.pace)) / source: \(sim.paceSource)").font(.caption).foregroundStyle(.secondary)
            Text("能力値: \(sim.independence.strengthSource)").font(.caption).foregroundStyle(.secondary)
            if sim.paceSource == "neutral-mid-fallback" {
                Text("現在のペース予測は未検証のため、勝手に推定せずMIDを中立値として使用しています。")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }

    @ViewBuilder
    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) { content() }
            .padding(18)
            .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
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

    private func progressLabel(_ p: Double) -> String {
        p >= 0.999 ? "ゴール" : "\(Int((p * 100).rounded()))%"
    }

    private func comparisonLabel(_ raw: String) -> String {
        switch raw {
        case "SIMULATOR_HIGHER": return "展開↑"
        case "PREDICTOR_HIGHER": return "AI↑"
        default: return "一致"
        }
    }
}
