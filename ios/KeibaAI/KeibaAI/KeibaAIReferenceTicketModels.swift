import Foundation
import Combine

// A local 100-yen preview of the frozen shadow rules. No purchase or server write.
enum KeibaAIReferenceProfile: String, CaseIterable, Identifiable {
    case wideAxis = "ワイド穴軸"
    case stable = "安定型候補"
    case balanced = "バランス型候補"
    case highValue = "高期待値型候補"
    var id: String { rawValue }
    static var shadowCases: [Self] { [.stable, .balanced, .highValue] }
    var label: String { self == .wideAxis ? "穴軸流し" : String(rawValue.dropLast(2)) }
    var threshold: Double { self == .highValue ? 1.20 : 1.10 }
    var maxPairs: Int { self == .stable ? 1 : (self == .balanced ? 2 : 3) }
}

struct KeibaAIReferencePair: Identifiable, Codable, Hashable {
    let number1: Int
    let name1: String
    let number2: Int
    let name2: String
    let probabilityPct: Double
    let oddsLow: Double
    let oddsHigh: Double
    let expectedValue: Double
    var id: String { "\(min(number1, number2))-\(max(number1, number2))" }
    // Match the four-decimal forward validator, not the three-decimal signal display.
    var selectionExpectedValue: Double { (probabilityPct / 100 * oddsLow * 10000).rounded() / 10000 }
}

struct KeibaAIWideAxisEvaluation: Codable, Hashable {
    struct Axis: Codable, Hashable {
        let number: Int
        let name: String
        let aiTop3Pct: Double
        let marketTop3Pct: Double
        let marketTop3Rank: Int
    }
    let schemaVersion: Int
    let ruleVersion: String
    let status: String
    let message: String
    let axis: Axis?
    let pairs: [KeibaAIReferencePair]
    let totalYen: Int
    let minimumSingleHitReturnYen: Double?
    let capturedAtJST: String?
}

struct KeibaAIReferenceRaceInput {
    let raceID: String
    let raceDate: String?
    let startTime: String
    let eligible: Bool
    let horses: [Int: String]
    let oddsConnected: Bool
    let capturedAtJST: String?
    let pairs: [KeibaAIReferencePair]
    var axisEvaluation: KeibaAIWideAxisEvaluation? = nil
    var aiTop3ByNumber: [Int: Double] = [:]
    var marketTop3ByNumber: [Int: Double] = [:]
}

struct KeibaAIReferenceRacePlan: Identifiable {
    let id: String
    let pairs: [KeibaAIReferencePair]
    let reason: String?
    let capturedAtJST: String?
    let budgetYen: Int
    var axis: KeibaAIWideAxisEvaluation.Axis? = nil
    var totalYen: Int { pairs.count * 100 }
    var remainingYen: Int { max(0, budgetYen - totalYen) }
}

enum KeibaAIReferencePlanner {
    static func date8(_ value: String?) -> String? {
        guard let value else { return nil }
        let raw = value.replacingOccurrences(of: "-", with: "")
        guard raw.count == 8, raw.allSatisfy({ $0.isASCII && $0.isNumber }) else { return nil }
        let f = formatter("yyyyMMdd")
        guard let date = f.date(from: raw), f.string(from: date) == raw else { return nil }
        return raw
    }

    private static func formatter(_ pattern: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "Asia/Tokyo")
        f.dateFormat = pattern
        f.isLenient = false
        return f
    }

    static func plans(inputs: [KeibaAIReferenceRaceInput], date: String,
                      profile: KeibaAIReferenceProfile, perRace: Int, dailyLimit: Int?) -> [KeibaAIReferenceRacePlan] {
        let validBudget = perRace >= 0 && perRace % 100 == 0 &&
            (dailyLimit.map { $0 >= 0 && $0 % 100 == 0 } ?? true)
        var dayRemaining = dailyLimit ?? Int.max
        let counts = Dictionary(grouping: inputs, by: \.raceID).mapValues(\.count)
        // Daily preview limits span every venue, regardless of the screen filter.
        return inputs.sorted { ($0.startTime, $0.raceID) < ($1.startTime, $1.raceID) }.map { race in
            func held(_ reason: String) -> KeibaAIReferenceRacePlan {
                KeibaAIReferenceRacePlan(id: race.raceID, pairs: [], reason: reason,
                                         capturedAtJST: race.capturedAtJST, budgetYen: max(0, perRace))
            }
            guard validBudget else { return held("予算設定を確認してください") }
            guard counts[race.raceID] == 1, date8(date) == date,
                  date8(race.raceDate) == date else { return held("対象日・レースの対応を確認できません") }
            guard race.eligible else { return held("予測データ待ち、または対象外のレースです") }
            var checkedAxis: KeibaAIWideAxisEvaluation.Axis?
            if profile == .wideAxis {
                guard let e = race.axisEvaluation, e.schemaVersion == 1,
                      e.ruleVersion == "wide-axis-v1-20261014" else { return held("ワイド専用の軸評価データ待ちです") }
                guard e.status == "ready" else { return held(e.message) }
                guard let a = e.axis, a.name == race.horses[a.number],
                      a.aiTop3Pct.isFinite, a.marketTop3Pct.isFinite,
                      a.aiTop3Pct > a.marketTop3Pct, a.aiTop3Pct <= 100, a.marketTop3Pct >= 0,
                      a.marketTop3Rank >= 6,
                      race.aiTop3ByNumber.count == race.horses.count,
                      race.marketTop3ByNumber.count == race.horses.count,
                      Set(race.aiTop3ByNumber.keys) == Set(race.horses.keys),
                      Set(race.marketTop3ByNumber.keys) == Set(race.horses.keys),
                      race.aiTop3ByNumber.values.allSatisfy({ $0.isFinite && (0...100).contains($0) }),
                      race.marketTop3ByNumber.values.allSatisfy({ $0.isFinite && (0...100).contains($0) }),
                      abs(race.aiTop3ByNumber.values.reduce(0,+)-300) <= 0.05,
                      abs(race.marketTop3ByNumber.values.reduce(0,+)-300) <= 0.05,
                      abs((race.aiTop3ByNumber[a.number] ?? -1000) - a.aiTop3Pct) < 0.005,
                      abs((race.marketTop3ByNumber[a.number] ?? -1000) - a.marketTop3Pct) < 0.005,
                      race.marketTop3ByNumber.values.filter({ $0 > a.marketTop3Pct }).count + 1 == a.marketTop3Rank,
                      e.capturedAtJST == race.capturedAtJST, (1...3).contains(e.pairs.count),
                      e.totalYen == e.pairs.count * 100,
                      e.minimumSingleHitReturnYen.map({ $0.isFinite && $0 >= Double(e.totalYen) }) == true,
                      e.pairs.allSatisfy({ p in
                          (p.number1 == a.number || p.number2 == a.number) && p.selectionExpectedValue >= 1.10 &&
                          p.oddsLow * 100 >= Double(e.totalYen) &&
                          p.probabilityPct <= min(race.aiTop3ByNumber[p.number1] ?? -1, race.aiTop3ByNumber[p.number2] ?? -1) + 0.01
                      }) else { return held("軸の3着内評価・相手・合計金額の対応を確認できません") }
                checkedAxis = a
            }
            guard race.oddsConnected, !race.pairs.isEmpty else { return held("発走前の実ワイドオッズ待ちです") }
            let postFormatter = formatter("yyyyMMdd HH:mm")
            let captureFormatter = formatter("yyyy-MM-dd HH:mm:ss")
            guard let post = postFormatter.date(from: date + " " + race.startTime),
                  postFormatter.string(from: post) == date + " " + race.startTime,
                  let rawCapture = race.capturedAtJST,
                  let capture = captureFormatter.date(from: rawCapture),
                  captureFormatter.string(from: capture) == rawCapture,
                  formatter("yyyyMMdd").string(from: capture) == date,
                  capture < post else { return held("発走前の取得時刻を確認できません") }
            var seen = Set<String>()
            let inputPairs = profile == .wideAxis ? (race.axisEvaluation?.pairs ?? []) : race.pairs
            for p in inputPairs {
                guard p.number1 > 0, p.number2 > 0, p.number1 != p.number2,
                      race.horses[p.number1] == p.name1, race.horses[p.number2] == p.name2,
                      seen.insert(p.id).inserted,
                      p.probabilityPct.isFinite, p.probabilityPct >= 0, p.probabilityPct <= 100,
                      p.oddsLow.isFinite, p.oddsHigh.isFinite, p.expectedValue.isFinite,
                      p.oddsHigh <= Double.greatestFiniteMagnitude / 100,
                      p.oddsLow >= 1, p.oddsHigh >= p.oddsLow,
                      abs(p.expectedValue - p.probabilityPct / 100 * p.oddsLow) <= 0.000005 * p.oddsLow + 0.00051
                else { return held("出走馬・確率・オッズの対応を確認できません") }
            }
            let candidates = profile == .wideAxis ? inputPairs : inputPairs.filter { $0.selectionExpectedValue >= profile.threshold }.sorted {
                if $0.selectionExpectedValue != $1.selectionExpectedValue { return $0.selectionExpectedValue > $1.selectionExpectedValue }
                if $0.probabilityPct != $1.probabilityPct { return $0.probabilityPct > $1.probabilityPct }
                return ($0.number1, $0.number2) < ($1.number1, $1.number2)
            }
            guard !candidates.isEmpty else { return held("条件に合う組み合わせがないため見送りです") }
            let count = min(profile.maxPairs, perRace / 100, dayRemaining / 100)
            guard count > 0 else { return held(perRace == 0 ? "1レースの予算が0円のため見送りです" : "1日の仮配分上限に達しました") }
            let chosen = Array(candidates.prefix(count))
            dayRemaining -= chosen.count * 100
            var plan = KeibaAIReferenceRacePlan(id: race.raceID, pairs: chosen,
                reason: chosen.count < min(profile.maxPairs, candidates.count) ? "予算上限に合わせて点数を減らしています" : nil,
                capturedAtJST: rawCapture, budgetYen: perRace)
            plan.axis = checkedAxis
            return plan
        }
    }
}

struct KeibaAITicketShadowProfile: Decodable {
    struct Rule: Decodable { let thresholdEV: Double; let topNPerRace: Int }
    let rule: Rule
    let selectedRaces: Int
    let selectedBets: Int
    let hits: Int
    let returnIndex: Double?
    let status: String
}

struct KeibaAITicketShadowData: Decodable {
    struct ReviewGates: Decodable {
        let minValidatedRaces: Int
        let minSelectedBets: Int
        let minOverallReturnIndex: Double
        let minBootstrap95LowerReturnIndex: Double
        let minRecentHalfReturnIndex: Double
    }
    let schemaVersion: Int
    let ruleVersion: String
    let generatedAtJST: String
    let validatedRacesAvailable: Int
    let autoBetEnabled: Bool
    let moneyAllocationEnabled: Bool
    let reviewGates: ReviewGates
    let profiles: [String: KeibaAITicketShadowProfile]

    static func decode(_ bytes: Data) throws -> Self {
        let data = try JSONDecoder().decode(Self.self, from: bytes)
        guard data.schemaVersion == 1, data.ruleVersion == "wide-ticket-shadow-v1-20261012",
              !data.autoBetEnabled, !data.moneyAllocationEnabled,
              !data.generatedAtJST.isEmpty, data.validatedRacesAvailable >= 0,
              data.reviewGates.minValidatedRaces == 300, data.reviewGates.minSelectedBets == 150,
              data.reviewGates.minOverallReturnIndex == 1,
              data.reviewGates.minBootstrap95LowerReturnIndex == 1,
              data.reviewGates.minRecentHalfReturnIndex == 0.95 else { throw URLError(.cannotParseResponse) }
        for key in KeibaAIReferenceProfile.shadowCases {
            guard let p = data.profiles[key.rawValue], p.rule.thresholdEV == key.threshold,
                  p.rule.topNPerRace == key.maxPairs, p.selectedRaces >= 0,
                  p.selectedRaces <= data.validatedRacesAvailable,
                  p.selectedBets >= p.selectedRaces, p.hits >= 0, p.hits <= p.selectedBets,
                  p.returnIndex.map({ $0.isFinite && $0 >= 0 }) ?? (p.selectedBets == 0),
                  ["data_collecting", "review_candidate"].contains(p.status)
            else { throw URLError(.cannotParseResponse) }
        }
        return data
    }
}

struct KeibaAIWideAxisShadowData: Decodable {
    struct Counts: Decodable {
        let recordedRaces: Int
        let held: Int
        let awaitingResults: Int
        let matched: Int
        let entrantChanged: Int
        let invalid: Int
        let selectedBets: Int
        let deadHeatExcluded: Int?
        let noCandidate: Int?
        let missingRecords: Int?
    }
    let schemaVersion: Int
    let ruleVersion: String
    let generatedAtJST: String
    let counts: Counts
    let returnIndex: Double?
    let autoBetEnabled: Bool
    let moneyAllocationEnabled: Bool

    static func decode(_ bytes: Data) throws -> Self {
        let d = try JSONDecoder().decode(Self.self, from: bytes)
        let c = d.counts
        guard d.schemaVersion == 1, d.ruleVersion == "wide-axis-v1-20261014",
              !d.autoBetEnabled, !d.moneyAllocationEnabled,
              [c.recordedRaces,c.held,c.awaitingResults,c.matched,c.entrantChanged,c.invalid,c.selectedBets].allSatisfy({ $0 >= 0 && $0 <= Int.max / 8 }),
              [c.deadHeatExcluded,c.noCandidate,c.missingRecords].compactMap({ $0 }).allSatisfy({ $0 >= 0 && $0 <= Int.max / 8 }),
              c.held == (c.noCandidate ?? c.held) + (c.deadHeatExcluded ?? 0),
              c.recordedRaces == c.held + c.awaitingResults + c.matched + c.entrantChanged + c.invalid,
              c.selectedBets >= c.matched,
              d.returnIndex.map({ $0.isFinite && $0 >= 0 }) ?? (c.matched == 0)
        else { throw URLError(.cannotParseResponse) }
        return d
    }
}

@MainActor
final class KeibaAITicketShadowStore: ObservableObject {
    @Published private(set) var data: KeibaAITicketShadowData?
    @Published private(set) var errorMessage: String?
    @Published private(set) var axisData: KeibaAIWideAxisShadowData?
    @Published private(set) var axisErrorMessage: String?
    @Published private(set) var isLoading = false
    private let session: URLSession
    init(session: URLSession = .shared) { self.session = session }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            var url = URLComponents(string: "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/reports/TicketPlanShadow.json")!
            url.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
            var request = URLRequest(url: url.url!)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 15
            let (bytes, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw URLError(.badServerResponse) }
            let next = try KeibaAITicketShadowData.decode(bytes)
            try Task.checkCancellation()
            data = next
            errorMessage = nil
        } catch {
            errorMessage = data == nil ? "検証状況を取得できません。件数・回収率は未確認です。" : "検証状況の更新に失敗しました。前回の取得値を表示しています。"
        }
        do {
            var url = URLComponents(string: "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/reports/WideAxisShadow.json")!
            url.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
            var request = URLRequest(url: url.url!)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 15
            let (bytes, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw URLError(.badServerResponse) }
            let next = try KeibaAIWideAxisShadowData.decode(bytes)
            try Task.checkCancellation()
            axisData = next
            axisErrorMessage = nil
        } catch {
            axisErrorMessage = axisData == nil ? "新しい穴軸評価の検証件数・回収率は未確認です。" : "新評価の更新に失敗しました。前回の取得値を表示しています。"
        }
    }
}
