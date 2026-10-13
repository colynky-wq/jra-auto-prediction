import Foundation

protocol KeibaAIHorseIdentityCandidate {
    var name: String { get }
    var horseKey: String? { get }
    var birthYear: Int? { get }
    var currentAge: Int? { get }
    var latestDate: String? { get }
}

enum KeibaAIHorseIdentityError: LocalizedError {
    case unavailable
    case mismatch
    case ambiguous

    var errorDescription: String? {
        switch self {
        case .unavailable:
            return "この馬の生年を確認できません。データを更新してください。古い予測では識別情報が不足している場合があります。"
        case .mismatch:
            return "予測と馬データの識別情報が一致しません。データを更新して再取得してください。"
        case .ambiguous:
            return "同じ馬に複数のプロフィールが見つかりました。馬データの更新後に再取得してください。"
        }
    }
}

struct KeibaAIHorseIdentity: Hashable {
    let name: String
    let horseKey: String?
    let age: Int?
    let raceDate: String?

    enum Resolution: Hashable {
        case identified(Int)
        case unavailable
        case invalid
    }

    static func normalizedName(_ value: String) -> String {
        value.precomposedStringWithCompatibilityMapping
            .components(separatedBy: .whitespacesAndNewlines).joined()
    }

    static func year(from date: String?) -> Int? {
        guard let text = date else { return nil }
        let compact = text.replacingOccurrences(of: "-", with: "")
        guard compact.count == 8, compact.allSatisfy({ $0.isASCII && $0.isNumber }),
              text == compact || text == "\(compact.prefix(4))-\(compact.dropFirst(4).prefix(2))-\(compact.suffix(2))",
              let year = Int(compact.prefix(4)), (1900...2200).contains(year),
              let month = Int(compact.dropFirst(4).prefix(2)),
              let day = Int(compact.suffix(2)) else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        guard let value = calendar.date(from: DateComponents(year: year, month: month, day: day)) else { return nil }
        let parts = calendar.dateComponents([.year, .month, .day], from: value)
        guard parts.year == year, parts.month == month, parts.day == day else { return nil }
        return year
    }

    static func keyYear(name: String, key: String) -> Int? {
        guard let separator = key.lastIndex(of: "_") else { return nil }
        let suffix = String(key[key.index(after: separator)...])
        guard suffix.count == 4, suffix.allSatisfy({ $0.isASCII && $0.isNumber }),
              let year = Int(suffix), (1900...2200).contains(year),
              normalizedName(String(key[..<separator])) == normalizedName(name) else { return nil }
        return year
    }

    var resolution: Resolution {
        guard !Self.normalizedName(name).isEmpty else { return .invalid }
        var fromKey: Int?
        if let horseKey {
            guard let year = Self.keyYear(name: name, key: horseKey) else { return .invalid }
            fromKey = year
        }
        var fromAge: Int?
        if let age {
            guard (1...40).contains(age) else { return .invalid }
            if let raceDate {
                guard let year = Self.year(from: raceDate) else { return .invalid }
                fromAge = year - age
            }
        }
        if let fromKey, let fromAge, fromKey != fromAge { return .invalid }
        guard let year = fromKey ?? fromAge else { return .unavailable }
        return .identified(year)
    }

    func matches<C: KeibaAIHorseIdentityCandidate>(_ candidate: C) -> Bool {
        guard Self.normalizedName(name) == Self.normalizedName(candidate.name),
              case let .identified(expected) = resolution else { return false }
        let identity = Self(name: candidate.name, horseKey: candidate.horseKey,
                            age: candidate.currentAge, raceDate: candidate.latestDate)
        switch identity.resolution {
        case let .identified(year):
            if let explicit = candidate.birthYear, explicit != year { return false }
            return year == expected
        case .unavailable:
            return candidate.birthYear == expected
        case .invalid:
            return false
        }
    }

    func select<C: KeibaAIHorseIdentityCandidate>(from candidates: [C]) throws -> C? {
        switch resolution {
        case .unavailable: throw KeibaAIHorseIdentityError.unavailable
        case .invalid: throw KeibaAIHorseIdentityError.mismatch
        case .identified: break
        }
        let matching = candidates.filter { matches($0) }
        guard matching.count <= 1 else { throw KeibaAIHorseIdentityError.ambiguous }
        return matching.first
    }
}
