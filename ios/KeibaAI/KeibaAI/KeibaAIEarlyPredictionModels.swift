import Foundation

struct KeibaAIEarlyPredictionHorse: Codable, Hashable, Identifiable {
    var id: Int { number }
    let rank: Int
    let number: Int
    let name: String
    let odds: Double
    let marketProbability: Double
    let aiProbability: Double
    let aiTop2Probability: Double
    let aiTop3Probability: Double
    let aiMarketRatio: Double
}

struct KeibaAIEarlyPredictionRace: Codable, Hashable, Identifiable {
    var id: String { raceID }
    let raceID: String
    let raceDate: String
    let venue: String
    let raceNumber: Int
    let startTime: String
    let raceName: String
    let horses: [KeibaAIEarlyPredictionHorse]
}

struct KeibaAIEarlyPredictionSnapshot: Codable, Hashable {
    let stage: String
    let capturedAtJST: String
    let races: [KeibaAIEarlyPredictionRace]
}

struct KeibaAIEarlyPredictionData: Codable, Hashable {
    let schemaVersion: Int
    let status: String
    let formalRecommendation: Bool
    let raceDate: String
    let method: String
    let snapshots: [String: KeibaAIEarlyPredictionSnapshot]
}

@MainActor
final class KeibaAIEarlyPredictionStore: ObservableObject {
    @Published private(set) var data: KeibaAIEarlyPredictionData?
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private var loadedDate8: String?

    func race(stage: String, raceID: String) -> KeibaAIEarlyPredictionRace? {
        data?.snapshots[stage]?.races.first { $0.raceID == raceID }
    }

    func load(date8: String, force: Bool = false) async {
        if isLoading { return }
        if !force, loadedDate8 == date8 { return }
        isLoading = true
        defer { isLoading = false }
        errorMessage = nil

        guard var components = URLComponents(string: "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/EarlyPredictionData.json") else { return }
        components.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
        guard let url = components.url else { return }
        do {
            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 15
            request.setValue("KeibaAI-iPad/2.2", forHTTPHeaderField: "User-Agent")
            let (payload, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                throw URLError(.badServerResponse)
            }
            let decoded = try JSONDecoder().decode(KeibaAIEarlyPredictionData.self, from: payload)
            guard decoded.raceDate.replacingOccurrences(of: "-", with: "") == date8 else {
                data = nil
                loadedDate8 = date8
                return
            }
            data = decoded
            loadedDate8 = date8
        } catch {
            data = nil
            errorMessage = "前日・朝予想はまだありません。"
            loadedDate8 = date8
        }
    }
}
