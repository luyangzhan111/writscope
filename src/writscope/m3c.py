"""Budget-gated real incremental extraction pilot, separate from M3b scripts.

The pilot records model extraction and structural scope assessment. It does not
score against private labels or compare methods during the paid run.
"""

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import time

from pydantic import ValidationError

from .artifacts import canonical_hash, digest, write_json
from .budget import PriceCeiling
from .contracts import load_bundle, parse_json
from .m3b import (_artifacts, _code_provenance, _proposal, _write_jsonl,
                  build_request)
from .preflight import CASES
from .providers import ModelConfig, ProviderError
from .providers.deepseek import DeepSeekProvider, MODEL_ID, wire_bytes
from .state import (AuthorizationState, ScopeAssessment, StateEngine,
                    TransitionLogEntry, assess_impacts)


PRICE_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
PRICE = PriceCeiling(input_cny_per_million=Decimal("2"),
                     output_cny_per_million=Decimal("8"),
                     source_url=PRICE_URL, checked_at_utc="2026-10-21")
MODEL = ModelConfig(model_id=MODEL_ID, temperature=0.0, max_output_tokens=4096)
MAX_ATTEMPTS = 15
MAX_WIRE_BYTES = 100_000
MAX_RESPONSE_BYTES = 100_000
TIMEOUT_SECONDS = 90


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote(wire_size: int) -> Decimal:
    if wire_size > MAX_WIRE_BYTES:
        raise ValueError("wire request exceeds frozen limit")
    return ((Decimal(wire_size + PRICE.framing_token_allowance) * PRICE.input_cny_per_million
             + Decimal(MODEL.max_output_tokens) * PRICE.output_cny_per_million)
            / Decimal(1_000_000))


def _input_root(root: Path) -> Path:
    return root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact"


def prepare(root: Path, max_cny: Decimal) -> dict:
    """Read only public bundles; no credential or network access."""
    if not max_cny.is_finite() or max_cny <= 0:
        raise ValueError("positive finite new authorization cap required")
    prompt = (root / "prompts" / "incremental_extract.md").read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValueError("empty extraction prompt")
    cases = {}
    for case in CASES:
        bundle = load_bundle(_input_root(root) / case)
        if bundle.review_input.case_id != case or len(bundle.review_input.events) != 5:
            raise ValueError("unexpected pilot case or event count")
        engine = StateEngine(case, bundle.review_input.events, "0" * 64)
        first = build_request(bundle, 0, engine.snapshot(), prompt, MODEL)
        serialized = first.model_dump_json()
        if "EVALUATOR_ONLY_M1_REFERENCE" in serialized or "evaluation_private" in serialized:
            raise ValueError("private material in extraction request")
        from .baselines import build_request as baseline_request
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
    worst_case = quote(MAX_WIRE_BYTES) * MAX_ATTEMPTS
    if max_cny < worst_case:
        raise ValueError(f"budget below full-run reservation floor {worst_case} CNY")
    return {"schema_version": "0.1-draft", "execution_kind": "preflight_no_model_call",
            "stage": "m3c_real_incremental", "created_at_utc": now(),
            "provider": "deepseek", "model": MODEL.model_dump(),
            "prompt_sha256": digest(prompt.encode("utf-8")),
            "output_contract": "transition_quote_v1", "cases": cases,
            "planned_attempts": MAX_ATTEMPTS, "attempts_per_event": 1,
            "concurrency": 1, "timeout_seconds": TIMEOUT_SECONDS,
            "max_wire_bytes": MAX_WIRE_BYTES,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "price_ceiling": PRICE.model_dump(mode="json"),
            "authorized_max_cny": str(max_cny),
            "worst_case_reservation_cny": str(worst_case),
            "first_request_reservation_cny": {case: str(quote(item["first_wire_bytes"]))
                                              for case, item in cases.items()},
            "reservation_note": "local estimate, not a provider-side billing limit",
            **_code_provenance()}


def _check_preflight(root: Path, preflight: dict) -> tuple[dict, str]:
    cap = Decimal(preflight["authorized_max_cny"])
    current = prepare(root, cap)
    # Timestamps are informational; everything that determines paid requests is fixed.
    for key in current.keys() - {"created_at_utc"}:
        if current[key] != preflight.get(key):
            raise ValueError("inputs, code, price or policy changed after preflight: " + key)
    return current, (root / "prompts" / "incremental_extract.md").read_text(encoding="utf-8")


async def run(root: Path, output: Path, preflight: dict, provider) -> dict:
    """At most 15 paid attempts; stop the entire pilot at the first failure."""
    _, prompt = _check_preflight(root, preflight)
    if output.resolve().is_relative_to(_input_root(root).resolve()):
        raise ValueError("output cannot modify public input bundle")
    cap = Decimal(preflight["authorized_max_cny"])
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "preflight.json", preflight)
    (output / "prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
    manifest = {"schema_version": "0.1-draft", "execution_kind": "real_model_incremental",
                "status": "running", "started_at_utc": now(), "cases": {},
                "attempts": 0, "reserved_estimated_cny": "0",
                "provider_billed_cny": None,
                "billing_status": "provider_bill_not_available",
                "price_ceiling": PRICE.model_dump(mode="json"),
                "authorized_max_cny": str(cap), "preflight_sha256": digest((output / "preflight.json").read_bytes())}
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
        engine_hash = canonical_hash({"prompt_sha256": preflight["prompt_sha256"],
                                      "model": MODEL.model_dump(),
                                      "output_contract": "transition_quote_v1"})
        engine = StateEngine(case, bundle.review_input.events, engine_hash,
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
                cost_quote = quote(len(encoded))
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
            manifest["reserved_estimated_cny"] = str(reserved)
            case_record["attempted_events"] += 1
            call = {"event_id": event.event_id, "started_at_utc": now(),
                    "request_sha256": digest((step / "request.json").read_bytes()),
                    "wire_sha256": digest(encoded), "wire_bytes": len(encoded),
                    "reserved_estimated_cny": str(cost_quote), "actual_usage": None,
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
                call["actual_usage"] = (response.actual_usage.model_dump(mode="json")
                                        if response.actual_usage is not None else None)
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
            usage.append(parse_json((output / case / ref).read_text(encoding="utf-8")).get("actual_usage"))
    manifest["usage"] = {field: {"known_subtotal": str(sum((Decimal(str(item[field]))
                              for item in usage if item is not None and item.get(field) is not None),
                              Decimal("0"))),
                              "unknown_attempts": sum(item is None or item.get(field) is None for item in usage)}
                         for field in ("input_tokens", "output_tokens", "estimated_cost_cny",
                                       "billed_cost_cny")}
    manifest["artifacts"] = _artifacts(output)
    manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
    write_json(output / "run.json", manifest)
    return manifest


def verify_run(root: Path, output: Path) -> dict:
    """Rebuild saved requests and accepted transitions; never call the provider."""
    manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    preflight = parse_json((output / "preflight.json").read_text(encoding="utf-8"))
    if (manifest.get("execution_kind") != "real_model_incremental" or
            manifest.get("status") not in {"completed", "stopped_on_failure"} or
            manifest.get("preflight_sha256") != digest((output / "preflight.json").read_bytes())):
        raise ValueError("real incremental run identity mismatch")
    _, prompt = _check_preflight(root, preflight)
    artifacts = _artifacts(output)
    if artifacts != manifest.get("artifacts") or canonical_hash(artifacts) != manifest.get("artifact_set_sha256"):
        raise ValueError("real incremental artifact hash mismatch")
    if (output / "prompt.md").read_text(encoding="utf-8") != prompt:
        raise ValueError("saved extraction prompt mismatch")
    config_hash = canonical_hash({"prompt_sha256": preflight["prompt_sha256"],
                                  "model": MODEL.model_dump(),
                                  "output_contract": "transition_quote_v1"})
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
        engine = StateEngine(case, bundle.review_input.events, config_hash,
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
            if (call["event_id"] != event.event_id or
                    call["request_sha256"] != digest((step / "request.json").read_bytes()) or
                    call["wire_sha256"] != digest(wire_bytes(request)) or
                    call["wire_bytes"] != len(wire_bytes(request)) or
                    Decimal(call["reserved_estimated_cny"]) != quote(call["wire_bytes"])):
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
            or reserved > Decimal(manifest["authorized_max_cny"]) or
            (manifest["status"] == "completed") != all_completed):
        raise ValueError("real incremental run totals mismatch")
    return manifest


def score_run(root: Path, output: Path, labels_path: Path, score_output: Path) -> dict:
    """Score the saved development pilot only after replay, in a separate directory."""
    manifest = verify_run(root, output)
    labels = parse_json(labels_path.read_text(encoding="utf-8"))
    if labels.get("kind") != "reference_labels_not_predictions":
        raise ValueError("unexpected reference label file")
    by_case = {item["case_id"]: item for item in labels["labels"]}
    if set(by_case) != set(CASES):
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
    summary = {"schema_version": "0.1-draft", "execution_kind": "real_model_incremental_dev_pilot_score",
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
    parser = argparse.ArgumentParser(description="M3c budget-gated real incremental pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="offline checks only; no key or network")
    pre.add_argument("--root", type=Path, default=Path.cwd())
    pre.add_argument("--max-cny", type=Decimal, required=True)
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
        result = prepare(args.root.resolve(), args.max_cny)
        args.output.mkdir(parents=True, exist_ok=False)
        write_json(args.output / "preflight.json", result)
        print(f"M3c offline preflight: {result['planned_attempts']} calls; "
              f"worst-case reserved {result['worst_case_reservation_cny']} CNY")
        return
    if args.command == "verify":
        result = verify_run(args.root.resolve(), args.run)
        print(f"M3c replay verified: attempts={result['attempts']}")
        return
    if args.command == "score":
        result = score_run(args.root.resolve(), args.run, args.labels, args.output)
        print(f"M3c development pilot scored: {result['valid_cases']}/{result['planned_cases']} valid")
        return
    preflight = parse_json((args.preflight / "preflight.json").read_text(encoding="utf-8"))
    _check_preflight(args.root.resolve(), preflight)
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY unavailable; no model call made")
    result = asyncio.run(run(args.root.resolve(), args.output, preflight,
                             DeepSeekProvider(key, PRICE)))
    print(f"M3c {result['status']}: attempts={result['attempts']} -> {args.output}")


if __name__ == "__main__":
    main()
