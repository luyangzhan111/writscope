"""M3g integration uses scripted sources only; no credentials or network."""

import asyncio
from decimal import Decimal
import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from scripts import m3d_gate, m3e_gate, m3g_offline_runner as gate
from test_m3c_real_pilot import PublicNoChangeProvider
from writscope.contracts import parse_json
from writscope.providers import ProviderResponse


def _path(root: Path) -> Path:
    return root / "reports" / "local" / ("m3g-test-" + uuid4().hex)


class WrongScopeProvider(PublicNoChangeProvider):
    async def generate(self, request):
        response = await super().generate(request)
        event = request.public_input["review_input"]["events"][-1]
        if self.calls == 1:
            payload = {"operation": "add_constraint", "source_quote": event["content"],
                       "target_ids": [], "constraint": {
                           "constraint_id": "c001", "subjects": [{
                               "protocol": "http", "method": "GET", "path": "/api/v1/profile",
                               "api_version": "v1", "location": "response.body", "field": "status"}],
                           "predicate": {"kind": "response_shape", "expected": {"status": "active"},
                                         "qualifiers": {}}, "interpretation": "resolved",
                           "supersedes": []}, "authorization": None}
        elif self.calls == 5:
            payload = {"operation": "grant", "source_quote": event["content"],
                       "target_ids": [], "constraint": None, "authorization": {
                           "authorization_id": "a001", "subjects": [{
                               "protocol": "http", "method": "GET", "path": "/api/v2/profile",
                               "api_version": "v2", "location": "response.body", "field": "state"}],
                           "change_type": "replace_field",
                           "change_details": {"old_field": "status", "new_field": "state",
                                              "preserve_value_semantics": True},
                           "conditions": [], "interpretation": "resolved",
                           "constraint_refs": ["c001"], "supersedes": []}}
        else:
            return response
        return ProviderResponse(text=json.dumps(payload, ensure_ascii=False),
                                finish_reason="stop", actual_usage=response.actual_usage)


def test_c002_saved_response_completes_and_replays(root):
    source, output = _path(root), _path(root)
    try:
        original = asyncio.run(m3d_gate.run(root, source, m3d_gate.prepare(root, Decimal("2")),
                                            PublicNoChangeProvider()))
        assert original["status"] == "completed"
        result = gate.run_offline(root, "c002", source, output)
        assert result["status"] == "completed"
        assert result["attempted_events"] == 5
        assert result["new_model_calls"] == 0
        assert (output / "assessment.json").exists()
        assert gate.verify_run(root, "c002", source, output)["artifact_set_sha256"] == result[
            "artifact_set_sha256"]
    finally:
        for path in (source, output):
            if path.exists():
                shutil.rmtree(path)


def test_c003_scope_rejection_stops_and_replays(root):
    source, output = _path(root), _path(root)
    try:
        original = asyncio.run(m3e_gate.run(root, source, m3e_gate.prepare(root, Decimal("2")),
                                            WrongScopeProvider()))
        assert original["status"] == "completed"  # Historical kernel accepted it.
        result = gate.run_offline(root, "c003", source, output)
        assert result["status"] == "stopped_on_failure"
        assert result["attempted_events"] == 5
        assert not (output / "assessment.json").exists()
        call = parse_json((output / "step_005" / "call.json").read_text(encoding="utf-8"))
        assert call["transition_accepted"] is False
        assert "constraint reference scope mismatch" in call["error"]
        assert gate.verify_run(root, "c003", source, output)["status"] == "stopped_on_failure"
        response_path = output / "step_005" / "response.txt"
        response_path.write_text(response_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with pytest.raises(ValueError, match="artifact hash mismatch"):
            gate.verify_run(root, "c003", source, output)
    finally:
        for path in (source, output):
            if path.exists():
                shutil.rmtree(path)


def test_archived_response_read_failure_stops_before_next_event(root):
    source, output = _path(root), _path(root)
    try:
        asyncio.run(m3d_gate.run(root, source, m3d_gate.prepare(root, Decimal("2")),
                                 PublicNoChangeProvider()))
        seen = []

        def fail_second(path):
            seen.append(path)
            if len(seen) == 2:
                raise OSError("scripted read failure")
            return path.read_text(encoding="utf-8")

        result = gate.run_offline(root, "c002", source, output, fail_second)
        assert result["status"] == "stopped_on_failure"
        assert result["attempted_events"] == len(seen) == 2
        assert not (output / "step_003").exists()
        assert not (output / "assessment.json").exists()
        assert gate.verify_run(root, "c002", source, output)["status"] == "stopped_on_failure"
    finally:
        for path in (source, output):
            if path.exists():
                shutil.rmtree(path)
