"""Replay mock repeats, check private reference binding, exercise metrics without publishing scores."""
import json
from pathlib import Path

from scripts.s4_metric_core import aggregate
from scripts.s4_repeat_offline import verify
from writscope.artifacts import digest


def check(bundle_root, scripts_path, run, reference_path, disposition_path):
    replay = verify(bundle_root, scripts_path, run)
    ref_raw = Path(reference_path).read_bytes()
    refs = json.loads(ref_raw)
    dispositions = json.loads(Path(disposition_path).read_text(encoding="utf-8"))
    if (refs.get("version") != "s4_p3_author_draft_v1" or
            dispositions.get("version") != "s4_project_disposition_v1" or
            dispositions.get("reference_sha256") != digest(ref_raw)):
        raise ValueError("reference binding mismatch")
    cases = run["schedule"]["plan"]["cases"]
    case_refs = {}
    for case in cases:
        item = dispositions["cases"][case]
        if item["expected"] != refs["cases"][case]["expected"]:
            raise ValueError("reference mismatch")
        expected_scope = ("excluded_ambiguity" if item["expected"]["authorization"] == "ambiguous"
                          else "include_author_draft")
        if item.get("main_authorization_and_decision") != expected_scope:
            raise ValueError("unsupported disposition scope; revise evaluator before proceeding")
        case_refs[case] = {"expected": item["expected"], "disputed": False}
    pairs = {key: pair["cases"] for key, pair in dispositions["pairs"].items()
             if all(c in case_refs for c in pair["cases"])}
    actual = {(j["case_id"], j["method"], repeat): j
              for repeat, subrun in enumerate(run["rounds"], 1) for j in subrun["jobs"]}
    jobs = []
    for row in run["rows"]:
        key = (row["case_id"], row["method"], row["repeat_index"])
        original = actual.get(key)
        if original is None and row["status"] != "not_run":
            raise ValueError("missing trajectory")
        final = original["final"] if original else None
        prediction = {axis: final[axis] for axis in ("impact", "authorization", "decision")} if final else None
        jobs.append({"case_id": row["case_id"], "method": row["method"], "track": row["track"],
                     "repeat_index": row["repeat_index"], "status": row["status"], "prediction": prediction})
    aggregate(jobs, case_refs, pairs)  # deliberately discard all synthetic counts
    return {"version": "s4_metrics_pipeline_offline_v1", "execution_kind": "offline_mock",
            "replay": replay, "planned_jobs": len(jobs), "pair_count": len(pairs),
            "reference_sha256": digest(ref_raw), "scoring": "not_scored_scripted_mock", "accuracy": None,
            "actual_model_calls": 0}
