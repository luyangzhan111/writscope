"""M3p: one replayable, scripted-provider incremental trajectory.

This entry point accepts ScriptedMock only. It never reads credentials or
constructs a network provider. Scripted outputs test the runner, not models.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import time

from scripts import (m3i_task_context, m3j_transition_batch, m3k_history_lookup,
                     m3m_final_review, m3n_budget_envelope, m3o_interaction_contract)
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from scripts.m3k_history_lookup import HistoryLookupSession, LookupPolicy, LookupRequest
from scripts.m3m_final_review import _evaluate
from scripts.m3n_budget_envelope import (AttemptLedger, MAX_EVENTS, MAX_LOOKUPS,
                                         MAX_OUTPUT_TOKENS, TIMEOUT_SECONDS)
from scripts.m3o_interaction_contract import (LookupEvidence, LookupIntent,
                                              build_final_request, build_request,
                                              parse_response)
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.budget import PriceCeiling
from writscope.contracts import load_bundle, parse_json
from writscope.events import DecisionPoint
from writscope.m3b import _artifacts, _code_provenance
from writscope.providers import ModelConfig, ProviderError
from writscope.providers.deepseek import wire_bytes
from writscope.providers.mock import Script, ScriptedMock, load_script


VERSION = "m3p_scripted_trajectory_v1"
MAX_RESPONSE_BYTES = 100_000
COMPONENTS = (m3i_task_context, m3j_transition_batch, m3k_history_lookup,
              m3m_final_review, m3n_budget_envelope, m3o_interaction_contract)


def _config_hash(interaction_prompt: str, final_prompt: str, model: ModelConfig,
                 policy: LookupPolicy) -> str:
    return canonical_hash({"version": VERSION,
                           "interaction_prompt_sha256": digest(interaction_prompt.encode()),
                           "final_prompt_sha256": digest(final_prompt.encode()),
                           "model": model.model_dump(), "lookup_policy": policy.model_dump(),
                           "batch_policy": m3j_transition_batch.POLICY_VERSION,
                           "context_policy": m3i_task_context.POLICY_VERSION})


def _component_hashes() -> dict[str, str]:
    return {Path(module.__file__).name: digest(Path(module.__file__).read_bytes())
            for module in COMPONENTS}


def _settings(interaction_prompt: str, final_prompt: str, model: ModelConfig,
              policy: LookupPolicy, price: PriceCeiling, cap: Decimal) -> dict:
    if not interaction_prompt.strip() or not final_prompt.strip():
        raise ValueError("both prompts are required")
    if (model.model_id != "deepseek-flash" or model.temperature is None or
            model.max_output_tokens != MAX_OUTPUT_TOKENS):
        raise ValueError("scripted wire configuration must match M3n bounds")
    if policy.max_requests != MAX_LOOKUPS or policy.max_events_per_request > 2:
        raise ValueError("lookup policy exceeds M3n bounds")
    if not cap.is_finite() or cap <= 0:
        raise ValueError("positive finite simulated cap required")
    return {"version": VERSION, "interaction_prompt": interaction_prompt,
            "final_prompt": final_prompt, "model": model.model_dump(),
            "lookup_policy": policy.model_dump(), "simulated_price": price.model_dump(mode="json"),
            "simulated_local_cap_cny": str(cap),
            "timeout_seconds": TIMEOUT_SECONDS, "max_response_bytes": MAX_RESPONSE_BYTES}


def _price(value: dict) -> PriceCeiling:
    return PriceCeiling.model_validate({**value,
        "input_cny_per_million": Decimal(value["input_cny_per_million"]),
        "output_cny_per_million": Decimal(value["output_cny_per_million"])})


async def _execute(bundle, script: Script, settings: dict) -> dict:
    model = ModelConfig.model_validate(settings["model"])
    policy = LookupPolicy.model_validate(settings["lookup_policy"])
    price = _price(settings["simulated_price"])
    ledger = AttemptLedger(price, Decimal(settings["simulated_local_cap_cny"]))
    events = bundle.review_input.events
    if len(events) != MAX_EVENTS:
        raise ValueError("M3p requires five visible events")
    config_hash = _config_hash(settings["interaction_prompt"], settings["final_prompt"],
                               model, policy)
    state = BatchStateEngine(bundle.review_input.case_id, events, config_hash,
                             set(bundle.registry.artifacts))
    context = TaskContextLedger(events, set(bundle.registry.artifacts))
    provider = ScriptedMock(script)
    calls: list[dict] = []
    steps: list[dict] = []
    lookup_history: list[LookupEvidence] = []
    failure: dict | None = None
    final_request = None
    final_result = None

    async def invoke(request, stage: str, event_seq: int | None):
        call_id = f"scripted_call_{len(calls) + 1:03d}"
        row = {"call_id": call_id, "stage": stage, "event_seq": event_seq,
               "request": request.model_dump(), "response_text": None,
               "synthetic_usage": None, "error": None, "status": "running",
               "attempt": None}
        try:
            ledger.reserve(stage, event_seq, len(wire_bytes(request)))
        except ValueError as exc:
            row["status"], row["error"] = "budget_rejected", str(exc)
            calls.append(row)
            return row, None
        try:
            response = await asyncio.wait_for(provider.generate(request),
                                              timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            row["status"], row["error"] = "timeout", "scripted provider timeout"
            row["attempt"] = ledger.finish("timeout")
            calls.append(row)
            return row, None
        except ProviderError as exc:
            row["status"], row["error"] = "provider_error", str(exc)
            usage = exc.synthetic_usage
            row["synthetic_usage"] = usage.model_dump() if usage else None
            row["attempt"] = ledger.finish(
                "provider_error", input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None)
            calls.append(row)
            return row, None
        row["response_text"] = response.text
        row["synthetic_usage"] = (response.synthetic_usage.model_dump()
                                  if response.synthetic_usage else None)
        if len(response.text.encode("utf-8")) > MAX_RESPONSE_BYTES:
            row["status"], row["error"] = "invalid_output", "response byte limit exceeded"
            row["attempt"] = ledger.finish("invalid_output",
                input_tokens=response.synthetic_usage.input_tokens if response.synthetic_usage else None,
                output_tokens=response.synthetic_usage.output_tokens if response.synthetic_usage else None)
            calls.append(row)
            return row, None
        calls.append(row)
        return row, response

    def finish(row, status: str, response):
        usage = response.synthetic_usage if response else None
        attempt = ledger.finish(status, input_tokens=usage.input_tokens if usage else None,
                                output_tokens=usage.output_tokens if usage else None)
        row["attempt"] = attempt
        row["status"] = attempt["status"]
        return attempt["status"] == "succeeded"

    for index, event in enumerate(events):
        if failure is not None:
            break
        prior_lookups = len(lookup_history)
        current_lookups = []
        step = {"seq": event.seq, "event_id": event.event_id,
                "prior_state_sha256": state.snapshot().state_hash,
                "prior_context_sha256": context.snapshot().state_hash,
                "call_ids": [], "lookups": [], "context_record": None,
                "batch_record": None, "context_elapsed_ms": None,
                "batch_elapsed_ms": None, "status": "running", "error": None}
        steps.append(step)
        stage = "extract"
        while True:
            try:
                request = build_request(bundle, index, state.snapshot(), context.snapshot(),
                    settings["interaction_prompt"], model, policy,
                    prior_lookup_attempts=prior_lookups, lookup_records=current_lookups)
            except ValueError as exc:
                step["status"], step["error"] = "request_rejected", str(exc)
                break
            call, response = await invoke(request, stage, event.seq)
            step["call_ids"].append(call["call_id"])
            if response is None:
                step["status"], step["error"] = call["status"], call["error"]
                break
            try:
                parsed = parse_response(response.text, bundle, index, config_hash,
                    call["call_id"], policy,
                    lookup_attempts_used=prior_lookups + len(current_lookups))
            except ValueError as exc:
                finish(call, "invalid_output", response)
                step["status"], step["error"] = call["status"], str(exc)
                break
            if isinstance(parsed, LookupIntent):
                if not finish(call, "succeeded", response):
                    step["status"], step["error"] = "usage_missing", "scripted token usage missing"
                    break
                point = DecisionPoint(event_id=event.event_id, seq=event.seq)
                session = HistoryLookupSession(events, point, policy,
                    initial_attempts=prior_lookups + len(current_lookups))
                record = session.lookup(LookupRequest(event_ids=parsed.event_ids))
                step["lookups"].append(record.model_dump())
                ledger.record_local_lookup(event.seq, succeeded=record.status == "succeeded")
                if record.status != "succeeded":
                    step["status"], step["error"] = "lookup_denied", record.denial_reason
                    break
                # Local timing is audited above, but never enters the model wire
                # request or the final review; otherwise replay changes bytes.
                stable_record = record.model_copy(update={"elapsed_ms": 0.0})
                current_lookups.append(stable_record)
                lookup_history.append(LookupEvidence(event_id=event.event_id,
                                                     seq=event.seq, record=stable_record))
                stage = "lookup_continuation"
                continue
            if (response.synthetic_usage is None or
                    response.synthetic_usage.input_tokens is None or
                    response.synthetic_usage.output_tokens is None):
                finish(call, "succeeded", response)
                step["status"], step["error"] = "usage_missing", "scripted token usage missing"
                break
            started = time.perf_counter()
            try:
                staged_context = deepcopy(context)
                context_record = staged_context.submit(parsed.context_batch)
            except ValueError as exc:
                step["context_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
                finish(call, "state_rejected", response)
                step["status"], step["error"] = "context_rejected", str(exc)
                break
            step["context_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            started = time.perf_counter()
            batch_record = state.submit_batch(parsed.transition_batch)
            step["batch_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            step["batch_record"] = batch_record.model_dump()
            if not batch_record.accepted:
                finish(call, "state_rejected", response)
                step["status"], step["error"] = "batch_rejected", batch_record.rejection_reason
                break
            if not finish(call, "succeeded", response):
                step["status"], step["error"] = "usage_missing", "scripted token usage missing"
                break
            context = staged_context
            step["context_record"] = context_record.model_dump()
            ledger.accept_event(event.seq)
            step["status"] = "accepted"
            break
        step["state_sha256"] = state.snapshot().state_hash
        step["context_sha256"] = context.snapshot().state_hash
        if step["status"] != "accepted":
            failure = {"stage": "event", "event_seq": event.seq,
                       "status": step["status"], "error": step["error"]}
            ledger.stopped = True

    if failure is None:
        try:
            final_request = build_final_request(bundle, state.snapshot(), context.snapshot(),
                settings["final_prompt"], model, policy, lookup_history)
        except ValueError as exc:
            failure = {"stage": "final_request", "event_seq": None,
                       "status": "request_rejected", "error": str(exc)}
            ledger.stopped = True
        else:
            call, response = await invoke(final_request, "final_review", None)
            if response is None:
                failure = {"stage": "final_review", "event_seq": None,
                           "status": call["status"], "error": call["error"]}
            else:
                try:
                    result = _evaluate(response.text, bundle,
                                       final_request.public_input["current_state"])
                except ValueError as exc:
                    finish(call, "invalid_output", response)
                    failure = {"stage": "final_review", "event_seq": None,
                               "status": call["status"], "error": str(exc)}
                else:
                    if finish(call, "succeeded", response):
                        final_result = result
                    else:
                        failure = {"stage": "final_review", "event_seq": None,
                                   "status": "usage_missing", "error": "scripted token usage missing"}
    if failure is None and provider.position != len(script.steps):
        failure = {"stage": "script", "event_seq": None,
                   "status": "unused_script_steps", "error": "script has unconsumed responses"}
        final_result = None
        ledger.stopped = True
    return {"status": "completed" if failure is None else "stopped_on_failure",
            "failure": failure, "calls": calls, "steps": steps,
            "state": state.snapshot().model_dump(),
            "task_context": context.snapshot().model_dump(),
            "lookup_history": [item.model_dump() for item in lookup_history],
            "final_request": final_request.model_dump() if final_request else None,
            "final_result": final_result, "ledger": ledger.summary(),
            "script_steps_consumed": provider.position}


def _normalized(result: dict) -> dict:
    normalized = deepcopy(result)
    for step in normalized["steps"]:
        step.pop("context_elapsed_ms", None)
        step.pop("batch_elapsed_ms", None)
        for lookup in step["lookups"]:
            lookup.pop("elapsed_ms", None)
    for item in normalized["lookup_history"]:
        item["record"].pop("elapsed_ms", None)
    return normalized


def _manifest(bundle, settings: dict, script: Script, result: dict,
              artifacts: dict[str, str]) -> dict:
    return {"schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "offline_scripted_provider_trajectory",
            "case_id": bundle.review_input.case_id,
            "status": result["status"], "failure": result["failure"],
            "public_bundle_sha256": bundle.sha256,
            "settings_sha256": canonical_hash(settings),
            "script_sha256": canonical_hash(script.model_dump()),
            "events_accepted": result["ledger"]["events_accepted"],
            "simulated_provider_attempts": result["ledger"]["provider_attempts"],
            "lookup_attempts": result["ledger"]["lookup_attempts"],
            "actual_model_calls": 0, "provider_billed_cost_cny": "unknown",
            "simulated_reservation_cny": result["ledger"]["reserved_cny"],
            "unknown_billed_attempts": result["ledger"]["unknown_billed_attempts"],
            "final_review_valid": result["final_result"] is not None,
            "final_review_sha256": (canonical_hash(result["final_result"])
                                     if result["final_result"] is not None else None),
            "runner_sha256": digest(Path(__file__).read_bytes()),
            "component_sha256": _component_hashes(), **_code_provenance(),
            "artifacts": artifacts, "artifact_set_sha256": canonical_hash(artifacts)}


def _write_result(output: Path, result: dict) -> None:
    for name, value in (("calls.json", result["calls"]), ("steps.json", result["steps"]),
                        ("state.json", result["state"]),
                        ("task_context.json", result["task_context"]),
                        ("lookup_history.json", result["lookup_history"]),
                        ("ledger.json", result["ledger"]),
                        ("outcome.json", {"status": result["status"],
                                          "failure": result["failure"],
                                          "script_steps_consumed": result["script_steps_consumed"]})):
        write_json(output / name, value)
    if result["final_request"] is not None:
        write_json(output / "final_request.json", result["final_request"])
    if result["final_result"] is not None:
        write_json(output / "final_result.json", result["final_result"])


def run_offline(bundle_dir: Path, script: Script, output: Path, interaction_prompt: str,
                final_prompt: str, model: ModelConfig, policy: LookupPolicy,
                simulated_price: PriceCeiling, simulated_cap_cny: Decimal) -> dict:
    bundle_dir, output = bundle_dir.resolve(), output.resolve()
    if output.is_relative_to(bundle_dir):
        raise ValueError("output cannot modify public input")
    settings = _settings(interaction_prompt, final_prompt, model, policy,
                         simulated_price, simulated_cap_cny)
    bundle = load_bundle(bundle_dir)
    result = asyncio.run(_execute(bundle, script, settings))
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "settings.json", settings)
    write_json(output / "script.json", script.model_dump())
    _write_result(output, result)
    manifest = _manifest(bundle, settings, script, result, _artifacts(output))
    write_json(output / "run.json", manifest)
    return manifest


def verify_run(bundle_dir: Path, output: Path) -> dict:
    bundle = load_bundle(bundle_dir)
    settings = parse_json((output / "settings.json").read_text(encoding="utf-8"))
    script = Script.model_validate(parse_json((output / "script.json").read_text(encoding="utf-8")))
    checked = _settings(settings["interaction_prompt"], settings["final_prompt"],
                        ModelConfig.model_validate(settings["model"]),
                        LookupPolicy.model_validate(settings["lookup_policy"]),
                        _price(settings["simulated_price"]),
                        Decimal(settings["simulated_local_cap_cny"]))
    if checked != settings:
        raise ValueError("trajectory settings mismatch")
    result = asyncio.run(_execute(bundle, script, settings))
    artifacts = _artifacts(output)
    saved_manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    if artifacts != saved_manifest.get("artifacts"):
        raise ValueError("trajectory artifact hash mismatch")
    saved = {"status": parse_json((output / "outcome.json").read_text(encoding="utf-8"))["status"],
             "failure": parse_json((output / "outcome.json").read_text(encoding="utf-8"))["failure"],
             "script_steps_consumed": parse_json((output / "outcome.json").read_text(encoding="utf-8"))["script_steps_consumed"]}
    for key, name in (("calls", "calls.json"), ("steps", "steps.json"),
                      ("state", "state.json"), ("task_context", "task_context.json"),
                      ("lookup_history", "lookup_history.json"), ("ledger", "ledger.json")):
        saved[key] = parse_json((output / name).read_text(encoding="utf-8"))
    saved["final_request"] = (parse_json((output / "final_request.json").read_text(encoding="utf-8"))
                              if (output / "final_request.json").exists() else None)
    saved["final_result"] = (parse_json((output / "final_result.json").read_text(encoding="utf-8"))
                             if (output / "final_result.json").exists() else None)
    if _normalized(saved) != _normalized(result):
        raise ValueError("trajectory replay mismatch")
    expected = _manifest(bundle, settings, script, result, artifacts)
    if saved_manifest != expected:
        raise ValueError("trajectory manifest mismatch")
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3p scripted offline trajectory; no network")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--script", type=Path)
    parser.add_argument("--interaction-prompt", type=Path)
    parser.add_argument("--final-prompt", type=Path)
    parser.add_argument("--simulated-price", type=Path)
    parser.add_argument("--simulated-cap-cny", type=Decimal)
    args = parser.parse_args(argv)
    if args.command == "run":
        if any(value is None for value in (args.script, args.interaction_prompt,
                                            args.final_prompt, args.simulated_price,
                                            args.simulated_cap_cny)):
            parser.error("run requires script, both prompts, simulated price and cap")
        result = run_offline(args.bundle, load_script(args.script), args.output,
            args.interaction_prompt.read_text(encoding="utf-8"),
            args.final_prompt.read_text(encoding="utf-8"),
            ModelConfig(model_id="deepseek-flash"),
            LookupPolicy(max_requests=2, max_events_per_request=2),
            _price(parse_json(args.simulated_price.read_text(encoding="utf-8"))),
            args.simulated_cap_cny)
    else:
        result = verify_run(args.bundle, args.output)
    print(json.dumps({"status": result["status"],
                      "simulated_provider_attempts": result["simulated_provider_attempts"],
                      "actual_model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
