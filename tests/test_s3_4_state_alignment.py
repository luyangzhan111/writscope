"""Evaluator-only reference compilation and local-exception identity checks."""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_4_state_alignment as diagnostic
from writscope.artifacts import canonical_hash, write_json
from writscope.contracts import load_bundle

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "evaluation_private/case_001"
OBSERVATIONS = {
    "c001": ROOT / "reports/local/m3c-live-20261021",
    "c002": ROOT / "reports/local/m3d-c002-v2-live-20261021",
}


def _annotation(case="c002"):
    return diagnostic._read(PRIVATE / f"s3_4_{case}_annotation_v2.json")


def _bundle(case="c002"):
    return load_bundle(ROOT / "reports/m1-2026-10-18/inputs/verified_impact" / case)


def _rehash(annotation):
    annotation["annotation_hash"] = canonical_hash({
        key: value for key, value in annotation.items() if key != "annotation_hash"})


@pytest.mark.parametrize("case", ["c001", "c002"])
def test_full_reference_compiles_and_observation_replays_without_provider(monkeypatch, case):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider must never be constructed")

    monkeypatch.setattr(diagnostic.m3c, "DeepSeekProvider", forbidden)
    monkeypatch.setattr(diagnostic.m3d_gate, "DeepSeekProvider", forbidden)
    output = ROOT / "reports/local" / f"s3-4-state-test-{uuid4().hex}"
    annotation_path = PRIVATE / f"s3_4_{case}_annotation_v2.json"
    report = diagnostic.run_diagnostic(ROOT, OBSERVATIONS[case], annotation_path, case, output)
    assert report["new_model_calls"] == 0
    assert report["original_case_model_calls"] == 5
    assert report["oracle_conditioned_model_review"] == "not_run"
    assert not any("state_update_or_policy_error_candidate" in row["labels"]
                   for row in report["prefixes"])
    assert report["prefixes"][0]["difference"]["changed"][0]["fields"] == ["predicate"]
    assert report["prefixes"][-1]["candidate_projection_matches"]
    if case == "c002":
        assert report["prefixes"][-1]["exactly_matched_facts_by_kind"] == {
            "authorization": 1, "exception": 1}
        assert report["reference_deterministic_scope_check"]["decision"] == "allow"
    else:
        assert report["reference_deterministic_scope_check"]["decision"] == "revise"
    assert diagnostic.verify_diagnostic(ROOT, OBSERVATIONS[case], annotation_path,
                                         case, output) == report
    trace = diagnostic._read(output / "evaluator_trace.json")
    trace["reference"]["prefixes"][-1]["state"]["constraints"] = []
    write_json(output / "evaluator_trace.json", trace)
    with pytest.raises(ValueError, match="saved v2 state diagnostic replay mismatch"):
        diagnostic.verify_diagnostic(ROOT, OBSERVATIONS[case], annotation_path, case, output)


def test_local_exception_graph_is_invariant_to_all_runtime_id_renames():
    state = diagnostic._read(OBSERVATIONS["c002"] / "c002/state.json")
    renamed = deepcopy(state)
    renamed["constraints"][0]["constraint_id"] = "different_constraint_id"
    renamed["authorizations"][0]["authorization_id"] = "different_auth_id"
    renamed["authorizations"][0]["constraint_refs"] = ["different_constraint_id"]
    renamed["exceptions"][0].update(exception_id="different_exception_id",
                                    constraint_ids=["different_constraint_id"],
                                    authorization_id="different_auth_id")
    assert diagnostic.project_graph(state)["facts"] == diagnostic.project_graph(renamed)["facts"]
    assert diagnostic.project_graph(state)["ids"] != diagnostic.project_graph(renamed)["ids"]
    # Also rename reference proposals; independent expected fact symbols stay fixed.
    annotation = _annotation()
    annotation["events"][0]["proposals"][0]["constraint"]["constraint_id"] = "reference_new_id"
    draft = annotation["events"][-1]["proposals"][0]["authorization"]
    draft["authorization_id"] = "reference_new_auth_id"
    draft["constraint_refs"] = ["reference_new_id"]
    _rehash(annotation)
    compiled = diagnostic.compile_reference(annotation, _bundle(), ROOT)
    assert compiled["deterministic_scope_check"]["decision"] == "allow"


def test_alignment_refuses_ambiguous_dangling_and_wrong_type_id_edges():
    state = diagnostic._read(OBSERVATIONS["c002"] / "c002/state.json")
    ambiguous = deepcopy(state)
    extra = deepcopy(ambiguous["constraints"][0])
    extra["constraint_id"] = "another_id_same_source_scope"
    extra["predicate"]["qualifiers"] = {"different_representation": True}
    ambiguous["constraints"].append(extra)
    with pytest.raises(ValueError, match="alignment_ambiguous"):
        diagnostic.project_graph(ambiguous)
    for target in ("nonexistent", state["authorizations"][0]["authorization_id"]):
        invalid = deepcopy(state)
        invalid["authorizations"][0]["constraint_refs"] = [target]
        with pytest.raises(ValueError, match="wrong-type reference"):
            diagnostic.project_graph(invalid)
    revoked = deepcopy(state)
    revoked["authorizations"][0]["lifecycle"] = "revoked"
    with pytest.raises(ValueError, match="lifecycle update requires a new contract"):
        diagnostic.project_graph(revoked)


def test_projection_preserves_version_and_candidate_condition_differences():
    state = diagnostic._read(OBSERVATIONS["c002"] / "c002/state.json")
    original = diagnostic.project_graph(state)
    changed = deepcopy(state)
    changed["authorizations"][0]["conditions"][0]["value"] = "0" * 64
    changed["exceptions"][0]["conditions"][0]["value"] = "0" * 64
    delta = diagnostic._difference(diagnostic.project_graph(changed), original)
    assert {row["kind"] for row in delta["changed"]} == {"authorization", "exception"}
    assert all(row["fields"] == ["conditions"] for row in delta["changed"])
    changed = deepcopy(state)
    changed["authorizations"][0]["subjects"][0]["api_version"] = "v2"
    delta = diagnostic._difference(diagnostic.project_graph(changed), original)
    assert delta["missing_anchors"] and delta["extra_anchors"]


def test_independent_expected_state_future_fact_and_forged_quote_are_checked():
    annotation = _annotation()
    missing_exception = deepcopy(annotation)
    missing_exception["events"][-1]["expected_poststate_facts"].remove("exception_current_v1")
    _rehash(missing_exception)
    with pytest.raises(ValueError, match="oracle_compile_mismatch at e005"):
        diagnostic.compile_reference(missing_exception, _bundle(), ROOT)
    future = deepcopy(annotation)
    future["events"][0]["expected_poststate_facts"].append("auth_current_v1")
    _rehash(future)
    with pytest.raises(ValueError, match="future expected fact"):
        diagnostic.compile_reference(future, _bundle(), ROOT)
    forged = deepcopy(annotation)
    forged["fact_catalog"]["con_v1_exact_response"]["source_quote"] = "not a public quote"
    _rehash(forged)
    with pytest.raises(ValueError, match="unique trusted user source quote"):
        diagnostic.compile_reference(forged, _bundle(), ROOT)


def test_original_observation_verification_precedes_private_read(monkeypatch):
    def fail(*_args, **_kwargs):
        raise ValueError("original integrity failure")

    monkeypatch.setattr(diagnostic.m3d_gate, "verify_run", fail)
    with pytest.raises(ValueError, match="original integrity failure"):
        diagnostic.build_report(ROOT, OBSERVATIONS["c002"], PRIVATE / "absent.json", "c002")
