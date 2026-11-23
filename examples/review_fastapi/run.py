"""Run four local review phases against the three owned FastAPI variants."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import writscope

from .export import export, json_bytes, new_output_path
from .service import ROUTE


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _review_source_hashes() -> dict[str, str]:
    package = Path(writscope.__file__).resolve().parent
    return {name: _digest(package / name)
            for name in ("__main__.py", "cli.py", "review.py", "review_openapi.py", "review_html.py")}


def _write_events(path: Path, events: list[dict]) -> None:
    data = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in events)
    with path.open("xb") as stream:
        stream.write(data.encode("utf-8"))


def _run_review(before: Path, after: Path, ledger: Path | None, output: Path) -> dict:
    command = [sys.executable, "-B", "-m", "writscope", "review",
               "--before", str(before), "--after", str(after),
               "--method", "GET", "--path", ROUTE, "--output", str(output)]
    if ledger is not None:
        command.extend(["--ledger", str(ledger)])
    environment = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH")
                   if name in os.environ}
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                               env=environment, timeout=60)
    # All inputs here are owned example artifacts, not arbitrary project code.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".stdout.txt").write_text(completed.stdout, encoding="utf-8")
    output.with_suffix(".stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise ValueError(f"review failed for {output.name}; see saved stderr and stdout")
    report_path = output / "review.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not (output / "index.html").is_file():
        raise ValueError("review did not create its HTML report")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if any(_digest(output / name) != expected for name, expected in manifest["files"].items()):
        raise ValueError("review report manifest does not match its artifacts")
    return {"command": command, "exit_code": completed.returncode,
            "review_sha256": _digest(report_path),
            "report_files_sha256": {name: _digest(output / name)
                                    for name in ("review.json", "index.html", "manifest.json")},
            "report_manifest_verified": True,
            "result": {key: report[key] for key in ("impact", "authorization", "decision")}}


def run(destination: Path, *, repeat: bool = False) -> dict:
    destination = new_output_path(destination)
    destination.mkdir(parents=True)
    review_sources = _review_source_hashes()
    with (destination / "review-source-binding.json").open("xb") as stream:
        stream.write(json_bytes({"package": "writscope", "source_files": review_sources}))
    evidence_dir = destination / "evidence"
    export(evidence_dir)
    before = evidence_dir / "before.openapi.json"
    breaking = evidence_dir / "breaking.openapi.json"
    fixed = evidence_dir / "fixed.openapi.json"

    grant = {
        "event_id": "approval-1", "operation": "grant",
        "source": "Local owned-example maintainer explicitly permits this exact status-field removal.",
        "method": "GET", "path": ROUTE, "field": "status",
        "change_type": "remove_required_response_field",
        "before_sha256": _digest(before), "after_sha256": _digest(breaking),
    }
    revoke = {
        "event_id": "revocation-1", "operation": "revoke",
        "source": "Local owned-example maintainer revokes approval-1; retain the original status contract.",
        "target_event_id": "approval-1",
    }
    approval_path = destination / "maintainer-approved.jsonl"
    revoked_path = destination / "maintainer-revoked.jsonl"
    _write_events(approval_path, [grant])
    _write_events(revoked_path, [grant, revoke])

    phases = (
        ("01-unapproved", breaking, None, ("breaking", "not_covered", "revise")),
        ("02-approved", breaking, approval_path, ("breaking", "covered", "allow")),
        ("03-revoked", breaking, revoked_path, ("breaking", "not_covered", "revise")),
        ("04-fixed", fixed, revoked_path, ("compatible", "not_needed", "allow")),
    )
    records = []
    for phase, after, ledger, expected in phases:
        item = _run_review(before, after, ledger, destination / phase)
        if tuple(item["result"][key] for key in ("impact", "authorization", "decision")) != expected:
            raise ValueError(f"unexpected review result for {phase}")
        item["phase"] = phase
        if repeat:
            replay = _run_review(before, after, ledger, destination / (phase + "-repeat"))
            if item["report_files_sha256"] != replay["report_files_sha256"]:
                raise ValueError(f"report bytes differ on repeat for {phase}")
            item["repeat"] = {"report_files_sha256": replay["report_files_sha256"],
                              "byte_identical": True, "report_manifest_verified": True}
        if _review_source_hashes() != review_sources:
            raise ValueError("review source changed during the run; keep these artifacts and use a new directory")
        records.append(item)

    export_manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    if any(_digest(evidence_dir / name) != expected
           for name, expected in export_manifest["files"].items()):
        raise ValueError("review changed the exported evidence")
    if any(_digest(Path(__file__).with_name(name)) != expected
           for name, expected in export_manifest["source_files"].items()):
        raise ValueError("owned application sources changed during the run")
    if _review_source_hashes() != review_sources:
        raise ValueError("review source changed during the run; keep these artifacts and use a new directory")
    artifacts = {path.relative_to(destination).as_posix(): _digest(path)
                 for path in sorted(destination.rglob("*")) if path.is_file()}
    record = {
        "schema_version": "writscope_owned_fastapi_four_phase_run_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_kind": "owned_example_execution_and_local_contract_review",
        "production_data": False,
        "model_calls": 0,
        "external_requests": 0,
        "authorization_input": "Hand-authored local example maintainer ledger; not external identity verification.",
        "source_files": {name: _digest(Path(__file__).with_name(name))
                         for name in ("service.py", "export.py", "run.py")},
        "review_source_files": review_sources,
        "evidence_preserved": True,
        "repeat_each_phase": repeat,
        "phases": records,
        "files": artifacts,
        "limitations": [
            "Owned illustrative code, not production data or a user study.",
            "Runtime checks cover one fixed response per variant.",
            "The review operates on declared OpenAPI contracts, not arbitrary code execution.",
            "The maintainer ledger is explicitly provided for this local demonstration.",
        ],
    }
    with (destination / "run.json").open("xb") as stream:
        stream.write(json_bytes(record))
    return {"output": str(destination), "phases": [item["result"] for item in records],
            "repeat_each_phase": repeat, "evidence_preserved": True, "model_calls": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / ".work" / ("run-" + uuid4().hex[:12]))
    parser.add_argument("--repeat", action="store_true", help="repeat each review and compare report bytes")
    args = parser.parse_args(argv)
    try:
        result = run(args.output, repeat=args.repeat)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Four-phase run failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
