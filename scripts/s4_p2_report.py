"""Evaluator-side report after a saved offline round; mock trajectories are never scored."""

import argparse
import asyncio
import json
from pathlib import Path

from scripts.s4_p2 import checked_bundle, verify
from writscope.artifacts import digest, write_json
from writscope.contracts import parse_json
from writscope.events import JudgmentLabels


def reference_integrity(path, bundle_root, cases):
    raw = path.read_bytes()
    data = parse_json(raw.decode("utf-8"))
    if data.get("version") != "s4_p1_author_draft_v1":
        raise ValueError("unexpected reference version")
    for case in cases:
        ref = data["cases"][case]
        JudgmentLabels.model_validate(ref["expected"])
        event = checked_bundle(bundle_root / case).review_input.events[-1]
        if (ref["review"] != "author_draft_not_independently_reviewed" or event.role != "user"
                or ref["source_event"] != event.event_id or not ref["source_quote"]
                or ref["source_quote"] not in event.content):
            raise ValueError("reference source or review status invalid")
    return digest(raw)


def compare_axes(reference, prediction):
    """Evaluator primitive. A missing/invalid output remains missing, not revise."""
    expected = JudgmentLabels.model_validate(reference)
    if prediction is None:
        return {"status": "no_valid_output", "axes": None, "all_axes": None}
    actual = JudgmentLabels.model_validate({axis: prediction[axis] for axis in ("impact", "authorization", "decision")})
    axes = {axis: getattr(expected, axis) == getattr(actual, axis)
            for axis in ("impact", "authorization", "decision")}
    return {"status": "valid_output", "axes": axes, "all_axes": all(axes.values())}


def score_real_only(run, references):
    """Never score the scripted P2 round. Real-run verification is not built here."""
    if run.get("execution_kind") != "real_model":
        raise ValueError("offline scripted trajectories are not model results and cannot be scored")
    raise NotImplementedError("real-run scoring requires a separately verified frozen S4 run")


async def report(bundle_root, scripts_path, run, references):
    replay = await verify(bundle_root, scripts_path, run)
    reference_sha = reference_integrity(references, bundle_root, run["config"]["cases"])
    # Exercise the independent comparison path only after all methods finish;
    # deliberately discard all synthetic comparison outcomes and accuracy.
    reference_data = parse_json(references.read_text(encoding="utf-8"))["cases"]
    for job in run["jobs"]:
        compare_axes(reference_data[job["case_id"]]["expected"], job["final"])
    return {"version": "s4_p2_evaluator_report_v1", "execution_kind": "offline_mock",
            "replay": replay, "references_sha256": reference_sha,
            "reference_status": "author_draft_not_independently_reviewed",
            "scoring": "not_scored_scripted_mock", "comparison_path": "validated_not_reported", "accuracy": None,
            "planned_jobs": len(run["jobs"]),
            "rows": [{"case_id": j["case_id"], "method": j["method"], "status": j["status"],
                      "failure": j["failure"], "attempts": len(j["attempts"]),
                      "has_final": j["final"] is not None} for j in run["jobs"]],
            "ledger": run["ledger"], "actual_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--scripts", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--references", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("report output already exists")
    data = asyncio.run(report(args.bundle_root, args.scripts,
                              parse_json(args.run.read_text(encoding="utf-8")), args.references))
    write_json(args.output, data)
    print(json.dumps({"status": "saved", "planned_jobs": data["planned_jobs"], "scoring": data["scoring"]}))


if __name__ == "__main__":
    main()
