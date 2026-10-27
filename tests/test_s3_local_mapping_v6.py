"""Offline semantic-boundary controls; no provider or reference labels."""
import json
from pathlib import Path
import re

from scripts import s3_acceptance_v4 as frozen
from scripts import s3_interaction_v3
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine

ROOT = Path(__file__).resolve().parents[1]
PROMPT = re.sub(r"\s+", " ", (ROOT / "prompts/incremental_interaction_v6.md").read_text(encoding="utf-8"))


def test_v6_requires_explicit_server_authority_and_separates_local_context():
    assert "explicitly permits a server-visible API change" in PROMPT
    assert "does not authorize a change to a server-visible API" in PROMPT
    assert "Do not borrow permission from a later, unseen event." in PROMPT
    assert "c004" not in PROMPT and "e001" not in PROMPT


def test_offline_local_mapping_control_is_context_plus_constraint_only():
    bundle = frozen.bundle(ROOT, "c004")
    event = bundle.review_input.events[0]
    constraint_quote = "GET /api/v1/profile 的旧客户端契约是 HTTP 200，响应对象只能包含 status，值为字符串 active，表示账户启用。"
    mapping_quote = "可以在客户端本地映射成页面需要的 state。"
    raw = json.dumps({
        "kind": "extract",
        "task_context": [{"source_quote": mapping_quote, "verification_refs": []}],
        "state_items": [{
            "source_quote": constraint_quote,
            "target_ids": [],
            "constraint": {
                "constraint_id": "control_v1_profile_status",
                "subjects": [{"protocol": "http", "method": "GET", "path": "/api/v1/profile",
                              "api_version": "v1", "location": "response.body", "field": "status"}],
                "predicate": {"kind": "response_shape", "expected": {"status": "active"},
                              "qualifiers": {"http_status": 200, "exact_keys": ["status"],
                                             "status_type": "string"}},
                "interpretation": "resolved", "supersedes": []},
            "authorization": None,
            "operation": "add_constraint"}]}, ensure_ascii=False)
    parsed = s3_interaction_v3.parse_response(
        raw, bundle, 0, "a" * 64, "offline_control_call", frozen.POLICY)
    context = TaskContextLedger(bundle.review_input.events, set(bundle.registry.artifacts))
    context.submit(parsed.context_batch)
    engine = BatchStateEngine(bundle.review_input.case_id, bundle.review_input.events,
                              "a" * 64, set(bundle.registry.artifacts))
    record = engine.submit_batch(parsed.transition_batch)

    assert event.content.find(mapping_quote) >= 0
    assert record.accepted is True
    assert len(engine.constraints) == 1
    assert engine.authorizations == {}
    assert context.snapshot().task_context[0].requirement == mapping_quote


def test_saved_v4_response_still_reproduces_scope_rejection_without_repair():
    bundle = frozen.bundle(ROOT, "c004")
    calls = json.loads((ROOT / "reports/local/s3-development-v4-live-20261026/calls.json")
                       .read_text(encoding="utf-8"))
    saved = next(row for row in calls if row["job_id"] == "job_002")
    parsed = s3_interaction_v3.parse_response(
        saved["response_text"], bundle, 0, "b" * 64, saved["call_id"], frozen.POLICY)
    engine = BatchStateEngine(bundle.review_input.case_id, bundle.review_input.events,
                              "b" * 64, set(bundle.registry.artifacts))
    record = engine.submit_batch(parsed.transition_batch)

    assert record.accepted is False
    assert record.rejection_reason == saved["error"]
    assert engine.constraints == {}
    assert engine.authorizations == {}
