"""Fail-closed read-only checks for a rebuilt Wide-axis app-facing summary.

This verifies one summary covers exactly the genuine frozen new-rule files
visible in its checkout. It cannot verify the original pre-post source alone;
wide_axis_shadow.build independently validates every included archive.
"""
from pathlib import Path
import json
import re


def check(root):
    root=Path(root)
    path=root/"reports/WideAxisShadow.json"
    if not path.is_file():
        raise ValueError("no Wide-axis summary")
    report=json.loads(path.read_text(encoding="utf-8"))
    counts=report.get("counts",{})
    if report.get("schemaVersion") != 1 or report.get("ruleVersion") != "wide-axis-v1-20261014":
        raise ValueError("Wide-axis source/rule identity invalid")
    if report.get("autoBetEnabled") is not False or report.get("moneyAllocationEnabled") is not False:
        raise ValueError("automatic money allocation is forbidden")
    if not isinstance(report.get("generatedAtJST"),str) or not re.fullmatch(r"20\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",report["generatedAtJST"]):
        raise ValueError("missing timestamp")
    keys=("recordedRaces","held","awaitingResults","matched","entrantChanged","invalid","selectedBets")
    if any(type(counts.get(k)) is not int or counts[k] < 0 for k in keys):
        raise ValueError("invalid status totals")
    recorded=sum(1 for _ in (root/"reports").glob("20??????/wide_axis_v1/[0-9]*.json")
                 if re.fullmatch(r"\d{12}\.json", _.name))
    if counts["recordedRaces"] != recorded:
        raise ValueError(f"Wide-axis summary is stale or incomplete: {counts['recordedRaces']} vs {recorded}")
    if counts["recordedRaces"] != sum(counts[k] for k in ("held","awaitingResults","matched","entrantChanged","invalid")):
        raise ValueError("Wide-axis partition mismatch")
    if counts["selectedBets"] < counts["matched"]:
        raise ValueError("invalid settled pair total")
    return recorded


if __name__=="__main__":
    print("WIDE_AXIS_RECONCILE_VERIFIED",check(Path(__file__).resolve().parents[1]))
