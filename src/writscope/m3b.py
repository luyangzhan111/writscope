"""Offline incremental extraction pipeline; no evaluator labels or network CLI."""

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import time

from pydantic import Field, ValidationError

from .artifacts import canonical_hash, digest, safe_path, write_json
from .contracts import PublicBundle, SourceQuote, load_bundle, parse_json
from .events import StrictModel
from .providers import ModelConfig, ModelRequest
from .providers.mock import Script, ScriptedMock, load_script
from .state import (AuthorizationDraft, AuthorizationState, ConstraintDraft,
                    Operation, ScopeAssessment, StateEngine, TransitionLogEntry,
                    TransitionProposal, assess_impacts)


class ExtractionPayload(StrictModel):
    """Only model-produced fields; identity, order and provenance come from the runner."""

    operation: Operation
    source_quote: str = Field(min_length=1)
    target_ids: list[str]
    constraint: ConstraintDraft | None
    authorization: AuthorizationDraft | None


def build_request(bundle: PublicBundle, event_index: int, state: AuthorizationState,
                  prompt: str, model: ModelConfig) -> ModelRequest:
    if not prompt.strip() or not 0 <= event_index < len(bundle.review_input.events):
        raise ValueError("invalid incremental prompt or event index")
    event = bundle.review_input.events[event_index]
    if state.through_seq != event_index:
        raise ValueError("state must end immediately before extraction event")
    review = bundle.review_input.model_dump()
    review["events"] = [item.model_dump() for item in bundle.review_input.events[:event_index + 1]]
    review["decision_point"] = {"event_id": event.event_id, "seq": event.seq}
    base_public = {"review_input": bundle.review_input.model_dump(),
                   "artifacts": dict(bundle.contents),
                   "artifact_registry": bundle.registry.model_dump()}
    public = {"review_input": review, "artifacts": dict(bundle.contents),
              "artifact_registry": bundle.registry.model_dump(),
              "prior_state": state.model_dump()}
    return ModelRequest(method="incremental_extract", model_config_values=model,
                        instructions=prompt, prompt_sha256=digest(prompt.encode("utf-8")),
                        evidence_sha256=canonical_hash(base_public), public_input=public,
                        output_contract="transition_quote_v1",
                        output_schema=ExtractionPayload.model_json_schema())


def _proposal(raw: str, bundle: PublicBundle, event_index: int, config_hash: str,
              call_id: str) -> TransitionProposal:
    payload = ExtractionPayload.model_validate(parse_json(raw))
    event = bundle.review_input.events[event_index]
    return TransitionProposal(schema_version="0.1-draft", case_id=bundle.review_input.case_id,
                              source_event_id=event.event_id, source_seq=event.seq,
                              extractor_config_hash=config_hash, extraction_call_id=call_id,
                              operation=payload.operation,
                              source=SourceQuote(event_id=event.event_id, quote=payload.source_quote),
                              target_ids=payload.target_ids, constraint=payload.constraint,
                              authorization=payload.authorization)


def _write_jsonl(path: Path, entries: list[TransitionLogEntry]):
    path.write_text("".join(json.dumps(item.model_dump(), ensure_ascii=False,
                                    sort_keys=True, separators=(",", ":")) + "\n"
                            for item in entries), encoding="utf-8", newline="\n")


def _artifacts(output: Path) -> dict[str, str]:
    return {path.relative_to(output).as_posix(): digest(safe_path(
                output, path.relative_to(output).as_posix()).read_bytes())
            for path in sorted(output.rglob("*")) if path.is_file() and path.name != "run.json"}


def _code_provenance() -> dict:
    package = Path(__file__).resolve().parent
    root = package.parents[1]
    sources = {path.relative_to(root).as_posix(): digest(path.read_bytes())
               for path in sorted(package.rglob("*.py"))}
    lock = root / "uv.lock"
    return {"source_tree_sha256": canonical_hash(sources),
            "environment_lock_sha256": digest(lock.read_bytes()) if lock.exists() else None}


async def run(bundle_path: Path, output: Path, provider, prompt: str, *,
              model: ModelConfig | None = None, timeout_seconds: float = 5.0,
              max_response_bytes: int = 100_000) -> dict:
    """One offline provider attempt per event; any failure stops without an assessment."""
    run_started = time.perf_counter()
    bundle = load_bundle(bundle_path)
    if output.resolve().is_relative_to(bundle_path.resolve()):
        raise ValueError("output cannot modify public input bundle")
    if not prompt.strip() or timeout_seconds <= 0 or max_response_bytes < 1:
        raise ValueError("invalid M3b run settings")
    if not isinstance(provider, ScriptedMock):
        raise ValueError("M3b is offline-only and requires ScriptedMock")
    model = model or ModelConfig()
    config_hash = canonical_hash({"prompt_sha256": digest(prompt.encode("utf-8")),
                                  "model": model.model_dump(),
                                  "output_contract": "transition_quote_v1"})
    engine = StateEngine(bundle.review_input.case_id, bundle.review_input.events,
                         config_hash, set(bundle.registry.artifacts))
    output.mkdir(parents=True, exist_ok=False)
    (output / "prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
    write_json(output / "script.json", provider.script.model_dump())
    status, error = "completed", None
    calls = []
    response_bytes = 0
    request_bytes = 0
    for index, event in enumerate(bundle.review_input.events):
        step_dir = output / f"step_{event.seq:03d}"
        step_dir.mkdir()
        request = build_request(bundle, index, engine.snapshot(), prompt, model)
        write_json(step_dir / "request.json", request.model_dump())
        request_bytes += (step_dir / "request.json").stat().st_size
        call_id = f"extract_{event.seq:03d}"
        call = {"call_id": call_id, "event_id": event.event_id,
                "request_sha256": digest((step_dir / "request.json").read_bytes()),
                "response_sha256": None, "status": "running", "error": None,
                "synthetic_usage": None, "actual_usage": None,
                "provider_elapsed_ms": None, "elapsed_ms": None,
                "transition_submitted": False}
        started = time.perf_counter()
        try:
            provider_started = time.perf_counter()
            try:
                response = await asyncio.wait_for(provider.generate(request.model_copy(deep=True)),
                                                  timeout_seconds)
            finally:
                call["provider_elapsed_ms"] = round((time.perf_counter() - provider_started) * 1000, 3)
            raw = response.text
            (step_dir / "response.txt").write_text(raw, encoding="utf-8", newline="\n")
            call["response_sha256"] = digest((step_dir / "response.txt").read_bytes())
            call["synthetic_usage"] = (response.synthetic_usage.model_dump()
                                       if response.synthetic_usage is not None else None)
            call["actual_usage"] = (response.actual_usage.model_dump(mode="json")
                                    if response.actual_usage is not None else None)
            response_bytes += (step_dir / "response.txt").stat().st_size
            if len(raw.encode("utf-8")) > max_response_bytes:
                raise ValueError("response exceeds limit")
            proposal = _proposal(raw, bundle, index, config_hash, call_id)
            entry = engine.submit(proposal)
            call["transition_submitted"] = True
            if not entry.transition.accepted:
                raise ValueError("state transition rejected: " + str(entry.transition.rejection_reason))
            call["status"] = "succeeded"
        except TimeoutError:
            call["status"], call["error"] = "timeout", "provider deadline exceeded"
        except (ValueError, TypeError, ValidationError) as exc:
            call["status"], call["error"] = "invalid_output", str(exc)
        except Exception as exc:
            call["status"], call["error"] = "provider_error", type(exc).__name__
        call["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        write_json(step_dir / "call.json", call)
        calls.append(call)
        if call["status"] != "succeeded":
            status, error = "incomplete", call["error"]
            break
    state = engine.snapshot()
    _write_jsonl(output / "transitions.jsonl", engine.records)
    write_json(output / "state.json", state.model_dump())
    assessment = None
    lookup_ms = None
    if status == "completed":
        started = time.perf_counter()
        assessment = assess_impacts(state, bundle.evidence, bundle.review_input.candidate_patch.sha256)
        lookup_ms = round((time.perf_counter() - started) * 1000, 3)
        write_json(output / "assessment.json", assessment.model_dump())
    artifacts = _artifacts(output)
    synthetic_usage = {}
    for field in ("input_tokens", "output_tokens", "cost_usd"):
        values = [(item["synthetic_usage"] or {}).get(field) for item in calls]
        known = [value for value in values if value is not None]
        synthetic_usage[field] = {"known_subtotal": sum(known),
                                  "unknown_attempts": len(values) - len(known),
                                  "complete_total": sum(known) if len(values) == len(known) else None}
    manifest = {"schema_version": "0.1-draft", "execution_kind": "offline_mock_incremental",
                "status": status, "error": error, "case_id": bundle.review_input.case_id,
                "public_bundle_sha256": bundle.sha256,
                "common_evidence_sha256": build_request(bundle, 0,
                    StateEngine(bundle.review_input.case_id, bundle.review_input.events,
                                config_hash).snapshot(), prompt, model).evidence_sha256,
                "extractor_config_hash": config_hash, "model": model.model_dump(),
                "prompt_sha256": digest(prompt.encode("utf-8")),
                "script_sha256": digest((output / "script.json").read_bytes()),
                **_code_provenance(),
                "attempted_events": len(calls), "consumed_events": state.through_seq,
                "accepted_transitions": sum(item.transition.accepted for item in engine.records),
                "rejected_transitions": sum(not item.transition.accepted for item in engine.records),
                "state_hash": state.state_hash,
                "cost": {"actual_model_calls": 0, "provider_attempts": len(calls),
                         "actual_extraction_tokens": None, "actual_extraction_cost_cny": None,
                         "provider_billed_cny": None,
                         "unknown_cost_reason": "scripted mock; real extraction cost not observed",
                         "synthetic_test_usage": synthetic_usage,
                         "request_bytes": request_bytes, "response_bytes": response_bytes,
                         "provider_elapsed_ms": round(sum(item["provider_elapsed_ms"] or 0 for item in calls), 3),
                         "pipeline_attempt_elapsed_ms": round(sum(item["elapsed_ms"] for item in calls), 3),
                         "state_update_elapsed_ms": round(sum(item.update_elapsed_ms for item in engine.records), 3),
                         "scope_lookup_elapsed_ms": lookup_ms,
                         "run_wall_elapsed_ms": round((time.perf_counter() - run_started) * 1000, 3)},
                "artifacts": artifacts, "artifact_set_sha256": canonical_hash(artifacts)}
    write_json(output / "run.json", manifest)
    return manifest


def verify_run(bundle_path: Path, output: Path) -> dict:
    bundle = load_bundle(bundle_path)
    manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    if (manifest.get("execution_kind") != "offline_mock_incremental" or
            manifest.get("public_bundle_sha256") != bundle.sha256 or
            manifest.get("case_id") != bundle.review_input.case_id or
            manifest.get("status") not in {"completed", "incomplete"}):
        raise ValueError("M3b run identity mismatch")
    artifacts = _artifacts(output)
    if artifacts != manifest.get("artifacts") or canonical_hash(artifacts) != manifest.get("artifact_set_sha256"):
        raise ValueError("M3b artifact hash mismatch")
    prompt = (output / "prompt.md").read_text(encoding="utf-8")
    script = Script.model_validate(parse_json((output / "script.json").read_text(encoding="utf-8")))
    if digest((output / "script.json").read_bytes()) != manifest["script_sha256"]:
        raise ValueError("M3b script hash mismatch")
    model = ModelConfig.model_validate(manifest["model"])
    config_hash = canonical_hash({"prompt_sha256": digest(prompt.encode("utf-8")),
                                  "model": model.model_dump(),
                                  "output_contract": "transition_quote_v1"})
    if config_hash != manifest["extractor_config_hash"] or digest(prompt.encode("utf-8")) != manifest["prompt_sha256"]:
        raise ValueError("extractor configuration mismatch")
    lines = (output / "transitions.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [TransitionLogEntry.model_validate(parse_json(line)) for line in lines]
    engine = StateEngine(bundle.review_input.case_id, bundle.review_input.events,
                         config_hash, set(bundle.registry.artifacts))
    calls = []
    expected_files = {"prompt.md", "script.json", "transitions.jsonl", "state.json"}
    if manifest["status"] == "completed":
        expected_files.add("assessment.json")
    for index in range(manifest["attempted_events"]):
        event = bundle.review_input.events[index]
        step = output / f"step_{event.seq:03d}"
        saved_request = ModelRequest.model_validate(parse_json((step / "request.json").read_text(encoding="utf-8")))
        expected = build_request(bundle, index, engine.snapshot(), prompt, model)
        if saved_request != expected or expected.evidence_sha256 != manifest["common_evidence_sha256"]:
            raise ValueError("incremental request replay mismatch")
        call = parse_json((step / "call.json").read_text(encoding="utf-8"))
        expected_files.update({f"step_{event.seq:03d}/request.json", f"step_{event.seq:03d}/call.json"})
        if (call["request_sha256"] != digest((step / "request.json").read_bytes()) or
                call["event_id"] != event.event_id or call["call_id"] != f"extract_{event.seq:03d}"):
            raise ValueError("incremental call request mismatch")
        if call["response_sha256"] is not None:
            expected_files.add(f"step_{event.seq:03d}/response.txt")
            raw = (step / "response.txt").read_text(encoding="utf-8")
            if call["response_sha256"] != digest((step / "response.txt").read_bytes()):
                raise ValueError("incremental response mismatch")
            if index >= len(script.steps) or script.steps[index].action != "response" or raw != script.steps[index].text:
                raise ValueError("response disagrees with saved offline script")
            if call["transition_submitted"]:
                proposal = _proposal(raw, bundle, index, config_hash, call["call_id"])
                generated = engine.submit(proposal)
                if generated.transition != entries[index].transition or generated.proposal != entries[index].proposal:
                    raise ValueError("incremental transition replay mismatch")
                if (call["status"] == "succeeded") != generated.transition.accepted:
                    raise ValueError("transition and call status disagree")
        elif call["transition_submitted"] or call["status"] == "succeeded":
            raise ValueError("successful or submitted call has no response")
        calls.append(call)
    if set(artifacts) != expected_files:
        raise ValueError("unexpected M3b artifact set")
    saved_state = AuthorizationState.model_validate(parse_json((output / "state.json").read_text(encoding="utf-8")))
    if (engine.snapshot() != saved_state or saved_state.state_hash != manifest["state_hash"] or
            len(entries) != len(engine.records) or manifest["consumed_events"] != saved_state.through_seq or
            manifest["accepted_transitions"] != sum(item.transition.accepted for item in entries) or
            manifest["rejected_transitions"] != sum(not item.transition.accepted for item in entries) or
            manifest["cost"]["actual_model_calls"] != 0 or
            manifest["cost"]["provider_attempts"] != len(calls) or
            manifest["cost"]["actual_extraction_tokens"] is not None or
            manifest["cost"]["actual_extraction_cost_cny"] is not None or
            manifest["cost"]["request_bytes"] != sum(
                (output / f"step_{index + 1:03d}/request.json").stat().st_size
                for index in range(len(calls))) or
            manifest["cost"]["response_bytes"] != sum(
                (output / f"step_{index + 1:03d}/response.txt").stat().st_size
                for index, call in enumerate(calls) if call["response_sha256"] is not None) or
            manifest["cost"]["provider_elapsed_ms"] != round(
                sum(call["provider_elapsed_ms"] or 0 for call in calls), 3) or
            manifest["cost"]["pipeline_attempt_elapsed_ms"] != round(
                sum(call["elapsed_ms"] for call in calls), 3) or
            manifest["cost"]["state_update_elapsed_ms"] != round(
                sum(item.update_elapsed_ms for item in entries), 3)):
        raise ValueError("incremental run totals or state mismatch")
    complete = (len(calls) == len(bundle.review_input.events) and
                saved_state.through_seq == bundle.review_input.decision_point.seq and
                all(call["status"] == "succeeded" for call in calls))
    if (manifest["status"] == "completed") != complete or (
            not complete and calls and calls[-1]["status"] == "succeeded"):
        raise ValueError("incremental completion mismatch")
    if complete:
        expected_assessment = assess_impacts(saved_state, bundle.evidence,
                                             bundle.review_input.candidate_patch.sha256)
        saved_assessment = ScopeAssessment.model_validate(parse_json((output / "assessment.json").read_text(encoding="utf-8")))
        if expected_assessment != saved_assessment:
            raise ValueError("incremental assessment mismatch")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3b offline incremental pipeline; no real model calls")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--bundle", type=Path, required=True)
    run_cmd.add_argument("--script", type=Path, required=True)
    run_cmd.add_argument("--prompt", type=Path, default=Path("prompts/incremental_extract.md"))
    run_cmd.add_argument("--output", type=Path)
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("--bundle", type=Path, required=True)
    verify_cmd.add_argument("--run", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "verify":
        manifest = verify_run(args.bundle, args.run)
        print(f"M3b replay verified: {manifest['case_id']} {manifest['state_hash']}")
        return
    script = load_script(args.script)
    prompt = args.prompt.read_text(encoding="utf-8")
    output = args.output or Path("reports/local") / f"m3b-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    manifest = asyncio.run(run(args.bundle, output, ScriptedMock(script), prompt))
    print(f"M3b {manifest['status']}: {manifest['case_id']} -> {output}; "
          f"calls={manifest['cost']['provider_attempts']} actual_model_calls=0")


if __name__ == "__main__":
    main()
