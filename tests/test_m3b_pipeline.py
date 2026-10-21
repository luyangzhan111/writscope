"""Offline pipeline tests. Scripted responses are fixtures, not model results."""

import asyncio
import json

import pytest

from writscope.baselines import build_request as baseline_request
from writscope.contracts import load_bundle
from writscope.m3b import ExtractionPayload, build_request, run, verify_run
from writscope.providers import ModelConfig
from writscope.providers.mock import Script, ScriptedMock, Step
from writscope.state import (AuthorizationDraft, ChangeDetails, Condition,
                             ConstraintDraft, Predicate, StateEngine)


PROMPT = "Extract one transition from the current trusted event."


def bundle_for(root, case="c002"):
    path = root / "reports/m1-2026-10-18/inputs/verified_impact" / case
    return path, load_bundle(path)


def payloads(bundle, *, grant=True):
    events = bundle.review_input.events
    subject = bundle.evidence.impacts[0].subject
    rows = [ExtractionPayload(operation="add_constraint", source_quote=events[0].content,
                              target_ids=[], constraint=ConstraintDraft(
                                  constraint_id="con_status", subjects=[subject],
                                  predicate=Predicate(kind="field_exists", expected=True,
                                                      qualifiers={"contract_ref": "contract"}),
                                  interpretation="resolved", supersedes=[]),
                              authorization=None)]
    rows.extend(ExtractionPayload(operation="no_change", source_quote=event.content,
                                  target_ids=[], constraint=None, authorization=None)
                for event in events[1:])
    if grant:
        rows[-1] = ExtractionPayload(
            operation="grant", source_quote=events[-1].content, target_ids=[], constraint=None,
            authorization=AuthorizationDraft(
                authorization_id="auth_replace", subjects=[subject], change_type="replace_field",
                change_details=ChangeDetails(old_field="status", new_field="state",
                                             preserve_value_semantics=True),
                conditions=[Condition(kind="candidate_sha256",
                                      value=bundle.review_input.candidate_patch.sha256,
                                      resolution="unknown", evidence_refs=["patch"])],
                interpretation="resolved", constraint_refs=["con_status"], supersedes=[]))
    return rows


def script(rows):
    return Script(schema_version="0.1-draft", execution_kind="offline_mock",
                  description="offline response sequence, no request inspection",
                  steps=[Step(action="response", text=json.dumps(row.model_dump(), ensure_ascii=False))
                         for row in rows])


def test_incremental_scripted_run_replays_and_uses_same_public_evidence(root, tmp_path):
    path, bundle = bundle_for(root)
    output = tmp_path / "run"
    manifest = asyncio.run(run(path, output, ScriptedMock(script(payloads(bundle))), PROMPT))
    assert manifest["status"] == "completed"
    assert manifest["cost"]["actual_model_calls"] == 0
    assert manifest["cost"]["provider_attempts"] == 5
    assert manifest["cost"]["actual_extraction_cost_cny"] is None
    assert manifest["cost"]["provider_elapsed_ms"] <= manifest["cost"]["pipeline_attempt_elapsed_ms"]
    assert manifest["cost"]["synthetic_test_usage"]["input_tokens"]["complete_total"] is None
    assert manifest["common_evidence_sha256"] == baseline_request(
        bundle, "full_checklist", "same public evidence", ModelConfig()).evidence_sha256
    assert verify_run(path, output)["state_hash"] == manifest["state_hash"]
    assessment = json.loads((output / "assessment.json").read_text(encoding="utf-8"))
    assert (assessment["impact"], assessment["authorization"], assessment["decision"]) == (
        "breaking", "covered", "allow")
    first_request = json.loads((output / "step_001/request.json").read_text(encoding="utf-8"))
    assert [event["event_id"] for event in first_request["public_input"]["review_input"]["events"]] == ["e001"]
    assert "evaluation_private" not in json.dumps(first_request, ensure_ascii=False)


def test_request_builder_rejects_future_state_and_mock_only(root, tmp_path):
    path, bundle = bundle_for(root)
    state = StateEngine("c002", bundle.review_input.events, "0" * 64).snapshot()
    with pytest.raises(ValueError, match="immediately before"):
        build_request(bundle, 1, state, PROMPT, ModelConfig())
    with pytest.raises(ValueError, match="offline-only"):
        asyncio.run(run(path, tmp_path / "no_run", object(), PROMPT))
    assert not (tmp_path / "no_run").exists()


def test_invalid_response_stops_without_fabricated_assessment(root, tmp_path):
    path, _ = bundle_for(root)
    bad = Script(schema_version="0.1-draft", execution_kind="offline_mock",
                 description="invalid JSON", steps=[Step(action="response", text="{bad")])
    output = tmp_path / "invalid"
    manifest = asyncio.run(run(path, output, ScriptedMock(bad), PROMPT))
    assert manifest["status"] == "incomplete"
    assert manifest["attempted_events"] == 1
    assert manifest["consumed_events"] == 0
    assert not (output / "assessment.json").exists()
    assert verify_run(path, output)["status"] == "incomplete"


def test_tool_spoof_is_recorded_as_rejected_transition(root, tmp_path):
    path, bundle = bundle_for(root)
    rows = payloads(bundle)
    rows[1] = rows[-1].model_copy(update={"source_quote": bundle.review_input.events[1].content})
    output = tmp_path / "spoof"
    manifest = asyncio.run(run(path, output, ScriptedMock(script(rows)), PROMPT))
    assert manifest["status"] == "incomplete"
    assert manifest["consumed_events"] == 2
    assert manifest["rejected_transitions"] == 1
    assert not (output / "assessment.json").exists()
    assert verify_run(path, output)["status"] == "incomplete"


def test_saved_response_tamper_fails_verification(root, tmp_path):
    path, bundle = bundle_for(root)
    output = tmp_path / "tamper"
    asyncio.run(run(path, output, ScriptedMock(script(payloads(bundle))), PROMPT))
    (output / "step_005/response.txt").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash"):
        verify_run(path, output)
