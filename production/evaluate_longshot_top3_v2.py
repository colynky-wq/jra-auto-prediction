"""Compare the frozen longshot model and the two original T-10 baselines."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path

import formal_portfolio_capture as capture
from inspect_frozen_win_probabilities import official_podium, paired_race_uncertainty, prob
from longshot_top3_v2_runtime import (
    CAPTURE_START, FOLDER, entry_horses, model_spec, path_for, qualifying_horses,
    top_number, validate_record,
)
from market_shrink_shadow import fingerprint
from win_odds_freshness import validate_win_odds_freshness

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "research/LongshotTop3V2ForwardReview.json"
METHODS = {
    "candidate": "top3ProbabilityPct",
    "market": "marketTop3Probability",
    "formalAI": "aiTop3Probability",
}
CALIBRATION_BINS = ((0, 5), (5, 10), (10, 20), (20, 30), (30, 50), (50, 100))


def verified_fresh_win_odds(race: dict) -> tuple[bool, str]:
    """Verify frozen source provenance; never substitute HTTP fetch time.

    Legacy records lacking a source timestamp are retained in the older
    descriptive cohort, but never promoted into the 30-minute cohort.
    """
    evidence = race.get("winOddsFreshness")
    if not isinstance(evidence, dict):
        return False, "missing_source_provenance"
    provider = evidence.get("sourceProvider", "netkeiba_api")
    allowed_fields = {"netkeiba_api": "data.official_datetime",
                      "jra_official_web": "jra.html.interim_label"}
    if (evidence.get("sourceField") != allowed_fields.get(provider)
            or evidence.get("freshnessStatus") != "verified_within_30m"
            or evidence.get("publicationFreshnessStatus") != "verified_within_30m"
            or evidence.get("maxSourceAgeSeconds") != 1800):
        return False, "invalid_source_provenance"
    try:
        published = datetime.fromisoformat(race["capturedAtJST"])
        received = datetime.fromisoformat(evidence["receivedAtJST"])
        if (published.utcoffset() is None or received.utcoffset() is None
                or received > published):
            return False, "invalid_source_provenance"
        upstream = {"sourceProvider": provider,
                    "data": {"official_datetime": evidence["sourceUpdatedAtJST"]}}
        if provider == "jra_official_web":
            for field in ("sourceDisplayLabel", "sourceRaceDateJST",
                          "sourceRaceID", "sourceTimePrecision",
                          "sourcePageSha256", "sourceURL"):
                upstream[field] = evidence[field]
            if (upstream["sourceRaceID"] != race["raceID"]
                    or upstream["sourceRaceDateJST"] != race["raceDate"].replace("-", "")):
                return False, "invalid_source_provenance"
        at_receipt = validate_win_odds_freshness(upstream, received)
        at_publication = validate_win_odds_freshness(upstream, published)
        for recorded, computed in (
            (evidence["sourceAgeSecondsAtReceipt"], at_receipt["sourceAgeSecondsAtReceipt"]),
            (evidence["sourceAgeSecondsAtPublication"], at_publication["sourceAgeSecondsAtReceipt"]),
        ):
            if (isinstance(recorded, bool) or not isinstance(recorded, (int, float))
                    or not math.isfinite(recorded) or abs(recorded - computed) > 0.005):
                return False, "invalid_source_provenance"
    except (ValueError, TypeError, KeyError, OverflowError):
        return False, "invalid_source_provenance"
    return True, "verified_30m_at_publication"


def calibration_summary(samples):
    """Describe frozen probabilities without refitting or choosing new thresholds."""
    def metrics(rows):
        n = len(rows)
        observed = sum(row["outcome"] for row in rows)
        expected = math.fsum(row["probability"] for row in rows)
        return {
            "horses": n, "races": len({row["raceID"] for row in rows}),
            "raceDates": len({row["raceDate"] for row in rows}),
            "observedHits": observed, "expectedHits": expected,
            "meanPredictedPct": expected * 100 / n if n else None,
            "observedRatePct": observed * 100 / n if n else None,
            "brierScore": math.fsum((row["probability"] - row["outcome"]) ** 2
                                     for row in rows) / n if n else None,
        }
    result = metrics(samples)
    result["bins"] = []
    for low, high in CALIBRATION_BINS:
        rows = [row for row in samples if low <= row["probability"] * 100
                and (row["probability"] * 100 < high or high == 100)]
        result["bins"].append({"fromPctInclusive": low, "toPct": high, **metrics(rows)})
    return result


def aligned_candidates(race, record) -> list[dict]:
    if (record["capturedAtJST"] != race["capturedAtJST"]
            or record["sourcePredictionSha256"] != fingerprint(race)):
        raise ValueError("research prediction and formal T-10 inputs differ")
    expected = qualifying_horses(entry_horses(race))
    scores = record["horses"]
    by_number = {horse["number"]: horse for horse in scores}
    if (len(scores) != len(by_number)
            or set(by_number) != {horse["number"] for horse in expected}):
        raise ValueError("research and formal T-10 longshot fields differ")
    candidates = []
    for horse in expected:
        score = by_number[horse["number"]]
        if (score["name"], score["popularity"], score["winOdds"]) != (
                horse["name"], horse["popularity"], horse["odds"]):
            raise ValueError("longshot horse or odds differ from formal T-10")
        for key in ("marketTop3Probability", "aiTop3Probability"):
            if key in score and score[key] != horse[key]:
                raise ValueError("stored longshot baseline differs from formal T-10")
        row = {"number": horse["number"], "top3ProbabilityPct": score["top3ProbabilityPct"],
               **{key: horse[key] for key in ("marketTop3Probability", "aiTop3Probability")}}
        for field in METHODS.values():
            prob(row[field], field)
        candidates.append(row)
    if record["selectedHorseNumber"] != top_number(candidates, METHODS["candidate"]):
        raise ValueError("frozen longshot pick differs from model scores")
    return candidates


def evaluate(root, audit) -> dict:
    aligned = audit.get("aligned", {})
    rows = audit.get("races")
    if (audit.get("projectionState") != "verified" or aligned.get("countsAreCurrent") is not True
            or audit.get("cohortLockRecoveryRequired") is True or not audit.get("generatedAtJST")
            or not isinstance(rows, list) or len(rows) != aligned.get("racesObserved")
            or len({(row["raceDate"], row["raceID"]) for row in rows}) != len(rows)):
        raise ValueError("current verified aligned audit required")
    spec = model_spec()
    expected = [row for row in rows if row["raceDate"] >= CAPTURE_START and row.get("inputFingerprint")]
    excluded = {(row["raceDate"], row["raceID"])
                for row in audit.get("quality", {}).get("resultExclusions", [])}
    counts = Counter()
    hits = Counter()
    errors = {name: [] for name in METHODS}
    rejected = []
    samples, selected_samples = defaultdict(list), defaultdict(list)
    daily_errors = defaultdict(lambda: {method: [] for method in METHODS})
    fresh_hits = Counter()
    fresh_samples, fresh_selected_samples = defaultdict(list), defaultdict(list)
    fresh_errors = {name: [] for name in METHODS}
    fresh_daily_errors = defaultdict(lambda: {method: [] for method in METHODS})
    for audited in expected:
        day, race_id = audited["raceDate"], audited["raceID"]
        path = path_for(root, day, race_id)
        if not path.is_file():
            counts["missing"] += 1
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            validate_record(record, day, race_id, spec)
            bundle = capture.load(root, day, race_id)
            if bundle["fingerprint"] != audited["inputFingerprint"]:
                raise ValueError("original formal T-10 fingerprint mismatch")
            race = bundle["prediction"]
            candidates = aligned_candidates(race, record)
            fresh_ok, provenance_reason = verified_fresh_win_odds(race)
            counts["sourceProvenance_" + provenance_reason] += 1
            if audited.get("resultState") != "matched" or (day, race_id) in excluded:
                counts["pendingOrExcluded"] += 1
                continue
            if not candidates:
                counts["noQualifyingHorse"] += 1
                continue
            entrants = [horse["number"] for horse in race["horses"]]
            try:
                podium = official_podium(audited["actualFinish"], entrants, 3, race_id)
            except ValueError as exc:
                if "ambiguous podium dead heat" not in str(exc):
                    raise
                podium = None
            if podium is None:
                counts["incompletePodium"] += 1
                continue
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            counts["rejected"] += 1
            if len(rejected) < 30:
                rejected.append({"raceID": race_id, "raceDate": day, "reason": str(exc)})
            continue

        counts["scored"] += 1
        counts["scoredHorses"] += len(candidates)
        if fresh_ok:
            counts["freshScored"] += 1
            counts["freshScoredHorses"] += len(candidates)
        for method, field in METHODS.items():
            chosen = top_number(candidates, field)
            hits[method] += int(chosen in podium)
            if fresh_ok:
                fresh_hits[method] += int(chosen in podium)
            race_error = math.fsum(
                (horse[field] / 100 - (horse["number"] in podium)) ** 2 for horse in candidates
            ) / len(candidates)
            errors[method].append(race_error)
            daily_errors[day][method].append(race_error)
            if fresh_ok:
                fresh_errors[method].append(race_error)
                fresh_daily_errors[day][method].append(race_error)
            for horse in candidates:
                sample = {"raceID": race_id, "raceDate": day, "number": horse["number"],
                          "probability": prob(horse[field], field),
                          "outcome": int(horse["number"] in podium)}
                samples[method].append(sample)
                if fresh_ok:
                    fresh_samples[method].append(sample)
                if horse["number"] == chosen:
                    selected_samples[method].append(sample)
                    if fresh_ok:
                        fresh_selected_samples[method].append(sample)

    total, n = len(expected), counts["scored"]
    fresh_n = counts["freshScored"]
    fresh_cohort = {
        "status": ("insufficient_verified_30m_evidence" if fresh_n < 30
                   else "verified_30m_descriptive_comparison"),
        "acceptedSourceTimestampFields": ["data.official_datetime",
                                          "jra.html.interim_label"],
        "maximumAgeSecondsAtPublication": 1800,
        "verifiedCapturedRaces": counts["sourceProvenance_verified_30m_at_publication"],
        "missingProvenanceCaptures": counts["sourceProvenance_missing_source_provenance"],
        "invalidProvenanceCaptures": counts["sourceProvenance_invalid_source_provenance"],
        "matchedRaces": fresh_n,
        "matchedRaceDates": len(fresh_daily_errors),
        "matchedCandidateHorses": counts["freshScoredHorses"],
        "topPickComparison": {method: {
            "top3Hits": fresh_hits[method],
            "top3RatePct": 100 * fresh_hits[method] / fresh_n if fresh_n else None,
            "brierEqualRace": (math.fsum(fresh_errors[method]) / fresh_n if fresh_n else None),
        } for method in METHODS},
        "pairedCandidateBrierDifference": {
            method: paired_race_uncertainty([a - b for a, b in zip(
                fresh_errors["candidate"], fresh_errors[method], strict=True,
            )]) for method in ("market", "formalAI")
        },
        "calibrationAllQualifyingHorses": {
            method: calibration_summary(fresh_samples[method]) for method in METHODS
        },
        "calibrationSelectedHorse": {
            method: calibration_summary(fresh_selected_samples[method]) for method in METHODS
        },
        "dailyMatchedBrier": {
            day: {method: {"races": len(values),
                           "meanBrier": math.fsum(values) / len(values)}
                  for method, values in methods.items()}
            for day, methods in sorted(fresh_daily_errors.items())
        },
        "promotionApproved": False,
        "limitations": [
            "Requires frozen source update time verified within 30 minutes at publication.",
            "Provider timestamp semantics need live verification; receipt time alone is insufficient.",
            "Small cohorts do not establish profitable betting or model superiority.",
        ],
    }
    return {
        "schemaVersion": 1,
        "researchOnly": True,
        "sourceAuditGeneratedAtJST": audit["generatedAtJST"],
        "model": spec,
        "sourceAuditFingerprint": fingerprint(audit),
        "calibrationBinEdgesPct": [0, 5, 10, 20, 30, 50, 100],
        "matchedRaceDates": len(daily_errors),
        "freshOddsVerified30m": fresh_cohort,
        "eligibleAuditedRaces": total,
        "storedCandidateCaptures": sum(1 for _ in Path(root).glob(f"reports/*/{FOLDER}/*.json")),
        "missingCandidateCaptures": counts["missing"],
        "rejectedCaptures": counts["rejected"],
        "pendingOrExcludedRaces": counts["pendingOrExcluded"],
        "racesWithoutEligibleHorse": counts["noQualifyingHorse"],
        "incompletePodiumRaces": counts["incompletePodium"],
        "matchedRaces": n,
        "matchedCandidateHorses": counts["scoredHorses"],
        "topPickComparison": {method: {
            "top3Hits": hits[method],
            "top3RatePct": 100 * hits[method] / n if n else None,
            "brierEqualRace": math.fsum(errors[method]) / n if n else None,
        } for method in METHODS},
        "pairedCandidateBrierDifference": {
            method: paired_race_uncertainty([a - b for a, b in zip(
                errors["candidate"], errors[method], strict=True,
            )]) for method in ("market", "formalAI")
        },
        "calibrationAllQualifyingHorses": {
            method: calibration_summary(samples[method]) for method in METHODS
        },
        "calibrationSelectedHorse": {
            method: calibration_summary(selected_samples[method]) for method in METHODS
        },
        "dailyMatchedBrier": {
            day: {method: {"races": len(values), "meanBrier": math.fsum(values) / len(values)}
                  for method, values in methods.items()}
            for day, methods in sorted(daily_errors.items())
        },
        "rejectedExamples": rejected,
    }


def markdown(report) -> str:
    lines = [
        "# 穴馬3着以内モデル 発走前の比較", "",
        "発走10分前に6番人気以下かつ単勝10倍以上の馬から、各方式で1頭を選びます。",
        "予測を同じ時点にそろえ、結果を照合できた同じレースで集計します。", "",
        f"- 参照した結果監査 {report['sourceAuditGeneratedAtJST']}",
        f"- 保存済み予測 {report['storedCandidateCaptures']} レース",
        f"- 結果照合済み {report['matchedRaces']} レース",
        f"- 監査対象のうち保存なし {report['missingCandidateCaptures']} レース",
        f"- 照合時に除外した保存記録 {report['rejectedCaptures']} レース", "",
        "## 元データ更新30分以内と検証できたレースだけ", "",
        "HTTP取得時刻ではなく元データ更新時刻から30分以内で絞った、別集計です。",
        f"- 鮮度検証済みの保存レース {report['freshOddsVerified30m']['verifiedCapturedRaces']}",
        f"- 元時刻が不明な保存レース {report['freshOddsVerified30m']['missingProvenanceCaptures']}",
        f"- 元時刻の整合性が不正な保存レース {report['freshOddsVerified30m']['invalidProvenanceCaptures']}",
        f"- 鮮度検証済み・結果照合済み {report['freshOddsVerified30m']['matchedRaces']} レース",
        "",
        "| 選び方 | 3着以内の回数 | 3着内率 | Brierスコア |",
        "|---|---:|---:|---:|",
        "| 選び方 | 3着以内の回数 | 3着内率 | 確率誤差（Brier） |",
        "|---|---:|---:|---:|",
    ]
    labels = {"candidate": "穴馬専用モデル", "market": "市場", "formalAI": "現行AI"}
    for method, result in report["freshOddsVerified30m"]["topPickComparison"].items():
        if result["top3RatePct"] is None:
            lines.append(f"| {labels[method]} | — | — | — |")
        else:
            lines.append(f"| {labels[method]} | {result['top3Hits']} | "
                         f"{result['top3RatePct']:.2f}% | {result['brierEqualRace']:.6f} |")
    lines += ["", "## 従来の全保存レース比較（鮮度未確認を含む）", ""]
    for method, result in report["topPickComparison"].items():
        if result["top3RatePct"] is None:
            lines.append(f"| {labels[method]} | — | — | — |")
        else:
            lines.append(f"| {labels[method]} | {result['top3Hits']} | "
                         f"{result['top3RatePct']:.2f}% | {result['brierEqualRace']:.6f} |")
    lines += ["", "## 予測確率と実際の3着内率", "",
              "対象の穴馬全頭を同じレースで比較。各方式が選んだ1頭の集計もJSONに保存します。",
              f"照合できた開催日数 {report['matchedRaceDates']} 日。", "",
              "| 方式 | 予測確率の帯 | 対象馬 | 予測平均 | 実際の3着内率 |",
              "|---|---|---:|---:|---:|"]
    for method, summary in report["calibrationAllQualifyingHorses"].items():
        for group in summary["bins"]:
            predicted = group["meanPredictedPct"]
            actual = group["observedRatePct"]
            predicted_text = f"{predicted:.2f}%" if predicted is not None else "—"
            actual_text = f"{actual:.2f}%" if actual is not None else "—"
            lines.append(f"| {labels[method]} | {group['fromPctInclusive']}〜{group['toPct']}% | "
                         f"{group['horses']} | {predicted_text} | {actual_text} |")
    lines += ["", "確率帯は下限を含み上限を含めず、最後の帯は100%も含みます。",
              "保存欠損・結果待ち・照合除外を集計し、結果の良いレースだけを選び直しません。",
              "同じレースの馬は独立ではありません。開催日数と日別の確率誤差も併記します。"]
    return "\n".join(lines) + "\n"


def main() -> None:
    audit = json.loads((ROOT / "AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    report = evaluate(ROOT, audit)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                      encoding="utf-8")
    OUTPUT.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print("LONGSHOT_TOP3_V2_COMPARISON", report["storedCandidateCaptures"], report["matchedRaces"])


if __name__ == "__main__":
    main()
