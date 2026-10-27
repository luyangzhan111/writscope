"""Offline history lookup: strict cutoff, accounting, and replay."""

import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from scripts.m3k_history_lookup import (HistoryLookupSession, LookupPolicy,
                                        LookupRequest, replay_lookups,
                                        run_offline, verify_run)
from writscope.artifacts import digest, write_json
from writscope.contracts import load_bundle, parse_json
from writscope.events import ConversationEvent, Provenance


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c002"


def _review():
    return load_bundle(BUNDLE).review_input


def test_only_trusted_visible_events_and_request_order_returned():
    review = _review()
    future = ConversationEvent(
        schema_version="0.1-draft", event_id="e006", seq=6, role="user",
        content="FUTURE_SECRET_SENTINEL", provenance=Provenance(
            origin="synthetic_fixture", authority_level="user", artifact_refs=[]))
    session = HistoryLookupSession([*review.events, future], review.decision_point,
                                   LookupPolicy(max_requests=4, max_events_per_request=3))
    good = session.lookup(LookupRequest(event_ids=["e004", "e001"]))
    assert good.status == "succeeded"
    assert [event.event_id for event in good.events] == ["e004", "e001"]
    assert good.events[0].content == review.events[3].content
    assert "FUTURE_SECRET_SENTINEL" not in str(good.model_dump())
    denied = session.lookup(LookupRequest(event_ids=["e001", "e006"]))
    assert denied.status == "denied" and denied.events == []
    assert denied.denial_reason == "lookup event outside visible history"
    assert replay_lookups([*review.events, future], review.decision_point,
                          session.policy, session.records).visible_history_sha256 == session.visible_history_sha256


def test_tool_event_keeps_its_role_and_cannot_supply_authority():
    review = _review()
    session = HistoryLookupSession(review.events, review.decision_point,
                                   LookupPolicy(max_requests=1, max_events_per_request=1))
    record = session.lookup(LookupRequest(event_ids=["e002"]))
    assert record.events[0].role == "tool"
    assert record.events[0].provenance.authority_level == "none"


def test_request_limits_denials_and_duplicate_validation():
    review = _review()
    session = HistoryLookupSession(review.events, review.decision_point,
                                   LookupPolicy(max_requests=2, max_events_per_request=1))
    with pytest.raises(ValueError, match="unique"):
        LookupRequest(event_ids=["e001", "e001"])
    assert session.lookup(LookupRequest(event_ids=["e001", "e002"])).status == "denied"
    assert session.lookup(LookupRequest(event_ids=["e005"])).status == "succeeded"
    exhausted = session.lookup(LookupRequest(event_ids=["e001"]))
    assert exhausted.status == "denied" and exhausted.events == []
    assert exhausted.denial_reason == "lookup request budget exhausted"
    assert [row.attempt for row in session.records] == [1, 2, 3]


def test_saved_run_replays_and_detects_tampering():
    output = ROOT / "reports/local" / ("m3k-test-" + uuid4().hex)
    try:
        requests = [LookupRequest(event_ids=["e001", "e004"]),
                    LookupRequest(event_ids=["e999"])]
        manifest = run_offline(BUNDLE, requests, output,
                               LookupPolicy(max_requests=3, max_events_per_request=2))
        assert manifest["new_model_calls"] == 0 and manifest["model_usage"] is None
        assert manifest["attempts"] == 2 and manifest["denied_attempts"] == 1
        assert verify_run(BUNDLE, output) == manifest
        log = output / "lookups.jsonl"
        rows = [parse_json(line) for line in log.read_text(encoding="utf-8").splitlines()]
        rows[0]["events"][0]["content"] = "tampered"
        log.write_text("".join(json.dumps(row, ensure_ascii=False,
                                         sort_keys=True, separators=(",", ":")) + "\n"
                               for row in rows), encoding="utf-8", newline="\n")
        saved = parse_json((output / "run.json").read_text(encoding="utf-8"))
        saved["log_sha256"] = digest(log.read_bytes())
        write_json(output / "run.json", saved)
        with pytest.raises(ValueError, match="replay mismatch"):
            verify_run(BUNDLE, output)
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_invalid_decision_point_refused():
    review = _review()
    wrong = review.decision_point.model_copy(update={"event_id": "e004"})
    with pytest.raises(ValueError, match="invalid decision point"):
        HistoryLookupSession(review.events, wrong,
                             LookupPolicy(max_requests=1, max_events_per_request=1))
