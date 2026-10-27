"""Offline gate/replay checks for the c004 v8-prompt diagnostic."""
import asyncio
import json
from decimal import Decimal
import os
from pathlib import Path

import pytest

from scripts import s3_acceptance_v8 as runner
from scripts import s3_acceptance_simulation_v8 as simulation

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "reports/local/s3-development-v8-preflight-final-20261026"


def test_preflight_is_single_c004_v8_scope_and_hides_prior_event_text():
    plan, previews = runner.prepare(ROOT, with_requests=True)
    request = previews["job_001"]
    c004 = runner.bundle(ROOT, "c004")
    hidden = c004.review_input.events[1].content
    instructions = " ".join(request["instructions"].split())

    assert plan["version"] == "s3_acceptance_v8"
    assert plan["status"] == "draft_unapproved"
    assert plan["authorization_scope"] == runner.SCOPE
    assert plan["case_ids"] == ["c004"]
    assert plan["policy"]["max_attempts"] == 8
    assert plan["policy"]["retries"] == 0
    assert plan["policy"]["concurrency"] == 1
    assert plan["local_cap_cny"] == "3.00"
    assert Decimal(plan["worst_case_reserved_cny"]) == Decimal("2.989824")
    assert plan["actual_model_calls"] == 0 and plan["new_authorization"] is None
    assert "never appear in `Condition.evidence_refs`" in instructions
    assert "A server/API grant requires a trusted user statement" in instructions
    assert "Never list every active constraint just to show that it remains active" in instructions
    assert "keep that condition `unknown`" in instructions
    final_prompt = " ".join((ROOT / "prompts/incremental_final_review_v2.md").read_text(encoding="utf-8").split())
    assert "Cite only trusted `user` events there" in final_prompt
    assert "top-level `source_refs` field" in final_prompt
    assert hidden not in json.dumps(request, ensure_ascii=False)
    calls = runner.read(ROOT / "reports/local/s3-development-v5-v2-lookup-20261026/calls.json")
    fifth = next(row["request"] for row in calls if row["event_seq"] == 5 and row["stage"] == "extract")
    assert {row["event_id"] for row in fifth["public_input"]["history_index"]} >= {"e002", "e003"}
    assert hidden not in json.dumps(fifth, ensure_ascii=False)


def test_v8_lookup_simulation_replays_without_model_claims():
    # Dedicated temp-under-reports output is created by the test fixture below.
    output = ROOT / "reports/local/s3-development-v8-v8-lookup-final-20261026"
    manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, output))
    summary = runner.read(output.parent / (output.name + "-report") / "summary.json")
    assert manifest["execution_kind"] == "offline_simulation"
    assert manifest["actual_model_calls"] == 0
    assert manifest["simulated_provider_attempts"] == 7
    assert summary["metrics"] is None
    assert summary["s3_development_exit"] == "not_satisfied"
    assert summary["rows"][0]["lookup_evidence"] == "offline_transport_only"


def test_v8_first_failure_and_original_v4_response_replay():
    first_error = ROOT / "reports/local/s3-development-v8-first-error-final-20261026"
    old_rejection = ROOT / "reports/local/s3-development-v8-v4-rejection-final-20261026"
    error_manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, first_error))
    rejection_manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, old_rejection))
    error_calls = runner.read(first_error / "calls.json")
    rejection_calls = runner.read(old_rejection / "calls.json")
    assert error_manifest["status"] == "completed_with_job_failures" and len(error_calls) == 1
    assert rejection_manifest["status"] == "completed_with_job_failures" and len(rejection_calls) == 1
    assert rejection_calls[0]["status"] == "state_rejected"
    assert "authorization subject field differs from old_field" in rejection_calls[0]["error"]


def test_v5_bad_lookup_continuation_stops_as_invalid_output_under_v8():
    output = ROOT / "reports/local/s3-development-v8-saved-v5-bad-continuation-final-20261026"
    manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, output))
    calls = runner.read(output / "calls.json")
    assert manifest["status"] == "completed_with_job_failures"
    assert manifest["actual_model_calls"] == 0 and len(calls) == 6
    assert calls[-1]["status"] == "invalid_output"
    assert "unsupported condition kind" in calls[-1]["error"]


def test_v8_no_lookup_control_is_marked_not_exercised():
    output = ROOT / "reports/local/s3-development-v8-no-lookup-final-20261026"
    manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, output))
    job = runner.read(output / "jobs.json")[0]
    assert manifest["status"] == "completed"
    assert job["lookup_evidence"] == "not_exercised"
    assert manifest["actual_model_calls"] == 0


def test_v8_invalid_json_stops_before_following_event():
    output = ROOT / "reports/local/s3-development-v8-invalid-output-final-20261026"
    manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, output))
    calls = runner.read(output / "calls.json")
    assert manifest["status"] == "completed_with_job_failures"
    assert manifest["actual_model_calls"] == 0
    assert len(calls) == 1 and calls[0]["status"] == "invalid_output"


def test_consumed_v5_authorization_cannot_authorize_v8():
    old = runner.read(ROOT / "reports/local/s3-development-v4-authorization-20261026.json")
    with pytest.raises(ValueError, match="exact-scope"):
        runner.check_authorization(runner.prepare(ROOT), old)


def test_offline_prepare_does_not_read_credentials_or_private_reference(monkeypatch):
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert "evaluation_private" not in path.as_posix()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    monkeypatch.setattr(os.environ, "get", lambda *args: pytest.fail("credential read during offline prepare"))
    plan, _ = runner.prepare(ROOT, with_requests=True)
    assert plan["status"] == "draft_unapproved"
    assert plan["actual_model_calls"] == 0


def test_v4_contract_schema_is_bound_into_initial_request():
    _, previews = runner.prepare(ROOT, with_requests=True)
    request = previews["job_001"]
    condition = request["output_schema"]["$defs"]["Condition"]["properties"]
    assert request["output_contract"] == "interaction_quote_v4"
    assert condition["kind"]["const"] == "candidate_sha256"
    assert "e002" not in condition["evidence_refs"]["items"]["enum"]


def test_saved_v5_bad_lookup_response_is_rejected_by_v7_replay_contract():
    from scripts import s3_interaction_v4 as interaction
    prior = runner.read(ROOT / "reports/local/s3-development-v5-live-20261026/calls.json")
    bundle = runner.bundle(ROOT, "c004")
    with pytest.raises(ValueError, match="unsupported condition kind"):
        interaction.parse_response(prior[-1]["response_text"], bundle, 4, "a"*64,
                                   prior[-1]["call_id"], runner.POLICY, lookup_attempts_used=1)




