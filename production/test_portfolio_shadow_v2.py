from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio_shadow_v2 as m
import portfolio_shadow as v1

assert m.VERSION == "portfolio-shadow-v2-20261101"
assert m.PROSPECTIVE_START == "2026-11-02"
assert m.VARIANTS == ("battle_a_only", "top3_coverage", "combined")
assert v1.VERSION == "portfolio-shadow-v1-20261019"
assert v1.PROSPECTIVE_START == "2026-10-24"
assert v1.HIGH_CHAOS_WIDE_MIN_PROB_PCT == 5.0
assert v1.HIGH_CHAOS_WIDE_MIN_EV == 1.10


def sig(level=4, grade="A", pairs=None):
    return {
        "eligible": True,
        "venue": "東京",
        "raceNumber": 1,
        "raceName": "test",
        "startTime": "10:00",
        "chaos": {"level": level, "stars": "★★★★☆"},
        "battle": {"grade": grade},
        "wideEvaluation": {"status": "live_odds_connected", "pairs": pairs or []},
    }


def wp(a, b, p=10.0, ev=1.2, rank=1, score=1.0):
    return {
        "number1": a,
        "name1": f"h{a}",
        "number2": b,
        "name2": f"h{b}",
        "aiWideProbability": p,
        "conservativeExpectedValue": ev,
        "wideOddsLow": 5.0,
        "wideOddsHigh": 6.0,
        "practicalRank": rank,
        "practicalScore": score,
    }


def candidate(cid, p, ev, hit=None, payout=None):
    return {
        "id": str(cid),
        "label": str(cid),
        "probabilityPct": p,
        "oddsLow": 5.0,
        "oddsHigh": 5.0,
        "expectedMultiplierLow": ev,
        "unitYen": 100,
        "hit": hit,
        "payoutYen": payout,
    }


def row(kind, candidates, status="matched"):
    return {"betType": kind, "status": status, "candidates": candidates}


# Selection objects must not carry post-race outcome fields.
pre = m.candidate_pre_leg("trio", candidate("010203", 5.0, 1.2, True, 9990))
assert pre is not None
assert "hit" not in pre and "payoutYen" not in pre

# Formal T-10 Top3 comes only from the formal live_10min Win sidecar.
with TemporaryDirectory() as td:
    p = Path(td) / "R.json"
    p.write_text(json.dumps({
        "betType": "win",
        "prediction": {
            "raceID": "R",
            "predictionTiming": "live_10min",
            "captureQuality": "formal_10min",
            "horses": [
                {"number": 1, "aiProbability": 11.0},
                {"number": 2, "aiProbability": 20.0},
                {"number": 3, "aiProbability": 15.0},
                {"number": 4, "aiProbability": 3.0},
            ],
        },
    }, ensure_ascii=False), encoding="utf-8")
    assert m.formal_top3(p, "R") == [2, 3, 1]

# Practical ordering would take 1-2 and 4-5; strict Top3 membership removes
# 4-5, then coverage chooses 1-2 and 1-3 to cover all Top3 {1,2,3}.
pairs = [
    wp(1, 2, rank=1, score=9),
    wp(4, 5, rank=2, score=8),
    wp(1, 3, rank=9, score=1),
]
chosen = m.choose_wides_top3(sig(pairs=pairs), [1, 2, 3], 5.0, 1.10, 2)
assert {x["candidateID"] for x in chosen} == {"0102", "0103"}
assert all(set(x["numbers"]) & {1, 2, 3} for x in chosen)

# Strict membership is fail-closed: even excellent Wide candidates are rejected
# when neither horse belongs to the frozen formal Top3.
off_top3 = [
    wp(4, 5, p=20.0, ev=2.0, rank=1, score=99),
    wp(6, 7, p=18.0, ev=1.8, rank=2, score=90),
]
assert m.choose_wides_top3(sig(pairs=off_top3), [1, 2, 3], 5.0, 1.10, 2) == []

# If only one qualifying Top3-containing pair exists, keep one ticket rather than
# padding the set with an off-Top3 ticket just to reach the two-ticket limit.
mixed = [
    wp(1, 4, rank=5, score=3),
    wp(5, 6, p=30.0, ev=3.0, rank=1, score=100),
]
chosen = m.choose_wides_top3(sig(pairs=mixed), [1, 2, 3], 5.0, 1.10, 2)
assert [x["candidateID"] for x in chosen] == ["0104"]

# Regression: horse 10 must never be inferred from string "0104".
metrics = m.top3_metrics([
    {"betType": "wide", "numbers": [1, 4]},
    {"betType": "wide", "numbers": [2, 5]},
], [10, 2, 3])
assert metrics["selectedWideTop3CoveredNumbers"] == [2]
assert metrics["selectedWideTop3CoverageCount"] == 1

# High-chaos S is held by A-only/combined, while top3_coverage remains eligible.
rows = {("R", "trio"): row("trio", [candidate("010203", 5.0, 1.2, False, 0)])}
wide_results = {}
a_only = m.build_challenger_plan("battle_a_only", "R", "2026-11-02", rows, sig(5, "S", pairs), wide_results, [1, 2, 3])
top3 = m.build_challenger_plan("top3_coverage", "R", "2026-11-02", rows, sig(5, "S", pairs), wide_results, [1, 2, 3])
combined = m.build_challenger_plan("combined", "R", "2026-11-02", rows, sig(5, "S", pairs), wide_results, [1, 2, 3])
assert a_only["planType"] == "skip"
assert top3["planType"] != "skip"
assert combined["planType"] == "skip"

# Missing formal Top3 evidence fails closed for the Top3 challenger.
missing = m.build_challenger_plan("top3_coverage", "R", "2026-11-02", rows, sig(5, "A", pairs), wide_results, None)
assert missing["planType"] == "skip"
assert "fail-closed" in missing["reason"]

# If all value-qualified Wide pairs are off Top3, the whole Top3 challenger holds.
strict_hold = m.build_challenger_plan(
    "top3_coverage", "R", "2026-11-02", rows, sig(5, "A", off_top3), wide_results, [1, 2, 3]
)
assert strict_hold["planType"] == "skip"
assert "正式Top3を含み" in strict_hold["reason"]

# Dates through 2026-11-01 are replay-only; 11/02 starts prospective evidence.
pre_day = m.build_challenger_plan("battle_a_only", "R", "2026-11-01", rows, sig(5, "A", pairs), wide_results, [1, 2, 3])
post_day = m.build_challenger_plan("battle_a_only", "R", "2026-11-02", rows, sig(5, "A", pairs), wide_results, [1, 2, 3])
assert pre_day["scope"] == "posthoc_replay"
assert post_day["scope"] == "prospective"

# Settlement is attached only after selection, and exact numeric pair identity is used.
selected = m.choose_wides_practical(sig(pairs=[wp(1, 4)]), 5.0, 1.10, limit=1)
assert "hit" not in selected[0] and "payoutYen" not in selected[0]
settled = m.attach_settlement("R", selected, {}, {("R", 1, 4): (True, 870)})
assert settled[0]["hit"] is True and settled[0]["payoutYen"] == 870

print("portfolio shadow v2 hardening tests OK")
