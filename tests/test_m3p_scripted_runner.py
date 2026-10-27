"""M3p integrates scripted responses, state, lookup, final review and ledger."""

import json
from decimal import Decimal
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from scripts.m3k_history_lookup import LookupPolicy
from scripts.m3p_scripted_runner import run_offline, verify_run
from writscope.artifacts import canonical_hash, write_json
from writscope.budget import PriceCeiling
from writscope.contracts import load_bundle, parse_json
from writscope.m3b import _artifacts
from writscope.providers import ModelConfig, Usage
from writscope.providers.mock import Script, Step
from test_m3l_offline_trace import _plan as _rich_plan


ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "reports/m1-2026-10-18/inputs/verified_impact"
INTERACTION = (ROOT / "prompts/incremental_interaction_v1.md").read_text(encoding="utf-8")
FINAL = (ROOT / "prompts/incremental_final_review_v1.md").read_text(encoding="utf-8")
MODEL = ModelConfig(model_id="deepseek-flash")
POLICY = LookupPolicy(max_requests=2, max_events_per_request=2)
PRICE = PriceCeiling(input_cny_per_million=Decimal("2"),
                     output_cny_per_million=Decimal("8"),
                     source_url="offline-test-price", checked_at_utc="offline-test")
USAGE = Usage(input_tokens=20, output_tokens=10)


def _output():
    return ROOT / "reports/local" / ("m3p-test-" + uuid4().hex)


def _extract(event, *, context=None, state_items=None):
    return {"kind": "extract", "task_context": context or [],
            "state_items": state_items or [{"operation": "no_change",
                "source_quote": event.content, "target_ids": [],
                "constraint": None, "authorization": None}]}


def _final(bundle):
    subject = bundle.evidence.impacts[0].subject.model_dump()
    source = {"event_id": "e001", "quote": "保持旧客户端所使用的 v1 status 字段"}
    return {"impact": "breaking", "authorization": "ambiguous", "decision": "ask",
            "affected_subjects": [subject],
            "per_impact_authorization": [{"impact_id": "impact_001",
                "verdict": "ambiguous", "constraint_refs": [],
                "authorization_refs": [], "source_refs": [source],
                "reason": "scripted uncertainty"}],
            "constraint_refs": [], "authorization_refs": [], "source_refs": [source],
            "evidence_refs": ["impact_001"], "reason": "scripted interface check",
            "limitations": ["Scripted response, no model inference"],
            "derived_items": [], "checklist": None}


def _script(bundle, *, lookups=True):
    events = bundle.review_input.events
    values = []
    for index, event in enumerate(events):
        if lookups and index in (3, 4):
            values.append({"kind": "lookup_request",
                           "event_ids": ["e001" if index == 3 else "e004"]})
        context = []
        if index == 0:
            context = [{"source_quote": "修复新页面的状态显示",
                        "verification_refs": []}]
        if index == 3:
            context = [{"source_quote": "不增加接口或网络请求。",
                        "verification_refs": []}]
        values.append(_extract(event, context=context))
    values.append(_final(bundle))
    return Script(schema_version="0.1-draft", execution_kind="offline_mock",
                  description="M3p scripted interface fixture, no semantic prediction",
                  steps=[Step(action="response", text=json.dumps(value, ensure_ascii=False),
                              synthetic_usage=USAGE) for value in values])


def _rich_script(bundle):
    """Convert an offline fixture into provider responses, never runner state."""
    plan = _rich_plan()
    values = []
    for item in plan.events:
        values.extend({"kind": "lookup_request", "event_ids": request.event_ids}
                      for request in item.lookup_requests)
        values.append({"kind": "extract",
                       "task_context": [quote.model_dump() for quote in item.context_quotes],
                       "state_items": json.loads(item.response_text)["items"]})
    values.append(_final(bundle))
    return Script(schema_version="0.1-draft", execution_kind="offline_mock",
                  description="M3p scripted state-update fixture, not model evidence",
                  steps=[Step(action="response", text=json.dumps(value, ensure_ascii=False),
                              synthetic_usage=USAGE) for value in values])


def _run(case, output, script=None):
    bundle = load_bundle(INPUTS / case)
    script = script or _script(bundle)
    result = run_offline(INPUTS / case, script, output, INTERACTION, FINAL,
                         MODEL, POLICY, PRICE, Decimal("10"))
    return bundle, result


@pytest.mark.parametrize("case", ["c001", "c002", "c003"])
def test_three_histories_complete_with_lookup_final_review_and_replay(case):
    output = _output()
    try:
        bundle, result = _run(case, output)
        assert result["status"] == "completed"
        assert result["simulated_provider_attempts"] == 8
        assert result["lookup_attempts"] == 2
        assert result["actual_model_calls"] == 0
        assert result["unknown_billed_attempts"] == 8
        assert result["final_review_valid"]
        assert verify_run(INPUTS / case, output) == result
        calls = parse_json((output / "calls.json").read_text(encoding="utf-8"))
        assert [row["stage"] for row in calls] == [
            "extract", "extract", "extract", "extract", "lookup_continuation",
            "extract", "lookup_continuation", "final_review"]
        assert calls[3]["request"]["public_input"]["review_input"]["decision_point"]["seq"] == 4
        assert calls[4]["request"]["public_input"]["history_lookup"]["current_event_results"][0]["events"][0]["event_id"] == "e001"
        assert parse_json((output / "task_context.json").read_text(encoding="utf-8"))["through_seq"] == 5
        assert len(parse_json((output / "task_context.json").read_text(encoding="utf-8"))["task_context"]) == 2
        assert parse_json((output / "final_request.json").read_text(encoding="utf-8"))["evidence_sha256"] == canonical_hash({
            "review_input": bundle.review_input.model_dump(), "artifacts": dict(bundle.contents),
            "artifact_registry": bundle.registry.model_dump()})
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_scripted_responses_drive_nonempty_state_and_final_request():
    output = _output()
    bundle = load_bundle(INPUTS / "c002")
    try:
        _, result = _run("c002", output, _rich_script(bundle))
        assert result["status"] == "completed"
        state = parse_json((output / "state.json").read_text(encoding="utf-8"))
        assert {item["constraint_id"] for item in state["constraints"]} == {"con_status", "con_shape"}
        assert {item["authorization_id"] for item in state["authorizations"]} == {"auth_v1_replace"}
        final = parse_json((output / "final_request.json").read_text(encoding="utf-8"))
        assert final["public_input"]["current_state"] == state
        assert final["public_input"]["task_context"] == parse_json(
            (output / "task_context.json").read_text(encoding="utf-8"))
        assert verify_run(INPUTS / "c002", output) == result
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_state_rejection_stops_and_does_not_commit_context_or_final():
    output = _output()
    bundle = load_bundle(INPUTS / "c002")
    bad = _extract(bundle.review_input.events[0],
        context=[{"source_quote": "修复新页面的状态显示", "verification_refs": []}],
        state_items=[{"operation": "add_constraint",
            "source_quote": "保持旧客户端所使用的 v1 status 字段",
            "target_ids": [], "constraint": {
                "constraint_id": "bad_constraint", "subjects": [bundle.evidence.impacts[0].subject.model_dump()],
                "predicate": {"kind": "field_exists", "expected": True,
                              "qualifiers": {"contract_ref": "private_label"}},
                "interpretation": "resolved", "supersedes": []},
            "authorization": None}])
    script = Script(schema_version="0.1-draft", execution_kind="offline_mock",
                    description="rejected atomic batch", steps=[Step(
                        action="response", text=json.dumps(bad, ensure_ascii=False),
                        synthetic_usage=USAGE)])
    try:
        _, result = _run("c002", output, script)
        assert result["status"] == "stopped_on_failure"
        assert result["failure"]["status"] == "batch_rejected"
        assert result["events_accepted"] == 0
        assert not result["final_review_valid"]
        assert not (output / "final_result.json").exists()
        assert parse_json((output / "task_context.json").read_text(encoding="utf-8"))["task_context"] == []
        assert parse_json((output / "state.json").read_text(encoding="utf-8"))["constraints"] == []
        assert verify_run(INPUTS / "c002", output) == result
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_future_lookup_and_missing_usage_stop_without_final():
    bundle = load_bundle(INPUTS / "c001")
    for value, usage, status in (({"kind": "lookup_request", "event_ids": ["e005"]},
                                  USAGE, "invalid_output"),
                                 (_extract(bundle.review_input.events[0]), None, "usage_missing")):
        output = _output()
        script = Script(schema_version="0.1-draft", execution_kind="offline_mock",
                        description="stopping fixture", steps=[Step(
                            action="response", text=json.dumps(value, ensure_ascii=False),
                            synthetic_usage=usage)])
        try:
            _, result = _run("c001", output, script)
            assert result["failure"]["status"] == status
            assert result["simulated_provider_attempts"] == 1
            assert result["events_accepted"] == 0
            assert not (output / "final_result.json").exists()
            assert verify_run(INPUTS / "c001", output) == result
        finally:
            if output.exists():
                shutil.rmtree(output)


def test_request_tampering_fails_replay_even_with_rewritten_file_hashes():
    output = _output()
    try:
        _run("c003", output)
        path = output / "calls.json"
        calls = parse_json(path.read_text(encoding="utf-8"))
        calls[0]["request"]["public_input"]["review_input"]["events"][0]["content"] = "forged"
        write_json(path, calls)
        manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
        manifest["artifacts"] = _artifacts(output)
        manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
        write_json(output / "run.json", manifest)
        with pytest.raises(ValueError, match="trajectory replay mismatch"):
            verify_run(INPUTS / "c003", output)
    finally:
        if output.exists():
            shutil.rmtree(output)
