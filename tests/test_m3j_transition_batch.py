"""Offline atomic state batches; hand-authored inputs are mechanism checks."""

import json
from pathlib import Path

import pytest

from scripts.m3j_transition_batch import (BatchStateEngine, TransitionBatch,
                                          batch_from_response, replay_batches)
from writscope.artifacts import canonical_hash
from writscope.contracts import SourceQuote, load_bundle
from writscope.state import (AuthorizationDraft, ChangeDetails, Condition,
                             ConstraintDraft, Predicate, TransitionProposal,
                             assess_impacts)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = canonical_hash({"m3j": "offline_manual_diagnostic"})


def _bundle(case="c002"):
    return load_bundle(ROOT / "reports/m1-2026-10-18/inputs/verified_impact" / case)


def _proposal(bundle, index, operation, quote, *, constraint=None, authorization=None):
    event = bundle.review_input.events[index]
    return TransitionProposal(
        schema_version="0.1-draft", case_id=bundle.review_input.case_id,
        source_event_id=event.event_id, source_seq=event.seq,
        extractor_config_hash=CONFIG, extraction_call_id=f"extract_{event.seq:03d}",
        operation=operation, source=SourceQuote(event_id=event.event_id, quote=quote),
        target_ids=[], constraint=constraint, authorization=authorization)


def _batch(bundle, index, *proposals):
    event = bundle.review_input.events[index]
    return TransitionBatch(schema_version="0.1-draft", case_id=bundle.review_input.case_id,
                           source_event_id=event.event_id, source_seq=event.seq,
                           extractor_config_hash=CONFIG,
                           extraction_call_id=f"extract_{event.seq:03d}",
                           proposals=list(proposals))


def _constraint(bundle, name, *, contract_ref="contract"):
    return ConstraintDraft(
        constraint_id=name, subjects=[bundle.evidence.impacts[0].subject],
        predicate=Predicate(kind="field_exists", expected=True,
                            qualifiers={"contract_ref": contract_ref}),
        interpretation="resolved", supersedes=[])


def _grant(bundle, refs):
    return AuthorizationDraft(
        authorization_id="auth_v1_replace",
        subjects=[bundle.evidence.impacts[0].subject],
        change_type="replace_field",
        change_details=ChangeDetails(old_field="status", new_field="state",
                                     preserve_value_semantics=True),
        conditions=[Condition(kind="candidate_sha256",
                              value=bundle.review_input.candidate_patch.sha256,
                              resolution="unknown", evidence_refs=["patch"])],
        interpretation="resolved", constraint_refs=refs, supersedes=[])


def _engine(bundle):
    return BatchStateEngine(bundle.review_input.case_id, bundle.review_input.events,
                            CONFIG, set(bundle.registry.artifacts))


def test_two_constraints_same_event_then_local_grant_and_replay():
    bundle = _bundle()
    engine = _engine(bundle)
    first = _proposal(bundle, 0, "add_constraint", "保持旧客户端所使用的 v1 status 字段",
                      constraint=_constraint(bundle, "con_status"))
    second = _proposal(bundle, 0, "add_constraint", "响应对象只能包含 status",
                       constraint=_constraint(bundle, "con_shape"))
    assert engine.submit_batch(_batch(bundle, 0, first, second)).accepted
    for index in (1, 2, 3):
        item = _proposal(bundle, index, "no_change", bundle.review_input.events[index].content)
        assert engine.submit_batch(_batch(bundle, index, item)).accepted
    grant = _proposal(bundle, 4, "grant", bundle.review_input.events[4].content,
                      authorization=_grant(bundle, ["con_status", "con_shape"]))
    assert engine.submit_batch(_batch(bundle, 4, grant)).accepted
    state = engine.snapshot()
    assert state.through_seq == 5
    assert {item.constraint_id for item in state.constraints} == {"con_status", "con_shape"}
    assert len(state.exceptions) == 1
    assert assess_impacts(state, bundle.evidence,
                          bundle.review_input.candidate_patch.sha256).authorization == "covered"
    assert replay_batches("c002", bundle.review_input.events, CONFIG,
                          engine.batch_records, set(bundle.registry.artifacts)) == state


def test_second_invalid_item_rejects_whole_batch_and_consumes_event():
    bundle = _bundle()
    engine = _engine(bundle)
    good = _proposal(bundle, 0, "add_constraint", "保持旧客户端所使用的 v1 status 字段",
                     constraint=_constraint(bundle, "con_status"))
    bad = _proposal(bundle, 0, "add_constraint", "响应对象只能包含 status",
                    constraint=_constraint(bundle, "con_bad", contract_ref="private_label"))
    batch = _batch(bundle, 0, good, bad)
    record = engine.submit_batch(batch)
    assert not record.accepted and record.consumed_event and record.failed_item_index == 1
    assert "unknown public contract" in record.rejection_reason
    assert record.source_refs == [] and engine.snapshot().constraints == []
    assert engine.snapshot().through_seq == 1
    retry = engine.submit_batch(batch)
    assert not retry.accepted and not retry.consumed_event
    assert "stops trajectory" in retry.rejection_reason
    next_event = _proposal(bundle, 1, "no_change", bundle.review_input.events[1].content)
    assert not engine.submit_batch(_batch(bundle, 1, next_event)).consumed_event
    assert replay_batches("c002", bundle.review_input.events, CONFIG,
                          engine.batch_records, set(bundle.registry.artifacts)) == engine.snapshot()


def test_tool_cannot_create_authorization_in_batch():
    bundle = _bundle()
    engine = _engine(bundle)
    empty = _proposal(bundle, 0, "no_change", bundle.review_input.events[0].content)
    assert engine.submit_batch(_batch(bundle, 0, empty)).accepted
    fake = _proposal(bundle, 1, "grant", "状态显示失败",
                     authorization=_grant(bundle, []))
    record = engine.submit_batch(_batch(bundle, 1, fake))
    assert not record.accepted and record.failed_item_index == 0
    assert "only a user source" in record.rejection_reason
    assert not engine.snapshot().authorizations


def test_rejects_mixed_no_change_identity_and_tampered_replay():
    bundle = _bundle()
    first = _proposal(bundle, 0, "add_constraint", "保持旧客户端所使用的 v1 status 字段",
                      constraint=_constraint(bundle, "con_status"))
    no_change = _proposal(bundle, 0, "no_change", bundle.review_input.events[0].content)
    with pytest.raises(ValueError, match="only batch proposal"):
        _batch(bundle, 0, first, no_change)
    with pytest.raises(ValueError, match="identity mismatch"):
        _batch(bundle, 0, first, _proposal(bundle, 1, "no_change",
                                          bundle.review_input.events[1].content))
    engine = _engine(bundle)
    record = engine.submit_batch(_batch(bundle, 0, first))
    with pytest.raises(ValueError, match="replay mismatch"):
        replay_batches("c002", bundle.review_input.events, CONFIG,
                       [record.model_copy(update={"after_hash": "0" * 64})],
                       set(bundle.registry.artifacts))


def test_response_parser_supplies_identity_and_rejects_forged_metadata():
    bundle = _bundle()
    proposals = [
        _proposal(bundle, 0, "add_constraint", "保持旧客户端所使用的 v1 status 字段",
                  constraint=_constraint(bundle, "con_status")),
        _proposal(bundle, 0, "add_constraint", "响应对象只能包含 status",
                  constraint=_constraint(bundle, "con_shape")),
    ]
    items = [{"operation": item.operation, "source_quote": item.source.quote,
              "target_ids": item.target_ids,
              "constraint": item.constraint.model_dump(), "authorization": None}
             for item in proposals]
    parsed = batch_from_response(json.dumps({"items": items}), bundle, 0, CONFIG,
                                 "extract_001")
    assert parsed.proposals == proposals
    items[0]["source_event_id"] = "e999"
    with pytest.raises(ValueError):
        batch_from_response(json.dumps({"items": items}), bundle, 0, CONFIG,
                            "extract_001")
