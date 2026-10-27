"""Offline gate/replay checks for the c004 v6-prompt diagnostic."""
import asyncio
import json
from decimal import Decimal
import os
from pathlib import Path

import pytest

from scripts import s3_acceptance_v5 as runner

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "reports/local/s3-development-v5-preflight-final2-20261026"


def test_preflight_is_single_c004_v6_scope_and_hides_prior_event_text():
    plan, previews = runner.prepare(ROOT, with_requests=True)
    request = previews["job_001"]
    c004 = runner.bundle(ROOT, "c004")
    hidden = c004.review_input.events[1].content
    instructions = " ".join(request["instructions"].split())

    assert plan["version"] == "s3_acceptance_v5"
    assert plan["status"] == "draft_unapproved"
    assert plan["authorization_scope"] == runner.SCOPE
    assert plan["case_ids"] == ["c004"]
    assert plan["policy"]["max_attempts"] == 8
    assert plan["policy"]["retries"] == 0
    assert plan["policy"]["concurrency"] == 1
    assert plan["local_cap_cny"] == "3.00"
    assert Decimal(plan["worst_case_reserved_cny"]) == Decimal("2.989824")
    assert plan["actual_model_calls"] == 0 and plan["new_authorization"] is None
    assert "explicitly permits a server-visible API change" in instructions
    assert "Before emitting each grant" in instructions
    assert hidden not in json.dumps(request, ensure_ascii=False)
    calls = runner.read(ROOT / "reports/local/s3-development-v5-v2-lookup-20261026/calls.json")
    fifth = next(row["request"] for row in calls if row["event_seq"] == 5 and row["stage"] == "extract")
    assert {row["event_id"] for row in fifth["public_input"]["history_index"]} >= {"e002", "e003"}
    assert hidden not in json.dumps(fifth, ensure_ascii=False)


def test_saved_lookup_simulation_replays_without_model_claims():
    output = ROOT / "reports/local/s3-development-v5-v2-lookup-20261026"
    manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, output))
    summary = runner.read(ROOT / "reports/local/s3-development-v5-v2-lookup-20261026-report/summary.json")

    assert manifest["execution_kind"] == "offline_simulation"
    assert manifest["actual_model_calls"] == 0
    assert manifest["simulated_provider_attempts"] == 7
    assert summary["metrics"] is None
    assert summary["s3_development_exit"] == "not_satisfied"
    assert summary["rows"][0]["lookup_evidence"] == "offline_transport_only"


def test_saved_first_failure_and_original_v4_response_replay():
    first_error = ROOT / "reports/local/s3-development-v5-v2-first-error-20261026"
    old_rejection = ROOT / "reports/local/s3-development-v5-v2-v4-rejection-20261026"
    error_manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, first_error))
    rejection_manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, old_rejection))
    error_calls = runner.read(first_error / "calls.json")
    rejection_calls = runner.read(old_rejection / "calls.json")

    assert error_manifest["status"] == "completed_with_job_failures" and len(error_calls) == 1
    assert rejection_manifest["status"] == "completed_with_job_failures" and len(rejection_calls) == 1
    assert rejection_calls[0]["status"] == "state_rejected"
    assert "authorization subject field differs from old_field" in rejection_calls[0]["error"]


def test_saved_no_lookup_control_is_marked_not_exercised():
    output = ROOT / "reports/local/s3-development-v5-v2-no-lookup-20261026"
    manifest = asyncio.run(runner.verify(ROOT, PREFLIGHT, output))
    job = runner.read(output / "jobs.json")[0]
    assert manifest["status"] == "completed"
    assert job["lookup_evidence"] == "not_exercised"
    assert manifest["actual_model_calls"] == 0


def test_consumed_v4_authorization_cannot_authorize_v5():
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
