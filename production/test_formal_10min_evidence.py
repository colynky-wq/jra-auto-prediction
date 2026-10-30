from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import formal_10min_evidence as m


def row(rid="R1", day="2026-10-24", start="10:05"):
    return {"raceID": rid, "raceDate": day, "startTime": start}


def write_sidecar(root: Path, payload: dict, rid="R1", day="2026-10-24"):
    path = root / "reports" / day.replace("-", "") / "all_bet_v1" / "win" / f"{rid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def payload(
    rid="R1",
    day="2026-10-24",
    start="10:05",
    timing="live_10min",
    captured="2026-10-24T09:53:45+09:00",
    entry_captured="2026-10-24T09:53:26+09:00",
    trigger=None,
):
    prediction = {
        "raceID": rid,
        "raceDate": day,
        "startTime": start,
        "predictionTiming": timing,
        "capturedAtJST": captured,
    }
    if trigger is not None:
        prediction["triggerMinutesToPost"] = trigger
    return {
        "schemaVersion": 1,
        "betType": "win",
        "prediction": prediction,
        "entryEvidence": {
            "raceID": rid,
            "raceDate": day,
            "scheduledPostJST": f"{day}T{start}:00+09:00",
            "capturedAtJST": entry_captured,
        },
    }


with TemporaryDirectory() as td:
    root = Path(td)

    write_sidecar(root, payload())
    out = m.inspect_formal_10min_evidence(root, row())
    assert out["eligible"] is True
    assert out["reason"] == "formal_10min_verified"
    assert 8 <= out["minutesToPost"] <= 12

    out = m.inspect_formal_10min_evidence(root, row("MISSING"))
    assert out["eligible"] is False
    assert out["reason"] == "formal_win_sidecar_missing"

    write_sidecar(root, payload(rid="MORNING", timing="morning"), rid="MORNING")
    out = m.inspect_formal_10min_evidence(root, row("MORNING"))
    assert out["eligible"] is False
    assert out["reason"] == "prediction_not_formal_10min"

    write_sidecar(root, payload(rid="LATE", timing="late_recovery", captured="2026-10-24T09:59:00+09:00",
                                entry_captured="2026-10-24T09:59:00+09:00"), rid="LATE")
    out = m.inspect_formal_10min_evidence(root, row("LATE"))
    assert out["eligible"] is False

    write_sidecar(root, payload(rid="OUTSIDE", captured="2026-10-24T09:57:30+09:00",
                                entry_captured="2026-10-24T09:57:30+09:00"), rid="OUTSIDE")
    out = m.inspect_formal_10min_evidence(root, row("OUTSIDE"))
    assert out["eligible"] is False
    assert out["reason"] == "captured_outside_formal_window"

    bad = payload(rid="OTHER")
    write_sidecar(root, bad, rid="MISMATCH")
    out = m.inspect_formal_10min_evidence(root, row("MISMATCH"))
    assert out["eligible"] is False
    assert out["reason"] == "prediction_race_id_mismatch"

    write_sidecar(root, payload(rid="TRIGGER", trigger=10.0), rid="TRIGGER")
    out = m.inspect_formal_10min_evidence(root, row("TRIGGER"))
    assert out["eligible"] is True

    write_sidecar(root, payload(rid="BADTRIGGER", trigger=7.0), rid="BADTRIGGER")
    out = m.inspect_formal_10min_evidence(root, row("BADTRIGGER"))
    assert out["eligible"] is False
    assert out["reason"] == "trigger_outside_formal_window"

print("formal 10-minute evidence tests OK")
