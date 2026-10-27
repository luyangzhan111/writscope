"""M3o model-facing requests and response parsing, without a provider."""

import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from scripts.m3k_history_lookup import HistoryLookupSession, LookupPolicy, LookupRequest
from scripts.m3o_interaction_contract import (CASES, LookupEvidence, ParsedExtraction,
                                              build_final_request, build_request,
                                              parse_response, run_preview, verify_preview)
from writscope.artifacts import canonical_hash, write_json
from writscope.baselines import build_request as baseline_request
from writscope.contracts import load_bundle, parse_json
from writscope.events import DecisionPoint
from writscope.m3b import _artifacts
from writscope.providers import ModelConfig
from writscope.providers.deepseek import wire_bytes


ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "reports/m1-2026-10-18/inputs/verified_impact"
PROMPT = (ROOT / "prompts/incremental_interaction_v1.md").read_text(encoding="utf-8")
FINAL_PROMPT = (ROOT / "prompts/incremental_final_review_v1.md").read_text(encoding="utf-8")
POLICY = LookupPolicy(max_requests=2, max_events_per_request=2)
CONFIG_HASH = "0" * 64


def _parts(case="c002"):
    bundle = load_bundle(INPUTS / case)
    engine = BatchStateEngine(case, bundle.review_input.events, CONFIG_HASH,
                              set(bundle.registry.artifacts))
    context = TaskContextLedger(bundle.review_input.events,
                                set(bundle.registry.artifacts))
    return bundle, engine, context


def _no_change(event):
    return {"kind": "extract", "task_context": [], "state_items": [{
        "operation": "no_change", "source_quote": event.content,
        "target_ids": [], "constraint": None, "authorization": None}]}


def test_three_histories_all_event_prefixes_and_baseline_parity():
    model = ModelConfig()
    for case in CASES:
        bundle, engine, context = _parts(case)
        baseline = baseline_request(bundle, "full_checklist", "baseline", model)
        for index, event in enumerate(bundle.review_input.events):
            request = build_request(bundle, index, engine.snapshot(), context.snapshot(),
                                    PROMPT, model, POLICY)
            assert request.evidence_sha256 == baseline.evidence_sha256
            assert len(request.public_input["review_input"]["events"]) == index + 1
            assert request.public_input["review_input"]["decision_point"]["event_id"] == event.event_id
            assert request.public_input["history_lookup"]["current_event_results"] == []
            if index == 0:
                assert "evaluation_private" not in json.dumps(request.model_dump())
                assert "EVALUATOR_ONLY_M1_REFERENCE" not in json.dumps(request.model_dump())
            parsed = parse_response(json.dumps(_no_change(event), ensure_ascii=False),
                                    bundle, index, CONFIG_HASH, f"offline_{index}", POLICY)
            assert isinstance(parsed, ParsedExtraction)
            context.submit(parsed.context_batch)
            assert engine.submit_batch(parsed.transition_batch).accepted
        final = build_final_request(bundle, engine.snapshot(), context.snapshot(),
                                    FINAL_PROMPT, ModelConfig(model_id="deepseek-flash"),
                                    POLICY, [])
        assert final.evidence_sha256 == baseline.evidence_sha256
        assert final.model_config_values.model_id == "deepseek-flash"
        assert final.public_input["current_state"]["through_seq"] == 5
        assert b"deepseek-flash" in wire_bytes(final)


def test_lookup_request_and_continuation_use_trusted_cutoff():
    bundle, engine, context = _parts()
    initial = build_request(bundle, 0, engine.snapshot(), context.snapshot(),
                            PROMPT, ModelConfig(), POLICY)
    assert initial.public_input["history_lookup"]["remaining_requests"] == 2
    intent = parse_response('{"kind":"lookup_request","event_ids":["e001"]}',
                            bundle, 0, CONFIG_HASH, "first", POLICY)
    assert intent.event_ids == ["e001"]
    session = HistoryLookupSession(bundle.review_input.events,
                                   DecisionPoint(event_id="e001", seq=1), POLICY)
    record = session.lookup(LookupRequest(event_ids=intent.event_ids))
    continuation = build_request(bundle, 0, engine.snapshot(), context.snapshot(),
                                 PROMPT, ModelConfig(), POLICY,
                                 lookup_records=[record])
    lookup = continuation.public_input["history_lookup"]
    assert lookup["attempts_used"] == 1 and lookup["remaining_requests"] == 1
    assert lookup["current_event_results"][0]["events"][0]["content"] == bundle.review_input.events[0].content
    with pytest.raises(ValueError, match="outside visible history"):
        parse_response('{"kind":"lookup_request","event_ids":["e005"]}',
                       bundle, 0, CONFIG_HASH, "first", POLICY)
    with pytest.raises(ValueError, match="budget exhausted"):
        parse_response('{"kind":"lookup_request","event_ids":["e001"]}',
                       bundle, 0, CONFIG_HASH, "third", POLICY,
                       lookup_attempts_used=2)


def test_model_cannot_invent_identity_or_turn_tool_text_into_authorization():
    bundle, _, _ = _parts()
    user = bundle.review_input.events[0]
    response = _no_change(user)
    response["event_id"] = "e005"
    with pytest.raises(ValueError):
        parse_response(json.dumps(response, ensure_ascii=False), bundle, 0,
                       CONFIG_HASH, "call", POLICY)
    tool = bundle.review_input.events[1]
    response = _no_change(tool)
    response["task_context"] = [{"source_quote": tool.content,
                                 "verification_refs": []}]
    with pytest.raises(ValueError, match="trusted user source"):
        parse_response(json.dumps(response, ensure_ascii=False), bundle, 1,
                       CONFIG_HASH, "call", POLICY)
    response = _no_change(tool)
    response["state_items"][0]["operation"] = "grant"
    with pytest.raises(ValueError, match="trusted user source"):
        parse_response(json.dumps(response, ensure_ascii=False), bundle, 1,
                       CONFIG_HASH, "call", POLICY)


def test_quote_scope_and_prior_state_fail_closed():
    bundle, engine, context = _parts()
    event = bundle.review_input.events[0]
    response = _no_change(event)
    response["task_context"] = [{"source_quote": "not in event",
                                 "verification_refs": []}]
    with pytest.raises(ValueError, match="task-context quote"):
        parse_response(json.dumps(response, ensure_ascii=False), bundle, 0,
                       CONFIG_HASH, "call", POLICY)
    response = _no_change(event)
    response["state_items"][0]["source_quote"] = "not in event"
    with pytest.raises(ValueError, match="state source quote"):
        parse_response(json.dumps(response, ensure_ascii=False), bundle, 0,
                       CONFIG_HASH, "call", POLICY)
    state = engine.snapshot().model_copy(update={"through_seq": 1})
    with pytest.raises(ValueError, match="hash mismatch"):
        build_request(bundle, 0, state, context.snapshot(), PROMPT,
                      ModelConfig(), POLICY)
    with pytest.raises(ValueError, match="lookup event count"):
        parse_response('{"kind":"lookup_request","event_ids":["e001","e002","e003"]}',
                       bundle, 2, CONFIG_HASH, "call", POLICY)


def test_final_lookup_history_keeps_original_event_cutoff():
    bundle, engine, context = _parts()
    for index, event in enumerate(bundle.review_input.events):
        parsed = parse_response(json.dumps(_no_change(event), ensure_ascii=False),
                                bundle, index, CONFIG_HASH, f"offline_{index}", POLICY)
        context.submit(parsed.context_batch)
        assert engine.submit_batch(parsed.transition_batch).accepted
    session = HistoryLookupSession(bundle.review_input.events,
                                   DecisionPoint(event_id="e001", seq=1), POLICY)
    good = session.lookup(LookupRequest(event_ids=["e001"]))
    final = build_final_request(bundle, engine.snapshot(), context.snapshot(),
                                FINAL_PROMPT, ModelConfig(), POLICY,
                                [LookupEvidence(event_id="e001", seq=1, record=good)])
    assert final.public_input["history_lookup"]["attempts"][0]["record"]["events"][0]["event_id"] == "e001"
    late = HistoryLookupSession(bundle.review_input.events,
                                DecisionPoint(event_id="e005", seq=5), POLICY)
    bad = late.lookup(LookupRequest(event_ids=["e005"]))
    with pytest.raises(ValueError, match="lookup replay mismatch"):
        build_final_request(bundle, engine.snapshot(), context.snapshot(),
                            FINAL_PROMPT, ModelConfig(), POLICY,
                            [LookupEvidence(event_id="e001", seq=1, record=bad)])


def test_preview_replays_and_rejects_tampered_request():
    output = ROOT / "reports/local" / ("m3o-test-" + uuid4().hex)
    try:
        run = run_preview(ROOT, output, PROMPT)
        assert run["actual_model_calls"] == 0 and run["case_ids"] == list(CASES)
        assert verify_preview(ROOT, output) == run
        path = output / "c003-request.json"
        request = parse_json(path.read_text(encoding="utf-8"))
        request["public_input"]["review_input"]["events"][0]["content"] = "forged"
        write_json(path, request)
        manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
        manifest["artifacts"] = _artifacts(output)
        manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
        write_json(output / "run.json", manifest)
        with pytest.raises(ValueError, match="request replay mismatch"):
            verify_preview(ROOT, output)
    finally:
        if output.exists():
            shutil.rmtree(output)
