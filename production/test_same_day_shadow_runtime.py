from same_day_shadow_runtime import build_reports


def test_build_reports_keeps_formal_and_adds_shadow():
    races = [
        {
            "raceDate": "2026-11-01",
            "raceID": "202605050101",
            "venue": "東京",
            "raceNumber": 1,
            "startTime": "10:00",
            "raceName": "2歳未勝利",
            "course": "芝1400m",
            "predictionTiming": "live_10min",
            "captureQuality": "formal_10min",
            "horses": [
                {"number": 1, "aiProbability": 15.0},
                {"number": 8, "aiProbability": 10.0},
            ],
        },
        {
            "raceDate": "2026-11-01",
            "raceID": "202605050111",
            "venue": "東京",
            "raceNumber": 11,
            "startTime": "15:40",
            "raceName": "天皇賞（秋）",
            "course": "芝2000m",
            "predictionTiming": "live_10min",
            "captureQuality": "formal_10min",
            "horses": [
                {"number": 1, "aiProbability": 30.0},
                {"number": 17, "aiProbability": 20.0},
            ],
        },
    ]
    results = [
        {
            "raceDate": "2026-11-01",
            "raceID": "202605050101",
            "venue": "東京",
            "raceNumber": 1,
            "fieldSize": 8,
            "horses": [
                {"number": 1, "finishPosition": 8, "resultStatus": "確定", "popularity": 1},
                {"number": 2, "finishPosition": 7, "resultStatus": "確定", "popularity": 2},
                {"number": 3, "finishPosition": 6, "resultStatus": "確定", "popularity": 3},
                {"number": 4, "finishPosition": 5, "resultStatus": "確定", "popularity": 4},
                {"number": 5, "finishPosition": 1, "resultStatus": "確定", "popularity": 8},
                {"number": 6, "finishPosition": 2, "resultStatus": "確定", "popularity": 7},
                {"number": 7, "finishPosition": 3, "resultStatus": "確定", "popularity": 6},
                {"number": 8, "finishPosition": 4, "resultStatus": "確定", "popularity": 5},
            ],
        }
    ]

    report = build_reports(races, results, "2026-11-01")
    target = next(r for r in report if r["raceNumber"] == 11)
    assert target["formalPredictionTiming"] == "live_10min"
    assert target["formalCaptureQuality"] == "formal_10min"
    assert target["sameDayBias"]["sourceRaceNumbers"] == [1]
    assert target["challenger"]["status"] == "shadow_only"
    assert all("sameDayShadowRank" in h for h in target["challenger"]["horses"])
