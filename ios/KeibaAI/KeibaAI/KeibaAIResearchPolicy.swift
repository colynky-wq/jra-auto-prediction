import SwiftUI

struct KeibaAIResearchPolicyCard: View {
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
