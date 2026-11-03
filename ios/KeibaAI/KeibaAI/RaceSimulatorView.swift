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
private struct SimBatchSettings: Decodable { let runsPerRace: Int; let baseSeed: Int; let dtSeconds: Double }
private struct SimBatchSummary: Decodable { let inputRaces: Int; let simulatedRaces: Int; let skippedRaces: Int; let errorRaces: Int }
private struct SimRace: Decodable, Identifiable {
    var id: String { raceID }
    let raceID: String; let raceName: String?; let racecourse: String?; let track: String?; let distanceMeters: Double
    let pace: String; let paceSource: String; let raceDate: String?; let raceNumber: Int?; let startTime: String?; let venue: String?
    let inputQuality: SimInputQuality; let independence: SimIndependence; let settings: SimEngineSettings
    let horses: [SimHorse]; let comparisonWithPredictionAI: SimComparison; let replay: SimReplay
}
private struct SimInputQuality: Decodable { let formationStatus: String?; let formationCoveragePct: Double?; let paceForecastStatus: String?; let styleKnownHorses: Int; let styleTotalHorses: Int; let styleCoveragePct: Double }
private struct SimIndependence: Decodable { let usesPredictionAIForMotion: Bool; let usesMarketProbabilityForMotion: Bool; let usesOddsForMotion: Bool; let strengthSource: String }
private struct SimEngineSettings: Decodable { let runs: Int; let seed: Int; let dtSeconds: Double }
private struct SimHorse: Decodable, Identifiable {
    var id: Int { horseNumber }
    let horseNumber: Int; let horseName: String; let style: String; let independentStrength: Double
    let simulationWinProbability: Double; let simulationTop2Probability: Double; let simulationTop3Probability: Double; let expectedFinish: Double
    let earlyLeadProbability: Double; let top3At75Probability: Double; let averageFinishTimeSeconds: Double; let averageBlockedSeconds: Double; let averageLaneChanges: Double
    let rankBands: [SimRankBand]
}
private struct SimRankBand: Decodable, Identifiable { var id: Double { progress }; let progress: Double; let p20Rank: Double; let medianRank: Double; let p80Rank: Double }
private struct SimComparison: Decodable { let available: Bool; let note: String?; let reason: String?; let horses: [SimComparisonHorse]? }
private struct SimComparisonHorse: Decodable, Identifiable { var id: Int { horseNumber }; let horseNumber: Int; let horseName: String; let predictionAIWinProbabilityNormalized: Double; let simulationWinProbability: Double; let delta: Double; let label: String }

// v0.2 is frame-native: the iPhone renders the simulator's actual representative
// position/lane/speed/rank/gap states instead of inventing motion from rank bands.
private struct SimReplay: Decodable {
    let kind: String; let seed: Int; let dtSeconds: Double; let durationSeconds: Double; let frames: [SimFrame]; let note: String?
}
private struct SimFrame: Decodable, Identifiable { var id: Double { t }; let t: Double; let horses: [SimFrameHorse] }
private struct SimFrameHorse: Decodable, Identifiable {
    var id: Int { horseNumber }
    let horseNumber: Int; let progress: Double; let positionMeters: Double; let lane: Double; let speedMps: Double; let rank: Int; let gapMeters: Double; let blocked: Bool
}

@MainActor
private final class KeibaAIRaceSimulatorStore: ObservableObject {
    @Published private(set) var races: [Race] = []
    @Published private(set) var simulations: [String: SimRace] = [:]
    @Published private(set) var batch: SimBatch?
    @Published private(set) var sourceMessage = "未読み込み"
    @Published private(set) var isLoading = false
    private let rawMain = "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/"

    func simulation(for raceID: String) -> SimRace? { simulations[raceID] }
    func load(force: Bool = false) async {
        if isLoading || (!force && !races.isEmpty && batch != nil) { return }
        isLoading = true; defer { isLoading = false }
        do {
            let value: [Race] = try await fetch(rawMain + "RaceData.json")
            races = value.sorted {
                if $0.raceDate != $1.raceDate { return ($0.raceDate ?? "") > ($1.raceDate ?? "") }
                if $0.venue != $1.venue { return venueOrder($0.venue) < venueOrder($1.venue) }
                return $0.raceNumber < $1.raceNumber
            }
            let value2: SimBatch = try await fetch(rawMain + "RaceSimulationData.json")
            guard value2.schemaVersion == "race-simulator-v0.2-batch" else { throw URLError(.cannotParseResponse) }
            batch = value2
            simulations = Dictionary(uniqueKeysWithValues: value2.races.map { ($0.raceID, $0) })
            sourceMessage = "RaceSimulator v0.2・フレーム取得済"
        } catch {
            simulations = [:]; batch = nil
            sourceMessage = races.isEmpty ? "RaceData取得失敗" : "RaceSimulator v0.2取得待ち"
        }
    }
    private func fetch<T: Decodable>(_ raw: String) async throws -> T {
        guard var c = URLComponents(string: raw) else { throw URLError(.badURL) }
        c.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
        guard let url = c.url else { throw URLError(.badURL) }
        var req = URLRequest(url: url); req.cachePolicy = .reloadIgnoringLocalCacheData; req.timeoutInterval = 20
        req.setValue("KeibaAI-RaceSimulator/0.2", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else { throw URLError(.badServerResponse) }
        return try JSONDecoder().decode(T.self, from: data)
    }
    private func venueOrder(_ venue: String) -> Int { ["札幌","函館","福島","新潟","東京","中山","中京","京都","阪神","小倉"].firstIndex(of: venue) ?? 99 }
}

struct KeibaAIRaceSimulatorScreen: View {
    @StateObject private var store = KeibaAIRaceSimulatorStore()
    var body: some View {
        NavigationStack {
            List {
                Section {
                    Label("RaceSimulator v0.2", systemImage: "waveform.path.ecg") .font(.headline)
                    Text("予測AI・オッズ・人気を走行計算に入れず、独立能力・脚質・位置取りから展開を計算します。") .font(.caption).foregroundStyle(.secondary)
                    Text(store.sourceMessage).font(.caption2).foregroundStyle(.secondary)
                }
                Section("レース") {
                    ForEach(store.races) { race in
                        NavigationLink {
                            KeibaAIRaceSimulatorDestination(race: race, store: store)
                        } label: { RaceSimRow(race: race, ready: store.simulation(for: race.raceID) != nil) }
                    }
                }
            }
            .navigationTitle("展開シミュ")
            .refreshable { await store.load(force: true) }
        }
        .task { await store.load() }
    }
}

private struct RaceSimRow: View {
    let race: Race; let ready: Bool
    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(race.venue) \(race.raceNumber)R").font(.subheadline.bold())
                Text(race.raceName).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(); Image(systemName: ready ? "checkmark.circle.fill" : "clock").foregroundStyle(ready ? .green : .secondary)
        }
    }
}
private struct KeibaAIRaceSimulatorDestination: View {
    let race: Race; @ObservedObject var store: KeibaAIRaceSimulatorStore
    var body: some View {
        Group {
            if let sim = store.simulation(for: race.raceID) { RaceSimDetail(race: race, sim: sim, batch: store.batch) }
            else if store.isLoading { ProgressView("v0.2を取得中…") }
            else { ContentUnavailableView("v0.2生成待ち", systemImage: "clock", description: Text("\(race.venue) \(race.raceNumber)R のフレームデータはまだありません。")) }
        }
        .navigationTitle("\(race.venue) \(race.raceNumber)R").navigationBarTitleDisplayMode(.inline)
    }
}

private struct RaceSimDetail: View {
    let race: Race; let sim: SimRace; let batch: SimBatch?
    @State private var elapsed = 0.0
    @State private var playing = false
    @State private var speed = 1.0
    @State private var selectedHorse: Int?
    private let timer = Timer.publish(every: 1.0/30.0, on: .main, in: .common).autoconnect()

    private var duration: Double { max(sim.replay.durationSeconds, sim.replay.frames.last?.t ?? 1, 1) }
    private var progress: Double { min(1, max(0, elapsed / duration)) }
    private var framePair: (SimFrame, SimFrame, Double)? {
        let f = sim.replay.frames
        guard let first = f.first else { return nil }
        if elapsed <= first.t { return (first, first, 0) }
        guard let last = f.last else { return nil }
        if elapsed >= last.t { return (last, last, 0) }
        var lo = 0, hi = f.count - 1
        while hi - lo > 1 { let m = (lo + hi) / 2; if f[m].t <= elapsed { lo = m } else { hi = m } }
        let width = max(f[hi].t - f[lo].t, 0.0001)
        return (f[lo], f[hi], (elapsed - f[lo].t) / width)
    }
    private var currentStates: [SimFrameHorse] {
        guard let pair = framePair else { return [] }
        let b = Dictionary(uniqueKeysWithValues: pair.1.horses.map { ($0.horseNumber, $0) })
        return pair.0.horses.map { a in
            guard let z = b[a.horseNumber] else { return a }
            let q = pair.2, lerp: (Double,Double)->Double = { x,y in x + (y-x)*q }
            return SimFrameHorse(horseNumber:a.horseNumber, progress:lerp(a.progress,z.progress), positionMeters:lerp(a.positionMeters,z.positionMeters), lane:lerp(a.lane,z.lane), speedMps:lerp(a.speedMps,z.speedMps), rank:q < 0.5 ? a.rank : z.rank, gapMeters:lerp(a.gapMeters,z.gapMeters), blocked:q < 0.5 ? a.blocked : z.blocked)
        }.sorted { $0.rank < $1.rank }
    }
    private var simRanked: [SimHorse] { sim.horses.sorted { $0.simulationWinProbability == $1.simulationWinProbability ? $0.expectedFinish < $1.expectedFinish : $0.simulationWinProbability > $1.simulationWinProbability } }
    private func horseName(_ n: Int) -> String { sim.horses.first { $0.horseNumber == n }?.horseName ?? "#\(n)" }
    private var phase: String { progress < .05 ? "スタート" : progress < .25 ? "序盤" : progress < .60 ? "中盤" : progress < .82 ? "3〜4角" : progress < .995 ? "直線" : "ゴール" }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                card {
                    HStack { VStack(alignment:.leading) { Text("\(race.venue) \(race.raceNumber)R").font(.caption.bold()).foregroundStyle(.blue); Text(race.raceName).font(.title2.bold()); Text(sim.track ?? race.course).foregroundStyle(.secondary) }; Spacer(); Text("v0.2").font(.caption.bold()).padding(7).background(.blue.opacity(.1),in:Capsule()) }
                    Text("フレーム \(sim.replay.frames.count)枚 / \(String(format:"%.1f",sim.replay.dtSeconds))秒刻み / \(sim.replay.kind)").font(.caption).foregroundStyle(.secondary)
                }
                replayCard
                liveOrderCard
                resultCard
                qualityCard
            }.padding(16)
        }
        .background(Color(uiColor:.systemGroupedBackground))
        .onReceive(timer) { _ in
            guard playing else { return }
            elapsed = min(duration, elapsed + (1.0/30.0)*speed)
            if elapsed >= duration { playing = false }
        }
    }

    private var replayCard: some View {
        card {
            HStack { Label("v0.2 フレーム再生",systemImage:"play.circle.fill").font(.title3.bold()); Spacer(); Text(phase).font(.subheadline.bold()).foregroundStyle(.orange) }
            Text("丸いコースは表示用の模式図です。馬の前後位置・レーン・速度・順位・詰まりはv0.2が出力した実フレーム値を使っています。実GPSではありません。") .font(.caption).foregroundStyle(.secondary)
            GeometryReader { proxy in
                ZStack {
                    RoundedRectangle(cornerRadius:22).fill(Color(uiColor:.secondarySystemBackground))
                    Ellipse().stroke(Color.brown.opacity(.25),lineWidth:48).padding(28)
                    Ellipse().stroke(Color.green.opacity(.68),lineWidth:32).padding(28)
                    Ellipse().stroke(Color.white.opacity(.75),style:StrokeStyle(lineWidth:1.5,dash:[7,6])).padding(28)
                    ForEach(currentStates) { state in marker(state, size: proxy.size) }
                }
            }.frame(height:320)
            HStack {
                Button { if elapsed >= duration { elapsed = 0 }; playing.toggle() } label: { Label(playing ? "一時停止":"再生",systemImage:playing ? "pause.fill":"play.fill") }.buttonStyle(.borderedProminent)
                Button { playing=false; elapsed=0 } label: { Label("最初へ",systemImage:"backward.end.fill") }.buttonStyle(.bordered)
                Spacer(); Text(String(format:"%.1f / %.1f秒",elapsed,duration)).font(.caption.monospacedDigit())
            }
            Slider(value:$elapsed,in:0...duration,onEditingChanged:{ if $0 { playing=false } }).accessibilityLabel("レース再生位置")
            Picker("速度",selection:$speed) { Text("0.5×").tag(0.5);Text("1×").tag(1.0);Text("2×").tag(2.0);Text("4×").tag(4.0) }.pickerStyle(.segmented)
            if let n=selectedHorse, let s=currentStates.first(where:{$0.horseNumber==n}) {
                HStack { Text("#\(n) \(horseName(n))").font(.subheadline.bold()); Spacer(); Text("\(s.rank)位"); Text(String(format:"%.1fm/s",s.speedMps)); Text(String(format:"差 %.1fm",s.gapMeters)); if s.blocked { Label("詰まり",systemImage:"exclamationmark.triangle.fill").foregroundStyle(.orange) } }.font(.caption)
            }
        }
    }

    @ViewBuilder private func marker(_ s: SimFrameHorse, size: CGSize) -> some View {
        let angle = -Double.pi/2 + s.progress*Double.pi*2
        let laneOffset = CGFloat((s.lane-2.5)*4.0)
        let rx=max(24,size.width/2-55-laneOffset), ry=max(24,size.height/2-55-laneOffset*.45)
        let p=CGPoint(x:size.width/2+cos(angle)*rx,y:size.height/2+sin(angle)*ry)
        Button { selectedHorse=s.horseNumber } label: {
            VStack(spacing:1) {
                ZStack { Circle().fill(color(s.horseNumber)).frame(width:selectedHorse==s.horseNumber ? 34:29,height:selectedHorse==s.horseNumber ? 34:29); Text("\(s.horseNumber)").font(.caption.bold()).foregroundStyle(.white) }
                Text("\(s.rank)位").font(.system(size:9,weight:.bold)).padding(.horizontal,4).background(.ultraThinMaterial,in:Capsule())
                if s.blocked { Image(systemName:"exclamationmark.triangle.fill").font(.system(size:9)).foregroundStyle(.orange) }
            }
        }.buttonStyle(.plain).position(p).animation(.linear(duration:.06),value:elapsed)
    }

    private var liveOrderCard: some View {
        card {
            HStack { Text("現在位置").font(.title3.bold()); Spacer(); Text(paceLabel(sim.pace)).font(.caption.bold()).foregroundStyle(.orange) }
            ForEach(currentStates.prefix(8)) { s in
                HStack { Text("\(s.rank)位  #\(s.horseNumber) \(horseName(s.horseNumber))").font(.subheadline.weight(.semibold)).lineLimit(1); Spacer(); Text(String(format:"%.1fm/s",s.speedMps)).font(.caption.monospacedDigit()); Text(String(format:"+%.1fm",s.gapMeters)).font(.caption.monospacedDigit()).foregroundStyle(.secondary); if s.blocked { Image(systemName:"exclamationmark.triangle.fill").foregroundStyle(.orange) } }
            }
        }
    }
    private var resultCard: some View {
        card {
            Text("Monte Carlo 展開評価").font(.title3.bold())
            Text("代表フレーム1本の着順ではなく、\(sim.settings.runs)回の試行結果です。") .font(.caption).foregroundStyle(.secondary)
            ForEach(Array(simRanked.prefix(8).enumerated()),id:\.element.id) { i,h in
                HStack { Text("\(i+1). #\(h.horseNumber) \(h.horseName)").font(.subheadline.weight(.semibold)).lineLimit(1); Text(h.style).font(.caption2).foregroundStyle(.secondary); Spacer(); VStack(alignment:.trailing) { Text(String(format:"1着 %.1f%% / 3着内 %.1f%%",h.simulationWinProbability*100,h.simulationTop3Probability*100)).font(.caption.monospacedDigit()); Text(String(format:"詰まり %.1f秒 / 進路変更 %.1f回",h.averageBlockedSeconds,h.averageLaneChanges)).font(.caption2).foregroundStyle(.secondary) } }
            }
        }
    }
    private var qualityCard: some View {
        card {
            Label("v0.2 品質・独立性",systemImage:"checkmark.shield.fill").font(.headline)
            Text(String(format:"脚質カバー %.1f%%（%d/%d頭）",sim.inputQuality.styleCoveragePct,sim.inputQuality.styleKnownHorses,sim.inputQuality.styleTotalHorses)).font(.subheadline)
            Text("ペース: \(paceLabel(sim.pace)) / \(sim.paceSource)").font(.caption).foregroundStyle(.secondary)
            Text("独立能力: \(sim.independence.strengthSource)").font(.caption).foregroundStyle(.secondary)
            Label("予測AI・市場確率・オッズは走行計算に不使用",systemImage:"lock.shield").font(.caption.weight(.semibold)).foregroundStyle(.green)
            if let g=batch?.generatedAt { Text("生成: \(g)").font(.caption2).foregroundStyle(.secondary) }
        }
    }
    @ViewBuilder private func card<Content:View>(@ViewBuilder _ content:()->Content)->some View { VStack(alignment:.leading,spacing:12){content()}.padding(18).background(Color(uiColor:.systemBackground),in:RoundedRectangle(cornerRadius:20)) }
    private func color(_ n:Int)->Color { Color(hue:Double((n*47)%360)/360,saturation:.72,brightness:.84) }
    private func paceLabel(_ p:String)->String { p=="FAST" ? "ハイ" : p=="SLOW" ? "スロー" : "平均" }
}
