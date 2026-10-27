"""Saved-response reproduction and error-only propagation; no model requests."""
import asyncio
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_common_real_v2 as frozen
from scripts import s3_local_mapping_diagnosis as diagnostic
from scripts.s3_failure_summary_v1 import FailureSummaryCursor, state_rejection_detail
from writscope.artifacts import digest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/local/s3-real-v2-live-20261026"
PREPARED = ROOT / "reports/local/s3-real-v2-preflight-20261026-final"


def saved():
    jobs = frozen.read(SOURCE / "jobs.json")
    calls = frozen.read(SOURCE / "calls.json")
    plan = frozen.read(SOURCE / "plan.json")
    return jobs, calls, plan


def test_frozen_cursor_still_reproduces_null_and_adapter_only_fills_error():
    jobs, calls, plan = saved()
    unit = plan["jobs"][2]
    raw = calls[2]

    async def compare():
        old, new = frozen.Cursor(ROOT, unit), FailureSummaryCursor(ROOT, unit)
        await old.start()
        await new.start()
        assert old.pending == new.pending
        assert await old.consume(deepcopy(raw), plan) == ("state_rejected", None)
        status, error = await new.consume(deepcopy(raw), plan)
        reason = jobs[2]["transition_steps"][0]["batch_record"]["rejection_reason"]
        assert status == "state_rejected" and error == reason
        assert new.failure == {**old.failure, "error": reason}
        assert old.pending is new.pending is None
        old_out = old.outcome("real_model", [raw])
        new_out = new.outcome("real_model", [raw])
        new_out["failure"] = old_out["failure"]
        assert new_out == old_out
        batch = new.result["steps"][0]["batch_record"]
        assert batch["accepted"] is False and batch["failed_item_index"] == 1
        assert new_out["accepted_events"] == 0
        assert new_out["state"]["constraints"] == []

    asyncio.run(compare())


def evidence():
    call = {"call_id": "core_1", "event_seq": 1, "status": "state_rejected", "error": None}
    step = {"call_ids": ["core_1"], "seq": 1, "event_id": "e001",
            "status": "batch_rejected", "error": "rejected",
            "batch_record": {"accepted": False, "rejection_reason": "rejected", "failed_item_index": 1}}
    return call, {"steps": [step]}


@pytest.mark.parametrize("mutation", ["wrong_call", "wrong_event", "duplicate", "success", "missing", "inconsistent"])
def test_no_reason_invented_or_borrowed(mutation):
    call, result = evidence()
    step = result["steps"][0]
    if mutation == "wrong_call":
        step["call_ids"] = ["other"]
    elif mutation == "wrong_event":
        step["seq"] = 2
    elif mutation == "duplicate":
        result["steps"].append(deepcopy(step))
    elif mutation == "success":
        call["status"] = "succeeded"
    elif mutation == "missing":
        step["error"] = None
    else:
        step["batch_record"]["rejection_reason"] = "different"
    assert state_rejection_detail(call, result) is None


def test_preserve_explicit_error_and_support_matching_context_rejection():
    call, result = evidence()
    call["error"] = "existing"
    assert state_rejection_detail(call, result) is None
    call["error"] = None
    result["steps"][0].update(status="context_rejected", batch_record=None)
    assert state_rejection_detail(call, result)["error"] == "rejected"


def test_provider_error_summary_is_unchanged():
    _, calls, plan = saved()
    row = deepcopy(calls[0])
    row.update(response_text=None, status="provider_error", error="offline injected failure")

    async def check():
        cursor = FailureSummaryCursor(ROOT, plan["jobs"][0])
        await cursor.start()
        assert await cursor.consume(row, plan) == ("provider_error", "offline injected failure")
        assert cursor.pending is None

    asyncio.run(check())


def test_diagnostic_replay_read_only_and_no_provider_or_labels(monkeypatch):
    original = Path.read_text

    def allowed(path, *args, **kwargs):
        assert "evaluation_private" not in str(path) and path.name != ".env"
        return original(path, *args, **kwargs)

    async def forbidden(*args, **kwargs):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(Path, "read_text", allowed)
    monkeypatch.setattr(frozen.DeepSeekProvider, "generate", forbidden)
    paths = [*SOURCE.iterdir(), *PREPARED.iterdir(), Path(frozen.__file__)]
    before = {p: digest(p.read_bytes()) for p in paths if p.is_file()}
    output = ROOT / "reports/local" / ("s3-mapping-test-" + uuid4().hex)

    async def check():
        first = await diagnostic.run(ROOT, PREPARED, SOURCE, output)
        assert await diagnostic.run(ROOT, PREPARED, SOURCE, output, verify=True) == first
        assert first["raw_failure"]["error"] is None
        assert first["failure"]["error"] == first["reproduction"]["rejection_reason"]
        assert first["new_model_calls"] == 0 and first["metrics"] is None
        assert [j["status"] for j in first["jobs"]].count("not_run") == 6
        with pytest.raises(FileExistsError):
            await diagnostic.run(ROOT, PREPARED, SOURCE, output)
        tampered = frozen.read(output / "diagnostic.json")
        tampered["failure"]["error"] = "tampered"
        frozen.write_json(output / "diagnostic.json", tampered)
        with pytest.raises(ValueError, match="replay mismatch"):
            await diagnostic.run(ROOT, PREPARED, SOURCE, output, verify=True)

    # Saved claims retain the original machine path. Only the verifier read
    # view is relocated; the raw evidence and one-use paid gate are unchanged.
    from scripts.public_release import relocated_claims
    with relocated_claims(ROOT):
        asyncio.run(check())
    assert {p: digest(p.read_bytes()) for p in before} == before
