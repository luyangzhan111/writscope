"""M3r frozen, no-network preflight for a proposed complete c003 pilot."""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Callable

from scripts import (m3i_task_context, m3j_transition_batch, m3k_history_lookup,
                     m3m_final_review, m3n_budget_envelope, m3o_interaction_contract,
                     m3p_scripted_runner)
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from scripts.m3k_history_lookup import LookupPolicy
from scripts.m3n_budget_envelope import AttemptLedger, MAX_PROVIDER_ATTEMPTS
from scripts.m3o_interaction_contract import build_request
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.baselines import build_request as baseline_request
from writscope.budget import PriceCeiling
from writscope.contracts import load_bundle, parse_json
from writscope.m3b import _artifacts, _code_provenance
from writscope.providers import ModelConfig
from writscope.providers.deepseek import wire_bytes


VERSION = "m3r_real_preflight_v1"
CASE = "c003"
MODEL = ModelConfig(model_id="deepseek-flash", temperature=0.0,
                    max_output_tokens=4096)
POLICY = LookupPolicy(max_requests=2, max_events_per_request=2)
CAP_CNY = Decimal("3.00")
USD_PEAK_CACHE_MISS_PER_MILLION = Decimal("0.3")
USD_PEAK_OUTPUT_PER_MILLION = Decimal("1.2")
CONSERVATIVE_CNY_PER_USD = Decimal("10")
PRICE_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
PRICE_CHECKED_AT_UTC = "2026-10-23T18:14:28+00:00"
PRIVATE_MARKERS = ("EVALUATOR_ONLY_M1_REFERENCE", "evaluation_private")
COMPONENTS = (m3i_task_context, m3j_transition_batch, m3k_history_lookup,
              m3m_final_review, m3n_budget_envelope, m3o_interaction_contract,
              m3p_scripted_runner)


def _price() -> PriceCeiling:
    return PriceCeiling(input_cny_per_million=(
        USD_PEAK_CACHE_MISS_PER_MILLION * CONSERVATIVE_CNY_PER_USD),
        output_cny_per_million=(
            USD_PEAK_OUTPUT_PER_MILLION * CONSERVATIVE_CNY_PER_USD),
        source_url=PRICE_URL, checked_at_utc=PRICE_CHECKED_AT_UTC)


def _bundle_dir(root: Path) -> Path:
    return root / "reports/m1-2026-10-18/inputs/verified_impact" / CASE


def _component_hashes() -> dict[str, str]:
    return {Path(module.__file__).name: digest(Path(module.__file__).read_bytes())
            for module in COMPONENTS}


def prepare(root: Path) -> tuple[dict, dict]:
    """Build a reviewable proposal without touching credentials or network."""
    root = root.resolve()
    bundle = load_bundle(_bundle_dir(root))
    if bundle.review_input.case_id != CASE or len(bundle.review_input.events) != 5:
        raise ValueError("unexpected c003 public input")
    interaction = (root / "prompts/incremental_interaction_v1.md").read_text(encoding="utf-8")
    final = (root / "prompts/incremental_final_review_v1.md").read_text(encoding="utf-8")
    if not interaction.strip() or not final.strip():
        raise ValueError("empty frozen prompt")
    config_hash = m3p_scripted_runner._config_hash(interaction, final, MODEL, POLICY)
    state = BatchStateEngine(CASE, bundle.review_input.events, config_hash,
                             set(bundle.registry.artifacts)).snapshot()
    context = TaskContextLedger(bundle.review_input.events,
                                set(bundle.registry.artifacts)).snapshot()
    first = build_request(bundle, 0, state, context, interaction, MODEL, POLICY)
    baseline = baseline_request(bundle, "full_checklist", "public parity check", MODEL)
    if first.evidence_sha256 != baseline.evidence_sha256:
        raise ValueError("full-context baseline evidence mismatch")
    public = json.dumps(first.public_input, ensure_ascii=False)
    if any(marker in public for marker in PRIVATE_MARKERS):
        raise ValueError("private marker in model-facing request")
    ledger = AttemptLedger(_price(), CAP_CNY)
    first_wire = wire_bytes(first)
    if len(first_wire) > m3n_budget_envelope.MAX_WIRE_BYTES:
        raise ValueError("first request exceeds wire limit")
    plan = {
        "schema_version": "0.1-draft", "version": VERSION,
        "execution_kind": "preflight_no_model_call", "status": "draft_unapproved",
        "case_ids": [CASE], "order": [CASE], "development_diagnostic_only": True,
        "provider": "deepseek", "endpoint": "https://api.deepseek.com/chat/completions",
        "model": MODEL.model_dump(), "response_contract": {
            "event": "m3o_interaction_v1", "final": "source_quote_v1"},
        "prompt_sha256": {"interaction": digest(interaction.encode("utf-8")),
                          "final": digest(final.encode("utf-8"))},
        "public_bundle_sha256": bundle.sha256,
        "public_evidence_sha256": first.evidence_sha256,
        "first_request_sha256": canonical_hash(first.model_dump()),
        "first_wire_sha256": digest(first_wire),
        "first_wire_bytes": len(first_wire),
        "first_attempt_reservation_cny": str(ledger.quote(len(first_wire))),
        "policy": {"concurrency": 1, "max_initial_extract_attempts": 5,
                   "max_lookup_continuations": 2, "max_final_review_attempts": 1,
                   "max_provider_attempts": MAX_PROVIDER_ATTEMPTS,
                   "max_lookup_requests": POLICY.max_requests,
                   "max_events_per_lookup": POLICY.max_events_per_request,
                   "max_wire_bytes_per_attempt": m3n_budget_envelope.MAX_WIRE_BYTES,
                   "max_response_bytes_per_attempt": m3p_scripted_runner.MAX_RESPONSE_BYTES,
                   "max_output_tokens": MODEL.max_output_tokens,
                   "timeout_seconds_per_attempt": m3n_budget_envelope.TIMEOUT_SECONDS,
                   "retries": 0, "stop": "first_provider_usage_output_lookup_state_or_budget_failure"},
        "pricing": {"source_url": PRICE_URL,
                    "checked_at_utc": PRICE_CHECKED_AT_UTC,
                    "official_peak_cache_miss_usd_per_million": str(USD_PEAK_CACHE_MISS_PER_MILLION),
                    "official_peak_output_usd_per_million": str(USD_PEAK_OUTPUT_PER_MILLION),
                    "local_conversion_cny_per_usd": str(CONSERVATIVE_CNY_PER_USD),
                    "conversion_kind": "conservative_internal_ceiling_not_exchange_quote",
                    "price_ceiling_cny": _price().model_dump(mode="json")},
        "local_cap_cny": str(CAP_CNY),
        "full_run_worst_reservation_cny": str(ledger.full_run_floor()),
        "reservation_is_provider_hard_cap": False,
        "provider_bill_cny": "unknown",
        "actual_model_calls": 0,
        "new_paid_authorization": None,
        "requires_price_recheck_before_paid_execution": True,
        "m3s_execution_status": "planned_not_run",
        "component_sha256": _component_hashes(),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        **_code_provenance(),
    }
    return plan, first.model_dump()


def run_preflight(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to((root / "reports/m1-2026-10-18/inputs").resolve()):
        raise ValueError("preflight cannot modify public input")
    plan, first = prepare(root)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", plan)
    write_json(output / "first_request.json", first)
    artifacts = _artifacts(output)
    report = {"schema_version": "0.1-draft", "version": VERSION,
              "execution_kind": "preflight_no_model_call", "status": "draft_unapproved",
              "actual_model_calls": 0, "plan_sha256": canonical_hash(plan),
              "artifacts": artifacts, "artifact_set_sha256": canonical_hash(artifacts)}
    write_json(output / "run.json", report)
    return report


def verify_preflight(root: Path, output: Path) -> dict:
    plan, first = prepare(root)
    if parse_json((output / "plan.json").read_text(encoding="utf-8")) != plan:
        raise ValueError("preflight plan differs from current source")
    if parse_json((output / "first_request.json").read_text(encoding="utf-8")) != first:
        raise ValueError("preflight first request mismatch")
    artifacts = _artifacts(output)
    report = {"schema_version": "0.1-draft", "version": VERSION,
              "execution_kind": "preflight_no_model_call", "status": "draft_unapproved",
              "actual_model_calls": 0, "plan_sha256": canonical_hash(plan),
              "artifacts": artifacts, "artifact_set_sha256": canonical_hash(artifacts)}
    if parse_json((output / "run.json").read_text(encoding="utf-8")) != report:
        raise ValueError("preflight manifest mismatch")
    return report


def require_authorized_key(plan: dict, authorization: dict | None,
                           key_reader: Callable[[], str] | None = None) -> str:
    """M3s launch boundary: validate explicit scope before reading the key."""
    if (authorization is None or authorization.get("status") != "approved" or
            authorization.get("source_kind") != "direct_user_message" or
            authorization.get("scope") != "M3s_c003_complete_incremental" or
            authorization.get("plan_sha256") != canonical_hash(plan) or
            authorization.get("case_ids") != [CASE] or
            authorization.get("local_cap_cny") != str(CAP_CNY) or
            authorization.get("max_provider_attempts") != MAX_PROVIDER_ATTEMPTS or
            not isinstance(authorization.get("user_authorization_ref"), str) or
            not authorization["user_authorization_ref"].strip()):
        raise ValueError("new scoped paid-run authorization required before credential access")
    reader = key_reader or (lambda: os.environ.get("DEEPSEEK_API_KEY", ""))
    key = reader()
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is required after authorization")
    return key


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3r no-network real-run preflight")
    parser.add_argument("command", choices=("prepare", "verify", "gate"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        report = run_preflight(args.root, args.output)
    else:
        report = verify_preflight(args.root, args.output)
        if args.command == "gate":
            # A separate M3s execution implementation will consume a user-approved
            # receipt. This command deliberately has no paid-run option.
            require_authorized_key(parse_json((args.output / "plan.json").read_text(encoding="utf-8")), None)
    print(json.dumps({"status": report["status"], "actual_model_calls": 0},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
