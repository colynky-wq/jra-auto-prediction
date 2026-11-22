"""Read-only GitHub Actions diagnostic for the live result derivation chain.

Works only in a throwaway CI checkout. It never pushes, changes model
selection, creates formal T-10 recordings, or publishes outcome metrics.
"""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time

JST = timezone(timedelta(hours=9))
PIPELINE = [
    "same_day_shadow_runtime.py",
    "evaluate_same_day_shadow.py",
    "same_day_shadow_portfolio_review.py",
    "build_performance_data.py",
    "wide_forward_validation.py",
    "wide_validation_summary.py",
    "ticket_plan_shadow.py",
    "wide_axis_shadow.py",
]

def main():
    root = Path(__file__).resolve().parents[1]
    if not (root / "RaceData.json").is_file() or not (root / "ResultData.json").is_file():
        raise SystemExit("No published race/result inputs")
    race = json.loads((root / "RaceData.json").read_text())
    results = json.loads((root / "ResultData.json").read_text())
    if not isinstance(race, list) or not isinstance(results, list):
        raise SystemExit("Source race/result arrays invalid")
    day = datetime.now(JST).date().isoformat()
    ctx = {"checkedAtJST": datetime.now(JST).isoformat(),
           "date": day, "races": len(race), "results": len(results),
           "raceDays": sorted({x.get("raceDate", "") for x in race}),
           "resultDays": sorted({x.get("raceDate", "") for x in results}),
           "steps": [], "publication": False}
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "KEIBAAI_WIDE_VALIDATE_DATE": day.replace("-", ""),
           "KEIBAAI_RESULT_DATE": day.replace("-", ""),
           "KEIBAAI_SAME_DAY_DATE": day.replace("-", "")}
    output = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "keibaai-downstream-diagnostic.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    print("PUBLISHED_SOURCE_INPUTS", json.dumps({k:v for k,v in ctx.items() if k not in ("steps",)}, ensure_ascii=False),flush=True)
    for name in PIPELINE:
        start = time.monotonic()
        try:
            result = subprocess.run([sys.executable, "-u", str(root/"production"/name)],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=200)
            step = {"name": name, "returnCode": result.returncode,
                    "elapsedSeconds": round(time.monotonic() - start, 2),
                    "stdoutTail": result.stdout[-4500:], "stderrTail": result.stderr[-4500:]}
            ctx["steps"].append(step)
            print("DOWNSTREAM_DIAGNOSTIC", json.dumps(step, ensure_ascii=False),flush=True)
            if result.returncode:
                ctx["status"] = "first_failure_found"
                break
        except subprocess.TimeoutExpired as exc:
            ctx["steps"].append({"name": name, "status": "timeout",
                                "elapsedSeconds": round(time.monotonic()-start,2)})
            ctx["status"] = "first_timeout_found"
            break
    else:
        ctx["status"] = "all_stages_succeeded_in_checkout"
    output.write_text(json.dumps(ctx, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("DOWNSTREAM_DIAGNOSTIC_RESULT", ctx["status"],"report",output,flush=True)
    return int(ctx["status"] != "all_stages_succeeded_in_checkout")

if __name__ == "__main__":
    sys.exit(main())
