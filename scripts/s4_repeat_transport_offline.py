"""Persist and verify transport simulations for an already completed scripted run.

This is a post-hoc offline check, not transport used by the original runner.
It cannot authenticate a real run or enforce a shared budget.
"""
import argparse
import gzip
import json
from pathlib import Path

from scripts.s4_metrics_pipeline_offline import check as check_metrics
from scripts.s4_repeat_offline import verify as verify_repeat
from scripts.s4_simulated_artifact_bundle import _bytes, _digest, _load, _write
from scripts.s4_simulated_artifact_bundle import create as create_attempt
from scripts.s4_simulated_artifact_bundle import verify as verify_attempt
from writscope.artifacts import canonical_hash


def attempts(run):
    for repeat, subrun in enumerate(run["rounds"], 1):
        for job in subrun["jobs"]:
            for index, attempt in enumerate(job["attempts"], 1):
                yield {"case_id": job["case_id"], "method": job["method"], "track": "A",
                       "repeat_index": repeat, "attempt_index": index}, attempt


def synthetic_response(attempt):
    # A scripted failure has no HTTP response; 503 is a labeled transport
    # surrogate, not the historical cause or an actual provider response.
    if attempt["response"] is None:
        return 503, {"offline_surrogate": "scripted_error"}
    return 200, {"choices": [{"message": {"content": attempt["response"]},
                                "finish_reason": "stop"}], "usage": None}


async def create(directory, bundle_root, scripts_path, run):
    verify_repeat(bundle_root, scripts_path, run)
    directory = Path(directory)
    target = directory.resolve()
    if target.is_relative_to(Path(bundle_root).resolve()) or target == Path(scripts_path).resolve():
        raise ValueError("output overlaps method inputs")
    directory.mkdir(parents=False, exist_ok=False)
    _write(directory / "run.json", _bytes(run))
    entries = []
    for number, (identity, attempt) in enumerate(attempts(run), 1):
        name = f"attempt-{number:06d}"
        status, response = synthetic_response(attempt)
        await create_attempt(directory / name, attempt["request"], response,
                             job_id=canonical_hash(identity), http_status=status)
        entries.append({"identity": identity, "directory": name,
                        "manifest_sha256": _digest(directory / name / "manifest.json")})
    if len(entries) != run["attempts_scripted"]:
        raise ValueError("scripted attempt count mismatch")
    manifest = {"version": "s4_repeat_transport_offline_v1", "execution_kind": "offline_simulation",
                "run_sha256": _digest(directory / "run.json"), "attempts": entries,
                "planned_jobs": len(run["rows"]), "actual_model_calls": 0}
    _write(directory / "manifest.json", _bytes(manifest))
    return {"planned_jobs": len(run["rows"]), "simulated_attempts": len(entries), "actual_model_calls": 0}


async def verify(directory, bundle_root, scripts_path):
    directory = Path(directory)
    for name in ("manifest.json", "run.json"):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("incomplete repeat artifact")
    manifest = _load(directory / "manifest.json")
    if (manifest.get("version") != "s4_repeat_transport_offline_v1" or
            manifest.get("execution_kind") != "offline_simulation" or manifest.get("actual_model_calls") != 0 or
            manifest.get("run_sha256") != _digest(directory / "run.json")):
        raise ValueError("repeat artifact binding mismatch")
    run = _load(directory / "run.json")
    verify_repeat(bundle_root, scripts_path, run)
    expected = list(attempts(run))
    entries = manifest.get("attempts")
    if (not isinstance(entries, list) or len(entries) != len(expected) or
            len(expected) != run["attempts_scripted"] or manifest.get("planned_jobs") != len(run["rows"])):
        raise ValueError("repeat artifact denominator mismatch")
    outcomes = {"completed": 0, "failed": 0}
    for number, ((identity, attempt), entry) in enumerate(zip(expected, entries), 1):
        name = f"attempt-{number:06d}"
        path = directory / name
        if (path.is_symlink() or not path.is_dir() or entry.get("directory") != name or
                entry.get("identity") != identity or
                (path / "manifest.json").is_symlink() or not (path / "manifest.json").is_file() or
                _digest(path / "manifest.json") != entry.get("manifest_sha256")):
            raise ValueError("repeat attempt binding mismatch")
        outcome = await verify_attempt(path)
        status, response = synthetic_response(attempt)
        plan = _load(path / "plan.json")
        if (_bytes(_load(path / "request.json")) != _bytes(attempt["request"]) or
                _load(path / "response.json") != response or plan.get("http_status") != status or
                plan.get("job_id") != canonical_hash(identity)):
            raise ValueError("repeat attempt input mismatch")
        outcomes[outcome["outcome"]] += 1
    return {"status": "offline_repeat_transport_verified", "planned_jobs": len(run["rows"]),
            "simulated_attempts": len(entries), "transport_outcomes": outcomes,
            "actual_model_calls": 0, "provider_billed_cost": None}


async def check_after_transport(directory, bundle_root, scripts_path, reference_path, disposition_path):
    """Do not read private references until all mock transport artifacts verify."""
    proof = await verify(directory, bundle_root, scripts_path)
    report = check_metrics(bundle_root, scripts_path, _load(Path(directory) / "run.json"),
                           reference_path, disposition_path)
    return {"transport": proof, "evaluation_wiring": report, "accuracy": None}


def run_in_memory(coroutine):
    """Drive only a coroutine that completes without yielding or doing I/O waits."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    coroutine.close()
    raise RuntimeError("offline mock unexpectedly yielded; no event loop permitted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "verify", "check"))
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--scripts", required=True, type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--disposition", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report is not None and (args.report.resolve().is_relative_to(args.bundle_root.resolve()) or
                                    args.report.resolve().is_relative_to(args.directory.resolve())):
        parser.error("report must be outside public bundles and transport artifacts")
    if args.command == "create":
        if args.run is None:
            parser.error("create requires --run")
        opener = gzip.open if args.run.suffix == ".gz" else open
        with opener(args.run, "rt", encoding="utf-8") as stream:
            saved = json.load(stream)
        result = run_in_memory(create(args.directory, args.bundle_root, args.scripts, saved))
    elif args.command == "verify":
        result = run_in_memory(verify(args.directory, args.bundle_root, args.scripts))
    else:
        if args.references is None or args.disposition is None:
            parser.error("check requires --references and --disposition")
        result = run_in_memory(check_after_transport(args.directory, args.bundle_root, args.scripts,
                                                    args.references, args.disposition))
    if args.report is not None:
        _write(args.report, _bytes(result))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
