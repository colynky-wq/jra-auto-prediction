import Foundation

struct Candidate: Codable, Equatable, KeibaAIHorseIdentityCandidate {
    let profileID: String
    let name: String
    let horseKey: String?
    let birthYear: Int?
    let currentAge: Int?
    let latestDate: String?
}

struct LookupCase: Codable {
    let name: String
    let age: Int
    let horseKey: String
    let raceDate: String
    let candidates: [Candidate]
    let expectedID: String?
}

@main
struct ProfileIdentityChecks {
    static func check(_ value: Bool, _ message: String) {
        guard value else { fatalError(message) }
    }

    static func main() throws {
        let identity = KeibaAIHorseIdentity(name: "テスト", horseKey: "テスト_2024", age: 2, raceDate: "2026-10-17")
        let old = Candidate(profileID: "old", name: "テスト", horseKey: "テスト_2013", birthYear: 2013, currentAge: 13, latestDate: "2026-10-18")
        let young = Candidate(profileID: "young", name: "テスト", horseKey: "テスト_2024", birthYear: 2024, currentAge: 2, latestDate: "2026-10-01")
        check(try identity.select(from: [old, young])?.profileID == "young", "a later older namesake must not win")
        check(try identity.select(from: [old]) == nil, "a debut must not use an older namesake")
        let nextYear = KeibaAIHorseIdentity(name: "テスト", horseKey: nil, age: 3, raceDate: "20270101")
        check(nextYear.matches(young), "year rollover must retain the generation")
        let legacy = Candidate(profileID: "legacy", name: "テ ス ト", horseKey: nil, birthYear: nil, currentAge: 2, latestDate: "2026-10-01")
        check(identity.matches(legacy), "old index format must infer birth year from its own dated age")
        check(KeibaAIHorseIdentity(name: "テスト", horseKey: nil, age: 2, raceDate: "2026-10-17").resolution == .identified(2024), "age-only identity")
        check(KeibaAIHorseIdentity(name: "テスト", horseKey: "テスト_2024", age: nil, raceDate: nil).resolution == .identified(2024), "key-only identity")
        check(KeibaAIHorseIdentity(name: "テスト", horseKey: "テスト_2013", age: 2, raceDate: "2026-10-17").resolution == .invalid, "age/key conflict")
        check(KeibaAIHorseIdentity(name: "テスト", horseKey: "別馬_2024", age: 2, raceDate: "2026-10-17").resolution == .invalid, "key prefix mismatch")
        check(KeibaAIHorseIdentity(name: "テスト", horseKey: nil, age: nil, raceDate: "2026-10-17").resolution == .unavailable, "missing identity must be explicit")
        check(!KeibaAIHorseIdentity(name: "テストテソーロ", horseKey: nil, age: 2, raceDate: "2026-10-17").matches(young), "no substring match")
        let damaged = Candidate(profileID: "bad", name: "テスト", horseKey: "テスト_2024", birthYear: 2013, currentAge: 2, latestDate: "2026-10-01")
        check(!identity.matches(damaged), "index metadata must agree")
        for date in ["2026-02-30", "2026--10-17", "not-a-date"] {
            check(KeibaAIHorseIdentity.year(from: date) == nil, "invalid calendar date")
        }
        check(KeibaAIHorseIdentity.year(from: "2024-02-29") == 2024, "valid leap day")
        do {
            _ = try identity.select(from: [young, young]); fatalError("ambiguous entries must stop")
        } catch KeibaAIHorseIdentityError.ambiguous { }
        do {
            _ = try KeibaAIHorseIdentity(name: "テスト", horseKey: nil, age: nil, raceDate: nil).select(from: [young])
            fatalError("missing identity must stop")
        } catch KeibaAIHorseIdentityError.unavailable { }

        let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let races = try JSONDecoder().decode([Race].self, from: Data(contentsOf: root.appendingPathComponent("ios/KeibaAI/KeibaAI/RaceData.json")))
        check(races.count == 3 && races.flatMap(\.horses).count == 38, "original ZIP data must decode")
        check(races.flatMap(\.horses).allSatisfy { $0.age == nil && $0.horseKey == nil }, "old metadata remains optional")
        let first = races[0].horses[0]
        var json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(first)) as! [String: Any]
        json["age"] = 2; json["horseKey"] = "\(first.name)_2024"
        let enriched = try JSONDecoder().decode(Horse.self, from: JSONSerialization.data(withJSONObject: json))
        check(enriched.age == 2 && enriched.horseKey == "\(first.name)_2024", "new metadata must actually decode")
        check(enriched.aiProbability == first.aiProbability && enriched.odds == first.odds, "prediction values must stay unchanged")

        let cases = try JSONDecoder().decode([LookupCase].self, from: Data(contentsOf: root.appendingPathComponent("ios/tests/ProfileLookupCases.json")))
        for item in cases {
            let lookup = KeibaAIHorseIdentity(name: item.name, horseKey: item.horseKey, age: item.age, raceDate: item.raceDate)
            check(try lookup.select(from: item.candidates)?.profileID == item.expectedID, "recorded entrant \(item.name)")
            let dated = KeibaAIHorseIdentity(name: item.name, horseKey: nil, age: item.age, raceDate: item.raceDate)
            check(try dated.select(from: item.candidates)?.profileID == item.expectedID, "dated age lookup \(item.name)")
        }
        check(cases.count == 242, "full recorded-day coverage")
        print("PROFILE_IDENTITY_OK: synthetic boundaries, real model decoding, \(cases.count) saved entrants")
    }
}
