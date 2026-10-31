from pathlib import Path
import re

view_path = Path('ios/KeibaAI/KeibaAI/KeibaAI_NewDesignView.swift')
app_path = Path('ios/KeibaAI/KeibaAI/KeibaAIApp.swift')
s = view_path.read_text()

# Keep legacy destinations compiled, but show four primary choices.
s = s.replace('case ticket = "馬券プラン"', 'case ticket = "馬券"')
s = s.replace('case research = "券種別研究"', 'case research = "研究"')
if 'static let primaryCases' not in s:
    s = s.replace(
        '    case guide = "凡例"\n\n    var id: String { rawValue }',
        '    case guide = "凡例"\n\n    static let primaryCases: [KeibaAIMainTab] = [.today, .ticket, .research, .performance]\n\n    var id: String { rawValue }'
    )

if 'private enum KeibaAITodayMode' not in s:
    s = s.replace(
        'private enum KeibaAISortOption: String, CaseIterable, Identifiable {',
        '''private enum KeibaAITodayMode: String, CaseIterable, Identifiable {
    case recommended = "おすすめ"
    case all = "全レース"
    case longshot = "穴狙い"

    var id: String { rawValue }
}

private enum KeibaAISortOption: String, CaseIterable, Identifiable {'''
    )

if '@State private var todayMode:' not in s:
    s = s.replace(
        '    @State private var mainTab: KeibaAIMainTab = .today\n',
        '    @State private var mainTab: KeibaAIMainTab = .today\n    @State private var todayMode: KeibaAITodayMode = .recommended\n'
    )

s, n = re.subn(
    r'    private var mainTabBar: some View \{.*?\n    \}\n\n    private var snapshotBar: some View \{',
    '''    private var mainTabBar: some View {
        HStack(spacing: 8) {
            ForEach(KeibaAIMainTab.primaryCases) { tab in
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        mainTab = tab
                        switch tab {
                        case .ticket, .research, .performance:
                            preferredCompactColumn = .detail
                        default:
                            preferredCompactColumn = .sidebar
                        }
                    }
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: tab.icon)
                            .font(.subheadline.weight(.semibold))
                        Text(tab.rawValue)
                            .font(.caption2.weight(.bold))
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .foregroundStyle(mainTab == tab ? .white : .primary)
                    .background {
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .fill(mainTab == tab ? Color.blue : Color(uiColor: .secondarySystemGroupedBackground))
                    }
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("main-tab-\(tab.rawValue)")
            }
        }
        .accessibilityIdentifier("main-tabs")
    }

    private var snapshotBar: some View {''',
    s,
    count=1,
    flags=re.S
)
assert n == 1, 'mainTabBar patch failed'

old_controls = '''            if mainTab != .guide && mainTab != .performance && mainTab != .ticket {
                snapshotBar
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)

                venueAndSortControls
                    .padding(.horizontal, 12)
                    .padding(.bottom, 10)

                Divider()
            }'''
new_controls = '''            if mainTab == .today {
                snapshotBar
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)

                todayModeControl
                    .padding(.horizontal, 12)
                    .padding(.bottom, 8)

                venueAndSortControls
                    .padding(.horizontal, 12)
                    .padding(.bottom, 10)

                Divider()
            }'''
assert old_controls in s, 'sidebar controls anchor missing'
s = s.replace(old_controls, new_controls, 1)

s = s.replace(
    '''                } else if mainTab == .ticket {
                    ticketSidebar
                } else if (store.isLoading || scheduleStore.isLoading) && allScheduleItems.isEmpty {''',
    '''                } else if mainTab == .ticket {
                    ticketSidebar
                } else if mainTab == .research {
                    sidebarContent
                } else if (store.isLoading || scheduleStore.isLoading) && allScheduleItems.isEmpty {''',
    1
)

help_button = '''
            Button {
                mainTab = .guide
                preferredCompactColumn = .detail
            } label: {
                Image(systemName: "questionmark.circle")
                    .font(.headline)
                    .frame(width: 36, height: 36)
                    .background(.thinMaterial, in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("見方・凡例")
'''
settings_anchor = '''            Button {
                showSettings = true
            } label: {'''
if help_button.strip() not in s:
    assert settings_anchor in s, 'settings anchor missing'
    s = s.replace(settings_anchor, help_button + '\n' + settings_anchor, 1)

today_insert = '''    private var todayModeControl: some View {
        Picker("今日の表示", selection: $todayMode) {
            ForEach(KeibaAITodayMode.allCases) { mode in
                Text(mode.rawValue).tag(mode)
            }
        }
        .pickerStyle(.segmented)
        .accessibilityIdentifier("today-mode")
    }

    private var todayListItems: [KeibaAIScheduleItem] {
        switch todayMode {
        case .all:
            return displayedTodayItems
        case .longshot:
            return displayedTodayItems.filter { item in
                !(signalStore.signal(for: item.raceID)?.longshotCandidates ?? []).isEmpty
                || item.prediction.flatMap { appResearchStore.race(for: $0) }?.longshotCandidates.isEmpty == false
            }
        case .recommended:
            return displayedTodayItems.filter { item in
                guard let grade = signalStore.signal(for: item.raceID)?.battle?.grade else { return false }
                return grade == "S" || grade == "A" || grade == "B"
            }
        }
    }

'''
marker = '    private var todayList: some View {'
if 'private var todayModeControl' not in s:
    assert marker in s, 'todayList anchor missing'
    s = s.replace(marker, today_insert + marker, 1)

today_replacement = '''    private var todayList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                sectionHeader(
                    title: todayMode == .recommended ? "今日のおすすめ" : (todayMode == .longshot ? "穴狙い候補" : "今日の全レース"),
                    subtitle: todayMode == .recommended
                        ? "AI評価B以上だけ表示。候補がなければ無理に選びません"
                        : (todayMode == .longshot ? "人気薄の候補があるレースだけ表示" : "未予測を含む予定表 / \(todayListItems.count)レース")
                )

                if let scheduleError = scheduleStore.errorMessage {
                    Text(scheduleError)
                        .font(.caption2)
                        .foregroundStyle(.orange)
                        .padding(.horizontal, 4)
                }

                if todayListItems.isEmpty {
                    KeibaAIInfoCard(
                        icon: todayMode == .longshot ? "sparkles" : "hand.raised.fill",
                        title: todayMode == .recommended ? "今は強く見るレースなし" : (todayMode == .longshot ? "穴狙い候補なし" : "レースデータ待ち"),
                        message: todayMode == .recommended
                            ? "AI評価B以上だけをおすすめとして表示します。条件に届かない日は、無理にレースを選びません。"
                            : (todayMode == .longshot ? "高オッズだけでは候補にしません。AIと市場の評価差が条件に合うまで待ちます。" : "更新ボタンで最新データを確認してください。")
                    )
                } else {
                    ForEach(todayListItems) { item in
                        KeibaAIRaceCard(
                            item: item,
                            status: status(for: item),
                            signal: signalStore.signal(for: item.raceID),
                            isSelected: selectedRaceID == item.raceID
                        )
                        .contentShape(Rectangle())
                        .accessibilityIdentifier("race-card-\(item.raceID)")
                        .onTapGesture {
                            openRace(item.raceID)
                        }
                    }
                }
            }
            .padding(12)
        }
    }

    private var focusList: some View {'''
s, n = re.subn(
    r'    private var todayList: some View \{.*?\n    \}\n\n    private var focusList: some View \{',
    today_replacement,
    s,
    count=1,
    flags=re.S
)
assert n == 1, 'todayList patch failed'

card_replacement = '''private struct KeibaAIRaceCard: View {
    let item: KeibaAIScheduleItem
    let status: KeibaAIRaceStatus
    let signal: KeibaAIRaceSignal?
    let isSelected: Bool

    private var topHorse: Horse? {
        item.prediction?.horses.max { $0.aiProbability < $1.aiProbability }
    }

    private var stars: String {
        switch signal?.battle?.grade {
        case "S": return "★★★★★"
        case "A": return "★★★★☆"
        case "B": return "★★★☆☆"
        case "C": return "★★☆☆☆"
        default: return "—"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("\(item.venue) \(item.raceNumber)R")
                    .font(.headline)
                Spacer()
                Text(item.startTime)
                    .font(.headline.monospacedDigit())
            }

            Text(item.raceName)
                .font(.subheadline.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)

            if let horse = topHorse {
                HStack(spacing: 8) {
                    Text("本命")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .background(.blue, in: Capsule())
                    Text("#\(horse.number) \(horse.name)")
                        .font(.subheadline.weight(.bold))
                        .lineLimit(1)
                    Spacer()
                    Text(String(format: "AI %.1f%%", horse.aiProbability))
                        .font(.caption.weight(.semibold).monospacedDigit())
                        .foregroundStyle(.blue)
                }
            }

            if let hole = signal?.longshotCandidates?.first {
                HStack(spacing: 7) {
                    Label("穴候補", systemImage: "sparkles")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.pink)
                    Text("#\(hole.number) \(hole.name)")
                        .font(.caption.weight(.semibold))
                        .lineLimit(1)
                    Spacer()
                }
            }

            HStack(spacing: 8) {
                Label("評価 \(signal?.battle?.grade ?? "—")", systemImage: "star.fill")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.orange)
                Text(stars)
                    .font(.caption.monospaced())
                    .foregroundStyle(.orange)
                Spacer()
                Label(status.rawValue, systemImage: status.icon)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(status.tint)
            }
        }
        .padding(14)
        .background {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color(uiColor: isSelected ? .secondarySystemGroupedBackground : .systemBackground))
        }
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(isSelected ? Color.blue : Color.clear, lineWidth: 2)
        }
        .contentShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}

private struct KeibaAILongshotRaceCard: View {'''
s, n = re.subn(
    r'private struct KeibaAIRaceCard: View \{.*?\n\}\n\nprivate struct KeibaAILongshotRaceCard: View \{',
    card_replacement,
    s,
    count=1,
    flags=re.S
)
assert n == 1, 'race card patch failed'

detail_marker = 'private struct KeibaAIRaceDetailNew: View {'
assert detail_marker in s
before, detail = s.split(detail_marker, 1)
if '@State private var showReason' not in detail:
    detail = detail.replace(
        '    @State private var selectedHorseForProfile: Horse?\n',
        '    @State private var selectedHorseForProfile: Horse?\n    @State private var showReason = false\n    @State private var showResearch = false\n',
        1
    )

new_detail_body = '''    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                conclusionCard

                DisclosureGroup(isExpanded: $showReason) {
                    predictionSection
                        .padding(.top, 12)
                } label: {
                    Label("理由を見る", systemImage: "list.bullet.rectangle")
                        .font(.headline)
                }
                .padding(16)
                .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))

                DisclosureGroup(isExpanded: $showResearch) {
                    VStack(alignment: .leading, spacing: 14) {
                        detailTabBar
                        switch selectedTab {
                        case .prediction:
                            predictionSection
                        case .longshot:
                            practicalLongshotSection
                        case .result:
                            resultSection
                        case .wide:
                            wideSection
                        case .pace:
                            paceSection
                        case .course:
                            courseSection
                        case .history:
                            historySection
                        }
                    }
                    .padding(.top, 12)
                } label: {
                    Label("研究データを見る", systemImage: "flask")
                        .font(.headline)
                }
                .padding(16)
                .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))
            }
            .padding(18)
            .frame(maxWidth: 900, alignment: .leading)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .accessibilityIdentifier("race-detail")
        .navigationTitle("\(race.venue) \(race.raceNumber)R")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selectedHorseForProfile) { horse in
            KeibaAIHorseProfileView(
                horse: horse,
                race: race,
                identity: profileIdentity(for: horse),
                store: horseProfileStore
            )
        }
    }

    private var conclusionCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(race.venue) \(race.raceNumber)R ・ \(race.startTime)")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.blue)
                    Text(race.raceName)
                        .font(.title2.weight(.heavy))
                        .fixedSize(horizontal: false, vertical: true)
                    Text(race.course)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text("AI評価 \(signal?.battle?.grade ?? "—")")
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(.orange)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .background(.orange.opacity(0.10), in: Capsule())
            }

            Label("AIの結論", systemImage: "sparkles")
                .font(.headline)

            if let top = sortedHorses.first {
                conclusionHorseRow(label: "本命", horse: top, tint: .blue)
            }
            if sortedHorses.count > 1 {
                conclusionHorseRow(label: "対抗", horse: sortedHorses[1], tint: .indigo)
            }

            if let hole = signal?.longshotCandidates?.first {
                HStack(spacing: 10) {
                    Text("穴")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.white)
                        .frame(width: 42)
                        .padding(.vertical, 6)
                        .background(.pink, in: Capsule())
                    Text("#\(hole.number) \(hole.name)")
                        .font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(String(format: "%.1f倍", hole.odds))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            } else if let hole = research?.longshotCandidates.first {
                HStack(spacing: 10) {
                    Text("穴")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.white)
                        .frame(width: 42)
                        .padding(.vertical, 6)
                        .background(.pink, in: Capsule())
                    Text("#\(hole.number) \(hole.name)")
                        .font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(String(format: "%.1f倍", hole.odds))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Label("参考馬券（研究中）", systemImage: "ticket")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.secondary)
                if let pair = rankedSignalWidePairs.first, pair.wideOddsLow != nil {
                    Text("ワイド  \(pair.number1)-\(pair.number2)  \(pair.name1) × \(pair.name2)")
                        .font(.headline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(String(format: "AI同時3着内 %.1f%%", pair.aiWideProbability))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                } else {
                    Text("条件が揃ったときだけ候補を表示します。今は見送り・データ待ちです。")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }

            Text("普段はこの画面だけ見ればOKです。詳しい数字は下の『理由を見る』『研究データを見る』から確認できます。")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(18)
        .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 22))
        .accessibilityIdentifier("race-conclusion")
    }

    private func conclusionHorseRow(label: String, horse: Horse, tint: Color) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .font(.caption.weight(.bold))
                .foregroundStyle(.white)
                .frame(width: 42)
                .padding(.vertical, 6)
                .background(tint, in: Capsule())
            Text("#\(horse.number) \(horse.name)")
                .font(.headline)
                .lineLimit(1)
            Spacer()
            Text(String(format: "%.1f%%", horse.aiProbability))
                .font(.headline.monospacedDigit())
                .foregroundStyle(tint)
        }
    }

    private func profileIdentity'''
detail, n = re.subn(
    r'    var body: some View \{.*?\n    \}\n\n    private func profileIdentity',
    new_detail_body,
    detail,
    count=1,
    flags=re.S
)
assert n == 1, 'detail body patch failed'
s = before + detail_marker + detail
view_path.write_text(s)

# Remove the always-floating research-plan button. Data/model code stays compiled.
a = app_path.read_text()
replacement = '''private struct KeibaAIRootView: View {
    var body: some View {
        KeibaAINewDesignView()
    }
}

private struct KeibaAIPortfolioQuickEnvelope: Decodable {'''
a, n = re.subn(
    r'private struct KeibaAIRootView: View \{.*?\n\}\n\nprivate struct KeibaAIPortfolioQuickEnvelope: Decodable \{',
    replacement,
    a,
    count=1,
    flags=re.S
)
assert n == 1, 'root floating-button patch failed'
app_path.write_text(a)
