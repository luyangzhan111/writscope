"""Versioned c003 real-path candidate with the M3f scope policy.

Offline simulation is available before a new paid authorization. The same
request, budget, stop and replay path can later accept a real provider.
"""

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time

from pydantic import ValidationError

from writscope.artifacts import canonical_hash, digest, write_json
from writscope.budget import PriceCeiling
from writscope.contracts import load_bundle, parse_json
from writscope.m3b import (_artifacts, _code_provenance, _proposal, _write_jsonl,
                           build_request)
from writscope.providers import ModelConfig, ProviderError
from writscope.providers.deepseek import DeepSeekProvider, MODEL_ID, wire_bytes
from writscope.state import (AuthorizationState, ScopeAssessment, TransitionLogEntry,
                             assess_impacts)
from scripts import m3f_scope_policy
from scripts.m3f_scope_policy import POLICY_VERSION, ScopeValidatedStateEngine


MODEL = ModelConfig(model_id=MODEL_ID, temperature=0.0, max_output_tokens=4096)
CASES = ("c003",)
MAX_ATTEMPTS = 5
MAX_WIRE_BYTES = 100_000
MAX_RESPONSE_BYTES = 100_000
TIMEOUT_SECONDS = 90
USAGE_FIELDS = ("input_tokens", "output_tokens", "estimated_cost_cny", "billed_cost_cny")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote(wire_size: int, price: PriceCeiling) -> Decimal:
    if wire_size > MAX_WIRE_BYTES:
        raise ValueError("wire request exceeds frozen limit")
    return ((Decimal(wire_size + price.framing_token_allowance) * price.input_cny_per_million
             + Decimal(MODEL.max_output_tokens) * price.output_cny_per_million)
            / Decimal(1_000_000))


def _input_root(root: Path) -> Path:
    return root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact"


def prepare(root: Path, max_cny: Decimal, price: PriceCeiling,
            authorization_ref: str | None = None) -> dict:
    """Draft or approved preflight; no credential or network access."""
    if not max_cny.is_finite() or max_cny <= 0:
        raise ValueError("positive finite local cap required")
    if authorization_ref is not None and not authorization_ref.strip():
        raise ValueError("empty authorization reference")
    prompt = (root / "prompts" / "incremental_extract_v2.md").read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValueError("empty extraction prompt")
    prompt_sha = digest(prompt.encode("utf-8"))
    policy_sha = digest(Path(m3f_scope_policy.__file__).read_bytes())
    engine_config_hash = _config_hash({"prompt_sha256": prompt_sha,
                                       "policy_version": POLICY_VERSION,
                                       "policy_source_sha256": policy_sha})
    cases = {}
    for case in CASES:
        bundle = load_bundle(_input_root(root) / case)
        if bundle.review_input.case_id != case or len(bundle.review_input.events) != 5:
            raise ValueError("unexpected pilot case or event count")
        engine = ScopeValidatedStateEngine(case, bundle.review_input.events, engine_config_hash)
        first = build_request(bundle, 0, engine.snapshot(), prompt, MODEL)
        serialized = first.model_dump_json()
        if "EVALUATOR_ONLY_M1_REFERENCE" in serialized or "evaluation_private" in serialized:
            raise ValueError("private material in extraction request")
        from writscope.baselines import build_request as baseline_request
        baseline = baseline_request(bundle, "full_checklist", "public evidence check", MODEL)
        if first.evidence_sha256 != baseline.evidence_sha256:
            raise ValueError("incremental and baseline public evidence mismatch")
        cases[case] = {"bundle_sha256": bundle.sha256,
                       "evidence_sha256": first.evidence_sha256,
                       "first_request_sha256": digest(serialized.encode("utf-8")),
                       "first_wire_bytes": len(wire_bytes(first)),
                       "events": len(bundle.review_input.events)}
    if sum(item["events"] for item in cases.values()) != MAX_ATTEMPTS:
        raise ValueError("unexpected pilot attempt count")
    # The reservation floor uses the configured wire ceiling for every attempt.
    # It is deliberately higher than the quotes for the known first requests.
    worst_case = quote(MAX_WIRE_BYTES, price) * MAX_ATTEMPTS
    if max_cny < worst_case:
        raise ValueError(f"budget below full-run reservation floor {worst_case} CNY")
    return {"schema_version": "0.1-draft", "execution_kind": "preflight_no_model_call",
            "stage": "m3h_c003_scope_v2_real_path", "created_at_utc": now(),
            "provider": "deepseek", "model": MODEL.model_dump(),
            "prompt_sha256": prompt_sha,
            "output_contract": "transition_quote_v1", "cases": cases,
            "policy_version": POLICY_VERSION,
            "policy_source_sha256": policy_sha,
            "planned_attempts": MAX_ATTEMPTS, "attempts_per_event": 1,
            "concurrency": 1, "timeout_seconds": TIMEOUT_SECONDS,
            "max_wire_bytes": MAX_WIRE_BYTES,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "price_ceiling": price.model_dump(mode="json"),
            "pricing_evidence_status": "operator_supplied_not_live_checked_by_runner",
            "local_cap_cny": str(max_cny),
            "cap_covers": ["c003_incremental_extract_five_events"],
            "cap_excludes": ["history_lookup", "final_model_review", "formal_comparison"],
            "authorization_ref": authorization_ref,
            "authorization_status": "recorded_for_paid_run" if authorization_ref else "draft_unapproved",
            "worst_case_reservation_cny": str(worst_case),
            "first_request_reservation_cny": {case: str(quote(item["first_wire_bytes"], price))
                                              for case, item in cases.items()},
            "reservation_note": "local estimate, not a provider-side billing limit",
            **_code_provenance(),
            "runner_sha256": digest(Path(__file__).read_bytes())}


def _check_preflight(root: Path, preflight: dict) -> tuple[dict, str]:
    cap = Decimal(preflight["local_cap_cny"])
    price = _price(preflight)
    current = prepare(root, cap, price, preflight["authorization_ref"])
    # Timestamps are informational; everything that determines paid requests is fixed.
    for key in current.keys() - {"created_at_utc"}:
        if current[key] != preflight.get(key):
            raise ValueError("inputs, code, price or policy changed after preflight: " + key)
    return current, (root / "prompts" / "incremental_extract_v2.md").read_text(encoding="utf-8")


def _price(preflight: dict) -> PriceCeiling:
    return PriceCeiling.model_validate_json(json.dumps(preflight["price_ceiling"]))


def _usage_summary(records: list[dict | None]) -> dict:
    return {field: {"known_subtotal": str(sum((Decimal(str(item[field]))
                    for item in records if item is not None and item.get(field) is not None),
                    Decimal("0"))),
                    "unknown_attempts": sum(item is None or item.get(field) is None
                                            for item in records)}
            for field in USAGE_FIELDS}


def _config_hash(preflight: dict) -> str:
    return canonical_hash({"prompt_sha256": preflight["prompt_sha256"],
                           "model": MODEL.model_dump(),
                           "output_contract": "transition_quote_v1",
                           "policy_version": preflight["policy_version"],
                           "policy_source_sha256": preflight["policy_source_sha256"]})


async def run(root: Path, output: Path, preflight: dict, provider, *, mode: str) -> dict:
    """Run one provider through the real-path rules; no retry after failure."""
    _, prompt = _check_preflight(root, preflight)
    if mode not in {"offline_simulation", "real_model"}:
        raise ValueError("unknown execution mode")
    if mode == "real_model" and preflight["authorization_status"] != "recorded_for_paid_run":
        raise ValueError("new paid-run authorization reference required")
    if mode == "offline_simulation" and isinstance(provider, DeepSeekProvider):
        raise ValueError("real provider cannot be recorded as offline simulation")
    if mode == "real_model" and not isinstance(provider, DeepSeekProvider):
        raise ValueError("real mode requires the frozen DeepSeek provider")
    if output.resolve().is_relative_to(_input_root(root).resolve()):
        raise ValueError("output cannot modify public input bundle")
    cap = Decimal(preflight["local_cap_cny"])
    price = _price(preflight)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "preflight.json", preflight)
    (output / "prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
    manifest = {"schema_version": "0.1-draft", "execution_kind": (
                    "real_model_incremental_c003_scope_v2" if mode == "real_model"
                    else "offline_simulated_c003_scope_v2"),
                "status": "running", "started_at_utc": now(), "cases": {},
                "attempts": 0, "reserved_estimated_cny": "0",
                "reservation_kind": ("local_estimate" if mode == "real_model"
                                     else "hypothetical_offline"),
                "actual_model_calls": 0, "simulated_provider_attempts": 0,
                "usage_kind": "provider_response" if mode == "real_model" else "synthetic_mock",
                "provider_billed_cny": None,
                "billing_status": ("provider_bill_not_available" if mode == "real_model"
                                   else "not_applicable_offline_simulation"),
                "price_ceiling": price.model_dump(mode="json"),
                "local_cap_cny": str(cap), "preflight_sha256": digest((output / "preflight.json").read_bytes())}
    write_json(output / "run.json", manifest)
    reserved = Decimal("0")
    start = time.perf_counter()
    halt = False
    for case in CASES:
        if halt:
            break
        bundle = load_bundle(_input_root(root) / case)
        case_dir = output / case
        case_dir.mkdir()
        engine_hash = _config_hash(preflight)
        engine = ScopeValidatedStateEngine(case, bundle.review_input.events, engine_hash,
                             set(bundle.registry.artifacts))
        case_record = {"status": "running", "attempted_events": 0, "calls": [],
                       "public_bundle_sha256": bundle.sha256,
                       "evidence_sha256": preflight["cases"][case]["evidence_sha256"]}
        manifest["cases"][case] = case_record
        write_json(output / "run.json", manifest)
        for index, event in enumerate(bundle.review_input.events):
            request = build_request(bundle, index, engine.snapshot(), prompt, MODEL)
            if request.evidence_sha256 != case_record["evidence_sha256"]:
                case_record["status"] = "input_changed"
                halt = True
                break
            encoded = wire_bytes(request)
            try:
                cost_quote = quote(len(encoded), price)
                if manifest["attempts"] >= MAX_ATTEMPTS or reserved + cost_quote > cap:
                    raise ValueError("local attempt or estimated budget exhausted")
            except ValueError as exc:
                case_record["status"] = "budget_exhausted"
                case_record["error"] = str(exc)
                halt = True
                break
            step = case_dir / f"step_{event.seq:03d}"
            step.mkdir()
            write_json(step / "request.json", request.model_dump())
            # A timeout or missing usage never releases this reservation.
            reserved += cost_quote
            manifest["attempts"] += 1
            if mode == "real_model":
                manifest["actual_model_calls"] += 1
            else:
                manifest["simulated_provider_attempts"] += 1
            manifest["reserved_estimated_cny"] = str(reserved)
            case_record["attempted_events"] += 1
            call = {"event_id": event.event_id, "started_at_utc": now(),
                    "request_sha256": digest((step / "request.json").read_bytes()),
                    "wire_sha256": digest(encoded), "wire_bytes": len(encoded),
                    "reserved_estimated_cny": str(cost_quote), "actual_usage": None,
                    "synthetic_usage": None,
                    "response_sha256": None, "provider_response_id": None,
                    "served_model": None, "system_fingerprint": None,
                    "finish_reason": None, "status": "running", "error": None}
            write_json(step / "call.json", call)
            write_json(output / "run.json", manifest)
            call_start = time.perf_counter()
            try:
                response = await asyncio.wait_for(provider.generate(request.model_copy(deep=True)),
                                                  TIMEOUT_SECONDS)
                raw = response.text
                (step / "response.txt").write_text(raw, encoding="utf-8", newline="\n")
                call["response_sha256"] = digest((step / "response.txt").read_bytes())
                for key in ("provider_response_id", "served_model", "system_fingerprint", "finish_reason"):
                    call[key] = getattr(response, key)
                usage_record = (response.actual_usage.model_dump(mode="json")
                                if response.actual_usage is not None else None)
                call["actual_usage" if mode == "real_model" else "synthetic_usage"] = usage_record
                if (response.actual_usage is None or response.actual_usage.input_tokens is None
                        or response.actual_usage.output_tokens is None):
                    raise ValueError("provider usage missing")
                if response.finish_reason != "stop":
                    raise ValueError("provider finish reason is not stop")
                if len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
                    raise ValueError("response exceeds limit")
                proposal = _proposal(raw, bundle, index, engine_hash, f"{case}_extract_{event.seq:03d}")
                entry = engine.submit(proposal)
                if not entry.transition.accepted:
                    raise ValueError("state transition rejected: " + str(entry.transition.rejection_reason))
                call["status"] = "succeeded"
            except TimeoutError:
                call["status"], call["error"] = "timeout", "provider deadline exceeded"
            except ProviderError as exc:
                call["status"], call["error"] = "provider_error", str(exc)
            except (ValueError, TypeError, ValidationError) as exc:
                call["status"], call["error"] = "invalid_output", str(exc)
            except Exception as exc:
                call["status"], call["error"] = "provider_error", type(exc).__name__
            call["ended_at_utc"] = now()
            call["elapsed_ms"] = round((time.perf_counter() - call_start) * 1000, 3)
            write_json(step / "call.json", call)
            case_record["calls"].append(f"step_{event.seq:03d}/call.json")
            write_json(output / "run.json", manifest)
            if call["status"] != "succeeded":
                case_record["status"] = "incomplete"
                case_record["error"] = call["error"]
                halt = True
                break
        state = engine.snapshot()
        _write_jsonl(case_dir / "transitions.jsonl", engine.records)
        write_json(case_dir / "state.json", state.model_dump())
        if not halt and state.through_seq == len(bundle.review_input.events):
            assessment = assess_impacts(state, bundle.evidence,
                                        bundle.review_input.candidate_patch.sha256)
            write_json(case_dir / "assessment.json", assessment.model_dump())
            case_record["status"] = "completed"
        manifest["cases"][case] = case_record
        write_json(output / "run.json", manifest)
    manifest["status"] = "completed" if not halt else "stopped_on_failure"
    manifest["ended_at_utc"] = now()
    manifest["wall_elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
    usage = []
    for case in manifest["cases"]:
        for ref in manifest["cases"][case]["calls"]:
            usage.append(parse_json((output / case / ref).read_text(encoding="utf-8")).get(
                "actual_usage" if mode == "real_model" else "synthetic_usage"))
    manifest["usage"] = _usage_summary(usage)
    if mode == "offline_simulation":
        manifest["synthetic_usage"] = manifest.pop("usage")
        manifest["usage"] = {field: {"known_subtotal": None,
                                     "unknown_attempts": manifest["attempts"]}
                             for field in USAGE_FIELDS}
    manifest["artifacts"] = _artifacts(output)
    manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
    write_json(output / "run.json", manifest)
    return manifest


def verify_run(root: Path, output: Path) -> dict:
    """Rebuild saved requests and accepted transitions; never call the provider."""
    manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    preflight = parse_json((output / "preflight.json").read_text(encoding="utf-8"))
    if (manifest.get("execution_kind") not in {"real_model_incremental_c003_scope_v2",
                                                 "offline_simulated_c003_scope_v2"} or
            manifest.get("status") not in {"completed", "stopped_on_failure"} or
            manifest.get("preflight_sha256") != digest((output / "preflight.json").read_bytes())):
        raise ValueError("M3h run identity mismatch")
    _, prompt = _check_preflight(root, preflight)
    mode = "real_model" if manifest["execution_kind"].startswith("real_model_") else "offline_simulation"
    if mode == "real_model" and preflight["authorization_status"] != "recorded_for_paid_run":
        raise ValueError("real run missing authorization reference")
    if (set(manifest["cases"]) != set(CASES) or
            manifest["price_ceiling"] != preflight["price_ceiling"] or
            manifest["local_cap_cny"] != preflight["local_cap_cny"] or
            manifest["attempts"] > MAX_ATTEMPTS):
        raise ValueError("M3h budget or case identity mismatch")
    if (manifest["actual_model_calls"] != (manifest["attempts"] if mode == "real_model" else 0)
            or manifest["simulated_provider_attempts"] != (
                manifest["attempts"] if mode == "offline_simulation" else 0)
            or manifest["usage_kind"] != ("provider_response" if mode == "real_model" else "synthetic_mock")
            or manifest["reservation_kind"] != ("local_estimate" if mode == "real_model"
                                                 else "hypothetical_offline")
            or manifest["billing_status"] != ("provider_bill_not_available" if mode == "real_model"
                                              else "not_applicable_offline_simulation")):
        raise ValueError("M3h execution accounting mismatch")
    artifacts = _artifacts(output)
    if artifacts != manifest.get("artifacts") or canonical_hash(artifacts) != manifest.get("artifact_set_sha256"):
        raise ValueError("real incremental artifact hash mismatch")
    if (output / "prompt.md").read_text(encoding="utf-8") != prompt:
        raise ValueError("saved extraction prompt mismatch")
    config_hash = _config_hash(preflight)
    price = _price(preflight)
    attempts = 0
    reserved = Decimal("0")
    all_completed = True
    for case in CASES:
        if case not in manifest["cases"]:
            all_completed = False
            continue
        bundle = load_bundle(_input_root(root) / case)
        case_dir = output / case
        record = manifest["cases"][case]
        if record["public_bundle_sha256"] != bundle.sha256:
            raise ValueError("real incremental bundle mismatch")
        engine = ScopeValidatedStateEngine(case, bundle.review_input.events, config_hash,
                             set(bundle.registry.artifacts))
        if record["attempted_events"] != len(record["calls"]):
            raise ValueError("real incremental call count mismatch")
        for index, ref in enumerate(record["calls"]):
            event = bundle.review_input.events[index]
            step = case_dir / f"step_{event.seq:03d}"
            if ref != f"step_{event.seq:03d}/call.json":
                raise ValueError("real incremental call order mismatch")
            request = build_request(bundle, index, engine.snapshot(), prompt, MODEL)
            saved_request = parse_json((step / "request.json").read_text(encoding="utf-8"))
            if request.model_dump(mode="json") != saved_request:
                raise ValueError("real incremental request replay mismatch")
            call = parse_json((step / "call.json").read_text(encoding="utf-8"))
            if (mode == "offline_simulation" and call["actual_usage"] is not None) or (
                    mode == "real_model" and call["synthetic_usage"] is not None):
                raise ValueError("M3h usage provenance mismatch")
            if (call["event_id"] != event.event_id or
                    call["request_sha256"] != digest((step / "request.json").read_bytes()) or
                    call["wire_sha256"] != digest(wire_bytes(request)) or
                    call["wire_bytes"] != len(wire_bytes(request)) or
                    Decimal(call["reserved_estimated_cny"]) != quote(call["wire_bytes"], price)):
                raise ValueError("real incremental call provenance mismatch")
            attempts += 1
            reserved += Decimal(call["reserved_estimated_cny"])
            if call["response_sha256"] is not None:
                raw = (step / "response.txt").read_text(encoding="utf-8")
                if call["response_sha256"] != digest((step / "response.txt").read_bytes()):
                    raise ValueError("real incremental response hash mismatch")
                if call["status"] == "succeeded" or (
                        call["status"] == "invalid_output" and
                        call["error"] and call["error"].startswith("state transition rejected:")):
                    entry = engine.submit(_proposal(raw, bundle, index, config_hash,
                                                     f"{case}_extract_{event.seq:03d}"))
                    if (call["status"] == "succeeded") != entry.transition.accepted:
                        raise ValueError("real incremental transition/status mismatch")
            elif call["status"] == "succeeded":
                raise ValueError("successful extraction missing response")
            if call["status"] != "succeeded" and index != record["attempted_events"] - 1:
                raise ValueError("pilot continued after a failed call")
        saved_state = AuthorizationState.model_validate(parse_json(
            (case_dir / "state.json").read_text(encoding="utf-8")))
        saved_entries = [TransitionLogEntry.model_validate(parse_json(line)) for line in
                         (case_dir / "transitions.jsonl").read_text(encoding="utf-8").splitlines()]
        if (saved_state != engine.snapshot() or len(saved_entries) != len(engine.records) or
                any(left.proposal != right.proposal or left.transition != right.transition
                    for left, right in zip(saved_entries, engine.records))):
            raise ValueError("real incremental state replay mismatch")
        complete = (record["status"] == "completed" and
                    record["attempted_events"] == len(bundle.review_input.events))
        if complete:
            expected = assess_impacts(saved_state, bundle.evidence,
                                      bundle.review_input.candidate_patch.sha256)
            actual = ScopeAssessment.model_validate(parse_json(
                (case_dir / "assessment.json").read_text(encoding="utf-8")))
            if actual != expected:
                raise ValueError("real incremental assessment replay mismatch")
        else:
            all_completed = False
            if (case_dir / "assessment.json").exists():
                raise ValueError("incomplete real extraction has assessment")
    if (attempts != manifest["attempts"] or reserved != Decimal(manifest["reserved_estimated_cny"])
            or reserved > Decimal(manifest["local_cap_cny"]) or
            (manifest["status"] == "completed") != all_completed):
        raise ValueError("real incremental run totals mismatch")
    saved_usage = [parse_json((output / case / ref).read_text(encoding="utf-8"))[
        "actual_usage" if mode == "real_model" else "synthetic_usage"]
        for case in CASES for ref in manifest["cases"][case]["calls"]]
    if mode == "real_model":
        valid_usage = (manifest["usage"] == _usage_summary(saved_usage) and
                       "synthetic_usage" not in manifest)
    else:
        valid_usage = (manifest["synthetic_usage"] == _usage_summary(saved_usage) and
                       manifest["usage"] == {field: {"known_subtotal": None,
                                                     "unknown_attempts": attempts}
                                             for field in USAGE_FIELDS})
    if not valid_usage or manifest["provider_billed_cny"] is not None:
        raise ValueError("M3h usage summary mismatch")
    return manifest


def score_run(root: Path, output: Path, labels_path: Path, score_output: Path) -> dict:
    """Score only a separately authorized real run; structural result only."""
    manifest = verify_run(root, output)
    if manifest["execution_kind"] != "real_model_incremental_c003_scope_v2":
        raise ValueError("offline simulation cannot be scored as a model result")
    labels = parse_json(labels_path.read_text(encoding="utf-8"))
    if labels.get("kind") != "reference_labels_not_predictions":
        raise ValueError("unexpected reference label file")
    by_case = {item["case_id"]: item for item in labels["labels"]}
    if not set(CASES).issubset(by_case):
        raise ValueError("reference cases do not match pilot plan")
    rows = []
    for case in CASES:
        record = manifest["cases"].get(case)
        valid = record is not None and record["status"] == "completed"
        assessment = parse_json((output / case / "assessment.json").read_text(encoding="utf-8")) if valid else None
        reference = by_case[case]
        rows.append({"case_id": case, "status": "valid_structural_assessment" if valid else "no_valid_output",
                     "observed": {key: assessment[key] if assessment else None
                                  for key in ("impact", "authorization", "decision")},
                     "expected": {key: reference["expected_" + key]
                                  for key in ("impact", "authorization", "decision")},
                     "correct": {key: bool(assessment and assessment[key] == reference["expected_" + key])
                                 for key in ("impact", "authorization", "decision")}})
    summary = {"schema_version": "0.1-draft", "execution_kind": "real_model_incremental_c003_scope_v2_dev_score",
               "source_run_artifact_set_sha256": manifest["artifact_set_sha256"],
               "reference_labels_sha256": digest(labels_path.read_bytes()),
               "planned_cases": len(CASES), "valid_cases": sum(row["status"] == "valid_structural_assessment" for row in rows),
               "axis_correct": {key: sum(row["correct"][key] for row in rows)
                                for key in ("impact", "authorization", "decision")},
               "rows": rows, "comparison_status": "no_method_comparison",
               "semantic_extraction_audit": "not_performed"}
    score_output.mkdir(parents=True, exist_ok=False)
    write_json(score_output / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3h c003 scope-v2 real-path candidate")
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="offline checks only; no key or network")
    pre.add_argument("--root", type=Path, default=Path.cwd())
    pre.add_argument("--max-cny", type=Decimal, required=True)
    pre.add_argument("--price-json", type=Path, required=True,
                     help="locally reviewed PriceCeiling JSON; no online lookup")
    pre.add_argument("--authorization-ref", default=None,
                     help="only after independent user approval of this cap")
    pre.add_argument("--output", type=Path, required=True)
    paid = sub.add_parser("run", help="requires an independently authorized new cap")
    paid.add_argument("--root", type=Path, default=Path.cwd())
    paid.add_argument("--preflight", type=Path, required=True)
    paid.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify", help="replay saved requests and state without a key")
    verify.add_argument("--root", type=Path, default=Path.cwd())
    verify.add_argument("--run", type=Path, required=True)
    score = sub.add_parser("score", help="independently score saved development pilot")
    score.add_argument("--root", type=Path, default=Path.cwd())
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        price = PriceCeiling.model_validate_json(args.price_json.read_text(encoding="utf-8"))
        result = prepare(args.root.resolve(), args.max_cny, price, args.authorization_ref)
        args.output.mkdir(parents=True, exist_ok=False)
        write_json(args.output / "preflight.json", result)
        print(f"M3h offline preflight ({result['authorization_status']}): "
              f"{result['planned_attempts']} calls; worst-case reserved "
              f"{result['worst_case_reservation_cny']} CNY")
        return
    if args.command == "verify":
        result = verify_run(args.root.resolve(), args.run)
        print(f"M3h replay verified: attempts={result['attempts']}")
        return
    if args.command == "score":
        result = score_run(args.root.resolve(), args.run, args.labels, args.output)
        print(f"M3h development diagnostic scored: {result['valid_cases']}/{result['planned_cases']} valid")
        return
    preflight = parse_json((args.preflight / "preflight.json").read_text(encoding="utf-8"))
    _check_preflight(args.root.resolve(), preflight)
    if preflight["authorization_status"] != "recorded_for_paid_run":
        raise SystemExit("new paid-run authorization absent; no model call made")
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY unavailable; no model call made")
    result = asyncio.run(run(args.root.resolve(), args.output, preflight,
                             DeepSeekProvider(key, _price(preflight)), mode="real_model"))
    print(f"M3h {result['status']}: attempts={result['attempts']} -> {args.output}")


if __name__ == "__main__":
    main()
