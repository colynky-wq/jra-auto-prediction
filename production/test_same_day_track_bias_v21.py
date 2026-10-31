from same_day_track_bias_v21 import adjustment_gate_summary, apply_same_day_v21_adjustment


def test_draw_proxy_is_disabled_with_only_three_prior_races():
    bias = {
        "completedRaceCount": 3,
        "confidence": 0.325,
        "drawBiasScore": 0.20,
        "drawBiasLabel": "outer_draw_positive",
        "detailRaceCount": 0,
        "runningStyleConfidence": 0.0,
        "runningStyleBiasScore": 0.0,
        "runningStyleBiasLabel": "insufficient",
    }
    gates = adjustment_gate_summary(bias)
    assert gates["drawEnabled"] is False
    horses = [
        {"number": 1, "horseKey": "a", "aiProbability": 15.7},
        {"number": 13, "horseKey": "b", "aiProbability": 15.5},
    ]
    out = apply_same_day_v21_adjustment(horses, bias, {})
    by_no = {h["number"]: h for h in out}
    assert by_no[1]["sameDayDrawAdjustment"] == 0.0
    assert by_no[13]["sameDayDrawAdjustment"] == 0.0
    assert by_no[1]["aiProbability"] == 15.7
    assert out[0]["number"] == 1


def test_draw_proxy_requires_four_races_non_neutral_and_is_capped_at_three_percent():
    bias = {
        "completedRaceCount": 4,
        "confidence": 0.433,
        "drawBiasScore": 0.60,
        "drawBiasLabel": "outer_draw_positive",
        "detailRaceCount": 0,
        "runningStyleConfidence": 0.0,
        "runningStyleBiasScore": 0.0,
        "runningStyleBiasLabel": "insufficient",
    }
    gates = adjustment_gate_summary(bias)
    assert gates["drawEnabled"] is True
    horses = [
        {"number": 1, "horseKey": "a", "aiProbability": 10.0},
        {"number": 16, "horseKey": "b", "aiProbability": 10.0},
    ]
    out = apply_same_day_v21_adjustment(horses, bias, {})
    assert max(abs(h["sameDayDrawAdjustment"]) for h in out) <= 0.03


def test_neutral_style_does_not_move_rank_and_non_neutral_style_can():
    horses = [
        {"number": 1, "horseKey": "front", "aiProbability": 20.0},
        {"number": 8, "horseKey": "closer", "aiProbability": 20.0},
    ]
    neutral = {
        "completedRaceCount": 4,
        "confidence": 0.433,
        "drawBiasScore": 0.0,
        "drawBiasLabel": "neutral",
        "detailRaceCount": 4,
        "runningStyleConfidence": 0.68,
        "runningStyleBiasScore": 0.10,
        "runningStyleBiasLabel": "neutral",
    }
    a = apply_same_day_v21_adjustment(horses, neutral, {"front": 0.9, "closer": 0.1})
    assert all(h["sameDayStyleAdjustment"] == 0.0 for h in a)

    closer = dict(neutral)
    closer["runningStyleBiasScore"] = 0.30
    closer["runningStyleBiasLabel"] = "closer_positive"
    b = apply_same_day_v21_adjustment(horses, closer, {"front": 0.9, "closer": 0.1})
    by_key = {h["horseKey"]: h for h in b}
    assert by_key["closer"]["sameDayShadowScore"] > by_key["front"]["sameDayShadowScore"]
    assert max(abs(h["sameDayShadowAdjustment"]) for h in b) <= 0.06
