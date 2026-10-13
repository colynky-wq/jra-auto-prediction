"""Course-specific early-lap references, trained on 2024 only."""
from __future__ import annotations

from collections import defaultdict
import math
import re
import unicodedata

import numpy as np

METHOD_VERSION = "course-early-lap-reference-2024-v1"
MIN_SAMPLES = 30


def condition_key(record: dict) -> tuple:
    detail = unicodedata.normalize("NFKC", record["courseDetail"])
    detail = re.sub(r"[\s・･、,()（）]", "", detail)
    return (record["venue"], record["surface"], int(record["distanceM"]), detail,
            record["going"], int(record["earlyRecordedDistanceM"]))


def eligible_record(record: dict) -> bool:
    time = record.get("earlyRecordedTimeSeconds")
    return (record.get("going") in {"良", "稍重", "重", "不良"}
            and record.get("earlyRecordedDistanceM") in {600, 650, 700, 750}
            and isinstance(time, (int, float)) and math.isfinite(time) and time > 0
            and record.get("validation") and all(record["validation"].values()))


def build_baselines(records: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    seen = set()
    for record in records:
        if record["raceID"] in seen:
            raise ValueError("duplicate race ID")
        seen.add(record["raceID"])
        if not record["raceDate"].startswith("2024-") or not eligible_record(record):
            continue
        if not record["raceID"].startswith("2024"):
            raise ValueError("race date/year identity mismatch")
        groups[condition_key(record)].append(record["earlyRecordedTimeSeconds"])
    output = []
    for key, values in sorted(groups.items()):
        if len(values) < MIN_SAMPLES:
            continue
        q25, median, q75 = np.quantile(values, [.25, .5, .75])
        output.append({"condition": list(key), "sampleRaces": len(values),
                       "fastThresholdSeconds": round(float(q25), 4),
                       "medianSeconds": round(float(median), 4),
                       "slowThresholdSeconds": round(float(q75), 4)})
    return output


def classify_actual(record: dict, baselines: list[dict]) -> str | None:
    if not eligible_record(record):
        return None
    reference = next((r for r in baselines if tuple(r["condition"]) == condition_key(record)), None)
    if reference is None:
        return None
    value = record["earlyRecordedTimeSeconds"]
    return "fast" if value < reference["fastThresholdSeconds"] else "slow" if value > reference["slowThresholdSeconds"] else "middle"
