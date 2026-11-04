"""Replay and summarize candidate scripted runs without scoring model quality."""

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from scripts.s4_candidate_runner import verify
from scripts.s4_p4 import load_gzip
from writscope.artifacts import digest
from writscope.contracts import parse_json

FIELDS = ("input_tokens", "output_tokens", "cost_usd")


def summarize(run):
    if run.get("execution_kind") != "offline_mock" or run.get("ledger", {}).get("actual_model_calls") != 0:
        raise ValueError("only zero-call scripted runs may be summarized")
    config, jobs, ledger = run["config"], run["jobs"], run["ledger"]
    expected = {(case, method) for case in config["cases"] for method in config["methods"]}
    keys = [(j["case_id"], j["method"]) for j in jobs]
    if (run["phase"] != "completed" or not expected or len(keys) != len(expected) or set(keys) != expected
            or len(config["cases"]) != len(set(config["cases"]))
            or len(config["methods"]) != len(set(config["methods"]))):
        raise ValueError("planned denominator mismatch")
    for job in jobs:
        if job["status"] not in {"completed", "failed", "not_run"}:
            raise ValueError("invalid job status")
        if (job["status"] == "completed") != (job["final"] is not None):
            raise ValueError("missing or invented final output")
        if job["status"] == "not_run" and job["attempts"]:
            raise ValueError("not_run job has attempts")
    attempts = [a for job in jobs for a in job["attempts"]]
    if (len(attempts) != ledger["attempts_scripted"] or len(attempts) > config["max_attempts"]
            or sum(len(j["lookups"]) for j in jobs) != ledger["lookups"]):
        raise ValueError("attempt or lookup ledger mismatch")
    usage_fields = {}
    for field in FIELDS:
        values = [(a.get("synthetic_usage") or {}).get(field) for a in attempts]
        known = [v for v in values if v is not None]
        subtotal = sum((Decimal(str(v)) for v in known), Decimal(0))
        usage_fields[field] = {"known_sum": str(subtotal) if known else None,
                               "known_count": len(known), "unknown_count": len(values) - len(known),
                               "total": str(subtotal) if values and len(known) == len(values) else None}
    by_method = {}
    for method in config["methods"]:
        selected = [j for j in jobs if j["method"] == method]
        counts = Counter(j["status"] for j in selected)
        by_method[method] = {"planned": len(selected), "completed": counts["completed"],
                             "failed": counts["failed"], "not_run": counts["not_run"],
                             "attempts": sum(len(j["attempts"]) for j in selected),
                             "lookups": sum(len(j["lookups"]) for j in selected)}
    return {"version": "s4_candidate_script_report_v1", "execution_kind": "offline_mock",
            "planned_jobs": len(expected), "methods": by_method,
            "rows": [{"case_id": j["case_id"], "method": j["method"], "status": j["status"],
                      "failure": j["failure"], "attempts": len(j["attempts"]),
                      "lookups": len(j["lookups"]), "has_final": j["final"] is not None} for j in jobs],
            "synthetic_usage": {"scope": "synthetic_only", "fields": usage_fields},
            "actual_provider_tokens": None, "actual_provider_billing": None,
            "accuracy": None, "scoring": "not_scored_scripted_mock", "actual_model_calls": 0}


def report(bundle_root, scripts, run):
    summary = summarize(run)
    summary["replay"] = verify(bundle_root, scripts, run)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--scripts", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root, scripts, run_path, output = (p.resolve() for p in (args.bundle_root, args.scripts, args.run, args.output))
    if output.is_relative_to(root) or output in (scripts, run_path):
        raise ValueError("report output overlaps input")
    run = load_gzip(run_path) if run_path.suffix == ".gz" else parse_json(run_path.read_text(encoding="utf-8"))
    result = report(root, scripts, run)
    result["saved_run_sha256"] = digest(run_path.read_bytes())
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"planned": result["planned_jobs"], "replay": result["replay"]["status"],
                      "scoring": result["scoring"]}))


if __name__ == "__main__":
    main()
