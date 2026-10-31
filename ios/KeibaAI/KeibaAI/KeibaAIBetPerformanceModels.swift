import Foundation

struct KeibaAIBetPerformanceSummary: Decodable {
    let schemaVersion: Int
    let version: String
    let status: String
    let sourceGeneratedAtJST: String?
    let formalT10: KeibaAIFormalT10Performance
}

struct KeibaAIFormalT10Performance: Decodable {
    let prospectiveStartDate: String
    let targetRaces: Int
    let verifiedFormalRacesObserved: Int
    let eligibleRaces: Int
    let remainingTo100: Int
    let first100CohortLockedByRule: Bool
    let nonPerRaceBetTypes: [String]?
    let definition: String
    let betTypes: [KeibaAIFormalBetTypePerformance]
}

struct KeibaAIFormalBetTypePerformance: Decodable, Identifiable {
    let id: String
    let label: String
    let recordedRaces: Int
    let settledRaces: Int
    let hitRaces: Int
    let raceHitRatePct: Double?
    let candidateTickets: Int
    let hitTickets: Int
    let ticketHitRatePct: Double?
    let stakeYen: Int
    let returnYen: Int
    let profitYen: Int
    let returnRatePct: Double?
    let recordingCoveragePct: Double?
    let settledCoveragePct: Double?
    let missingRecordRaces: Int?
    let unsettledOrUnmatchedRaces: Int?
    let formalCohortApplicable: Bool?
    let formalEvaluationStatus: String?
}

@MainActor
final class KeibaAIBetPerformanceStore: ObservableObject {
    @Published var data: KeibaAIBetPerformanceSummary?
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let url = URL(string: "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/BetPerformanceSummary.json")!

    func load(force: Bool = false) async {
        if isLoading { return }
        if data != nil && !force { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
            if force {
                components.queryItems = [URLQueryItem(name: "t", value: String(Int(Date().timeIntervalSince1970)))]
            }
            var request = URLRequest(url: components.url ?? url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 20
            request.setValue("KeibaAI-iOS/1.0", forHTTPHeaderField: "User-Agent")
            let (payload, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                throw URLError(.badServerResponse)
            }
            data = try JSONDecoder().decode(KeibaAIBetPerformanceSummary.self, from: payload)
        } catch {
            errorMessage = "正式T-10成績を取得できませんでした。"
        }
    }
}
