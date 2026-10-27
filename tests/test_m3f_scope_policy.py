"""Mechanism checks for the new offline scope policy, without real calls."""

from scripts.m3f_scope_policy import ScopeValidatedStateEngine
from test_m3a_state import CONFIG_HASH, bundle_for, manual_proposals


def _engine(bundle):
    return ScopeValidatedStateEngine(bundle.review_input.case_id,
                                     bundle.review_input.events, CONFIG_HASH,
                                     set(bundle.registry.artifacts))


def test_c002_local_exception_and_c003_future_grant_still_work(root):
    c002 = bundle_for(root, "c002")
    engine = _engine(c002)
    assert all(engine.submit(row).transition.accepted for row in manual_proposals(c002))
    assert len(engine.snapshot().exceptions) == 1

    c003 = bundle_for(root, "c003")
    engine = _engine(c003)
    assert all(engine.submit(row).transition.accepted for row in manual_proposals(c003))
    assert not engine.snapshot().exceptions


def test_cross_version_ref_and_wrong_old_field_are_rejected_together(root):
    bundle = bundle_for(root, "c003")
    proposals = manual_proposals(bundle)
    v2 = proposals[-1].authorization.subjects[0]
    bad_auth = proposals[-1].authorization.model_copy(update={
        "constraint_refs": ["con_v1_status"],
        "subjects": [v2.model_copy(update={"field": "state"})],
    })
    proposals[-1] = proposals[-1].model_copy(update={"authorization": bad_auth})
    engine = _engine(bundle)
    assert all(engine.submit(row).transition.accepted for row in proposals[:-1])
    rejected = engine.submit(proposals[-1])
    assert not rejected.transition.accepted
    assert "constraint reference scope mismatch" in rejected.transition.rejection_reason
    assert "subject field differs from old_field" in rejected.transition.rejection_reason
    state = engine.snapshot()
    assert state.through_seq == 5
    assert state.constraints[0].lifecycle == "active"
    assert state.authorizations == []
    assert state.exceptions == []


def test_partial_exception_can_reference_broad_multisubject_constraint(root):
    bundle = bundle_for(root, "c002")
    proposals = manual_proposals(bundle)
    v1 = proposals[0].constraint.subjects[0]
    v2 = v1.model_copy(update={"path": "/api/v2/profile", "api_version": "v2"})
    broader = proposals[0].constraint.model_copy(update={"subjects": [v1, v2]})
    proposals[0] = proposals[0].model_copy(update={"constraint": broader})
    engine = _engine(bundle)
    assert all(engine.submit(row).transition.accepted for row in proposals)
    assert len(engine.snapshot().exceptions) == 1


def test_inactive_constraint_cannot_receive_a_new_exception(root):
    bundle = bundle_for(root, "c002")
    proposals = manual_proposals(bundle)
    engine = _engine(bundle)
    assert all(engine.submit(row).transition.accepted for row in proposals[:-1])
    old = engine.constraints["con_v1_status"]
    engine.constraints["con_v1_status"] = old.model_copy(update={"lifecycle": "superseded"})
    rejected = engine.submit(proposals[-1])
    assert not rejected.transition.accepted
    assert "constraint reference is inactive" in rejected.transition.rejection_reason
    assert engine.snapshot().authorizations == []
