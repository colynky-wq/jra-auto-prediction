import Foundation
#if canImport(Combine)
import Combine
#endif

struct KeibaAIBetResearchCandidate: Codable, Identifiable {
    let id: String
    let label: String
    let probabilityPct: Double
    let marketProbabilityPct: Double?
    let oddsLow: Double?
    let oddsHigh: Double?
    let expectedMultiplierLow: Double?
    let expectedMultiplierHigh: Double?
    let unitYen: Int
    let hit: Bool?
    let payoutYen: Int?

    static func percent(_ value: Double) -> String {
        if value > 0 && value < 0.000001 { return "0.000001%未満" }
        return String(format: value >= 1 ? "%.2f%%" : value >= 0.01 ? "%.3f%%" : "%.6f%%", value)
    }
    var probabilityText: String { Self.percent(probabilityPct) }
    var oddsText: String? {
        guard let low = oddsLow, let high = oddsHigh else { return nil }
        return low == high ? String(format: "%.1f倍", low) : String(format: "%.1f〜%.1f倍", low, high)
    }
}

struct KeibaAIBetResearchRow: Codable, Identifiable {
    let id: String
    let betType: String
    let raceDate: String
    let raceID: String
    let venue: String
    let raceNumber: Int?
    let raceName: String
    let startTime: String?
    let status: String
    let statusLabel: String
    let reason: String
    let hasRecord: Bool
    let isSaved: Bool
    let recordedAtJST: String?
    let candidates: [KeibaAIBetResearchCandidate]
    var needsAttention: Bool { ["invalid", "failed", "missing_record"].contains(status) }
    var title: String {
        betType == "win5" ? "WIN5 対象5レース" : raceNumber.map { "\(venue)\($0)R" } ?? "レース記録"
    }
}

struct KeibaAIBetResearchType: Codable, Identifiable {
    let id: String
    let label: String
    let description: String
    let methodNote: String
    let recordingStartDate: String
    let countUnit: String
    let cumulative: [String: Int]
}

struct KeibaAIBetResearchData: Codable {
    let schemaVersion: Int
    let version: String
    let status: String
    let referenceDisplayEnabled: Bool
    let formalRecommendationsEnabled: Bool
    let autoBetEnabled: Bool
    let stakeSizingEnabled: Bool
    let generatedAtJST: String
    let raceDate: String
    let collectionStatus: String
    let availableDates: [String]
    let catalog: [KeibaAIBetResearchType]
    let races: [KeibaAIBetResearchRow]
    let warnings: [String]
    let meaning: String

    static let types: Set<String> = ["win","place","frame_quinella","quinella","wide","exacta","trio","trifecta","support","win5"]
    func validate() throws {
        let keys: Set<String> = ["records","saved","matched","held","awaitingResults","payoutsPending","excluded","missingRecords","invalid","noCandidate"]
        let states: Set<String> = ["ready","awaiting_results","matched","held","payouts_pending","entrant_changed","dead_heat_excluded","invalid","missing_record","no_candidate","failed","no_target"]
        guard schemaVersion == 1, version == "bet-research-reference-v1-20261015", status == "reference_display_only",
              referenceDisplayEnabled, !formalRecommendationsEnabled, !autoBetEnabled, !stakeSizingEnabled,
              Self.validDay(raceDate), !generatedAtJST.isEmpty,
              ["collecting","attention_required"].contains(collectionStatus),
              Set(catalog.map(\.id)) == Self.types, catalog.count == Self.types.count,
              Set(races.map(\.id)).count == races.count, Set(availableDates).count == availableDates.count,
              availableDates.allSatisfy(Self.validDay) else { throw URLError(.cannotParseResponse) }
        for item in catalog {
            guard keys.isSubset(of: Set(item.cumulative.keys)), item.cumulative.values.allSatisfy({ $0 >= 0 }),
                  !item.label.isEmpty, Self.validDay(item.recordingStartDate),
                  item.cumulative["matched", default: 0] <= item.cumulative["saved", default: 0],
                  item.cumulative["saved", default: 0] <= item.cumulative["records", default: 0] else { throw URLError(.cannotParseResponse) }
        }
        for row in races {
            guard row.raceDate == raceDate, Self.types.contains(row.betType), states.contains(row.status),
                  row.id == row.betType + ":" + row.raceDate + ":" + row.raceID,
                  !row.statusLabel.isEmpty, !row.reason.isEmpty, row.candidates.count <= 3,
                  !row.isSaved || row.hasRecord,
                  Set(row.candidates.map(\.id)).count == row.candidates.count,
                  row.candidates.isEmpty || (row.hasRecord && row.isSaved && !["held","invalid","failed","missing_record","entrant_changed","dead_heat_excluded"].contains(row.status))
            else { throw URLError(.cannotParseResponse) }
            for c in row.candidates {
                guard !c.id.isEmpty, !c.label.isEmpty, c.probabilityPct.isFinite, (0...100).contains(c.probabilityPct),
                      [100,200].contains(c.unitYen), c.unitYen == (row.betType == "support" ? 200 : 100),
                      c.marketProbabilityPct.map({ $0.isFinite && (0...100).contains($0) }) ?? true,
                      c.payoutYen.map({ $0 >= 0 }) ?? true,
                      c.hit == nil || row.status == "matched", c.payoutYen == nil || row.status == "matched"
                else { throw URLError(.cannotParseResponse) }
                for v in [c.expectedMultiplierLow,c.expectedMultiplierHigh].compactMap({ $0 }) {
                    guard v.isFinite && v >= 0 else { throw URLError(.cannotParseResponse) }
                }
                if let low = c.oddsLow, let high = c.oddsHigh {
                    guard low.isFinite && high.isFinite && low >= 1 && high >= low else { throw URLError(.cannotParseResponse) }
                } else if c.oddsLow != nil || c.oddsHigh != nil { throw URLError(.cannotParseResponse) }
            }
        }
    }
    static func validDay(_ value: String) -> Bool {
        guard value.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil else { return false }
        let formatter = DateFormatter(); formatter.locale = Locale(identifier:"en_US_POSIX")
        formatter.timeZone = TimeZone(identifier:"Asia/Tokyo"); formatter.dateFormat = "yyyy-MM-dd"; formatter.isLenient = false
        return formatter.date(from:value).map { formatter.string(from:$0) == value } ?? false
    }
}

#if canImport(Combine)
@MainActor
final class KeibaAIBetResearchStore: ObservableObject {
    @Published private(set) var data: KeibaAIBetResearchData?
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    private var requestedDate8: String?
    private var requestID = UUID()
    private let session: URLSession
    init(session: URLSession = .shared) { self.session = session }

    func load(date8: String) async {
        if isLoading && requestedDate8 == date8 { return }
        let token = UUID(); requestID = token
        if requestedDate8 != date8 { data = nil }
        requestedDate8 = date8; isLoading = true; errorMessage = nil
        defer { if requestID == token { isLoading = false } }
        do {
            guard date8.count == 8, date8.allSatisfy(\.isNumber) else { throw URLError(.badURL) }
            let day = String(date8.prefix(4)) + "-" + String(date8.dropFirst(4).prefix(2)) + "-" + String(date8.suffix(2))
            guard KeibaAIBetResearchData.validDay(day) else { throw URLError(.badURL) }
            let root = try await fetch("BetResearchData.json")
            guard requestID == token, !Task.isCancelled else { return }
            if root.raceDate == day || !root.availableDates.contains(day) {
                data = root
            } else {
                let dated = try await fetch("reports/\(date8)/BetResearchData.json")
                guard requestID == token, !Task.isCancelled else { return }
                guard dated.raceDate == day else { throw URLError(.cannotParseResponse) }
                data = dated
            }
        } catch {
            if requestID == token && !Task.isCancelled {
                errorMessage = data == nil ? "研究情報を取得できませんでした。更新して再取得してください。" : "更新できなかったため、前回取得した研究情報を表示しています。"
            }
        }
    }
    private func fetch(_ path: String) async throws -> KeibaAIBetResearchData {
        var components = URLComponents(string:"https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/" + path)!
        components.queryItems = [URLQueryItem(name:"t",value:String(Date().timeIntervalSince1970))]
        var request = URLRequest(url:components.url!); request.timeoutInterval = 20; request.cachePolicy = .reloadIgnoringLocalCacheData
        let (bytes,response) = try await session.data(for:request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw URLError(.badServerResponse) }
        let result = try JSONDecoder().decode(KeibaAIBetResearchData.self,from:bytes)
        try result.validate(); return result
    }
}
#endif
