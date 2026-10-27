"""M3g versioned offline integration of the M3f scope policy.

Only archived public model responses are consumed. This entry has no paid-run
command, credentials, network client, private labels, or scoring path.
"""

import argparse
from pathlib import Path

from scripts import m3d_gate, m3e_gate, m3f_scope_policy
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import load_bundle, parse_json
from writscope.m3b import _artifacts, _code_provenance, _proposal, _write_jsonl, build_request
from writscope.state import AuthorizationState, ScopeAssessment, TransitionLogEntry, assess_impacts


STAGE = "m3g_scope_policy_runner_v1"
KIND = "offline_saved_response_integration"
SOURCES = {"c002": m3d_gate, "c003": m3e_gate}


def _source(root: Path, case: str, source_run: Path) -> tuple[dict, dict]:
    if case not in SOURCES:
        raise ValueError("M3g only supports c002 and c003 development cases")
    source = SOURCES[case]
    manifest = source.verify_run(root, source_run)
    if manifest["status"] != "completed" or set(manifest["cases"]) != {case}:
        raise ValueError("expected one completed historical case")
    preflight_path = source_run / "preflight.json"
    return manifest, parse_json(preflight_path.read_text(encoding="utf-8"))


def prepare(root: Path, case: str, source_run: Path) -> dict:
    """Freeze the old response source, new policy, prompt, code and public input."""
    source_manifest, source_preflight = _source(root, case, source_run)
    bundle = load_bundle(m3e_gate._input_root(root) / case)
    prompt = (root / "prompts" / "incremental_extract_v2.md").read_text(encoding="utf-8")
    if digest(prompt.encode("utf-8")) != source_preflight["prompt_sha256"]:
        raise ValueError("source prompt differs from current v2 prompt")
    return {"schema_version": "0.1-draft", "stage": STAGE,
            "execution_kind": "offline_preflight_no_model_call", "case_id": case,
            "source_execution_kind": source_manifest["execution_kind"],
            "source_artifact_set_sha256": source_manifest["artifact_set_sha256"],
            "source_preflight_sha256": digest((source_run / "preflight.json").read_bytes()),
            "public_bundle_sha256": bundle.sha256,
            "evidence_sha256": source_preflight["cases"][case]["evidence_sha256"],
            "prompt_sha256": source_preflight["prompt_sha256"],
            "model": m3e_gate.MODEL.model_dump(),
            "output_contract": "transition_quote_v1",
            "policy_version": m3f_scope_policy.POLICY_VERSION,
            "policy_source_sha256": digest(Path(m3f_scope_policy.__file__).read_bytes()),
            "source_gate_sha256": digest(Path(SOURCES[case].__file__).read_bytes()),
            "runner_sha256": digest(Path(__file__).read_bytes()),
            **_code_provenance(), "planned_events": len(bundle.review_input.events),
            "attempts_per_event": 1, "concurrency": 1, "retries": 0,
            "timeout_seconds": source_preflight["timeout_seconds"],
            "historical_price_ceiling": source_preflight["price_ceiling"],
            "fee_policy_status": "historical_snapshot_only_new_paid_cap_and_price_check_required",
            "new_model_calls": 0, "provider_billed_cny": None}


def _config_hash(preflight: dict) -> str:
    return canonical_hash({"prompt_sha256": preflight["prompt_sha256"],
                           "model": preflight["model"],
                           "output_contract": preflight["output_contract"],
                           "policy_version": preflight["policy_version"],
                           "policy_source_sha256": preflight["policy_source_sha256"]})


def _same_public_request(new_request: dict, old_request: dict) -> bool:
    """The policy changes prior_state hashes, but not raw event evidence."""
    new_request = dict(new_request)
    old_request = dict(old_request)
    new_request["public_input"] = dict(new_request["public_input"])
    old_request["public_input"] = dict(old_request["public_input"])
    new_request["public_input"].pop("prior_state", None)
    old_request["public_input"].pop("prior_state", None)
    return new_request == old_request


def run_offline(root: Path, case: str, source_run: Path, output: Path,
                response_loader=None) -> dict:
    """Run saved responses through the new engine; stop on the first failure.

    response_loader is only an offline test seam. It receives an archived text
    path; production replay reads that path directly.
    """
    if output.resolve().is_relative_to(source_run.resolve()) or output.resolve().is_relative_to(
            m3e_gate._input_root(root).resolve()):
        raise ValueError("output cannot modify source run or public input")
    preflight = prepare(root, case, source_run)
    bundle = load_bundle(m3e_gate._input_root(root) / case)
    prompt = (root / "prompts" / "incremental_extract_v2.md").read_text(encoding="utf-8")
    config_hash = _config_hash(preflight)
    engine = m3f_scope_policy.ScopeValidatedStateEngine(
        case, bundle.review_input.events, config_hash, set(bundle.registry.artifacts))
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "preflight.json", preflight)
    (output / "prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
    calls = []
    status = "completed"
    for index, event in enumerate(bundle.review_input.events):
        request = build_request(bundle, index, engine.snapshot(), prompt, m3e_gate.MODEL)
        source_step = source_run / case / f"step_{event.seq:03d}"
        if not _same_public_request(request.model_dump(mode="json"), parse_json(
                (source_step / "request.json").read_text(encoding="utf-8"))):
            raise ValueError("new runner request differs from historical public request")
        step = output / f"step_{event.seq:03d}"
        step.mkdir()
        write_json(step / "request.json", request.model_dump())
        call = {"event_id": event.event_id, "status": "running", "error": None,
                "request_sha256": digest((step / "request.json").read_bytes()),
                "source_response_sha256": None, "response_sha256": None,
                "transition_accepted": None}
        write_json(step / "call.json", call)
        try:
            source_path = source_step / "response.txt"
            raw = (response_loader or _read_response)(source_path)
            if not isinstance(raw, str):
                raise TypeError("offline response loader must return text")
            if digest(raw.encode("utf-8")) != digest(source_path.read_bytes()):
                raise ValueError("archived response content changed")
            (step / "response.txt").write_text(raw, encoding="utf-8", newline="\n")
            call["source_response_sha256"] = digest(source_path.read_bytes())
            call["response_sha256"] = digest((step / "response.txt").read_bytes())
            proposal = _proposal(raw, bundle, index, config_hash,
                                 f"{case}_extract_{event.seq:03d}")
            entry = engine.submit(proposal)
            call["transition_accepted"] = entry.transition.accepted
            if not entry.transition.accepted:
                raise ValueError("state transition rejected: " + str(entry.transition.rejection_reason))
            call["status"] = "succeeded"
        except (OSError, TypeError, ValueError) as exc:
            call["status"] = "invalid_output" if call["response_sha256"] else "source_error"
            call["error"] = str(exc)
            status = "stopped_on_failure"
        write_json(step / "call.json", call)
        calls.append(f"step_{event.seq:03d}/call.json")
        if status != "completed":
            break
    state = engine.snapshot()
    _write_jsonl(output / "transitions.jsonl", engine.records)
    write_json(output / "state.json", state.model_dump())
    if status == "completed":
        assessment = assess_impacts(state, bundle.evidence,
                                    bundle.review_input.candidate_patch.sha256)
        write_json(output / "assessment.json", assessment.model_dump())
    manifest = {"schema_version": "0.1-draft", "stage": STAGE, "execution_kind": KIND,
                "status": status, "case_id": case, "attempted_events": len(calls),
                "calls": calls, "state_hash": state.state_hash,
                "preflight_sha256": digest((output / "preflight.json").read_bytes()),
                "source_artifact_set_sha256": preflight["source_artifact_set_sha256"],
                "new_model_calls": 0, "provider_billed_cny": None,
                "cost_status": "offline_replay_no_new_provider_usage",
                "comparison_status": "mechanism_integration_not_method_comparison"}
    manifest["artifacts"] = _artifacts(output)
    manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
    write_json(output / "run.json", manifest)
    return manifest


def _read_response(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def verify_run(root: Path, case: str, source_run: Path, output: Path) -> dict:
    """Verify frozen inputs, each request, stop point, state and artifact hashes."""
    manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    preflight = parse_json((output / "preflight.json").read_text(encoding="utf-8"))
    if (manifest.get("stage") != STAGE or manifest.get("execution_kind") != KIND or
            manifest.get("case_id") != case or
            manifest.get("status") not in {"completed", "stopped_on_failure"} or
            manifest.get("new_model_calls") != 0 or
            manifest.get("preflight_sha256") != digest((output / "preflight.json").read_bytes()) or
            preflight != prepare(root, case, source_run)):
        raise ValueError("M3g frozen run identity mismatch")
    artifacts = _artifacts(output)
    if artifacts != manifest.get("artifacts") or canonical_hash(artifacts) != manifest.get("artifact_set_sha256"):
        raise ValueError("M3g artifact hash mismatch")
    bundle = load_bundle(m3e_gate._input_root(root) / case)
    prompt = (output / "prompt.md").read_text(encoding="utf-8")
    if digest(prompt.encode("utf-8")) != preflight["prompt_sha256"]:
        raise ValueError("M3g prompt mismatch")
    engine = m3f_scope_policy.ScopeValidatedStateEngine(
        case, bundle.review_input.events, _config_hash(preflight), set(bundle.registry.artifacts))
    calls = manifest["calls"]
    if not calls or len(calls) != manifest["attempted_events"] or len(calls) > len(bundle.review_input.events):
        raise ValueError("M3g call count mismatch")
    for index, ref in enumerate(calls):
        event = bundle.review_input.events[index]
        if ref != f"step_{event.seq:03d}/call.json":
            raise ValueError("M3g call order mismatch")
        step = output / f"step_{event.seq:03d}"
        request = build_request(bundle, index, engine.snapshot(), prompt, m3e_gate.MODEL)
        if request.model_dump(mode="json") != parse_json((step / "request.json").read_text(encoding="utf-8")):
            raise ValueError("M3g request replay mismatch")
        call = parse_json((step / "call.json").read_text(encoding="utf-8"))
        if call["event_id"] != event.event_id or call["request_sha256"] != digest(
                (step / "request.json").read_bytes()):
            raise ValueError("M3g call provenance mismatch")
        if call["response_sha256"] is not None:
            raw = (step / "response.txt").read_text(encoding="utf-8")
            source_path = source_run / case / f"step_{event.seq:03d}" / "response.txt"
            if (call["response_sha256"] != digest((step / "response.txt").read_bytes()) or
                    call["source_response_sha256"] != digest(source_path.read_bytes()) or
                    raw != source_path.read_text(encoding="utf-8")):
                raise ValueError("M3g source response mismatch")
            entry = engine.submit(_proposal(raw, bundle, index, _config_hash(preflight),
                                             f"{case}_extract_{event.seq:03d}"))
            if call["transition_accepted"] != entry.transition.accepted or (
                    call["status"] == "succeeded") != entry.transition.accepted:
                raise ValueError("M3g transition/status mismatch")
            if not entry.transition.accepted and call["error"] != (
                    "state transition rejected: " + str(entry.transition.rejection_reason)):
                raise ValueError("M3g rejection reason mismatch")
        elif call["status"] != "source_error" or call["transition_accepted"] is not None:
            raise ValueError("M3g missing response/status mismatch")
        if call["status"] != "succeeded" and index != len(calls) - 1:
            raise ValueError("M3g continued after failure")
    state = AuthorizationState.model_validate(parse_json((output / "state.json").read_text(encoding="utf-8")))
    entries = [TransitionLogEntry.model_validate(parse_json(line)) for line in
               (output / "transitions.jsonl").read_text(encoding="utf-8").splitlines()]
    if (state != engine.snapshot() or state.state_hash != manifest["state_hash"] or
            len(entries) != len(engine.records) or
            any(left.proposal != right.proposal or left.transition != right.transition
                for left, right in zip(entries, engine.records))):
        raise ValueError("M3g state replay mismatch")
    complete = len(calls) == len(bundle.review_input.events) and all(
        parse_json((output / ref).read_text(encoding="utf-8"))["status"] == "succeeded"
        for ref in calls)
    if (manifest["status"] == "completed") != complete:
        raise ValueError("M3g completion mismatch")
    if complete:
        actual = ScopeAssessment.model_validate(parse_json(
            (output / "assessment.json").read_text(encoding="utf-8")))
        expected = assess_impacts(state, bundle.evidence, bundle.review_input.candidate_patch.sha256)
        if actual != expected:
            raise ValueError("M3g assessment replay mismatch")
    elif (output / "assessment.json").exists():
        raise ValueError("M3g incomplete run has assessment")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3g offline scope policy integration")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("replay", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=Path.cwd())
        command.add_argument("--case", choices=sorted(SOURCES), required=True)
        command.add_argument("--source-run", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root, source_run, output = (path.resolve() for path in
                                (args.root, args.source_run, args.output))
    if args.command == "replay":
        result = run_offline(root, args.case, source_run, output)
    else:
        result = verify_run(root, args.case, source_run, output)
    print(f"M3g {result['status']}: {result['attempted_events']} saved responses, 0 new model calls")


if __name__ == "__main__":
    main()
