"""Offline M3a state replay and structural-scope diagnostic CLI."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import time

from pydantic import ValidationError

from .artifacts import canonical_hash, digest, write_json
from .contracts import load_bundle, parse_json
from .state import (AuthorizationState, ScopeAssessment, StateEngine,
                    TransitionLogEntry, TransitionProposal, assess_impacts,
                    replay_entries)


def _lines(path: Path):
    raw = path.read_text(encoding="utf-8")
    if not raw.endswith("\n"):
        raise ValueError("JSONL must end with a newline")
    lines = raw.splitlines()
    if not lines or any(not line for line in lines):
        raise ValueError("JSONL must contain nonempty lines")
    return lines


def load_proposals(path: Path) -> list[TransitionProposal]:
    proposals = []
    for number, line in enumerate(_lines(path), 1):
        try:
            proposals.append(TransitionProposal.model_validate(parse_json(line)))
        except ValidationError as error:
            codes = [".".join(map(str, item["loc"])) + ":" + item["type"]
                     for item in error.errors(include_input=False)]
            raise ValueError(f"proposal line {number} schema rejected: {', '.join(codes)}") from None
        except ValueError as error:
            raise ValueError(f"proposal line {number} JSON rejected: {error}") from None
    return proposals


def _write_jsonl(path: Path, values):
    path.write_text("".join(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")) + "\n" for value in values),
                    encoding="utf-8", newline="\n")


def _code_provenance() -> dict:
    package = Path(__file__).resolve().parent
    sources = {path.name: digest(path.read_bytes()) for path in sorted(package.glob("*.py"))}
    sources.update({"providers/" + path.name: digest(path.read_bytes())
                    for path in sorted((package / "providers").glob("*.py"))})
    sources.update({"baselines/" + path.name: digest(path.read_bytes())
                    for path in sorted((package / "baselines").glob("*.py"))})
    lock = package.parents[1] / "uv.lock"
    return {"source_tree_sha256": canonical_hash(sources),
            "environment_lock_sha256": digest(lock.read_bytes()) if lock.exists() else None}


def run(bundle_path: Path, proposals_path: Path, output: Path) -> dict:
    """No provider or evaluator labels: proposals are manual diagnostic inputs."""
    bundle = load_bundle(bundle_path)
    if output.resolve().is_relative_to(bundle_path.resolve()):
        raise ValueError("M3a output cannot modify its public input bundle")
    provenance = _code_provenance()
    try:
        proposals = load_proposals(proposals_path)
    except ValueError as error:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "input_error.json", {"stage": "proposal_schema_or_json",
                                                  "reason": str(error),
                                                  "input_sha256": digest(proposals_path.read_bytes())})
        artifacts = {"input_error.json": digest((output / "input_error.json").read_bytes())}
        manifest = {"schema_version": "0.1-draft", "execution_kind": "manual_diagnostic",
                    "status": "invalid_input", "case_id": bundle.review_input.case_id,
                    "public_bundle_sha256": bundle.sha256, **provenance,
                    "proposal_count": 0, "consumed_events": 0,
                    "accepted_transitions": 0, "rejected_transitions": 0,
                    "input_errors": 1,
                    "state_hash": None,
                    "cost": {"actual_model_calls": 0, "extractor_tokens": None,
                             "extractor_cost_cny": None, "provider_billed_cny": None,
                             "extractor_cost_note": "invalid manual input; real extraction cost unknown",
                             "state_update_elapsed_ms": 0.0, "scope_lookup_elapsed_ms": None},
                    "artifacts": artifacts, "artifact_set_sha256": canonical_hash(artifacts)}
        write_json(output / "run.json", manifest)
        return manifest
    config_hashes = {item.extractor_config_hash for item in proposals}
    if len(config_hashes) != 1:
        raise ValueError("M3a needs one explicit extractor configuration")
    engine = StateEngine(bundle.review_input.case_id, bundle.review_input.events,
                         next(iter(config_hashes)), set(bundle.registry.artifacts))
    entries = [engine.submit(proposal) for proposal in proposals]
    state = engine.snapshot()
    complete = state.through_seq == bundle.review_input.decision_point.seq
    lookup_ms = None
    assessment = None
    if complete:
        started = time.perf_counter()
        assessment = assess_impacts(state, bundle.evidence, bundle.review_input.candidate_patch.sha256)
        lookup_ms = round((time.perf_counter() - started) * 1000, 3)
    output.mkdir(parents=True, exist_ok=False)
    _write_jsonl(output / "proposals.jsonl", [item.model_dump() for item in proposals])
    _write_jsonl(output / "transitions.jsonl", [entry.model_dump() for entry in entries])
    write_json(output / "state.json", state.model_dump())
    if assessment is not None:
        write_json(output / "assessment.json", assessment.model_dump())
    artifacts = {path.name: digest(path.read_bytes()) for path in sorted(output.iterdir())}
    manifest = {
        "schema_version": "0.1-draft", "execution_kind": "manual_diagnostic",
        "status": "completed" if complete else "incomplete",
        "case_id": bundle.review_input.case_id, "public_bundle_sha256": bundle.sha256,
        **provenance,
        "extractor_config_hash": engine.extractor_config_hash,
        "proposal_count": len(proposals), "consumed_events": state.through_seq,
        "accepted_transitions": sum(entry.transition.accepted for entry in entries),
        "rejected_transitions": sum(not entry.transition.accepted for entry in entries),
        "input_errors": 0,
        "state_hash": state.state_hash,
        "cost": {"actual_model_calls": 0, "extractor_tokens": None,
                 "extractor_cost_cny": None, "provider_billed_cny": None,
                 "extractor_cost_note": "manual proposals; real extraction cost is unknown, not zero",
                 "state_update_elapsed_ms": round(sum(entry.update_elapsed_ms for entry in entries), 3),
                 "scope_lookup_elapsed_ms": lookup_ms},
        "artifacts": artifacts, "artifact_set_sha256": canonical_hash(artifacts),
    }
    write_json(output / "run.json", manifest)
    return manifest


def verify_run(bundle_path: Path, output: Path) -> dict:
    bundle = load_bundle(bundle_path)
    manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    if manifest.get("execution_kind") != "manual_diagnostic" or manifest.get("public_bundle_sha256") != bundle.sha256:
        raise ValueError("run kind or public input hash mismatch")
    if manifest.get("case_id") != bundle.review_input.case_id or manifest.get("status") not in {"completed", "incomplete", "invalid_input"}:
        raise ValueError("M3a run identity or status mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or canonical_hash(artifacts) != manifest.get("artifact_set_sha256"):
        raise ValueError("M3a artifact set hash mismatch")
    expected_files = ({"input_error.json"} if manifest["status"] == "invalid_input" else
                      {"proposals.jsonl", "transitions.jsonl", "state.json"})
    if manifest["status"] == "completed":
        expected_files.add("assessment.json")
    if set(artifacts) != expected_files:
        raise ValueError("unexpected M3a artifact set")
    for name, expected in artifacts.items():
        if digest((output / name).read_bytes()) != expected:
            raise ValueError("M3a artifact hash mismatch: " + name)
    if manifest["status"] == "invalid_input":
        if (manifest["cost"]["actual_model_calls"] != 0 or manifest["state_hash"] is not None or
                manifest["input_errors"] != 1 or manifest["proposal_count"] != 0):
            raise ValueError("invalid-input run must not claim state or model calls")
        return manifest
    proposals = load_proposals(output / "proposals.jsonl")
    entries = [TransitionLogEntry.model_validate(parse_json(line))
               for line in _lines(output / "transitions.jsonl")]
    if proposals != [entry.proposal for entry in entries]:
        raise ValueError("proposal/transition log mismatch")
    state = replay_entries(bundle.review_input.case_id, bundle.review_input.events,
                           manifest["extractor_config_hash"], entries,
                           set(bundle.registry.artifacts))
    expected_complete = state.through_seq == bundle.review_input.decision_point.seq
    if (manifest["proposal_count"] != len(proposals) or
            manifest["input_errors"] != 0 or
            manifest["consumed_events"] != state.through_seq or
            manifest["accepted_transitions"] != sum(entry.transition.accepted for entry in entries) or
            manifest["rejected_transitions"] != sum(not entry.transition.accepted for entry in entries) or
            (manifest["status"] == "completed") != expected_complete or
            manifest["cost"]["actual_model_calls"] != 0 or
            manifest["cost"]["extractor_cost_cny"] is not None or
            manifest["cost"]["state_update_elapsed_ms"] != round(sum(entry.update_elapsed_ms for entry in entries), 3)):
        raise ValueError("M3a run totals mismatch")
    saved_state = AuthorizationState.model_validate(parse_json((output / "state.json").read_text(encoding="utf-8")))
    if state != saved_state or state.state_hash != manifest["state_hash"]:
        raise ValueError("M3a state replay mismatch")
    if manifest["status"] == "completed":
        assessment = assess_impacts(state, bundle.evidence, bundle.review_input.candidate_patch.sha256)
        saved = ScopeAssessment.model_validate(parse_json((output / "assessment.json").read_text(encoding="utf-8")))
        if assessment != saved:
            raise ValueError("M3a scope replay mismatch")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3a offline state kernel; no model calls")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--bundle", type=Path, required=True)
    run_cmd.add_argument("--proposals", type=Path, required=True)
    run_cmd.add_argument("--output", type=Path)
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("--bundle", type=Path, required=True)
    verify_cmd.add_argument("--run", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "verify":
        manifest = verify_run(args.bundle, args.run)
        print(f"M3a replay verified: {manifest['case_id']} {manifest['state_hash']}")
        return
    output = args.output or Path("reports/local") / f"m3a-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    manifest = run(args.bundle, args.proposals, output)
    print(f"M3a {manifest['status']}: {manifest['case_id']} -> {output}; "
          f"accepted={manifest['accepted_transitions']} rejected={manifest['rejected_transitions']}")


if __name__ == "__main__":
    main()
