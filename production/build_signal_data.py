from __future__ import annotations

import itertools
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RACEDATA_PATH = REPO / "RaceData.json"
OUT_PATH = REPO / "SignalData.json"
REPORTS_DIR = REPO / "reports"

SIGNAL_VERSION = "keibaai-signals-v1"

# Frozen from the 2024 reference sample in research/wide-budget-model-v1.
# The score definitions were evaluated on an untouched 2025 sample.
CHAOS_CUTS = [
    0.44909320762324856,
    0.5675705643046633,
    0.6491365420737711,
    0.7019851917215711,
]
BATTLE_CUTS = [
    0.29750323298090603,
    0.35645536043857456,
    0.4193528947422855,
    0.5039026883811242,
]


def finite(value, default=None):
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def prob_pct(value) -> float:
    x = finite(value, 0.0)
    return max(0.0, min(1.0, float(x) / 100.0))


def normalized(values: list[float], total: float = 1.0) -> list[float]:
    xs = [max(float(x), 1e-12) for x in values]
    s = sum(xs)
    if s <= 0:
        return [float(total) / max(len(xs), 1)] * len(xs)
    return [x / s * float(total) for x in xs]


def normalized_entropy(p: list[float]) -> float:
    q = normalized(p, 1.0)
    n = len(q)
    if n <= 1:
        return 0.0
    return -sum(x * math.log(max(x, 1e-12)) for x in q) / math.log(n)


def grade(value: float, cuts: list[float], labels: list[str]) -> str:
    if value <= cuts[0]:
        return labels[0]
    if value <= cuts[1]:
        return labels[1]
    if value <= cuts[2]:
        return labels[2]
    if value <= cuts[3]:
        return labels[3]
    return labels[4]


def maximum_entropy_top3_pair_probabilities(target_marginals: list[float]) -> list[list[float]]:
    """Pair-inclusion probabilities for a fixed-size three-horse set model."""
    n = len(target_marginals)
    if n < 3:
        return [[0.0] * n for _ in range(n)]

    target = [min(max(float(x), 1e-8), 1.0 - 1e-8) for x in target_marginals]
    target = normalized(target, 3.0)
    for _ in range(20):
        over = [i for i, x in enumerate(target) if x >= 1.0]
        if not over:
            break
        fixed_sum = 0.0
        free = []
        for i in range(n):
            if i in over:
                target[i] = 1.0 - 1e-8
                fixed_sum += target[i]
            else:
                free.append(i)
        remain = 3.0 - fixed_sum
        free_sum = sum(target[i] for i in free)
        if free and free_sum > 0:
            for i in free:
                target[i] = target[i] / free_sum * remain

    triples = list(itertools.combinations(range(n), 3))
    q = [1.0 / len(triples)] * len(triples)
    membership = [[] for _ in range(n)]
    for k, tri in enumerate(triples):
        for i in tri:
            membership[i].append(k)

    for _ in range(3000):
        max_err = 0.0
        for i in range(n):
            m = sum(q[k] for k in membership[i])
            t = target[i]
            max_err = max(max_err, abs(m - t))
            if m <= 1e-15 or m >= 1.0 - 1e-15:
                continue
            multiplier = t * (1.0 - m) / (m * (1.0 - t))
            if not math.isfinite(multiplier) or multiplier <= 0:
                continue
            member_set = set(membership[i])
            total = 0.0
            for k in range(len(q)):
                if k in member_set:
                    q[k] *= multiplier
                total += q[k]
            if total > 0:
                inv = 1.0 / total
                q = [x * inv for x in q]
        if max_err < 1e-10:
            break

    pair = [[0.0] * n for _ in range(n)]
    for weight, (a, b, c) in zip(q, triples):
        for i, j in ((a, b), (a, c), (b, c)):
            pair[i][j] += weight
            pair[j][i] += weight
    return pair


def build_race_signal(race: dict) -> dict | None:
    horses = [dict(h) for h in (race.get("horses") or [])]
    if len(horses) < 4:
        return None
    if "障" in str(race.get("course") or "") or "障害" in str(race.get("raceName") or ""):
        return {
            "raceDate": race.get("raceDate"),
            "raceID": str(race.get("raceID") or ""),
            "venue": race.get("venue"),
            "raceNumber": race.get("raceNumber"),
            "signalVersion": SIGNAL_VERSION,
            "eligible": False,
            "excludedReason": "障害レースは平地モデルの対象外",
        }

    numbers, market, ai, market_t3, ai_t3 = [], [], [], [], []
    for h in horses:
        try:
            number = int(h.get("number"))
        except Exception:
            return None
        numbers.append(number)
        market.append(prob_pct(h.get("marketProbability")))
        ai.append(prob_pct(h.get("aiProbability")))
        market_t3.append(prob_pct(h.get("marketTop3Probability")))
        ai_t3.append(prob_pct(h.get("aiTop3Probability")))

    market = normalized(market, 1.0)
    ai = normalized(ai, 1.0)
    market_t3 = normalized(market_t3, 3.0)
    ai_t3 = normalized(ai_t3, 3.0)

    market_order = sorted(range(len(horses)), key=lambda i: (-market[i], numbers[i]))
    market_rank = {numbers[i]: rank + 1 for rank, i in enumerate(market_order)}
    market_p1 = market[market_order[0]]
    market_p2 = market[market_order[1]]

    uncertainty = max(0.0, min(1.0, (1.0 - market_p1) / 0.85))
    entropy = normalized_entropy(market)
    closeness = max(0.0, min(1.0, 1.0 - (market_p1 - market_p2) / 0.25))
    disagreement_l1 = sum(abs(a - m) for a, m in zip(ai, market)) / 2.0
    disagreement = max(0.0, min(1.0, disagreement_l1 / 0.15))

    longshot_edge = 0.0
    for i, no in enumerate(numbers):
        if market_rank[no] >= 6:
            longshot_edge = max(longshot_edge, max(ai_t3[i] - market_t3[i], 0.0))
    longshot_signal = max(0.0, min(1.0, longshot_edge / 0.20))
    field_factor = max(0.0, min(1.0, (len(horses) - 8.0) / 10.0))

    chaos_score = (
        0.30 * uncertainty
        + 0.25 * entropy
        + 0.20 * closeness
        + 0.15 * disagreement
        + 0.07 * longshot_signal
        + 0.03 * field_factor
    )
    chaos_label = grade(chaos_score, CHAOS_CUTS, ["★☆☆☆☆", "★★☆☆☆", "★★★☆☆", "★★★★☆", "★★★★★"])
    chaos_level = chaos_label.count("★")

    candidate_rows = []
    for i, h in enumerate(horses):
        ratio = ai[i] / max(market[i], 1e-9)
        edge = ai[i] - market[i]
        ratio_component = max(0.0, min(1.0, (ratio - 1.0) / 0.40))
        edge_component = max(0.0, min(1.0, edge / 0.06))
        support_component = max(0.0, min(1.0, ai[i] / 0.10))
        score = 0.55 * ratio_component + 0.30 * edge_component + 0.15 * support_component
        positive = ratio > 1.0 and edge > 0.0 and ai[i] >= 0.01
        candidate_rows.append({
            "number": numbers[i], "name": str(h.get("name") or ""), "score": score,
            "positive": positive, "aiProbability": ai[i], "marketProbability": market[i],
            "aiMarketRatio": ratio, "aiMarketEdge": edge,
        })
    positive_rows = [x for x in candidate_rows if x["positive"]]
    battle = max(positive_rows or candidate_rows, key=lambda x: (x["score"], x["aiProbability"]))
    battle_score = battle["score"]
    battle_grade = grade(battle_score, BATTLE_CUTS, ["見送り", "C", "B", "A", "S"])

    longshots = []
    for i, h in enumerate(horses):
        odds = finite(h.get("odds"))
        ratio = ai[i] / max(market[i], 1e-9)
        edge = ai[i] - market[i]
        rank = market_rank[numbers[i]]
        if rank >= 6 and odds is not None and odds >= 8.0 and ai[i] >= 0.01 and ratio >= 1.20:
            longshots.append({
                "number": numbers[i], "name": str(h.get("name") or ""), "odds": round(odds, 1),
                "marketRank": rank, "marketProbability": round(market[i] * 100.0, 3),
                "aiProbability": round(ai[i] * 100.0, 3), "aiTop3Probability": round(ai_t3[i] * 100.0, 3),
                "aiMarketRatio": round(ratio, 4), "aiMarketEdgePct": round(edge * 100.0, 3),
                "researchBand": "1.4以上（標本小）" if ratio >= 1.4 else "1.2〜1.4",
            })
    longshots.sort(key=lambda x: (-x["aiMarketRatio"], -x["aiTop3Probability"], x["number"]))

    market_pair = maximum_entropy_top3_pair_probabilities(market_t3)
    ai_pair = maximum_entropy_top3_pair_probabilities(ai_t3)
    wide_pairs = []
    for i in range(len(horses)):
        for j in range(i + 1, len(horses)):
            mp, ap = market_pair[i][j], ai_pair[i][j]
            edge = ap - mp
            ratio = ap / max(mp, 1e-9)
            wide_pairs.append({
                "number1": numbers[i], "name1": str(horses[i].get("name") or ""),
                "number2": numbers[j], "name2": str(horses[j].get("name") or ""),
                "marketWideProbability": round(mp * 100.0, 3), "aiWideProbability": round(ap * 100.0, 3),
                "probabilityEdgePct": round(edge * 100.0, 3), "aiMarketProbabilityRatio": round(ratio, 4),
            })
    wide_pairs.sort(key=lambda x: (-x["probabilityEdgePct"], -x["aiWideProbability"], x["number1"], x["number2"]))

    return {
        "raceDate": race.get("raceDate"), "raceID": str(race.get("raceID") or ""),
        "venue": race.get("venue"), "raceNumber": race.get("raceNumber"), "startTime": race.get("startTime"),
        "raceName": race.get("raceName"), "course": race.get("course"), "fieldSize": len(horses),
        "signalVersion": SIGNAL_VERSION, "eligible": True,
        "chaos": {
            "score": round(chaos_score, 6), "level": chaos_level, "stars": chaos_label,
            "label": ["比較的堅め", "やや堅め", "中間", "やや混戦", "かなり混戦"][chaos_level - 1],
            "validatedOn": "2025 untouched holdout; cutoffs fixed on 2024",
        },
        "battle": {
            "score": round(battle_score, 6), "grade": battle_grade,
            "candidateNumber": battle["number"], "candidateName": battle["name"],
            "hasPositiveValueCandidate": bool(positive_rows),
            "aiProbability": round(battle["aiProbability"] * 100.0, 3),
            "marketProbability": round(battle["marketProbability"] * 100.0, 3),
            "aiMarketRatio": round(battle["aiMarketRatio"], 4),
            "aiMarketEdgePct": round(battle["aiMarketEdge"] * 100.0, 3),
            "meaning": "市場との評価差に注目する度合い。的中しやすさや利益保証ではありません。",
        },
        "longshotCandidates": longshots,
        "wideEvaluation": {
            "status": "probability_only",
            "method": "fixed-size Top3 maximum-entropy joint distribution",
            "note": "ワイド実オッズ未接続のため、買い目の期待値ではなく同時3着内確率の比較です。",
            "pairs": wide_pairs[:20],
        },
        "ticketPlan": {
            "status": "wide_odds_pending", "enabled": False,
            "message": "ワイドの発走前実オッズを保存・実運用で検証できるまで金額配分は行いません。",
        },
    }


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix=path.name+'.',
                                         suffix='.tmp', delete=False, encoding='utf-8') as f:
            temporary = Path(f.name)
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    if not RACEDATA_PATH.exists():
        print("RaceData.json not found; nothing to publish.")
        return
    with RACEDATA_PATH.open("r", encoding="utf-8") as f:
        races = json.load(f)
    if not isinstance(races, list):
        raise RuntimeError("RaceData.json root must be a list")

    output = []
    for race in races:
        built = build_race_signal(race)
        if built is not None:
            output.append(built)
    output.sort(key=lambda r: (str(r.get("raceDate") or ""), str(r.get("startTime") or ""), str(r.get("raceID") or "")))

    payload = {
        "schemaVersion": 1, "signalVersion": SIGNAL_VERSION,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"), "formalSignals": True,
        "researchBasis": {
            "cutoffs": "2024", "untouchedEvaluation": "2025",
            "liveTiming": "T-10前後のRaceDataを使用。歴史検証のオッズ時点との差は実運用成績で継続確認。",
        },
        "races": output,
    }
    write_json(OUT_PATH, payload)

    by_date: dict[str, list[dict]] = {}
    for row in output:
        d = str(row.get("raceDate") or "").replace("-", "")
        if len(d) == 8 and d.isdigit():
            by_date.setdefault(d, []).append(row)
    for d, rows in by_date.items():
        day_payload = dict(payload)
        day_payload["races"] = rows
        write_json(REPORTS_DIR / d / "SignalData.json", day_payload)

    print("signal version:", SIGNAL_VERSION)
    print("race signals:", len(output))
    print("output:", OUT_PATH)


if __name__ == "__main__":
    main()
