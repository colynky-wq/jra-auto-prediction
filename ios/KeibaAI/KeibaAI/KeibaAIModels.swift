import Foundation

struct Horse: Identifiable, Hashable, Codable {
    var id: Int { number }

    let number: Int
    let name: String

    let odds: Double
    let marketProbability: Double
    let aiProbability: Double

    // 旧RaceData.jsonでも読めるよう Optional。
    // GitHub側に値が追加されると自動的に表示されます。
    let marketTop2Probability: Double?
    let aiTop2Probability: Double?
    let marketTop3Probability: Double?
    let aiTop3Probability: Double?

    let aiMarketRatio: Double
    let expectedIndex: Double

    var age: Int? = nil
    var horseKey: String? = nil
}

// ============================================================
// MARK: - レースデータ
// ============================================================

struct Race: Identifiable, Hashable, Codable {
    var id: String { raceID }

    let raceID: String
    let raceDate: String?
    let venue: String
    let raceNumber: Int
    let startTime: String
    let raceName: String
    let course: String
    let horses: [Horse]
}
