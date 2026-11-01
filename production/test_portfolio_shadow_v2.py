from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio_shadow_v2 as m
import portfolio_shadow as v1

assert m.VERSION == "portfolio-shadow-v2-20261101"
assert m.PROSPECTIVE_START == "2026-11-02"
assert m.VARIANTS == ("battle_a_only", "top3_coverage", "combined")
# V1 must stay frozen.
assert v1.VERSION == "portfolio-shadow-v1-20261019"
assert v1.PROSPECTIVE_START == "2026-10-24"
assert v1.HIGH_CHAOS_WIDE_MIN_PROB_PCT == 5.0
assert v1.HIGH_CHAOS_WIDE_MIN_EV == 1.10


def sig(level, grade, pairs=None):
    return {
        "eligible": True,
        "venue": "X",
        "raceNumber": 1,
        "raceName": "r",
        "startTime": "10:00",
        "chaos": {"level": level, "stars": "*"},
        "battle": {"grade": grade},
        "wideEvaluation": {"status": "live_odds_connected", "pairs": pairs or []},
    }


def wp(a, b, p=10.0, ev=1.2, low=12.0, rank=1, score=1.0):
    return {
        "number1": a,
        "name1": f"h{a}",
        "number2": b,
        "name2": f"h{b}",
        "aiWideProbability": p,
        "conservativeExpectedValue": ev,
        "wideOddsLow": low,
        "wideOddsHigh": low,
        "practicalScore": score,
        "practicalRank": rank,
    }


def c(cid, p, ev, odds, hit=None, payout=None):
    return {
        "id": str(cid),
        "label": str(cid),
        "probabilityPct": p,
        "oddsLow": odds,
        "oddsHigh": odds,
        "expectedMultiplierLow": ev,
        "unitYen": 100,
        "hit": hit,
        "payoutYen": payout,
    }


def row(kind, candidates, status="matched"):
    return {"betType": kind, "status": status, "candidates": candidates}


# Frozen formal Top3 comes only from formal live_10min Win evidence.
record = {
    "prediction": {
        "raceID": "R",
        "raceDate": "2026-11-02",
        "predictionTiming": "live_10min",
        "captureQuality": "formal_10min",
        "horses": [
            {"number": 1, "aiProbability": 11.0},
            {"number": 2, "aiProbability": 20.0},
            {"number": 3, "aiProbability": 15.0},
            {"number": 4, "aiProbability": 3.0},
        ],
    }
}
assert m._formal_top3_from_record(record, "R", "2026-11-02") == (2, 3, 1)
record["prediction"]["predictionTiming"] = "morning"
assert m._formal_top3_from_record(record, "R", "2026-11-02") == ()

# V1 practical-rank ordering would take 1-2 and 4-5. V2 excludes 4-5 because
# every selected Wide must contain a formal Top3 horse, then takes 1-2 and 1-3
# so that the two-ticket set covers all three formal Top3 horses.
pairs = [
    wp(1, 2, rank=1, score=9),
    wp(4, 5, rank=2, score=8),
    wp(1, 3, rank=9, score=1),
]
chosen = m.choose_wides_top3_coverage("R", sig(5, "A", pairs), {}, (1, 2, 3), 5.0, 1.10, 2)
assert {x["candidateID"] for x in chosen} == {"0102", "0103"}
assert all(set(m._wide_leg_numbers(x) or ()) & {1, 2, 3} for x in chosen)

# Fail closed when all otherwise-qualifying Wide candidates are outside formal Top3.
non_top3_pairs = [wp(4, 5, rank=1, score=9), wp(6, 7, rank=2, score=8)]
assert m.choose_wides_top3_coverage("R", sig(5, "A", non_top3_pairs), {}, (1, 2, 3), 5.0, 1.10, 2) == []
p = m.build_variant_plan("top3_coverage", "R", "2026-11-02", {}, sig(5, "A", non_top3_pairs), {}, (1, 2, 3))
assert p["planType"] == "skip"

# Fixed-width decoding prevents substring errors such as treating 10-11 as horse 1.
assert m._wide_leg_numbers({"candidateID": "1011"}) == (10, 11)
assert m._wide_leg_numbers({"candidateID": "0103"}) == (1, 3)
assert m._wide_leg_numbers({"candidateID": "bad"}) is None

# High-chaos S is isolated only in the A-only hypothesis; top3_coverage still
# permits S so the two hypotheses can be measured independently.
p = m.build_variant_plan("battle_a_only", "R", "2026-11-02", {}, sig(5, "S", pairs), {}, (1, 2, 3))
assert p["planType"] == "skip" and p["scope"] == "prospective"
p = m.build_variant_plan("combined", "R", "2026-11-02", {}, sig(5, "S", pairs), {}, (1, 2, 3))
assert p["planType"] == "skip"
p = m.build_variant_plan("top3_coverage", "R", "2026-11-02", {}, sig(5, "S", pairs), {}, (1, 2, 3))
assert p["planType"] == "wide_focus" and len(p["legs"]) == 2

# A-grade combined challenger applies strict Top3 coverage and preserves the v1 trio gate.
rows = {("R", "trio"): row("trio", [c("010203", 5.0, 1.20, 24.0, False, 0)])}
p = m.build_variant_plan("combined", "R", "2026-11-02", rows, sig(5, "A", pairs), {}, (1, 2, 3))
assert p["planType"] == "wide_plus_trio"
assert {x["candidateID"] for x in p["legs"] if x["betType"] == "wide"} == {"0102", "0103"}
assert p["wideTop3CoveredNumbers"] == [1, 2, 3]

# Missing formal T-10 evidence fails closed for Top3 variants.
p = m.build_variant_plan("top3_coverage", "R", "2026-11-02", {}, sig(5, "A", pairs), {}, ())
assert p["planType"] == "skip"

# Dates through 11/01 are explicitly replay-only; they must not become V2 proof.
p = m.build_variant_plan("combined", "R", "2026-11-01", {}, sig(5, "A", pairs), {}, (1, 2, 3))
assert p["scope"] == "posthoc_replay"

# Low/medium chaos behavior is inherited from frozen v1, only relabeled for V2 scope.
rows = {("R", "win"): row("win", [c(1, 25.0, 1.10, 4.0, True, 400)])}
p = m.build_variant_plan("combined", "R", "2026-11-02", rows, sig(2, "A"), {}, ())
assert p["planType"] == "win_single" and p["ruleVersion"] == m.VERSION and p["variant"] == "combined"

print("portfolio shadow v2 tests OK")
