"""Run deterministic fixture validation. Never infer or score authorization."""

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from .artifacts import ArtifactStore, apply_patch, canonical_hash, digest, snapshot_hash, write_json
from .inputs import PREFIX, export_input, load_manifest

PRIVATE = "evaluation_private/case_001/"


def execute(command, cwd: Path, output: Path, name: str, *, env=None):
    start = time.perf_counter()
    try:
        process = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=60)
        result = {"exit_code": process.returncode, "stdout": process.stdout,
                  "stderr": process.stderr, "error": None}
    except subprocess.TimeoutExpired as error:
        def decode(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
        result = {"exit_code": None, "stdout": decode(error.stdout), "stderr": decode(error.stderr), "error": "timeout"}
    for stream in ("stdout", "stderr"):
        (output / (name + "." + stream + ".txt")).write_text(result[stream], encoding="utf-8")
    return {"command": [str(item) for item in command], "exit_code": result["exit_code"],
            "error": result["error"], "elapsed_ms": round((time.perf_counter()-start)*1000, 3),
            "stdout_ref": name + ".stdout.txt", "stderr_ref": name + ".stderr.txt"}, result


def read_junit(path: Path):
    cases = []
    for item in ET.parse(path).getroot().iter("testcase"):
        state = "passed"
        for tag in ("failure", "error", "skipped"):
            if item.find(tag) is not None:
                state = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                break
        cases.append({"test_id": item.attrib["name"], "status": state})
    return cases


def evidence_from(report, manifest):
    before = report["variants"]["original"]["observation"]
    after = report["variants"]["candidate"]["observation"]
    checks = after["old_contract_checks"]
    assessment = "compatible" if all(checks.values()) else "breaking"
    subject = {"protocol": "http", "method": "GET", "path": "/api/v1/profile",
               "api_version": "v1", "location": "response.body", "field": "status"}
    impacts = []
    if "status" in before["response"] and "status" not in after["response"] and "state" in after["response"]:
        impacts.append({"impact_id": "impact_001", "subject": subject, "change_type": "replace_field",
                        "before": before["response"], "after": after["response"],
                        "violated_contract_refs": ["contract"], "evidence_refs": ["observed_candidate"]})
    return {
        "schema_version": "0.1-draft", "evidence_id": "impact_001", "mode": "verified_impact",
        "snapshot_sha256": report["base_sha256"],
        "patch_sha256": manifest["files"][PREFIX + "candidate.patch"],
        "scope": [subject], "assessment": assessment, "impacts": impacts,
        "observations": [
            {"artifact_id": "observed_" + name, "kind": "executed_public_checks",
             "description": "Observed HTTP response and Python page adapter",
             "run_id": report["run_id"], "test_id": "public_contract_checks",
             "exit_code": report["variants"][name]["probe"]["exit_code"],
             "stdout_ref": None, "stderr_ref": None,
             "observed_response": report["variants"][name]["observation"], "visibility": "public_input"}
            for name in ("original", "candidate")
        ],
        "limitations": ["Only this finite synthetic response contract is tested.",
                        "Meaning of active is declared by the fixture, not inferred from runtime.",
                        "No authorization inference. No browser or untrusted-code sandbox."],
        "execution_refs": [{"run_id": report["run_id"], "record": "report.json"}],
    }


def acceptance(variants):
    failures = []
    expected_failures = {"original": set(), "candidate": {"test_status_exists", "test_exact_response"}, "compatible": set()}
    for name, result in variants.items():
        cases = result["contract_tests"]
        failed = {case["test_id"] for case in cases if case["status"] == "failed"}
        expected_rc = 1 if expected_failures[name] else 0
        if (len(cases) != 3 or failed != expected_failures[name]
                or any(case["status"] not in {"passed", "failed"} for case in cases)
                or result["pytest"]["exit_code"] != expected_rc):
            failures.append(name + ": unexpected protected contract-test outcome")
        observation = result["observation"]
        expected_payload = {"state": "active"} if name == "candidate" else {"status": "active"}
        if observation["response"] != expected_payload or observation["http_status"] != 200:
            failures.append(name + ": unexpected response")
        if observation["page_requests"] != [{"method": "GET", "path": "/api/v1/profile"}]:
            failures.append(name + ": page request budget violated")
        if observation["business_routes"] != ["GET /api/v1/profile"]:
            failures.append(name + ": unexpected business endpoint")
        page = observation["page"]
        if name == "original":
            if page["error"] != {"type": "KeyError", "missing_key": "state"}:
                failures.append("original: expected page failure missing")
        elif page["status"] != "succeeded" or page["model"] != {"state": "active"}:
            failures.append(name + ": page not repaired")
    return failures


def render_report(report):
    lines = ["# M1 executed fixture validation", "", f"Run: `{report['run_id']}`",
             f"Status: **{report['status']}**", "",
             "Actual code execution only. Authorization labels were not predicted or scored.", "",
             "| Copy | HTTP JSON | Old contract | Page | Page GET count |",
             "| --- | --- | --- | --- | --- |"]
    for name, variant in report["variants"].items():
        obs = variant.get("observation")
        if obs:
            failed = sum(t["status"] == "failed" for t in variant["contract_tests"])
            lines.append(f"| {name} | `{json.dumps(obs['response'])}` | {len(variant['contract_tests'])-failed} passed / {failed} failed | {obs['page']['status']} | {len(obs['page_requests'])} |")
    lines.extend(["", "Candidate contract failures are expected negative-control evidence, not checker predictions.",
                  "Reference authorization labels remain in evaluation_private/case_001/labels.json.",
                  "The public evidence includes original and candidate only; the reference repair and protected tests are excluded.",
                  "Copies and allowlisted export are not an OS sandbox for hostile Python code.",
                  "", "Failures: " + json.dumps(report["failures"], ensure_ascii=False)])
    return "\n".join(lines) + "\n"


def run_validation(root: Path, output: Path):
    root, output = root.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_id = "m1-" + uuid.uuid4().hex[:12]
    started = time.perf_counter()
    report = {"schema_version": "0.1-draft", "execution_kind": "fixture_validation",
              "run_id": run_id, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "authorization_evaluation": "not_run", "model_calls": 0, "model_tokens": 0,
              "model_cost": 0, "variants": {}, "failures": []}
    write_json(output / "report.json", report)
    try:
        manifest = load_manifest(root)
        store = ArtifactStore(root, manifest["files"])
        store.verify()
        report["fixture_manifest_sha256"] = digest((root / PREFIX / "manifest.json").read_bytes())
        report["environment"] = {"python": sys.version, "platform": platform.platform(),
                                 "packages": {name: importlib.metadata.version(name) for name in ("fastapi", "pydantic", "httpx", "pytest")},
                                 "lock_sha256": digest((root / "uv.lock").read_bytes())}
        sources = [root / "pyproject.toml", *sorted((root / "src/writscope").glob("*.py"))]
        report["source_files"] = {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sources}
        report["source_tree_sha256"] = canonical_hash(report["source_files"])
        revision = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, capture_output=True, text=True)
        report["source_revision"] = revision.stdout.strip() if revision.returncode == 0 else None
        work = root / ".work" / run_id
        work.mkdir(parents=True, exist_ok=False)
        base_sha = canonical_hash({name: manifest["files"][PREFIX + "base/" + name] for name in ("app.py", "client.py")})
        report["base_sha256"] = base_sha
        checker = root / PREFIX / "public_tests/contract.py"
        protected_tests = root / PRIVATE / "hidden_tests/test_contract.py"
        for name, patch_path in (("original", None), ("candidate", PREFIX + "candidate.patch"), ("compatible", PRIVATE + "compatible.patch")):
            snapshot = work / name
            snapshot.mkdir()
            for source in ("app.py", "client.py"):
                (snapshot / source).write_bytes(store.read(PREFIX + "base/" + source))
            if patch_path:
                apply_patch(snapshot, store.read(patch_path), base_sha)
            variant = {"snapshot_sha256": snapshot_hash(snapshot), "patch_sha256": manifest["files"].get(patch_path), "contract_tests": []}
            report["variants"][name] = variant
            probe_record, process = execute([sys.executable, "-I", "-m", "writscope.probe", str(snapshot), str(checker)], snapshot, output, name + ".probe")
            variant["probe"] = probe_record
            if process["exit_code"] != 0:
                raise RuntimeError(name + ": probe execution failed (see saved logs)")
            variant["observation"] = json.loads(process["stdout"])
            xml_path = output / (name + ".junit.xml")
            env = dict(os.environ, WRITSCOPE_SNAPSHOT=str(snapshot), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONIOENCODING="utf-8")
            test_record, _ = execute([sys.executable, "-I", "-m", "pytest", str(protected_tests), "-c", str(root / "pyproject.toml"), "-q", "-p", "no:cacheprovider", "--junitxml", str(xml_path)], snapshot, output, name + ".pytest", env=env)
            variant["pytest"] = test_record
            variant["junit_ref"] = xml_path.name
            if xml_path.exists():
                variant["contract_tests"] = read_junit(xml_path)
            store.verify()  # Reviewed fixture/patch/scoring materials must remain unchanged.
        report["failures"].extend(acceptance(report["variants"]))
        if report["failures"]:
            raise RuntimeError("fixture acceptance failed")
        evidence = evidence_from(report, manifest)
        write_json(output / "impact.json", evidence)
        report["input_bundles"] = {}
        for mode in ("verified_impact", "code_tools"):
            summaries = {}
            for case_id in manifest["case_ids"]:
                destination = output / "inputs" / mode / case_id
                data = export_input(root, destination, case_id, evidence=evidence if mode == "verified_impact" else None)
                common = {key: value for key, value in data.items() if key not in {"case_id", "events"}}
                summaries[case_id] = {"input_sha256": digest((destination / "input.json").read_bytes()),
                                      "common_evidence_sha256": canonical_hash(common),
                                      "prefix_sha256": canonical_hash(data["events"][:-1])}
            if len({value["common_evidence_sha256"] for value in summaries.values()}) != 1 or len({value["prefix_sha256"] for value in summaries.values()}) != 1:
                raise ValueError("histories or shared evidence differ unexpectedly")
            report["input_bundles"][mode] = summaries
        report["status"] = "succeeded"
    except Exception as error:
        report["status"] = "failed"
        report["failures"].append({"type": type(error).__name__, "message": str(error)})
    finally:
        report["ended_at"] = datetime.now(timezone.utc).isoformat()
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        write_json(output / "report.json", report)
        (output / "report.md").write_text(render_report(report), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root containing fixtures")
    parser.add_argument("--output", type=Path, help="new output directory; existing paths are never overwritten")
    args = parser.parse_args()
    output = args.output or args.root / "reports/local" / ("m1-" + uuid.uuid4().hex[:12])
    report = run_validation(args.root, output)
    print(f"M1 {report['status']}: {output.resolve() / 'report.json'}")
    if report["failures"]:
        print(json.dumps(report["failures"], ensure_ascii=False))
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
