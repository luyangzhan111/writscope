"""Combined offline trace uses manual responses, never a model provider."""

import json
from pathlib import Path
import shutil
from uuid import uuid4

from scripts.m3i_task_context import ContextQuote
from scripts.m3k_history_lookup import LookupPolicy, LookupRequest
from scripts.m3l_offline_trace import EventPlan, TracePlan, run_offline, verify_run
from writscope.artifacts import canonical_hash, write_json
from writscope.contracts import load_bundle, parse_json
from writscope.state import AuthorizationDraft, ChangeDetails, Condition, ConstraintDraft, Predicate


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c002"


def _output():
    return ROOT / "reports/local" / ("m3l-test-" + uuid4().hex)


def _payload(operation, quote, *, constraint=None, authorization=None):
    return {"operation": operation, "source_quote": quote, "target_ids": [],
            "constraint": constraint.model_dump() if constraint else None,
            "authorization": authorization.model_dump() if authorization else None}


def _plan(*, bad_second=False, bad_lookup=False, lookup_budget=2,
          wrong_scope=False, bad_response_at=None):
    bundle = load_bundle(BUNDLE)
    review = bundle.review_input
    subject = bundle.evidence.impacts[0].subject
    constraints = [ConstraintDraft(
        constraint_id=key, subjects=[subject],
        predicate=Predicate(kind="field_exists", expected=True,
                            qualifiers={"contract_ref": "private_label" if bad_second and index == 1 else "contract"}),
        interpretation="resolved", supersedes=[])
        for index, key in enumerate(("con_status", "con_shape"))]
    granted_subject = subject.model_copy(update={"api_version": "v2", "path": "/api/v2/profile"}) if wrong_scope else subject
    grant = AuthorizationDraft(
        authorization_id="auth_v1_replace", subjects=[granted_subject], change_type="replace_field",
        change_details=ChangeDetails(old_field="status", new_field="state",
                                     preserve_value_semantics=True),
        conditions=[Condition(kind="candidate_sha256", value=review.candidate_patch.sha256,
                              resolution="unknown", evidence_refs=["patch"])],
        interpretation="resolved", constraint_refs=["con_status", "con_shape"],
        supersedes=[])
    responses = [
        [_payload("add_constraint", "保持旧客户端所使用的 v1 status 字段",
                  constraint=constraints[0]),
         _payload("add_constraint", "响应对象只能包含 status", constraint=constraints[1])],
        [_payload("no_change", review.events[1].content)],
        [_payload("no_change", review.events[2].content)],
        [_payload("no_change", review.events[3].content)],
        [_payload("grant", review.events[4].content, authorization=grant)],
    ]
    context = [
        [ContextQuote(source_quote="修复新页面的状态显示", verification_refs=[]),
         ContextQuote(source_quote="可以在客户端本地映射成页面需要的 state。", verification_refs=[])],
        [], [],
        [ContextQuote(source_quote="不增加接口或网络请求。", verification_refs=[])],
        [],
    ]
    lookups = [[], [], [], [LookupRequest(event_ids=["e001"])],
               [LookupRequest(event_ids=["e006" if bad_lookup else "e004"])]]
    return TracePlan(
        schema_version="0.1-draft", case_id=review.case_id,
        lookup_policy=LookupPolicy(max_requests=lookup_budget, max_events_per_request=2),
        events=[EventPlan(event_id=event.event_id, context_quotes=context[index],
                          lookup_requests=lookups[index],
                          response_text="{" if bad_response_at == event.seq else
                          json.dumps({"items": responses[index]}, ensure_ascii=False))
                for index, event in enumerate(review.events)])


def test_complete_trace_records_three_components_and_replays():
    output = _output()
    try:
        manifest = run_offline(BUNDLE, _plan(), output)
        assert manifest["status"] == "completed" and manifest["events_attempted"] == 5
        assert manifest["lookup_attempts"] == 2 and manifest["new_model_calls"] == 0
        assert manifest["final_model_review"] == "not_run"
        trace = parse_json((output / "trace.json").read_text(encoding="utf-8"))
        assert trace[3]["lookups"][0]["events"][0]["event_id"] == "e001"
        assert trace[4]["lookups"][0]["events"][0]["event_id"] == "e004"
        assert all(row["status"] == "accepted" for row in trace)
        context = parse_json((output / "task_context.json").read_text(encoding="utf-8"))
        assert context["through_seq"] == 5 and len(context["task_context"]) == 3
        assessment = parse_json((output / "assessment.json").read_text(encoding="utf-8"))
        assert assessment["authorization"] == "covered"
        assert verify_run(BUNDLE, output) == manifest
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_batch_failure_stops_without_partial_state_or_assessment():
    output = _output()
    try:
        manifest = run_offline(BUNDLE, _plan(bad_second=True), output)
        assert manifest["status"] == "stopped_on_failure" and manifest["events_attempted"] == 1
        assert not (output / "assessment.json").exists()
        trace = parse_json((output / "trace.json").read_text(encoding="utf-8"))
        assert trace[0]["status"] == "batch_rejected"
        assert trace[0]["batch_record"]["failed_item_index"] == 1
        state = parse_json((output / "state.json").read_text(encoding="utf-8"))
        assert state["through_seq"] == 1 and state["constraints"] == []
        assert verify_run(BUNDLE, output) == manifest
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_lookup_denial_and_global_limit_stop_before_event_update():
    for options, expected_reason in (({"bad_lookup": True}, "outside visible"),
                                     ({"lookup_budget": 1}, "budget exhausted")):
        output = _output()
        try:
            manifest = run_offline(BUNDLE, _plan(**options), output)
            assert manifest["status"] == "stopped_on_failure"
            assert manifest["events_attempted"] == 5 and manifest["lookup_attempts"] == 2
            trace = parse_json((output / "trace.json").read_text(encoding="utf-8"))
            assert trace[-1]["status"] == "lookup_denied"
            assert expected_reason in trace[-1]["error"]
            assert trace[-1]["context_record"] is None and trace[-1]["batch_record"] is None
            assert parse_json((output / "state.json").read_text(encoding="utf-8"))["through_seq"] == 4
            assert not (output / "assessment.json").exists()
            assert verify_run(BUNDLE, output) == manifest
        finally:
            if output.exists():
                shutil.rmtree(output)


def test_tampered_trace_is_rejected_even_if_artifact_hash_rewritten():
    output = _output()
    try:
        run_offline(BUNDLE, _plan(), output)
        trace_path = output / "trace.json"
        trace = parse_json(trace_path.read_text(encoding="utf-8"))
        trace[3]["lookups"][0]["events"][0]["content"] = "forged"
        write_json(trace_path, trace)
        manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
        from writscope.m3b import _artifacts
        manifest["artifacts"] = _artifacts(output)
        manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
        write_json(output / "run.json", manifest)
        try:
            verify_run(BUNDLE, output)
            raise AssertionError("tampered lookup content should not replay")
        except ValueError as exc:
            assert "replay mismatch" in str(exc)
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_invalid_response_stops_without_final_assessment():
    output = _output()
    try:
        manifest = run_offline(BUNDLE, _plan(bad_response_at=3), output)
        assert manifest["status"] == "stopped_on_failure" and manifest["events_attempted"] == 3
        trace = parse_json((output / "trace.json").read_text(encoding="utf-8"))
        assert trace[-1]["status"] == "invalid_response"
        assert parse_json((output / "state.json").read_text(encoding="utf-8"))["through_seq"] == 2
        assert not (output / "assessment.json").exists()
        assert verify_run(BUNDLE, output) == manifest
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_m3f_scope_rule_is_active_in_combined_trace():
    output = _output()
    try:
        manifest = run_offline(BUNDLE, _plan(wrong_scope=True), output)
        assert manifest["status"] == "stopped_on_failure"
        trace = parse_json((output / "trace.json").read_text(encoding="utf-8"))
        assert trace[-1]["status"] == "batch_rejected"
        assert "scope mismatch" in trace[-1]["error"]
        assert not (output / "assessment.json").exists()
        assert verify_run(BUNDLE, output) == manifest
    finally:
        if output.exists():
            shutil.rmtree(output)
