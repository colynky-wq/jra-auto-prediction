import Foundation
import Combine

struct KeibaAIHorseIndexPayload: Codable {
    let schemaVersion: Int
    let generatedAtJST: String?
    let horseCount: Int
    let horses: [KeibaAIHorseIndexEntry]
}

struct KeibaAIHorseIndexEntry: Identifiable, Codable, Hashable, KeibaAIHorseIdentityCandidate {
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

struct KeibaAIHorseCareer: Codable, Hashable {
    let starts: Int
    let wins: Int
    let top3: Int
    let winRatePct: Double?
    let top3RatePct: Double?
    let averageFinish: Double?
}

struct KeibaAIHorseAffinityStat: Identifiable, Codable, Hashable {
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

struct KeibaAIHorseRecentRace: Identifiable, Codable, Hashable {
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

struct KeibaAIHorseProfile: Identifiable, Codable, Hashable, KeibaAIHorseIdentityCandidate {
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

struct KeibaAIHorseProfileShard: Codable {
    let schemaVersion: Int
    let shard: String
    let profiles: [String: KeibaAIHorseProfile]
}

@MainActor
final class KeibaAIHorseProfileStore: ObservableObject {
    @Published private(set) var entriesByName: [String: [KeibaAIHorseIndexEntry]] = [:]
    @Published private(set) var profilesByID: [String: KeibaAIHorseProfile] = [:]
    @Published var sourceMessage = "馬DB 未読み込み"
    @Published var isLoadingIndex = false

    private let owner = "colynky-wq"
    private let repo = "jra-auto-prediction"
    private let branch = "main"
    private let session: URLSession

    init(session: URLSession = .shared) { self.session = session }

    private var indexLoaded = false
    private var indexGeneration: String?
    private var indexTask: Task<Void, Error>?
    private var loadedShards: Set<String> = []
    private var identityArchives: [String: KeibaAIHorseIdentityArchive] = [:]

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
            guard let data = try await fetch(path: "HorseIndex.json") else { throw HorseProfileError.incompleteDatabase }
            let payload = try JSONDecoder().decode(KeibaAIHorseIndexPayload.self, from: data)
            guard payload.schemaVersion == 1, payload.horseCount == payload.horses.count,
                  Set(payload.horses.map(\.profileID)).count == payload.horses.count else {
                throw HorseProfileError.incompleteDatabase
            }
            if force || payload.generatedAtJST != indexGeneration {
                profilesByID = [:]
                loadedShards = []
                identityArchives = [:]
            }
            entriesByName = Dictionary(grouping: payload.horses, by: { normalizedName($0.name) })
            indexGeneration = payload.generatedAtJST
            indexLoaded = true
            sourceMessage = "馬DB \(payload.horseCount)頭"
        }
        indexTask = task
        try await task.value
    }

    func profile(for requested: KeibaAIHorseIdentity, raceID: String, number: Int,
                 force: Bool = false) async throws -> KeibaAIHorseProfile? {
        guard raceID.count == 12, raceID.allSatisfy({ $0.isASCII && $0.isNumber }), (1...18).contains(number) else {
            throw KeibaAIHorseIdentityError.mismatch
        }
        if let date = requested.raceDate {
            guard let year = KeibaAIHorseIdentity.year(from: date), Int(raceID.prefix(4)) == year else {
                throw KeibaAIHorseIdentityError.mismatch
            }
        }
        try await ensureIndex(force: force)
        var identity = requested
        if identity.resolution == .unavailable, let date = identity.raceDate,
           KeibaAIHorseIdentity.year(from: date) != nil {
            let date8 = date.replacingOccurrences(of: "-", with: "")
            let archiveGeneration = indexGeneration
            var archive = force ? nil : identityArchives[date8]
            if archive == nil, let data = try await fetch(path: "reports/\(date8)/HorseIdentityData.json", allowMissing: true) {
                do {
                    archive = try JSONDecoder().decode(KeibaAIHorseIdentityArchive.self, from: data)
                } catch { throw KeibaAIHorseIdentityError.mismatch }
                if archiveGeneration == indexGeneration { identityArchives[date8] = archive }
            }
            if let found = try archive?.identity(name: identity.name, number: number, raceID: raceID, date: date) {
                identity = found
            }
        }
        let candidates = entriesByName[normalizedName(identity.name)] ?? []
        guard let entry = try identity.select(from: candidates) else { return nil }

        if !force, let cached = profilesByID[entry.profileID] {
            return try verified(cached, entry: entry, identity: identity)
        }

        if force || !loadedShards.contains(entry.shard) {
            let generation = indexGeneration
            guard let data = try await fetch(path: "HorseProfiles/\(entry.shard).json") else { throw HorseProfileError.incompleteDatabase }
            let shard = try JSONDecoder().decode(KeibaAIHorseProfileShard.self, from: data)
            guard shard.schemaVersion == 1, shard.shard == entry.shard,
                  let profile = shard.profiles[entry.profileID] else {
                throw HorseProfileError.incompleteDatabase
            }
            let checked = try verified(profile, entry: entry, identity: identity)
            // Cache the entire shard so tapping another horse in the same
            // shard does not download it again. Discard an outdated response.
            if generation == indexGeneration {
                profilesByID.merge(shard.profiles) { _, fresh in fresh }
                loadedShards.insert(entry.shard)
            }
            return checked
        }
        throw HorseProfileError.incompleteDatabase
    }

    private func verified(_ profile: KeibaAIHorseProfile, entry: KeibaAIHorseIndexEntry,
                          identity: KeibaAIHorseIdentity) throws -> KeibaAIHorseProfile {
        guard profile.schemaVersion == 1, profile.profileID == entry.profileID,
              identity.matches(profile) else { throw KeibaAIHorseIdentityError.mismatch }
        return profile
    }

    private func fetch(path: String, allowMissing: Bool = false) async throws -> Data? {
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

        let (data, response) = try await session.data(for: request)
        if allowMissing, (response as? HTTPURLResponse)?.statusCode == 404 { return nil }
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
