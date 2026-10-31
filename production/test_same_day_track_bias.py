from same_day_track_bias import build_same_day_bias, apply_draw_shadow_adjustment


def _race(rid, no, time, course="芝1600m", venue="東京"):
    return {
        "raceDate": "2026-11-01",
        "raceID": rid,
        "venue": venue,
        "raceNumber": no,
        "startTime": time,
        "course": course,
        "horses": [
            {"number": 1, "aiProbability": 20.0},
            {"number": 8, "aiProbability": 10.0},
        ],
    }


def _result(rid, no, rows, venue="東京"):
    return {
        "raceDate": "2026-11-01",
        "raceID": rid,
        "venue": venue,
        "raceNumber": no,
        "fieldSize": 8,
        "horses": rows,
    }


def test_uses_only_earlier_same_venue_same_surface():
    races = [
        _race("202605050101", 1, "10:00", "芝1400m"),
        _race("202605050102", 2, "10:30", "ダ1400m"),
        _race("202605050103", 3, "11:00", "芝1800m"),
        _race("202605050111", 11, "15:40", "芝2000m"),
        _race("202605050112", 12, "16:20", "芝1600m"),
        _race("202608050101", 1, "10:05", "芝1600m", venue="京都"),
    ]
    results = [
        _result("202605050101", 1, [
            {"number": 1, "finishPosition": 7, "resultStatus": "確定", "popularity": 1},
            {"number": 2, "finishPosition": 6, "resultStatus": "確定", "popularity": 2},
            {"number": 3, "finishPosition": 5, "resultStatus": "確定", "popularity": 3},
            {"number": 4, "finishPosition": 4, "resultStatus": "確定", "popularity": 4},
            {"number": 5, "finishPosition": 1, "resultStatus": "確定", "popularity": 8},
            {"number": 6, "finishPosition": 2, "resultStatus": "確定", "popularity": 7},
            {"number": 7, "finishPosition": 3, "resultStatus": "確定", "popularity": 6},
            {"number": 8, "finishPosition": 8, "resultStatus": "確定", "popularity": 5},
        ]),
        _result("202605050102", 2, [
            {"number": 1, "finishPosition": 1, "resultStatus": "確定", "popularity": 1},
        ]),
        _result("202605050103", 3, [
            {"number": 1, "finishPosition": 8, "resultStatus": "確定", "popularity": 1},
            {"number": 2, "finishPosition": 7, "resultStatus": "確定", "popularity": 2},
            {"number": 3, "finishPosition": 6, "resultStatus": "確定", "popularity": 3},
            {"number": 4, "finishPosition": 5, "resultStatus": "確定", "popularity": 4},
            {"number": 5, "finishPosition": 1, "resultStatus": "確定", "popularity": 8},
            {"number": 6, "finishPosition": 2, "resultStatus": "確定", "popularity": 7},
            {"number": 7, "finishPosition": 3, "resultStatus": "確定", "popularity": 6},
            {"number": 8, "finishPosition": 4, "resultStatus": "確定", "popularity": 5},
        ]),
        # This is after the target and must never leak into 11R.
        _result("202605050112", 12, [
            {"number": 1, "finishPosition": 1, "resultStatus": "確定", "popularity": 1},
        ]),
    ]

    bias = build_same_day_bias(
        races,
        results,
        race_date="2026-11-01",
        venue="東京",
        target_start_time="15:40",
        target_course="芝2000m",
        target_race_number=11,
    )
    assert bias["sourceRaceNumbers"] == [1, 3]
    assert bias["completedRaceCount"] == 2
    assert bias["drawBiasScore"] > 0
    assert bias["drawBiasLabel"] == "outer_draw_positive"


def test_incomplete_result_is_excluded():
    races = [_race("202605050101", 1, "10:00"), _race("202605050111", 11, "15:40", "芝2000m")]
    results = [_result("202605050101", 1, [
        {"number": 1, "finishPosition": 1, "resultStatus": "確定", "popularity": 1},
        {"number": 2, "finishPosition": None, "resultStatus": None, "popularity": 2},
    ])]
    bias = build_same_day_bias(
        races, results,
        race_date="2026-11-01", venue="東京",
        target_start_time="15:40", target_course="芝2000m", target_race_number=11,
    )
    assert bias["completedRaceCount"] == 0
    assert bias["drawBiasLabel"] == "insufficient"


def test_shadow_adjustment_does_not_overwrite_formal_probability():
    horses = [
        {"number": 1, "aiProbability": 30.0},
        {"number": 8, "aiProbability": 20.0},
    ]
    bias = {"drawBiasScore": 0.20, "confidence": 0.65}
    adjusted = apply_draw_shadow_adjustment(horses, bias)
    original = {h["number"]: h["aiProbability"] for h in adjusted}
    assert original[1] == 30.0
    assert original[8] == 20.0
    assert all("sameDayShadowScore" in h for h in adjusted)
    assert max(abs(h["sameDayShadowAdjustment"]) for h in adjusted) <= 0.06
