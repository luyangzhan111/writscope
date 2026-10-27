"""M3m uses only replayed public data and caller-supplied scripted responses."""

import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from test_m3l_offline_trace import _plan
from scripts.m3l_offline_trace import run_offline as run_trace
from scripts.m3m_final_review import run_offline, verify_run
from writscope.artifacts import canonical_hash, write_json
from writscope.baselines import build_request as build_baseline
from writscope.contracts import load_bundle, parse_json
from writscope.m3b import _artifacts
from writscope.providers import ModelConfig


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c002"
PROMPT = (ROOT / "prompts/incremental_final_review_v1.md").read_text(encoding="utf-8")


def _paths():
    stem = "m3m-test-" + uuid4().hex
    return ROOT / "reports/local" / (stem + "-trace"), ROOT / "reports/local" / (stem + "-review")


def _response(state):
    bundle = load_bundle(BUNDLE)
    subject = bundle.evidence.impacts[0].subject.model_dump()
    items = []
    for kind, records, key in (("constraint", state["constraints"], "constraint_id"),
                               ("authorization", state["authorizations"], "authorization_id")):
        for entry in records:
            items.append({"item_id": entry[key], "kind": kind,
                          "subjects": entry["subjects"], "predicate": "scripted state item",
                          "change_types": ["replace_field"], "conditions": [],
                          "lifecycle": entry["lifecycle"],
                          "interpretation": entry["interpretation"],
                          "source_refs": [{"event_id": entry["source_event_id"],
                                           "quote": entry["source_quote"]}]})
    source = {"event_id": "e005", "quote": state["authorizations"][0]["source_quote"]}
    return {"impact": "breaking", "authorization": "covered", "decision": "allow",
            "affected_subjects": [subject],
            "per_impact_authorization": [{"impact_id": "impact_001", "verdict": "covered",
                                          "constraint_refs": ["con_status", "con_shape"],
                                          "authorization_refs": ["auth_v1_replace"],
                                          "source_refs": [source], "reason": "scripted coverage"}],
            "constraint_refs": ["con_status", "con_shape"],
            "authorization_refs": ["auth_v1_replace"],
            "source_refs": [source], "evidence_refs": ["impact_001"],
            "reason": "scripted structural fixture", "limitations": ["No model call"],
            "derived_items": items, "checklist": None}


def _clean(*paths):
    for path in paths:
        if path.exists():
            shutil.rmtree(path)


def test_request_and_scripted_result_replay_with_equal_public_evidence():
    trace, output = _paths()
    try:
        run_trace(BUNDLE, _plan(), trace)
        response = json.dumps(_response(parse_json((trace / "state.json").read_text(encoding="utf-8"))),
                              ensure_ascii=False)
        manifest = run_offline(BUNDLE, trace, output, PROMPT, response)
        assert manifest["status"] == "validated_scripted_response"
        assert manifest["new_model_calls"] == 0
        request = parse_json((output / "request.json").read_text(encoding="utf-8"))
        baseline = build_baseline(load_bundle(BUNDLE), "full_direct", "baseline", ModelConfig())
        assert request["evidence_sha256"] == baseline.evidence_sha256
        assert set(request["public_input"]) == {"review_input", "artifacts", "artifact_registry",
                                                "current_state", "task_context", "history_lookup"}
        assert "evaluation_private" not in json.dumps(request)
        assert request["public_input"]["history_lookup"]["attempts"]
        assert verify_run(BUNDLE, trace, output) == manifest
    finally:
        _clean(trace, output)


@pytest.mark.parametrize("change,fragment", [
    ("offset", "forbids model-supplied offsets"),
    ("invented_state", "outside current state"),
])
def test_invalid_response_saved_without_result(change, fragment):
    trace, output = _paths()
    try:
        run_trace(BUNDLE, _plan(), trace)
        response = _response(parse_json((trace / "state.json").read_text(encoding="utf-8")))
        if change == "offset":
            response["source_refs"][0]["start"] = 0
        else:
            response["derived_items"][0]["item_id"] = "invented"
            response["constraint_refs"][1] = "invented"
            response["per_impact_authorization"][0]["constraint_refs"][1] = "invented"
        manifest = run_offline(BUNDLE, trace, output, PROMPT,
                               json.dumps(response, ensure_ascii=False))
        assert manifest["status"] == "invalid_response"
        assert fragment in manifest["error"]
        assert not (output / "result.json").exists()
        assert verify_run(BUNDLE, trace, output) == manifest
    finally:
        _clean(trace, output)


def test_incomplete_trace_has_no_request_or_result():
    trace, output = _paths()
    try:
        run_trace(BUNDLE, _plan(bad_response_at=3), trace)
        manifest = run_offline(BUNDLE, trace, output, PROMPT, "{}")
        assert manifest["status"] == "no_final_review"
        assert not (output / "request.json").exists()
        assert not (output / "response.txt").exists()
        assert verify_run(BUNDLE, trace, output) == manifest
    finally:
        _clean(trace, output)


def test_request_tampering_fails_replay_even_after_hash_rewrite():
    trace, output = _paths()
    try:
        run_trace(BUNDLE, _plan(), trace)
        run_offline(BUNDLE, trace, output, PROMPT)
        path = output / "request.json"
        request = parse_json(path.read_text(encoding="utf-8"))
        request["public_input"]["current_state"]["constraints"] = []
        write_json(path, request)
        manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
        manifest["artifacts"] = _artifacts(output)
        manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
        write_json(output / "run.json", manifest)
        with pytest.raises(ValueError, match="request replay mismatch"):
            verify_run(BUNDLE, trace, output)
    finally:
        _clean(trace, output)
