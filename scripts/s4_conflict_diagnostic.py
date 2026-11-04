"""Replay and inspect scripted S4 terminal/state conflicts; never score models."""

import argparse
import asyncio
import json
from pathlib import Path

from scripts.s4_p2 import checked_bundle, verify
from writscope.artifacts import digest
from writscope.contracts import parse_json, validate_output

AXES = ("impact", "authorization", "decision")


async def diagnose(bundle_root, scripts_path, run):
    # Refuse real execution records before invoking any replay machinery.
    if run.get("execution_kind") != "offline_mock" or run.get("ledger", {}).get("actual_model_calls") != 0:
        raise ValueError("diagnostic accepts only zero-call scripted runs")
    replay = await verify(bundle_root, scripts_path, run)
    rows = []
    for job in run["jobs"]:
        result = {"case_id": job["case_id"], "method": job["method"],
                  "original_status": job["status"], "classification": "not_incremental",
                  "different_axes": [], "response_sha256": None}
        if job["method"] != "incremental":
            rows.append(result)
            continue
        result["classification"] = "no_terminal_response"
        attempts = job["attempts"]
        attempt = attempts[-1] if attempts else None
        if not attempt or attempt["stage"] not in ("final_review", "continue_final_review") or attempt["response"] is None:
            rows.append(result)
            continue
        raw = attempt["response"]
        result["response_sha256"] = digest(raw.encode("utf-8"))
        try:
            response = parse_json(raw)
            if not isinstance(response, dict) or response.get("kind") != "final":
                raise ValueError("response is not a terminal payload")
            bundle = checked_bundle(bundle_root / job["case_id"])
            terminal = validate_output(json.dumps(response.get("payload"), ensure_ascii=False), bundle,
                                       "full_direct", output_contract="source_quote_v1")
        except (ValueError, TypeError, KeyError):
            result["classification"] = "invalid_terminal_response"
            rows.append(result)
            continue
        assessment = job["assessment"]
        if not isinstance(assessment, dict) or any(axis not in assessment for axis in AXES):
            raise ValueError("validated terminal missing structural assessment")
        differences = [axis for axis in AXES if getattr(terminal, axis) != assessment[axis]]
        result["different_axes"] = differences
        result["classification"] = "state_conflict" if differences else "state_consistent"
        rows.append(result)
    return {"version": "s4_mock_conflict_diagnostic_v1", "execution_kind": "offline_mock",
            "replay": replay, "planned_jobs": len(run["jobs"]), "rows": rows,
            "model_scoring": "not_performed", "accuracy": None, "actual_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--scripts", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle_root, scripts, run_path, output = (p.resolve() for p in
                                               (args.bundle_root, args.scripts, args.run, args.output))
    if output.is_relative_to(bundle_root) or output in (scripts, run_path):
        raise ValueError("diagnostic output overlaps input")
    run = parse_json(run_path.read_text(encoding="utf-8"))
    result = asyncio.run(diagnose(bundle_root, scripts, run))
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"diagnosed {result['planned_jobs']} scripted jobs; no model scoring")


if __name__ == "__main__":
    main()
