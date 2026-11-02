import SwiftUI
import Foundation
import Combine

@main
struct KeibaAIApp: App {
    var body: some Scene {
        WindowGroup { KeibaAIRootView() }
    }
}

private struct KeibaAIRootView: View {
    var body: some View {
        TabView {
            KeibaAINewDesignView()
                .tabItem { Label("予測AI", systemImage: "sparkles") }
            KeibaAIRaceSimulatorScreen()
                .tabItem { Label("展開シミュ", systemImage: "figure.run") }
        }
    }
}

// MARK: - Simulator source data

private struct SimResearchEnvelope: Decodable {
    let generatedAtJST: String
    let races: [SimResearchRace]
}

private struct SimResearchRace: Decodable {
    let raceID: String
    let raceDate: String
    let horses: [SimResearchHorse]
}

private struct SimResearchHorse: Decodable {
    let number: Int
    let name: String
    let styleTendency: SimStyle?
    let initialPositionHistory: SimPosition?
}

private struct SimStyle: Decodable {
    let sampleRaces: Int
    let confidence: String
    let distributionPct: [String: Double]
    let latestHistoryDate: String
}

private struct SimPosition: Decodable {
    let sampleRaces: Int
    let meanRelativePosition: Double
    let latestHistoryDate: String
}

@MainActor
private final class KeibaAIRaceSimulatorStore: ObservableObject {
    @Published var races: [Race] = []
    @Published var research: [String: SimResearchRace] = [:]
    @Published var generatedAtJST: String?
    @Published var isLoading = false
    @Published var message = "未読み込み"

    private let base = "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/"

    func load(force: Bool = false) async {
        if isLoading || (!force && !races.isEmpty) { return }
        isLoading = true
        defer { isLoading = false }

        do {
            let value: [Race] = try await fetch(base + "RaceData.json")
            races = value.sorted {
                if $0.raceDate != $1.raceDate { return ($0.raceDate ?? "") > ($1.raceDate ?? "") }
                if $0.venue != $1.venue { return venueOrder($0.venue) < venueOrder($1.venue) }
                return $0.raceNumber < $1.raceNumber
            }
            message = "レース取得済"
        } catch {
            races = []
            message = "RaceData取得失敗"
        }

        do {
            let value: SimResearchEnvelope = try await fetch(base + "AppResearchData.json")
            research = Dictionary(uniqueKeysWithValues: value.races.map { ($0.raceID, $0) })
            generatedAtJST = value.generatedAtJST
            if !races.isEmpty { message = "レース・脚質取得済" }
        } catch {
            research = [:]
            if !races.isEmpty { message = "脚質データ更新待ち" }
        }
    }

    func research(for race: Race) -> SimResearchRace? {
        guard let value = research[race.raceID] else { return nil }
        guard let date = race.raceDate else { return value }
        return date.replacingOccurrences(of: "-", with: "") == value.raceDate.replacingOccurrences(of: "-", with: "") ? value : nil
    }

    private func fetch<T: Decodable>(_ raw: String) async throws -> T {
        guard var c = URLComponents(string: raw) else { throw URLError(.badURL) }
        c.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
        guard let url = c.url else { throw URLError(.badURL) }
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 12
        request.setValue("KeibaAI-RaceSimulator/0.1", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else { throw URLError(.badServerResponse) }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func venueOrder(_ venue: String) -> Int {
        ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"].firstIndex(of: venue) ?? 99
    }
}

// MARK: - Independent simulation engine

private struct SimInput {
    let number: Int
    let name: String
    let escape: Double
    let front: Double
    let stalk: Double
    let close: Double
    let samples: Int
    let meanPosition: Double?
    let hasHistory: Bool
}

private struct SimStat: Identifiable {
    var id: Int { number }
    let number: Int
    let name: String
    let winPct: Double
    let top3Pct: Double
    let meanRank: Double
    let samples: Int
    let hasHistory: Bool
}

private struct SimResult {
    let pace: String
    let reason: String
    let coverage: Double
    let confidence: String
    let phases: [(String, [Int])]
    let scenarios: [(String, String, [Int])]
    let stats: [SimStat]
}

private enum RaceSimulator {
    private struct Model { let input: SimInput; let base: Double }
    private struct RNG {
        var state: UInt64
        mutating func unit() -> Double {
            state = state &* 2862933555777941757 &+ 3037000493
            return Double(state >> 11) / Double(UInt64.max >> 11)
        }
        mutating func centered() -> Double { unit() * 2 - 1 }
    }

    static func run(race: Race, inputs: [SimInput], trials: Int = 1200) -> SimResult {
        let count = max(inputs.count, 1)
        let distance = Int(race.course.filter(\.isNumber))
        let models = inputs.map { Model(input: $0, base: basePosition($0)) }
        let coverage = Double(inputs.filter(\.hasHistory).count) / Double(count) * 100
        let pressure = models.reduce(0.0) { $0 + $1.input.escape + $1.input.front * 0.55 } / Double(count)
        var paceIndex = pressure + (distance.map { $0 <= 1400 ? 0.035 : ($0 >= 2400 ? -0.025 : 0) } ?? 0)
        if count >= 16 { paceIndex += 0.015 }
        paceIndex = clamp(paceIndex, 0.05, 0.55)
        let pace = paceIndex >= 0.30 ? "ハイ寄り" : (paceIndex <= 0.19 ? "スロー寄り" : "平均寄り")

        let phaseDefs: [(String, Double)] = [("序盤", 0.10), ("向正面", 0.38), ("4角", 0.68), ("直線・ゴール", 1.0)]
        let phases = phaseDefs.map { title, progress in
            let order = models.sorted {
                phaseScore($0, progress, paceIndex, distance, race.raceID) < phaseScore($1, progress, paceIndex, distance, race.raceID)
            }.map { $0.input.number }
            return (title, order)
        }

        var wins: [Int: Int] = [:], top3: [Int: Int] = [:]
        var rankSum: [Int: Double] = [:]
        var rng = RNG(state: seed(race.raceID + "|sim-v0.1"))
        let n = max(200, trials)
        for _ in 0..<n {
            let runPace = clamp(paceIndex + rng.centered() * 0.075, 0.05, 0.55)
            let scored = models.map { model in
                (model, finishScore(model, runPace, distance) + rng.centered() * uncertainty(model.input))
            }.sorted {
                abs($0.1 - $1.1) > 0.000001 ? $0.1 < $1.1 : $0.0.input.number < $1.0.input.number
            }
            for (i, row) in scored.enumerated() {
                let no = row.0.input.number
                if i == 0 { wins[no, default: 0] += 1 }
                if i < 3 { top3[no, default: 0] += 1 }
                rankSum[no, default: 0] += Double(i + 1)
            }
        }

        let stats = models.map { model in
            let no = model.input.number
            return SimStat(
                number: no,
                name: model.input.name,
                winPct: Double(wins[no, default: 0]) / Double(n) * 100,
                top3Pct: Double(top3[no, default: 0]) / Double(n) * 100,
                meanRank: rankSum[no, default: 0] / Double(n),
                samples: model.input.samples,
                hasHistory: model.input.hasHistory
            )
        }.sorted {
            $0.top3Pct != $1.top3Pct ? $0.top3Pct > $1.top3Pct : $0.meanRank < $1.meanRank
        }

        func scenario(_ title: String, _ note: String, _ p: Double) -> (String, String, [Int]) {
            let order = models.sorted { finishScore($0, p, distance) < finishScore($1, p, distance) }.prefix(3).map { $0.input.number }
            return (title, note, Array(order))
        }
        let scenarios = [
            scenario("本線", "現在の脚質構成", paceIndex),
            scenario("前残り", "想定より流れが落ち着く", clamp(paceIndex - 0.10, 0.05, 0.55)),
            scenario("差し台頭", "想定より前半が速い", clamp(paceIndex + 0.10, 0.05, 0.55))
        ]

        let frontCount = inputs.filter { $0.escape + $0.front >= 0.60 }.count
        let closeCount = inputs.filter { $0.stalk + $0.close >= 0.70 }.count
        let confidence = coverage >= 80 ? "中〜高" : (coverage >= 60 ? "中" : "低")
        return SimResult(
            pace: pace,
            reason: "前寄り\(frontCount)頭、差し・追込寄り\(closeCount)頭。距離と頭数を加味。",
            coverage: coverage,
            confidence: confidence,
            phases: phases,
            scenarios: scenarios,
            stats: stats
        )
    }

    private static func basePosition(_ x: SimInput) -> Double {
        let style = x.escape * 0.08 + x.front * 0.28 + x.stalk * 0.58 + x.close * 0.84
        guard let observed = x.meanPosition else { return style }
        let weight = x.samples >= 5 ? 0.70 : (x.samples >= 3 ? 0.62 : (x.samples == 2 ? 0.50 : 0.35))
        return observed * weight + style * (1 - weight)
    }

    private static func phaseScore(_ m: Model, _ progress: Double, _ pace: Double, _ distance: Int?, _ raceID: String) -> Double {
        let x = m.input, front = x.escape + x.front * 0.65, close = x.close + x.stalk * 0.45
        let centered = pace - 0.235
        var score = m.base + progress * centered * front * 0.34 - progress * centered * close * 0.26
        if let d = distance {
            if d <= 1400 { score -= progress * front * 0.035; score += progress * x.close * 0.020 }
            if d >= 2200 { score += progress * x.escape * 0.030; score -= progress * x.close * 0.025 }
        }
        let jitter = Double(seed("\(raceID)|\(x.number)|\(Int(progress * 100))") % 10_001) / 10_000 * 2 - 1
        return score + jitter * (x.hasHistory ? 0.010 : 0.025)
    }

    private static func finishScore(_ m: Model, _ pace: Double, _ distance: Int?) -> Double {
        let x = m.input, front = x.escape + x.front * 0.60, close = x.close + x.stalk * 0.45
        let centered = pace - 0.235
        var score = m.base + centered * front * 0.42 - centered * close * 0.34
        if let d = distance {
            if d <= 1400 { score -= front * 0.045; score += x.close * 0.025 }
            if d >= 2200 { score += x.escape * 0.040; score -= x.close * 0.035 }
        }
        return score
    }

    private static func uncertainty(_ x: SimInput) -> Double {
        if !x.hasHistory { return 0.145 }
        return x.samples >= 5 ? 0.062 : (x.samples >= 3 ? 0.080 : (x.samples == 2 ? 0.095 : 0.115))
    }

    private static func seed(_ text: String) -> UInt64 {
        var h: UInt64 = 1469598103934665603
        for b in text.utf8 { h ^= UInt64(b); h &*= 1099511628211 }
        return h == 0 ? 1 : h
    }

    private static func clamp(_ x: Double, _ lo: Double, _ hi: Double) -> Double { min(hi, max(lo, x)) }
}

// MARK: - Simulator UI

private struct KeibaAIRaceSimulatorScreen: View {
    @StateObject private var store = KeibaAIRaceSimulatorStore()
    @State private var selectedID: String?

    private var selected: Race? {
        store.races.first { $0.raceID == selectedID } ?? store.races.first
    }

    var body: some View {
        NavigationSplitView {
            List {
                Section {
                    Text("予測AIと独立: AI勝率・人気・オッズはシミュレーション入力に使いません。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(store.message).font(.caption2).foregroundStyle(.secondary)
                }
                Section("レース") {
                    ForEach(store.races) { race in
                        Button {
                            selectedID = race.raceID
                        } label: {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text("\(race.venue) \(race.raceNumber)R").font(.subheadline.bold())
                                    Text(race.raceName).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                }
                                Spacer()
                                if selected?.raceID == race.raceID { Image(systemName: "checkmark.circle.fill").foregroundStyle(.blue) }
                            }
                        }.buttonStyle(.plain)
                    }
                }
            }
            .navigationTitle("展開シミュ")
            .refreshable { await store.load(force: true) }
        } detail: {
            if let race = selected {
                KeibaAIRaceSimulatorDetail(race: race, research: store.research(for: race), generatedAt: store.generatedAtJST)
            } else if store.isLoading {
                ProgressView("読み込み中…")
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

private struct KeibaAIRaceSimulatorDetail: View {
    let race: Race
    let research: SimResearchRace?
    let generatedAt: String?

    private var inputs: [SimInput] {
        race.horses.map { horse in
            let h = research?.horses.first { $0.number == horse.number && $0.name == horse.name }
            let v = styleVector(h)
            let n = max(h?.styleTendency?.sampleRaces ?? 0, h?.initialPositionHistory?.sampleRaces ?? 0)
            return SimInput(number: horse.number, name: horse.name, escape: v.0, front: v.1, stalk: v.2, close: v.3,
                            samples: n, meanPosition: h?.initialPositionHistory?.meanRelativePosition,
                            hasHistory: h?.styleTendency != nil || h?.initialPositionHistory != nil)
        }
    }

    private var sim: SimResult { RaceSimulator.run(race: race, inputs: inputs) }
    private var names: [Int: String] { Dictionary(uniqueKeysWithValues: race.horses.map { ($0.number, $0.name) }) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                card {
                    Text("\(race.venue) \(race.raceNumber)R ・ \(race.startTime)").font(.caption.bold()).foregroundStyle(.blue)
                    Text(race.raceName).font(.title2.bold())
                    Text(race.course).foregroundStyle(.secondary)
                    Divider()
                    Label("独立入力", systemImage: "lock.shield").font(.headline)
                    Text("使用: 過去脚質・初角位置・距離・頭数 / 未使用: AI勝率・市場確率・人気・単勝オッズ")
                        .font(.subheadline).foregroundStyle(.secondary)
                    if let generatedAt { Text("研究データ更新: \(generatedAt)").font(.caption2).foregroundStyle(.tertiary) }
                }

                card {
                    HStack { Text("展開シミュレーションの答え").font(.title3.bold()); Spacer(); Text(sim.pace).bold().foregroundStyle(.orange) }
                    Text(sim.reason).font(.subheadline).foregroundStyle(.secondary)
                    Text(String(format: "履歴カバー %.0f%% ・ 参考度 %@ ・ 1,200試行", sim.coverage, sim.confidence)).font(.caption)
                    ForEach(Array(sim.stats.prefix(8).enumerated()), id: \.offset) { i, s in
                        HStack {
                            Text("\(i + 1). #\(s.number) \(s.name)").font(.subheadline.weight(.semibold)).lineLimit(1)
                            Spacer()
                            Text(String(format: "3着内 %.1f%% / 1着 %.1f%%", s.top3Pct, s.winPct)).font(.caption.monospacedDigit())
                        }
                        if i < min(7, sim.stats.count - 1) { Divider() }
                    }
                    Text("※ 表示率はシミュレーション内の割合で、校正済みの実勝率ではありません。").font(.caption2).foregroundStyle(.secondary)
                }

                card {
                    Text("レース中の位置変化").font(.title3.bold())
                    ForEach(Array(sim.phases.enumerated()), id: \.offset) { _, phase in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(phase.0).font(.headline)
                            Text(phase.1.prefix(8).map { "#\($0) \(names[$0] ?? "")" }.joined(separator: " → "))
                                .font(.caption).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }

                card {
                    Text("展開がズレた場合").font(.title3.bold())
                    ForEach(Array(sim.scenarios.enumerated()), id: \.offset) { _, row in
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(row.0) — \(row.1)").font(.subheadline.bold())
                            Text(row.2.map { "#\($0) \(names[$0] ?? "")" }.joined(separator: " → ")).font(.caption)
                        }
                    }
                }

                integrationCard

                card {
                    Label("Simulator v0.1", systemImage: "flask").font(.headline)
                    Text("学習済み展開AIではなく、過去6走の脚質・初角位置を使うヒューリスティック・モンテカルロ版です。まず独立性を固定し、今後は保存した予測と実着順で重みを検証・学習化します。")
                        .font(.subheadline)
                }
            }.padding(18).frame(maxWidth: 900, alignment: .leading)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("\(race.venue) \(race.raceNumber)R")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var integrationCard: some View {
        let ai = Array(race.horses.sorted { $0.aiProbability > $1.aiProbability }.prefix(3))
        let flow = Array(sim.stats.prefix(3))
        let aiSet = Set(ai.map(\.number))
        let overlap = flow.filter { aiSet.contains($0.number) }
        let label = overlap.count == 3 ? "高整合" : (overlap.count == 2 ? "整合" : (overlap.count == 1 ? "一部整合" : "乖離大"))
        return card {
            HStack { Text("最終統合").font(.title3.bold()); Spacer(); Text(label).font(.caption.bold()).foregroundStyle(overlap.count >= 2 ? .green : .orange) }
            Text("ここで初めて予測AIと展開シミュを照合します。シミュレーター側の計算にはAI勝率を入れていません。")
                .font(.subheadline).foregroundStyle(.secondary)
            HStack(alignment: .top) {
                VStack(alignment: .leading) {
                    Text("予測AI").font(.caption.bold()).foregroundStyle(.secondary)
                    ForEach(ai) { h in Text("#\(h.number) \(h.name)  \(String(format: "%.1f%%", h.aiProbability))").font(.caption) }
                }.frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .leading) {
                    Text("展開シミュ").font(.caption.bold()).foregroundStyle(.secondary)
                    ForEach(flow) { s in Text("#\(s.number) \(s.name)  3着内\(String(format: "%.1f%%", s.top3Pct))").font(.caption) }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
            Text(overlap.isEmpty ? "共通上位なし。AI本命に展開上の逆風がないか確認。" : "共通上位: " + overlap.map { "#\($0.number) \($0.name)" }.joined(separator: "、"))
                .font(.subheadline.weight(.semibold))
            Text("統合層はAI勝率を書き換えず、現段階では研究用の補助判定です。").font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) { content() }
            .padding(18)
            .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }

    private func styleVector(_ h: SimResearchHorse?) -> (Double, Double, Double, Double) {
        if let d = h?.styleTendency?.distributionPct {
            let a = max(0, d["逃げ"] ?? 0) / 100, b = max(0, d["先行"] ?? 0) / 100
            let c = max(0, d["差し"] ?? 0) / 100, e = max(0, d["追込"] ?? 0) / 100
            let sum = a + b + c + e
            if sum > 0 { return (a / sum, b / sum, c / sum, e / sum) }
        }
        if let p = h?.initialPositionHistory?.meanRelativePosition {
            if p <= 0.18 { return (0.45, 0.40, 0.10, 0.05) }
            if p <= 0.38 { return (0.10, 0.55, 0.30, 0.05) }
            if p <= 0.68 { return (0.02, 0.18, 0.60, 0.20) }
            return (0.01, 0.04, 0.30, 0.65)
        }
        return (0.05, 0.25, 0.45, 0.25)
    }
}
