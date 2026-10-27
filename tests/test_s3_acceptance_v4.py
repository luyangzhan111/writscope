import asyncio
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import s3_acceptance_v4 as runner
from scripts import s3_lookup_case_v1
from scripts.s3_acceptance_simulation_v4 import PublicControl
from scripts.s3_interaction_v3 import build_request
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from writscope.artifacts import digest
from writscope.providers import ProviderError
from writscope.providers.deepseek import wire_bytes
from writscope.state import Condition, _condition_verdict

ROOT = Path(__file__).resolve().parents[1]


async def drive(case, profile):
    unit = next(row for row in runner.prepare(ROOT)["jobs"] if row["case_id"] == case)
    cursor = runner.Cursor(ROOT, unit)
    control = PublicControl(ROOT, profile)
    await cursor.start()
    calls = []
    while cursor.pending is not None:
        pending = cursor.pending
        request = pending["request"]
        row = {"call_id": f"call_{len(calls)+1:03d}", "stage": pending["stage"],
               "event_seq": pending["event_seq"], "request": request.model_dump(),
               "wire_bytes": len(wire_bytes(request)), "reserved_cny": str(runner.quote(request)),
               "response_text": None, "usage": None, "finish_reason": None,
               "status": "provider_error", "error": None, "provider_elapsed_ms": 0.0}
        try:
            response = await control.generate(request)
        except ProviderError as exc:
            row["error"] = str(exc)
        else:
            row.update(response_text=response.text, usage=response.actual_usage.model_dump(mode="json"),
                       finish_reason=response.finish_reason, error=None)
        row["status"], row["error"] = await cursor.consume(row, {})
        calls.append(row)
        assert len(calls) <= 8
    return cursor.outcome("offline_simulation", calls), calls


def test_saved_c002_constraint_revoke_still_rejected_and_error_propagates():
    path = ROOT / "reports/local/s3-incremental-v3-live-20261026/calls.json"
    before = digest(path.read_bytes())
    outcome, calls = asyncio.run(drive("c002", "original_rejection"))
    assert outcome["accepted_events"] == 4
    assert outcome["failure"]["error"] == "authorization target is missing or inactive"
    assert calls[-1]["error"] == outcome["failure"]["error"]
    assert outcome["state"]["authorizations"] == []
    assert next(c for c in outcome["state"]["constraints"] if c["constraint_id"] == "c001")["lifecycle"] == "active"
    assert digest(path.read_bytes()) == before


def test_lookup_fact_missing_from_e005_request_then_returned_in_continuation():
    result, calls = asyncio.run(drive("c004", "lookup"))
    fifth = [row for row in calls if row["event_seq"] == 5]
    assert [row["stage"] for row in fifth] == ["extract", "lookup_continuation"]
    bundle = runner.bundle(ROOT, "c004")
    report = bundle.review_input.events[1].content
    assert report not in json.dumps(fifth[0]["request"], ensure_ascii=False)
    assert report in json.dumps(fifth[1]["request"], ensure_ascii=False)
    returned = fifth[1]["request"]["public_input"]["history_lookup"]["current_event_results"][0]["events"][0]
    assert returned["role"] == "tool" and returned["provenance"]["authority_level"] == "none"
    assert result["status"] == "completed"
    assert result["lookup_evidence"] == "offline_transport_only"


def test_matching_report_is_separate_counterfactual_with_opposite_condition():
    mismatch = s3_lookup_case_v1.build(ROOT)
    matching = s3_lookup_case_v1.build(ROOT, matching_candidate=True)
    assert mismatch.review_input.events[4] == matching.review_input.events[4]
    assert mismatch.contents == matching.contents
    assert mismatch.review_input.events[1] != matching.review_input.events[1]
    actual = mismatch.review_input.candidate_patch.sha256
    other = digest(b"WritScope public synthetic alternate candidate registration v1")
    for value, verdict in ((actual, "covered"), (other, "not_covered")):
        condition = Condition(kind="candidate_sha256", value=value, resolution="unknown", evidence_refs=["patch"])
        assert _condition_verdict([condition], actual) == verdict


@pytest.mark.parametrize("profile,error", [("future_lookup", "lookup event outside visible history"),
                                           ("invalid_output", None), ("first_error", "offline first-track failure")])
def test_failure_summary_and_stop(profile, error):
    case = "c004" if profile == "future_lookup" else "c002"
    result, calls = asyncio.run(drive(case, profile))
    assert result["status"] == "stopped_on_failure" and len(calls) == 1
    assert result["failure"]["error"]
    if error:
        assert error in result["failure"]["error"]


def test_old_authorization_and_budget_envelope_rejected_before_send(monkeypatch):
    plan = runner.prepare(ROOT)
    old = runner.read(ROOT / "reports/local/s3-incremental-v3-authorization-20261026.json")
    with pytest.raises(ValueError, match="exact-scope"):
        runner.check_authorization(plan, old)
    unit = plan["jobs"][0]
    prompts, config = runner.settings(ROOT)
    cursor = runner.Cursor(ROOT, unit)
    asyncio.run(cursor.start())
    existing = [{"reserved_cny": "0"} for _ in range(16)]
    assert runner.budget_failure(plan, unit, existing, [], cursor.pending)["status"] == "budget_exhausted"


def test_prepare_does_not_read_private_reference_or_credentials(monkeypatch):
    original = Path.read_text
    def guarded(path, *args, **kwargs):
        assert "evaluation_private" not in path.as_posix()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    monkeypatch.setattr(runner.os.environ, "get", lambda *args: pytest.fail("credential read during offline prepare"))
    plan, requests = runner.prepare(ROOT, with_requests=True)
    assert plan["actual_model_calls"] == 0 and plan["new_authorization"] is None
    assert plan["policy"]["max_attempts"] == 16
    assert Decimal(plan["worst_case_reserved_cny"]) == Decimal("5.979648")
    assert all("s3_acceptance_labels" not in json.dumps(req) for req in requests.values())
