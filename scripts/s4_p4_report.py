"""Evaluator-only 90-row report; never publish mock accuracy."""

import argparse
import asyncio
import json
from pathlib import Path

from scripts.s4_p2_report import compare_axes
from scripts.s4_p3_audit import audit
from scripts.s4_p4 import CASES, load_gzip, verify_round
from writscope.artifacts import digest, write_json
from writscope.contracts import parse_json


async def report(bundle_root, scripts, run_path, references, p0):
    replay = await verify_round(bundle_root, scripts, run_path)
    # Evaluator material is accessed only after all method trajectories complete.
    audit_result = audit(bundle_root, references, p0)
    refs = parse_json(references.read_text(encoding="utf-8"))["cases"]
    run = load_gzip(run_path)
    for row in run["jobs"]:
        compare_axes(refs[row["case_id"]]["expected"], row["final"])
    return {"version": "s4_p4_evaluator_report_v1", "execution_kind": "offline_mock",
            "planned_jobs": 90, "planned_cases": len(CASES), "scoring": "not_scored_scripted_mock",
            "comparison_path": "validated_not_reported", "accuracy": None,
            "reference_status": "author_draft_not_independently_reviewed",
            "reference_sha256": digest(references.read_bytes()), "semantic_review_pending": True,
            "data_audit": {"cases": audit_result["case_count"], "pairs": audit_result["pair_count"],
                           "status": audit_result["status"]}, "replay": replay,
            "rows": [{"case_id": j["case_id"], "method": j["method"], "status": j["status"],
                      "failure": j["failure"], "attempts": len(j["attempts"]),
                      "has_final": j["final"] is not None} for j in run["jobs"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--scripts", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--references", required=True, type=Path)
    parser.add_argument("--p0", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("report output already exists")
    data = asyncio.run(report(args.bundle_root, args.scripts, args.run, args.references, args.p0))
    write_json(args.output, data)
    print(json.dumps({"planned": data["planned_jobs"], "scoring": data["scoring"], "replay": data["replay"]["replay"]["status"]}))


if __name__ == "__main__":
    main()
