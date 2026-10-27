"""V2 shape constraints, old scope/provenance gates, and saved failure reproduction."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import m3o_interaction_contract as v1
from scripts import s3_interaction_v2 as v2
from scripts import s3_proposal_shape_diagnosis as diagnosis
from scripts import s3_common_round as common
from scripts import m3q_fault_evidence_audit as fixtures
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from writscope.artifacts import canonical_hash, digest, write_json

ROOT = Path(__file__).resolve().parents[1]


def parts():
    data = common.bundle(ROOT, "c002")
    subject = data.evidence.impacts[0].subject.model_dump()
    constraint = {"constraint_id": "new_c", "subjects": [subject],
                  "predicate": {"kind": "field_exists", "expected": True, "qualifiers": {}},
                  "interpretation": "resolved", "supersedes": []}
    authorization = {"authorization_id": "new_a", "subjects": [subject], "change_type": "replace_field",
                     "change_details": {"old_field": "status", "new_field": "state", "preserve_value_semantics": True},
                     "conditions": [], "interpretation": "resolved", "constraint_refs": ["existing_c"], "supersedes": []}
    base = {"source_quote": data.review_input.events[0].content, "target_ids": [],
            "constraint": None, "authorization": None}
    return data, base, constraint, authorization


def wrap(item):
    return {"kind": "extract", "task_context": [], "state_items": [item]}


@pytest.mark.parametrize("operation", ["add_constraint", "grant", "replace_constraint",
                                       "replace_authorization", "revoke", "mark_ambiguous", "no_change"])
def test_valid_operation_shapes_and_required_null_fields(operation):
    _, item, constraint, authorization = parts()
    item["operation"] = operation
    if operation == "add_constraint":
        item["constraint"] = constraint
    elif operation == "grant":
        item["authorization"] = authorization
    elif operation.startswith("replace_"):
        item["operation"] = "supersede"; item["target_ids"] = ["old_id"]
        key, value = ("constraint", constraint) if operation.endswith("constraint") else ("authorization", authorization)
        value["supersedes"] = ["old_id"]; item[key] = value
    elif operation in {"revoke", "mark_ambiguous"}:
        item["target_ids"] = ["old_id"]
    raw = wrap(item)
    assert v2.RESPONSE_ADAPTER.validate_python(raw).model_dump() == raw
    del raw["state_items"][0]["target_ids"]
    with pytest.raises(ValueError):
        v2.RESPONSE_ADAPTER.validate_python(raw)


@pytest.mark.parametrize("defect", ["grant_targets", "grant_supersedes", "bare_supersede",
                                    "mismatched_replacement", "mixed_no_change", "both_payloads", "duplicate_targets"])
def test_bad_shapes_are_rejected_without_automatic_repairs(defect):
    _, item, constraint, authorization = parts()
    item.update(operation="grant", authorization=authorization)
    if defect == "grant_targets": item["target_ids"] = ["existing_c"]
    elif defect == "grant_supersedes": authorization["supersedes"] = ["existing_c"]
    elif defect == "bare_supersede": item.update(operation="supersede", target_ids=["old"], authorization=None)
    elif defect == "mismatched_replacement":
        item.update(operation="supersede", target_ids=["old"]); authorization["supersedes"] = ["different"]
    elif defect == "both_payloads": item["constraint"] = constraint
    elif defect == "duplicate_targets": item.update(operation="revoke", target_ids=["old", "old"], authorization=None)
    raw = wrap(item)
    if defect == "mixed_no_change":
        raw["state_items"].append({"operation": "no_change", "source_quote": item["source_quote"],
                                   "target_ids": [], "constraint": None, "authorization": None})
    before = deepcopy(raw)
    with pytest.raises(ValueError): v2.RESPONSE_ADAPTER.validate_python(raw)
    assert raw == before


def test_schema_exposes_constraints_in_model_visible_json():
    schema = v2.RESPONSE_ADAPTER.json_schema()["$defs"]
    assert schema["Grant"]["properties"]["target_ids"]["maxItems"] == 0
    assert schema["NewAuthorization"]["properties"]["supersedes"]["maxItems"] == 0
    assert schema["ReplaceConstraint"]["properties"]["target_ids"]["minItems"] == 1
    assert schema["ReplaceConstraint"]["properties"]["authorization"]["type"] == "null"
    assert schema["NoChangeCommit"]["properties"]["state_items"]["maxItems"] == 1


def test_trusted_roles_quotes_lookup_limits_and_identity_are_preserved():
    data, item, _, authorization = parts()
    tool = data.review_input.events[1]
    item.update(operation="grant", authorization=authorization, source_quote=tool.content)
    with pytest.raises(ValueError, match="trusted user"):
        v2.parse_response(json.dumps(wrap(item)), data, 1, "0"*64, "offline", common.POLICY)
    item.update(operation="no_change", authorization=None, source_quote="nonexistent quote")
    with pytest.raises(ValueError, match="quote missing"):
        v2.parse_response(json.dumps(wrap(item)), data, 0, "0"*64, "offline", common.POLICY)
    for ids, used, message in [(["e005"], 0, "outside visible"), (["e001"], 2, "budget exhausted")]:
        with pytest.raises(ValueError, match=message):
            v2.parse_response(json.dumps({"kind": "lookup_request", "event_ids": ids}), data, 0,
                              "0"*64, "offline", common.POLICY, lookup_attempts_used=used)
    with pytest.raises(ValueError):
        v2.RESPONSE_ADAPTER.validate_python({"kind": "lookup_request", "event_ids": ["e001"], "role": "user"})


def test_new_requests_preserve_input_evidence_and_cannot_reuse_old_contract():
    data, _, _, _ = parts()
    prompt = (ROOT/"prompts/incremental_interaction_v2.md").read_text(encoding="utf-8")
    state = BatchStateEngine("c002", data.review_input.events, "0"*64, set(data.registry.artifacts)).snapshot()
    context = TaskContextLedger(data.review_input.events, set(data.registry.artifacts)).snapshot()
    old = v1.build_request(data, 0, state, context, prompt, common.MODEL, common.POLICY)
    new = v2.build_request(data, 0, state, context, prompt, common.MODEL, common.POLICY)
    assert new.public_input == old.public_input and new.evidence_sha256 == old.evidence_sha256
    assert new.output_contract == "interaction_quote_v2" and new.output_schema != old.output_schema
    assert canonical_hash(new.model_dump()) != canonical_hash(old.model_dump())


def test_saved_failure_reproduces_and_control_is_not_a_model_result(monkeypatch):
    original_read = Path.read_text
    def public_only(path, *args, **kwargs):
        assert "evaluation_private" not in str(path) and path.name != ".env"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", public_only)
    audit = diagnosis.build_audit(ROOT)
    assert audit["new_actual_model_calls"] == 0 and audit["v1_model_schema_accepts_failed_payload"]
    assert audit["v1_historical_shape_reproduction"]["accepted_events"] == 4
    failed = audit["v2_saved_response_diagnostics"]["c002"]
    assert failed["accepted_events"] == 4 and not failed["steps"][-1]["state_changed"]
    assert not failed["steps"][-1]["context_changed"]
    assert audit["synthetic_control"]["accepted_events"] == 5
    assert audit["synthetic_control_keeps_prior_constraints"]
    assert audit["model_effectiveness"] == "not_measured"
    assert all(len(step["request"]["public_input"]["review_input"]["events"]) == step["event_seq"]
               for preview in audit["previews"] for step in preview["steps"])


def test_cross_version_scope_gate_still_rejects_valid_shape():
    data = common.bundle(ROOT, "c003")
    script = fixtures._rich_script(data, wrong_scope=True)
    rows = []
    for step in script.steps:
        value = json.loads(step.text)
        if value.get("kind") == "extract":
            rows.append({"stage": "extract", "event_seq": len(rows)+1,
                         "call_id": f"synthetic_scope_{len(rows)+1}", "response_text": step.text})
    result = diagnosis.replay_prefix(ROOT, "c003", rows)
    assert result["accepted_events"] == 4 and not result["steps"][-1]["accepted"]
    assert result["state"]["authorizations"] == []


def test_saved_audit_rebuild_rejects_rehashed_tamper(monkeypatch):
    # The original-run verifier is separately exercised by the CLI below; keep
    # this test synchronous and deny any accidental provider path.
    monkeypatch.setattr(diagnosis.asyncio, "run", lambda coroutine: coroutine.close())
    output = ROOT/"reports/local"/("s3-shape-test-"+uuid4().hex)
    original = diagnosis.run(ROOT, output)
    assert diagnosis.run(ROOT, output, verify=True) == original
    audit = common.read(output/"audit.json")
    audit["v2_saved_response_diagnostics"]["c002"]["accepted_events"] = 5
    write_json(output/"audit.json", audit)
    write_json(output/"run.json", {"version": diagnosis.VERSION, "new_actual_model_calls": 0,
                                  "audit_sha256": digest((output/"audit.json").read_bytes())})
    with pytest.raises(ValueError, match="reconstruction mismatch"):
        diagnosis.run(ROOT, output, verify=True)
