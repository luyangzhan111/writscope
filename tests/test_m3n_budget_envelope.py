"""M3n budget policy is offline and cannot authorize a paid run."""

from decimal import Decimal
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from test_m3l_offline_trace import _plan
from scripts.m3l_offline_trace import run_offline as run_trace
from scripts.m3m_final_review import run_offline as run_review
from scripts.m3n_budget_envelope import (AttemptLedger, MAX_PROVIDER_ATTEMPTS,
                                         run_offline, verify_run)
from writscope.artifacts import write_json
from writscope.budget import PriceCeiling
from writscope.contracts import parse_json


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c002"
PROMPT = (ROOT / "prompts/incremental_final_review_v1.md").read_text(encoding="utf-8")
PRICE = PriceCeiling(input_cny_per_million=Decimal("2"),
                     output_cny_per_million=Decimal("8"),
                     source_url="offline-test-price", checked_at_utc="test")


def _paths():
    stem = ROOT / "reports/local" / ("m3n-test-" + uuid4().hex)
    return Path(str(stem) + "-trace"), Path(str(stem) + "-review"), Path(str(stem) + "-envelope")


def _clean(*paths):
    for path in paths:
        if path.exists():
            shutil.rmtree(path)


def test_all_stages_counted_and_unknown_bills_stay_unknown():
    ledger = AttemptLedger(PRICE, Decimal("10"))
    assert ledger.full_run_floor() == Decimal("1.993216")
    for seq in range(1, 6):
        ledger.reserve("extract", seq, 100)
        ledger.finish("succeeded", input_tokens=20, output_tokens=10)
        if seq in (4, 5):
            ledger.record_local_lookup(seq, succeeded=True)
            ledger.reserve("lookup_continuation", seq, 100)
            ledger.finish("succeeded", input_tokens=20, output_tokens=10)
        ledger.accept_event(seq)
    ledger.reserve("final_review", None, 100)
    ledger.finish("succeeded", input_tokens=20, output_tokens=10)
    summary = ledger.summary()
    assert summary["provider_attempts"] == MAX_PROVIDER_ATTEMPTS
    assert summary["lookup_attempts"] == 2
    assert summary["unknown_billed_attempts"] == MAX_PROVIDER_ATTEMPTS
    assert summary["known_billed_subtotal_cny"] == "0"
    assert summary["reserved_cny"] != "0"


def test_no_retry_final_gate_and_failure_keep_reservation():
    ledger = AttemptLedger(PRICE, Decimal("10"))
    with pytest.raises(ValueError, match="complete extraction"):
        ledger.reserve("final_review", None, 100)
    assert ledger.summary()["stopped"]
    ledger = AttemptLedger(PRICE, Decimal("10"))
    ledger.reserve("extract", 1, 100)
    with pytest.raises(ValueError, match="active attempt"):
        ledger.reserve("extract", 1, 100)
    assert ledger.summary()["stopped"]
    ledger = AttemptLedger(PRICE, Decimal("10"))
    ledger.reserve("extract", 1, 100)
    ledger.finish("timeout")
    assert ledger.summary()["unknown_token_attempts"] == 1
    assert ledger.summary()["reserved_cny"] != "0"
    with pytest.raises(ValueError, match="prior failure"):
        ledger.reserve("extract", 1, 100)


def test_missing_usage_and_local_cap_stop_without_releasing_reservation():
    ledger = AttemptLedger(PRICE, Decimal("10"))
    ledger.reserve("extract", 1, 100)
    assert ledger.finish("succeeded")["status"] == "usage_missing"
    assert ledger.summary()["stopped"]
    with pytest.raises(ValueError, match="full-run reservation floor"):
        AttemptLedger(PRICE, Decimal("0.000001"))
    oversize = AttemptLedger(PRICE, Decimal("10"))
    with pytest.raises(ValueError, match="size limit"):
        oversize.reserve("extract", 1, 100_001)
    assert oversize.summary()["stopped"]
    assert oversize.summary()["provider_attempts"] == 0


def test_prepared_request_yields_blocked_replayable_envelope():
    trace, review, output = _paths()
    try:
        run_trace(BUNDLE, _plan(), trace)
        run_review(BUNDLE, trace, review, PROMPT)
        draft = run_offline(BUNDLE, trace, review, output)
        assert draft["status"] == "blocked_for_paid_run"
        assert draft["maximum_provider_attempts"] == 8
        assert draft["price_ceiling"] is None
        assert draft["authorized_local_cap_cny"] is None
        assert draft["actual_model_calls"] == 0
        assert verify_run(BUNDLE, trace, review, output) == draft
    finally:
        _clean(trace, review, output)


def test_incomplete_trace_and_tampered_envelope_rejected():
    trace, review, output = _paths()
    try:
        run_trace(BUNDLE, _plan(bad_response_at=3), trace)
        run_review(BUNDLE, trace, review, PROMPT)
        with pytest.raises(ValueError, match="complete trace"):
            run_offline(BUNDLE, trace, review, output)
    finally:
        _clean(trace, review, output)
    trace, review, output = _paths()
    try:
        run_trace(BUNDLE, _plan(), trace)
        run_review(BUNDLE, trace, review, PROMPT)
        run_offline(BUNDLE, trace, review, output)
        path = output / "envelope.json"
        draft = parse_json(path.read_text(encoding="utf-8"))
        draft["maximum_provider_attempts"] = 99
        write_json(path, draft)
        with pytest.raises(ValueError, match="replay mismatch"):
            verify_run(BUNDLE, trace, review, output)
    finally:
        _clean(trace, review, output)
