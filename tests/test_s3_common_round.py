"""Same-round evidence, global stop, exact approval, replay and report boundaries."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import m3q_fault_evidence_audit as fixtures
from scripts import s3_common_round as round
from scripts import s3_common_report as reporting
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.providers import ActualUsage, ProviderResponse, ProviderError

ROOT = Path(__file__).resolve().parents[1]


def fresh():
    return ROOT / "reports/local" / ("s3-common-test-"+uuid4().hex)


@pytest.fixture(scope="module")
def prepared():
    output = fresh()
    round.preflight(ROOT, output)
    return output


class PublicScriptProvider:
    def __init__(self, *, bad_first=False, missing_usage=False, future_lookup=False):
        self.inc = {}
        self.bad_first, self.missing_usage = bad_first, missing_usage
        for case in round.CASES:
            script = fixtures._basic_script(round.bundle(ROOT, case))
            if future_lookup and case == "c001":
                script.steps[3].text = json.dumps({"kind": "lookup_request", "event_ids": ["e005"]})
            self.inc[case] = round.helper.SimulatedActualProvider(script)

    async def generate(self, request):
        if self.bad_first:
            return ProviderResponse(text="{}", actual_usage=ActualUsage(input_tokens=20, output_tokens=10,
                usage_source="provider_response", cost_source="unavailable"), finish_reason="stop")
        case = request.public_input["review_input"]["case_id"]
        if request.method.startswith("incremental"):
            return await self.inc[case].generate(request)
        value = fixtures._final(round.bundle(ROOT, case))
        if request.method == "full_checklist":
            value["checklist"] = {key: "scripted transport check only" for key in (
                "reconstruction", "subject_check", "version_check", "operation_check",
                "conditions_check", "lifecycle_check")}
        return ProviderResponse(text=json.dumps(value, ensure_ascii=False), finish_reason="stop",
            actual_usage=None if self.missing_usage else ActualUsage(input_tokens=20, output_tokens=10,
                usage_source="provider_response", cost_source="unavailable"))


def receipt(plan):
    return {"status": "approved", "source_kind": "direct_user_message", "scope": round.SCOPE,
            "plan_sha256": canonical_hash(plan), "case_ids": list(round.CASES), "methods": list(round.METHODS),
            "local_cap_cny": "12.00", "max_provider_attempts": 30, "planned_rounds": 1,
            "runner_sha256": digest(Path(round.__file__).read_bytes()),
            "price_source_url": round.PRICE_URL, "price_rechecked_at_utc": datetime.now(timezone.utc).isoformat(),
            "user_authorization_ref": "synthetic_gate_test_not_a_human_approval"}


def test_plan_has_all_jobs_balanced_order_equal_evidence_and_cost(prepared):
    plan = round.preflight(ROOT, prepared, verify=True)
    assert len(plan["jobs"]) == 9 and sum(row["max_attempts"] for row in plan["jobs"]) == 30
    assert plan["worst_case_reserved_cny"] == "11.211840"
    assert plan["new_authorization"] is None and not plan["budget_matched"]
    assert plan["reference_status"].endswith("pending")
    for case in round.CASES:
        assert {j["method"] for j in plan["jobs"] if j["case_id"] == case} == set(round.METHODS)


def test_full_simulation_replays_without_reference_reads_or_model_metrics(prepared, monkeypatch):
    original = Path.read_text
    def public_only(path, *args, **kwargs):
        assert "evaluation_private" not in str(path) and path.name != ".env"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", public_only)
    output = fresh()
    result = asyncio.run(round.execute(ROOT, prepared, output, PublicScriptProvider()))
    assert result["status"] == "completed" and result["actual_model_calls"] == 0
    assert result["simulated_provider_attempts"] == 30
    stages = result["totals"]["by_stage"]
    assert stages["review"]["attempts"] == 6
    assert sum(stage["attempts"] for stage in stages.values()) == 30
    assert sum(float(stage["reserved_cny"]) for stage in stages.values()) == pytest.approx(float(result["totals"]["reserved_cny"]))
    assert asyncio.run(round.verify(ROOT, prepared, output)) == result
    report = asyncio.run(reporting.report(ROOT, prepared, fresh(), run_dir=output))
    assert len(report["rows"]) == 9 and report["metrics"] is None and report["reference_sha256"] is None
    assert {r["lookup_evidence"] for r in report["rows"] if r["method"] == "incremental"} == {"offline_transport_only"}
    for row in round.read(output / "calls.json"):
        inp = row["request"]["public_input"]
        if row["event_seq"] is not None:
            assert len(inp["review_input"]["events"]) == row["event_seq"]


@pytest.mark.parametrize("options,status", [({"bad_first": True}, "invalid_output"),
                                          ({"missing_usage": True}, "usage_missing")])
def test_first_failure_stops_entire_round_and_keeps_missing_jobs(prepared, options, status):
    output = fresh()
    result = asyncio.run(round.execute(ROOT, prepared, output, PublicScriptProvider(**options)))
    assert result["status"] == "stopped_on_failure" and result["simulated_provider_attempts"] == 1
    jobs = round.read(output / "jobs.json")
    assert jobs[0]["failure"]["status"] == status
    assert [j["status"] for j in jobs[1:]] == ["not_run"]*8
    assert asyncio.run(round.verify(ROOT, prepared, output)) == result


def test_future_lookup_stops_all_following_cases(prepared):
    output = fresh()
    result = asyncio.run(round.execute(ROOT, prepared, output, PublicScriptProvider(future_lookup=True)))
    assert result["status"] == "stopped_on_failure"
    assert result["failure"]["job_id"] == "job_003"
    assert round.read(output / "jobs.json")[2]["accepted_events"] == 3
    assert all(j["status"] == "not_run" for j in round.read(output / "jobs.json")[3:])
    assert asyncio.run(round.verify(ROOT, prepared, output)) == result


def test_missing_old_wrong_scope_and_stale_approval_cannot_read_key(prepared, monkeypatch):
    plan = round.prepare(ROOT)
    def forbidden(*args, **kwargs):
        raise AssertionError("credential access before approval")
    monkeypatch.setattr(round.os.environ, "get", forbidden)
    with pytest.raises(ValueError, match="authorization required"):
        round.run_real(ROOT, prepared, fresh(), {})
    round.check_authorization(plan, receipt(plan))
    with pytest.raises(ValueError, match="authorization required"):
        round.check_authorization(plan, {**receipt(plan), "scope": "S3_small_c003_lookup_acceptance_v1"})
    with pytest.raises(ValueError, match="fresh official"):
        round.check_authorization(plan, {**receipt(plan), "price_rechecked_at_utc":
            (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()})


def test_rehashed_request_forgery_rejected(prepared):
    output = fresh()
    asyncio.run(round.execute(ROOT, prepared, output, PublicScriptProvider(bad_first=True)))
    calls = round.read(output / "calls.json")
    calls[0]["request"]["public_input"]["review_input"]["events"][0]["content"] = "forged"
    write_json(output / "calls.json", calls)
    manifest = round.read(output / "run.json")
    manifest["artifacts"] = round._artifacts(output)
    write_json(output / "run.json", manifest)
    with pytest.raises(ValueError, match="request or cost"):
        asyncio.run(round.verify(ROOT, prepared, output))


def test_metrics_keep_failed_denominators_and_missing_positive():
    # Metric arithmetic only, not model or independent-review evidence.
    rows = [{"expected": {"impact": "breaking", "authorization": "not_covered", "decision": "revise"},
             "observed": {"impact": "breaking", "authorization": "covered", "decision": "allow"}},
            {"expected": {"impact": "breaking", "authorization": "covered", "decision": "allow"}, "observed": None}]
    value = reporting.metrics(rows)
    assert value["axes"]["impact"] == {"count": 1, "denominator": 2}
    assert value["false_allow"] == {"count": 1, "denominator": 1}
    assert value["false_block"] == {"count": 0, "denominator": 1}
    assert value["positive_not_allowed_including_missing"] == {"count": 1, "denominator": 1}


def test_one_round_claim_blocks_new_output_directory():
    isolated = fresh(); plan = round.prepare(ROOT)
    round.claim(isolated, plan, isolated/"one", receipt(plan))
    with pytest.raises(FileExistsError):
        round.claim(isolated, plan, isolated/"two", receipt(plan))


@pytest.mark.parametrize("kind,status", [("timeout", "timeout"), ("provider", "provider_error"),
                                        ("finish", "invalid_output"), ("usage", "usage_exceeds_reservation")])
def test_provider_faults_are_preserved_and_stop_before_job_two(prepared, kind, status):
    delegate = PublicScriptProvider()
    class FaultProvider:
        async def generate(self, request):
            if kind == "timeout":
                raise TimeoutError("offline deadline")
            if kind == "provider":
                raise ProviderError("offline provider failure")
            response = await delegate.generate(request)
            if kind == "finish":
                return response.model_copy(update={"finish_reason": "length"})
            return response.model_copy(update={"actual_usage": response.actual_usage.model_copy(update={"input_tokens": 200000})})
    output = fresh()
    result = asyncio.run(round.execute(ROOT, prepared, output, FaultProvider()))
    assert result["failure"]["status"] == status and result["simulated_provider_attempts"] == 1
    assert asyncio.run(round.verify(ROOT, prepared, output)) == result
