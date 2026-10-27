"""Offline construction and replay of a final incremental review.

The saved response is supplied by the caller; this module never calls a provider.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

from scripts.m3l_offline_trace import verify_run as verify_trace
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import QuoteReviewPayload, load_bundle, parse_json, validate_output
from writscope.m3b import _artifacts
from writscope.providers import ModelConfig, ModelRequest


VERSION = "m3m_final_review_offline_v1"


class FinalReviewRequest(ModelRequest):
    method: Literal["incremental_review"]


def _read(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def build_request(bundle_dir: Path, trace_dir: Path, instructions: str) -> FinalReviewRequest:
    trace = verify_trace(bundle_dir, trace_dir)
    if trace["status"] != "completed":
        raise ValueError("no_final_review: extraction trace is incomplete")
    if not instructions.strip():
        raise ValueError("empty final review instructions")
    public_bundle = load_bundle(bundle_dir)
    state, context, steps = (_read(trace_dir / name) for name in
                             ("state.json", "task_context.json", "trace.json"))
    point = public_bundle.review_input.decision_point
    if (state["through_seq"] != point.seq or state["through_event_id"] != point.event_id
            or context["through_seq"] != point.seq
            or state["state_hash"] != trace["state_sha256"]
            or context["state_hash"] != trace["context_sha256"]):
        raise ValueError("final review state or context cutoff mismatch")
    base = {"review_input": public_bundle.review_input.model_dump(),
            "artifacts": dict(public_bundle.contents),
            "artifact_registry": public_bundle.registry.model_dump()}
    public = {**base, "current_state": state, "task_context": context,
              "history_lookup": {"policy": _read(trace_dir / "plan.json")["lookup_policy"],
                                 "attempts": [lookup for step in steps
                                              for lookup in step["lookups"]]}}
    return FinalReviewRequest(method="incremental_review",
                        model_config_values=ModelConfig(), instructions=instructions,
                        prompt_sha256=digest(instructions.encode("utf-8")),
                        evidence_sha256=canonical_hash(base), public_input=public,
                        output_contract="source_quote_v1",
                        output_schema=QuoteReviewPayload.model_json_schema())


def _state_bound(result, state: dict) -> None:
    known = {item["constraint_id"]: ("constraint", item)
             for item in state["constraints"]}
    known.update({item["authorization_id"]: ("authorization", item)
                  for item in state["authorizations"]})
    for item in result.derived_items:
        match = known.get(item.item_id)
        if match is None or match[0] != item.kind:
            raise ValueError("final review derived item outside current state")
        source = match[1]
        if not any(ref.event_id == source["source_event_id"] and
                   ref.quote == source["source_quote"] for ref in item.source_refs):
            raise ValueError("final review item source disagrees with state")


def _evaluate(raw: str, public_bundle, state: dict) -> dict:
    result = validate_output(raw, public_bundle, "incremental_review",
                             output_contract="source_quote_v1")
    _state_bound(result, state)
    return result.model_dump()


def _manifest(bundle, trace, request, status, error, artifacts):
    return {"schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "offline_scripted_final_review",
            "case_id": bundle.review_input.case_id,
            "public_bundle_sha256": bundle.sha256,
            "source_trace_artifact_set_sha256": trace["artifact_set_sha256"],
            "request_sha256": canonical_hash(request.model_dump()) if request else None,
            "evidence_sha256": request.evidence_sha256 if request else None,
            "status": status, "error": error, "new_model_calls": 0,
            "provider_usage": None, "provider_cost": "unknown",
            "runner_sha256": digest(Path(__file__).read_bytes()),
            "artifacts": artifacts, "artifact_set_sha256": canonical_hash(artifacts)}


def run_offline(bundle_dir: Path, trace_dir: Path, output: Path, instructions: str,
                response_text: str | None = None) -> dict:
    bundle_dir, trace_dir, output = (p.resolve() for p in (bundle_dir, trace_dir, output))
    if output.is_relative_to(bundle_dir) or output.is_relative_to(trace_dir):
        raise ValueError("review output cannot modify input")
    public_bundle = load_bundle(bundle_dir)
    trace = verify_trace(bundle_dir, trace_dir)
    request = None
    if trace["status"] == "completed":
        request = build_request(bundle_dir, trace_dir, instructions)
    output.mkdir(parents=True, exist_ok=False)
    status, error = "no_final_review", None
    if request is not None:
        (output / "prompt.md").write_text(instructions, encoding="utf-8", newline="\n")
        write_json(output / "request.json", request.model_dump())
        status = "prepared"
        if response_text is not None:
            (output / "response.txt").write_text(response_text, encoding="utf-8", newline="\n")
            try:
                result = _evaluate(response_text, public_bundle,
                                   request.public_input["current_state"])
            except ValueError as exc:
                status, error = "invalid_response", str(exc)
            else:
                status = "validated_scripted_response"
                write_json(output / "result.json", result)
    write_json(output / "call.json", {"kind": "no_provider_call", "status": status,
                                      "actual_model_calls": 0, "usage": None,
                                      "error": error})
    manifest = _manifest(public_bundle, trace, request, status, error, _artifacts(output))
    write_json(output / "run.json", manifest)
    return manifest


def verify_run(bundle_dir: Path, trace_dir: Path, output: Path) -> dict:
    bundle_dir, trace_dir, output = (p.resolve() for p in (bundle_dir, trace_dir, output))
    public_bundle = load_bundle(bundle_dir)
    trace = verify_trace(bundle_dir, trace_dir)
    manifest = _read(output / "run.json")
    artifacts = _artifacts(output)
    if artifacts != manifest.get("artifacts"):
        raise ValueError("final review artifact hash mismatch")
    request = None
    if trace["status"] == "completed":
        instructions = (output / "prompt.md").read_text(encoding="utf-8")
        request = build_request(bundle_dir, trace_dir, instructions)
        if _read(output / "request.json") != request.model_dump():
            raise ValueError("final review request replay mismatch")
    elif any((output / name).exists() for name in
             ("prompt.md", "request.json", "response.txt", "result.json")):
        raise ValueError("incomplete trace has final review artifacts")
    status, error = ("prepared", None) if request else ("no_final_review", None)
    if (output / "response.txt").exists():
        raw = (output / "response.txt").read_text(encoding="utf-8")
        try:
            result = _evaluate(raw, public_bundle, request.public_input["current_state"])
        except ValueError as exc:
            status, error = "invalid_response", str(exc)
            if (output / "result.json").exists():
                raise ValueError("invalid response has a result")
        else:
            status = "validated_scripted_response"
            if _read(output / "result.json") != result:
                raise ValueError("final review result replay mismatch")
    elif (output / "result.json").exists():
        raise ValueError("result without response")
    expected_call = {"kind": "no_provider_call", "status": status,
                     "actual_model_calls": 0, "usage": None, "error": error}
    if _read(output / "call.json") != expected_call:
        raise ValueError("final review call replay mismatch")
    expected = _manifest(public_bundle, trace, request, status, error, artifacts)
    if manifest != expected:
        raise ValueError("final review manifest mismatch")
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3m offline final review")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt", type=Path)
    parser.add_argument("--response", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.prompt is None:
            parser.error("run requires --prompt")
        result = run_offline(args.bundle, args.trace, args.output,
                             args.prompt.read_text(encoding="utf-8"),
                             args.response.read_text(encoding="utf-8") if args.response else None)
    else:
        result = verify_run(args.bundle, args.trace, args.output)
    print(result["status"])


if __name__ == "__main__":
    main()
