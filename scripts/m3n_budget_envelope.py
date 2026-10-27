"""Offline call envelope and reservation rules for a future complete M3 run.

This module has no provider, credential access, or paid-run entry point.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from scripts.m3l_offline_trace import verify_run as verify_trace
from scripts.m3m_final_review import verify_run as verify_review
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.budget import PriceCeiling
from writscope.contracts import load_bundle, parse_json


VERSION = "m3n_full_run_envelope_draft_v1"
MAX_EVENTS = 5
MAX_LOOKUPS = 2
MAX_PROVIDER_ATTEMPTS = MAX_EVENTS + MAX_LOOKUPS + 1
MAX_WIRE_BYTES = 100_000
MAX_OUTPUT_TOKENS = 4096
TIMEOUT_SECONDS = 90


class AttemptLedger:
    """Sequential, no-retry reservation ledger for offline policy tests.

    A future real runner must use this rule before each provider call and save
    each attempt, including timeouts and failures. This class makes no calls.
    """

    def __init__(self, price: PriceCeiling, local_cap_cny: Decimal):
        if not local_cap_cny.is_finite() or local_cap_cny <= 0:
            raise ValueError("positive finite local cap required")
        self.price = price
        self.cap = local_cap_cny
        self.reserved = Decimal("0")
        self.attempts: list[dict] = []
        self.active: dict | None = None
        self.accepted_events = 0
        self.extract_attempted: set[int] = set()
        self.lookup_count = 0
        self.pending_continuation = 0
        self.final_attempted = False
        self.stopped = False
        if local_cap_cny < self.full_run_floor():
            raise ValueError("local cap below full-run reservation floor")

    def quote(self, wire_size: int) -> Decimal:
        if not 0 < wire_size <= MAX_WIRE_BYTES:
            raise ValueError("wire request outside configured size limit")
        return ((Decimal(wire_size + self.price.framing_token_allowance)
                 * self.price.input_cny_per_million
                 + Decimal(MAX_OUTPUT_TOKENS) * self.price.output_cny_per_million)
                / Decimal(1_000_000))

    def _fail(self, message: str):
        self.stopped = True
        raise ValueError(message)

    def full_run_floor(self) -> Decimal:
        return self.quote(MAX_WIRE_BYTES) * MAX_PROVIDER_ATTEMPTS

    def reserve(self, stage: str, event_seq: int | None, wire_size: int) -> dict:
        if self.stopped or self.active is not None:
            self._fail("prior failure or active attempt blocks next call")
        if len(self.attempts) >= MAX_PROVIDER_ATTEMPTS:
            self._fail("provider attempt limit exhausted")
        if stage == "extract":
            if (event_seq != self.accepted_events + 1 or
                    event_seq in self.extract_attempted or event_seq > MAX_EVENTS):
                self._fail("extraction event out of order or retried")
        elif stage == "lookup_continuation":
            if event_seq != self.accepted_events + 1 or self.pending_continuation != 1:
                self._fail("lookup continuation requires one pending lookup")
        elif stage == "final_review":
            if event_seq is not None or self.accepted_events != MAX_EVENTS or self.final_attempted:
                self._fail("final review requires complete extraction and one attempt")
        else:
            self._fail("unknown provider stage")
        try:
            charge = self.quote(wire_size)
        except ValueError:
            self.stopped = True
            raise
        if self.reserved + charge > self.cap:
            self.stopped = True
            raise ValueError("local reservation cap exhausted")
        if stage == "extract":
            self.extract_attempted.add(event_seq)
        elif stage == "lookup_continuation":
            self.pending_continuation = 0
        else:
            self.final_attempted = True
        self.reserved += charge
        attempt = {"stage": stage, "event_seq": event_seq, "wire_bytes": wire_size,
                   "reserved_cny": str(charge), "status": "reserved",
                   "input_tokens": None, "output_tokens": None,
                   "billed_cost_cny": None}
        self.attempts.append(attempt)
        self.active = attempt
        return attempt.copy()

    def finish(self, status: str, *, input_tokens: int | None = None,
               output_tokens: int | None = None,
               billed_cost_cny: Decimal | None = None) -> dict:
        if self.active is None or status not in {"succeeded", "timeout", "provider_error",
                                                 "invalid_output", "state_rejected"}:
            self._fail("no active attempt or invalid outcome")
        if any(value is not None and value < 0 for value in
               (input_tokens, output_tokens, billed_cost_cny)):
            self._fail("negative provider usage")
        if status == "succeeded" and (input_tokens is None or output_tokens is None):
            status = "usage_missing"
        self.active.update(status=status, input_tokens=input_tokens,
                           output_tokens=output_tokens,
                           billed_cost_cny=(str(billed_cost_cny)
                                            if billed_cost_cny is not None else None))
        result = self.active.copy()
        self.active = None
        if status != "succeeded":
            self.stopped = True
        return result

    def record_local_lookup(self, event_seq: int, *, succeeded: bool) -> None:
        if (self.stopped or self.active is not None or
                event_seq != self.accepted_events + 1 or
                event_seq not in self.extract_attempted or
                self.pending_continuation or self.lookup_count >= MAX_LOOKUPS):
            self._fail("lookup outside current event or budget")
        self.lookup_count += 1
        if not succeeded:
            self.stopped = True
        else:
            self.pending_continuation = 1

    def accept_event(self, event_seq: int) -> None:
        if (self.stopped or self.active is not None or self.pending_continuation or
                event_seq != self.accepted_events + 1 or
                event_seq not in self.extract_attempted or
                not self.attempts or self.attempts[-1]["status"] != "succeeded" or
                self.attempts[-1]["event_seq"] != event_seq):
            self._fail("event cannot be accepted before valid extraction")
        self.accepted_events += 1

    def summary(self) -> dict:
        return {"provider_attempts": len(self.attempts),
                "lookup_attempts": self.lookup_count,
                "events_accepted": self.accepted_events,
                "reserved_cny": str(self.reserved),
                "known_billed_subtotal_cny": str(sum(
                    (Decimal(row["billed_cost_cny"]) for row in self.attempts
                     if row["billed_cost_cny"] is not None), Decimal("0"))),
                "unknown_billed_attempts": sum(
                    row["billed_cost_cny"] is None for row in self.attempts),
                "unknown_token_attempts": sum(
                    row["input_tokens"] is None or row["output_tokens"] is None
                    for row in self.attempts),
                "stopped": self.stopped}


def draft_envelope(bundle_dir: Path, trace_dir: Path, review_dir: Path) -> dict:
    bundle = load_bundle(bundle_dir)
    trace = verify_trace(bundle_dir, trace_dir)
    review = verify_review(bundle_dir, trace_dir, review_dir)
    if (trace["status"] != "completed" or review["status"] != "prepared" or
            len(bundle.review_input.events) != MAX_EVENTS):
        raise ValueError("draft requires complete trace and prepared final request")
    return {"schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "offline_policy_draft", "case_id": bundle.review_input.case_id,
            "status": "blocked_for_paid_run", "actual_model_calls": 0,
            "public_bundle_sha256": bundle.sha256,
            "trace_artifact_set_sha256": trace["artifact_set_sha256"],
            "review_artifact_set_sha256": review["artifact_set_sha256"],
            "evidence_sha256": review["evidence_sha256"],
            "maximum_provider_attempts": MAX_PROVIDER_ATTEMPTS,
            "stage_limits": {"extract": MAX_EVENTS,
                             "lookup_continuation": MAX_LOOKUPS,
                             "final_review": 1},
            "local_lookup_limit": MAX_LOOKUPS,
            "max_wire_bytes_per_attempt": MAX_WIRE_BYTES,
            "max_output_tokens_per_attempt": MAX_OUTPUT_TOKENS,
            "timeout_seconds_per_attempt": TIMEOUT_SECONDS,
            "concurrency": 1, "extract_attempts_per_event": 1,
            "continuations_per_lookup": 1, "final_review_attempts": 1,
            "retries": 0,
            "failure_rule": "first provider, usage, output, lookup, state, or budget failure stops; no final review unless all events accepted",
            "reservation_rule": "reserve each attempt before sending; keep reservation after timeout or missing usage; total includes extraction, lookup continuation, and final review",
            "price_ceiling": None, "authorized_local_cap_cny": None,
            "worst_case_reservation_cny": None,
            "unresolved": ["fresh official price and provider billing boundary",
                           "new user-approved total cap covering all stages",
                           "real provider request for atomic batch and task context",
                           "model-initiated lookup and provider continuation",
                           "real final-review model config and call/replay"],
            "runner_sha256": digest(Path(__file__).read_bytes())}


def run_offline(bundle_dir: Path, trace_dir: Path, review_dir: Path, output: Path) -> dict:
    bundle_dir, trace_dir, review_dir, output = (
        path.resolve() for path in (bundle_dir, trace_dir, review_dir, output))
    if any(output.is_relative_to(path) for path in (bundle_dir, trace_dir, review_dir)):
        raise ValueError("envelope output cannot modify source inputs")
    draft = draft_envelope(bundle_dir, trace_dir, review_dir)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "envelope.json", draft)
    write_json(output / "run.json", {
        "schema_version": "0.1-draft", "version": VERSION,
        "execution_kind": "offline_policy_draft", "actual_model_calls": 0,
        "envelope_sha256": canonical_hash(draft),
        "envelope_file_sha256": digest((output / "envelope.json").read_bytes())})
    return draft


def verify_run(bundle_dir: Path, trace_dir: Path, review_dir: Path, output: Path) -> dict:
    draft = draft_envelope(bundle_dir, trace_dir, review_dir)
    saved = parse_json((output / "envelope.json").read_text(encoding="utf-8"))
    run = parse_json((output / "run.json").read_text(encoding="utf-8"))
    if saved != draft or run != {
            "schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "offline_policy_draft", "actual_model_calls": 0,
            "envelope_sha256": canonical_hash(draft),
            "envelope_file_sha256": digest((output / "envelope.json").read_bytes())}:
        raise ValueError("budget envelope replay mismatch")
    return draft


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3n offline full-run budget envelope")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    command = run_offline if args.command == "run" else verify_run
    result = command(args.bundle, args.trace, args.review, args.output)
    print(result["status"])


if __name__ == "__main__":
    main()
