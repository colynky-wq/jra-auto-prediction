from same_day_track_bias_v2 import build_same_day_bias_v2, apply_same_day_v2_adjustment


def _race(rid, no, time, course="芝1600m"):
    return {
        "raceDate": "2026-11-01",
        "raceID": rid,
        "venue": "東京",
        "raceNumber": no,
        "startTime": time,
        "course": course,
    }


def test_v2_detects_closer_and_fast_last3_signal_without_future_leakage():
    races = [
        _race("202605050103", 3, "11:00", "芝1800m"),
        _race("202605050105", 5, "12:20", "芝2000m"),
        _race("202605050111", 11, "15:40", "芝2000m"),
        _race("202605050112", 12, "16:20", "芝1600m"),
    ]
    rows3 = [
        {"number": 1, "finishPosition": 8, "resultStatus": "確定", "popularity": 1, "passingOrder": "1-1-1-1", "last3F": 36.0},
        {"number": 2, "finishPosition": 7, "resultStatus": "確定", "popularity": 2, "passingOrder": "2-2-2-2", "last3F": 35.7},
        {"number": 3, "finishPosition": 6, "resultStatus": "確定", "popularity": 3, "passingOrder": "3-3-3-3", "last3F": 35.4},
        {"number": 4, "finishPosition": 5, "resultStatus": "確定", "popularity": 4, "passingOrder": "4-4-4-4", "last3F": 35.0},
        {"number": 5, "finishPosition": 4, "resultStatus": "確定", "popularity": 5, "passingOrder": "5-5-5-5", "last3F": 34.8},
        {"number": 6, "finishPosition": 3, "resultStatus": "確定", "popularity": 6, "passingOrder": "8-8-8-6", "last3F": 33.9},
        {"number": 7, "finishPosition": 2, "resultStatus": "確定", "popularity": 7, "passingOrder": "7-7-7-7", "last3F": 33.6},
        {"number": 8, "finishPosition": 1, "resultStatus": "確定", "popularity": 8, "passingOrder": "8-8-8-8", "last3F": 33.2},
    ]
    rows5 = [dict(r, finishPosition=r["finishPosition"], passingOrder=r["passingOrder"], last3F=r["last3F"]) for r in rows3]
    results = [
        {
            "raceDate": "2026-11-01", "raceID": "202605050103", "venue": "東京", "raceNumber": 3,
            "fieldSize": 8, "raceLap": {"first600mSeconds": 34.0, "raceLast600mSeconds": 35.2}, "horses": rows3,
        },
        {
            "raceDate": "2026-11-01", "raceID": "202605050105", "venue": "東京", "raceNumber": 5,
            "fieldSize": 8, "raceLap": {"first600mSeconds": 35.0, "raceLast600mSeconds": 35.8}, "horses": rows5,
        },
        {
            "raceDate": "2026-11-01", "raceID": "202605050112", "venue": "東京", "raceNumber": 12,
            "fieldSize": 8, "horses": [
                {"number": 1, "finishPosition": 1, "resultStatus": "確定", "popularity": 1, "passingOrder": "1-1-1-1", "last3F": 33.0}
            ],
        },
    ]

    bias = build_same_day_bias_v2(
        races, results,
        race_date="2026-11-01", venue="東京",
        target_start_time="15:40", target_course="芝2000m", target_race_number=11,
    )
    assert bias["sourceRaceNumbers"] == [3, 5]
    assert bias["detailSourceRaceNumbers"] == [3, 5]
    assert bias["runningStyleBiasLabel"] == "closer_positive"
    assert bias["closingSpeedBiasLabel"] == "fast_last3_rewarded"
    assert bias["lapRaceCount"] == 2
    assert bias["paceShapeLabel"] == "early_faster_than_late"


def test_v2_adjustment_preserves_formal_probability_and_rewards_style_alignment():
    horses = [
        {"number": 1, "horseKey": "front", "aiProbability": 20.0},
        {"number": 8, "horseKey": "closer", "aiProbability": 20.0},
    ]
    bias = {
        "drawBiasScore": 0.0,
        "confidence": 0.5,
        "runningStyleBiasScore": 0.8,
        "runningStyleConfidence": 0.8,
    }
    adjusted = apply_same_day_v2_adjustment(
        horses, bias, {"front": 0.9, "closer": 0.1}
    )
    by_key = {h["horseKey"]: h for h in adjusted}
    assert by_key["front"]["aiProbability"] == 20.0
    assert by_key["closer"]["aiProbability"] == 20.0
    assert by_key["closer"]["sameDayShadowScore"] > by_key["front"]["sameDayShadowScore"]
    assert max(abs(h["sameDayShadowAdjustment"]) for h in adjusted) <= 0.08
