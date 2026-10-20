"""Review execution; labels and scoring are deliberately absent."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import time
from typing import Callable, Literal
import uuid

from pydantic import Field

from .artifacts import canonical_hash, digest, write_json
from .baselines import METHODS, build_request
from .budget import ReservationLedger
from .contracts import load_bundle, validate_output
from .events import StrictModel
from .providers import ModelConfig, ModelRequest, ProviderError, ScriptExhausted


class RunPolicy(StrictModel):
    execution_kind: Literal["offline_mock", "real_model"] = "offline_mock"
    timeout_seconds: float = Field(default=5.0, gt=0, le=180, allow_inf_nan=False)
    max_attempts_per_job: int = Field(default=2, ge=1, le=5)
    max_total_attempts: int = Field(default=12, ge=0, le=100)
    max_request_bytes: int = Field(default=1_000_000, ge=1)
    max_response_bytes: int = Field(default=100_000, ge=1)


def now():
    return datetime.now(timezone.utc).isoformat()


def summarize_usage(attempts, execution_kind="offline_mock"):
    synthetic = {}
    for field in ("input_tokens", "output_tokens", "cost_usd"):
        values = [(a.get("synthetic_usage") or {}).get(field) for a in attempts]
        known = [value for value in values if value is not None]
        synthetic[field] = {"known_subtotal": sum(known), "unknown_attempts": len(values) - len(known),
                            "complete_total": sum(known) if len(known) == len(values) else None}
    result = {"actual_model_calls": len(attempts) if execution_kind == "real_model" else 0,
              "provider_attempts": len(attempts),
              "provider_elapsed_ms": round(sum(a["elapsed_ms"] for a in attempts), 3)}
    if execution_kind == "offline_mock":
        result.update(actual_model_tokens=0, actual_model_cost_usd=0, synthetic_test_usage=synthetic)
    else:
        actual = {}
        for field in ("input_tokens", "output_tokens", "cache_hit_input_tokens", "cache_miss_input_tokens",
                      "reasoning_tokens", "estimated_cost_cny", "billed_cost_cny"):
            values = [(a.get("actual_usage") or {}).get(field) for a in attempts]
            known = [value for value in values if value is not None]
            subtotal = sum((Decimal(str(value)) for value in known), Decimal("0"))
            actual[field] = {"known_subtotal": str(subtotal), "unknown_attempts": len(values) - len(known),
                             "complete_total": str(subtotal) if len(values) == len(known) else None}
        result["provider_reported_usage_and_estimates"] = actual
        result["billing_status"] = "not_provider_billed_amount"
    return result


async def run_suite(input_dirs: list[Path], output: Path, prompts: dict[str, str], provider_factory,
                    *, policy: RunPolicy | None = None, model: ModelConfig | None = None,
                    provenance: dict | None = None, ledger: ReservationLedger | None = None,
                    wire_size: Callable[[ModelRequest], int] | None = None,
                    expected_request_hashes: list[str] | None = None):
    policy, model = policy or RunPolicy(), model or ModelConfig()
    if policy.execution_kind == "real_model" and (ledger is None or wire_size is None or expected_request_hashes is None):
        raise ValueError("real model run requires preflight hashes and budget reservation")
    if policy.execution_kind == "offline_mock" and ledger is not None:
        raise ValueError("offline mock must not use the real budget ledger")
    if set(prompts) != set(METHODS):
        raise ValueError("both full-context baselines are required")
    plans = [{"job_id": f"job_{index:03d}", "input_index": index // 2,
              "case_id_hint": input_dirs[index // 2].name,
              "method": METHODS[index % 2], "repeat_index": 0} for index in range(len(input_dirs) * 2)]
    if not plans:
        raise ValueError("at least one input bundle is required")
    if expected_request_hashes is not None and len(expected_request_hashes) != len(plans):
        raise ValueError("preflight request count mismatch")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": "0.1-draft", "run_id": ("m2b-" if policy.execution_kind == "real_model" else "m2a-") + uuid.uuid4().hex[:12],
                "execution_kind": policy.execution_kind, "status": "running", "started_at": now(),
                "policy": policy.model_dump(), "model": model.model_dump(), "jobs": plans,
                "provenance": provenance or {}, "artifacts": {}}
    write_json(output / "run.json", manifest)
    start_suite = time.perf_counter()
    total_attempts, all_attempts, evidence_by_input = 0, [], {}
    halt_future_calls = False
    for plan in plans:
        job_dir = output / plan["job_id"]
        job_dir.mkdir()
        started = time.perf_counter()
        job = {**plan, "execution_kind": policy.execution_kind, "case_id": None, "status": "running",
               "started_at": now(), "attempt_refs": [], "result_ref": None, "request_ref": None,
               "error": None, "evidence_sha256": None}
        bundle = None
        try:
            bundle = load_bundle(input_dirs[plan["input_index"]])
            job["case_id"] = bundle.review_input.case_id
            request = build_request(bundle, plan["method"], prompts[plan["method"]], model)
            serialized = request.model_dump_json()
            if expected_request_hashes is not None and digest(serialized.encode("utf-8")) != expected_request_hashes[int(plan["job_id"][4:])]:
                halt_future_calls = True
                raise ValueError("input changed after paid-call preflight")
            if len(serialized.encode("utf-8")) > policy.max_request_bytes:
                raise ValueError("full input exceeds configured request limit; never truncated")
            evidence_hash = evidence_by_input.setdefault(plan["input_index"], request.evidence_sha256)
            if evidence_hash != request.evidence_sha256:
                raise ValueError("baseline evidence changed between methods")
            job["evidence_sha256"] = request.evidence_sha256
            write_json(job_dir / "request.json", request.model_dump())
            job["request_ref"] = "request.json"
        except Exception as error:
            job["status"], job["error"] = "invalid_input", str(error)
            if policy.execution_kind == "real_model":
                halt_future_calls = True

        if job["status"] == "running" and halt_future_calls:
            job["status"], job["error"] = "stopped", "run halted after preflight or usage failure"
        if job["status"] == "running":
            try:
                provider = provider_factory(plan["method"])
            except Exception as error:
                job["status"], job["error"] = "provider_error", str(error)
            else:
                for number in range(1, policy.max_attempts_per_job + 1):
                    if total_attempts >= policy.max_total_attempts:
                        job["status"], job["error"] = "budget_exhausted", "suite provider-attempt limit reached"
                        break
                    reservation = None
                    if policy.execution_kind == "real_model":
                        try:
                            reservation = ledger.reserve(wire_size(request))
                        except ValueError as error:
                            job["status"], job["error"] = "budget_exhausted", str(error)
                            halt_future_calls = True
                            break
                    total_attempts += 1
                    attempt = {"attempt": number, "stage": "review", "started_at": now(),
                               "request_sha256": digest((job_dir / "request.json").read_bytes()),
                               "execution_kind": policy.execution_kind,
                               "usage_source": "synthetic_script" if policy.execution_kind == "offline_mock" else "provider_response_or_unknown",
                               "synthetic_usage": None, "actual_usage": None,
                               "reserved_estimated_cost_cny": str(reservation) if reservation is not None else None,
                               "response_ref": None, "provider_response_id": None,
                               "served_model": None, "system_fingerprint": None, "finish_reason": None,
                               "error": None}
                    call_start = time.perf_counter()
                    raw_response = None
                    retryable = False
                    try:
                        response = await asyncio.wait_for(provider.generate(request.model_copy(deep=True)), policy.timeout_seconds)
                        raw_response = response.text
                        attempt["synthetic_usage"] = response.synthetic_usage.model_dump() if response.synthetic_usage is not None else None
                        attempt["actual_usage"] = response.actual_usage.model_dump(mode="json") if response.actual_usage is not None else None
                        if policy.execution_kind == "real_model":
                            attempt["usage_source"] = response.actual_usage.usage_source if response.actual_usage is not None else "unavailable"
                        for field in ("provider_response_id", "served_model", "system_fingerprint", "finish_reason"):
                            attempt[field] = getattr(response, field)
                        response_path = job_dir / f"response_{number:02d}.txt"
                        response_path.write_text(raw_response, encoding="utf-8", newline="\n")
                        attempt["response_ref"] = response_path.name
                        if len(raw_response.encode("utf-8")) > policy.max_response_bytes:
                            raise ValueError("response exceeds output limit")
                        if policy.execution_kind == "real_model":
                            if response.actual_usage is None or response.actual_usage.input_tokens is None or response.actual_usage.output_tokens is None:
                                halt_future_calls = True
                                raise ValueError("provider usage missing; stop future paid calls")
                            if response.finish_reason != "stop":
                                raise ValueError("provider finish reason is not stop: " + str(response.finish_reason))
                        result = validate_output(raw_response, bundle, plan["method"])
                        write_json(job_dir / "result.json", result.model_dump())
                        job["result_ref"] = "result.json"
                        attempt["status"] = "succeeded"
                    except TimeoutError:
                        attempt["status"], attempt["error"] = "timeout", "provider deadline exceeded"
                        retryable = True
                        if policy.execution_kind == "real_model":
                            halt_future_calls = True
                    except ScriptExhausted as error:
                        attempt["status"], attempt["error"] = "provider_error", str(error)
                        if policy.execution_kind == "real_model":
                            halt_future_calls = True
                    except ProviderError as error:
                        attempt["status"], attempt["error"] = "provider_error", str(error)
                        attempt["synthetic_usage"] = error.synthetic_usage.model_dump() if error.synthetic_usage is not None else None
                        retryable = error.retryable
                        if policy.execution_kind == "real_model":
                            halt_future_calls = True
                    except (ValueError, TypeError) as error:
                        attempt["status"], attempt["error"] = "invalid_output", str(error)
                    except Exception as error:
                        attempt["status"], attempt["error"] = "provider_error", str(error)
                        if policy.execution_kind == "real_model":
                            halt_future_calls = True
                    attempt["ended_at"] = now()
                    attempt["elapsed_ms"] = round((time.perf_counter() - call_start) * 1000, 3)
                    attempt_path = job_dir / f"attempt_{number:02d}.json"
                    write_json(attempt_path, attempt)
                    job["attempt_refs"].append(attempt_path.name)
                    all_attempts.append(attempt)
                    job["status"], job["error"] = attempt["status"], attempt["error"]
                    if attempt["status"] == "succeeded" or not retryable or (policy.execution_kind == "real_model" and halt_future_calls):
                        break
        job["ended_at"] = now()
        job["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        write_json(job_dir / "job.json", job)
        for file in sorted(job_dir.iterdir()):
            manifest["artifacts"][file.relative_to(output).as_posix()] = digest(file.read_bytes())
        write_json(output / "run.json", manifest)
    manifest["status"] = "completed"  # Individual failures are preserved, not erased.
    manifest["ended_at"] = now()
    manifest["elapsed_ms"] = round((time.perf_counter() - start_suite) * 1000, 3)
    manifest["totals"] = summarize_usage(all_attempts, policy.execution_kind)
    if ledger is not None:
        manifest["budget"] = {"currency": "CNY", "authorized_max_cny": str(ledger.policy.max_total_cny),
                              "reserved_estimated_cny": str(ledger.reserved_cny),
                              "reservation_attempts": ledger.attempts,
                              "note": "local conservative estimate; provider billed amount unknown"}
    manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
    write_json(output / "run.json", manifest)
    return manifest
