import SwiftUI
import Foundation
import Combine
import UIKit

// ファイル名: ContentView.swift
// 共通のHorse / Race / RaceStore / SettingsViewを定義するファイルです。
// 新デザイン画面は別のKeibaAI_NewDesignView.swiftに置きます。

// ============================================================
// MARK: - 馬データ
// ============================================================

// Horse / Race は KeibaAIModels.swift で定義します。

// ============================================================
// MARK: - GitHub履歴
// ============================================================

struct RaceSnapshot: Identifiable, Hashable {
    let id: String
    let label: String
    let detail: String
    let sha: String?
    let date: Date?
}

private struct GitHubCommit: Decodable {
    let sha: String
    let commit: CommitBody

    struct CommitBody: Decodable {
        let message: String
        let author: CommitAuthor?
    }

    struct CommitAuthor: Decodable {
        let date: String
    }
}

// ============================================================
// MARK: - RaceStore
// ============================================================

@MainActor
final class RaceStore: ObservableObject {

    @Published var races: [Race] = []
    @Published var snapshots: [RaceSnapshot] = [
        RaceSnapshot(
            id: "latest",
            label: "最新",
            detail: "GitHub main / RaceData.json",
            sha: nil,
            date: nil
        )
    ]

    @Published var selectedSnapshotID = "latest"
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var sourceMessage = "未読み込み"
    @Published var lastLoadedAt: Date?

    private let owner = "colynky-wq"
    private let repo = "jra-auto-prediction"
    private let branch = "main"

    private var hasStarted = false
    private var activeSnapshotRequest = UUID()

    func start() async {
        guard !hasStarted else { return }
        hasStarted = true

        await loadSnapshot(id: "latest")
        await loadHistory()
    }

    func refresh() async {
        // The refresh button means "return to the current live data".
        // Historical snapshots remain selectable explicitly from the picker,
        // but a refresh must not keep the app pinned to an old race day.
        await loadSnapshot(id: "latest")
        await loadHistory()
    }

    func loadSnapshot(id: String) async {
        let requestID = UUID()
        activeSnapshotRequest = requestID
        selectedSnapshotID = id
        isLoading = true
        errorMessage = nil

        defer {
            if activeSnapshotRequest == requestID { isLoading = false }
        }

        do {
            let races: [Race]
            let sourceLabel: String

            if id == "latest" {
                let url = URL(
                    string:
                        "https://raw.githubusercontent.com/\(owner)/\(repo)/\(branch)/RaceData.json"
                )!

                races = try await fetchRaces(from: url)

                sourceLabel = "GitHub 最新データ"
            } else if let snapshot = snapshots.first(where: { $0.id == id }),
                      let sha = snapshot.sha {

                let url = URL(
                    string:
                        "https://raw.githubusercontent.com/\(owner)/\(repo)/\(sha)/RaceData.json"
                )!

                races = try await fetchRaces(from: url)

                sourceLabel = "\(snapshot.label) の保存データ"
            } else {
                throw RaceStoreError.snapshotNotFound
            }

            guard activeSnapshotRequest == requestID else { return }
            self.races = sortRaces(races)
            self.sourceMessage = sourceLabel
            self.lastLoadedAt = Date()

        } catch {
            guard activeSnapshotRequest == requestID else { return }
            // 最新データだけは、通信失敗時にアプリ同梱JSONへフォールバック。
            if id == "latest",
               let bundled = loadBundledRaceData() {

                self.races = sortRaces(bundled)
                self.sourceMessage = "アプリ内 RaceData.json（通信失敗時の予備）"
                self.lastLoadedAt = Date()
                self.errorMessage =
                    "GitHubから取得できなかったため、アプリ内データを表示しています。"

            } else {
                self.races = []
                self.errorMessage =
                    "レースデータを取得できませんでした。\n\(error.localizedDescription)"
            }
        }
    }

    func loadHistory() async {
        do {
            var components = URLComponents(
                string:
                    "https://api.github.com/repos/\(owner)/\(repo)/commits"
            )!

            components.queryItems = [
                URLQueryItem(name: "path", value: "RaceData.json"),
                URLQueryItem(name: "per_page", value: "100")
            ]

            guard let url = components.url else {
                throw RaceStoreError.invalidURL
            }

            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 20
            request.setValue(
                "application/vnd.github+json",
                forHTTPHeaderField: "Accept"
            )
            request.setValue(
                "KeibaAI-iPad/1.0",
                forHTTPHeaderField: "User-Agent"
            )

            let (data, response) = try await URLSession.shared.data(for: request)
            try validateHTTP(response)

            let commits = try JSONDecoder().decode(
                [GitHubCommit].self,
                from: data
            )

            let formatter = ISO8601DateFormatter()
            var newestByDay: [String: RaceSnapshot] = [:]

            let jst = TimeZone(identifier: "Asia/Tokyo")!
            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = jst

            let labelFormatter = DateFormatter()
            labelFormatter.locale = Locale(identifier: "ja_JP")
            labelFormatter.timeZone = jst
            labelFormatter.dateFormat = "yyyy/MM/dd"

            let timeFormatter = DateFormatter()
            timeFormatter.locale = Locale(identifier: "ja_JP")
            timeFormatter.timeZone = jst
            timeFormatter.dateFormat = "HH:mm"

            for item in commits {
                guard let dateText = item.commit.author?.date,
                      let date = formatter.date(from: dateText)
                else {
                    continue
                }

                let dayKey = labelFormatter.string(from: date)

                // APIは新しい順なので、同日の最初の1件＝その日の最終保存版。
                if newestByDay[dayKey] == nil {
                    newestByDay[dayKey] = RaceSnapshot(
                        id: item.sha,
                        label: dayKey,
                        detail:
                            "保存 \(timeFormatter.string(from: date)) / \(shortMessage(item.commit.message))",
                        sha: item.sha,
                        date: date
                    )
                }
            }

            let historical = newestByDay.values.sorted {
                ($0.date ?? .distantPast) > ($1.date ?? .distantPast)
            }

            snapshots = [
                RaceSnapshot(
                    id: "latest",
                    label: "最新",
                    detail: "GitHub main / RaceData.json",
                    sha: nil,
                    date: nil
                )
            ] + historical

        } catch {
            // 履歴一覧が取れなくても「最新」の閲覧は続けられる。
            if snapshots.isEmpty {
                snapshots = [
                    RaceSnapshot(
                        id: "latest",
                        label: "最新",
                        detail: "GitHub main / RaceData.json",
                        sha: nil,
                        date: nil
                    )
                ]
            }
        }
    }

    private func fetchRaces(from url: URL) async throws -> [Race] {
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 20
        request.setValue(
            "KeibaAI-iPad/1.0",
            forHTTPHeaderField: "User-Agent"
        )

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateHTTP(response)

        return try JSONDecoder().decode(
            [Race].self,
            from: data
        )
    }

    private func validateHTTP(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse,
              (200...299).contains(http.statusCode)
        else {
            throw RaceStoreError.httpError
        }
    }

    private func loadBundledRaceData() -> [Race]? {
        do {
            let data: Data
            if let url = Bundle.main.url(forResource: "RaceData", withExtension: "json") {
                data = try Data(contentsOf: url)
            } else if let asset = NSDataAsset(name: "RaceData") {
                // This project's JSON is stored in Assets/RaceData.dataset.
                data = asset.data
            } else {
                return nil
            }
            return try JSONDecoder().decode(
                [Race].self,
                from: data
            )
        } catch {
            return nil
        }
    }

    private func sortRaces(_ races: [Race]) -> [Race] {
        races.sorted {
            if $0.startTime != $1.startTime {
                return $0.startTime < $1.startTime
            }
            if $0.venue != $1.venue {
                return $0.venue < $1.venue
            }
            return $0.raceNumber < $1.raceNumber
        }
    }

    private func shortMessage(_ message: String) -> String {
        let firstLine = message
            .split(separator: "\n", maxSplits: 1)
            .first
            .map(String.init) ?? message

        if firstLine.count <= 34 {
            return firstLine
        }

        return String(firstLine.prefix(34)) + "…"
    }
}

private enum RaceStoreError: LocalizedError {
    case invalidURL
    case httpError
    case snapshotNotFound

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "URLを作成できませんでした。"
        case .httpError:
            return "GitHubから正常な応答がありませんでした。"
        case .snapshotNotFound:
            return "指定した過去データが見つかりませんでした。"
        }
    }
}

// ============================================================
// MARK: - メイン画面
// ============================================================

struct ContentView: View {

    @StateObject private var store = RaceStore()
    @State private var selectedRace: Race?
    @State private var showSettings = false

    var body: some View {
        NavigationSplitView {
            VStack(spacing: 0) {

                historyPicker

                Divider()

                raceList
            }
            .navigationTitle("レース")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task {
                            selectedRace = nil
                            await store.refresh()
                        }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(store.isLoading)
                }

                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
            }

        } detail: {
            if let race = selectedRace {
                RaceDetailView(race: race)
            } else {
                ContentUnavailableView(
                    "レースを選択",
                    systemImage: "flag.checkered",
                    description: Text(
                        store.races.isEmpty
                            ? "左側のデータを読み込んでいます"
                            : "一覧からレースを選択してください"
                    )
                )
            }
        }
        .task {
            await store.start()
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
    }

    private var historyPicker: some View {
        VStack(
            alignment: .leading,
            spacing: 6
        ) {
            HStack {
                Text("表示データ")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Spacer()

                if store.isLoading {
                    ProgressView()
                        .controlSize(.small)
                }
            }

            Picker(
                "表示データ",
                selection: Binding(
                    get: {
                        store.selectedSnapshotID
                    },
                    set: { newValue in
                        selectedRace = nil
                        Task {
                            await store.loadSnapshot(id: newValue)
                        }
                    }
                )
            ) {
                ForEach(store.snapshots) { snapshot in
                    Text(snapshot.label)
                        .tag(snapshot.id)
                }
            }
            .pickerStyle(.menu)

            Text(store.sourceMessage)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(2)

            if let error = store.errorMessage {
                Text(error)
                    .font(.caption2)
                    .foregroundStyle(.red)
                    .fixedSize(
                        horizontal: false,
                        vertical: true
                    )
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private var raceList: some View {
        if store.isLoading && store.races.isEmpty {
            VStack(spacing: 12) {
                ProgressView()
                Text("GitHubからレースデータを取得中…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(
                maxWidth: .infinity,
                maxHeight: .infinity
            )

        } else if store.races.isEmpty {
            ContentUnavailableView(
                "レースデータがありません",
                systemImage: "tray",
                description: Text(
                    store.errorMessage
                        ?? "更新ボタンを押して再取得してください"
                )
            )

        } else {
            List(
                store.races,
                selection: $selectedRace
            ) { race in
                RaceRow(race: race)
                    .tag(race)
            }
        }
    }
}

// ============================================================
// MARK: - レース一覧 1行
// ============================================================

struct RaceRow: View {

    let race: Race

    var body: some View {
        VStack(
            alignment: .leading,
            spacing: 6
        ) {
            HStack {
                Text(
                    "\(race.venue) \(race.raceNumber)R"
                )
                .font(.headline)

                Spacer()

                Text(race.startTime)
                    .font(.headline)
            }

            Text(race.raceName)
                .font(.subheadline)

            Text(race.course)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }
}

// ============================================================
// MARK: - レース詳細
// ============================================================

struct RaceDetailView: View {

    let race: Race

    var sortedHorses: [Horse] {
        race.horses.sorted {
            $0.aiProbability > $1.aiProbability
        }
    }

    var body: some View {
        List {
            Section {
                LabeledContent(
                    "競馬場",
                    value: race.venue
                )

                LabeledContent(
                    "発走",
                    value: race.startTime
                )

                LabeledContent(
                    "コース",
                    value: race.course
                )
            } header: {
                Text(
                    "\(race.raceNumber)R \(race.raceName)"
                )
            }

            Section("AI予測") {
                ForEach(sortedHorses) { horse in
                    HorsePredictionRow(horse: horse)
                }
            }
        }
        .navigationTitle(
            "\(race.venue) \(race.raceNumber)R"
        )
    }
}

// ============================================================
// MARK: - 馬の予測表示
// ============================================================

struct HorsePredictionRow: View {

    let horse: Horse

    private func probabilityText(
        _ value: Double?
    ) -> String {
        guard let value else {
            return "--"
        }

        return String(
            format: "%.1f%%",
            value
        )
    }

    var researchLabel: String {
        if horse.aiMarketRatio >= 1.20 {
            return "高乖離候補"
        } else if horse.aiMarketRatio >= 1.10 {
            return "注目"
        } else if horse.aiMarketRatio >= 1.05 {
            return "やや高評価"
        } else if horse.aiMarketRatio >= 0.95 {
            return "市場並み"
        } else {
            return "AI低評価"
        }
    }

    var body: some View {
        VStack(
            alignment: .leading,
            spacing: 8
        ) {
            HStack {
                Text("\(horse.number)")
                    .font(.title3)
                    .bold()

                Text(horse.name)
                    .font(.headline)

                Spacer()

                Text(researchLabel)
                    .font(.caption)
            }

            Divider()

            HStack {
                PredictionValue(
                    title: "単勝",
                    value: String(
                        format: "%.1f",
                        horse.odds
                    )
                )

                PredictionValue(
                    title: "市場",
                    value: String(
                        format: "%.1f%%",
                        horse.marketProbability
                    )
                )

                PredictionValue(
                    title: "AI",
                    value: String(
                        format: "%.1f%%",
                        horse.aiProbability
                    )
                )
            }

            HStack {
                PredictionValue(
                    title: "AI÷市場",
                    value: String(
                        format: "%.2f",
                        horse.aiMarketRatio
                    )
                )

                PredictionValue(
                    title: "期待指数",
                    value: String(
                        format: "%.1f",
                        horse.expectedIndex
                    )
                )

                Spacer()
                    .frame(maxWidth: .infinity)
            }

            HStack {
                PredictionValue(
                    title: "AI 2着内",
                    value: probabilityText(
                        horse.aiTop2Probability
                    )
                )

                PredictionValue(
                    title: "AI 3着内",
                    value: probabilityText(
                        horse.aiTop3Probability
                    )
                )

                Spacer()
                    .frame(maxWidth: .infinity)
            }
        }
        .padding(.vertical, 6)
    }
}

// ============================================================
// MARK: - 小さい数値表示
// ============================================================

struct PredictionValue: View {

    let title: String
    let value: String

    var body: some View {
        VStack(
            alignment: .leading,
            spacing: 2
        ) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)

            Text(value)
                .font(.subheadline)
                .bold()
        }
        .frame(
            maxWidth: .infinity,
            alignment: .leading
        )
    }
}

// ============================================================
// MARK: - 設定画面
// ============================================================

struct SettingsView: View {

    @Environment(\.dismiss)
    private var dismiss

    @State private var showResearchCandidates = true
    @State private var use10MinuteOdds = true

    var body: some View {
        NavigationStack {
            Form {
                Section("予測表示") {
                    Toggle(
                        "研究候補を表示",
                        isOn: $showResearchCandidates
                    )

                    Toggle(
                        "10分前オッズを使用",
                        isOn: $use10MinuteOdds
                    )
                }

                Section("現在のAI") {
                    LabeledContent(
                        "モデル",
                        value: "LightGBM"
                    )

                    LabeledContent(
                        "特徴量",
                        value: "37"
                    )

                    LabeledContent(
                        "順位内確率",
                        value: "Top2 / Top3 対応"
                    )

                    LabeledContent(
                        "データ",
                        value: "GitHub自動取得"
                    )
                }
            }
            .navigationTitle("設定")
            .toolbar {
                ToolbarItem(
                    placement: .confirmationAction
                ) {
                    Button("完了") {
                        dismiss()
                    }
                }
            }
        }
    }
}

// ============================================================
// MARK: - Preview
// ============================================================

#Preview {
    ContentView()
}
