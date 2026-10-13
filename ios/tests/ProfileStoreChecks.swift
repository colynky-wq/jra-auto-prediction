import Foundation

final class StubProtocol: URLProtocol {
    static let lock = NSLock()
    static var payloads: [String: (Int, Data)] = [:]
    static var requests: [String] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        Self.lock.lock()
        Self.requests.append(path)
        let response = Self.payloads[path] ?? (404, Data())
        Self.lock.unlock()
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: response.0, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: response.1)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { }

    static func put(_ path: String, _ object: Any) throws {
        let data = try JSONSerialization.data(withJSONObject: object)
        lock.lock(); defer { lock.unlock() }
        payloads["/colynky-wq/jra-auto-prediction/main/" + path] = (200, data)
    }
    static func reset() {
        lock.lock(); defer { lock.unlock() }
        payloads = [:]; requests = []
    }
    static func count(_ suffix: String) -> Int {
        lock.lock(); defer { lock.unlock() }
        return requests.filter { $0.hasSuffix(suffix) }.count
    }
}

@main
struct ProfileStoreChecks {
    static func check(_ condition: Bool, _ message: String) { if !condition { fatalError(message) } }
    static func profile(_ id: String, birth: Int) -> [String: Any] {
        ["schemaVersion": 1, "profileID": id, "horseKey": "テスト_\(birth)", "name": "テスト",
         "currentAge": 2026 - birth, "latestDate": "2026-10-01",
         "career": ["starts": 1, "wins": 0, "top3": 0],
         "bestVenues": [], "venueStats": [], "venueSurfaceStats": [], "surfaceStats": [], "distanceStats": [], "recentRaces": []]
    }
    static func index() -> [String: Any] {
        ["schemaVersion": 1, "generatedAtJST": "unit-generation", "horseCount": 2, "horses": [
            ["profileID": "old", "name": "テスト", "horseKey": "テスト_2013", "birthYear": 2013, "currentAge": 13, "latestDate": "2026-10-18", "shard": "aa"],
            ["profileID": "young", "name": "テスト", "horseKey": "テスト_2024", "birthYear": 2024, "currentAge": 2, "latestDate": "2026-10-01", "shard": "aa"]
        ]]
    }
    @MainActor
    static func store() throws -> KeibaAIHorseProfileStore {
        StubProtocol.reset()
        try StubProtocol.put("HorseIndex.json", index())
        try StubProtocol.put("HorseProfiles/aa.json", ["schemaVersion": 1, "shard": "aa", "profiles": ["old": profile("old", birth: 2013), "young": profile("young", birth: 2024)]])
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return KeibaAIHorseProfileStore(session: URLSession(configuration: config))
    }
    @MainActor
    static func main() async throws {
        let young = KeibaAIHorseIdentity(name: "テスト", horseKey: "テスト_2024", age: 2, raceDate: "2026-10-11")
        let old = KeibaAIHorseIdentity(name: "テスト", horseKey: "テスト_2013", age: 13, raceDate: "2026-10-11")
        let known = try store()
        check(try await known.profile(for: young, raceID: "202605040405", number: 2)?.profileID == "young", "store chooses correct generation")
        check(try await known.profile(for: old, raceID: "202605040405", number: 1)?.profileID == "old", "whole-shard cache retains old identity")
        check(StubProtocol.count("HorseProfiles/aa.json") == 1, "cache must avoid redundant shard request")

        let cached = try store()
        try StubProtocol.put("HorseProfiles/aa.json", ["schemaVersion": 1, "shard": "aa", "profiles": ["old": profile("old", birth: 2013), "young": profile("young", birth: 2013)]])
        _ = try await cached.profile(for: old, raceID: "202605040405", number: 1)
        do {
            _ = try await cached.profile(for: young, raceID: "202605040405", number: 2)
            fatalError("cached wrong generation must stop")
        } catch KeibaAIHorseIdentityError.mismatch { }

        let historical = try store()
        try StubProtocol.put("reports/20261011/HorseIdentityData.json", ["schemaVersion": 1, "raceDate": "2026-10-11", "races": [
            ["raceID": "202605040405", "horses": [["number": 2, "name": "テスト", "age": 2, "horseKey": "テスト_2024"]]]
        ]])
        let missing = KeibaAIHorseIdentity(name: "テスト", horseKey: nil, age: nil, raceDate: "2026-10-11")
        check(try await historical.profile(for: missing, raceID: "202605040405", number: 2)?.profileID == "young", "legacy prediction resolves only exact recorded entrant")
        _ = try await historical.profile(for: missing, raceID: "202605040405", number: 2)
        check(StubProtocol.count("HorseIdentityData.json") == 1, "legacy metadata cache")
        do {
            _ = try await historical.profile(for: missing, raceID: "202605040405", number: 3)
            fatalError("different number must stay unidentified")
        } catch KeibaAIHorseIdentityError.unavailable { }

        let absent = try store()
        do {
            _ = try await absent.profile(for: missing, raceID: "202605040405", number: 2)
            fatalError("404 cannot trigger name-only lookup")
        } catch KeibaAIHorseIdentityError.unavailable { }

        let invalid = try store()
        do {
            _ = try await invalid.profile(for: young, raceID: "202505040405", number: 2)
            fatalError("race year mismatch must stop before any fetch")
        } catch KeibaAIHorseIdentityError.mismatch { }
        check(StubProtocol.count("HorseIndex.json") == 0, "invalid identity performs no lookup")
        print("PROFILE_STORE_OK: actual asynchronous fetch, cache, legacy recovery, and failure isolation")
    }
}
