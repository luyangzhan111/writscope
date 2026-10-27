import json
from pathlib import Path

import pytest

from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from scripts.m3k_history_lookup import HistoryLookupSession, LookupPolicy, LookupRequest
from scripts import s3_interaction_v3 as interaction
from writscope.baselines import build_request as baseline
from writscope.contracts import load_bundle
from writscope.events import DecisionPoint
from writscope.providers import ModelConfig


ROOT = Path(__file__).resolve().parents[1]
POLICY = LookupPolicy(max_requests=2, max_events_per_request=2)
MODEL = ModelConfig(model_id="deepseek-flash", temperature=0.0, max_output_tokens=4096)
PROMPT = (ROOT / "prompts/incremental_interaction_v4.md").read_text(encoding="utf-8")
FINAL = (ROOT / "prompts/incremental_final_review_v1.md").read_text(encoding="utf-8")


def data(case="c001"):
    return load_bundle(ROOT / "reports/m1-2026-10-18/inputs/verified_impact" / case)


def accepted_prefix(bundle):
    config_hash = interaction.config_hash(PROMPT, FINAL, MODEL, POLICY)
    state = BatchStateEngine(bundle.review_input.case_id, bundle.review_input.events,
                             config_hash, set(bundle.registry.artifacts))
    context = TaskContextLedger(bundle.review_input.events, set(bundle.registry.artifacts))
    event = bundle.review_input.events[0]
    raw = json.dumps({"kind": "extract", "task_context": [], "state_items": [{
        "operation": "no_change", "source_quote": event.content[:20], "target_ids": [],
        "constraint": None, "authorization": None}]}, ensure_ascii=False)
    parsed = interaction.parse_response(raw, bundle, 0, config_hash, "call_prefix", POLICY)
    assert state.submit_batch(parsed.transition_batch).accepted
    context.submit(parsed.context_batch)
    return state.snapshot(), context.snapshot(), config_hash


def test_request_hides_prior_event_text_but_preserves_history_index_and_evidence_hash():
    bundle = data()
    state, context, _ = accepted_prefix(bundle)
    request = interaction.build_request(bundle, 1, state, context, PROMPT, MODEL, POLICY)
    visible = request.public_input["review_input"]["events"]
    assert [event["event_id"] for event in visible] == ["e002"]
    assert request.public_input["history_index"] == [{"event_id": "e001", "seq": 1, "role": "user"}]
    assert bundle.review_input.events[0].content not in json.dumps(request.public_input, ensure_ascii=False)
    full = baseline(bundle, "full_checklist", "public parity", MODEL)
    assert request.evidence_sha256 == full.evidence_sha256


def test_tool_event_no_change_quotes_current_tool_text_without_authority():
    bundle = data()
    state, context, config_hash = accepted_prefix(bundle)
    event = bundle.review_input.events[1]
    raw = json.dumps({"kind": "extract", "task_context": [], "state_items": [{
        "operation": "no_change", "source_quote": event.content[:16], "target_ids": [],
        "constraint": None, "authorization": None}]}, ensure_ascii=False)
    parsed = interaction.parse_response(raw, bundle, 1, config_hash, "call_tool", POLICY)
    assert parsed.transition_batch.proposals[0].operation == "no_change"


def test_tool_event_cannot_create_task_context():
    bundle = data()
    state, context, config_hash = accepted_prefix(bundle)
    event = bundle.review_input.events[1]
    raw = json.dumps({"kind": "extract", "task_context": [{
        "source_quote": event.content[:16], "verification_refs": []}], "state_items": [{
        "operation": "no_change", "source_quote": event.content[:16], "target_ids": [],
        "constraint": None, "authorization": None}]}, ensure_ascii=False)
    with pytest.raises(ValueError, match="task context requires trusted user source"):
        interaction.parse_response(raw, bundle, 1, config_hash, "call_bad_context", POLICY)


def test_lookup_returns_old_event_only_in_continuation_request():
    bundle = data()
    state, context, _ = accepted_prefix(bundle)
    point = DecisionPoint(event_id="e002", seq=2)
    session = HistoryLookupSession(bundle.review_input.events, point, POLICY)
    record = session.lookup(LookupRequest(event_ids=["e001"]))
    assert record.status == "succeeded"
    request = interaction.build_request(bundle, 1, state, context, PROMPT, MODEL, POLICY,
                                        prior_lookup_attempts=0, lookup_records=[record])
    public = request.public_input
    assert public["review_input"]["events"][0]["event_id"] == "e002"
    assert public["history_lookup"]["current_event_results"][0]["events"][0]["event_id"] == "e001"
    assert public["history_lookup"]["remaining_requests"] == 1
