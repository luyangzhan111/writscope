"""Replay repeat mock trajectories before testing isolated comparison wiring.

This module cannot score real runs and intentionally suppresses mock metrics.
"""
import json
from pathlib import Path

from scripts.s4_p2_report import compare_axes
from scripts.s4_repeat_offline import verify
from writscope.artifacts import digest


def report(bundle_root, scripts_path, run, reference_path, disposition_path):
    # Verify before opening any evaluator reference or disposition file.
    replay = verify(bundle_root, scripts_path, run)
    ref_raw = Path(reference_path).read_bytes()
    refs = json.loads(ref_raw)
    dispositions = json.loads(Path(disposition_path).read_text(encoding="utf-8"))
    if (refs.get("version") != "s4_p3_author_draft_v1"
            or dispositions.get("version") != "s4_project_disposition_v1"
            or dispositions.get("reference_sha256") != digest(ref_raw)):
        raise ValueError("reference or disposition binding mismatch")
    results = []
    for repeat, subrun in enumerate(run["rounds"], start=1):
        for job in subrun["jobs"]:
            case = job["case_id"]
            if dispositions["cases"][case]["expected"] != refs["cases"][case]["expected"]:
                raise ValueError("reference disagreement")
            compare_axes(refs["cases"][case]["expected"], job["final"])
            results.append((case, job["method"], repeat))
    planned = [(j["case_id"], j["method"], j["repeat_index"]) for j in run["schedule"]["plan"]["jobs"]]
    if len(set(planned)) != len(planned) or len(set(results)) != len(results) or not set(results) <= set(planned):
        raise ValueError("repeat identity mismatch")
    return {"version": "s4_repeat_evaluator_offline_v1", "execution_kind": "offline_mock",
            "replay": replay, "planned_jobs": len(planned), "checked_jobs": len(results),
            "reference_sha256": digest(ref_raw), "disposition_sha256": digest(Path(disposition_path).read_bytes()),
            "reference_status": "project_author_draft_not_independently_reviewed",
            "scoring": "not_scored_scripted_mock", "accuracy": None, "actual_model_calls": 0}
