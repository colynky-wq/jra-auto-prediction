from evaluate_same_day_shadow import evaluate


def test_evaluator_uses_only_v2_with_prior_evidence_and_compares_winner_rank():
    races = [
        {
            "raceDate": "2026-11-01", "raceID": "r1", "venue": "東京", "raceNumber": 5,
            "startTime": "12:20", "raceName": "test", "course": "芝2000m",
            "predictionTiming": "live_10min",
            "horses": [
                {"number": 1, "aiProbability": 40.0},
                {"number": 2, "aiProbability": 30.0},
                {"number": 3, "aiProbability": 20.0},
            ],
        },
        {
            "raceDate": "2026-11-01", "raceID": "r2", "venue": "東京", "raceNumber": 11,
            "startTime": "15:40", "raceName": "target", "course": "芝2000m",
            "predictionTiming": "live_10min",
            "horses": [
                {"number": 1, "aiProbability": 40.0},
                {"number": 2, "aiProbability": 30.0},
                {"number": 3, "aiProbability": 20.0},
            ],
        },
    ]
    results = [
        {"raceDate": "2026-11-01", "raceID": "r1", "venue": "東京", "horses": [{"number": 2, "finishPosition": 1}]},
        {"raceDate": "2026-11-01", "raceID": "r2", "venue": "東京", "horses": [{"number": 2, "finishPosition": 1}]},
    ]
    shadows = [
        {
            "raceDate": "2026-11-01", "raceID": "r1",
            "sameDayBias": {"version": "same_day_track_bias_v2", "completedRaceCount": 0},
            "challenger": {"horses": [{"number": 2, "sameDayShadowRank": 1}]},
        },
        {
            "raceDate": "2026-11-01", "raceID": "r2",
            "sameDayBias": {
                "version": "same_day_track_bias_v2", "completedRaceCount": 4, "detailRaceCount": 4,
                "runningStyleBiasLabel": "closer_positive", "closingSpeedBiasLabel": "fast_last3_rewarded",
                "paceShapeLabel": "early_faster_than_late",
            },
            "challenger": {"horses": [
                {"number": 1, "sameDayShadowRank": 2},
                {"number": 2, "sameDayShadowRank": 1},
                {"number": 3, "sameDayShadowRank": 3},
            ]},
        },
    ]
    out = evaluate(races, results, shadows, "2026-11-01")
    assert out["overall"]["races"] == 1
    assert out["overall"]["formalTop1Hits"] == 0
    assert out["overall"]["shadowTop1Hits"] == 1
    assert out["overall"]["formalMeanWinnerRank"] == 2.0
    assert out["overall"]["shadowMeanWinnerRank"] == 1.0
    assert out["skipped"]["no_prior_same_surface_race"] == 1
