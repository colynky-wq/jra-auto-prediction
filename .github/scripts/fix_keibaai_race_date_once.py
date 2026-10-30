from pathlib import Path

SOURCE_PATH = Path("ios/KeibaAI/KeibaAI/KeibaAI_NewDesignView.swift")
VERIFY_PATH = Path(".github/workflows/keibaai-ios-verify.yml")
TEST_PATH = Path("ios/tests/test_race_date_display_regression.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly 1 match, got {count}")
    return text.replace(old, new, 1)


source = SOURCE_PATH.read_text(encoding="utf-8")

old = '''    private var selectedDate8: String {
        if store.selectedSnapshotID == "latest" {
            return Self.todayJST8
        }

        let savedDates = Set(store.races.compactMap { $0.raceDate?.replacingOccurrences(of: "-", with: "") })
        if savedDates.count == 1, let date8 = savedDates.first, date8.count == 8 {
            return date8
        }

        if let snapshot = store.snapshots.first(where: { $0.id == store.selectedSnapshotID }),
           let date = snapshot.date {
            return Self.date8Formatter.string(from: date)
        }

        return Self.todayJST8
    }

    private static var date8Formatter: DateFormatter = {
'''
new = '''    private var selectedDate8: String {
        // 開催日は保存日時や端末の「今日」より、RaceData.raceDateを最優先する。
        // 前夜に翌日分を取得した場合も、翌日の開催日として扱う。
        let raceDates = Set(store.races.compactMap { Self.normalizedDate8($0.raceDate) })
        if raceDates.count == 1, let date8 = raceDates.first {
            return date8
        }

        if let preferred = raceDates.sorted().first(where: { $0 >= Self.todayJST8 })
            ?? raceDates.sorted().last {
            return preferred
        }

        // raceDateを持たない古い履歴だけ、保存日のラベルへフォールバックする。
        if store.selectedSnapshotID != "latest",
           let snapshot = store.snapshots.first(where: { $0.id == store.selectedSnapshotID }),
           let date = snapshot.date {
            return Self.date8Formatter.string(from: date)
        }

        return Self.todayJST8
    }

    private static func normalizedDate8(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let digits = raw.filter { $0.isNumber }
        return digits.count == 8 ? digits : nil
    }

    private static func displayDate(_ date8: String) -> String {
        guard date8.count == 8 else { return date8 }
        let y = date8.prefix(4)
        let mStart = date8.index(date8.startIndex, offsetBy: 4)
        let mEnd = date8.index(mStart, offsetBy: 2)
        let dStart = mEnd
        return "\\(y)/\\(date8[mStart..<mEnd])/\\(date8[dStart...])"
    }

    private static var date8Formatter: DateFormatter = {
'''
source = replace_once(source, old, new, "selectedDate8 resolver")

old = '''                    raceDate: entry.raceDate ?? selectedDate8,
'''
new = '''                    raceDate: Self.normalizedDate8(prediction?.raceDate)
                        ?? Self.normalizedDate8(entry.raceDate)
                        ?? selectedDate8,
'''
source = replace_once(source, old, new, "schedule merge raceDate")

old = '''                    course: race.course,
                    raceDate: selectedDate8,
                    fieldSize: race.horses.count,
'''
count = source.count(old)
if count != 2:
    raise SystemExit(f"prediction raceDate propagation: expected 2 matches, got {count}")
new = '''                    course: race.course,
                    raceDate: Self.normalizedDate8(race.raceDate) ?? selectedDate8,
                    fieldSize: race.horses.count,
'''
source = source.replace(old, new)

old = '''            VStack(alignment: .leading, spacing: 2) {
                Text("表示データ")
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                Picker(
                    "表示データ",
'''
new = '''            VStack(alignment: .leading, spacing: 3) {
                Text("開催日")
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                Text(Self.displayDate(selectedDate8))
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .accessibilityIdentifier("race-date-display")

                Text("保存データ")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)

                Picker(
                    "保存データ",
'''
source = replace_once(source, old, new, "snapshot bar date display")

old = '''                    ForEach(store.snapshots) { snapshot in
                        Text(snapshot.label)
                            .tag(snapshot.id)
                    }
'''
new = '''                    ForEach(store.snapshots) { snapshot in
                        Text(snapshot.id == "latest" ? "最新" : "保存 \\(snapshot.label)")
                            .tag(snapshot.id)
                    }
'''
source = replace_once(source, old, new, "snapshot labels")

old = '''                    title: selectedVenue == "全場" ? "本日の全レース" : "\\(selectedVenue)のレース",
'''
new = '''                    title: selectedVenue == "全場"
                        ? "\\(Self.displayDate(selectedDate8))の全レース"
                        : "\\(Self.displayDate(selectedDate8)) \\(selectedVenue)のレース",
'''
source = replace_once(source, old, new, "race list day title")

old = '''                if !race.startTime.isEmpty { Label(race.startTime, systemImage: "clock") }
                Label(race.course, systemImage: "map")
'''
new = '''                if !race.startTime.isEmpty { Label(race.startTime, systemImage: "clock") }
                if let raceDate = race.raceDate {
                    Label(raceDate.replacingOccurrences(of: "-", with: "/"), systemImage: "calendar")
                }
                Label(race.course, systemImage: "map")
'''
source = replace_once(source, old, new, "race detail date")

SOURCE_PATH.write_text(source, encoding="utf-8")

TEST_PATH.write_text('''from pathlib import Path
import json

SOURCE = Path("ios/KeibaAI/KeibaAI/KeibaAI_NewDesignView.swift")
RACE_DATA = Path("RaceData.json")


def normalize(raw):
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits if len(digits) == 8 else None


def resolve(race_dates, today8, snapshot_date8=None):
    dates = sorted({d for d in (normalize(v) for v in race_dates) if d})
    if len(dates) == 1:
        return dates[0]
    future = next((d for d in dates if d >= today8), None)
    if future:
        return future
    if dates:
        return dates[-1]
    return snapshot_date8 or today8


# Core regression: data saved on 10/30 for a 10/31 race must display 10/31.
assert resolve(["2026-10-31"], "20261030", "20261030") == "20261031"
# Historical fallback still works when old data has no raceDate.
assert resolve([], "20261030", "20261025") == "20261025"

source = SOURCE.read_text(encoding="utf-8")
assert 'if store.selectedSnapshotID == "latest" {\\n            return Self.todayJST8' not in source
assert 'let raceDates = Set(store.races.compactMap { Self.normalizedDate8($0.raceDate) })' in source
assert 'raceDate: Self.normalizedDate8(prediction?.raceDate)' in source
assert 'raceDate: Self.normalizedDate8(race.raceDate) ?? selectedDate8' in source
assert 'Text("開催日")' in source
assert 'accessibilityIdentifier("race-date-display")' in source
assert 'Text(snapshot.id == "latest" ? "最新" : "保存 \\(snapshot.label)")' in source

races = json.loads(RACE_DATA.read_text(encoding="utf-8"))
assert races, "RaceData.json is empty"
for race in races:
    if race.get("raceDate") is not None:
        assert normalize(race["raceDate"]), race

print("race date display regression checks passed")
''', encoding="utf-8")

verify = VERIFY_PATH.read_text(encoding="utf-8")
marker = '''      - name: Build the complete iOS app without signing
'''
step = '''      - name: Verify race-day resolution and display regression
        run: python3 ios/tests/test_race_date_display_regression.py
      - name: Build the complete iOS app without signing
'''
if "Verify race-day resolution and display regression" not in verify:
    verify = replace_once(verify, marker, step, "iOS verify workflow build marker")
    VERIFY_PATH.write_text(verify, encoding="utf-8")

print("KeibaAI race-date repair patch applied")
