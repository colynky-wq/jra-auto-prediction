import json

from live_recording_resume import research_sidecars_complete
from ticket_shadow_bridge import failure_path, record_prediction_best_effort


def _race():
    return {
        "raceDate": "2026-11-01",
        "raceID": "202605040111",
        "venue": "東京",
        "raceNumber": 11,
        "startTime": "15:40",
        "predictionTiming": "live_10min",
        "captureQuality": "formal_10min",
        "capturedAtJST": "2026-11-01T15:30:00+09:00",
        "horses": [
            {"number": 1, "name": "H1"},
            {"number": 2, "name": "H2"},
            {"number": 3, "name": "H3"},
        ],
    }


def _base_sidecars(root, race):
    day = root / "reports" / "20261101"
    rid = race["raceID"]
    for kind in ("win", "place", "frame_quinella", "quinella", "exacta"):
        path = day / "all_bet_v1" / kind / f"{rid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    for kind in ("trio", "trifecta"):
        path = day / "triple_v1" / kind / f"{rid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    path = day / "wide_axis_v1" / f"{rid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")


def test_prediction_failure_is_frozen_and_never_raises(tmp_path):
    race = _race()
    # Deliberately invalid Top2 shape: the actual shadow runtime must reject it.
    inputs = {"numbers": [1, 2, 3], "win": [0.5, 0.3, 0.2], "top2": [0.8], "top3": [1.0, 1.0, 1.0]}
    status = record_prediction_best_effort(tmp_path, race, inputs, first_formal_capture=True)
    assert status["status"] == "error"
    marker = failure_path(tmp_path, race)
    assert marker.is_file()
    row = json.loads(marker.read_text(encoding="utf-8"))
    assert row["researchLabel"] == "🧪研究モデル"
    assert row["shadowOnly"] is True
    assert row["autoBet"] is False
    assert row["predictionTiming"] == "live_10min"
    assert row["capturedAtJST"] == race["capturedAtJST"]
    assert row["prospectiveProbabilitiesPresent"] is False
    assert row["oddsCaptured"] is False and row["oddsSnapshot"] is None


def test_resume_missing_shadow_records_failure_not_hindsight_prediction(tmp_path):
    race = _race()
    inputs = {"numbers": [1, 2, 3], "win": [0.5, 0.3, 0.2], "top2": [0.8, 0.7, 0.5], "top3": [1.0, 1.0, 1.0]}
    status = record_prediction_best_effort(tmp_path, race, inputs, first_formal_capture=False)
    assert status["status"] == "error"
    marker = failure_path(tmp_path, race)
    row = json.loads(marker.read_text(encoding="utf-8"))
    assert row["stage"] == "prediction"
    assert "hindsight backfill refused" in row["error"]
    prediction = tmp_path / "reports" / "20261101" / "ticket_shadow_v1" / "prediction" / f"{race['raceID']}.json"
    assert not prediction.exists()


def test_failure_marker_satisfies_research_resume_gate_after_formal_sidecars(tmp_path):
    race = _race()
    _base_sidecars(tmp_path, race)
    assert not research_sidecars_complete(tmp_path, "20261101", race["raceID"])
    inputs = {"numbers": [1, 2, 3], "win": [0.5, 0.3, 0.2], "top2": [0.8], "top3": [1.0, 1.0, 1.0]}
    record_prediction_best_effort(tmp_path, race, inputs, first_formal_capture=True)
    assert research_sidecars_complete(tmp_path, "20261101", race["raceID"])
