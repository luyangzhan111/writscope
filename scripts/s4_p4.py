"""P4 offline round: 90 scripted trajectories, bounded journals and replay."""

import argparse
import asyncio
import gzip
import json
from decimal import Decimal
from pathlib import Path

from scripts.s4_p2 import METHODS, ROOT, execute, normalise, verify
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import parse_json

CASES = [f"s4_{n:03d}" for n in range(1, 31)]


def usage_summary(run):
    attempts = [a for job in run["jobs"] for a in job["attempts"]]
    budget = run["ledger"]["budget"]
    if run["ledger"]["attempts_scripted"] != len(attempts) or budget["reserved_units"] != len(attempts):
        raise ValueError("attempt/reservation ledger mismatch")
    fields = ("input_tokens", "output_tokens", "cost_usd")
    result = {}
    for field in fields:
        values = []
        for attempt in attempts:
            usage = attempt.get("synthetic_usage")
            if usage is None and attempt.get("error"):
                usage = attempt["error"].get("synthetic_usage")
            values.append(usage.get(field) if usage else None)
        known = [v for v in values if v is not None]
        total = sum((Decimal(str(v)) for v in known), Decimal(0))
        result[field] = {"known_sum": str(total) if known else None, "known_count": len(known),
                         "unknown_count": len(values) - len(known),
                         "total": str(total) if len(known) == len(values) and values else None}
    return {"scope": "synthetic_only", "attempts": len(attempts), "fields": result,
            "actual_provider_tokens": None, "actual_provider_billing": None}


def validate_round(run):
    expected = {(c, m) for c in CASES for m in METHODS}
    rows = run["jobs"]
    keys = [(j["case_id"], j["method"]) for j in rows]
    if run["phase"] != "completed" or len(keys) != 90 or set(keys) != expected:
        raise ValueError("planned 90-job denominator mismatch")
    if run["execution_kind"] != "offline_mock" or run["ledger"]["actual_model_calls"] != 0:
        raise ValueError("P4 only accepts offline mock runs")
    for job in rows:
        if job["status"] not in {"completed", "failed", "not_run"}:
            raise ValueError("unknown job status")
        if (job["status"] == "completed") != (job["final"] is not None):
            raise ValueError("missing or invented final output")
        if job["status"] == "not_run" and job["attempts"]:
            raise ValueError("not_run job has provider attempts")
    for case in CASES:
        evidence_hashes = {a["request"]["evidence_sha256"] for j in rows if j["case_id"] == case for a in j["attempts"]}
        if len(evidence_hashes) > 1:
            raise ValueError("methods have different base evidence")
        for job in (j for j in rows if j["case_id"] == case):
            for attempt in job["attempts"]:
                data = attempt["request"]["public_input"]
                if "expected" in data or "references" in data or "pair_id" in data:
                    raise ValueError("private metadata in method input")
                if data["review_input"]["tool_policy"]["available_tools"] != ["history_lookup"]:
                    raise ValueError("inconsistent method tool policy")
                if any(e["seq"] > data["history_lookup_policy"]["through_seq"] for e in data["review_input"]["events"]):
                    raise ValueError("future event exposed")
    return {"planned": 90, "completed": sum(j["status"] == "completed" for j in rows),
            "failed": sum(j["status"] == "failed" for j in rows),
            "not_run": sum(j["status"] == "not_run" for j in rows),
            "usage": usage_summary(run), "actual_model_calls": 0}


def save_gzip(path, obj):
    raw = (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.write_bytes(gzip.compress(raw, mtime=0))


def load_gzip(path):
    return parse_json(gzip.decompress(path.read_bytes()).decode("utf-8"))


async def run_round(bundle_root, scripts, output, max_attempts):
    output = output.absolute()
    bundle_root, scripts = bundle_root.resolve(), scripts.resolve()
    if ".." in output.parts or any(p.is_symlink() or p.is_junction() for p in (output, *output.parents)):
        raise ValueError("unsafe output path")
    if output.is_relative_to(bundle_root) or bundle_root.is_relative_to(output) or scripts.is_relative_to(output):
        raise ValueError("output overlaps input")
    output.mkdir(parents=False, exist_ok=False)
    journal = output / "journal"
    journal.mkdir()
    saved = {}
    def checkpoint(result):
        # Save only the currently changing job plus the ledger, not the entire
        # growing 90-job run after every provider attempt.
        if result["jobs"]:
            job = result["jobs"][-1]
            key = job["case_id"] + "-" + job["method"]
            h = canonical_hash(normalise(job))
            if saved.get(key) != h:
                tmp = journal / (key + ".tmp")
                write_json(tmp, job)
                tmp.replace(journal / (key + ".json"))
                saved[key] = h
        tmp = output / "progress.tmp"
        write_json(tmp, {"phase": result["phase"], "planned": 90, "jobs_started": len(result["jobs"]),
                         "ledger": result["ledger"]})
        tmp.replace(output / "progress.json")
    try:
        result = await execute(bundle_root, scripts, CASES, list(METHODS), max_attempts, checkpoint)
        summary = validate_round(result)
        save_gzip(output / "run.json.gz", result)
        write_json(output / "summary.json", summary)
        write_json(output / "manifest.json", {"version": "s4_p4_run_binding_v1",
                   "wrapper_sha256": digest((ROOT / "scripts/s4_p4.py").read_bytes()),
                   "script_generator_sha256": digest((ROOT / "scripts/s4_p4_scripts.py").read_bytes()),
                   "dataset_manifest_sha256": digest((ROOT / "fixtures/s4_p3/manifest.json").read_bytes()),
                   "scripts_sha256": digest(scripts.read_bytes()),
                   "compressed_run_sha256": digest((output / "run.json.gz").read_bytes())})
        return summary
    except Exception as exc:
        write_json(output / "interruption.json", {"phase": "interrupted", "kind": type(exc).__name__,
                                                   "detail": str(exc), "actual_model_calls": 0})
        raise


async def verify_round(bundle_root, scripts, run_path):
    binding = parse_json((run_path.parent / "manifest.json").read_text(encoding="utf-8"))
    expected = {"version": "s4_p4_run_binding_v1",
                "wrapper_sha256": digest((ROOT / "scripts/s4_p4.py").read_bytes()),
                "script_generator_sha256": digest((ROOT / "scripts/s4_p4_scripts.py").read_bytes()),
                "dataset_manifest_sha256": digest((ROOT / "fixtures/s4_p3/manifest.json").read_bytes()),
                "scripts_sha256": digest(scripts.read_bytes()),
                "compressed_run_sha256": digest(run_path.read_bytes())}
    if binding != expected:
        raise ValueError("P4 saved run binding mismatch")
    run = load_gzip(run_path)
    result = validate_round(run)
    replay = await verify(bundle_root, scripts, run)
    return {"replay": replay, "summary": result, "compressed_sha256": digest(run_path.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "verify"])
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--scripts", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--max-attempts", type=int, default=260)
    args = parser.parse_args()
    if args.command == "run" and args.output:
        result = asyncio.run(run_round(args.bundle_root, args.scripts, args.output, args.max_attempts))
    elif args.command == "verify" and args.run:
        result = asyncio.run(verify_round(args.bundle_root, args.scripts, args.run))
    else:
        parser.error("run needs --output; verify needs --run")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
