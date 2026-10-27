"""Preparation integrity, reference isolation, and untriggered lookup semantics."""

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_independent_review_pack as review
from scripts import s3_lookup_offline_preflight as lookup
from writscope.artifacts import digest, write_json
from writscope.providers import deepseek

ROOT = Path(__file__).resolve().parents[1]


def test_review_package_is_unanswered_and_response_edits_do_not_claim_acceptance():
    output = ROOT / "reports/local" / f"s3-review-test-{uuid4().hex}"
    manifest = review.prepare(ROOT, output)
    assert review.verify(ROOT, output) == manifest
    template = review._read(output / "reviewer_response.template.json")
    assert template["review_status"] == "pending"
    assert len(template["shared_prefix_reviews"]) == 4 and len(template["branch_reviews"]) == 3
    assert all(row["constraints"] is None for row in template["shared_prefix_reviews"])
    assert all(row["terminal"]["authorization"] is None for row in template["branch_reviews"])
    response = deepcopy(template)
    response["review_status"] = "self_claimed_complete"
    response["reviewer"]["reviewer_id"] = "some_reviewer"
    write_json(output / review.RESPONSE, response)
    assert review.verify(ROOT, output)["response_assessed"] is False
    # Rehashing tampered frozen inputs cannot turn a future event into a valid prefix.
    target = output / "stage1/shared_prefix_001.json"
    changed = review._read(target)
    future = review._read(output / "stage2/c003_after_e005.json")["review_input"]["events"][-1]
    changed["review_input"]["events"].append(future)
    write_json(target, changed)
    manifest["artifacts"]["stage1/shared_prefix_001.json"] = digest(target.read_bytes())
    write_json(output / "run.json", manifest)
    with pytest.raises(ValueError, match="public source/template replay mismatch"):
        review.verify(ROOT, output)


def test_preparers_never_read_reference_answers_or_construct_provider(monkeypatch):
    original_read = Path.read_text

    def public_only(path, *args, **kwargs):
        assert "evaluation_private" not in str(path)
        assert path.name != ".env"
        return original_read(path, *args, **kwargs)

    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr(Path, "read_text", public_only)
    monkeypatch.setattr(deepseek, "DeepSeekProvider", forbidden_provider)
    review_files = review.build_files(ROOT)
    lookup_files = lookup.build_files(ROOT)
    for name, payload in review_files.items():
        if name.startswith(("stage1/", "stage2/")):
            text = json.dumps(payload, ensure_ascii=False)
            assert "expected_authorization" not in text and "EVALUATOR_ONLY_M1_REFERENCE" not in text
    assert lookup_files["plan.json"]["new_authorization"] is None


def test_lookup_transport_preflight_preserves_prefixes_and_stops_faults():
    files = lookup.build_files(ROOT)
    plan = files["plan.json"]
    assert plan["status"] == "draft_unapproved" and not plan["paid_run_ready"]
    assert plan["price_ceiling"] is None and plan["local_cap_cny"] is None
    assert not plan["old_authorizations_reusable"] and plan["real_runner_binding"] is None
    cases = {row["name"]: row for row in files["transport_cases.json"]["cases"]}
    assert [cases[name]["simulated_requests_built"] for name in ("no_lookup", "one_lookup", "two_lookups")] == [6, 7, 8]
    assert [cases[name]["scripted_lookup_requests"] for name in ("future_lookup", "unknown_lookup", "lookup_limit")] == [1, 1, 3]
    assert all(not cases[name]["final_request_prepared"] for name in ("future_lookup", "unknown_lookup", "lookup_limit"))
    assert [cases[name]["accepted_events"] for name in ("future_lookup", "unknown_lookup", "lookup_limit")] == [3, 4, 4]
    for case in cases.values():
        assert case["actual_provider_attempts"] == 0 and case["evidence_classification"] == "offline_transport_only"
        for call in case["requests"]:
            visible = call["request"]["public_input"]["review_input"]["events"]
            assert len(visible) == (call["event_seq"] or 5)
            assert visible[-1]["seq"] == len(visible)
            assert call["wire_bytes"] <= lookup.MAX_BYTES


def test_lookup_preflight_replay_rejects_forged_approval_even_with_updated_hash():
    output = ROOT / "reports/local" / f"s3-lookup-test-{uuid4().hex}"
    manifest = lookup.prepare(ROOT, output)
    assert lookup.verify(ROOT, output) == manifest
    plan = lookup._read(output / "plan.json")
    plan["paid_run_ready"] = True
    plan["new_authorization"] = {"claimed_approved": True}
    write_json(output / "plan.json", plan)
    manifest["artifacts"]["plan.json"] = digest((output / "plan.json").read_bytes())
    write_json(output / "run.json", manifest)
    with pytest.raises(ValueError, match="deterministic replay mismatch"):
        lookup.verify(ROOT, output)


@pytest.mark.parametrize("kind,trigger,completed,requests,succeeded,continuations,final_valid,expected", [
    ("offline_scripted", "model_selected", True, 1, 1, 1, True, "offline_transport_only"),
    ("real_model", "model_selected", True, 0, 0, 0, True, "not_exercised"),
    ("real_model", "model_selected", True, 1, 1, 1, True, "lookup_branch_exercised"),
    ("real_model", "model_selected", False, 1, 1, 0, False, "failed_or_incomplete_preserved"),
    ("real_model", "model_selected", True, 1, 1, 0, True, "incomplete_lookup_evidence"),
    ("real_model", "forced_control", True, 1, 1, 1, True, "prompted_transport_control_only"),
])
def test_lookup_outcome_classification_does_not_promote_scripted_or_unused_path(
        kind, trigger, completed, requests, succeeded, continuations, final_valid, expected):
    assert lookup.classify_lookup_evidence(execution_kind=kind, trigger_policy=trigger, completed=completed,
        requests=requests, succeeded=succeeded, continuations=continuations, final_valid=final_valid) == expected


def test_expected_fault_cannot_swallow_an_unrelated_parser_failure(monkeypatch):
    def unrelated(*_args, **_kwargs):
        raise ValueError("unrelated underlying defect")

    monkeypatch.setattr(lookup, "parse_response", unrelated)
    with pytest.raises(ValueError, match="unrelated underlying defect"):
        lookup.build_files(ROOT)
