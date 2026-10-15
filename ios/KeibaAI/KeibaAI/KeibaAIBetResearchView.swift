import SwiftUI

struct KeibaAIBetResearchView: View {
    let date8: String
    let refreshID: UUID
    @StateObject private var store = KeibaAIBetResearchStore()
    @State private var selectedType = "win"
    @State private var selectedDay = ""
    private var chosenDate8: String { selectedDay.isEmpty ? date8 : selectedDay }
    private var chosenDay: String {
        String(chosenDate8.prefix(4)) + "-" + String(chosenDate8.dropFirst(4).prefix(2)) + "-" + String(chosenDate8.suffix(2))
    }
    private var selected: KeibaAIBetResearchType? { store.data?.catalog.first { $0.id == selectedType } }
    private var rows: [KeibaAIBetResearchRow] { store.data?.races.filter { $0.betType == selectedType && $0.raceDate == chosenDay } ?? [] }

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
                        ForEach(Array(Set(data.availableDates.map{$0.replacingOccurrences(of:"-",with:"")}+[date8])).sorted(by:>),id:\.self) { value in
                            Text(String(value.prefix(4))+"/"+String(value.dropFirst(4).prefix(2))+"/"+String(value.suffix(2))).tag(value)
                        }
                    }.pickerStyle(.menu).accessibilityIdentifier("bet-research-date")
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
                Button { Task { await store.load(date8:chosenDate8) } } label:{Image(systemName:"arrow.clockwise")}
                    .accessibilityLabel("券種別研究を更新").disabled(store.isLoading)
            }
        }
        .refreshable { await store.load(date8:chosenDate8) }
        .task(id:chosenDate8) { await store.load(date8:chosenDate8) }
        .task(id:refreshID) { await store.load(date8:chosenDate8) }
        .onChange(of:date8) { _,_ in selectedDay = "" }
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
