import SwiftUI
import Foundation
import Combine

// ============================================================
// KeibaAI - New Design / Wide Practical + Horse DB
// ============================================================
// ファイル名: KeibaAI_NewDesignView.swift
// このファイルは新デザイン画面です。
// 共通モデルなどを定義するContentView.swiftと、別々のファイルに置きます。
//
// ContentView.swift側の以下を再利用します：
// - Horse / Race / RaceSnapshot
// - RaceStore（GitHub RaceData.json + 履歴取得）
// - SettingsView
//
// ライブ予測 RaceData に加え、SignalData / ResultData / PerformanceData を読み込みます。
// App の起点だけ ContentView() → KeibaAINewDesignView() に変えると
// 新デザインを試せます。
//
// 注意：
// 「荒れ度」「勝負度」「大穴候補」は正式 SignalData を表示します。
// 2024年で区切りを固定し、別に残した2025年データで検証した研究結果を採用します。
// ワイドは SignalData v2 の発走前実オッズ・AI的中率・保守的期待値・実戦優先度に対応します。
// 過去レースは WideValidation.json も読み込み、発走前評価と確定払戻を比較表示します。
// Kelly値は候補順位づけ専用で、購入金額の自動配分には使用しません。
// 馬名タップで HorseIndex / HorseProfiles の馬DBをオンデマンド表示します。
// AppResearchData は大穴v2比較と過去走の脚質傾向を表示する研究用データです。
// 表示中の予測と馬・オッズ・AI勝率が一致した場合だけ接続します。
// ============================================================

private enum KeibaAIMainTab: String, CaseIterable, Identifiable {
    case today = "今日"
    case focus = "注目レース"
    case longshot = "大穴"
    case ticket = "馬券プラン"
    case performance = "成績"
    case guide = "凡例"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .today: return "calendar"
        case .focus: return "scope"
        case .longshot: return "sparkles"
        case .ticket: return "ticket"
        case .performance: return "chart.line.uptrend.xyaxis"
        case .guide: return "book.closed.fill"
        }
    }
}

private enum KeibaAIDetailTab: String, CaseIterable, Identifiable {
    case prediction = "AI予測"
    case longshot = "大穴比較"
    case result = "結果比較"
    case wide = "ワイド評価"
    case pace = "展開・脚質"
    case course = "コース分析"
    case history = "過去データ"

    var id: String { rawValue }
}


private enum KeibaAISortOption: String, CaseIterable, Identifiable {
    case startTime = "発走時刻"
    case raceNumber = "R順"
    case venue = "競馬場"
    case battle = "勝負度"
    case chaos = "荒れ度"
    case longshot = "大穴候補"
    case predictedFirst = "AI予測済優先"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .startTime: return "clock"
        case .raceNumber: return "number"
        case .venue: return "building.columns"
        case .battle: return "target"
        case .chaos: return "waveform.path.ecg"
        case .longshot: return "sparkles"
        case .predictedFirst: return "checkmark.circle"
        }
    }
}

private enum KeibaAIRaceStatus: String {
    case predicted = "AI予測済"
    case waiting = "未予測"
    case finished = "発走済"
    case excluded = "対象外"

    var tint: Color {
        switch self {
        case .predicted: return .green
        case .waiting: return .blue
        case .finished: return .secondary
        case .excluded: return .secondary
        }
    }

    var icon: String {
        switch self {
        case .predicted: return "checkmark.circle.fill"
        case .waiting: return "clock.fill"
        case .finished: return "flag.checkered"
        case .excluded: return "nosign"
        }
    }
}


private struct KeibaAIResultHorse: Identifiable, Hashable, Codable {
    var id: Int { number }

    let number: Int
    let name: String
    let finishPosition: Int?
    let resultStatus: String?
    let finalOdds: Double?
    let popularity: Int?
}

private struct KeibaAIResultRace: Identifiable, Hashable, Codable {
    var id: String { raceID }

    let raceID: String
    let raceDate: String?
    let venue: String?
    let raceNumber: Int?
    let fieldSize: Int?
    let horses: [KeibaAIResultHorse]
}

@MainActor
private final class KeibaAIResultStore: ObservableObject {
    @Published private(set) var racesByID: [String: KeibaAIResultRace] = [:]
    @Published var sourceMessage = "結果 未読み込み"
    @Published var isLoading = false

    private let owner = "colynky-wq"
    private let repo = "jra-auto-prediction"
    private let branch = "main"
    private var loadedDate8: String?

    func result(for raceID: String) -> KeibaAIResultRace? {
        racesByID[raceID]
    }

    func load(date8: String, force: Bool = false) async {
        if isLoading { return }
        if !force, loadedDate8 == date8 { return }

        isLoading = true
        defer { isLoading = false }

        let candidates: [(branch: String, path: String, label: String)] = [
            ("main", "ResultData.json", "本番結果"),
            ("main", "reports/\(date8)/ResultData.json", "本番日別結果"),
            ("research/wide-budget-model-v1", "ResultData.json", "研究版結果")
        ]

        for candidate in candidates {
            do {
                let path = candidate.path
                guard let encoded = path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
                      var components = URLComponents(
                        string: "https://raw.githubusercontent.com/\(owner)/\(repo)/\(candidate.branch)/\(encoded)"
                      ) else { continue }

                components.queryItems = [
                    URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))
                ]
                guard let url = components.url else { continue }

                var request = URLRequest(url: url)
                request.cachePolicy = .reloadIgnoringLocalCacheData
                request.timeoutInterval = 10
                request.setValue("KeibaAI-iPad/2.1", forHTTPHeaderField: "User-Agent")

                let (data, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse,
                      (200...299).contains(http.statusCode) else { continue }

                let decoded = try JSONDecoder().decode([KeibaAIResultRace].self, from: data)
                let filtered = decoded.filter { race in
                    guard let d = race.raceDate, !d.isEmpty else { return true }
                    return d.replacingOccurrences(of: "-", with: "") == date8
                }

                // A root ResultData.json from another race day must not stop
                // historical/day-specific lookup. Continue to the next source.
                if filtered.isEmpty { continue }

                racesByID = Dictionary(uniqueKeysWithValues: filtered.map { ($0.raceID, $0) })
                sourceMessage = "結果データ取得済"
                loadedDate8 = date8
                return
            } catch {
                continue
            }
        }

        racesByID = [:]
        sourceMessage = "結果待ち"
        loadedDate8 = date8
    }
}



// ============================================================
// MARK: - Horse profile database
// ============================================================

private struct KeibaAIHorseIndexPayload: Codable {
    let schemaVersion: Int
    let generatedAtJST: String?
    let horseCount: Int
    let horses: [KeibaAIHorseIndexEntry]
}

private struct KeibaAIHorseIndexEntry: Identifiable, Codable, Hashable, KeibaAIHorseIdentityCandidate {
    var id: String { profileID }

    let profileID: String
    let name: String
    let horseKey: String?
    let birthYear: Int?
    let currentAge: Int?
    let latestDate: String?
    let starts: Int?
    let wins: Int?
    let top3RatePct: Double?
    let bestVenue: String?
    let bestVenueScore: Double?
    let shard: String
}

private struct KeibaAIHorseCareer: Codable, Hashable {
    let starts: Int
    let wins: Int
    let top3: Int
    let winRatePct: Double?
    let top3RatePct: Double?
    let averageFinish: Double?
}

private struct KeibaAIHorseAffinityStat: Identifiable, Codable, Hashable {
    var id: String {
        [venue, surface, distanceBand, String(starts)].compactMap { $0 }.joined(separator: "|")
    }

    let venue: String?
    let surface: String?
    let distanceBand: String?
    let starts: Int
    let wins: Int
    let top3: Int
    let winRatePct: Double?
    let top3RatePct: Double?
    let averageFinish: Double?
    let performanceScore: Double?
    let smoothedPerformanceScore: Double?
    let confidence: String?
}

private struct KeibaAIHorseRecentRace: Identifiable, Codable, Hashable {
    var id: String { "\(raceID)-\(horseNumber ?? 0)-\(date ?? "")" }

    let date: String?
    let raceID: String
    let venue: String?
    let raceNumber: Int?
    let surface: String?
    let distance: Int?
    let raceClass: String?
    let horseNumber: Int?
    let finish: Int?
    let popularity: Int?
    let winOdds: Double?
    let jockey: String?
    let weightCarried: Double?
    let bodyWeight: Int?
    let bodyWeightChange: Int?

    enum CodingKeys: String, CodingKey {
        case date, raceID, venue, raceNumber, surface, distance
        case raceClass = "class"
        case horseNumber, finish, popularity, winOdds, jockey, weightCarried, bodyWeight, bodyWeightChange
    }
}

private struct KeibaAIHorseProfile: Identifiable, Codable, Hashable, KeibaAIHorseIdentityCandidate {
    var id: String { profileID }
    var birthYear: Int? { horseKey.flatMap { KeibaAIHorseIdentity.keyYear(name: name, key: $0) } }

    let schemaVersion: Int
    let profileID: String
    let horseKey: String?
    let name: String
    let currentAge: Int?
    let latestDate: String?
    let latestJockey: String?
    let latestTrainer: String?
    let career: KeibaAIHorseCareer
    let bestVenues: [KeibaAIHorseAffinityStat]
    let venueStats: [KeibaAIHorseAffinityStat]
    let venueSurfaceStats: [KeibaAIHorseAffinityStat]
    let surfaceStats: [KeibaAIHorseAffinityStat]
    let distanceStats: [KeibaAIHorseAffinityStat]
    let recentRaces: [KeibaAIHorseRecentRace]
    let notes: [String: String]?
}

private struct KeibaAIHorseProfileShard: Codable {
    let schemaVersion: Int
    let shard: String
    let profiles: [String: KeibaAIHorseProfile]
}

@MainActor
private final class KeibaAIHorseProfileStore: ObservableObject {
    @Published private(set) var entriesByName: [String: [KeibaAIHorseIndexEntry]] = [:]
    @Published private(set) var profilesByID: [String: KeibaAIHorseProfile] = [:]
    @Published var sourceMessage = "馬DB 未読み込み"
    @Published var isLoadingIndex = false

    private let owner = "colynky-wq"
    private let repo = "jra-auto-prediction"
    private let branch = "main"
    private var indexLoaded = false
    private var indexGeneration: String?
    private var indexTask: Task<Void, Error>?
    private var loadedShards: Set<String> = []

    private func normalizedName(_ name: String) -> String {
        name.precomposedStringWithCompatibilityMapping
            .components(separatedBy: .whitespacesAndNewlines).joined()
    }

    func loadIndex(force: Bool = false) async {
        do {
            try await ensureIndex(force: force)
        } catch {
            sourceMessage = "馬DB 取得失敗・再取得できます"
        }
    }

    private func ensureIndex(force: Bool = false) async throws {
        // A profile opened during startup waits for the same index request.
        if let task = indexTask {
            try await task.value
            return
        }
        if indexLoaded && !force { return }

        isLoadingIndex = true
        let task = Task<Void, Error> { @MainActor [self] in
            defer {
                isLoadingIndex = false
                indexTask = nil
            }
            let data = try await fetch(path: "HorseIndex.json")
            let payload = try JSONDecoder().decode(KeibaAIHorseIndexPayload.self, from: data)
            guard payload.schemaVersion == 1, payload.horseCount == payload.horses.count,
                  Set(payload.horses.map(\.profileID)).count == payload.horses.count else {
                throw HorseProfileError.incompleteDatabase
            }
            if force || payload.generatedAtJST != indexGeneration {
                profilesByID = [:]
                loadedShards = []
            }
            entriesByName = Dictionary(grouping: payload.horses, by: { normalizedName($0.name) })
            indexGeneration = payload.generatedAtJST
            indexLoaded = true
            sourceMessage = "馬DB \(payload.horseCount)頭"
        }
        indexTask = task
        try await task.value
    }

    func profile(for identity: KeibaAIHorseIdentity, force: Bool = false) async throws -> KeibaAIHorseProfile? {
        try await ensureIndex(force: force)
        let candidates = entriesByName[normalizedName(identity.name)] ?? []
        guard let entry = try identity.select(from: candidates) else { return nil }

        if !force, let cached = profilesByID[entry.profileID] {
            return try verified(cached, entry: entry, identity: identity)
        }

        if force || !loadedShards.contains(entry.shard) {
            let generation = indexGeneration
            let data = try await fetch(path: "HorseProfiles/\(entry.shard).json")
            let shard = try JSONDecoder().decode(KeibaAIHorseProfileShard.self, from: data)
            guard shard.shard == entry.shard,
                  let profile = shard.profiles[entry.profileID] else {
                throw HorseProfileError.incompleteDatabase
            }
            // Cache the entire shard so tapping another horse in the same
            // shard does not download it again. Discard an outdated response.
            if generation == indexGeneration {
                profilesByID.merge(shard.profiles) { _, fresh in fresh }
                loadedShards.insert(entry.shard)
            }
            return try verified(profile, entry: entry, identity: identity)
        }
        throw HorseProfileError.incompleteDatabase
    }

    private func verified(_ profile: KeibaAIHorseProfile, entry: KeibaAIHorseIndexEntry,
                          identity: KeibaAIHorseIdentity) throws -> KeibaAIHorseProfile {
        guard profile.schemaVersion == 1, profile.profileID == entry.profileID,
              identity.matches(profile) else { throw KeibaAIHorseIdentityError.mismatch }
        return profile
    }

    private func fetch(path: String) async throws -> Data {
        guard let encoded = path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
              var components = URLComponents(
                string: "https://raw.githubusercontent.com/\(owner)/\(repo)/\(branch)/\(encoded)"
              ) else {
            throw URLError(.badURL)
        }

        components.queryItems = [
            URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))
        ]
        guard let url = components.url else { throw URLError(.badURL) }

        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 12
        request.setValue("KeibaAI-iPad/3.0", forHTTPHeaderField: "User-Agent")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse,
              (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return data
    }

    private enum HorseProfileError: LocalizedError {
        case incompleteDatabase

        var errorDescription: String? {
            "馬DBの更新が完了していない可能性があります。少し待って再取得してください。"
        }
    }
}

// ============================================================
// MARK: - Research display: practical longshots and historical running style
// ============================================================

private struct KeibaAIStyleTendency: Codable, Hashable {
    let sampleRaces: Int
    let dominantStyle: String
    let confidence: String
    let distributionPct: [String: Double]
    let latestHistoryDate: String

    var label: String {
        let highest = distributionPct.values.max() ?? 0
        let leading = ["逃げ", "先行", "差し", "追込"].filter {
            abs((distributionPct[$0] ?? 0) - highest) < 0.05
        }
        return leading.count > 1 ? leading.joined(separator: "・") + "に分散" : dominantStyle + "傾向"
    }
}

private struct KeibaAIResearchHorse: Identifiable, Codable, Hashable {
    var id: Int { number }
    let number: Int
    let name: String
    let age: Int?
    let horseKey: String?
    let sourceAiProbabilityPct: Double
    let sourceMarketProbabilityPct: Double
    let sourceOdds: Double
    let styleTendency: KeibaAIStyleTendency?
    let initialPositionHistory: KeibaAIInitialPositionHistory?
}

private struct KeibaAIInitialPositionHistory: Codable, Hashable {
    let sampleRaces: Int
    let meanRelativePosition: Double
    let minRelativePosition: Double
    let maxRelativePosition: Double
    let latestHistoryDate: String

    var band: String {
        meanRelativePosition <= 0.33 ? "前寄り" : meanRelativePosition <= 0.67 ? "中ほど" : "後ろ寄り"
    }
}

private struct KeibaAIFormationValidation: Codable, Hashable {
    let eligibleRaces: Int
    let leaderTop1Pct: Double?
    let leaderTop3Pct: Double?
    let pairwiseOrderAgreementPct: Double?
}

private struct KeibaAIPaceAvailability: Codable, Hashable {
    let status: String
    let reason: String
}

private struct KeibaAIFormationReference: Codable, Hashable {
    let methodVersion: String
    let status: String
    let knownHorses: Int
    let unknownHorses: Int
    let totalHorses: Int
    let coveragePct: Double
    let minimumPastRaces: Int
    let minimumCoveragePct: Double
    let orderedHorseNumbers: [Int]
    let escapeTrendHorseNumbers: [Int]
    let frontTrendHorseNumbers: [Int]
    let paceForecast: KeibaAIPaceAvailability
    let meaning: String
    let validation2025: KeibaAIFormationValidation?
}

private struct KeibaAIPracticalLongshot: Identifiable, Codable, Hashable {
    var id: Int { number }
    let number: Int
    let name: String
    let odds: Double
    let marketRank: Int
    let aiProbabilityPct: Double
    let aiMarketRatio: Double?
    let legacyV1Eligible: Bool
    let expectedValue: Double
    let practicalScore: Double
    let practicalRank: Int
}

private struct KeibaAIAppResearchRace: Identifiable, Codable, Hashable {
    var id: String { raceID }
    let raceID: String
    let raceDate: String
    let horses: [KeibaAIResearchHorse]
    let longshotCandidates: [KeibaAIPracticalLongshot]
    let formation: KeibaAIFormationReference?

    func matches(_ race: Race) -> Bool {
        guard raceID == race.raceID else { return false }
        if let date = race.raceDate,
           date.replacingOccurrences(of: "-", with: "") != raceDate.replacingOccurrences(of: "-", with: "") {
            return false
        }
        let source = horses.sorted { $0.number < $1.number }
        let displayed = race.horses.sorted { $0.number < $1.number }
        guard source.count == displayed.count,
              Set(source.map(\.number)).count == source.count else { return false }
        return zip(source, displayed).allSatisfy { pair in
            let (original, horse) = pair
            let predictedIdentity = KeibaAIHorseIdentity(name: horse.name, horseKey: horse.horseKey,
                                                       age: horse.age, raceDate: race.raceDate).resolution
            let researchIdentity = KeibaAIHorseIdentity(name: original.name, horseKey: original.horseKey,
                                                      age: original.age, raceDate: raceDate).resolution
            if predictedIdentity == .invalid || researchIdentity == .invalid { return false }
            if case let .identified(predictedYear) = predictedIdentity,
               case let .identified(researchYear) = researchIdentity,
               predictedYear != researchYear { return false }
            return original.number == horse.number && original.name == horse.name
                && abs(original.sourceAiProbabilityPct - horse.aiProbability) < 0.000001
                && abs(original.sourceMarketProbabilityPct - horse.marketProbability) < 0.000001
                && abs(original.sourceOdds - horse.odds) < 0.000001
        }
    }
}

private struct KeibaAIAppResearchData: Codable {
    let schemaVersion: Int
    let status: String
    let generatedAtJST: String
    let stakeSizingEnabled: Bool
    let formalSignalsChanged: Bool
    let races: [KeibaAIAppResearchRace]
}

@MainActor
private final class KeibaAIAppResearchStore: ObservableObject {
    @Published private(set) var racesByID: [String: KeibaAIAppResearchRace] = [:]
    @Published var sourceMessage = "大穴・脚質データ 未読み込み"
    private var isLoading = false
    private var loadedDate8: String?

    private var requestedDate8: String?
    private var requestID = UUID()

    func race(for prediction: Race) -> KeibaAIAppResearchRace? {
        guard let value = racesByID[prediction.raceID], value.matches(prediction) else { return nil }
        return value
    }

    func load(date8: String, force: Bool = false) async {
        if (isLoading && requestedDate8 == date8) || (!force && loadedDate8 == date8) { return }
        let currentRequest = UUID()
        requestID = currentRequest
        requestedDate8 = date8
        isLoading = true
        defer { if requestID == currentRequest { isLoading = false } }
        if loadedDate8 != date8 { racesByID = [:] }
        let base = "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/"
        for path in ["AppResearchData.json", "reports/\(date8)/AppResearchData.json"] {
            guard requestID == currentRequest else { return }
            do {
                guard var components = URLComponents(string: base + path) else { continue }
                components.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
                guard let url = components.url else { continue }
                var request = URLRequest(url: url)
                request.timeoutInterval = 12
                request.cachePolicy = .reloadIgnoringLocalCacheData
                let (data, response) = try await URLSession.shared.data(for: request)
                guard requestID == currentRequest else { return }
                guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else { continue }
                let payload = try JSONDecoder().decode(KeibaAIAppResearchData.self, from: data)
                guard payload.schemaVersion == 1, payload.status == "research_display_only",
                      !payload.stakeSizingEnabled, !payload.formalSignalsChanged else { continue }
                let races = payload.races.filter { $0.raceDate.replacingOccurrences(of: "-", with: "") == date8 }
                guard !races.isEmpty, Set(races.map(\.raceID)).count == races.count else { continue }
                racesByID = Dictionary(uniqueKeysWithValues: races.map { ($0.raceID, $0) })
                loadedDate8 = date8
                sourceMessage = "大穴比較・脚質傾向 取得済"
                return
            } catch {
                continue
            }
        }
        if requestID == currentRequest { sourceMessage = "大穴・脚質データ 更新待ち" }
    }
}

// ============================================================
// MARK: - Validated SignalData / v1-v2 compatible
// ============================================================

private struct KeibaAIChaosSignal: Codable, Hashable {
    let score: Double
    let level: Int
    let stars: String
    let label: String
    let validatedOn: String?
}

private struct KeibaAIBattleSignal: Codable, Hashable {
    let score: Double
    let grade: String
    let candidateNumber: Int
    let candidateName: String
    let hasPositiveValueCandidate: Bool
    let aiProbability: Double
    let marketProbability: Double
    let aiMarketRatio: Double
    let aiMarketEdgePct: Double
    let meaning: String?
}

private struct KeibaAILongshotSignal: Identifiable, Codable, Hashable {
    var id: Int { number }
    let number: Int
    let name: String
    let odds: Double
    let marketRank: Int
    let marketProbability: Double
    let aiProbability: Double
    let aiTop3Probability: Double
    let aiMarketRatio: Double
    let aiMarketEdgePct: Double
    let researchBand: String?
}

private struct KeibaAIWidePairSignal: Identifiable, Codable, Hashable {
    var id: String { "\(number1)-\(number2)" }
    let number1: Int
    let name1: String
    let number2: Int
    let name2: String
    let marketWideProbability: Double
    let aiWideProbability: Double
    let probabilityEdgePct: Double
    let aiMarketProbabilityRatio: Double

    // SignalData v2: 発走前実ワイドオッズ＋実戦優先度
    let wideOddsLow: Double?
    let wideOddsHigh: Double?
    let conservativeExpectedValue: Double?
    let upperExpectedValue: Double?
    let breakEvenProbabilityPct: Double?
    let probabilityCushionPct: Double?
    let kellyFractionPct: Double?
    let practicalScore: Double?
    let practicalRank: Int?
    let hitProbabilityRank: Int?
    let valueBand: String?
    let oddsPopularity: Int?

    var practicalScoreValue: Double {
        if let practicalScore { return practicalScore }
        guard let odds = wideOddsLow, odds > 1 else { return 0 }
        let p = max(0, min(1, aiWideProbability / 100.0))
        let ev = p * odds
        guard ev > 1 else { return 0 }
        return max(0, min(1, (ev - 1) / (odds - 1))) * 100
    }
}

private struct KeibaAIWideEvaluationSignal: Codable, Hashable {
    let status: String
    let method: String?
    let note: String?
    let pairs: [KeibaAIWidePairSignal]

    // SignalData v2 fields. Optional so v1 history continues to decode.
    let oddsConnected: Bool?
    let capturedAtJST: String?
    let officialDatetime: String?
    let connectedPairCount: Int?
    let validationPairCount: Int?
    let positiveCandidateCount: Int?
    let rankingMethod: String?
}

private struct KeibaAITicketSignal: Codable, Hashable {
    let status: String
    let enabled: Bool
    let message: String?
}

private struct KeibaAIRaceSignal: Identifiable, Codable, Hashable {
    var id: String { raceID }
    let raceDate: String?
    let raceID: String
    let venue: String?
    let raceNumber: Int?
    let startTime: String?
    let raceName: String?
    let course: String?
    let fieldSize: Int?
    let signalVersion: String?
    let eligible: Bool
    let excludedReason: String?
    let chaos: KeibaAIChaosSignal?
    let battle: KeibaAIBattleSignal?
    let longshotCandidates: [KeibaAILongshotSignal]?
    let wideEvaluation: KeibaAIWideEvaluationSignal?
    let ticketPlan: KeibaAITicketSignal?
}

private struct KeibaAISignalData: Codable, Hashable {
    let schemaVersion: Int
    let signalVersion: String
    let generatedAtJST: String
    let formalSignals: Bool
    let races: [KeibaAIRaceSignal]
}

@MainActor
private final class KeibaAISignalStore: ObservableObject {
    @Published private(set) var data: KeibaAISignalData?
    @Published private(set) var racesByID: [String: KeibaAIRaceSignal] = [:]
    @Published var sourceMessage = "分析v2 未読み込み"
    @Published var isLoading = false

    private var loadedDate8: String?

    func signal(for raceID: String) -> KeibaAIRaceSignal? {
        racesByID[raceID]
    }

    func load(date8: String, force: Bool = false) async {
        if isLoading { return }
        if !force, loadedDate8 == date8 { return }
        isLoading = true
        defer { isLoading = false }

        let candidates = [
            "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/SignalData.json",
            "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/reports/\(date8)/SignalData.json"
        ]

        for raw in candidates {
            do {
                guard var c = URLComponents(string: raw) else { continue }
                c.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
                guard let url = c.url else { continue }
                var request = URLRequest(url: url)
                request.cachePolicy = .reloadIgnoringLocalCacheData
                request.timeoutInterval = 10
                request.setValue("KeibaAI-iPad/2.3", forHTTPHeaderField: "User-Agent")
                let (payload, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else { continue }
                let decoded = try JSONDecoder().decode(KeibaAISignalData.self, from: payload)
                let filtered = decoded.races.filter {
                    ($0.raceDate ?? "").replacingOccurrences(of: "-", with: "") == date8
                }
                if filtered.isEmpty { continue }
                data = decoded
                racesByID = Dictionary(uniqueKeysWithValues: filtered.map { ($0.raceID, $0) })
                sourceMessage = "分析v2 取得済"
                loadedDate8 = date8
                return
            } catch {
                continue
            }
        }

        data = nil
        racesByID = [:]
        sourceMessage = "分析v2 データ待ち"
        loadedDate8 = date8
    }
}


// ============================================================
// MARK: - Wide forward validation (saved pre-race odds vs payout)
// ============================================================

private struct KeibaAIWideValidationPair: Identifiable, Codable, Hashable {
    var id: String { "\(number1)-\(number2)" }

    let number1: Int
    let name1: String?
    let number2: Int
    let name2: String?
    let marketWideProbabilityPct: Double?
    let aiWideProbabilityPct: Double
    let probabilityEdgePct: Double?
    let oddsLow: Double
    let oddsHigh: Double
    let conservativeExpectedValue: Double
    let upperExpectedValue: Double
    let breakEvenProbabilityPct: Double?
    let probabilityCushionPct: Double?
    let kellyFractionPct: Double?
    let practicalScore: Double?
    let practicalRank: Int?
    let hit: Bool
    let payoutPer100Yen: Int
    let returnIndexPer100Yen: Double?

    var practicalScoreValue: Double {
        if let practicalScore { return practicalScore }
        guard oddsLow > 1 else { return 0 }
        let p = max(0, min(1, aiWideProbabilityPct / 100.0))
        let ev = p * oddsLow
        guard ev > 1 else { return 0 }
        return max(0, min(1, (ev - 1) / (oddsLow - 1))) * 100
    }
}

private struct KeibaAIWideValidationRace: Identifiable, Codable, Hashable {
    var id: String { raceID }

    let raceID: String
    let raceDate: String?
    let venue: String?
    let raceNumber: Int?
    let startTime: String?
    let raceName: String?
    let course: String?
    let chaosStars: String?
    let chaosLevel: Int?
    let battleGrade: String?
    let snapshotCapturedAtJST: String?
    let minutesToPost: Double?
    let evaluatedPairs: Int?
    let validationScope: String?
    let rankingMethod: String?
    let pairs: [KeibaAIWideValidationPair]
}

private struct KeibaAIWideValidationData: Codable, Hashable {
    let schemaVersion: Int
    let generatedAtJST: String
    let date: String?
    let meaning: String?
    let ticketPlanAutoEnable: Bool?
    let newValidationsThisRun: Int?
    let waitingForResult: Int?
    let races: [KeibaAIWideValidationRace]
}

@MainActor
private final class KeibaAIWideValidationStore: ObservableObject {
    @Published private(set) var data: KeibaAIWideValidationData?
    @Published private(set) var racesByID: [String: KeibaAIWideValidationRace] = [:]
    @Published var sourceMessage = "ワイド検証 未読み込み"
    @Published var isLoading = false

    private var loadedDate8: String?

    func race(for raceID: String) -> KeibaAIWideValidationRace? {
        racesByID[raceID]
    }

    func load(date8: String, force: Bool = false) async {
        if isLoading { return }
        if !force, loadedDate8 == date8 { return }

        isLoading = true
        defer { isLoading = false }

        do {
            guard var components = URLComponents(
                string: "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/reports/\(date8)/WideValidation.json"
            ) else { throw URLError(.badURL) }
            components.queryItems = [
                URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))
            ]
            guard let url = components.url else { throw URLError(.badURL) }

            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 10
            request.setValue("KeibaAI-iPad/2.4", forHTTPHeaderField: "User-Agent")

            let (payload, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse,
                  (200...299).contains(http.statusCode) else {
                throw URLError(.badServerResponse)
            }

            let decoded = try JSONDecoder().decode(KeibaAIWideValidationData.self, from: payload)
            data = decoded
            racesByID = Dictionary(uniqueKeysWithValues: decoded.races.map { ($0.raceID, $0) })
            sourceMessage = "ワイド検証 取得済"
            loadedDate8 = date8
        } catch {
            data = nil
            racesByID = [:]
            sourceMessage = "ワイド検証データ待ち"
            loadedDate8 = date8
        }
    }
}


private struct KeibaAIPerformanceMetrics: Codable, Hashable {
    let races: Int
    let aiTopWins: Int
    let aiTopWinRatePct: Double?
    let aiTopTop3: Int
    let aiTopTop3RatePct: Double?
    let marketTopWins: Int
    let marketTopWinRatePct: Double?
    let meanWinnerAiRank: Double?
    let meanTop3Overlap: Double?
    let referenceAiTopFlatReturnIndex: Double?
}

private struct KeibaAIPerformancePeriod: Identifiable, Codable, Hashable {
    let id: String
    let label: String
    let startDate: String?
    let endDate: String?
    let metrics: KeibaAIPerformanceMetrics
}

private struct KeibaAIPerformanceBattle: Identifiable, Codable, Hashable {
    var id: String { grade }
    let grade: String
    let prototype: Bool
    let metrics: KeibaAIPerformanceMetrics
}

private struct KeibaAIPerformanceChaos: Identifiable, Codable, Hashable {
    var id: Int { level }
    let level: Int
    let stars: String
    let prototype: Bool
    let metrics: KeibaAIPerformanceMetrics
}

private struct KeibaAIPerformanceDaily: Identifiable, Codable, Hashable {
    var id: String { raceDate }
    let raceDate: String
    let metrics: KeibaAIPerformanceMetrics
}

private struct KeibaAIPerformanceRace: Identifiable, Codable, Hashable {
    var id: String { raceID }

    let raceDate: String
    let raceID: String
    let venue: String?
    let raceNumber: Int
    let startTime: String?
    let raceName: String?
    let course: String?
    let fieldSize: Int
    let prototypeBattleScore: Double
    let prototypeBattleGrade: String
    let prototypeChaosLevel: Int
    let prototypeChaosStars: String
    let aiTopNumber: Int
    let aiTopName: String
    let aiTopProbability: Double
    let aiTopFinish: Int?
    let aiTopWon: Bool
    let aiTopTop3: Bool
    let marketTopNumber: Int
    let marketTopName: String
    let marketTopFinish: Int?
    let marketTopWon: Bool
    let winnerNumber: Int
    let winnerName: String
    let winnerAiRank: Int
    let winnerMarketRank: Int
    let aiTop3ActualTop3Overlap: Int
    let referenceAiTopFlatReturnIndex: Double
}

private struct KeibaAIPerformanceData: Codable, Hashable {
    let schemaVersion: Int
    let generatedAtJST: String
    let forwardValidation: Bool
    let prototypeLabels: Bool
    let notes: [String]
    let periods: [KeibaAIPerformancePeriod]
    let byBattleGrade: [KeibaAIPerformanceBattle]
    let byChaosLevel: [KeibaAIPerformanceChaos]
    let daily: [KeibaAIPerformanceDaily]
    let recentRaces: [KeibaAIPerformanceRace]
}

@MainActor
private final class KeibaAIPerformanceStore: ObservableObject {
    @Published private(set) var data: KeibaAIPerformanceData?
    @Published var sourceMessage = "成績 未読み込み"
    @Published var isLoading = false

    private var hasLoaded = false

    func load(force: Bool = false) async {
        if isLoading { return }
        if hasLoaded && !force { return }

        isLoading = true
        defer { isLoading = false }

        do {
            var components = URLComponents(
                string: "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/PerformanceData.json"
            )
            components?.queryItems = [
                URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))
            ]
            guard let url = components?.url else {
                throw URLError(.badURL)
            }

            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 10
            request.setValue("KeibaAI-iPad/2.2", forHTTPHeaderField: "User-Agent")

            let (payload, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse,
                  (200...299).contains(http.statusCode) else {
                throw URLError(.badServerResponse)
            }

            data = try JSONDecoder().decode(KeibaAIPerformanceData.self, from: payload)
            sourceMessage = "実運用成績 取得済"
            hasLoaded = true
        } catch {
            sourceMessage = "成績データ待ち"
        }
    }
}

private struct KeibaAIScheduleEntry: Identifiable, Hashable, Codable {
    var id: String { raceID }

    let raceID: String
    let venue: String
    let raceNumber: Int
    let startTime: String
    let raceName: String
    let course: String
    let raceDate: String?
    let fieldSize: Int?

    init(
        raceID: String,
        venue: String,
        raceNumber: Int,
        startTime: String,
        raceName: String,
        course: String,
        raceDate: String? = nil,
        fieldSize: Int? = nil
    ) {
        self.raceID = raceID
        self.venue = venue
        self.raceNumber = raceNumber
        self.startTime = startTime
        self.raceName = raceName
        self.course = course
        self.raceDate = raceDate
        self.fieldSize = fieldSize
    }
}

private struct KeibaAIScheduleItem: Identifiable, Hashable {
    var id: String { raceID }

    let raceID: String
    let venue: String
    let raceNumber: Int
    let startTime: String
    let raceName: String
    let course: String
    let raceDate: String
    let fieldSize: Int?
    let prediction: Race?

    var isPredicted: Bool { prediction != nil }
}

@MainActor
private final class KeibaAIScheduleStore: ObservableObject {
    @Published var entries: [KeibaAIScheduleEntry] = []
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var sourceMessage = "予定表 未読み込み"

    private let owner = "colynky-wq"
    private let repo = "jra-auto-prediction"
    private let branch = "main"
    private var loadedDate8: String?

    func load(date8: String, force: Bool = false) async {
        if !force, loadedDate8 == date8, !entries.isEmpty { return }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            if let github = try await fetchGitHubSchedule(date8: date8), !github.isEmpty {
                entries = sort(github)
                sourceMessage = "GitHub 1日予定表"
                loadedDate8 = date8
                return
            }
        } catch {
            // ScheduleData.json は将来の正式経路。未配置・日付不一致ならnetkeibaへフォールバック。
        }

        do {
            let live = try await fetchNetkeibaSchedule(date8: date8)
            entries = sort(live)
            sourceMessage = "netkeiba 当日予定表"
            loadedDate8 = date8
        } catch {
            entries = []
            sourceMessage = "予定表を取得できませんでした"
            errorMessage = "予定表の取得に失敗しました。AI予測済みレースは引き続き表示できます。"
        }
    }

    private func fetchGitHubSchedule(date8: String) async throws -> [KeibaAIScheduleEntry]? {
        var components = URLComponents(
            string: "https://raw.githubusercontent.com/\(owner)/\(repo)/\(branch)/ScheduleData.json"
        )!
        components.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
        guard let url = components.url else { return nil }

        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 10
        request.setValue("KeibaAI-iPad/2.0", forHTTPHeaderField: "User-Agent")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            return nil
        }

        let decoded = try JSONDecoder().decode([KeibaAIScheduleEntry].self, from: data)
        let filtered = decoded.filter { entry in
            guard let raceDate = entry.raceDate, !raceDate.isEmpty else { return true }
            return raceDate.replacingOccurrences(of: "-", with: "") == date8
        }
        return filtered
    }

    private func fetchNetkeibaSchedule(date8: String) async throws -> [KeibaAIScheduleEntry] {
        var components = URLComponents(string: "https://race.netkeiba.com/top/race_list_sub.html")!
        components.queryItems = [URLQueryItem(name: "kaisai_date", value: date8)]
        guard let url = components.url else { throw ScheduleError.invalidURL }

        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 20
        request.setValue(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Safari/537.36 KeibaAI-iPad/2.0",
            forHTTPHeaderField: "User-Agent"
        )
        request.setValue("ja,en;q=0.8", forHTTPHeaderField: "Accept-Language")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw ScheduleError.http
        }
        guard let html = String(data: data, encoding: .utf8) else {
            throw ScheduleError.decode
        }

        let parsed = parseRaceListHTML(html, date8: date8)
        guard !parsed.isEmpty else { throw ScheduleError.empty }
        return parsed
    }

    private func parseRaceListHTML(_ html: String, date8: String) -> [KeibaAIScheduleEntry] {
        let chunks = html.components(separatedBy: "RaceList_DataItem")
        var byID: [String: KeibaAIScheduleEntry] = [:]

        for raw in chunks.dropFirst() {
            let block = String(raw.prefix(9000))
            guard let raceID = firstCapture(in: block, pattern: #"race_id=(\d{12})"#) else { continue }

            let plain = normalizeVisibleText(block)
            guard let time = firstCapture(in: plain, pattern: #"(?<!\d)(\d{1,2}:\d{2})(?!\d)"#) else { continue }

            let venue = venueName(from: raceID)
            let raceNo = Int(String(raceID.suffix(2))) ?? 0
            let name = extractRaceName(from: block) ?? "\(raceNo)R"
            let course = extractCourse(from: plain) ?? "コース未取得"
            let fieldSize = firstCapture(in: plain, pattern: #"(?<!\d)(\d{1,2})頭"#)
                .flatMap(Int.init)

            byID[raceID] = KeibaAIScheduleEntry(
                raceID: raceID,
                venue: venue,
                raceNumber: raceNo,
                startTime: time.count == 4 ? "0\(time)" : time,
                raceName: name,
                course: course,
                raceDate: date8,
                fieldSize: fieldSize
            )
        }

        // CSS構造が変わった場合の最低限フォールバック。
        if byID.isEmpty {
            let pattern = #"race_id=(\d{12})"#
            for match in allCaptures(in: html, pattern: pattern) {
                let raceID = match
                let venue = venueName(from: raceID)
                let raceNo = Int(String(raceID.suffix(2))) ?? 0
                byID[raceID] = KeibaAIScheduleEntry(
                    raceID: raceID,
                    venue: venue,
                    raceNumber: raceNo,
                    startTime: "--:--",
                    raceName: "\(raceNo)R",
                    course: "コース未取得",
                    raceDate: date8
                )
            }
        }

        return Array(byID.values)
    }

    private func extractRaceName(from html: String) -> String? {
        let patterns = [
            #"class=[\"'][^\"']*RaceName[^\"']*[\"'][^>]*>(.*?)</"#,
            #"class=[\"'][^\"']*ItemTitle[^\"']*[\"'][^>]*>(.*?)</"#
        ]
        for pattern in patterns {
            if let raw = firstCapture(in: html, pattern: pattern, dotMatchesNewline: true) {
                let cleaned = normalizeVisibleText(raw)
                if !cleaned.isEmpty { return cleaned }
            }
        }
        return nil
    }

    private func extractCourse(from text: String) -> String? {
        guard let regex = try? NSRegularExpression(pattern: #"(芝|ダ|障)[^0-9]{0,12}([0-9]{3,4})m"#) else {
            return nil
        }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = regex.firstMatch(in: text, range: range), match.numberOfRanges >= 3,
              let r1 = Range(match.range(at: 1), in: text),
              let r2 = Range(match.range(at: 2), in: text) else { return nil }
        return "\(text[r1])\(text[r2])m"
    }

    private func normalizeVisibleText(_ raw: String) -> String {
        let noTags = raw.replacingOccurrences(of: #"<[^>]+>"#, with: " ", options: .regularExpression)
        let decoded = noTags
            .replacingOccurrences(of: "&nbsp;", with: " ")
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&#039;", with: "'")
            .replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">")
        return decoded
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func firstCapture(
        in text: String,
        pattern: String,
        dotMatchesNewline: Bool = false
    ) -> String? {
        var options: NSRegularExpression.Options = []
        if dotMatchesNewline { options.insert(.dotMatchesLineSeparators) }
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return nil }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = regex.firstMatch(in: text, range: range), match.numberOfRanges >= 2,
              let capture = Range(match.range(at: 1), in: text) else { return nil }
        return String(text[capture])
    }

    private func allCaptures(in text: String, pattern: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        return regex.matches(in: text, range: range).compactMap { match in
            guard match.numberOfRanges >= 2, let r = Range(match.range(at: 1), in: text) else { return nil }
            return String(text[r])
        }
    }

    private func venueName(from raceID: String) -> String {
        guard raceID.count >= 6 else { return "不明" }
        let start = raceID.index(raceID.startIndex, offsetBy: 4)
        let end = raceID.index(start, offsetBy: 2)
        let code = String(raceID[start..<end])
        return [
            "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
            "05": "東京", "06": "中山", "07": "中京", "08": "京都",
            "09": "阪神", "10": "小倉"
        ][code] ?? code
    }

    private func sort(_ values: [KeibaAIScheduleEntry]) -> [KeibaAIScheduleEntry] {
        values.sorted {
            if $0.startTime != $1.startTime { return $0.startTime < $1.startTime }
            if $0.venue != $1.venue { return venueOrder($0.venue) < venueOrder($1.venue) }
            return $0.raceNumber < $1.raceNumber
        }
    }

    private func venueOrder(_ venue: String) -> Int {
        ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
            .firstIndex(of: venue) ?? 99
    }

    private enum ScheduleError: Error {
        case invalidURL, http, decode, empty
    }
}

struct KeibaAINewDesignView: View {
    @StateObject private var store = RaceStore()
    @StateObject private var scheduleStore = KeibaAIScheduleStore()
    @StateObject private var resultStore = KeibaAIResultStore()
    @StateObject private var signalStore = KeibaAISignalStore()
    @StateObject private var wideValidationStore = KeibaAIWideValidationStore()
    @StateObject private var performanceStore = KeibaAIPerformanceStore()
    @StateObject private var horseProfileStore = KeibaAIHorseProfileStore()
    @StateObject private var appResearchStore = KeibaAIAppResearchStore()
    @StateObject private var budgetStore = KeibaAIBudgetStore()

    @State private var mainTab: KeibaAIMainTab = .today
    @State private var selectedRaceID: String?
    @State private var detailTab: KeibaAIDetailTab = .prediction
    @State private var showSettings = false
    @State private var showRecordingStatus = false

    @State private var selectedVenue = "全場"
    @State private var sortOption: KeibaAISortOption = .startTime
    @State private var selectedPerformancePeriodID = "today"
    @State private var usePracticalLongshot = false

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 320, ideal: 390, max: 470)
        } detail: {
            detail
        }
        .navigationSplitViewStyle(.balanced)
        .task {
            await store.start()
            async let schedule: Void = scheduleStore.load(date8: selectedDate8)
            async let results: Void = resultStore.load(date8: selectedDate8)
            async let signals: Void = signalStore.load(date8: selectedDate8)
            async let wide: Void = wideValidationStore.load(date8: selectedDate8)
            async let performance: Void = performanceStore.load()
            async let horses: Void = horseProfileStore.loadIndex()
            async let research: Void = appResearchStore.load(date8: selectedDate8)
            _ = await (schedule, results, signals, wide, performance, horses, research)
            chooseFirstRaceIfNeeded()
        }
        .task(id: selectedDate8) {
            // On the latest race day, poll ResultData periodically so a finished
            // race switches from 「結果待ち」 to 「結果取得済」 without requiring
            // the user to press the refresh button. GitHub publishes about every
            // five minutes; one-minute app polling keeps the UI responsive.
            while !Task.isCancelled {
                if store.selectedSnapshotID == "latest" {
                    if !store.isLoading { await store.loadSnapshot(id: "latest") }
                    await resultStore.load(date8: selectedDate8, force: true)
                    await signalStore.load(date8: selectedDate8, force: true)
                    await wideValidationStore.load(date8: selectedDate8, force: true)
                    await performanceStore.load(force: true)
                    await appResearchStore.load(date8: selectedDate8, force: true)
                }

                do {
                    try await Task.sleep(for: .seconds(60))
                } catch {
                    return
                }
            }
        }
        .onChange(of: store.races) { _, _ in
            chooseFirstRaceIfNeeded()
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
        .sheet(isPresented: $showRecordingStatus) {
            KeibaAIRecordingStatusScreen()
        }
    }

    // MARK: - Schedule merge / filter / sort

    private var selectedDate8: String {
        if store.selectedSnapshotID == "latest" {
            return Self.todayJST8
        }

        let savedDates = Set(store.races.compactMap { $0.raceDate?.replacingOccurrences(of: "-", with: "") })
        if savedDates.count == 1, let date8 = savedDates.first, date8.count == 8 {
            return date8
        }

        if let snapshot = store.snapshots.first(where: { $0.id == store.selectedSnapshotID }),
           let date = snapshot.date {
            return Self.date8Formatter.string(from: date)
        }

        return Self.todayJST8
    }

    private static var date8Formatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "ja_JP")
        f.timeZone = TimeZone(identifier: "Asia/Tokyo")
        f.dateFormat = "yyyyMMdd"
        return f
    }()

    private static var todayJST8: String {
        date8Formatter.string(from: Date())
    }

    private var allScheduleItems: [KeibaAIScheduleItem] {
        let predictions = store.races.filter { race in
            guard let date = race.raceDate else { return true }
            return date.replacingOccurrences(of: "-", with: "") == selectedDate8
        }
        let predictedByID = Dictionary(uniqueKeysWithValues: predictions.map { ($0.raceID, $0) })
        var items: [KeibaAIScheduleItem] = []
        var seen = Set<String>()

        for entry in scheduleStore.entries {
            seen.insert(entry.raceID)
            let prediction = predictedByID[entry.raceID]
            items.append(
                KeibaAIScheduleItem(
                    raceID: entry.raceID,
                    venue: prediction?.venue ?? entry.venue,
                    raceNumber: prediction?.raceNumber ?? entry.raceNumber,
                    startTime: prediction?.startTime ?? entry.startTime,
                    raceName: prediction?.raceName ?? entry.raceName,
                    course: prediction?.course ?? entry.course,
                    raceDate: entry.raceDate ?? selectedDate8,
                    fieldSize: prediction?.horses.count ?? entry.fieldSize,
                    prediction: prediction
                )
            )
        }

        // 予定表取得に失敗しても、既に予測済みのレースは必ず表示する。
        for race in predictions where !seen.contains(race.raceID) {
            items.append(
                KeibaAIScheduleItem(
                    raceID: race.raceID,
                    venue: race.venue,
                    raceNumber: race.raceNumber,
                    startTime: race.startTime,
                    raceName: race.raceName,
                    course: race.course,
                    raceDate: selectedDate8,
                    fieldSize: race.horses.count,
                    prediction: race
                )
            )
        }

        return items
    }

    private var venueOptions: [String] {
        let names = Set(allScheduleItems.map(\.venue))
        let ordered = names.sorted { venueOrder($0) < venueOrder($1) }
        return ["全場"] + ordered
    }

    private var venueFilteredItems: [KeibaAIScheduleItem] {
        guard selectedVenue != "全場" else { return allScheduleItems }
        return allScheduleItems.filter { $0.venue == selectedVenue }
    }

    private var displayedTodayItems: [KeibaAIScheduleItem] {
        sort(items: venueFilteredItems, option: sortOption)
    }

    private var predictedRacesForSelectedVenue: [Race] {
        venueFilteredItems.compactMap(\.prediction)
    }

    private var selectedScheduleItem: KeibaAIScheduleItem? {
        guard let selectedRaceID else { return nil }
        return allScheduleItems.first { $0.raceID == selectedRaceID }
    }

    private func chooseFirstRaceIfNeeded() {
        guard !allScheduleItems.isEmpty else {
            selectedRaceID = nil
            return
        }

        if let selectedRaceID,
           allScheduleItems.contains(where: { $0.raceID == selectedRaceID }) {
            return
        }

        selectedRaceID = sort(items: allScheduleItems, option: .startTime).first?.raceID
    }

    private func venueOrder(_ venue: String) -> Int {
        ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
            .firstIndex(of: venue) ?? 99
    }

    private func status(for item: KeibaAIScheduleItem) -> KeibaAIRaceStatus {
        if item.course.contains("障") || item.raceName.contains("障害") {
            return .excluded
        }
        if resultStore.result(for: item.raceID) != nil { return .finished }

        guard item.startTime != "--:--" else {
            return item.prediction != nil ? .predicted : .waiting
        }

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "ja_JP")
        formatter.timeZone = TimeZone(identifier: "Asia/Tokyo")
        formatter.dateFormat = "yyyyMMdd HH:mm"

        if let post = formatter.date(from: "\(item.raceDate) \(item.startTime)"), Date() >= post {
            return .finished
        }
        return item.prediction != nil ? .predicted : .waiting
    }

    private func sort(
        items: [KeibaAIScheduleItem],
        option: KeibaAISortOption
    ) -> [KeibaAIScheduleItem] {
        items.sorted { lhs, rhs in
            switch option {
            case .startTime:
                if lhs.startTime != rhs.startTime { return lhs.startTime < rhs.startTime }
                if lhs.venue != rhs.venue { return venueOrder(lhs.venue) < venueOrder(rhs.venue) }
                return lhs.raceNumber < rhs.raceNumber

            case .raceNumber:
                if lhs.raceNumber != rhs.raceNumber { return lhs.raceNumber < rhs.raceNumber }
                if lhs.startTime != rhs.startTime { return lhs.startTime < rhs.startTime }
                return venueOrder(lhs.venue) < venueOrder(rhs.venue)

            case .venue:
                if lhs.venue != rhs.venue { return venueOrder(lhs.venue) < venueOrder(rhs.venue) }
                return lhs.raceNumber < rhs.raceNumber

            case .battle:
                let lv = signalStore.signal(for: lhs.raceID)?.battle?.score ?? -1
                let rv = signalStore.signal(for: rhs.raceID)?.battle?.score ?? -1
                if lv != rv { return lv > rv }
                return lhs.startTime < rhs.startTime

            case .chaos:
                let lv = signalStore.signal(for: lhs.raceID)?.chaos?.score ?? -1
                let rv = signalStore.signal(for: rhs.raceID)?.chaos?.score ?? -1
                if lv != rv { return lv > rv }
                return lhs.startTime < rhs.startTime

            case .longshot:
                let lv = signalStore.signal(for: lhs.raceID)?.longshotCandidates?.count ?? -1
                let rv = signalStore.signal(for: rhs.raceID)?.longshotCandidates?.count ?? -1
                if lv != rv { return lv > rv }
                return lhs.startTime < rhs.startTime

            case .predictedFirst:
                if lhs.isPredicted != rhs.isPredicted { return lhs.isPredicted && !rhs.isPredicted }
                return lhs.startTime < rhs.startTime
            }
        }
    }

    // MARK: - Sidebar

    private var sidebar: some View {
        VStack(spacing: 0) {
            brandHeader
                .padding(.horizontal, 18)
                .padding(.top, 12)
                .padding(.bottom, 10)

            mainTabBar
                .padding(.horizontal, 12)
                .padding(.bottom, 10)

            Divider()

            if mainTab != .guide && mainTab != .performance && mainTab != .ticket {
                snapshotBar
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)

                venueAndSortControls
                    .padding(.horizontal, 12)
                    .padding(.bottom, 10)

                Divider()
            }

            Group {
                if mainTab == .guide {
                    guideSidebar
                } else if mainTab == .performance {
                    performanceSidebar
                } else if mainTab == .ticket {
                    ticketSidebar
                } else if (store.isLoading || scheduleStore.isLoading) && allScheduleItems.isEmpty {
                    loadingState
                } else if allScheduleItems.isEmpty {
                    emptyState
                } else {
                    sidebarContent
                }
            }
        }
        .background(Color(uiColor: .systemGroupedBackground))
    }

    private var brandHeader: some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(.blue.gradient)
                    .frame(width: 42, height: 42)

                Image(systemName: "flag.checkered")
                    .font(.title3.bold())
                    .foregroundStyle(.white)
            }

            VStack(alignment: .leading, spacing: 2) {
                Text("KeibaAI")
                    .font(.title2.weight(.heavy))

                Text("レース選びを、もっと分かりやすく")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Button {
                Task {
                    await store.refresh()
                    await scheduleStore.load(date8: selectedDate8, force: true)
                    await resultStore.load(date8: selectedDate8, force: true)
                    await signalStore.load(date8: selectedDate8, force: true)
                    await wideValidationStore.load(date8: selectedDate8, force: true)
                    await performanceStore.load(force: true)
                    await horseProfileStore.loadIndex(force: true)
                    await appResearchStore.load(date8: selectedDate8, force: true)
                    chooseFirstRaceIfNeeded()
                }
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.headline)
                    .frame(width: 36, height: 36)
                    .background(.thinMaterial, in: Circle())
            }
            .buttonStyle(.plain)
            .disabled(store.isLoading || scheduleStore.isLoading)
            .accessibilityLabel("更新")

            Button {
                showSettings = true
            } label: {
                Image(systemName: "gearshape")
                    .font(.headline)
                    .frame(width: 36, height: 36)
                    .background(.thinMaterial, in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("設定")
        }
    }

    private var mainTabBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(KeibaAIMainTab.allCases) { tab in
                    Button {
                        withAnimation(.easeInOut(duration: 0.18)) {
                            mainTab = tab
                        }
                    } label: {
                        Label(tab.rawValue, systemImage: tab.icon)
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 11)
                            .padding(.vertical, 8)
                            .foregroundStyle(mainTab == tab ? .white : .primary)
                            .background {
                                Capsule(style: .continuous)
                                    .fill(mainTab == tab ? Color.blue : Color(uiColor: .secondarySystemGroupedBackground))
                            }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var snapshotBar: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text("表示データ")
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                Picker(
                    "表示データ",
                    selection: Binding(
                        get: { store.selectedSnapshotID },
                        set: { newValue in
                            selectedRaceID = nil
                            Task {
                                await store.loadSnapshot(id: newValue)
                                await scheduleStore.load(date8: selectedDate8, force: true)
                                await resultStore.load(date8: selectedDate8, force: true)
                                await signalStore.load(date8: selectedDate8, force: true)
                                await wideValidationStore.load(date8: selectedDate8, force: true)
                                await appResearchStore.load(date8: selectedDate8, force: true)
                                selectedVenue = "全場"
                                chooseFirstRaceIfNeeded()
                            }
                        }
                    )
                ) {
                    ForEach(store.snapshots) { snapshot in
                        Text(snapshot.label)
                            .tag(snapshot.id)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text(store.sourceMessage)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                Text(scheduleStore.sourceMessage)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }

            if store.isLoading || scheduleStore.isLoading {
                ProgressView()
                    .controlSize(.small)
            }
        }
    }

    private var venueAndSortControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 7) {
                    ForEach(venueOptions, id: \.self) { venue in
                        Button {
                            selectedVenue = venue
                            if let current = selectedScheduleItem, venue != "全場", current.venue != venue {
                                selectedRaceID = displayedTodayItems.first?.raceID
                            }
                        } label: {
                            Text(venue)
                                .font(.caption.weight(.semibold))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 7)
                                .foregroundStyle(selectedVenue == venue ? .white : .primary)
                                .background(
                                    selectedVenue == venue ? Color.indigo : Color(uiColor: .secondarySystemGroupedBackground),
                                    in: Capsule()
                                )
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            HStack {
                Label(
                    selectedVenue == "全場" ? "全開催地" : "\(selectedVenue)のみ",
                    systemImage: "mappin.and.ellipse"
                )
                .font(.caption2)
                .foregroundStyle(.secondary)

                Spacer()

                Menu {
                    ForEach(KeibaAISortOption.allCases) { option in
                        Button {
                            sortOption = option
                        } label: {
                            Label(option.rawValue, systemImage: option.icon)
                        }
                    }
                } label: {
                    Label("並び替え：\(sortOption.rawValue)", systemImage: "arrow.up.arrow.down")
                        .font(.caption.weight(.semibold))
                }
            }
        }
    }

    @ViewBuilder
    private var sidebarContent: some View {
        switch mainTab {
        case .today:
            todayList
        case .focus:
            focusList
        case .longshot:
            longshotList
        case .ticket:
            ticketSidebar
        case .performance:
            performanceSidebar
        case .guide:
            guideSidebar
        }
    }

    private var todayList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                sectionHeader(
                    title: selectedVenue == "全場" ? "本日の全レース" : "\(selectedVenue)のレース",
                    subtitle: "未予測を含む予定表 / \(displayedTodayItems.count)レース"
                )

                if let scheduleError = scheduleStore.errorMessage {
                    Text(scheduleError)
                        .font(.caption2)
                        .foregroundStyle(.orange)
                        .padding(.horizontal, 4)
                }

                ForEach(displayedTodayItems) { item in
                    KeibaAIRaceCard(
                        item: item,
                        status: status(for: item),
                        signal: signalStore.signal(for: item.raceID),
                        isSelected: selectedRaceID == item.raceID
                    )
                    .onTapGesture {
                        selectedRaceID = item.raceID
                        detailTab = .prediction
                    }
                }
            }
            .padding(12)
        }
    }

    private var focusList: some View {
        // 「注目レース」は勝負度v1がB以上のレースだけ。
        let races = predictedRacesForSelectedVenue
            .filter { race in
                guard let grade = signalStore.signal(for: race.raceID)?.battle?.grade else { return false }
                return grade == "S" || grade == "A" || grade == "B"
            }
            .sorted {
                (signalStore.signal(for: $0.raceID)?.battle?.score ?? -1) >
                (signalStore.signal(for: $1.raceID)?.battle?.score ?? -1)
            }

        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                sectionHeader(
                    title: "注目レース",
                    subtitle: "勝負度B以上のみ表示 / 見送り・Cは除外"
                )

                if races.isEmpty {
                    KeibaAIInfoCard(
                        icon: "clock",
                        title: "現在、勝負度B以上のレースはありません",
                        message: "『今日』タブでは予測済み・見送りを含む全レースを確認できます。注目レースは、AIと市場の評価差などから相対的に注目度が高いレースだけを表示します。"
                    )
                } else {
                    ForEach(races) { race in
                        KeibaAIRaceCard(
                            item: item(from: race),
                            status: .predicted,
                            signal: signalStore.signal(for: race.raceID),
                            isSelected: selectedRaceID == race.raceID
                        )
                        .onTapGesture {
                            selectedRaceID = race.raceID
                            detailTab = .prediction
                        }
                    }
                }
            }
            .padding(12)
        }
    }

    private var longshotList: some View {
        let races = predictedRacesForSelectedVenue.filter { race in
            if usePracticalLongshot { return !(appResearchStore.race(for: race)?.longshotCandidates ?? []).isEmpty }
            return !(signalStore.signal(for: race.raceID)?.longshotCandidates ?? []).isEmpty
        }

        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                Picker("大穴の表示", selection: $usePracticalLongshot) {
                    Text("従来v1").tag(false)
                    Text("v2比較・研究").tag(true)
                }
                .pickerStyle(.segmented)

                sectionHeader(
                    title: usePracticalLongshot ? "大穴v2 比較" : "大穴候補",
                    subtitle: usePracticalLongshot ? appResearchStore.sourceMessage : "v1条件：市場6番手以下・8倍以上・AI1%以上・AI÷市場1.20以上"
                )

                if usePracticalLongshot {
                    KeibaAIInfoCard(
                        icon: "flask",
                        title: "当たりやすさと期待値を比較",
                        message: "AI勝率・保存オッズ・理論期待倍率を並べます。比較順位は研究用です。従来の候補条件と購入金額の配分は変更しません。"
                    )
                }

                if races.isEmpty {
                    KeibaAIInfoCard(
                        icon: "sparkles",
                        title: usePracticalLongshot ? "比較データまたは比較対象がありません" : "現在の予測済みデータに候補はありません",
                        message: usePracticalLongshot ? "研究データが表示中の予測と一致すると比較できます。更新ボタンで最新データを取得してください。" : "現在の10分前付近データでv1条件に該当する馬はいません。高オッズだけを理由に候補にはしません。"
                    )
                } else {
                    ForEach(races) { race in
                        VStack(alignment: .leading, spacing: 8) {
                            if usePracticalLongshot, let research = appResearchStore.race(for: race) {
                                Text("\(race.venue) \(race.raceNumber)R ・ \(race.raceName)")
                                    .font(.headline)
                                ForEach(research.longshotCandidates.prefix(3)) { candidate in
                                    KeibaAIPracticalCandidateRow(candidate: candidate)
                                }
                            } else {
                                KeibaAILongshotRaceCard(
                                    race: race,
                                    candidates: signalStore.signal(for: race.raceID)?.longshotCandidates ?? [],
                                    isSelected: selectedRaceID == race.raceID
                                )
                            }
                        }
                        .padding(usePracticalLongshot ? 12 : 0)
                        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
                        .onTapGesture {
                            selectedRaceID = race.raceID
                            detailTab = usePracticalLongshot ? .longshot : .prediction
                        }
                    }
                }
            }
            .padding(12)
        }
    }

    private var ticketSidebar: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                sectionHeader(
                    title: "馬券プラン",
                    subtitle: "確率評価は接続済み / 金額配分はまだ行わない"
                )

                KeibaAIInfoCard(
                    icon: "checkmark.shield.fill",
                    title: "ワイド確率まで接続済み",
                    message: "荒れ度・勝負度・大穴候補に加え、発走前ワイド実オッズ・AI的中率・保守的期待値・実戦優先度まで接続済みです。金額配分は検証件数が十分に貯まるまでロックします。"
                )

                KeibaAIBudgetCard(store: budgetStore)
            }
            .padding(12)
        }
    }


    private var performanceSidebar: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                sectionHeader(
                    title: "実運用成績",
                    subtitle: performanceStore.sourceMessage
                )

                Button {
                    showRecordingStatus = true
                } label: {
                    Label("研究記録状況", systemImage: "checklist")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(14)
                }
                .buttonStyle(.bordered)

                if performanceStore.isLoading && performanceStore.data == nil {
                    ProgressView("成績を取得中…")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 24)
                } else if let data = performanceStore.data {
                    ForEach(data.periods) { period in
                        Button {
                            selectedPerformancePeriodID = period.id
                        } label: {
                            HStack(spacing: 12) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(mainPeriodLabel(period))
                                        .font(.headline)
                                        .foregroundStyle(.primary)

                                    Text("\(period.metrics.races)レースを突合")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }

                                Spacer()

                                if let rate = period.metrics.aiTopWinRatePct {
                                    Text(String(format: "%.1f%%", rate))
                                        .font(.headline.monospacedDigit())
                                        .foregroundStyle(.blue)
                                }

                                Image(systemName: "chevron.right")
                                    .font(.caption.weight(.bold))
                                    .foregroundStyle(.tertiary)
                            }
                            .padding(14)
                            .background(
                                selectedPerformancePeriodID == period.id
                                ? Color.blue.opacity(0.10)
                                : Color(uiColor: .systemBackground),
                                in: RoundedRectangle(cornerRadius: 16, style: .continuous)
                            )
                            .overlay {
                                if selectedPerformancePeriodID == period.id {
                                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                                        .stroke(Color.blue, lineWidth: 1.5)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                    }

                    Text("AI1位勝率を大きく表示していますが、これは馬券推奨ではなく予測性能の確認用です。")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 4)
                        .padding(.top, 4)
                } else {
                    KeibaAIInfoCard(
                        icon: "chart.line.uptrend.xyaxis",
                        title: "成績データを準備中",
                        message: "結果データの蓄積後、今日・直近7日・全期間の成績を表示します。"
                    )
                }
            }
            .padding(12)
        }
    }

    private var guideSidebar: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                sectionHeader(
                    title: "凡例・設計思想",
                    subtitle: "KeibaAIの数字の見方と、しないこと"
                )

                KeibaAIInfoCard(
                    icon: "waveform.path.ecg",
                    title: "荒れ度と勝負度は別物",
                    message: "荒れ度はレースの混戦・不確実さ、勝負度はAIと市場の評価差から見た注目度です。荒れる＝買うべき、ではありません。"
                )

                KeibaAIInfoCard(
                    icon: "nosign",
                    title: "障害レースは予測対象外",
                    message: "現在のモデルは平地の芝・ダート用です。障害レースは予定表には表示しても、AI予測は出しません。"
                )

                KeibaAIInfoCard(
                    icon: "checkmark.shield.fill",
                    title: "結果で必ず検証",
                    message: "発走前の予測を保存し、レース後の実着順と照合します。結果を後から予測に混ぜないことを重視しています。"
                )
            }
            .padding(12)
        }
    }

    private func mainPeriodLabel(_ period: KeibaAIPerformancePeriod) -> String {
        period.id == "all" ? "実運用開始から" : period.label.replacingOccurrences(of: "前向き検証 全期間", with: "実運用開始から")
    }

    private func item(from race: Race) -> KeibaAIScheduleItem {
        KeibaAIScheduleItem(
            raceID: race.raceID,
            venue: race.venue,
            raceNumber: race.raceNumber,
            startTime: race.startTime,
            raceName: race.raceName,
            course: race.course,
            raceDate: selectedDate8,
            fieldSize: race.horses.count,
            prediction: race
        )
    }

    private func sectionHeader(title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.headline)

            Text(subtitle)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 4)
    }

    private var loadingState: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("レース予定とAI予測を取得中…")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var emptyState: some View {
        ContentUnavailableView(
            "レースデータがありません",
            systemImage: "tray",
            description: Text(store.errorMessage ?? scheduleStore.errorMessage ?? "更新ボタンを押して再取得してください")
        )
    }

    // MARK: - Detail

    @ViewBuilder
    private var detail: some View {
        if mainTab == .guide {
            KeibaAIGuideView()
        } else if mainTab == .performance {
            KeibaAIPerformanceView(
                data: performanceStore.data,
                selectedPeriodID: selectedPerformancePeriodID
            )
        } else if mainTab == .ticket {
            KeibaAITicketPlanView(budgetStore: budgetStore)
        } else if let item = selectedScheduleItem {
            if let race = item.prediction {
                KeibaAIRaceDetailNew(
                    race: race,
                    result: resultStore.result(for: race.raceID),
                    signal: signalStore.signal(for: race.raceID),
                    wideValidation: wideValidationStore.race(for: race.raceID),
                    research: appResearchStore.race(for: race),
                    horseProfileStore: horseProfileStore,
                    selectedTab: $detailTab
                )
            } else {
                KeibaAIUnpredictedRaceDetail(
                    item: item,
                    status: status(for: item)
                )
            }
        } else {
            ContentUnavailableView(
                "レースを選択",
                systemImage: "flag.checkered",
                description: Text("左の一覧からレースを選択してください")
            )
        }
    }
}

// ============================================================
// MARK: - Race Card
// ============================================================

private struct KeibaAIRaceCard: View {
    let item: KeibaAIScheduleItem
    let status: KeibaAIRaceStatus
    let signal: KeibaAIRaceSignal?
    let isSelected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("\(item.venue) \(item.raceNumber)R")
                    .font(.headline)

                Spacer()

                Text(item.startTime)
                    .font(.headline.monospacedDigit())
            }

            Text(item.raceName)
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)

            HStack(spacing: 8) {
                Text(item.course)
                if let fieldSize = item.fieldSize {
                    Text("・")
                    Text("\(fieldSize)頭")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            HStack(spacing: 8) {
                if item.prediction != nil, let signal, signal.eligible {
                    KeibaAIPill(title: "荒れ度", value: signal.chaos?.stars ?? "--", tint: .orange)
                    KeibaAIPill(title: "勝負度", value: signal.battle?.grade ?? "--", tint: .blue)
                } else {
                    KeibaAIPill(title: "荒れ度", value: "--", tint: .orange)
                    KeibaAIPill(title: "勝負度", value: "--", tint: .blue)
                }

                Spacer()

                Label(status.rawValue, systemImage: status.icon)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(status.tint)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 5)
                    .background(status.tint.opacity(0.10), in: Capsule())
            }

            if item.prediction != nil {
                Text(signal?.eligible == true ? "荒れ度・勝負度 v2" : "分析v2 データ待ち")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            } else {
                Text(
                    status == .excluded
                    ? "障害レースは現在のAI予測対象外です"
                    : (status == .finished ? "このレースの10分前AI予測データは未保存です" : "発走約10分前にAI予測を追加予定")
                )
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(14)
        .background {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color(uiColor: isSelected ? .secondarySystemGroupedBackground : .systemBackground))
        }
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(isSelected ? Color.blue : Color.clear, lineWidth: 2)
        }
        .contentShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}

private struct KeibaAILongshotRaceCard: View {
    let race: Race
    let candidates: [KeibaAILongshotSignal]
    let isSelected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("\(race.venue) \(race.raceNumber)R")
                    .font(.headline)

                Spacer()

                Text("候補 \(candidates.count)頭")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.pink)
            }

            Text(race.raceName)
                .font(.subheadline)

            ForEach(candidates.prefix(3)) { horse in
                HStack(spacing: 8) {
                    Text("\(horse.number)")
                        .font(.caption.bold())
                        .frame(width: 24, height: 24)
                        .background(.pink.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))

                    Text(horse.name)
                        .font(.caption.weight(.semibold))
                        .lineLimit(1)

                    Spacer()

                    Text(String(format: "%.1f倍", horse.odds))
                        .font(.caption.monospacedDigit())

                    Text(String(format: "×%.2f", horse.aiMarketRatio))
                        .font(.caption.bold().monospacedDigit())
                        .foregroundStyle(.pink)
                }
            }
        }
        .padding(14)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
        .overlay {
            RoundedRectangle(cornerRadius: 18)
                .stroke(isSelected ? Color.pink : Color.clear, lineWidth: 2)
        }
    }
}

private struct KeibaAIPracticalCandidateRow: View {
    let candidate: KeibaAIPracticalLongshot

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text("比較\(candidate.practicalRank)位")
                    .font(.caption.bold()).foregroundStyle(.pink)
                Text("\(candidate.number)  \(candidate.name)")
                    .font(.subheadline.weight(.semibold)).lineLimit(1)
                Spacer(minLength: 0)
                if candidate.legacyV1Eligible {
                    Text("v1候補").font(.caption2).foregroundStyle(.blue)
                }
            }
            HStack(spacing: 12) {
                metric("AI勝率", String(format: "%.2f%%", candidate.aiProbabilityPct))
                metric("保存オッズ", String(format: "%.1f倍", candidate.odds))
                metric("理論期待", String(format: "%.2f倍", candidate.expectedValue))
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.pink.opacity(0.05), in: RoundedRectangle(cornerRadius: 12))
    }

    private func metric(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.caption.weight(.semibold).monospacedDigit())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct KeibaAIPill: View {
    let title: String
    let value: String
    let tint: Color

    var body: some View {
        HStack(spacing: 5) {
            Text(title)
                .foregroundStyle(.secondary)

            Text(value)
                .fontWeight(.bold)
                .foregroundStyle(tint)
        }
        .font(.caption2)
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(tint.opacity(0.08), in: Capsule())
    }
}

private struct KeibaAIUnpredictedRaceDetail: View {
    let item: KeibaAIScheduleItem
    let status: KeibaAIRaceStatus

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("\(item.venue) \(item.raceNumber)R")
                                .font(.caption.weight(.bold))
                                .foregroundStyle(.blue)

                            Text(item.raceName)
                                .font(.largeTitle.weight(.heavy))

                            HStack(spacing: 12) {
                                Label(item.startTime, systemImage: "clock")
                                Label(item.course, systemImage: "map")
                                if let fieldSize = item.fieldSize {
                                    Label("\(fieldSize)頭", systemImage: "person.3.fill")
                                }
                            }
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        }

                        Spacer()

                        Label(status.rawValue, systemImage: status.icon)
                            .font(.caption.weight(.bold))
                            .foregroundStyle(status.tint)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 7)
                            .background(status.tint.opacity(0.10), in: Capsule())
                    }

                    Divider()

                    KeibaAIInfoCard(
                        icon: status == .excluded ? "nosign" : (status == .finished ? "flag.checkered" : "clock.badge.checkmark"),
                        title: status == .excluded ? "現在のAI予測対象外です" : (status == .finished ? "AI予測データはありません" : "10分前AI予測を待っています"),
                        message: status == .excluded
                            ? "現在のKeibaAIは平地の芝・ダートを対象に設計しています。障害レースは競走特性が大きく異なるため、専用モデルを検証するまでは予測を出しません。"
                            : (status == .finished
                                ? "予定表として表示しています。このレースのAI勝率・Top2・Top3はRaceData.jsonに保存されていません。"
                                : "このレースは予定表から先に表示されています。発走約8〜12分前にライブ予測が保存されると、この画面がAI予測画面へ自動的に切り替わります。")
                    )
                }
                .padding(20)
                .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 22))

                HStack(spacing: 12) {
                    KeibaAISummaryTile(
                        title: "発走",
                        value: item.startTime,
                        note: item.raceDate,
                        icon: "clock",
                        tint: .blue
                    )

                    KeibaAISummaryTile(
                        title: "開催地",
                        value: item.venue,
                        note: "\(item.raceNumber)R",
                        icon: "building.columns",
                        tint: .green
                    )

                    KeibaAISummaryTile(
                        title: "コース",
                        value: item.course,
                        note: "予定表",
                        icon: "map.fill",
                        tint: .orange
                    )
                }
            }
            .padding(22)
            .frame(maxWidth: 1050, alignment: .leading)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("\(item.venue) \(item.raceNumber)R")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// ============================================================
// MARK: - New Race Detail
// ============================================================

private struct KeibaAIRaceDetailNew: View {
    let race: Race
    let result: KeibaAIResultRace?
    let signal: KeibaAIRaceSignal?
    let wideValidation: KeibaAIWideValidationRace?
    let research: KeibaAIAppResearchRace?
    @ObservedObject var horseProfileStore: KeibaAIHorseProfileStore
    @Binding var selectedTab: KeibaAIDetailTab
    @State private var selectedHorseForProfile: Horse?

    private var sortedHorses: [Horse] {
        race.horses.sorted { $0.aiProbability > $1.aiProbability }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                headerCard
                detailTabBar

                switch selectedTab {
                case .prediction:
                    predictionSection
                case .longshot:
                    practicalLongshotSection
                case .result:
                    resultSection
                case .wide:
                    wideSection
                case .pace:
                    paceSection
                case .course:
                    courseSection
                case .history:
                    historySection
                }
            }
            .padding(22)
            .frame(maxWidth: 1050, alignment: .leading)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("\(race.venue) \(race.raceNumber)R")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selectedHorseForProfile) { horse in
            KeibaAIHorseProfileView(
                horse: horse,
                race: race,
                identity: profileIdentity(for: horse),
                store: horseProfileStore
            )
        }
    }

    private func profileIdentity(for horse: Horse) -> KeibaAIHorseIdentity {
        let reference = research?.matches(race) == true
            ? research?.horses.first { $0.number == horse.number && $0.name == horse.name } : nil
        return KeibaAIHorseIdentity(name: horse.name, horseKey: horse.horseKey ?? reference?.horseKey,
                                   age: horse.age ?? reference?.age, raceDate: race.raceDate ?? research?.raceDate)
    }

    private var headerCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("\(race.venue) \(race.raceNumber)R")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.blue)

                    Text(race.raceName)
                        .font(.largeTitle.weight(.heavy))

                    HStack(spacing: 12) {
                        Label(race.startTime, systemImage: "clock")
                        Label(race.course, systemImage: "map")
                            .lineLimit(1)
                            .fixedSize(horizontal: true, vertical: false)
                        Label("\(race.horses.count)頭", systemImage: "person.3.fill")
                    }
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: 8) {
                    Text(result == nil ? "AI予測済" : "結果取得済")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(result == nil ? Color.green : Color.secondary)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background((result == nil ? Color.green : Color.secondary).opacity(0.10), in: Capsule())

                    Text("Race ID \(race.raceID)")
                        .font(.caption2.monospaced())
                        .foregroundStyle(.tertiary)
                }
            }

            Divider()

            HStack(spacing: 12) {
                KeibaAISummaryTile(
                    title: "荒れ度",
                    value: signal?.chaos?.stars ?? "--",
                    note: signal?.chaos?.label ?? "v1待ち",
                    icon: "waveform.path.ecg",
                    tint: .orange
                )

                KeibaAISummaryTile(
                    title: "勝負度",
                    value: signal?.battle?.grade ?? "--",
                    note: signal?.battle.map { "候補 #\($0.candidateNumber)" } ?? "v1待ち",
                    icon: "target",
                    tint: .blue
                )

                KeibaAISummaryTile(
                    title: "大穴候補",
                    value: "\(signal?.longshotCandidates?.count ?? 0)頭",
                    note: "v1条件",
                    icon: "sparkles",
                    tint: .pink
                )
            }
        }
        .padding(20)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 22))
    }

    private var detailTabBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(KeibaAIDetailTab.allCases) { tab in
                    Button {
                        withAnimation(.easeInOut(duration: 0.18)) {
                            selectedTab = tab
                        }
                    } label: {
                        Text(tab.rawValue)
                            .font(.subheadline.weight(.semibold))
                            .padding(.horizontal, 16)
                            .padding(.vertical, 9)
                            .foregroundStyle(selectedTab == tab ? .white : .primary)
                            .background {
                                Capsule()
                                    .fill(selectedTab == tab ? Color.blue : Color(uiColor: .systemBackground))
                            }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var predictionSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("AI予測順位")
                    .font(.title3.weight(.bold))

                Spacer()

                Text("勝率順")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            ForEach(Array(sortedHorses.enumerated()), id: \.element.id) { index, horse in
                KeibaAIHorseCard(
                    rank: index + 1,
                    horse: horse,
                    onOpenProfile: {
                        selectedHorseForProfile = horse
                    }
                )
            }
        }
    }

    private var resultSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("予測と結果")
                .font(.title3.weight(.bold))

            if let result {
                let aiSorted = sortedHorses
                let aiRankByNumber = Dictionary(
                    uniqueKeysWithValues: aiSorted.enumerated().map { ($0.element.number, $0.offset + 1) }
                )
                let resultSorted = result.horses.sorted {
                    ($0.finishPosition ?? 999) < ($1.finishPosition ?? 999)
                }
                let topPick = aiSorted.first
                let topPickFinish = topPick.flatMap { horse in
                    result.horses.first(where: { $0.number == horse.number })?.finishPosition
                }
                let winner = result.horses.first(where: { $0.finishPosition == 1 })
                let winnerAIRank = winner.flatMap { aiRankByNumber[$0.number] }
                let aiTop3Numbers = Set(aiSorted.prefix(3).map(\.number))
                let actualTop3Numbers = Set(
                    result.horses.compactMap { horse -> Int? in
                        guard let finish = horse.finishPosition, finish <= 3 else { return nil }
                        return horse.number
                    }
                )
                let top3Overlap = aiTop3Numbers.intersection(actualTop3Numbers).count

                HStack(spacing: 12) {
                    KeibaAISummaryTile(
                        title: "AI1位の結果",
                        value: topPickFinish.map { "\($0)着" } ?? "--",
                        note: topPick?.name ?? "",
                        icon: "target",
                        tint: .blue
                    )

                    KeibaAISummaryTile(
                        title: "勝ち馬のAI順位",
                        value: winnerAIRank.map { "\($0)位" } ?? "--",
                        note: winner?.name ?? "",
                        icon: "trophy.fill",
                        tint: .orange
                    )

                    KeibaAISummaryTile(
                        title: "AI上位3頭",
                        value: "\(top3Overlap)/3頭",
                        note: "実際の3着内との一致",
                        icon: "checkmark.seal.fill",
                        tint: .green
                    )
                }

                VStack(spacing: 0) {
                    HStack {
                        Text("着順")
                            .frame(width: 42, alignment: .leading)
                        Text("馬")
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text("AI順位")
                            .frame(width: 68, alignment: .trailing)
                        Text("10分前AI勝率")
                            .frame(width: 98, alignment: .trailing)
                        Text("最終単勝")
                            .frame(width: 78, alignment: .trailing)
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)

                    Divider()

                    ForEach(resultSorted) { resultHorse in
                        let predictedHorse = race.horses.first(where: { $0.number == resultHorse.number })
                        HStack {
                            Text(resultHorse.finishPosition.map(String.init) ?? resultHorse.resultStatus ?? "--")
                                .font(.headline.monospacedDigit())
                                .frame(width: 42, alignment: .leading)

                            HStack(spacing: 8) {
                                Text("\(resultHorse.number)")
                                    .font(.caption.bold().monospacedDigit())
                                    .frame(width: 24, height: 24)
                                    .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 6))

                                Text(resultHorse.name)
                                    .font(.subheadline.weight(.semibold))
                                    .lineLimit(1)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)

                            Text(aiRankByNumber[resultHorse.number].map { "\($0)位" } ?? "--")
                                .font(.subheadline.monospacedDigit())
                                .frame(width: 68, alignment: .trailing)

                            Text(predictedHorse.map { String(format: "%.1f%%", $0.aiProbability) } ?? "--")
                                .font(.subheadline.monospacedDigit())
                                .frame(width: 98, alignment: .trailing)

                            Text(resultHorse.finalOdds.map { String(format: "%.1f倍", $0) } ?? "--")
                                .font(.subheadline.monospacedDigit())
                                .frame(width: 78, alignment: .trailing)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)

                        Divider()
                    }
                }
                .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))

                Text("予測値は発走前に保存された値と、レース後に取得した確定結果を馬番で突合しています。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                KeibaAIInfoCard(
                    icon: "flag.checkered",
                    title: "結果データ待ち",
                    message: "結果確定後、通常は数分〜10分程度で自動取得し、AI順位と実着順をこの画面で比較します。"
                )
            }
        }
    }

    private var rankedSignalWidePairs: [KeibaAIWidePairSignal] {
        guard let evaluation = signal?.wideEvaluation else { return [] }
        let hasOdds = evaluation.oddsConnected == true || evaluation.pairs.contains(where: { $0.wideOddsLow != nil })
        if hasOdds {
            return evaluation.pairs.sorted {
                if $0.practicalScoreValue != $1.practicalScoreValue {
                    return $0.practicalScoreValue > $1.practicalScoreValue
                }
                if $0.aiWideProbability != $1.aiWideProbability {
                    return $0.aiWideProbability > $1.aiWideProbability
                }
                return ($0.conservativeExpectedValue ?? 0) > ($1.conservativeExpectedValue ?? 0)
            }
        }
        return evaluation.pairs.sorted {
            if $0.aiWideProbability != $1.aiWideProbability {
                return $0.aiWideProbability > $1.aiWideProbability
            }
            return $0.probabilityEdgePct > $1.probabilityEdgePct
        }
    }

    private var rankedValidatedWidePairs: [KeibaAIWideValidationPair] {
        guard let wideValidation else { return [] }
        return wideValidation.pairs.sorted {
            if $0.practicalScoreValue != $1.practicalScoreValue {
                return $0.practicalScoreValue > $1.practicalScoreValue
            }
            if $0.aiWideProbabilityPct != $1.aiWideProbabilityPct {
                return $0.aiWideProbabilityPct > $1.aiWideProbabilityPct
            }
            return $0.conservativeExpectedValue > $1.conservativeExpectedValue
        }
    }

    private func validationPair(_ number1: Int, _ number2: Int) -> KeibaAIWideValidationPair? {
        let a = min(number1, number2)
        let b = max(number1, number2)
        return wideValidation?.pairs.first {
            min($0.number1, $0.number2) == a && max($0.number1, $0.number2) == b
        }
    }

    private func wideOddsText(low: Double?, high: Double?) -> String {
        guard let low else { return "--" }
        if let high, abs(high - low) >= 0.05 {
            return String(format: "%.1f–%.1f", low, high)
        }
        return String(format: "%.1f", low)
    }

    private func practicalBand(ev: Double?, score: Double) -> (String, Color) {
        guard let ev else { return ("確率順", .secondary) }
        if ev >= 1.20 && score > 0 { return ("高期待", .orange) }
        if ev >= 1.10 && score > 0 { return ("有力", .green) }
        if ev >= 1.00 && score > 0 { return ("候補", .blue) }
        return ("見送り", .secondary)
    }

    private var wideSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("ワイド評価")
                .font(.title3.weight(.bold))

            KeibaAIInfoCard(
                icon: "target",
                title: "当たりやすさと期待値を両方見る",
                message: "順位は『市場との差が大きい順』ではありません。発走前の実ワイドオッズが取れたら、AIが見積もる2頭同時3着内確率と保守的期待値を組み合わせ、現実的に当たりやすい候補を上位にします。"
            )

            if let evaluation = signal?.wideEvaluation,
               !rankedSignalWidePairs.isEmpty,
               (evaluation.oddsConnected == true || rankedSignalWidePairs.contains(where: { $0.wideOddsLow != nil })) {

                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 10) {
                        Label("発走前実オッズ接続済", systemImage: "checkmark.seal.fill")
                            .font(.caption.weight(.bold))
                            .foregroundStyle(.green)

                        if let captured = evaluation.capturedAtJST {
                            Text("取得 \(captured)")
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }

                        Spacer()

                        if let n = evaluation.connectedPairCount {
                            Text("\(n)組接続")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }

                    if let method = evaluation.rankingMethod {
                        Text(method)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(12)
                .background(.green.opacity(0.07), in: RoundedRectangle(cornerRadius: 14))

                VStack(spacing: 0) {
                    HStack(spacing: 8) {
                        Text("順位").frame(width: 38, alignment: .leading)
                        Text("組み合わせ").frame(maxWidth: .infinity, alignment: .leading)
                        Text("AI的中").frame(width: 66, alignment: .trailing)
                        Text("実オッズ").frame(width: 88, alignment: .trailing)
                        Text("保守EV").frame(width: 64, alignment: .trailing)
                        Text("実戦優先").frame(width: 74, alignment: .trailing)
                        if wideValidation != nil {
                            Text("結果").frame(width: 78, alignment: .trailing)
                        }
                    }
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(12)

                    Divider()

                    ForEach(Array(rankedSignalWidePairs.prefix(10).enumerated()), id: \.element.id) { index, pair in
                        let score = pair.practicalScoreValue
                        let band = practicalBand(ev: pair.conservativeExpectedValue, score: score)
                        let validated = validationPair(pair.number1, pair.number2)

                        HStack(spacing: 8) {
                            Text("\(pair.practicalRank ?? index + 1)")
                                .font(.subheadline.bold().monospacedDigit())
                                .foregroundStyle(index < 3 ? .blue : .secondary)
                                .frame(width: 38, alignment: .leading)

                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(pair.number1)-\(pair.number2)  \(pair.name1) × \(pair.name2)")
                                    .font(.subheadline.weight(.semibold))
                                    .lineLimit(1)
                                if let valueBand = pair.valueBand {
                                    Text(valueBand)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)

                            Text(String(format: "%.1f%%", pair.aiWideProbability))
                                .font(.subheadline.bold().monospacedDigit())
                                .frame(width: 66, alignment: .trailing)

                            Text(wideOddsText(low: pair.wideOddsLow, high: pair.wideOddsHigh))
                                .font(.subheadline.monospacedDigit())
                                .frame(width: 88, alignment: .trailing)

                            Text(pair.conservativeExpectedValue.map { String(format: "%.2f", $0) } ?? "--")
                                .font(.subheadline.bold().monospacedDigit())
                                .foregroundStyle((pair.conservativeExpectedValue ?? 0) >= 1.0 ? .green : .secondary)
                                .frame(width: 64, alignment: .trailing)

                            VStack(alignment: .trailing, spacing: 2) {
                                Text(band.0)
                                    .font(.caption.weight(.bold))
                                    .foregroundStyle(band.1)
                                Text(String(format: "%.2f", score))
                                    .font(.caption2.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }
                            .frame(width: 74, alignment: .trailing)

                            if wideValidation != nil {
                                Group {
                                    if let validated {
                                        if validated.hit {
                                            Text("的中 ¥\(validated.payoutPer100Yen)")
                                                .foregroundStyle(.green)
                                        } else {
                                            Text("不的中")
                                                .foregroundStyle(.secondary)
                                        }
                                    } else {
                                        Text("--")
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                .font(.caption.weight(.semibold))
                                .frame(width: 78, alignment: .trailing)
                            }
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        Divider()
                    }
                }
                .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))

                Text(evaluation.note ?? "実戦優先度は候補の順位づけ専用です。")
                    .font(.caption)
                    .foregroundStyle(.secondary)

            } else if let validation = wideValidation, !rankedValidatedWidePairs.isEmpty {
                KeibaAIInfoCard(
                    icon: "checkmark.circle.fill",
                    title: "発走前評価と確定払戻を突合済み",
                    message: "このレースでは発走前に保存したワイド実オッズとAI確率を、レース後の確定払戻と比較できます。表示順位はAI的中率と保守的期待値を両方考慮した実戦優先度です。"
                )

                if let captured = validation.snapshotCapturedAtJST {
                    HStack(spacing: 8) {
                        Text("オッズ固定: \(captured)")
                        if let minutes = validation.minutesToPost {
                            Text(String(format: "発走 %.1f分前", minutes))
                        }
                    }
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                }

                VStack(spacing: 0) {
                    HStack(spacing: 8) {
                        Text("順位").frame(width: 38, alignment: .leading)
                        Text("組み合わせ").frame(maxWidth: .infinity, alignment: .leading)
                        Text("AI的中").frame(width: 66, alignment: .trailing)
                        Text("実オッズ").frame(width: 88, alignment: .trailing)
                        Text("保守EV").frame(width: 64, alignment: .trailing)
                        Text("実戦優先").frame(width: 74, alignment: .trailing)
                        Text("結果").frame(width: 82, alignment: .trailing)
                    }
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(12)

                    Divider()

                    ForEach(Array(rankedValidatedWidePairs.prefix(10).enumerated()), id: \.element.id) { index, pair in
                        let band = practicalBand(ev: pair.conservativeExpectedValue, score: pair.practicalScoreValue)

                        HStack(spacing: 8) {
                            Text("\(pair.practicalRank ?? index + 1)")
                                .font(.subheadline.bold().monospacedDigit())
                                .foregroundStyle(index < 3 ? .blue : .secondary)
                                .frame(width: 38, alignment: .leading)

                            Text("\(pair.number1)-\(pair.number2)  \(pair.name1 ?? "") × \(pair.name2 ?? "")")
                                .font(.subheadline.weight(.semibold))
                                .lineLimit(1)
                                .frame(maxWidth: .infinity, alignment: .leading)

                            Text(String(format: "%.1f%%", pair.aiWideProbabilityPct))
                                .font(.subheadline.bold().monospacedDigit())
                                .frame(width: 66, alignment: .trailing)

                            Text(wideOddsText(low: pair.oddsLow, high: pair.oddsHigh))
                                .font(.subheadline.monospacedDigit())
                                .frame(width: 88, alignment: .trailing)

                            Text(String(format: "%.2f", pair.conservativeExpectedValue))
                                .font(.subheadline.bold().monospacedDigit())
                                .foregroundStyle(pair.conservativeExpectedValue >= 1.0 ? .green : .secondary)
                                .frame(width: 64, alignment: .trailing)

                            VStack(alignment: .trailing, spacing: 2) {
                                Text(band.0)
                                    .font(.caption.weight(.bold))
                                    .foregroundStyle(band.1)
                                Text(String(format: "%.2f", pair.practicalScoreValue))
                                    .font(.caption2.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }
                            .frame(width: 74, alignment: .trailing)

                            Text(pair.hit ? "的中 ¥\(pair.payoutPer100Yen)" : "不的中")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(pair.hit ? .green : .secondary)
                                .frame(width: 82, alignment: .trailing)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        Divider()
                    }
                }
                .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))

            } else if let evaluation = signal?.wideEvaluation, !rankedSignalWidePairs.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Label("実ワイドオッズ取得前", systemImage: "clock")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.orange)

                    Text("この段階では『市場との差』ではなく、AIが見積もる同時3着内確率が高い順に表示します。実オッズ取得後に実戦優先度へ自動で切り替わります。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(12)
                .background(.orange.opacity(0.07), in: RoundedRectangle(cornerRadius: 14))

                VStack(spacing: 0) {
                    HStack {
                        Text("順位").frame(width: 38, alignment: .leading)
                        Text("組み合わせ").frame(maxWidth: .infinity, alignment: .leading)
                        Text("AI的中").frame(width: 74, alignment: .trailing)
                        Text("市場").frame(width: 70, alignment: .trailing)
                        Text("差").frame(width: 70, alignment: .trailing)
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(12)

                    Divider()

                    ForEach(Array(rankedSignalWidePairs.prefix(10).enumerated()), id: \.element.id) { index, pair in
                        HStack {
                            Text("\(pair.hitProbabilityRank ?? index + 1)")
                                .font(.subheadline.bold().monospacedDigit())
                                .frame(width: 38, alignment: .leading)
                            Text("\(pair.number1)-\(pair.number2)  \(pair.name1) × \(pair.name2)")
                                .font(.subheadline.weight(.semibold))
                                .lineLimit(1)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Text(String(format: "%.1f%%", pair.aiWideProbability))
                                .font(.subheadline.bold().monospacedDigit())
                                .foregroundStyle(.blue)
                                .frame(width: 74, alignment: .trailing)
                            Text(String(format: "%.1f%%", pair.marketWideProbability))
                                .font(.subheadline.monospacedDigit())
                                .frame(width: 70, alignment: .trailing)
                            Text(String(format: "%+.1fpt", pair.probabilityEdgePct))
                                .font(.caption.bold().monospacedDigit())
                                .foregroundStyle(pair.probabilityEdgePct > 0 ? .green : .secondary)
                                .frame(width: 70, alignment: .trailing)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        Divider()
                    }
                }
                .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))

                Text(evaluation.note ?? "発走前ワイド実オッズの取得待ちです。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                KeibaAIInfoCard(
                    icon: "clock",
                    title: "ワイド評価データ待ち",
                    message: "AI予測とSignalDataが更新されると、まず当たりやすさ順の確率評価を表示し、実ワイドオッズ取得後に実戦優先度へ切り替えます。"
                )
            }

            KeibaAIInfoCard(
                icon: "lock.fill",
                title: "馬券金額はまだ自動配分しません",
                message: signal?.ticketPlan?.message ?? "実戦優先度は候補の並べ替えに使います。十分な実運用件数・回収率・的中率・連敗の安定性を確認するまで、購入金額の自動配分はロックします。"
            )
        }
    }

    private var paceSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("過去走から見た脚質傾向")
                .font(.title3.weight(.bold))

            KeibaAIInfoCard(
                icon: "figure.run",
                title: "過去の走り方を割合で表示",
                message: "このレースより前の最大6走を参考にします。直線コースの通過順位は除きます。割合は過去走の頻度で、今回の戦法の確率ではありません。"
            )

            if let research {
                if let formation = research.formation {
                    KeibaAIFormationCard(reference: formation, researchHorses: research.horses, horses: race.horses) {
                        selectedHorseForProfile = $0
                    }
                } else {
                    KeibaAIInfoCard(
                        icon: "arrow.clockwise",
                        title: "参考隊列データ 更新待ち",
                        message: "参考隊列の項目は公開データの更新後に表示されます。更新ボタンで再取得してください。"
                    )
                }
                ForEach(sortedHorses) { horse in
                    VStack(alignment: .leading, spacing: 10) {
                        Button { selectedHorseForProfile = horse } label: {
                            HStack {
                                Text("\(horse.number)  \(horse.name)").font(.headline)
                                Spacer()
                                Image(systemName: "chevron.right").font(.caption)
                            }
                        }
                        .buttonStyle(.plain)

                        if let stat = research.horses.first(where: { $0.number == horse.number })?.styleTendency {
                            Text("\(stat.label) ・ 過去\(stat.sampleRaces)走 ・ 参考度 \(stat.confidence)")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.blue)
                            ForEach(["逃げ", "先行", "差し", "追込"], id: \.self) { style in
                                HStack(spacing: 10) {
                                    Text(style).font(.caption).frame(width: 36, alignment: .leading)
                                    ProgressView(value: min(1, max(0, (stat.distributionPct[style] ?? 0) / 100)))
                                    Text(String(format: "%.1f%%", stat.distributionPct[style] ?? 0))
                                        .font(.caption.monospacedDigit())
                                        .frame(width: 56, alignment: .trailing)
                                }
                            }
                            Text("最終参照 \(stat.latestHistoryDate) ・ 参考度は過去走の数と傾向のまとまりの目安です")
                                .font(.caption2).foregroundStyle(.secondary)
                        } else {
                            Text("このレースより前の位置取りデータが未収録です。")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .padding(16)
                    .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 16))
                }
            } else {
                KeibaAIInfoCard(
                    icon: "arrow.clockwise",
                    title: "脚質データ 更新待ち",
                    message: "表示中の予測と研究データが一致すると表示できます。更新ボタンで再取得してください。"
                )
            }
        }
    }

    private var practicalLongshotSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("大穴v2 比較・研究").font(.title3.weight(.bold))
            KeibaAIInfoCard(
                icon: "flask",
                title: "勝率と価格を一緒に見る",
                message: "市場6番手以下・単勝8倍以上・AI勝率1%以上を比較対象にします。理論期待倍率はAI勝率×保存オッズです。同じ期待倍率なら、より当たりやすい候補が上位になります。順位は研究用で、購入金額は配分しません。"
            )
            if let research {
                if research.longshotCandidates.isEmpty {
                    Text("このレースには比較対象の馬がいません。")
                        .foregroundStyle(.secondary)
                }
                ForEach(research.longshotCandidates) { candidate in
                    Button {
                        selectedHorseForProfile = race.horses.first { $0.number == candidate.number }
                    } label: {
                        KeibaAIPracticalCandidateRow(candidate: candidate)
                    }
                    .buttonStyle(.plain)
                }
            } else {
                Text("表示中の予測に対応した研究データの取得待ちです。更新して再取得してください。")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var courseSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("コース分析")
                .font(.title3.weight(.bold))

            HStack(spacing: 12) {
                KeibaAISummaryTile(
                    title: "競馬場",
                    value: race.venue,
                    note: "現在値",
                    icon: "building.columns",
                    tint: .green
                )

                KeibaAISummaryTile(
                    title: "コース",
                    value: race.course,
                    note: "現在値",
                    icon: "map.fill",
                    tint: .green
                )
            }

            KeibaAIInfoCard(
                icon: "chart.xyaxis.line",
                title: "コース傾向は今後追加",
                message: "脚質別成績、内外の有利不利、距離・芝ダート別の傾向などを過去データから集計して表示する予定です。"
            )
        }
    }

    private var historySection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("過去データ")
                .font(.title3.weight(.bold))

            KeibaAIInfoCard(
                icon: "clock.arrow.circlepath",
                title: "GitHub履歴はすでに利用可能",
                message: "左上の『表示データ』から過去に保存されたRaceData.jsonを選択できます。将来はレース詳細内にも比較表示を追加できます。"
            )
        }
    }
}

private struct KeibaAISummaryTile: View {
    let title: String
    let value: String
    let note: String
    let icon: String
    let tint: Color

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .font(.headline)
                .foregroundStyle(tint)
                .frame(width: 34, height: 34)
                .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 9))

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Text(value)
                    .font(.headline)
                    .lineLimit(1)

                Text(note)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
    }
}

// ============================================================
// MARK: - Horse Card
// ============================================================

private struct KeibaAIHorseCard: View {
    let rank: Int
    let horse: Horse
    let onOpenProfile: () -> Void

    private var researchLabel: String {
        if horse.aiMarketRatio >= 1.20 {
            return "高乖離候補"
        } else if horse.aiMarketRatio >= 1.10 {
            return "注目"
        } else if horse.aiMarketRatio >= 1.05 {
            return "やや高評価"
        } else if horse.aiMarketRatio >= 0.95 {
            return "市場並み"
        } else {
            return "AI低評価"
        }
    }

    var body: some View {
        VStack(spacing: 14) {
            HStack(spacing: 12) {
                Text("\(rank)")
                    .font(.headline.weight(.heavy))
                    .foregroundStyle(rank <= 3 ? .white : .secondary)
                    .frame(width: 32, height: 32)
                    .background(rankColor, in: RoundedRectangle(cornerRadius: 9))

                Text("\(horse.number)")
                    .font(.title3.weight(.heavy).monospacedDigit())
                    .frame(width: 36, height: 36)
                    .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 9))

                Button(action: onOpenProfile) {
                    HStack(spacing: 7) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(horse.name)
                                .font(.headline)
                                .foregroundStyle(.primary)

                            HStack(spacing: 5) {
                                Text(researchLabel)
                                    .font(.caption2.weight(.semibold))
                                    .foregroundStyle(labelTint)
                                Text("馬データを見る")
                                    .font(.caption2)
                                    .foregroundStyle(.blue)
                            }
                        }

                        Image(systemName: "chevron.right")
                            .font(.caption.weight(.bold))
                            .foregroundStyle(.blue)
                    }
                }
                .buttonStyle(.plain)

                Spacer()

                VStack(alignment: .trailing, spacing: 2) {
                    Text(String(format: "%.1f倍", horse.odds))
                        .font(.headline.monospacedDigit())

                    Text("単勝")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            Divider()

            LazyVGrid(
                columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: 5),
                spacing: 10
            ) {
                KeibaAIValueCell(title: "市場", value: pct(horse.marketProbability))
                KeibaAIValueCell(title: "AI勝率", value: pct(horse.aiProbability), emphasis: true)
                KeibaAIValueCell(title: "AI 2着内", value: optionalPct(horse.aiTop2Probability), emphasis: true)
                KeibaAIValueCell(title: "AI 3着内", value: optionalPct(horse.aiTop3Probability), emphasis: true)
                KeibaAIValueCell(title: "AI÷市場", value: String(format: "%.2f", horse.aiMarketRatio), emphasis: horse.aiMarketRatio >= 1.10)
                KeibaAIValueCell(title: "期待指数", value: String(format: "%.1f", horse.expectedIndex))
                KeibaAIValueCell(title: "市場2着内", value: optionalPct(horse.marketTop2Probability))
                KeibaAIValueCell(title: "市場3着内", value: optionalPct(horse.marketTop3Probability))
            }
        }
        .padding(16)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
    }

    private var rankColor: Color {
        switch rank {
        case 1: return .blue
        case 2: return .indigo
        case 3: return .purple
        default: return Color(uiColor: .secondarySystemGroupedBackground)
        }
    }

    private var labelTint: Color {
        if horse.aiMarketRatio >= 1.20 { return .pink }
        if horse.aiMarketRatio >= 1.10 { return .orange }
        if horse.aiMarketRatio >= 1.05 { return .blue }
        return .secondary
    }

    private func pct(_ value: Double) -> String {
        String(format: "%.1f%%", value)
    }

    private func optionalPct(_ value: Double?) -> String {
        guard let value else { return "--" }
        return String(format: "%.1f%%", value)
    }
}

private struct KeibaAIValueCell: View {
    let title: String
    let value: String
    var emphasis = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)

            Text(value)
                .font(.subheadline.weight(.bold).monospacedDigit())
                .foregroundStyle(emphasis ? .blue : .primary)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            emphasis ? Color.blue.opacity(0.07) : Color(uiColor: .secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: 11)
        )
    }
}


// ============================================================
// MARK: - Horse Profile Sheet
// ============================================================

private struct KeibaAIHorseProfileView: View {
    let horse: Horse
    let race: Race
    let identity: KeibaAIHorseIdentity
    @ObservedObject var store: KeibaAIHorseProfileStore

    @Environment(\.dismiss) private var dismiss
    @State private var profile: KeibaAIHorseProfile?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var identityIssue = false
    @State private var activeRequest: UUID?

    private var currentSurface: String? {
        let text = race.course.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("芝") { return "芝" }
        if text.hasPrefix("ダ") { return "ダ" }
        return nil
    }

    private var currentVenueStat: KeibaAIHorseAffinityStat? {
        guard let profile else { return nil }
        if let surface = currentSurface,
           let exact = profile.venueSurfaceStats.first(where: { $0.venue == race.venue && $0.surface == surface }) {
            return exact
        }
        return profile.venueStats.first(where: { $0.venue == race.venue })
    }

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView("馬データを取得中…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let profile {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 18) {
                            profileHeader(profile)
                            currentRaceAffinity(profile)
                            careerSection(profile)
                            bestVenueSection(profile)
                            surfaceDistanceSection(profile)
                            recentRaceSection(profile)
                            dataPolicyCard(profile)
                        }
                        .padding(20)
                    }
                    .background(Color(uiColor: .systemGroupedBackground))
                } else {
                    VStack(spacing: 16) {
                        ContentUnavailableView(
                            identityIssue ? "馬を特定できませんでした" : errorMessage == nil ? "過去走データが未収録です" : "馬データを取得できませんでした",
                            systemImage: identityIssue ? "person.crop.circle.badge.questionmark" : errorMessage == nil ? "doc.text.magnifyingglass" : "wifi.exclamationmark",
                            description: Text(errorMessage ?? "新馬や履歴未収録の馬はプロフィールを表示できません。データが追加されると表示できます。")
                        )
                        Button("再取得") {
                            Task { await loadProfile(force: true) }
                        }
                        .buttonStyle(.borderedProminent)
                        .padding(.bottom, 24)
                    }
                }
            }
            .navigationTitle(horse.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("閉じる") { dismiss() }
                }
            }
            .task(id: identity) {
                await loadProfile()
            }
        }
    }

    @MainActor
    private func loadProfile(force: Bool = false) async {
        let request = UUID()
        activeRequest = request
        isLoading = true
        errorMessage = nil
        identityIssue = false
        defer { if activeRequest == request { isLoading = false } }
        do {
            let loaded = try await store.profile(for: identity, force: force)
            guard !Task.isCancelled, activeRequest == request else { return }
            profile = loaded
        } catch {
            guard !Task.isCancelled, activeRequest == request else { return }
            profile = nil
            identityIssue = error is KeibaAIHorseIdentityError
            errorMessage = identityIssue ? error.localizedDescription
                : "通信状態を確認して再取得してください。馬DBの更新中にも、一時的に取得できないことがあります。"
        }
    }

    private func profileHeader(_ p: KeibaAIHorseProfile) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 14) {
                Text("\(horse.number)")
                    .font(.title2.weight(.heavy).monospacedDigit())
                    .frame(width: 46, height: 46)
                    .background(.blue.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
                    .foregroundStyle(.blue)

                VStack(alignment: .leading, spacing: 4) {
                    Text(p.name)
                        .font(.title2.weight(.heavy))
                    HStack(spacing: 10) {
                        if let year = p.birthYear { Text("\(String(year))年生まれ") }
                        if let age = p.currentAge { Text("最終出走時 \(age)歳") }
                        if let jockey = p.latestJockey { Text("最新騎手 \(jockey)") }
                        if let trainer = p.latestTrainer { Text("調教師 \(trainer)") }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }

                Spacer()
            }

            if let latest = p.latestDate {
                Text("馬DB最終履歴 \(latest) ・ 今回 \(race.venue) \(race.raceNumber)R \(race.course)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(18)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }

    private func currentRaceAffinity(_ p: KeibaAIHorseProfile) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("今回の競馬場との相性", systemImage: "building.columns.fill")
                    .font(.title3.weight(.bold))
                Spacer()
                Text("予測モデルには未採用")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.orange)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(.orange.opacity(0.10), in: Capsule())
            }

            if let stat = currentVenueStat {
                HStack(spacing: 12) {
                    KeibaAIProfileMetric(
                        title: "適性参考値",
                        value: stat.smoothedPerformanceScore.map { String(format: "%.1f", $0) } ?? "--",
                        note: affinityLabel(stat.smoothedPerformanceScore),
                        tint: affinityTint(stat.smoothedPerformanceScore)
                    )
                    KeibaAIProfileMetric(
                        title: "出走数",
                        value: "\(stat.starts)走",
                        note: "信頼度 \(stat.confidence ?? "--")",
                        tint: .blue
                    )
                    KeibaAIProfileMetric(
                        title: "複勝率",
                        value: pctText(stat.top3RatePct),
                        note: stat.surface.map { "\(race.venue)\($0)" } ?? race.venue,
                        tint: .green
                    )
                }

                Text("適性参考値は、過去の着順を頭数差で補正し、出走数が少ない場合は50点へ寄せています。1走だけの好走で『得意』と断定しない設計です。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                KeibaAIInfoCard(
                    icon: "questionmark.circle",
                    title: "\(race.venue)の過去走なし",
                    message: "この競馬場での履歴がないため、競馬場相性は未判定です。未知をマイナス評価にはしません。"
                )
            }
        }
        .padding(18)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }

    private func careerSection(_ p: KeibaAIHorseProfile) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("収録済みの成績")
                .font(.title3.weight(.bold))

            HStack(spacing: 12) {
                KeibaAIProfileMetric(title: "出走", value: "\(p.career.starts)", note: "平地JRA", tint: .blue)
                KeibaAIProfileMetric(title: "1着", value: "\(p.career.wins)", note: pctText(p.career.winRatePct), tint: .orange)
                KeibaAIProfileMetric(title: "3着内", value: "\(p.career.top3)", note: pctText(p.career.top3RatePct), tint: .green)
                KeibaAIProfileMetric(title: "平均着順", value: p.career.averageFinish.map { String(format: "%.2f", $0) } ?? "--", note: "参考", tint: .purple)
            }
        }
        .padding(18)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }

    private func bestVenueSection(_ p: KeibaAIHorseProfile) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("得意競馬場候補（参考）")
                .font(.title3.weight(.bold))

            Text("点数は、着順を頭数に合わせて0〜100点にした過去成績の参考値です。出走が少ないほど50点に近づけています。相手の強さや距離の違いは調整していません。")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            let rows = p.bestVenues.isEmpty ? Array(p.venueStats.prefix(3)) : Array(p.bestVenues.prefix(3))
            if rows.isEmpty {
                Text("競馬場別の履歴がまだありません。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(rows) { stat in
                    HStack(spacing: 12) {
                        Text(stat.venue ?? "--")
                            .font(.headline)
                            .frame(width: 52, alignment: .leading)

                        ProgressView(value: min(max((stat.smoothedPerformanceScore ?? 50) / 100.0, 0), 1))
                            .frame(maxWidth: .infinity)

                        Text(stat.smoothedPerformanceScore.map { String(format: "%.1f点", $0) } ?? "--")
                            .font(.subheadline.bold().monospacedDigit())
                            .frame(width: 62, alignment: .trailing)

                        Text("\(stat.starts)走 / \(stat.confidence ?? "--")")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(width: 78, alignment: .trailing)
                    }
                }
            }
            Text("履歴の目安：1走＝低、2〜4走＝中、5走以上＝高。表示点数は勝率ではありません。")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(18)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }

    private func surfaceDistanceSection(_ p: KeibaAIHorseProfile) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("芝・ダート / 距離傾向")
                .font(.title3.weight(.bold))

            if !p.surfaceStats.isEmpty {
                HStack(spacing: 10) {
                    ForEach(Array(p.surfaceStats.prefix(3))) { stat in
                        KeibaAIProfileMetric(
                            title: stat.surface ?? "--",
                            value: stat.smoothedPerformanceScore.map { String(format: "%.1f", $0) } ?? "--",
                            note: "\(stat.starts)走・複勝 \(pctText(stat.top3RatePct))",
                            tint: .blue
                        )
                    }
                }
            }

            if !p.distanceStats.isEmpty {
                VStack(spacing: 8) {
                    ForEach(Array(p.distanceStats.prefix(5))) { stat in
                        HStack {
                            Text(stat.distanceBand ?? "--")
                                .font(.subheadline.weight(.semibold))
                            Spacer()
                            Text("適性 \(stat.smoothedPerformanceScore.map { String(format: "%.1f", $0) } ?? "--")")
                                .font(.caption.monospacedDigit())
                            Text("\(stat.starts)走")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .frame(width: 44, alignment: .trailing)
                        }
                    }
                }
            }
        }
        .padding(18)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }

    private func recentRaceSection(_ p: KeibaAIHorseProfile) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("直近のレース")
                .font(.title3.weight(.bold))

            ForEach(p.recentRaces.prefix(8)) { r in
                HStack(spacing: 10) {
                    Text(r.finish.map { "\($0)着" } ?? "--")
                        .font(.headline.monospacedDigit())
                        .frame(width: 42, alignment: .leading)

                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(r.venue ?? "--") \(r.raceNumber.map { "\($0)R" } ?? "")  \(r.surface ?? "")\(r.distance.map(String.init) ?? "")m")
                            .font(.subheadline.weight(.semibold))
                        Text([r.date, r.raceClass, r.jockey].compactMap { $0 }.joined(separator: " ・ "))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    VStack(alignment: .trailing, spacing: 2) {
                        Text(r.popularity.map { "\($0)人気" } ?? "--")
                            .font(.caption)
                        Text(r.winOdds.map { String(format: "%.1f倍", $0) } ?? "")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 5)

                if r.id != p.recentRaces.prefix(8).last?.id {
                    Divider()
                }
            }
        }
        .padding(18)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }

    private func dataPolicyCard(_ p: KeibaAIHorseProfile) -> some View {
        KeibaAIInfoCard(
            icon: "checkmark.shield.fill",
            title: "馬DBの使い方",
            message: "競馬場・芝ダート・距離の傾向は、馬を理解するための参考表示です。競馬場適性を追加した41特徴量モデルは改善が小さく確証が取れていないため、本番AIは現在も37特徴量のままです。"
        )
    }

    private func pctText(_ value: Double?) -> String {
        guard let value else { return "--" }
        return String(format: "%.1f%%", value)
    }

    private func affinityLabel(_ score: Double?) -> String {
        guard let score else { return "未判定" }
        if score >= 60 { return "得意傾向" }
        if score >= 55 { return "やや良好" }
        if score >= 45 { return "標準付近" }
        return "低め傾向"
    }

    private func affinityTint(_ score: Double?) -> Color {
        guard let score else { return .secondary }
        if score >= 60 { return .green }
        if score >= 55 { return .blue }
        if score >= 45 { return .orange }
        return .pink
    }
}

private struct KeibaAIProfileMetric: View {
    let title: String
    let value: String
    let note: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title3.weight(.bold).monospacedDigit())
                .foregroundStyle(tint)
            Text(note)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
    }
}

// ============================================================
// MARK: - Future Feature Placeholders
// ============================================================

private struct KeibaAIStylePlaceholder: View {
    let title: String
    let icon: String

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(.blue)

            Text(title)
                .font(.subheadline.weight(.bold))

            Text("—")
                .font(.headline)
                .foregroundStyle(.tertiary)
        }
        .padding(14)
        .frame(maxWidth: .infinity)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 16))
    }
}

private struct KeibaAIInfoCard: View {
    let icon: String
    let title: String
    let message: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(.blue)
                .frame(width: 38, height: 38)
                .background(.blue.opacity(0.09), in: RoundedRectangle(cornerRadius: 10))

            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                    .font(.headline)

                Text(message)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
    }
}

private struct KeibaAIFormationCard: View {
    let reference: KeibaAIFormationReference
    let researchHorses: [KeibaAIResearchHorse]
    let horses: [Horse]
    let onSelectHorse: (Horse) -> Void

    private var ordered: [KeibaAIResearchHorse] {
        reference.orderedHorseNumbers.compactMap { number in
            researchHorses.first { $0.number == number }
        }
    }

    private func numberList(_ numbers: [Int]) -> String {
        numbers.isEmpty ? "該当なし" : numbers.map { String($0) }.joined(separator: "・") + "番"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("初角の参考隊列・研究", systemImage: "list.number")
                .font(.title3.weight(.bold))
            Text("過去走の平均位置を、前→後の順に並べます。今回の並びを確定するものではありません。同じ平均値の馬は馬番順です。")
                .font(.caption).foregroundStyle(.secondary)

            Text("\(reference.minimumPastRaces)走以上の履歴：\(reference.knownHorses) / \(reference.totalHorses)頭")
                .font(.subheadline.weight(.semibold))

            if reference.status == "reference_order" {
                ForEach(ordered) { horse in
                    Button {
                        if let selected = horses.first(where: { $0.number == horse.number }) {
                            onSelectHorse(selected)
                        }
                    } label: {
                        HStack(spacing: 12) {
                            Text("\(horse.number)")
                                .font(.subheadline.weight(.bold).monospacedDigit())
                                .frame(width: 32, height: 32)
                                .background(.blue.opacity(0.10), in: Circle())
                            VStack(alignment: .leading, spacing: 3) {
                                Text(horse.name).font(.subheadline.weight(.semibold))
                                if let history = horse.initialPositionHistory {
                                    Text("\(history.band) ・ 過去\(history.sampleRaces)走")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        .foregroundStyle(.primary)
                        .padding(10)
                        .background(.blue.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
                    }
                    .buttonStyle(.plain)
                }
                if reference.unknownHorses > 0 {
                    Text("履歴不足の\(reference.unknownHorses)頭は参考順に含めていません。最後方と判断したわけではありません。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let validation = reference.validation2025, let rate = validation.leaderTop3Pct {
                    Text("2025年の対象\(validation.eligibleRaces)レースでは、実際の初角先頭馬が参考順の上位3頭に\(String(format: "%.1f", rate))%含まれました。1着馬の的中率ではありません。")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            } else if reference.status == "no_corner" {
                Text("直線コースなど、初角の隊列を評価できないレースは表示対象外です。")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else if reference.status == "validation_pending" {
                Text("参考隊列の検証結果は公開待ちです。更新ボタンで再取得してください。")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                Text("履歴不足のため、レース全体の参考隊列を保留します。過去\(reference.minimumPastRaces)走以上の履歴が出走馬の\(Int(reference.minimumCoveragePct))%以上、かつ3頭以上必要です。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }

            if reference.status != "no_corner" {
                Divider()
                Text("履歴のある\(reference.knownHorses)頭の傾向")
                    .font(.caption.weight(.semibold))
                Text("逃げ傾向 \(reference.escapeTrendHorseNumbers.count)頭：\(numberList(reference.escapeTrendHorseNumbers))")
                    .font(.caption)
                Text("前方傾向 \(reference.frontTrendHorseNumbers.count)頭：\(numberList(reference.frontTrendHorseNumbers))")
                    .font(.caption)
                Text("過去走の逃げ、または逃げ＋先行の頻度が50%以上の馬を数えます。今回の逃げ馬の数や戦法の確率ではありません。")
                    .font(.caption2).foregroundStyle(.secondary)
            }

            Divider()
            Label("ペース予想は検証待ち", systemImage: "clock")
                .font(.subheadline.weight(.semibold))
            Text(reference.paceForecast.reason)
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(16)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
    }
}

private enum KeibaAIBudgetAmount {
    static let presets = [1_000, 3_000, 5_000, 10_000]

    static func isValid(_ amount: Int) -> Bool {
        amount >= 0 && amount % 100 == 0
    }

    static func parse(_ text: String) -> Int? {
        let digits = text.folding(options: .widthInsensitive, locale: Locale(identifier: "ja_JP"))
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: ",", with: "")
        guard !digits.isEmpty,
              digits.allSatisfy({ $0 >= "0" && $0 <= "9" }),
              let amount = Int(digits), isValid(amount) else { return nil }
        return amount
    }

    static func label(_ amount: Int) -> String {
        amount.formatted(.number.locale(Locale(identifier: "ja_JP"))) + "円"
    }

    static func adjusted(_ text: String, by delta: Int) -> Int? {
        guard let current = parse(text) else { return nil }
        let (next, overflow) = current.addingReportingOverflow(delta)
        return !overflow && isValid(next) ? next : nil
    }
}

@MainActor
private final class KeibaAIBudgetStore: ObservableObject {
    @Published private(set) var perRaceYen: Int
    @Published private(set) var dailyLimitYen: Int?

    private let defaults: UserDefaults
    private static let raceKey = "KeibaAI.budget.perRaceYen.v1"
    private static let dayKey = "KeibaAI.budget.dailyLimitYen.v1"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let race = defaults.object(forKey: Self.raceKey) as? Int
        perRaceYen = race.flatMap { KeibaAIBudgetAmount.isValid($0) ? $0 : nil } ?? 3_000
        let day = defaults.object(forKey: Self.dayKey) as? Int
        dailyLimitYen = day.flatMap { KeibaAIBudgetAmount.isValid($0) ? $0 : nil }
    }

    func save(perRace: Int, dailyLimit: Int?) {
        guard KeibaAIBudgetAmount.isValid(perRace),
              dailyLimit.map({ KeibaAIBudgetAmount.isValid($0) }) ?? true else { return }
        perRaceYen = perRace
        dailyLimitYen = dailyLimit
        defaults.set(perRace, forKey: Self.raceKey)
        if let dailyLimit {
            defaults.set(dailyLimit, forKey: Self.dayKey)
        } else {
            defaults.removeObject(forKey: Self.dayKey)
        }
    }
}

private struct KeibaAIBudgetCard: View {
    @ObservedObject var store: KeibaAIBudgetStore
    @State private var showEditor = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("予算設定", systemImage: "yensign.circle")
                    .font(.headline)
                Spacer()
                Button("変更") { showEditor = true }
                    .buttonStyle(.bordered)
                    .accessibilityLabel("予算を変更")
            }

            Divider()

            LabeledContent("1レースの予算") {
                Text(KeibaAIBudgetAmount.label(store.perRaceYen))
                    .fontWeight(.semibold).monospacedDigit()
            }
            LabeledContent("1日の上限") {
                Text(store.dailyLimitYen.map { KeibaAIBudgetAmount.label($0) } ?? "未設定")
                    .fontWeight(.semibold).monospacedDigit()
            }

            if store.perRaceYen == 0 || store.dailyLimitYen == 0 {
                Label("0円の設定は見送りの目安です", systemImage: "pause.circle")
                    .font(.caption).foregroundStyle(.secondary)
            } else if let daily = store.dailyLimitYen, daily < store.perRaceYen {
                Text("1日の上限が1レースの予算より小さく設定されています。")
                    .font(.caption).foregroundStyle(.orange)
            }

            Text("設定額を使い切る必要はありません。見送りや残額を認める目安として保存します。購入実績・当日の使用額はまだ管理していません。")
                .font(.caption).foregroundStyle(.secondary)
            Label("購入金額の自動配分は待機中", systemImage: "lock.fill")
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
        }
        .padding(16)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
        .sheet(isPresented: $showEditor) {
            KeibaAIBudgetEditor(store: store)
        }
    }
}

private struct KeibaAIBudgetEditor: View {
    @ObservedObject var store: KeibaAIBudgetStore
    @Environment(\.dismiss) private var dismiss
    @State private var perRaceText: String
    @State private var dailyText: String
    @State private var dailyLimitEnabled: Bool
    @FocusState private var focusedField: AmountField?

    private enum AmountField: Hashable {
        case perRace, daily
    }

    init(store: KeibaAIBudgetStore) {
        self.store = store
        _perRaceText = State(initialValue: String(store.perRaceYen))
        _dailyText = State(initialValue: String(store.dailyLimitYen ?? 10_000))
        _dailyLimitEnabled = State(initialValue: store.dailyLimitYen != nil)
    }

    private var canSave: Bool {
        KeibaAIBudgetAmount.parse(perRaceText) != nil &&
        (!dailyLimitEnabled || KeibaAIBudgetAmount.parse(dailyText) != nil)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    amountInput("1レースの予算", text: $perRaceText, field: .perRace)
                } header: {
                    Text("1レースの予算")
                } footer: {
                    Text("すべてのレースに共通の目安です。0円も設定できます。")
                }

                Section {
                    Toggle("1日の上限を設定する", isOn: $dailyLimitEnabled)
                    if dailyLimitEnabled {
                        amountInput("1日の上限", text: $dailyText, field: .daily)
                    }
                } header: {
                    Text("1日の上限")
                } footer: {
                    Text("1レースの予算とは別に保存します。購入履歴や残り予算の集計にはまだ接続していません。")
                }

                Section {
                    if dailyLimitEnabled,
                       let race = KeibaAIBudgetAmount.parse(perRaceText),
                       let day = KeibaAIBudgetAmount.parse(dailyText), day < race {
                        Text("1日の上限が1レースの予算より小さくなっています。各設定額はそのまま保存できます。")
                            .font(.footnote).foregroundStyle(.orange)
                    }
                    Text("全額を使う必要はありません。設定はこの端末に保存します。購入金額の自動配分は引き続き待機中です。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("予算を変更")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("キャンセル") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        guard let race = KeibaAIBudgetAmount.parse(perRaceText) else { return }
                        let daily = dailyLimitEnabled ? KeibaAIBudgetAmount.parse(dailyText) : nil
                        guard !dailyLimitEnabled || daily != nil else { return }
                        store.save(perRace: race, dailyLimit: daily)
                        dismiss()
                    }
                    .disabled(!canSave)
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("閉じる") { focusedField = nil }
                }
            }
            .onChange(of: dailyLimitEnabled) { _, enabled in
                if !enabled && focusedField == .daily { focusedField = nil }
            }
        }
    }

    private func amountInput(_ title: String, text: Binding<String>, field: AmountField) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                TextField("金額", text: text)
                    .keyboardType(.numberPad)
                    .focused($focusedField, equals: field)
                    .font(.title3.monospacedDigit())
                    .accessibilityLabel(title + "を円で入力")
                Text("円").foregroundStyle(.secondary)
            }

            HStack {
                Button {
                    if let amount = KeibaAIBudgetAmount.adjusted(text.wrappedValue, by: -100) {
                        text.wrappedValue = String(amount)
                    }
                } label: {
                    Label("100円", systemImage: "minus")
                }
                .disabled(KeibaAIBudgetAmount.adjusted(text.wrappedValue, by: -100) == nil)
                .accessibilityLabel(title + "を100円減らす")

                Spacer()

                Button {
                    if let amount = KeibaAIBudgetAmount.adjusted(text.wrappedValue, by: 100) {
                        text.wrappedValue = String(amount)
                    }
                } label: {
                    Label("100円", systemImage: "plus")
                }
                .disabled(KeibaAIBudgetAmount.adjusted(text.wrappedValue, by: 100) == nil)
                .accessibilityLabel(title + "を100円増やす")
            }
            .buttonStyle(.bordered)

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                ForEach(KeibaAIBudgetAmount.presets, id: \.self) { amount in
                    Button {
                        text.wrappedValue = String(amount)
                        focusedField = nil
                    } label: {
                        Text(KeibaAIBudgetAmount.label(amount))
                            .font(.subheadline.monospacedDigit())
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(KeibaAIBudgetAmount.parse(text.wrappedValue) == amount ? .blue : .secondary)
                    .accessibilityLabel(title + "を" + KeibaAIBudgetAmount.label(amount) + "に設定")
                }
            }

            if KeibaAIBudgetAmount.parse(text.wrappedValue) == nil {
                Text("0円以上・100円単位の整数を入力してください。")
                    .font(.caption).foregroundStyle(.red)
            } else {
                Text("100円単位で任意の金額を入力できます。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}


// ============================================================
// MARK: - Forward Performance
// ============================================================

private struct KeibaAIPerformanceView: View {
    let data: KeibaAIPerformanceData?
    let selectedPeriodID: String

    private var selectedPeriod: KeibaAIPerformancePeriod? {
        guard let data else { return nil }
        return data.periods.first(where: { $0.id == selectedPeriodID }) ?? data.periods.first
    }

    private func periodLabel(_ period: KeibaAIPerformancePeriod) -> String {
        period.id == "all" ? "実運用開始から" : period.label.replacingOccurrences(of: "前向き検証 全期間", with: "実運用開始から")
    }

    private func pct(_ value: Double?) -> String {
        guard let value else { return "--" }
        return String(format: "%.1f%%", value)
    }

    private func num(_ value: Double?, digits: Int = 2) -> String {
        guard let value else { return "--" }
        return String(format: "%.\(digits)f", value)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("実運用成績", systemImage: "chart.line.uptrend.xyaxis")
                            .font(.largeTitle.weight(.heavy))

                        Text("発走前に保存した予測だけを、あとから確定結果と突合して集計します。")
                            .font(.title3)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    if let data {
                        VStack(alignment: .trailing, spacing: 3) {
                            Text("更新")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            Text(data.generatedAtJST)
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                if let data, let period = selectedPeriod {
                    KeibaAIPerformanceNotice(notes: data.notes)

                    VStack(alignment: .leading, spacing: 8) {
                        Text(periodLabel(period))
                            .font(.title2.weight(.bold))
                        if let start = period.startDate, let end = period.endDate {
                            Text(start == end ? start : "\(start) 〜 \(end)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    let m = period.metrics

                    LazyVGrid(
                        columns: [
                            GridItem(.flexible(), spacing: 12),
                            GridItem(.flexible(), spacing: 12),
                            GridItem(.flexible(), spacing: 12)
                        ],
                        spacing: 12
                    ) {
                        KeibaAIPerformanceMetricCard(title: "突合レース", value: "\(m.races)", detail: "結果取得済み")
                        KeibaAIPerformanceMetricCard(title: "AI1位 勝率", value: pct(m.aiTopWinRatePct), detail: "\(m.aiTopWins)勝")
                        KeibaAIPerformanceMetricCard(title: "AI1位 3着内率", value: pct(m.aiTopTop3RatePct), detail: "\(m.aiTopTop3)回")
                        KeibaAIPerformanceMetricCard(title: "市場1位 勝率", value: pct(m.marketTopWinRatePct), detail: "\(m.marketTopWins)勝 / 比較基準")
                        KeibaAIPerformanceMetricCard(title: "勝ち馬のAI平均順位", value: num(m.meanWinnerAiRank), detail: "小さいほど上位評価")
                        KeibaAIPerformanceMetricCard(title: "AI上位3頭の一致", value: num(m.meanTop3Overlap), detail: "実際の3着内との平均一致頭数")
                    }

                    if let ref = m.referenceAiTopFlatReturnIndex {
                        HStack(spacing: 12) {
                            Image(systemName: "info.circle.fill")
                                .foregroundStyle(.orange)
                            VStack(alignment: .leading, spacing: 3) {
                                Text("参考：AI1位の単勝回収指数 \(String(format: "%.3f", ref))")
                                    .font(.subheadline.weight(.semibold))
                                Text("AI1位を毎レース1単位と仮定した研究用の数字です。現在の勝負度や馬券推奨の成績ではありません。")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(14)
                        .background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
                    }

                    if selectedPeriodID == "all" || selectedPeriodID == "last7" {
                        KeibaAIPerformanceBreakdown(
                            title: "勝負度別（正式版）",
                            rows: data.byBattleGrade.map {
                                ($0.grade, $0.metrics.races, pct($0.metrics.aiTopWinRatePct), pct($0.metrics.aiTopTop3RatePct))
                            }
                        )

                        KeibaAIPerformanceBreakdown(
                            title: "荒れ度別（正式版・保存済みレース）",
                            rows: data.byChaosLevel.map {
                                ($0.stars, $0.metrics.races, pct($0.metrics.aiTopWinRatePct), pct($0.metrics.aiTopTop3RatePct))
                            }
                        )
                    }

                    VStack(alignment: .leading, spacing: 12) {
                        Text("最近の突合レース")
                            .font(.title3.weight(.bold))

                        ForEach(data.recentRaces.prefix(16)) { race in
                            HStack(spacing: 12) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("\(race.venue ?? "") \(race.raceNumber)R  \(race.raceName ?? "")")
                                        .font(.subheadline.weight(.semibold))
                                    Text("\(race.raceDate)  AI1位 \(race.aiTopNumber) \(race.aiTopName)")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }

                                Spacer()

                                VStack(alignment: .trailing, spacing: 3) {
                                    Text(race.aiTopFinish.map { "\($0)着" } ?? "--")
                                        .font(.headline.monospacedDigit())
                                    Text("勝馬AI \(race.winnerAiRank)位")
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .padding(.vertical, 8)

                            Divider()
                        }
                    }
                    .padding(18)
                    .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
                } else {
                    ContentUnavailableView(
                        "成績データを準備中",
                        systemImage: "chart.line.uptrend.xyaxis",
                        description: Text("結果の自動突合が進むと、ここに成績が表示されます。")
                    )
                    .frame(maxWidth: .infinity, minHeight: 420)
                }
            }
            .padding(28)
            .frame(maxWidth: 1050, alignment: .leading)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("成績")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct KeibaAIPerformanceNotice: View {
    let notes: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("まだ標本は少ないので、短期成績だけでモデルを良し悪し判定しません", systemImage: "checkmark.shield.fill")
                .font(.headline)

            ForEach(Array(notes.enumerated()), id: \.offset) { _, note in
                Text("・\(note)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 18))
    }
}

private struct KeibaAIPerformanceMetricCard: View {
    let title: String
    let value: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title2.weight(.heavy).monospacedDigit())
            Text(detail)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 92, alignment: .leading)
        .padding(15)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
    }
}

private struct KeibaAIPerformanceBreakdown: View {
    let title: String
    let rows: [(String, Int, String, String)]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.title3.weight(.bold))

            HStack {
                Text("区分").frame(maxWidth: .infinity, alignment: .leading)
                Text("レース").frame(width: 70, alignment: .trailing)
                Text("AI1位勝率").frame(width: 90, alignment: .trailing)
                Text("3着内率").frame(width: 90, alignment: .trailing)
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)

            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                HStack {
                    Text(row.0)
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text("\(row.1)")
                        .frame(width: 70, alignment: .trailing)
                    Text(row.2)
                        .frame(width: 90, alignment: .trailing)
                    Text(row.3)
                        .frame(width: 90, alignment: .trailing)
                }
                .font(.subheadline.monospacedDigit())

                Divider()
            }
        }
        .padding(18)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))
    }
}

// ============================================================
// MARK: - Legend / Design Philosophy
// ============================================================

private struct KeibaAIGuideView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 8) {
                    Label("凡例・KeibaAIの設計思想", systemImage: "book.closed.fill")
                        .font(.largeTitle.weight(.heavy))

                    Text("数字を増やすためではなく、レースを『選ぶ・理解する・後で検証する』ためのアプリです。")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }

                KeibaAIGuideNotice()

                KeibaAIGuideSection(
                    icon: "waveform.path.ecg",
                    title: "荒れ度",
                    subtitle: "レースがどれくらい混戦・不確実になりそうか"
                ) {
                    VStack(alignment: .leading, spacing: 10) {
                        KeibaAIGuideScaleRow(mark: "★☆☆☆☆", title: "比較的堅め", detail: "上位馬がはっきりしている傾向")
                        KeibaAIGuideScaleRow(mark: "★★★☆☆", title: "中間", detail: "上位の差が小さく、複数馬にチャンス")
                        KeibaAIGuideScaleRow(mark: "★★★★★", title: "かなり混戦", detail: "本命の信頼度が低く、人気薄も入りやすい傾向")

                        Text("荒れ度v1は2024年で区切りを固定し、別に残した2025年データで検証しました。本命の弱さ、市場のばらつき、上位人気の接近、AIと市場の不一致、人気薄のTop3シグナル、頭数を組み合わせます。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                KeibaAIGuideSection(
                    icon: "target",
                    title: "勝負度",
                    subtitle: "『当たりそう』ではなく、AIから見て市場とのズレに注目できるか"
                ) {
                    VStack(alignment: .leading, spacing: 10) {
                        KeibaAIGuideGradeRow(grade: "S", meaning: "評価差が非常に大きい", detail: "最上位区分。利益保証ではなく実運用で確認を続ける")
                        KeibaAIGuideGradeRow(grade: "A", meaning: "注目度が高い", detail: "AI評価と市場評価に比較的大きな差がある")
                        KeibaAIGuideGradeRow(grade: "B", meaning: "候補", detail: "一定の差はあるが、慎重に見る")
                        KeibaAIGuideGradeRow(grade: "C", meaning: "弱め", detail: "差が小さく、積極的な根拠は少ない")
                        KeibaAIGuideGradeRow(grade: "見送り", meaning: "無理に選ばない", detail: "全レースに参加しない、という設計")

                        Text("勝負度v1は候補馬のAI÷市場・確率差・AI確率を中心に評価します。荒れ度が高い＝勝負度が高い、ではありません。A/Sも利益を保証する区分ではありません。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                KeibaAIGuideSection(
                    icon: "chart.bar.doc.horizontal",
                    title: "主なAI表示",
                    subtitle: "市場とAIを並べて見るための数字"
                ) {
                    VStack(alignment: .leading, spacing: 11) {
                        KeibaAIGuideDefinition(term: "市場", text: "単勝オッズから作った基準の勝率。市場がその馬をどれくらい評価しているかの目安です。")
                        KeibaAIGuideDefinition(term: "AI勝率", text: "KeibaAIが推定した1着になる確率。レース内で合計が約100%になるよう調整します。")
                        KeibaAIGuideDefinition(term: "AI 2着内 / 3着内", text: "2着以内・3着以内に入る推定確率。単勝とは別モデルで補正しています。")
                        KeibaAIGuideDefinition(term: "AI÷市場", text: "AI評価 ÷ 市場評価。1.00より大きいほど、市場よりAIが高く見ている馬です。大きいだけで買いを意味しません。")
                        KeibaAIGuideDefinition(term: "期待指数", text: "10分前の単勝オッズとAI勝率を掛け合わせた参考指数。100超は理論上の妙味候補ですが、確率誤差やオッズ変動があるため利益を保証しません。")
                        KeibaAIGuideDefinition(term: "大穴候補", text: "v1は市場順位6番手以下・単勝8倍以上・AI勝率1%以上・AI÷市場1.20以上。単なる高オッズ馬とは区別します。歴史検証と約10分前運用の差は実運用成績で確認します。")
                        KeibaAIGuideDefinition(term: "大穴v2比較", text: "大穴画面で研究表示へ切り替えると、AI勝率・保存オッズ・理論期待倍率を比較できます。正式なv1候補条件や購入金額の配分は変えません。")
                        KeibaAIGuideDefinition(term: "脚質傾向", text: "レース当日より前の最大6走から、逃げ・先行・差し・追込の割合を表示します。割合は過去の頻度で、次走の戦法の確率ではありません。参考度は出走数と傾向のまとまりの目安です。")
                        KeibaAIGuideDefinition(term: "参考隊列", text: "過去の初角の平均位置を前→後に並べた研究表示です。過去3走以上の履歴が出走馬の80%以上あるレースに限ります。直線コースは除き、履歴不足の馬は順に含めません。レース全体の実際の並びを当てた表示ではありません。")
                        KeibaAIGuideDefinition(term: "競馬場の参考点", text: "競馬場ごとの着順を頭数で補正し、0〜100点にまとめます。少ない出走では50点へ近づける補正をします。勝率を表す数字ではなく、相手の強さや距離の違いは調整していません。")
                        KeibaAIGuideDefinition(term: "ワイド評価", text: "2頭が両方3着以内に入る同時確率。発走前実オッズ取得後は、AI的中率と保守的期待値を合わせた「実戦優先度」で並べます。同じ期待値なら、より当たりやすい組み合わせを上位にします。Kelly値は順位付け専用で購入金額には使いません。")
                        KeibaAIGuideDefinition(term: "予算設定", text: "馬券プランで1レースの予算と1日の上限を別々に変更できます。100円単位の任意入力・プリセットに対応し、この端末に保存します。設定額を使い切る必要はなく、購入実績の集計や自動配分にはまだ接続していません。")
                    }
                }

                KeibaAIGuideSection(
                    icon: "clock.arrow.circlepath",
                    title: "レース状態",
                    subtitle: "予定表から結果検証まで"
                ) {
                    VStack(alignment: .leading, spacing: 10) {
                        KeibaAIStatusLegend(label: "未予測", icon: "clock.fill", detail: "予定表にはあるが、まだ発走前AI予測を保存していないレース")
                        KeibaAIStatusLegend(label: "AI予測済", icon: "checkmark.circle.fill", detail: "発走約8〜12分前の情報で予測を保存したレース")
                        KeibaAIStatusLegend(label: "結果取得済 / 発走済", icon: "flag.checkered", detail: "レース後。保存した予測と実着順を『結果比較』で照合します")
                        KeibaAIStatusLegend(label: "対象外", icon: "nosign", detail: "現在のモデルでは予測しないレース")
                    }
                }

                KeibaAIGuideSection(
                    icon: "nosign",
                    title: "現在の予測対象",
                    subtitle: "対象を広げるより、同じ条件で正しく検証することを優先"
                ) {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("対象：JRAの平地・芝 / ダート", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                        Label("対象外：障害レース", systemImage: "xmark.circle.fill")
                            .foregroundStyle(.secondary)

                        Text("障害競走は距離・飛越・展開など平地とは競走特性が大きく異なります。現在の平地モデルを流用せず、専用データと専用モデルを検証できるまで予測を出しません。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                KeibaAIGuideSection(
                    icon: "checkmark.shield.fill",
                    title: "KeibaAIが大切にすること",
                    subtitle: "予測精度だけでなく、検証可能な作りにする"
                ) {
                    VStack(alignment: .leading, spacing: 12) {
                        KeibaAIDesignPrinciple(number: "1", title: "未来の情報を混ぜない", text: "着順・払戻などレース後に分かる情報は、発走前予測の入力に使いません。")
                        KeibaAIDesignPrinciple(number: "2", title: "予測した時点の数字を保存する", text: "約10分前のオッズとAI予測を残し、後から都合よく書き換えません。")
                        KeibaAIDesignPrinciple(number: "3", title: "必ず結果と突合する", text: "AI順位、実着順、最終オッズを比較し、良かった予測だけでなく外れも蓄積します。")
                        KeibaAIDesignPrinciple(number: "4", title: "全レースを買う前提にしない", text: "『見送り』を重要な判断として扱い、AIが得意そうなレースを選ぶ方向を目指します。")
                        KeibaAIDesignPrinciple(number: "5", title: "回収率は実運用で確認する", text: "過去データで良く見えても利益確定とは考えません。発走前に保存したライブ予測だけを使って結果を確認します。")
                        KeibaAIDesignPrinciple(number: "6", title: "馬券プランは検証後に有効化", text: "ワイドなどの同時確率と実際のオッズを確認してから、予算配分機能へ進みます。")
                    }
                }

                KeibaAIGuideSection(
                    icon: "arrow.forward.circle.fill",
                    title: "『実運用成績』と前向き検証",
                    subtitle: "アプリでは分かりやすい言葉を優先"
                ) {
                    Text("前向き検証とは、レースが始まる前に保存した予測を、終わった後の結果と照合する検証方法です。『全期間』という意味ではありません。成績画面の『実運用開始から』は、この方式で保存できた日以降の全データを意味します。")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                KeibaAIGuideSection(
                    icon: "exclamationmark.triangle.fill",
                    title: "利用上の注意",
                    subtitle: "AIは判断材料であり、結果の保証ではありません"
                ) {
                    Text("KeibaAIの確率・荒れ度・勝負度・期待指数は推定値です。的中や利益を保証するものではありません。特に人気薄は結果のブレが大きいため、将来の馬券機能でも予算上限と見送り判断を重視します。")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(28)
            .frame(maxWidth: 1000, alignment: .leading)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("凡例")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct KeibaAIGuideNotice: View {
    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: "lightbulb.max.fill")
                .font(.title2)
                .foregroundStyle(.blue)
                .frame(width: 36)

            VStack(alignment: .leading, spacing: 5) {
                Text("基本方針")
                    .font(.headline)
                Text("市場を無視して『当てるAI』を目指すのではなく、市場を基準にAIとの違いを見つけ、レースを選び、結果で検証し続けます。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(18)
        .background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 20))
    }
}

private struct KeibaAIGuideSection<Content: View>: View {
    let icon: String
    let title: String
    let subtitle: String
    @ViewBuilder let content: Content

    init(icon: String, title: String, subtitle: String, @ViewBuilder content: () -> Content) {
        self.icon = icon
        self.title = title
        self.subtitle = subtitle
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 11) {
                Image(systemName: icon)
                    .font(.headline)
                    .foregroundStyle(.blue)
                    .frame(width: 26, height: 26)
                    .background(.blue.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.title3.weight(.bold))
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            content
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 22))
    }
}

private struct KeibaAIGuideScaleRow: View {
    let mark: String
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(mark)
                .font(.subheadline.weight(.bold))
                .foregroundStyle(.orange)
                .frame(width: 86, alignment: .leading)
            Text(title)
                .font(.subheadline.weight(.semibold))
                .frame(width: 100, alignment: .leading)
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

private struct KeibaAIGuideGradeRow: View {
    let grade: String
    let meaning: String
    let detail: String

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Text(grade)
                .font(.subheadline.weight(.heavy))
                .foregroundStyle(grade == "見送り" ? Color.secondary : Color.blue)
                .frame(width: 54, height: 30)
                .background((grade == "見送り" ? Color.secondary : Color.blue).opacity(0.10), in: Capsule())

            VStack(alignment: .leading, spacing: 2) {
                Text(meaning)
                    .font(.subheadline.weight(.semibold))
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct KeibaAIGuideDefinition: View {
    let term: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text(term)
                .font(.caption.weight(.bold))
                .foregroundStyle(.blue)
                .frame(width: 92, alignment: .leading)
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct KeibaAIStatusLegend: View {
    let label: String
    let icon: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Label(label, systemImage: icon)
                .font(.caption.weight(.bold))
                .frame(width: 142, alignment: .leading)
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

private struct KeibaAIDesignPrinciple: View {
    let number: String
    let title: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text(number)
                .font(.caption.weight(.heavy))
                .foregroundStyle(.white)
                .frame(width: 25, height: 25)
                .background(.blue, in: Circle())

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Text(text)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct KeibaAITicketPlanView: View {
    @ObservedObject var budgetStore: KeibaAIBudgetStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("馬券プラン")
                    .font(.largeTitle.weight(.heavy))

                Text("1レースの予算と1日の上限を設定できます。購入金額の自動配分は検証後に進めます。")
                    .font(.title3)
                    .foregroundStyle(.secondary)

                KeibaAIBudgetCard(store: budgetStore)

                KeibaAIInfoCard(
                    icon: "checkmark.shield.fill",
                    title: "確率モデルは接続済み / 金額配分は待機",
                    message: "荒れ度・勝負度・大穴候補・ワイド同時3着内確率に加え、発走前ワイド実オッズと実戦優先度まで接続済みです。現在は実運用結果を蓄積し、購入金額の自動配分を解禁できるか検証しています。"
                )

                VStack(alignment: .leading, spacing: 10) {
                    Label("1. 荒れ度・勝負度", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    Label("2. 大穴候補", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    Label("3. ワイド同時3着内確率", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    Label("4. ワイド実オッズ・実戦優先度", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    Label("5. 予算別の金額配分", systemImage: "lock.fill").foregroundStyle(.secondary)
                }
                .font(.headline)
                .padding(18)
                .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 20))

            }
            .padding(28)
            .frame(maxWidth: 900, alignment: .leading)
        }
        .background(Color(uiColor: .systemGroupedBackground))
    }
}

// ============================================================
// MARK: - Preview
// ============================================================

#Preview {
    KeibaAINewDesignView()
}


// MARK: - Research recording status
private struct KeibaAIRecordingStatusData: Decodable {
    let generatedAtJST: String
    let recordingStartDate: String
    let counts: [String: Int]
    let races: [KeibaAIRecordingStatusRow]
    let emptyMessage: String
}

private struct KeibaAIRecordingStatusRow: Decodable, Identifiable {
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

@MainActor
private final class KeibaAIRecordingStatusStore: ObservableObject {
    @Published private(set) var data: KeibaAIRecordingStatusData?
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            guard let url = URL(string: "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/PaceRecordingStatus.json") else { return }
            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 20
            let (payload, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                throw URLError(.badServerResponse)
            }
            let decoded = try JSONDecoder().decode(KeibaAIRecordingStatusData.self, from: payload)
            guard !Task.isCancelled else { return }
            data = decoded
        } catch {
            if !Task.isCancelled {
                errorMessage = "一覧を取得できませんでした。通信を確認して更新してください。"
            }
        }
    }
}

private struct KeibaAIRecordingStatusScreen: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var statusStore = KeibaAIRecordingStatusStore()
    @State private var selectedDate = "すべて"
    @State private var attentionOnly = false

    private var dateOptions: [String] {
        ["すべて"] + Array(Set(statusStore.data?.races.map(\.raceDate) ?? [])).sorted(by: >)
    }

    private var visibleRows: [KeibaAIRecordingStatusRow] {
        (statusStore.data?.races ?? []).filter {
            (selectedDate == "すべて" || $0.raceDate == selectedDate) && (!attentionOnly || $0.needsAttention)
        }
    }

    private func statusColor(_ state: String) -> Color {
        switch state {
        case "saved": return .green
        case "held": return .orange
        case "failed", "missing", "invalid": return .red
        default: return .secondary
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    Text("発走前の研究予測が保存され、その後の結果と照合されたかを確認します。ペース予想の表示は引き続き保留中です。")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    if let error = statusStore.errorMessage {
                        Text(error).font(.subheadline).foregroundStyle(.orange)
                    }
                    if let data = statusStore.data {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("対象 \(data.counts["totalRaces", default: 0])件・予測保存済み \(data.counts["saved", default: 0])件")
                                .font(.headline)
                            Text("照合済み \(data.counts["matched", default: 0])件・要確認 \(data.counts["needsAttention", default: 0])件")
                            Text("更新：\(data.generatedAtJST) JST")
                                .font(.caption).foregroundStyle(.secondary)
                            Text("記録開始：\(data.recordingStartDate)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Picker("日付", selection: $selectedDate) {
                            ForEach(dateOptions, id: \.self) { date in Text(date).tag(date) }
                        }
                        .pickerStyle(.menu)
                        Toggle("要確認のレースだけ表示", isOn: $attentionOnly)
                        if data.races.isEmpty {
                            Text(data.emptyMessage).foregroundStyle(.secondary)
                                .padding(.vertical, 20)
                        } else if visibleRows.isEmpty {
                            Text("この条件に該当するレースはありません。")
                                .foregroundStyle(.secondary).padding(.vertical, 20)
                        }
                        ForEach(visibleRows) { row in
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text("\(row.venue)\(row.raceNumber)R").font(.headline)
                                    Spacer()
                                    Text(row.startTime ?? "発走時刻未収録").font(.subheadline)
                                }
                                Text(row.raceDate).font(.caption).foregroundStyle(.secondary)
                                HStack(spacing: 12) {
                                    Text(row.recordingLabel).foregroundStyle(statusColor(row.recordingState))
                                    Text(row.resultLabel).foregroundStyle(.secondary)
                                }
                                .font(.subheadline.weight(.semibold))
                                Text(row.reason).font(.subheadline)
                                ForEach(Array(row.issues.enumerated()), id: \.offset) { _, issue in
                                    Text(issue).font(.caption).foregroundStyle(.orange)
                                }
                                if let last = row.lastAttemptAtJST {
                                    Text("最終試行：\(last)").font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(14)
                            .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
                        }
                    } else if statusStore.isLoading {
                        ProgressView("記録状況を取得中…").frame(maxWidth: .infinity)
                    }
                }
                .padding(16)
            }
            .navigationTitle("研究記録状況")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("閉じる") { dismiss() }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await statusStore.load() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .accessibilityLabel("記録状況を更新")
                    .disabled(statusStore.isLoading)
                }
            }
            .task { await statusStore.load() }
            .refreshable { await statusStore.load() }
        }
    }
}
