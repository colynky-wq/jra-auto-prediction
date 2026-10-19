import SwiftUI
import Foundation

@main
struct KeibaAIApp: App {
    var body: some Scene {
        WindowGroup {
            KeibaAIRootView()
        }
    }
}

private struct KeibaAIRootView: View {
    @StateObject private var portfolioStore = KeibaAIPortfolioQuickStore()
    @State private var showPortfolio = false

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            KeibaAINewDesignView()

            if portfolioStore.displayRaces != nil {
                Button {
                    showPortfolio = true
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "ticket.fill")
                        VStack(alignment: .leading, spacing: 1) {
                            Text("研究プラン")
                                .font(.subheadline.weight(.bold))
                            Text(portfolioStore.shortStatus)
                                .font(.caption2)
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .foregroundStyle(.white)
                    .background(.blue.gradient, in: Capsule())
                    .shadow(radius: 5, y: 2)
                }
                .buttonStyle(.plain)
                .padding(.trailing, 18)
                .padding(.bottom, 18)
                .accessibilityIdentifier("portfolio-research-plan-button")
            }
        }
        .task {
            while !Task.isCancelled {
                await portfolioStore.load(force: true)
                do {
                    try await Task.sleep(for: .seconds(60))
                } catch {
                    return
                }
            }
        }
        .sheet(isPresented: $showPortfolio) {
            NavigationStack {
                KeibaAIPortfolioQuickView(store: portfolioStore)
            }
        }
    }
}

private struct KeibaAIPortfolioQuickEnvelope: Decodable {
    struct Summary: Decodable {
        let racesSeen: Int
        let selectedRaces: Int
        let settledSelectedRaces: Int
        let hitAnyRaces: Int
        let profitableRaces: Int
        let torigamiRaces: Int
        let stakeYen: Int
        let returnYen: Int
        let returnIndex: Double?
    }

    struct Leg: Decodable, Identifiable {
        let betType: String
        let candidateID: String
        let label: String
        let probabilityPct: Double
        let oddsLow: Double
        let oddsHigh: Double?
        let expectedMultiplierLow: Double
        let unitYen: Int
        var id: String { betType + ":" + candidateID }
    }

    struct RacePlan: Decodable, Identifiable {
        let raceDate: String
        let raceID: String
        let venue: String?
        let raceNumber: Int?
        let raceName: String?
        let startTime: String?
        let chaosLevel: Int?
        let chaosStars: String?
        let battleGrade: String?
        let scope: String
        let ruleVersion: String
        let planType: String
        let planLabel: String
        let reason: String
        let legs: [Leg]
        let totalStakeYen: Int
        var id: String { raceDate + ":" + raceID }
    }

    let schemaVersion: Int
    let version: String
    let status: String
    let formalRecommendationsEnabled: Bool
    let autoBetEnabled: Bool
    let stakeSizingEnabled: Bool
    let generatedAtJST: String
    let prospectiveStartDate: String
    let developmentBackfill: Summary
    let prospective: Summary
    let races: [RacePlan]
    let meaning: String
}

@MainActor
private final class KeibaAIPortfolioQuickStore: ObservableObject {
    @Published private(set) var data: KeibaAIPortfolioQuickEnvelope?
    @Published private(set) var sourceMessage = "研究プラン 未読み込み"
    @Published private(set) var isLoading = false

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "Asia/Tokyo")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    var todayISO: String { Self.dateFormatter.string(from: Date()) }

    var displayDate: String? {
        guard let data else { return nil }
        if data.races.contains(where: { $0.raceDate == todayISO }) {
            return todayISO
        }
        return data.races.map(\.raceDate).max()
    }

    var displayRaces: [KeibaAIPortfolioQuickEnvelope.RacePlan]? {
        guard let displayDate else { return nil }
        let rows = (data?.races ?? [])
            .filter { $0.raceDate == displayDate }
            .sorted {
                (($0.startTime ?? ""), ($0.venue ?? ""), ($0.raceNumber ?? 0), $0.raceID)
                < (($1.startTime ?? ""), ($1.venue ?? ""), ($1.raceNumber ?? 0), $1.raceID)
            }
        return rows.isEmpty ? nil : rows
    }

    var selectedRaces: [KeibaAIPortfolioQuickEnvelope.RacePlan] {
        (displayRaces ?? []).filter { $0.planType != "skip" }
    }

    var shortStatus: String {
        guard let rows = displayRaces else { return "データ待ち" }
        let selected = rows.filter { $0.planType != "skip" }.count
        return selected == 0 ? "全見送り" : "参加候補 \(selected)R"
    }

    func load(force: Bool = false) async {
        if isLoading { return }
        if !force, data != nil { return }
        isLoading = true
        defer { isLoading = false }

        do {
            guard var components = URLComponents(
                string: "https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/PortfolioResearchData.json"
            ) else { throw URLError(.badURL) }
            components.queryItems = [URLQueryItem(name: "t", value: String(Date().timeIntervalSince1970))]
            guard let url = components.url else { throw URLError(.badURL) }

            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 10
            request.setValue("KeibaAI-iOS/2.1", forHTTPHeaderField: "User-Agent")

            let (bytes, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                throw URLError(.badServerResponse)
            }

            let decoded = try JSONDecoder().decode(KeibaAIPortfolioQuickEnvelope.self, from: bytes)
            guard decoded.schemaVersion == 1,
                  decoded.version == "portfolio-shadow-v1-20261019",
                  decoded.status == "research_display_only",
                  !decoded.formalRecommendationsEnabled,
                  !decoded.autoBetEnabled,
                  !decoded.stakeSizingEnabled else {
                throw URLError(.cannotParseResponse)
            }

            data = decoded
            sourceMessage = "研究プラン取得済"
        } catch {
            sourceMessage = "研究プラン 更新待ち"
        }
    }
}

private struct KeibaAIPortfolioQuickView: View {
    @ObservedObject var store: KeibaAIPortfolioQuickStore
    @Environment(\.dismiss) private var dismiss

    private var rows: [KeibaAIPortfolioQuickEnvelope.RacePlan] {
        store.displayRaces ?? []
    }

    private var selected: [KeibaAIPortfolioQuickEnvelope.RacePlan] {
        rows.filter { $0.planType != "skip" }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header

                if let data = store.data, let date = store.displayDate {
                    summary(data: data, date: date)

                    if selected.isEmpty {
                        VStack(alignment: .leading, spacing: 7) {
                            Label("今回は見送り", systemImage: "hand.raised.fill")
                                .font(.title3.weight(.bold))
                            Text("現在の固定条件では参加候補がありません。無理に馬券を作らない設計です。")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
                    } else {
                        ForEach(selected) { race in
                            raceCard(race)
                        }
                    }

                    if rows.count > selected.count {
                        Text("見送り \(rows.count - selected.count)R。条件不足のレースは候補を出しません。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Text("この画面はshadow研究です。正式な購入指示ではなく、10/24以降の前向き検証で性能を確認します。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.top, 4)
                } else if store.isLoading {
                    ProgressView("研究プランを取得中…")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 50)
                } else {
                    ContentUnavailableView(
                        "研究プランを準備中",
                        systemImage: "ticket",
                        description: Text(store.sourceMessage)
                    )
                }
            }
            .padding(20)
            .frame(maxWidth: 760, alignment: .leading)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("研究プラン")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("閉じる") { dismiss() }
            }
        }
        .refreshable { await store.load(force: true) }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("このレースなら、どの馬券か", systemImage: "ticket.fill")
                .font(.title2.weight(.heavy))
            Text("荒れ度と勝負度を増やさず、内部で組み合わせて1つの研究プランにまとめます。")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func summary(data: KeibaAIPortfolioQuickEnvelope, date: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("表示日")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text(date)
                        .font(.subheadline.weight(.semibold).monospacedDigit())
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text("参加候補")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text("\(selected.count) / \(rows.count)R")
                        .font(.headline.monospacedDigit())
                }
            }

            if date >= data.prospectiveStartDate {
                HStack {
                    Text("短期目標 100R")
                    Spacer()
                    Text("\(min(data.prospective.racesSeen, 100))/100R")
                        .monospacedDigit()
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                ProgressView(value: min(1, Double(data.prospective.racesSeen) / 100.0))
            } else {
                Text("10/24から固定ルールの前向き検証を開始します。現在の過去分は開発・表示確認用です。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 16))
    }

    @ViewBuilder
    private func raceCard(_ race: KeibaAIPortfolioQuickEnvelope.RacePlan) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(alignment: .firstTextBaseline) {
                Text("\(race.venue ?? "") \(race.raceNumber.map { "\($0)R" } ?? "")")
                    .font(.headline)
                if let startTime = race.startTime {
                    Text(startTime)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(race.chaosStars ?? "--")
                    .font(.caption)
                    .foregroundStyle(.orange)
                Text("勝負度 \(race.battleGrade ?? "--")")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.blue)
            }

            if let raceName = race.raceName, !raceName.isEmpty {
                Text(raceName)
                    .font(.subheadline.weight(.semibold))
            }

            HStack {
                Text("研究プラン：\(race.planLabel)")
                    .font(.title3.weight(.bold))
                Spacer()
                Text("合計 \(race.totalStakeYen)円")
                    .font(.subheadline.weight(.bold).monospacedDigit())
            }

            ForEach(race.legs) { leg in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(betLabel(leg.betType))
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(.blue, in: Capsule())
                    Text(leg.label)
                        .font(.subheadline.weight(.semibold))
                    Spacer(minLength: 8)
                    Text("\(leg.unitYen)円")
                        .font(.subheadline.monospacedDigit())
                }
            }

            Text(race.reason)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
    }

    private func betLabel(_ value: String) -> String {
        switch value {
        case "win": return "単勝"
        case "wide": return "ワイド"
        case "trio": return "3連複"
        default: return value
        }
    }
}
