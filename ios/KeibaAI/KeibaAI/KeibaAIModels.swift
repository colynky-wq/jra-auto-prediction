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

    // 発走前のJRA出馬表から取得。旧RaceDataとの互換性のためOptional。
    // 馬カードでは騎手→馬体重増減→斤量→人気・オッズの順で表示します。
    var jockey: String? = nil
    var carriedWeight: Double? = nil
    var popularity: Int? = nil
    var bodyWeight: Int? = nil
    var bodyWeightChange: Int? = nil
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