"""Versioned offline scope-reference policy; never changes historical runs.

This module lives outside the package source tree so M3c/M3d/M3e source
provenance and saved replay remain intact. It makes no provider calls.
"""

import argparse
from pathlib import Path

from scripts import m3e_gate
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import load_bundle, parse_json
from writscope.m3b import _write_jsonl, _proposal
from writscope.state import StateEngine, TransitionProposal


POLICY_VERSION = "scope_ref_v2_offline"


class ScopeValidatedStateEngine(StateEngine):
    """Reject inconsistent local exceptions before the v1 kernel applies them."""

    def _apply(self, proposal, event, ref, constraints, authorizations, exceptions):
        if proposal.operation in {"grant", "supersede"} and proposal.authorization is not None:
            draft = proposal.authorization
            problems = []
            if any(subject.field != draft.change_details.old_field for subject in draft.subjects):
                problems.append("authorization subject field differs from old_field")
            for constraint_id in draft.constraint_refs:
                constraint = constraints.get(constraint_id)
                if constraint is None:
                    continue  # The unchanged kernel reports an unknown ID.
                if constraint.lifecycle != "active":
                    problems.append("constraint reference is inactive: " + constraint_id)
                elif not any(subject in constraint.subjects for subject in draft.subjects):
                    problems.append("constraint reference scope mismatch: " + constraint_id)
            if problems:
                raise ValueError("; ".join(problems))
        return super()._apply(proposal, event, ref, constraints, authorizations, exceptions)


def replay_saved_m3e(root: Path, source_run: Path):
    """Diagnose saved model proposals only; no scoring labels or network access."""
    original = m3e_gate.verify_run(root, source_run)
    if original["status"] != "completed" or set(original["cases"]) != {"c003"}:
        raise ValueError("expected a completed c003 real run")
    preflight = parse_json((source_run / "preflight.json").read_text(encoding="utf-8"))
    bundle = load_bundle(m3e_gate._input_root(root) / "c003")
    config_hash = canonical_hash({"prompt_sha256": preflight["prompt_sha256"],
                                  "model": m3e_gate.MODEL.model_dump(),
                                  "output_contract": "transition_quote_v1"})
    engine = ScopeValidatedStateEngine("c003", bundle.review_input.events,
                                       config_hash, set(bundle.registry.artifacts))
    rows = []
    for index, event in enumerate(bundle.review_input.events):
        step = source_run / "c003" / f"step_{event.seq:03d}"
        raw = (step / "response.txt").read_text(encoding="utf-8")
        proposal: TransitionProposal = _proposal(raw, bundle, index, config_hash,
                                                  f"c003_extract_{event.seq:03d}")
        entry = engine.submit(proposal)
        rows.append({"event_id": event.event_id, "operation": proposal.operation,
                     "source_response_sha256": digest((step / "response.txt").read_bytes()),
                     "accepted_under_v2": entry.transition.accepted,
                     "rejection_reason": entry.transition.rejection_reason})
        if not entry.transition.accepted:
            break
    report = {"schema_version": "0.1-draft", "execution_kind": "offline_policy_diagnostic",
              "policy_version": POLICY_VERSION, "policy_source_sha256": digest(Path(__file__).read_bytes()),
              "source_run_artifact_set_sha256": original["artifact_set_sha256"],
              "source_run_attempts": original["attempts"], "new_model_calls": 0,
              "diagnostic_events": len(rows),
              "status": "rejected" if rows and not rows[-1]["accepted_under_v2"] else "accepted",
              "rows": rows, "resulting_state_sha256": engine.snapshot().state_hash,
              "comparison_status": "counterfactual_policy_replay_not_model_result"}
    return report, engine


def run_diagnostic(root: Path, source_run: Path, output: Path) -> dict:
    if output.resolve().is_relative_to(source_run.resolve()):
        raise ValueError("diagnostic cannot modify its source run")
    report, engine = replay_saved_m3e(root, source_run)
    output.mkdir(parents=True, exist_ok=False)
    _write_jsonl(output / "transitions.jsonl", engine.records)
    write_json(output / "state.json", engine.snapshot().model_dump())
    write_json(output / "diagnostic.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3f offline scope-reference diagnostic")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_diagnostic(args.root.resolve(), args.source_run.resolve(), args.output.resolve())
    print(f"M3f offline {result['status']}: {result['diagnostic_events']} saved events, "
          f"new_model_calls={result['new_model_calls']}")


if __name__ == "__main__":
    main()
