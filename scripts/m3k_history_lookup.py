"""Versioned, read-only history lookup over trusted visible events.

This offline adapter makes no model calls. Model-initiated lookup and its
provider costs require a later runner integration.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import time
from typing import Literal

from pydantic import Field, model_validator

from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import load_bundle, parse_json
from writscope.events import ConversationEvent, DecisionPoint, StrictModel


POLICY_VERSION = "history_lookup_v1_offline"


class LookupPolicy(StrictModel):
    max_requests: int = Field(ge=1, le=100)
    max_events_per_request: int = Field(ge=1, le=20)


class LookupRequest(StrictModel):
    event_ids: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_ids(self):
        if any(not item for item in self.event_ids) or len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("lookup event IDs must be nonempty and unique")
        return self


class LookupRecord(StrictModel):
    schema_version: Literal["0.1-draft"]
    policy_version: Literal["history_lookup_v1_offline"]
    attempt: int = Field(ge=1)
    request: LookupRequest
    request_sha256: str
    status: Literal["succeeded", "denied"]
    denial_reason: str | None
    events: list[ConversationEvent]
    response_sha256: str
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)


class HistoryLookupSession:
    """Only the trusted prefix may be returned; a denied lookup is all-or-nothing."""

    def __init__(self, events: list[ConversationEvent], point: DecisionPoint,
                 policy: LookupPolicy, *, initial_attempts: int = 0):
        if initial_attempts < 0:
            raise ValueError("negative prior lookup attempts")
        if [event.seq for event in events] != list(range(1, len(events) + 1)):
            raise ValueError("non-contiguous trusted history")
        if len({event.event_id for event in events}) != len(events):
            raise ValueError("duplicate trusted event ID")
        if point.seq > len(events) or events[point.seq - 1].event_id != point.event_id:
            raise ValueError("invalid decision point")
        self.point = point
        self.policy = policy
        self.initial_attempts = initial_attempts
        self.visible = list(events[:point.seq])
        self.by_id = {event.event_id: event for event in self.visible}
        self.visible_history_sha256 = canonical_hash([
            event.model_dump() for event in self.visible])
        self.records: list[LookupRecord] = []

    def lookup(self, request: LookupRequest) -> LookupRecord:
        started = time.perf_counter()
        attempt = self.initial_attempts + len(self.records) + 1
        reason = None
        if attempt > self.policy.max_requests:
            reason = "lookup request budget exhausted"
        elif len(request.event_ids) > self.policy.max_events_per_request:
            reason = "lookup event count exceeds limit"
        elif any(event_id not in self.by_id for event_id in request.event_ids):
            reason = "lookup event outside visible history"
        events = [] if reason else [self.by_id[event_id] for event_id in request.event_ids]
        record = LookupRecord(
            schema_version="0.1-draft", policy_version=POLICY_VERSION,
            attempt=attempt, request=request,
            request_sha256=canonical_hash(request.model_dump()),
            status="denied" if reason else "succeeded", denial_reason=reason,
            events=events,
            response_sha256=canonical_hash([event.model_dump() for event in events]),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        self.records.append(record)
        return record


def replay_lookups(events: list[ConversationEvent], point: DecisionPoint,
                   policy: LookupPolicy, records: list[LookupRecord], *,
                   initial_attempts: int = 0) -> HistoryLookupSession:
    session = HistoryLookupSession(events, point, policy,
                                   initial_attempts=initial_attempts)
    for saved in records:
        generated = session.lookup(saved.request)
        if (generated.model_dump(exclude={"elapsed_ms"}) !=
                saved.model_dump(exclude={"elapsed_ms"})):
            raise ValueError("lookup replay mismatch")
    return session


def run_offline(bundle_dir: Path, requests: list[LookupRequest], output: Path,
                policy: LookupPolicy) -> dict:
    bundle_dir, output = bundle_dir.resolve(), output.resolve()
    if output.is_relative_to(bundle_dir):
        raise ValueError("lookup output cannot modify public input")
    bundle = load_bundle(bundle_dir)
    review = bundle.review_input
    session = HistoryLookupSession(review.events, review.decision_point, policy)
    output.mkdir(parents=True, exist_ok=False)
    rows = [session.lookup(request) for request in requests]
    log = output / "lookups.jsonl"
    log.write_text("".join(json.dumps(row.model_dump(), ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":")) + "\n"
                           for row in rows), encoding="utf-8", newline="\n")
    manifest = {
        "schema_version": "0.1-draft", "execution_kind": "offline_manual_lookup",
        "policy_version": POLICY_VERSION, "case_id": review.case_id,
        "decision_point": review.decision_point.model_dump(),
        "policy": policy.model_dump(), "public_bundle_sha256": bundle.sha256,
        "visible_history_sha256": session.visible_history_sha256,
        "attempts": len(rows), "denied_attempts": sum(row.status == "denied" for row in rows),
        "lookup_elapsed_ms": round(sum(row.elapsed_ms for row in rows), 3),
        "new_model_calls": 0, "model_usage": None,
        "log_sha256": digest(log.read_bytes()),
        "runner_sha256": digest(Path(__file__).read_bytes()),
    }
    write_json(output / "run.json", manifest)
    return manifest


def verify_run(bundle_dir: Path, output: Path) -> dict:
    bundle = load_bundle(bundle_dir)
    manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    log = output / "lookups.jsonl"
    if digest(log.read_bytes()) != manifest.get("log_sha256"):
        raise ValueError("lookup log hash mismatch")
    if digest(Path(__file__).read_bytes()) != manifest.get("runner_sha256"):
        raise ValueError("lookup runner hash mismatch")
    records = [LookupRecord.model_validate(parse_json(line))
               for line in log.read_text(encoding="utf-8").splitlines()]
    review = bundle.review_input
    policy = LookupPolicy.model_validate(manifest["policy"])
    session = replay_lookups(review.events, review.decision_point, policy, records)
    expected = {
        "schema_version": "0.1-draft", "execution_kind": "offline_manual_lookup",
        "policy_version": POLICY_VERSION, "case_id": review.case_id,
        "decision_point": review.decision_point.model_dump(),
        "policy": policy.model_dump(), "public_bundle_sha256": bundle.sha256,
        "visible_history_sha256": session.visible_history_sha256,
        "attempts": len(records),
        "denied_attempts": sum(row.status == "denied" for row in records),
        "lookup_elapsed_ms": round(sum(row.elapsed_ms for row in records), 3),
        "new_model_calls": 0, "model_usage": None,
        "log_sha256": digest(log.read_bytes()),
        "runner_sha256": digest(Path(__file__).read_bytes()),
    }
    if manifest != expected:
        raise ValueError("lookup manifest mismatch")
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3k offline public-history lookup")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--requests", type=Path, help="JSON array of manual lookup requests")
    parser.add_argument("--max-requests", type=int, default=6)
    parser.add_argument("--max-events-per-request", type=int, default=5)
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.requests is None:
            parser.error("--requests is required for run")
        requests = [LookupRequest.model_validate(item) for item in
                    parse_json(args.requests.read_text(encoding="utf-8"))]
        result = run_offline(args.bundle, requests, args.output,
                             LookupPolicy(max_requests=args.max_requests,
                                          max_events_per_request=args.max_events_per_request))
    else:
        result = verify_run(args.bundle, args.output)
    print(json.dumps({"attempts": result["attempts"],
                      "denied_attempts": result["denied_attempts"],
                      "new_model_calls": result["new_model_calls"]}))


if __name__ == "__main__":
    main()
