"""V3 lifecycle preservation, identity controls, and corruption rejection."""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_4_lifecycle_alignment as diagnostic
from writscope.artifacts import canonical_hash, write_json
from writscope.contracts import load_bundle

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/local/s3-4-semantic-boundary-20261025"


def _snapshot(branch_id: str, index: int = -1):
    source = diagnostic._read(SOURCE / "audit.json")
    fixture = diagnostic._read(ROOT / "evaluation_private/case_001/s3_4_lifecycle_boundaries_v1.json")
    branch = next(row for row in source["synthetic_boundary_branches"] if row["branch_id"] == branch_id)
    fixture_branch = next(row for row in fixture["branches"] if row["branch_id"] == branch_id)
    bundle = load_bundle(ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c002")
    return deepcopy(branch["rows"][index]["state"]), diagnostic._trusted_events(bundle, fixture_branch)


def _rehash(state: dict):
    value = canonical_hash({key: value for key, value in state.items() if key not in {"state_id", "state_hash"}})
    state["state_hash"], state["state_id"] = value, "state_" + value[:16]


def test_six_branches_align_after_independent_id_renaming_without_provider(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr(diagnostic.source_audit.alignment.m3c, "DeepSeekProvider", forbidden)
    monkeypatch.setattr(diagnostic.source_audit.alignment.m3d_gate, "DeepSeekProvider", forbidden)
    output = ROOT / "reports/local" / f"s3-4-v3-test-{uuid4().hex}"
    report = diagnostic.run_diagnostic(ROOT, SOURCE, output)
    assert report["new_model_calls"] == 0
    assert report["aligned_prefix_pairs"] == 37
    assert all(row["graphs_equal"] for branch in report["branches"] for row in branch["prefix_pairs"])
    assert report["branches"][0]["prefix_pairs"][-1]["exception_gates"] == {
        "authorization_revoked": 1, "active_scope_candidate": 1}
    assert report["branches"][1]["prefix_pairs"][-1]["exception_gates"] == {
        "authorization_superseded": 1, "active_scope_candidate": 1}
    assert report["branches"][2]["prefix_pairs"][-1]["exception_gates"] == {"authorization_ambiguous": 1}
    assert report["branches"][3]["prefix_pairs"][-1]["exception_gates"] == {"constraint_inactive": 1}
    assert all(row["terminal_scope_check"] is None and row["not_attempted_extensions"] == 1
               for row in report["branches"][4:])
    assert diagnostic.verify_diagnostic(ROOT, SOURCE, output) == report
    trace = diagnostic._read(output / "evaluator_graphs.json")
    control = trace["branches"][0]["extension_graphs"][0]
    assert control["original"]["ids"] != control["renamed"]["ids"]
    assert control["original"]["graph"] == control["renamed"]["graph"]
    # Rewrite the stored graph AND its hash; deterministic replay must still reject.
    control["original"]["graph"]["edges"] = []
    control["original"]["graph_sha256"] = canonical_hash(control["original"]["graph"])
    write_json(output / "evaluator_graphs.json", trace)
    with pytest.raises(ValueError, match="saved v3 lifecycle alignment replay mismatch"):
        diagnostic.verify_diagnostic(ROOT, SOURCE, output)


def test_history_cannot_be_dropped_from_revocation_or_supersession():
    state, events = _snapshot("b001", 0)
    changed = deepcopy(state)
    changed["exceptions"] = []
    _rehash(changed)
    with pytest.raises(ValueError, match="missing historical local exception"):
        diagnostic.project_graph(changed, events)
    changed = deepcopy(state)
    changed["authorizations"][0]["updated_by"] = []
    _rehash(changed)
    with pytest.raises(ValueError, match="unsupported lifecycle history"):
        diagnostic.project_graph(changed, events)
    state, events = _snapshot("b002")
    new = next(item for item in state["authorizations"] if item["supersedes"])
    new["supersedes"] = []
    _rehash(state)
    with pytest.raises(ValueError, match="lacks a unique successor"):
        diagnostic.project_graph(state, events)


def test_typed_edges_temporal_sources_and_cycles_are_rejected():
    state, events = _snapshot("b002")
    changed = deepcopy(state)
    new = next(item for item in changed["authorizations"] if item["supersedes"])
    new["supersedes"] = [changed["constraints"][0]["constraint_id"]]
    _rehash(changed)
    with pytest.raises(ValueError, match="wrong-type reference"):
        diagnostic.project_graph(changed, events)
    changed = deepcopy(state)
    new = next(item for item in changed["authorizations"] if item["supersedes"])
    new["supersedes"] = [new["authorization_id"]]
    _rehash(changed)
    with pytest.raises(ValueError, match="supersede time, status, or scope"):
        diagnostic.project_graph(changed, events)
    state, events = _snapshot("b001", 0)
    state["authorizations"][0]["updated_by"] = ["e007"]
    _rehash(state)
    with pytest.raises(ValueError, match="update source, order, or future event"):
        diagnostic.project_graph(state, events)
    state, events = _snapshot("b006")
    state["authorizations"][0]["updated_by"] = ["e006"]  # trusted role is tool
    _rehash(state)
    with pytest.raises(ValueError, match="update source, order, or future event"):
        diagnostic.project_graph(state, events)


def test_valid_snapshot_projection_preserves_status_ambiguity_and_conditions():
    for branch_id, field, value in (("b001", "lifecycle", "active"),
                                    ("b003", "interpretation", "resolved")):
        state, events = _snapshot(branch_id, 0)
        original = diagnostic.project_graph(state, events)
        changed = deepcopy(state)
        changed["authorizations"][0][field] = value
        _rehash(changed)
        assert diagnostic.project_graph(changed, events)["graph"] != original["graph"]
    state, events = _snapshot("b001")
    original = diagnostic.project_graph(state, events)
    auth = next(item for item in state["authorizations"] if item["lifecycle"] == "active")
    auth["conditions"][0]["value"] = "0" * 64
    exception = next(item for item in state["exceptions"] if item["authorization_id"] == auth["authorization_id"])
    exception["conditions"][0]["value"] = "0" * 64
    _rehash(state)
    assert diagnostic.project_graph(state, events)["graph"] != original["graph"]


def test_multiple_lifecycle_reasons_remain_visible():
    state, events = _snapshot("b004")
    state["authorizations"][0]["interpretation"] = "ambiguous"
    _rehash(state)
    graph = diagnostic.project_graph(state, events)["graph"]
    exception = next(item for item in graph["facts"] if item["kind"] == "exception")
    assert exception["lifecycle_reasons"] == ["authorization_ambiguous", "constraint_inactive"]


def test_source_replay_precedes_lifecycle_graph_analysis(monkeypatch):
    def fail(*_args, **_kwargs):
        raise ValueError("source audit integrity failure")

    monkeypatch.setattr(diagnostic.source_audit, "verify_audit", fail)
    with pytest.raises(ValueError, match="source audit integrity failure"):
        diagnostic.build_report(ROOT, ROOT / "reports/local/absent-source")
