"""M3r freezes a reviewable plan and blocks credentials until authorization."""

from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from scripts.m3r_real_preflight import (CAP_CNY, CASE, MAX_PROVIDER_ATTEMPTS,
                                        prepare, require_authorized_key,
                                        run_preflight, verify_preflight)
from writscope.artifacts import canonical_hash, write_json
from writscope.contracts import parse_json
from writscope.m3b import _artifacts


ROOT = Path(__file__).resolve().parents[1]


def test_c003_complete_preflight_and_replay_with_no_provider_call():
    output = ROOT / "reports/local" / ("m3r-test-" + uuid4().hex)
    try:
        report = run_preflight(ROOT, output)
        assert verify_preflight(ROOT, output) == report
        plan = parse_json((output / "plan.json").read_text(encoding="utf-8"))
        assert plan["case_ids"] == ["c003"]
        assert plan["policy"]["max_provider_attempts"] == 8
        assert plan["policy"]["retries"] == 0
        assert plan["full_run_worst_reservation_cny"] == "2.989824"
        assert plan["local_cap_cny"] == "3.00"
        assert plan["actual_model_calls"] == 0
        assert plan["new_paid_authorization"] is None

        plan["policy"]["retries"] = 1
        write_json(output / "plan.json", plan)
        manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
        manifest["plan_sha256"] = canonical_hash(plan)
        manifest["artifacts"] = _artifacts(output)
        manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
        write_json(output / "run.json", manifest)
        with pytest.raises(ValueError, match="preflight plan differs"):
            verify_preflight(ROOT, output)
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_credential_gate_checks_direct_user_scope_before_reader():
    plan, _ = prepare(ROOT)
    reads = []

    def reader():
        reads.append(True)
        return "dummy-not-a-real-key"

    with pytest.raises(ValueError, match="authorization required"):
        require_authorized_key(plan, None, reader)
    with pytest.raises(ValueError, match="authorization required"):
        require_authorized_key(plan, {"status": "approved"}, reader)
    assert reads == []

    receipt = {"status": "approved", "source_kind": "direct_user_message",
               "scope": "M3s_c003_complete_incremental",
               "plan_sha256": canonical_hash(plan), "case_ids": [CASE],
               "local_cap_cny": str(CAP_CNY),
               "max_provider_attempts": MAX_PROVIDER_ATTEMPTS,
               "user_authorization_ref": "test-only-user-receipt"}
    assert require_authorized_key(plan, receipt, reader) == "dummy-not-a-real-key"
    assert reads == [True]
