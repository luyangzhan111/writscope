"""M3a offline diagnostics; hand-authored proposals are not extractor results."""

import json

import pytest

from writscope.artifacts import canonical_hash
from writscope.contracts import Impact, ImpactEvidence, SourceQuote, Subject, load_bundle
from writscope.events import ConversationEvent, Provenance
from writscope.m3a import run, verify_run
from writscope.state import (AuthorizationDraft, ChangeDetails, Condition,
                             ConstraintDraft, Predicate, StateEngine,
                             TransitionProposal, assess_impacts, replay_entries)


CONFIG_HASH = canonical_hash({"extractor": "manual_m3a_diagnostic_v1"})


def bundle_for(root, case):
    return load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact" / case)


def proposal(case, event, operation, *, quote=None, targets=None, constraint=None, authorization=None):
    return TransitionProposal(schema_version="0.1-draft", case_id=case,
                              source_event_id=event.event_id, source_seq=event.seq,
                              extractor_config_hash=CONFIG_HASH, extraction_call_id=None,
                              operation=operation,
                              source=SourceQuote(event_id=event.event_id,
                                                 quote=quote or event.content),
                              target_ids=targets or [], constraint=constraint,
                              authorization=authorization)


def constraint_for(subject):
    return ConstraintDraft(constraint_id="con_v1_status", subjects=[subject],
                           predicate=Predicate(kind="field_exists", expected=True,
                                               qualifiers={"contract_ref": "contract"}),
                           interpretation="resolved", supersedes=[])


def grant_for(subject, patch_sha, *, auth_id="auth_v1_replace", change="replace_field",
              constraint_refs=None, interpretation="resolved", condition_kind="candidate_sha256"):
    return AuthorizationDraft(
        authorization_id=auth_id, subjects=[subject], change_type=change,
        change_details=ChangeDetails(old_field=subject.field, new_field="state",
                                     preserve_value_semantics=True),
        conditions=[Condition(kind=condition_kind, value=patch_sha,
                              resolution="unknown", evidence_refs=["patch"])],
        interpretation=interpretation, constraint_refs=constraint_refs or [], supersedes=[],
    )


def manual_proposals(bundle, *, variant=None):
    inp = bundle.review_input
    case = inp.case_id
    events = inp.events
    subject = bundle.evidence.impacts[0].subject
    rows = [proposal(case, events[0], "add_constraint", constraint=constraint_for(subject))]
    rows.extend(proposal(case, event, "no_change") for event in events[1:4])
    variant = variant or case
    if variant == "c001":
        rows.append(proposal(case, events[4], "no_change"))
    elif variant == "c002":
        rows.append(proposal(case, events[4], "grant", authorization=grant_for(
            subject, inp.candidate_patch.sha256, constraint_refs=["con_v1_status"])))
    elif variant == "c003":
        v2 = subject.model_copy(update={"path": "/api/v2/profile", "api_version": "v2"})
        rows.append(proposal(case, events[4], "grant", authorization=grant_for(
            v2, inp.candidate_patch.sha256, auth_id="auth_v2_replace")))
    else:
        raise AssertionError(variant)
    return rows


def engine_for(bundle, proposals):
    engine = StateEngine(bundle.review_input.case_id, bundle.review_input.events, CONFIG_HASH,
                         set(bundle.registry.artifacts))
    entries = [engine.submit(row) for row in proposals]
    return engine, entries


@pytest.mark.parametrize("case,expected", [
    ("c001", ("breaking", "not_covered", "revise")),
    ("c002", ("breaking", "covered", "allow")),
    ("c003", ("breaking", "not_covered", "revise")),
])
def test_first_case_structural_coverage_and_deterministic_replay(root, case, expected):
    bundle = bundle_for(root, case)
    engine, entries = engine_for(bundle, manual_proposals(bundle))
    assert all(entry.transition.accepted for entry in entries)
    state = engine.snapshot()
    assert state.through_seq == 5
    assert state.constraints[0].lifecycle == "active"
    result = assess_impacts(state, bundle.evidence, bundle.review_input.candidate_patch.sha256)
    assert (result.impact, result.authorization, result.decision) == expected
    assert result.per_impact[0].constraint_refs == ["con_v1_status"]
    assert replay_entries(case, bundle.review_input.events, CONFIG_HASH, entries) == state
    if case == "c002":
        assert len(state.exceptions) == 1
        assert state.exceptions[0].constraint_ids == ["con_v1_status"]
        assert result.per_impact[0].authorization_refs == ["auth_v1_replace"]
    if case == "c003":
        assert state.authorizations[0].subjects[0].api_version == "v2"
        assert not state.exceptions


def test_revocation_regrant_preserves_broad_constraint_and_history(root):
    bundle = bundle_for(root, "c002")
    events = [*bundle.review_input.events]
    for seq, content in ((6, "撤销本次 v1 status 替换授权。"),
                         (7, "重新允许本次 v1 status 替换为 state。")):
        events.append(ConversationEvent(schema_version="0.1-draft", event_id=f"e{seq:03d}",
                                        seq=seq, role="user", content=content,
                                        provenance=Provenance(origin="synthetic_fixture",
                                                              authority_level="user", artifact_refs=[])))
    engine = StateEngine("c002", events, CONFIG_HASH)
    rows = manual_proposals(bundle)
    for row in rows:
        assert engine.submit(row).transition.accepted
    patch = bundle.review_input.candidate_patch.sha256
    assert assess_impacts(engine.snapshot(), bundle.evidence, patch).authorization == "covered"
    assert engine.submit(proposal("c002", events[5], "revoke",
                                  targets=["auth_v1_replace"])).transition.accepted
    revoked = engine.snapshot()
    assert assess_impacts(revoked, bundle.evidence, patch).authorization == "not_covered"
    assert revoked.constraints[0].lifecycle == "active"
    assert revoked.authorizations[0].lifecycle == "revoked"
    assert len(revoked.exceptions) == 1  # Historical exception remains, but is inactive.
    new_grant = grant_for(bundle.evidence.impacts[0].subject, patch,
                          auth_id="auth_v1_regrant", constraint_refs=["con_v1_status"])
    assert engine.submit(proposal("c002", events[6], "grant",
                                  authorization=new_grant)).transition.accepted
    restored = engine.snapshot()
    assert assess_impacts(restored, bundle.evidence, patch).authorization == "covered"
    assert len(restored.exceptions) == 2
    assert replay_entries("c002", events, CONFIG_HASH, engine.records) == restored


def test_tool_spoof_rejected_and_does_not_create_authorization(root):
    bundle = bundle_for(root, "c002")
    events = bundle.review_input.events
    engine = StateEngine("c002", events, CONFIG_HASH)
    assert engine.submit(manual_proposals(bundle)[0]).transition.accepted
    fake = proposal("c002", events[1], "grant", authorization=grant_for(
        bundle.evidence.impacts[0].subject, bundle.review_input.candidate_patch.sha256,
        constraint_refs=["con_v1_status"]))
    rejection = engine.submit(fake)
    assert not rejection.transition.accepted
    assert rejection.consumed_event
    assert "only a user source" in rejection.transition.rejection_reason
    for event in events[2:]:
        engine.submit(proposal("c002", event, "no_change"))
    assert not engine.snapshot().authorizations
    assert assess_impacts(engine.snapshot(), bundle.evidence,
                          bundle.review_input.candidate_patch.sha256).authorization == "not_covered"
    assert replay_entries("c002", events, CONFIG_HASH, engine.records) == engine.snapshot()


def test_unknown_public_evidence_reference_is_rejected(root):
    bundle = bundle_for(root, "c002")
    rows = manual_proposals(bundle)
    draft = rows[4].authorization
    condition = draft.conditions[0].model_copy(update={"evidence_refs": ["private_label"]})
    rows[4] = proposal("c002", bundle.review_input.events[4], "grant",
                       authorization=draft.model_copy(update={"conditions": [condition]}))
    engine, entries = engine_for(bundle, rows)
    assert all(entry.transition.accepted for entry in entries[:4])
    assert not entries[4].transition.accepted
    assert "unknown public condition evidence" in entries[4].transition.rejection_reason
    assert not engine.snapshot().authorizations


def test_future_duplicate_bad_quote_and_repeated_quote_are_rejected(root):
    bundle = bundle_for(root, "c002")
    events = bundle.review_input.events
    engine = StateEngine("c002", events, CONFIG_HASH)
    wrong = proposal("c002", events[4], "no_change")
    future = engine.submit(wrong)
    assert not future.transition.accepted and not future.consumed_event
    assert engine.snapshot().through_seq == 0
    repeated = proposal("c002", events[0], "add_constraint", quote="status",
                        constraint=constraint_for(bundle.evidence.impacts[0].subject))
    assert "not unique" in engine.submit(repeated).transition.rejection_reason
    assert not engine.snapshot().constraints
    good = proposal("c002", events[1], "no_change")
    assert engine.submit(good).transition.accepted
    before = engine.snapshot().state_hash
    duplicate = engine.submit(good)
    assert not duplicate.transition.accepted and not duplicate.consumed_event
    assert "duplicate" in duplicate.transition.rejection_reason
    assert engine.snapshot().state_hash == before
    bad = proposal("c002", events[2], "no_change", quote="不存在的伪造原文")
    assert "quote mismatch" in engine.submit(bad).transition.rejection_reason


def test_operation_version_condition_ambiguity_and_partial_coverage(root):
    bundle = bundle_for(root, "c002")
    impact = bundle.evidence.impacts[0]
    patch = bundle.review_input.candidate_patch.sha256
    base = manual_proposals(bundle)
    alias = grant_for(impact.subject, patch, change="add_alias",
                      constraint_refs=["con_v1_status"])
    base[4] = proposal("c002", bundle.review_input.events[4], "grant", authorization=alias)
    engine, _ = engine_for(bundle, base)
    assert assess_impacts(engine.snapshot(), bundle.evidence, patch).authorization == "not_covered"
    wrong_hash = "0" * 64 if patch != "0" * 64 else "1" * 64
    base[4] = proposal("c002", bundle.review_input.events[4], "grant",
                       authorization=grant_for(impact.subject, wrong_hash,
                                               constraint_refs=["con_v1_status"]))
    engine, _ = engine_for(bundle, base)
    assert assess_impacts(engine.snapshot(), bundle.evidence, patch).authorization == "not_covered"
    base[4] = proposal("c002", bundle.review_input.events[4], "grant",
                       authorization=grant_for(impact.subject, patch,
                                               constraint_refs=["con_v1_status"],
                                               condition_kind="human_review"))
    engine, _ = engine_for(bundle, base)
    assert assess_impacts(engine.snapshot(), bundle.evidence, patch).authorization == "ambiguous"
    base[4] = manual_proposals(bundle)[4]
    engine, _ = engine_for(bundle, base)
    other_subject = Subject(protocol="http", method="GET", path="/api/v1/settings",
                            api_version="v1", location="response.body", field="mode")
    second = Impact(impact_id="impact_002", subject=other_subject,
                    change_type="replace_field", before={"mode": "on"}, after={"state": "on"},
                    violated_contract_refs=["contract"], evidence_refs=["observed_candidate"])
    evidence = ImpactEvidence.model_validate({**bundle.evidence.model_dump(),
                                              "scope": [impact.subject, other_subject],
                                              "impacts": [impact.model_dump(), second.model_dump()]})
    assessed = assess_impacts(engine.snapshot(), evidence, patch)
    assert [row.verdict for row in assessed.per_impact] == ["covered", "not_covered"]
    assert (assessed.authorization, assessed.decision) == ("not_covered", "revise")


def test_ambiguous_grant_and_contradictory_condition_never_allow(root):
    bundle = bundle_for(root, "c002")
    patch = bundle.review_input.candidate_patch.sha256
    subject = bundle.evidence.impacts[0].subject
    rows = manual_proposals(bundle)
    rows[4] = proposal("c002", bundle.review_input.events[4], "grant",
                       authorization=grant_for(subject, patch,
                                               constraint_refs=["con_v1_status"],
                                               interpretation="ambiguous"))
    engine, _ = engine_for(bundle, rows)
    result = assess_impacts(engine.snapshot(), bundle.evidence, patch)
    assert (result.authorization, result.decision) == ("ambiguous", "ask")
    wrong = "0" * 64 if patch != "0" * 64 else "1" * 64
    draft = grant_for(subject, wrong, constraint_refs=["con_v1_status"])
    draft = draft.model_copy(update={"conditions": [Condition(kind="candidate_sha256",
                                                              value=wrong, resolution="satisfied",
                                                              evidence_refs=["patch"])]})
    rows[4] = proposal("c002", bundle.review_input.events[4], "grant", authorization=draft)
    engine, _ = engine_for(bundle, rows)
    result = assess_impacts(engine.snapshot(), bundle.evidence, patch)
    assert (result.authorization, result.decision) == ("not_covered", "revise")


def test_semantic_change_needs_explicit_non_preserving_grant(root):
    bundle = bundle_for(root, "c002")
    patch = bundle.review_input.candidate_patch.sha256
    subject = bundle.evidence.impacts[0].subject
    impact = Impact(impact_id="semantic_001", subject=subject, change_type="change_value_semantics",
                    before={"status": "active"}, after={"status": "disabled"},
                    violated_contract_refs=["contract"], evidence_refs=["observed_candidate"])
    evidence = ImpactEvidence.model_validate({**bundle.evidence.model_dump(),
                                              "impacts": [impact.model_dump()]})
    rows = manual_proposals(bundle)
    preserving = AuthorizationDraft(
        authorization_id="auth_semantic", subjects=[subject], change_type="change_value_semantics",
        change_details=ChangeDetails(old_field="status", new_field=None,
                                     preserve_value_semantics=True),
        conditions=[Condition(kind="candidate_sha256", value=patch, resolution="unknown",
                              evidence_refs=["patch"])],
        interpretation="resolved", constraint_refs=[], supersedes=[])
    rows[4] = proposal("c002", bundle.review_input.events[4], "grant", authorization=preserving)
    engine, _ = engine_for(bundle, rows)
    assert assess_impacts(engine.snapshot(), evidence, patch).authorization == "not_covered"
    allowed = preserving.model_copy(update={"change_details": preserving.change_details.model_copy(
        update={"preserve_value_semantics": False})})
    rows[4] = proposal("c002", bundle.review_input.events[4], "grant", authorization=allowed)
    engine, _ = engine_for(bundle, rows)
    assert assess_impacts(engine.snapshot(), evidence, patch).authorization == "covered"


def test_supersede_retains_prior_grant_and_rejects_self_target(root):
    bundle = bundle_for(root, "c002")
    events = [*bundle.review_input.events]
    for seq in (6, 7):
        events.append(ConversationEvent(schema_version="0.1-draft", event_id=f"e{seq:03d}",
                                        seq=seq, role="user", content=f"第 {seq} 条授权更新。",
                                        provenance=Provenance(origin="synthetic_fixture",
                                                              authority_level="user", artifact_refs=[])))
    engine = StateEngine("c002", events, CONFIG_HASH)
    for row in manual_proposals(bundle):
        engine.submit(row)
    patch = bundle.review_input.candidate_patch.sha256
    replacement = grant_for(bundle.evidence.impacts[0].subject, patch, auth_id="auth_replacement",
                            constraint_refs=["con_v1_status"]).model_copy(
                                update={"supersedes": ["auth_v1_replace"]})
    row = proposal("c002", events[5], "supersede", targets=["auth_v1_replace"],
                   authorization=replacement)
    assert engine.submit(row).transition.accepted
    state = engine.snapshot()
    assert {item.authorization_id: item.lifecycle for item in state.authorizations} == {
        "auth_replacement": "active", "auth_v1_replace": "superseded"}
    assert assess_impacts(state, bundle.evidence, patch).authorization == "covered"
    self_target = grant_for(bundle.evidence.impacts[0].subject, patch, auth_id="auth_self",
                            constraint_refs=["con_v1_status"]).model_copy(
                                update={"supersedes": ["auth_self"]})
    bad = engine.submit(proposal("c002", events[6], "supersede", targets=["auth_self"],
                                 authorization=self_target))
    assert not bad.transition.accepted
    assert "missing or inactive" in bad.transition.rejection_reason
    assert "auth_self" not in {item.authorization_id for item in engine.snapshot().authorizations}
    assert replay_entries("c002", events, CONFIG_HASH, engine.records) == engine.snapshot()


def test_partial_supersede_cannot_delete_broad_constraint(root):
    bundle = bundle_for(root, "c001")
    events = [*bundle.review_input.events,
              ConversationEvent(schema_version="0.1-draft", event_id="e006", seq=6,
                                role="user", content="仅更新未来 v2 的约束。",
                                provenance=Provenance(origin="synthetic_fixture",
                                                      authority_level="user", artifact_refs=[]))]
    engine = StateEngine("c001", events, CONFIG_HASH)
    for row in manual_proposals(bundle):
        engine.submit(row)
    v1 = bundle.evidence.impacts[0].subject
    v2 = v1.model_copy(update={"path": "/api/v2/profile", "api_version": "v2"})
    partial = ConstraintDraft(constraint_id="con_v2", subjects=[v2],
                              predicate=Predicate(kind="field_exists", expected=True,
                                                  qualifiers={}),
                              interpretation="resolved", supersedes=["con_v1_status"])
    entry = engine.submit(proposal("c001", events[5], "supersede",
                                   targets=["con_v1_status"], constraint=partial))
    assert not entry.transition.accepted
    assert "partial scope" in entry.transition.rejection_reason
    assert engine.snapshot().constraints[0].lifecycle == "active"


def test_ambiguous_constraint_prevents_structural_allow(root):
    bundle = bundle_for(root, "c002")
    rows = manual_proposals(bundle)
    rows[0] = proposal("c002", bundle.review_input.events[0], "add_constraint",
                       constraint=constraint_for(bundle.evidence.impacts[0].subject).model_copy(
                           update={"interpretation": "ambiguous"}))
    engine, _ = engine_for(bundle, rows)
    result = assess_impacts(engine.snapshot(), bundle.evidence,
                            bundle.review_input.candidate_patch.sha256)
    assert (result.authorization, result.decision) == ("ambiguous", "ask")
    assert result.per_impact[0].reason == "constraint_interpretation_ambiguous"


def test_compatible_and_unknown_impact_keep_decision_axes_separate(root):
    bundle = bundle_for(root, "c002")
    engine, _ = engine_for(bundle, manual_proposals(bundle))
    state = engine.snapshot()
    patch = bundle.review_input.candidate_patch.sha256
    compatible = ImpactEvidence.model_validate({**bundle.evidence.model_dump(),
                                                "assessment": "compatible", "impacts": []})
    known_safe = assess_impacts(state, compatible, patch)
    assert (known_safe.impact, known_safe.authorization, known_safe.decision) == (
        "compatible", "not_needed", "allow")
    unknown = ImpactEvidence.model_validate({**bundle.evidence.model_dump(),
                                             "assessment": "unknown", "impacts": []})
    uncertain = assess_impacts(state, unknown, patch)
    assert (uncertain.impact, uncertain.authorization, uncertain.decision) == (
        "unknown", "not_needed", "investigate")


def test_cli_log_hashes_and_replay_detect_tampering(root, tmp_path):
    bundle_path = root / "reports/m1-2026-10-18/inputs/verified_impact/c002"
    bundle = load_bundle(bundle_path)
    proposals = manual_proposals(bundle)
    proposal_path = tmp_path / "manual.jsonl"
    proposal_path.write_text("".join(json.dumps(row.model_dump(), ensure_ascii=False) + "\n"
                                     for row in proposals), encoding="utf-8")
    output = tmp_path / "run"
    manifest = run(bundle_path, proposal_path, output)
    assert manifest["status"] == "completed"
    assert manifest["cost"]["actual_model_calls"] == 0
    assert manifest["cost"]["extractor_cost_cny"] is None
    assert manifest["accepted_transitions"] == 5
    assert verify_run(bundle_path, output)["state_hash"] == manifest["state_hash"]
    with (output / "transitions.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        verify_run(bundle_path, output)


@pytest.mark.parametrize("text,expected", [
    ("{}\n", "schema rejected"),
    ("{\"schema_version\":\n", "JSON rejected"),
])
def test_invalid_proposal_schema_gets_a_record_without_state_or_model_call(root, tmp_path, text, expected):
    bundle_path = root / "reports/m1-2026-10-18/inputs/verified_impact/c001"
    proposals = tmp_path / "malformed.jsonl"
    proposals.write_text(text, encoding="utf-8")
    output = tmp_path / "rejected"
    manifest = run(bundle_path, proposals, output)
    assert manifest["status"] == "invalid_input"
    assert manifest["input_errors"] == 1
    assert manifest["state_hash"] is None
    assert manifest["cost"]["actual_model_calls"] == 0
    error = json.loads((output / "input_error.json").read_text(encoding="utf-8"))
    assert "proposal line 1 " + expected in error["reason"]
    assert verify_run(bundle_path, output)["status"] == "invalid_input"
