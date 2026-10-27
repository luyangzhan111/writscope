"""V2 real-boundary gate and offline faults; never spend or emit fake real runs."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_common_real_v2 as runner
from scripts import s3_common_real_v2_report as reporting
from scripts.s3_common_real_v2_simulation import PublicSimulationProvider
from writscope.artifacts import canonical_hash, digest, write_json

ROOT = Path(__file__).resolve().parents[1]


def fresh():
    return ROOT/"reports/local"/("s3-real-v2-test-"+uuid4().hex)


@pytest.fixture(scope="module")
def prepared():
    path = fresh()
    runner.preflight(ROOT, path)
    return path


def receipt(plan):
    return {"status": "approved", "source_kind": "direct_user_message", "scope": runner.SCOPE,
            "plan_sha256": canonical_hash(plan), "case_ids": list(runner.CASES), "methods": list(runner.METHODS),
            "local_cap_cny": "12.00", "max_provider_attempts": 30, "planned_rounds": 1,
            "runner_sha256": digest(Path(runner.__file__).read_bytes()), "price_source_url": runner.PRICE_URL,
            "price_sha256": canonical_hash(plan["price"]), "response_contract": "interaction_quote_v2",
            "price_rechecked_at_utc": datetime.now(timezone.utc).isoformat(),
            "user_authorization_ref": "synthetic_unit_test_not_an_actual_human_approval"}


def execute(prepared, profile="success_lookup", fault="none"):
    output = fresh()
    result = asyncio.run(runner.execute(ROOT, prepared, output, PublicSimulationProvider(ROOT, profile, fault)))
    assert asyncio.run(runner.verify(ROOT, prepared, output)) == result
    return output, result


def test_prepare_has_reviewable_requests_v2_and_total_reservation(prepared):
    plan = runner.preflight(ROOT, prepared, verify=True)
    first = runner.read(prepared/"first_requests.json")
    assert canonical_hash(first) == plan["first_requests_sha256"]
    assert plan["worst_case_reserved_cny"] == "11.211840" and plan["new_authorization"] is None
    assert len(first) == len(plan["jobs"]) == 9
    for unit in plan["jobs"]:
        request = first[unit["job_id"]]
        assert canonical_hash(request) == unit["first_request_sha256"]
        assert request["output_contract"] == ("interaction_quote_v2" if unit["method"] == "incremental" else "source_quote_v1")


def test_complete_adapter_run_is_offline_and_report_does_not_read_answers(prepared, monkeypatch):
    original = Path.read_text
    def no_answers(path, *args, **kwargs):
        assert "evaluation_private" not in str(path) and path.name != ".env"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", no_answers)
    output, result = execute(prepared)
    assert result["status"] == "completed" and result["actual_model_calls"] == 0
    assert result["simulated_provider_attempts"] == 30 and result["usage_kind"] == "synthetic_adapter"
    jobs = runner.read(output/"jobs.json")
    assert all(j["status"] == "completed" for j in jobs)
    assert all(j["lookup_evidence"] == "offline_transport_only" for j in jobs if j["method"] == "incremental")
    stages = result["totals"]["by_stage"]
    assert sum(row["attempts"] for row in stages.values()) == 30 and stages["review"]["attempts"] == 6
    report = asyncio.run(reporting.report(ROOT, prepared, fresh(), run_dir=output))
    assert report["metrics"] is None and report["reference_sha256"] is None
    assert not (output/"authorization.json").exists()


@pytest.mark.parametrize("profile,fault,job,status", [
    ("first_error", "none", "job_001", "provider_error"),
    ("missing_usage", "none", "job_001", "usage_missing"),
    ("bad_grant", "none", "job_005", "invalid_output"),
    ("future_lookup", "none", "job_003", "invalid_output"),
    ("final_invalid", "none", "job_003", "invalid_output"),
    ("scope_rejection", "none", "job_007", "state_rejected"),
    ("success_lookup", "timeout", "job_001", "timeout"),
    ("success_lookup", "finish", "job_001", "invalid_output"),
    ("success_lookup", "over_output", "job_001", "usage_exceeds_reservation"),
    ("success_lookup", "invalid_usage", "job_001", "usage_invalid"),
    ("success_lookup", "unexpected_error", "job_001", "provider_error")])
def test_fault_stops_entire_round_and_replays(prepared, profile, fault, job, status):
    output, result = execute(prepared, profile, fault)
    assert result["status"] == "stopped_on_failure" and result["failure"]["job_id"] == job
    jobs = runner.read(output/"jobs.json")
    failed_index = next(index for index, row in enumerate(jobs) if row["job_id"] == job)
    assert all(row["status"] == "not_run" for row in jobs[failed_index+1:])
    calls = runner.read(output/"calls.json")
    assert calls[-1]["status"] == status
    assert result["totals"]["reserved_cny"] != "0"
    if fault == "invalid_usage":
        assert calls[0]["usage"]["input_tokens"] == -1
        assert result["totals"]["input_tokens"]["complete_total"] is None


def test_no_lookup_remains_offline_evidence(prepared):
    output, result = execute(prepared, "success_no_lookup")
    assert result["status"] == "completed" and result["simulated_provider_attempts"] == 24
    assert all(j["lookup_evidence"] == "offline_transport_only"
               for j in runner.read(output/"jobs.json") if j["method"] == "incremental")


@pytest.mark.parametrize("field,value", [("status", "pending"), ("source_kind", "model"),
    ("scope", "S3_three_method_three_case_development_round_v1"), ("plan_sha256", "0"*64),
    ("runner_sha256", "0"*64), ("price_sha256", "0"*64), ("local_cap_cny", "13.00"),
    ("response_contract", "interaction_quote_v1"), ("max_provider_attempts", 31)])
def test_wrong_approval_fails_before_credential_access(prepared, monkeypatch, field, value):
    plan = runner.prepare(ROOT)
    old_get = runner.os.environ.get
    def no_key(name, *args):
        assert name != "DEEPSEEK_API_KEY", "credential accessed before gate"
        return old_get(name, *args)
    monkeypatch.setattr(runner.os.environ, "get", no_key)
    with pytest.raises(ValueError, match="authorization required"):
        runner.run_real(ROOT, prepared, fresh(), {**receipt(plan), field: value})


def test_missing_expired_future_and_plan_binding_rejected(prepared, monkeypatch):
    plan = runner.prepare(ROOT)
    with pytest.raises(ValueError): runner.check_authorization(plan, None)
    valid = receipt(plan)
    runner.check_authorization(plan, valid)
    for delta in (timedelta(days=-2), timedelta(minutes=10)):
        with pytest.raises(ValueError, match="fresh official"):
            runner.check_authorization(plan, {**valid, "price_rechecked_at_utc": (datetime.now(timezone.utc)+delta).isoformat()})
    changed = deepcopy(plan); changed["price"]["input_cny_per_million"] = "9"
    with pytest.raises(ValueError, match="binding mismatch"):
        runner.check_authorization(changed, receipt(changed))
    # Replay validates freshness at the saved launch, not at today's time.
    historical = datetime.now(timezone.utc)-timedelta(days=2)
    runner.check_authorization(plan, {**valid, "price_rechecked_at_utc": historical.isoformat()}, now=historical)


def test_spent_launch_and_existing_output_rejected_before_key(prepared, monkeypatch):
    plan = runner.prepare(ROOT)
    isolated = fresh(); isolated.mkdir()
    runner.claim(isolated, plan, isolated/"launch", receipt(plan))
    path = runner.claim_path(isolated, plan)
    monkeypatch.setattr(runner, "claim_path", lambda *args: path)
    old_get = runner.os.environ.get
    def no_key(name, *args):
        assert name != "DEEPSEEK_API_KEY"
        return old_get(name, *args)
    monkeypatch.setattr(runner.os.environ, "get", no_key)
    with pytest.raises(ValueError, match="already used"):
        runner.run_real(ROOT, prepared, fresh(), receipt(plan))
    with pytest.raises(FileExistsError):
        runner.claim(isolated, plan, isolated/"second", receipt(plan))


def test_budget_blocks_before_send_and_no_claimed_approval_in_simulation(prepared):
    plan, first = runner.prepare(ROOT, with_requests=True)
    unit = plan["jobs"][0]
    request = runner.baseline(
        runner.bundle(ROOT,"c001"), "full_direct", runner.settings(ROOT)[0]["full_direct"], runner.MODEL)
    pending = {"stage": "review", "request": request}
    assert runner.budget_failure(plan, unit, [{"reserved_cny": "12.00"}], [], pending)["status"] == "budget_exhausted"
    too_large = request.model_copy(update={"instructions": "x"*100001})
    assert runner.budget_failure(plan, unit, [], [], {**pending,"request":too_large}) is not None
    with pytest.raises(ValueError, match="provenance"):
        asyncio.run(runner.execute(ROOT, prepared, fresh(), PublicSimulationProvider(ROOT), receipt=receipt(plan)))


def test_rehashed_first_request_and_saved_request_tamper_fail(prepared):
    changed = fresh(); runner.preflight(ROOT, changed)
    previews = runner.read(changed/"first_requests.json")
    previews["job_001"]["public_input"]["review_input"]["events"][0]["content"] = "forged"
    write_json(changed/"first_requests.json", previews)
    manifest = runner.read(changed/"run.json"); manifest["artifacts"] = runner._artifacts(changed)
    write_json(changed/"run.json",manifest)
    with pytest.raises(ValueError,match="first requests"):
        runner.preflight(ROOT, changed, verify=True)
    output, _ = execute(prepared,"first_error")
    calls = runner.read(output/"calls.json")
    calls[0]["request"]["public_input"]["review_input"]["events"][0]["content"]="forged"
    write_json(output/"calls.json",calls)
    m=runner.read(output/"run.json");m["artifacts"]=runner._artifacts(output);write_json(output/"run.json",m)
    with pytest.raises(ValueError,match="request or cost"):
        asyncio.run(runner.verify(ROOT,prepared,output))


def test_valid_gate_credential_order_uses_substitutes_without_launch(prepared, monkeypatch):
    plan = runner.prepare(ROOT); events = []
    old_get = runner.os.environ.get
    def fake_key(name, *args):
        if name == "DEEPSEEK_API_KEY":
            events.append("key_reader")
            return "unit-test-placeholder-not-a-real-key"
        return old_get(name,*args)
    def no_network_constructor(key, price):
        assert key == "unit-test-placeholder-not-a-real-key"
        events.append("constructor")
        raise RuntimeError("offline_constructor_stop")
    monkeypatch.setattr(runner.os.environ,"get",fake_key)
    monkeypatch.setattr(runner,"DeepSeekProvider",no_network_constructor)
    output=fresh()
    # This unit test exercises the credential/constructor order, independently
    # of whether the historical frozen plan already has a consumed paid claim.
    unused_claim = fresh().with_suffix(".claim.json")
    monkeypatch.setattr(runner, "claim_path", lambda *_args: unused_claim)
    with pytest.raises(RuntimeError,match="offline_constructor_stop"):
        runner.run_real(ROOT,prepared,output,receipt(plan))
    assert events == ["key_reader","constructor"]
    assert not output.exists() and not runner.claim_path(ROOT,plan).exists()


def test_existing_output_blocks_before_credentials(prepared, monkeypatch):
    output=fresh();output.mkdir()
    old_get=runner.os.environ.get
    def no_key(name,*args):
        assert name != "DEEPSEEK_API_KEY"
        return old_get(name,*args)
    monkeypatch.setattr(runner.os.environ,"get",no_key)
    with pytest.raises(ValueError,match="already used"):
        runner.run_real(ROOT,prepared,output,receipt(runner.prepare(ROOT)))


def test_simulation_cannot_be_relabelled_real_before_scoring(prepared, monkeypatch):
    output,_=execute(prepared,"first_error")
    manifest=runner.read(output/"run.json")
    manifest.update(execution_kind="real_model",actual_model_calls=1,simulated_provider_attempts=0,
                    authorization_ref="untrusted_self_claim")
    write_json(output/"run.json",manifest)
    original=Path.read_text
    def no_answers(path,*args,**kwargs):
        assert "evaluation_private" not in str(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"read_text",no_answers)
    with pytest.raises(ValueError,match="missing saved authorization"):
        asyncio.run(reporting.report(ROOT,prepared,fresh(),run_dir=output))
