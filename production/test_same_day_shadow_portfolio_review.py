import json
from pathlib import Path

from same_day_shadow_portfolio_review import collect_daily_rows, build_review


def _write_day(root: Path, date8: str, race_date: str, rows: list[dict]):
    d = root / date8
    d.mkdir(parents=True, exist_ok=True)
    (d / "SameDayShadowEvaluation.json").write_text(
        json.dumps({"raceDate": race_date, "races": rows}, ensure_ascii=False),
        encoding="utf-8",
    )


def _row(rid, *, version="same_day_track_bias_v2_1", venue="東京", surface="芝", fr=2, sr=1, prior=4, draw=True, style=False):
    return {
        "raceID": rid,
        "venue": venue,
        "surface": surface,
        "raceNumber": 11,
        "startTime": "15:40",
        "challengerVersion": version,
        "formalWinnerRank": fr,
        "shadowWinnerRank": sr,
        "completedPriorSameSurfaceRaces": prior,
        "drawGateEnabled": draw,
        "styleGateEnabled": style,
        "formalTop1Hit": fr == 1,
        "shadowTop1Hit": sr == 1,
        "formalTop3Hit": fr <= 3,
        "shadowTop3Hit": sr <= 3,
    }


def test_excludes_development_day_and_wrong_challenger(tmp_path):
    _write_day(tmp_path, "20261031", "2026-10-31", [_row("dev")])
    _write_day(
        tmp_path,
        "20261101",
        "2026-11-01",
        [
            _row("good"),
            _row("old", version="same_day_track_bias_v2"),
        ],
    )
    rows, meta = collect_daily_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["raceID"].endswith("good") is False  # nonnumeric ids zfill but remain unique
    assert rows[0]["challengerVersion"] == "same_day_track_bias_v2_1"
    assert meta["excluded"]["pre_prospective_development_date"] == 1
    assert meta["excluded"]["wrong_challenger_version"] == 1


def test_summary_tracks_ab_delta_and_gates(tmp_path):
    _write_day(
        tmp_path,
        "20261101",
        "2026-11-01",
        [
            _row("202605050111", fr=2, sr=1, prior=5, draw=True, style=False),
            _row("202605050112", fr=1, sr=2, prior=6, draw=True, style=True),
            _row("202608050111", venue="京都", surface="ダート", fr=3, sr=3, prior=2, draw=False, style=False),
        ],
    )
    rows, meta = collect_daily_rows(tmp_path)
    review = build_review(rows, meta)
    assert review["racesObserved"] == 3
    assert review["reviewStatus"] == "collecting"
    assert review["remainingTo100"] == 97
    assert review["overall"]["shadowImprovedWinnerRank"] == 1
    assert review["overall"]["shadowWorsenedWinnerRank"] == 1
    assert review["overall"]["shadowUnchangedWinnerRank"] == 1
    assert review["qualityChecks"]["automaticPromotionAllowed"] is False
    assert review["byAdjustmentGate"]["draw_only"]["races"] == 1
    assert review["byAdjustmentGate"]["draw_and_style"]["races"] == 1
    assert review["byAdjustmentGate"]["neither"]["races"] == 1
    assert review["byEvidenceCount"]["4-5_prior_races"]["races"] == 1
    assert review["byEvidenceCount"]["6plus_prior_races"]["races"] == 1
    assert review["byEvidenceCount"]["1-3_prior_races"]["races"] == 1
