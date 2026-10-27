"""Offline diagnosis of saved c002 structure failure; no paid launch command."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path

from pydantic import ValidationError

from scripts import m3o_interaction_contract as v1
from scripts import s3_interaction_v2 as v2
from scripts import s3_common_round as common
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import parse_json
from writscope.providers.deepseek import wire_bytes

VERSION = "s3_proposal_shape_offline_v1"
SOURCE = "reports/local/s3-common-real-20261026"
PREPARED = "reports/local/s3-common-preflight-20261025-final-v2"


def replay_prefix(root, case, rows, *, version=2):
    data = common.bundle(root, case)
    prompt_name = "incremental_interaction_v2.md" if version == 2 else "incremental_interaction_v1.md"
    prompt = (root / "prompts" / prompt_name).read_text(encoding="utf-8")
    final = (root / common.PROMPTS["final"]).read_text(encoding="utf-8")
    config = (v2.config_hash(prompt, final, common.MODEL, common.POLICY) if version == 2 else
              common.core._config_hash(prompt, final, common.MODEL, common.POLICY))
    contract = v2 if version == 2 else v1
    state = BatchStateEngine(case, data.review_input.events, config, set(data.registry.artifacts))
    context = TaskContextLedger(data.review_input.events, set(data.registry.artifacts))
    steps = []
    for index, row in enumerate(rows):
        if row["stage"] != "extract" or row["event_seq"] != index+1:
            raise ValueError("diagnosis expects this saved no-lookup prefix, not a general runner")
        request = contract.build_request(data, index, state.snapshot(), context.snapshot(), prompt,
                                         common.MODEL, common.POLICY)
        before_state, before_context = state.snapshot().state_hash, context.snapshot().state_hash
        step = {"event_seq": index+1, "source_call_id": row["call_id"],
                "response_sha256": digest(row["response_text"].encode()),
                "request": request.model_dump(), "wire_bytes": len(wire_bytes(request)),
                "accepted": False, "errors": None}
        try:
            parsed = contract.parse_response(row["response_text"], data, index, config,
                                             row["call_id"], common.POLICY)
            if isinstance(parsed, v1.LookupIntent):
                raise ValueError("unexpected lookup in this saved extraction prefix")
            staged = deepcopy(context)
            staged.submit(parsed.context_batch)
            result = state.submit_batch(parsed.transition_batch)
            if not result.accepted:
                raise ValueError(result.rejection_reason)
            context = staged
            step["accepted"] = True
        except ValueError as exc:
            step["errors"] = (exc.errors(include_url=False, include_context=False, include_input=False)
                              if isinstance(exc, ValidationError) else str(exc))
        step.update(state_changed=before_state != state.snapshot().state_hash,
                    context_changed=before_context != context.snapshot().state_hash)
        steps.append(step)
        if not step["accepted"]:
            break
    return {"version": version, "case_id": case, "steps": steps,
            "accepted_events": sum(step["accepted"] for step in steps),
            "state": state.snapshot().model_dump(), "task_context": context.snapshot().model_dump(),
            "final_model_review": "not_run"}


def synthetic_shape_control(row):
    """Explicit evaluator fixture, NEVER an automatic repair in a model path."""
    control = deepcopy(row)
    value = parse_json(control["response_text"])
    item = value["state_items"][0]
    if item["operation"] != "grant" or len(value["state_items"]) != 2:
        raise ValueError("saved failure differs from frozen diagnosis target")
    item["target_ids"] = []
    item["authorization"]["supersedes"] = []
    value["state_items"] = [item]
    control.update(call_id="synthetic_shape_control_e005",
                   response_text=json.dumps(value, ensure_ascii=False))
    return control


def build_audit(root):
    source = root / SOURCE
    calls = common.read(source / "calls.json")
    by_case = {case: [row for row in calls if row["job_id"] == job]
               for case, job in (("c001", "job_003"), ("c002", "job_005"))}
    if [len(by_case[case]) for case in ("c001", "c002")] != [6, 5]:
        raise ValueError("unexpected saved job coverage")
    # Only extraction rows: terminal text cannot be transplanted into new states.
    observed = {case: [row for row in rows if row["stage"] == "extract"] for case, rows in by_case.items()}
    bad = observed["c002"][-1]
    generic_v1_accepts = v1.RESPONSE_ADAPTER.validate_json(bad["response_text"]) is not None
    original = replay_prefix(root, "c002", observed["c002"], version=1)
    upgraded = {case: replay_prefix(root, case, rows) for case, rows in observed.items()}
    prefix = replay_prefix(root, "c002", observed["c002"][:4])
    control = replay_prefix(root, "c002", observed["c002"][:4] + [synthetic_shape_control(bad)])
    if (original["accepted_events"] != 4 or upgraded["c002"]["accepted_events"] != 4 or
            upgraded["c001"]["accepted_events"] != 5 or control["accepted_events"] != 5):
        raise ValueError("unexpected offline structural diagnosis outcome")
    rejected = upgraded["c002"]["steps"][-1]
    if rejected["state_changed"] or rejected["context_changed"]:
        raise ValueError("shape failure must not advance state or context")
    constraints_preserved = prefix["state"]["constraints"] == control["state"]["constraints"]
    if not constraints_preserved:
        raise ValueError("synthetic local grant must not remove broader constraints")
    # Build all three histories from synthetic no_change transport states, no reference state.
    previews = []
    for case in common.CASES:
        data = common.bundle(root, case)
        rows = []
        for event in data.review_input.events:
            raw = {"kind": "extract", "task_context": [], "state_items": [{
                "operation": "no_change", "source_quote": event.content, "target_ids": [],
                "constraint": None, "authorization": None}]}
            rows.append({"stage": "extract", "event_seq": event.seq, "call_id": f"preview_{event.seq}",
                         "response_text": json.dumps(raw, ensure_ascii=False)})
        previews.append(replay_prefix(root, case, rows))
    max_wire = max(step["wire_bytes"] for result in previews for step in result["steps"])
    if max_wire > 100000:
        raise ValueError("v2 schema exceeds existing wire envelope")
    files = ["scripts/s3_interaction_v2.py", "scripts/s3_proposal_shape_diagnosis.py",
             "prompts/incremental_interaction_v2.md", "scripts/m3o_interaction_contract.py",
             "scripts/m3j_transition_batch.py", "src/writscope/state.py", "uv.lock",
             SOURCE+"/run.json", SOURCE+"/calls.json", SOURCE+"/jobs.json"]
    return {"version": VERSION, "execution_kind": "offline_structure_diagnostic_no_model_predictions",
            "new_actual_model_calls": 0, "source_historical_model_calls": len(calls),
            "v1_model_schema_accepts_failed_payload": generic_v1_accepts,
            "v1_historical_shape_reproduction": original, "v2_saved_response_diagnostics": upgraded,
            "synthetic_control_kind": "explicit_shape_only_fixture_not_semantically_adjudicated",
            "synthetic_control_edits": ["grant.target_ids=[]", "grant.authorization.supersedes=[]",
                                        "drop_unreached_bare_supersede_item"],
            "synthetic_control": control, "synthetic_control_keeps_prior_constraints": constraints_preserved,
            "preview_state_kind": "all_no_change_transport_only_not_reference_state", "previews": previews,
            "maximum_preview_wire_bytes": max_wire, "response_schema": v2.RESPONSE_ADAPTER.json_schema(),
            "real_v2_runner": "not_integrated_not_authorized", "model_effectiveness": "not_measured",
            "reference_semantics": "not_read_or_scored", "authorizations_reusable": False,
            "source_sha256": {path: digest((root/path).read_bytes()) for path in files}}


def run(root, output, *, verify=False):
    if output.resolve().parent != root.resolve() / "reports/local":
        raise ValueError("diagnosis requires fresh direct child of reports/local")
    # Same-source saved replay only; this never calls a provider or reads credentials.
    asyncio.run(common.verify(root, root / PREPARED, root / SOURCE))
    audit = build_audit(root)
    if verify:
        if common.read(output / "audit.json") != json.loads(json.dumps(audit)):
            raise ValueError("offline diagnosis reconstruction mismatch")
        if common.read(output / "run.json") != {"version": VERSION, "new_actual_model_calls": 0,
                "audit_sha256": digest((output/"audit.json").read_bytes())}:
            raise ValueError("offline diagnosis hash mismatch")
    else:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "audit.json", audit)
        write_json(output / "run.json", {"version": VERSION, "new_actual_model_calls": 0,
                   "audit_sha256": digest((output/"audit.json").read_bytes())})
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    audit = run(args.root, args.output, verify=args.command == "verify")
    print(json.dumps({"status": "offline_verified", "new_actual_model_calls": 0,
                      "maximum_preview_wire_bytes": audit["maximum_preview_wire_bytes"]}))


if __name__ == "__main__":
    main()
