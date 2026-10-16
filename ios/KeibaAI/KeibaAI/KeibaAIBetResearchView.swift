import SwiftUI
import Combine

struct KeibaAIBetResearchView: View {
    let date8: String
    let refreshID: UUID
    @StateObject private var store = KeibaAIBetResearchStore()
    @StateObject private var earlyStore = KeibaAIEarlyPredictionStore()
    @State private var selectedType = "win"
    @State private var selectedDay = ""
    @State private var showEarlyPredictions = false

    private var chosenDate8: String { selectedDay.isEmpty ? date8 : selectedDay }
    private var chosenDay: String {
        String(chosenDate8.prefix(4)) + "-" + String(chosenDate8.dropFirst(4).prefix(2)) + "-" + String(chosenDate8.suffix(2))
    }
    private var selected: KeibaAIBetResearchType? { store.data?.catalog.first { $0.id == selectedType } }
    private var rows: [KeibaAIBetResearchRow] { store.data?.races.filter { $0.betType == selectedType && $0.raceDate == chosenDay } ?? [] }
    private var dateOptions: [String] {
        let available = store.data?.availableDates.map { $0.replacingOccurrences(of: "-", with: "") } ?? []
        return Array(Set(available + [date8])).sorted(by: >)
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment:.leading, spacing:16) {
                VStack(alignment:.leading,spacing:8) {
                    Label("全券種の比較・研究中",systemImage:"flask.fill").font(.title3.bold()).foregroundStyle(.blue)
                    Text("発走前に保存した参考候補と、その後の結果を確認できます。おすすめ馬券への採用は検証中です。")
                        .font(.subheadline).fixedSize(horizontal:false,vertical:true)
                }
                if let error = store.errorMessage {
                    Label(error,systemImage:"exclamationmark.triangle").foregroundStyle(.orange).font(.subheadline)
                        .accessibilityIdentifier("bet-research-error")
                }
                if let data = store.data {
                    Picker("表示日",selection:Binding(get:{chosenDate8},set:{selectedDay=$0})) {
                        ForEach(dateOptions,id:\.self) { value in
                            Text(dateLabel(value)).tag(value)
                        }
                    }.pickerStyle(.menu).accessibilityIdentifier("bet-research-date")

                    earlyPredictionSection

                    LazyVGrid(columns:[GridItem(.adaptive(minimum:92))],spacing:8) {
                        ForEach(data.catalog) { item in
                            Button { selectedType = item.id } label: {
                                Text(item.label).font(.subheadline.weight(.semibold)).frame(maxWidth:.infinity).padding(.vertical,12)
                                    .foregroundStyle(selectedType == item.id ? .white : .primary)
                                    .background(selectedType == item.id ? Color.blue : Color(uiColor:.secondarySystemGroupedBackground),in:RoundedRectangle(cornerRadius:12))
                            }.buttonStyle(.plain).accessibilityIdentifier("bet-type-"+item.id)
                        }
                    }
                    ForEach(data.warnings,id:\.self) { Text($0).font(.subheadline).foregroundStyle(.orange) }
                    if let item = selected {
                        VStack(alignment:.leading,spacing:10) {
                            Text(item.label).font(.title2.bold())
                            Text(item.description).font(.subheadline)
                            Text(item.methodNote).font(.caption).foregroundStyle(.secondary)
                            Text("累計：記録 \(item.cumulative["records",default:0])\(item.countUnit) / 結果・払戻照合 \(item.cumulative["matched",default:0])\(item.countUnit)")
                                .font(.headline).fixedSize(horizontal:false,vertical:true).accessibilityIdentifier("bet-research-count")
                            Text("保留 \(item.cumulative["held",default:0])・結果待ち \(item.cumulative["awaitingResults",default:0])・払戻待ち \(item.cumulative["payoutsPending",default:0])")
                                .font(.subheadline).fixedSize(horizontal:false,vertical:true)
                            Text("出走馬変更・同着 \(item.cumulative["excluded",default:0])・未記録 \(item.cumulative["missingRecords",default:0])・要確認 \(item.cumulative["invalid",default:0])")
                                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
                            Text("記録開始：\(item.recordingStartDate)").font(.caption).foregroundStyle(.secondary)
                        }.padding(16).frame(maxWidth:.infinity,alignment:.leading)
                            .background(Color(uiColor:.secondarySystemGroupedBackground),in:RoundedRectangle(cornerRadius:16))
                        KeibaAIResearchPolicyCard(betType:selectedType)
                        Text("表示日 \(chosenDay) / \(rows.count)件").font(.headline)
                        if rows.isEmpty {
                            Text(chosenDay < item.recordingStartDate ? "選択日はこの券種の研究開始前です。" : "この日の初回記録はまだ確認できません。次の開催で保存状況を確認します。")
                                .foregroundStyle(.secondary).padding(.vertical,12).accessibilityIdentifier("bet-research-empty")
                        }
                        ForEach(rows) { row in raceCard(row) }
                    }
                    Text("更新：\(data.generatedAtJST.replacingOccurrences(of:"T",with:" "))")
                        .font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
                } else if store.isLoading {
                    ProgressView("研究情報を取得中…").frame(maxWidth:.infinity).padding(.vertical,24)
                }
            }.padding(16)
        }
        .background(Color(uiColor:.systemGroupedBackground))
        .navigationTitle("券種別研究")
        .accessibilityIdentifier("bet-research-screen")
        .toolbar {
            ToolbarItem(placement:.primaryAction) {
                Button {
                    Task {
                        await store.load(date8:chosenDate8)
                        await earlyStore.load(date8:chosenDate8, force:true)
                    }
                } label:{Image(systemName:"arrow.clockwise")}
                    .accessibilityLabel("券種別研究を更新").disabled(store.isLoading)
            }
        }
        .refreshable {
            await store.load(date8:chosenDate8)
            await earlyStore.load(date8:chosenDate8, force:true)
        }
        .task(id:chosenDate8) {
            await store.load(date8:chosenDate8)
            await earlyStore.load(date8:chosenDate8)
        }
        .task(id:refreshID) {
            await store.load(date8:chosenDate8)
            await earlyStore.load(date8:chosenDate8, force:true)
        }
        .onChange(of:date8) { _,_ in selectedDay = "" }
    }

    @ViewBuilder
    private var earlyPredictionSection: some View {
        VStack(alignment:.leading,spacing:10) {
            Button {
                withAnimation { showEarlyPredictions.toggle() }
            } label: {
                HStack {
                    Label("前日・朝予想（研究）",systemImage:"sunrise.fill")
                        .font(.headline)
                    Spacer()
                    Image(systemName: showEarlyPredictions ? "chevron.up" : "chevron.down")
                }
            }.buttonStyle(.plain)

            if let data = earlyStore.data {
                let evening = data.snapshots["previous_evening"]
                let morning = data.snapshots["morning"]
                HStack(spacing:12) {
                    earlyStatus("前日", snapshot:evening)
                    earlyStatus("朝", snapshot:morning)
                }
                Text("最終予想は各レース発走8〜12分前のオッズ・当日情報で『今日』画面に反映されます。前日・朝予想は研究用で、正式推奨ではありません。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)

                if showEarlyPredictions {
                    let raceIDs = Array(Set((evening?.races.map(\.raceID) ?? []) + (morning?.races.map(\.raceID) ?? []))).sorted()
                    if raceIDs.isEmpty {
                        Text("この時点で予測できたレースはありません。")
                            .font(.subheadline).foregroundStyle(.secondary)
                    } else {
                        ForEach(raceIDs,id:\.self) { rid in
                            let e = evening?.races.first { $0.raceID == rid }
                            let m = morning?.races.first { $0.raceID == rid }
                            earlyRaceCard(evening:e,morning:m)
                        }
                    }
                }
            } else if earlyStore.isLoading {
                ProgressView("前日・朝予想を確認中…")
            } else {
                Text(earlyStore.errorMessage ?? "前日・朝予想はまだありません。")
                    .font(.subheadline).foregroundStyle(.secondary)
                Text("本日分は朝予想から開始できます。次回開催からは前日20:00と当日7:30の2回を自動保存します。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
            }
        }
        .padding(14)
        .frame(maxWidth:.infinity,alignment:.leading)
        .background(Color(uiColor:.secondarySystemGroupedBackground),in:RoundedRectangle(cornerRadius:16))
        .accessibilityIdentifier("early-prediction-section")
    }

    private func earlyStatus(_ label:String, snapshot:KeibaAIEarlyPredictionSnapshot?) -> some View {
        VStack(alignment:.leading,spacing:3) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(snapshot == nil ? "未取得" : "取得済")
                .font(.subheadline.bold())
                .foregroundStyle(snapshot == nil ? .secondary : .blue)
            if let at = snapshot?.capturedAtJST {
                Text(shortTime(at)).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth:.infinity,alignment:.leading)
        .padding(10)
        .background(Color.blue.opacity(0.05),in:RoundedRectangle(cornerRadius:10))
    }

    private func earlyRaceCard(evening:KeibaAIEarlyPredictionRace?, morning:KeibaAIEarlyPredictionRace?) -> some View {
        let race = morning ?? evening
        return VStack(alignment:.leading,spacing:8) {
            HStack {
                Text("\(race?.venue ?? "") \(race?.raceNumber ?? 0)R")
                    .font(.subheadline.bold())
                Spacer()
                Text(race?.startTime ?? "").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            if let name = race?.raceName, !name.isEmpty {
                Text(name).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
            }
            HStack(alignment:.top,spacing:12) {
                earlyTop3("前日",race:evening)
                earlyTop3("朝",race:morning)
            }
        }
        .padding(12)
        .background(Color.blue.opacity(0.04),in:RoundedRectangle(cornerRadius:12))
    }

    private func earlyTop3(_ label:String, race:KeibaAIEarlyPredictionRace?) -> some View {
        VStack(alignment:.leading,spacing:4) {
            Text(label).font(.caption.bold()).foregroundStyle(.secondary)
            if let race {
                ForEach(Array(race.horses.prefix(3))) { horse in
                    Text("\(horse.rank)位  \(horse.number)番 \(horse.name)  \(String(format:"%.1f",horse.aiProbability))% / \(String(format:"%.1f",horse.odds))倍")
                        .font(.caption).fixedSize(horizontal:false,vertical:true)
                }
            } else {
                Text("未取得").font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth:.infinity,alignment:.leading)
    }

    private func shortTime(_ value:String) -> String {
        value.replacingOccurrences(of:"T",with:" ").prefix(16).description
    }

    private func dateLabel(_ value: String) -> String {
        let year = String(value.prefix(4))
        let month = String(value.dropFirst(4).prefix(2))
        let day = String(value.suffix(2))
        return "\(year)/\(month)/\(day)"
    }

    private func raceCard(_ row: KeibaAIBetResearchRow) -> some View {
        VStack(alignment:.leading,spacing:12) {
            HStack { Text(row.title).font(.headline);Spacer();Text(row.startTime ?? "").font(.subheadline.monospacedDigit()) }
            if !row.raceName.isEmpty && row.betType != "win5" { Text(row.raceName).font(.headline).fixedSize(horizontal:false,vertical:true) }
            Text(row.statusLabel).font(.subheadline.bold()).foregroundStyle(row.needsAttention ? .orange : .blue)
                .accessibilityIdentifier("bet-state-"+row.betType+"-"+row.raceID)
            if row.reason != row.statusLabel { Text(row.reason).font(.subheadline).fixedSize(horizontal:false,vertical:true) }
            if let at = row.recordedAtJST { Text("発走前の保存：\(at.replacingOccurrences(of:"T",with:" "))").font(.caption).foregroundStyle(.secondary) }
            ForEach(Array(row.candidates.enumerated()),id:\.element.id) { index,c in
                VStack(alignment:.leading,spacing:8) {
                    Text("参考候補 \(index+1)").font(.caption.bold()).foregroundStyle(.secondary)
                    Text(row.betType == "win5" ? c.label.replacingOccurrences(of:" → ",with:"\n") : c.label)
                        .font(.headline).fixedSize(horizontal:false,vertical:true)
                        .accessibilityIdentifier("bet-candidate-"+row.betType+"-"+c.id)
                    LazyVGrid(columns:[GridItem(.adaptive(minimum:140),alignment:.leading)],alignment:.leading,spacing:8) {
                        metric(row.betType == "support" ? "払戻対象に入る確率" : "AIの比較確率",c.probabilityText)
                        if let p = c.marketProbabilityPct { metric("市場からの比較確率",KeibaAIBetResearchCandidate.percent(p)) }
                        if let odds = c.oddsText { metric("保存時の実オッズ",odds) }
                        if let low = c.expectedMultiplierLow, let high = c.expectedMultiplierHigh {
                            metric("比較上の期待倍率", low == high ? String(format:"%.2f倍",low) : String(format:"%.2f〜%.2f倍",low,high))
                        }
                    }
                    if row.betType == "support" { Text("単勝100円＋複勝100円＝計200円の組合せです。部分的な払戻があっても利益になるとは限りません。").font(.caption).foregroundStyle(.secondary) }
                    if let hit = c.hit, let payout = c.payoutYen {
                        Text("保存候補の結果：\(hit ? "的中" : "外れ") / 参考払戻（\(c.unitYen)円換算）\(payout)円")
                            .font(.subheadline).fixedSize(horizontal:false,vertical:true)
                    }
                }.padding(12).frame(maxWidth:.infinity,alignment:.leading)
                    .background(Color.blue.opacity(0.06),in:RoundedRectangle(cornerRadius:12))
            }
        }.padding(16).frame(maxWidth:.infinity,alignment:.leading)
            .background(Color(uiColor:.secondarySystemGroupedBackground),in:RoundedRectangle(cornerRadius:16))
    }

    private func metric(_ label:String,_ value:String) -> some View {
        VStack(alignment:.leading,spacing:3) {Text(label).font(.caption).foregroundStyle(.secondary);Text(value).font(.subheadline.bold().monospacedDigit())}
            .fixedSize(horizontal:false,vertical:true)
    }
}

private struct KeibaAIResearchPolicyCard: View {
    let betType: String

    var body: some View {
        Group {
            if betType == "win5" {
                win5Card
            } else if betType == "wide" {
                wideCard
            }
        }
    }

    private var win5Card: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("WIN5 研究基準", systemImage: "5.circle.fill")
                    .font(.headline)
                Spacer()
                Text("暫定")
                    .font(.caption.bold())
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(Color.orange.opacity(0.16), in: Capsule())
                    .foregroundStyle(.orange)
            }

            Text("47開催・235レースの予備検証から作った暫定基準です。将来の的中や利益を保証するものではありません。")
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 9) {
                budgetRow(budget: "3,000円", skipBelow: "3%未満", hit: "2/47", roi: "45.1%")
                budgetRow(budget: "1万円", skipBelow: "5%未満", hit: "4/47", roi: "74.0%")
                budgetRow(budget: "3万円", skipBelow: "10%未満", hit: "6/47", roi: "95.7%")
            }

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Text("3万円案の参加判定テスト")
                    .font(.subheadline.bold())
                Text("推定突破率10%未満：0/18的中")
                    .font(.subheadline.monospacedDigit())
                Text("推定突破率10%以上：6/29的中（20.7%）")
                    .font(.subheadline.monospacedDigit())
                Text("同じ47開催から作った閾値なので、現在は「参加推奨」ではなく「研究上の参加候補／見送り候補」として扱います。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Divider()

            VStack(alignment: .leading, spacing: 7) {
                Text("AI上位3頭で勝ち馬を拾えた割合")
                    .font(.subheadline.bold())
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 92), alignment: .leading)], alignment: .leading, spacing: 8) {
                    legMetric("WIN1", "63.8%")
                    legMetric("WIN2", "72.3%")
                    legMetric("WIN3", "51.1%")
                    legMetric("WIN4", "51.1%")
                    legMetric("WIN5", "68.1%")
                }
                Text("WIN3・WIN4は今回の予備検証では難しく、候補を広げる優先度が高い傾向です。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Label("過去検証は保存済みオッズを使用しています。本番の10分前オッズとは条件が違うため、明日以降の前向き検証で再評価します。", systemImage: "exclamationmark.triangle")
                .font(.caption)
                .foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 16))
        .accessibilityIdentifier("win5-research-policy")
    }

    private var wideCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("ワイド予想 β版", systemImage: "arrow.left.and.right.circle.fill")
                    .font(.headline)
                Spacer()
                Text("準備中")
                    .font(.caption.bold())
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(Color.blue.opacity(0.14), in: Capsule())
                    .foregroundStyle(.blue)
            }

            Text("前向き検証が300レースに到達したらβ版を開始します。WIN5の過去235レースは、この300レースには数えません。")
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 5) {
                Text("開始条件")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text("前向き検証 300R")
                    .font(.title3.bold().monospacedDigit())
                Text("現在：前向きデータ集計中")
                    .font(.subheadline)
                    .foregroundStyle(.blue)
            }

            Divider()

            Text("β版で比較する買い方")
                .font(.subheadline.bold())
            ForEach([
                "AI1位軸流し",
                "AI上位3頭BOX",
                "AI1位 → 穴馬流し",
                "低オッズ除外",
                "期待値基準"
            ], id: \.self) { text in
                Label(text, systemImage: "checkmark.circle")
                    .font(.subheadline)
            }

            Text("評価：的中率・回収率・トリガミ率・平均購入点数・見送り条件")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 16))
        .accessibilityIdentifier("wide-beta-policy")
    }

    private func budgetRow(budget: String, skipBelow: String, hit: String, roi: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(budget)
                .font(.subheadline.bold())
                .frame(width: 64, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text("推定突破率 \(skipBelow) → 見送り候補")
                    .font(.subheadline)
                Text("予備検証：\(hit)的中 / 回収率 \(roi)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func legMetric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.subheadline.bold().monospacedDigit())
        }
    }
}

private struct KeibaAIEarlyPredictionHorse: Codable, Hashable, Identifiable {
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

private struct KeibaAIEarlyPredictionRace: Codable, Hashable, Identifiable {
    var id: String { raceID }
    let raceID: String
    let raceDate: String
    let venue: String
    let raceNumber: Int
    let startTime: String
    let raceName: String
    let horses: [KeibaAIEarlyPredictionHorse]
}

private struct KeibaAIEarlyPredictionSnapshot: Codable, Hashable {
    let stage: String
    let capturedAtJST: String
    let races: [KeibaAIEarlyPredictionRace]
}

private struct KeibaAIEarlyPredictionData: Codable, Hashable {
    let schemaVersion: Int
    let status: String
    let formalRecommendation: Bool
    let raceDate: String
    let method: String
    let snapshots: [String: KeibaAIEarlyPredictionSnapshot]
}

@MainActor
private final class KeibaAIEarlyPredictionStore: ObservableObject {
    @Published private(set) var data: KeibaAIEarlyPredictionData?
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?
    private var loadedDate8: String?

    func load(date8: String, force: Bool = false) async {
        if isLoading { return }
        if !force, loadedDate8 == date8 { return }
        isLoading = true
        defer { isLoading = false }
        errorMessage = nil

        guard var components = URLComponents(string:"https://raw.githubusercontent.com/colynky-wq/jra-auto-prediction/main/EarlyPredictionData.json") else { return }
        components.queryItems = [URLQueryItem(name:"t",value:String(Date().timeIntervalSince1970))]
        guard let url = components.url else { return }
        do {
            var request = URLRequest(url:url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 15
            request.setValue("KeibaAI-iPad/2.2",forHTTPHeaderField:"User-Agent")
            let (payload,response) = try await URLSession.shared.data(for:request)
            guard let http = response as? HTTPURLResponse,(200...299).contains(http.statusCode) else { throw URLError(.badServerResponse) }
            let decoded = try JSONDecoder().decode(KeibaAIEarlyPredictionData.self,from:payload)
            guard decoded.raceDate.replacingOccurrences(of:"-",with:"") == date8 else {
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
