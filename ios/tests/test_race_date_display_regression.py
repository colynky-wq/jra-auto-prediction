from pathlib import Path
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


# Regression: a race captured/saved on 10/30 but held on 10/31 must display 10/31.
assert resolve(["2026-10-31"], "20261030", "20261030") == "20261031"
# Legacy snapshots without raceDate may still fall back to their save date.
assert resolve([], "20261030", "20261025") == "20261025"

source = SOURCE.read_text(encoding="utf-8")
assert 'if store.selectedSnapshotID == "latest" {\n            return Self.todayJST8' not in source
assert 'let raceDates = Set(store.races.compactMap { Self.normalizedDate8($0.raceDate) })' in source
assert 'raceDate: Self.normalizedDate8(prediction?.raceDate)' in source
assert source.count('raceDate: Self.normalizedDate8(race.raceDate) ?? selectedDate8') == 2
assert 'Text("開催日")' in source
assert 'accessibilityIdentifier("race-date-display")' in source
assert r'Text(snapshot.id == "latest" ? "最新" : "保存 \(snapshot.label)")' in source
assert r'"\(Self.displayDate(selectedDate8))の全レース"' in source

races = json.loads(RACE_DATA.read_text(encoding="utf-8"))
assert races, "RaceData.json is empty"
for race in races:
    if race.get("raceDate") is not None:
        assert normalize(race["raceDate"]), race

print("race date display regression checks passed")
