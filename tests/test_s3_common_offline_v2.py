"""Offline V2 integration, scope, global stop, replay and report isolation."""
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_common_offline_v2 as runner
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.providers import deepseek

ROOT = Path(__file__).resolve().parents[1]


def fresh():
    return ROOT/"reports/local"/("s3-v2-round-test-"+uuid4().hex)


@pytest.fixture(scope="module")
def success():
    output = fresh()
    result = runner.run(ROOT, output)
    assert runner.run(ROOT, output, verify=True) == result
    return output, result


def test_complete_round_binds_v2_and_unchanged_baselines(success):
    output, result = success
    assert result["status"] == "completed" and result["actual_model_calls"] == 0
    assert result["synthetic_totals"]["simulated_provider_attempts"] == 30
    assert len(result["jobs"]) == 9 and all(j["final_review_valid"] for j in result["jobs"])
    plan = runner.read(output/"plan.json")
    assert plan["paid_budget"] is None and plan["new_authorization"] is None
    old = runner.read(ROOT/"reports/local/s3-common-preflight-20261025-final-v2/plan.json")
    assert all(plan["prompt_sha256"][method] == old["prompt_sha256"][method] for method in runner.METHODS[:2])
    assert all(plan["public_sources"][case]["evidence_sha256"] == old["public_sources"][case]["public_evidence_sha256"] for case in runner.CASES)
    for job in result["jobs"]:
        calls = runner.read(output/job["job_id"]/"calls.json")
        for call in calls:
            request = call["request"]
            if request["method"] == "incremental_interaction":
                assert request["output_contract"] == "interaction_quote_v2"
                assert len(request["public_input"]["review_input"]["events"]) == call["event_seq"]
            else:
                assert request["output_contract"] == "source_quote_v1"


def test_c002_grant_retains_constraints_and_lookup_chain(success):
    output, result = success
    job = next(j for j in result["jobs"] if j["case_id"] == "c002" and j["method"] == "incremental")
    state = runner.read(output/job["job_id"]/"state.json")
    assert job["events_accepted"] == 5 and job["lookup_attempts"] == 2
    assert job["lookup_evidence"] == "offline_transport_only"
    assert len(state["authorizations"]) == 1 and len(state["constraints"]) == 2
    assert all(c["lifecycle"] == "active" for c in state["constraints"])
    assert len(state["exceptions"]) == 1
    assert set(state["exceptions"][0]["constraint_ids"]) == {"con_status", "con_shape"}
    history = runner.read(output/job["job_id"]/"lookup_history.json")
    assert len(history) == 2 and all(r["record"]["status"] == "succeeded" for r in history)


def test_four_stage_budget_and_report_are_synthetic(success):
    output, result = success
    totals = result["synthetic_totals"]
    assert sum(value["attempts"] for value in totals["by_stage"].values()) == 30
    assert sum(Decimal(value["simulated_reservation_cny"]) for value in totals["by_stage"].values()) == Decimal(totals["simulated_reservation_cny"])
    assert result["metrics"] is None and result["actual_provider_usage"] is None
    assert result["reference_labels"] == "not_read"
    assert "无真实模型调用" in (output/"report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("profile,job_id,attempts", [
    ("first_error", "job_001", 1), ("missing_usage", "job_001", 1),
    ("bad_grant", "job_005", 18), ("bare_supersede", "job_005", 18),
    ("future_lookup", "job_003", 6), ("final_invalid", "job_003", 10),
    ("scope_rejection", "job_007", 27)])
def test_first_failure_halts_later_jobs_and_replays(profile, job_id, attempts):
    output = fresh()
    result = runner.run(ROOT, output, profile)
    assert result["status"] == "stopped_on_failure" and result["failure"]["job_id"] == job_id
    assert result["synthetic_totals"]["simulated_provider_attempts"] == attempts
    index = next(i for i, job in enumerate(result["jobs"]) if job["job_id"] == job_id)
    assert all(j["status"] == "not_run" for j in result["jobs"][index+1:])
    failed = result["jobs"][index]
    assert not failed["final_review_valid"]
    if failed["method"] == "incremental":
        assert not (output/job_id/"final_result.json").exists()
    assert runner.run(ROOT, output, profile, verify=True) == result


def test_no_lookup_path_is_not_model_selected_lookup_evidence():
    output = fresh()
    result = runner.run(ROOT, output, "success_no_lookup")
    assert result["status"] == "completed"
    assert result["synthetic_totals"]["simulated_provider_attempts"] == 24
    assert all(j["lookup_attempts"] == 0 and j["lookup_evidence"] == "offline_transport_only"
               for j in result["jobs"] if j["method"] == "incremental")
    assert runner.run(ROOT, output, "success_no_lookup", verify=True) == result


def test_runner_denies_network_credentials_and_reference_reads(monkeypatch):
    original = Path.read_text
    def public_only(path, *args, **kwargs):
        assert "evaluation_private" not in str(path) and path.name != ".env"
        return original(path, *args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError("offline entry touched a real provider")
    monkeypatch.setattr(Path, "read_text", public_only)
    monkeypatch.setattr(deepseek.DeepSeekProvider, "__init__", forbidden)
    result = runner.run(ROOT, fresh(), "bad_grant")
    assert result["actual_model_calls"] == 0
    with pytest.raises(SystemExit):
        runner.main(["real", "--output", str(fresh())])


def test_rehashed_request_or_report_tamper_is_rejected():
    output = fresh()
    runner.run(ROOT, output, "first_error")
    calls = runner.read(output/"job_001/calls.json")
    calls[0]["request"]["public_input"]["review_input"]["events"][0]["content"] = "forged"
    write_json(output/"job_001/calls.json", calls)
    manifest = runner.read(output/"run.json")
    manifest["artifacts"] = runner._artifacts(output)
    write_json(output/"run.json", manifest)
    with pytest.raises(ValueError, match="baseline request/response"):
        runner.run(ROOT, output, "first_error", verify=True)
