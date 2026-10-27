"""Evaluator-only v3 lifecycle graph alignment over saved synthetic boundaries.

No method runner or model contract is changed. Source audit replay precedes new
analysis. ID-renamed trajectories are synthetic identity controls, not models.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path

from scripts import s3_4_semantic_boundary_audit as source_audit
from scripts.m3j_transition_batch import BatchStateEngine, BatchRecord, TransitionBatch
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import parse_json
from writscope.events import ConversationEvent, Provenance
from writscope.state import AuthorizationState, verify_state_hash

VERSION = "s3_4_lifecycle_alignment_v3"


def _read(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def _sorted(items: list) -> list:
    return sorted(items, key=canonical_hash)


def _refs(refs: list[str], mapping: dict[str, str]) -> list[str]:
    if len(refs) != len(set(refs)) or any(ref not in mapping for ref in refs):
        raise ValueError("lifecycle_graph_invalid: duplicate, dangling, or wrong-type reference")
    return sorted(mapping[ref] for ref in refs)


def project_graph(raw: dict, trusted_events: list[ConversationEvent]) -> dict:
    """Preserve lifecycle facts and typed history edges independently of IDs.

    Intrinsic source/time/edge checks supplement replay; this snapshot function
    alone does not prove that a lifecycle update was authorized by a transition.
    """
    state = AuthorizationState.model_validate(raw)
    verify_state_hash(state)
    # Projection owns its nested values; later caller mutations must not rewrite
    # a graph already used as the independent side of a comparison.
    raw = deepcopy(state.model_dump())
    if state.task_context:
        raise ValueError("lifecycle_graph_unsupported: task context uses its separate ledger")
    if ([event.seq for event in trusted_events] != list(range(1, len(trusted_events) + 1)) or
            len({event.event_id for event in trusted_events}) != len(trusted_events) or
            state.through_seq > len(trusted_events)):
        raise ValueError("lifecycle_graph_invalid: trusted history order or cutoff")
    prefix = trusted_events[:state.through_seq]
    if (state.through_event_id != (prefix[-1].event_id if prefix else "") or
            state.source_history_sha256 != canonical_hash([event.model_dump() for event in prefix])):
        raise ValueError("lifecycle_graph_invalid: source history hash or decision point")
    events = {event.event_id: event for event in prefix}
    maps = {"constraint": {}, "authorization": {}, "exception": {}}
    items = {"constraint": {}, "authorization": {}}
    seen_ids, seen_anchors = set(), set()
    for kind, collection in (("constraint", raw["constraints"]), ("authorization", raw["authorizations"])):
        for item in collection:
            event = events.get(item["source_event_id"])
            quote = item["source_quote"]
            if (event is None or event.role != "user" or event.provenance.authority_level != "user" or
                    event.content.find(quote) < 0 or
                    event.content.find(quote, event.content.find(quote) + 1) >= 0 or
                    item["source_span"] != {"start": event.content.find(quote),
                                             "end": event.content.find(quote) + len(quote)} or
                    item["valid_from"] != {"event_id": event.event_id, "seq": event.seq}):
                raise ValueError("lifecycle_graph_invalid: item source or creation time")
            updates = [events.get(event_id) for event_id in item["updated_by"]]
            if (any(update is None or update.role != "user" or
                    update.provenance.authority_level != "user" or update.seq <= event.seq for update in updates) or
                    len({update.event_id for update in updates}) != len(updates) or
                    [update.seq for update in updates] != sorted(update.seq for update in updates)):
                raise ValueError("lifecycle_graph_invalid: update source, order, or future event")
            if ((item["lifecycle"] != "active" and not updates) or
                    (kind == "constraint" and item["lifecycle"] == "revoked")):
                raise ValueError("lifecycle_graph_invalid: unsupported lifecycle history")
            anchor = canonical_hash({"kind": kind, "source_event_id": item["source_event_id"],
                                     "source_quote": quote, "subjects": _sorted(item["subjects"])})
            item_id = item[kind + "_id"]
            if item_id in seen_ids or anchor in seen_anchors:
                raise ValueError("lifecycle_graph_ambiguous: duplicate ID or source/scope anchor")
            seen_ids.add(item_id)
            seen_anchors.add(anchor)
            maps[kind][item_id], items[kind][item_id] = anchor, item
    facts, edges = [], []
    for kind in ("constraint", "authorization"):
        successor_counts = Counter()
        for item_id, item in items[kind].items():
            supersedes = _refs(item["supersedes"], maps[kind])
            for old_id in item["supersedes"]:
                old = items[kind][old_id]
                if (old["valid_from"]["seq"] >= item["valid_from"]["seq"] or
                        old["lifecycle"] != "superseded" or
                        not old["updated_by"] or old["updated_by"][-1] != item["source_event_id"] or
                        _sorted(old["subjects"]) != _sorted(item["subjects"])):
                    raise ValueError("lifecycle_graph_invalid: supersede time, status, or scope")
                successor_counts[old_id] += 1
                edges.append({"kind": "supersedes", "item_type": kind,
                              "from": maps[kind][item_id], "to": maps[kind][old_id]})
            updates = [{"event_id": event_id, "seq": events[event_id].seq,
                        "trusted_event_sha256": canonical_hash(events[event_id].model_dump())}
                       for event_id in item["updated_by"]]
            fact = {"kind": kind, "anchor": maps[kind][item_id],
                    "source_event_id": item["source_event_id"], "source_quote": item["source_quote"],
                    "subjects": _sorted(item["subjects"]), "valid_from": item["valid_from"],
                    "lifecycle": item["lifecycle"], "interpretation": item["interpretation"],
                    "updated_by": updates, "supersedes": supersedes}
            if kind == "constraint":
                fact["predicate"] = item["predicate"]
            else:
                fact.update(change_type=item["change_type"], change_details=item["change_details"],
                            conditions=_sorted(item["conditions"]),
                            constraint_refs=_refs(item["constraint_refs"], maps["constraint"]))
                edges.extend({"kind": "authorization_constraint", "from": fact["anchor"], "to": ref}
                             for ref in fact["constraint_refs"])
            facts.append(fact)
        if any((item["lifecycle"] == "superseded") != (successor_counts[item_id] == 1) or
               successor_counts[item_id] > 1 for item_id, item in items[kind].items()):
            raise ValueError("lifecycle_graph_invalid: superseded item lacks a unique successor")
    for item in raw["exceptions"]:
        constraint_refs = _refs(item["constraint_ids"], maps["constraint"])
        auth_ref = _refs([item["authorization_id"]], maps["authorization"])[0]
        auth = items["authorization"][item["authorization_id"]]
        if (set(item["constraint_ids"]) != set(auth["constraint_refs"]) or
                _sorted(item["subjects"]) != _sorted(auth["subjects"]) or
                item["change_types"] != [auth["change_type"]] or
                _sorted(item["conditions"]) != _sorted(auth["conditions"]) or
                item["valid_from"] != auth["valid_from"]):
            raise ValueError("lifecycle_graph_invalid: historical exception disagrees with its authorization")
        anchor = canonical_hash({"kind": "exception", "authorization_ref": auth_ref,
                                 "constraint_refs": constraint_refs})
        if item["exception_id"] in seen_ids or anchor in seen_anchors:
            raise ValueError("lifecycle_graph_ambiguous: duplicate exception identity")
        seen_ids.add(item["exception_id"])
        seen_anchors.add(anchor)
        maps["exception"][item["exception_id"]] = anchor
        reasons = []
        if auth["lifecycle"] != "active":
            reasons.append("authorization_" + auth["lifecycle"])
        if any(items["constraint"][ref]["lifecycle"] != "active" for ref in item["constraint_ids"]):
            reasons.append("constraint_inactive")
        if auth["interpretation"] == "ambiguous":
            reasons.append("authorization_ambiguous")
        if auth["lifecycle"] != "active":
            gate = "authorization_" + auth["lifecycle"]
        elif any(items["constraint"][ref]["lifecycle"] != "active" for ref in item["constraint_ids"]):
            gate = "constraint_inactive"
        elif auth["interpretation"] == "ambiguous":
            gate = "authorization_ambiguous"
        else:
            gate = "active_scope_candidate"
        facts.append({"kind": "exception", "anchor": anchor, "authorization_ref": auth_ref,
                      "constraint_refs": constraint_refs, "subjects": _sorted(item["subjects"]),
                      "change_types": sorted(item["change_types"]), "conditions": _sorted(item["conditions"]),
                      "valid_from": item["valid_from"], "lifecycle_gate": gate,
                      "lifecycle_reasons": sorted(reasons)})
        edges.append({"kind": "exception_authorization", "from": anchor, "to": auth_ref})
        edges.extend({"kind": "exception_constraint", "from": anchor, "to": ref} for ref in constraint_refs)
    for auth_id, auth in items["authorization"].items():
        if auth["constraint_refs"] and not any(item["authorization_id"] == auth_id for item in raw["exceptions"]):
            raise ValueError("lifecycle_graph_invalid: missing historical local exception")
    graph = {"case_id": state.case_id, "through_event_id": state.through_event_id,
             "through_seq": state.through_seq, "source_history_sha256": state.source_history_sha256,
             "facts": _sorted(facts), "edges": _sorted(edges)}
    return {"graph": graph, "graph_sha256": canonical_hash(graph), "ids": maps}


def _renaming(records: list[dict]) -> dict[str, str]:
    mapping = {}
    for record in records:
        for proposal in record["batch"]["proposals"]:
            for kind in ("constraint", "authorization"):
                if proposal[kind] is not None:
                    item_id = proposal[kind][kind + "_id"]
                    if item_id not in mapping:
                        mapping[item_id] = f"renamed_{kind}_{len(mapping) + 1:03d}"
    return mapping


def _rename_batch(batch: dict, mapping: dict[str, str], config: str) -> TransitionBatch:
    batch = deepcopy(batch)
    batch["extractor_config_hash"] = config
    for proposal in batch["proposals"]:
        proposal["extractor_config_hash"] = config
        proposal["target_ids"] = [mapping.get(ref, ref) for ref in proposal["target_ids"]]
        for kind in ("constraint", "authorization"):
            draft = proposal[kind]
            if draft is None:
                continue
            draft[kind + "_id"] = mapping[draft[kind + "_id"]]
            draft["supersedes"] = [mapping.get(ref, ref) for ref in draft["supersedes"]]
            if kind == "authorization":
                draft["constraint_refs"] = [mapping.get(ref, ref) for ref in draft["constraint_refs"]]
    return TransitionBatch.model_validate(batch)


def _trusted_events(bundle, fixture_branch: dict) -> list[ConversationEvent]:
    return [*bundle.review_input.events, *[ConversationEvent(
        schema_version="0.1-draft", event_id=row["event_id"], seq=row["seq"],
        role=row["role"], content=row["content"],
        provenance=Provenance(origin="synthetic_fixture",
                              authority_level="user" if row["role"] == "user" else "none", artifact_refs=[]))
        for row in fixture_branch["events"]]]


def align_branch(branch: dict, fixture_branch: dict, bundle) -> tuple[dict, dict]:
    if branch["branch_id"] != fixture_branch["branch_id"]:
        raise ValueError("lifecycle alignment branch identity mismatch")
    events = _trusted_events(bundle, fixture_branch)
    records = [*branch["prefix_records"], *[row["record"] for row in branch["rows"]]]
    mapping = _renaming(records)
    config = records[0]["batch"]["extractor_config_hash"]
    renamed_config = canonical_hash({"version": VERSION, "identity_control": mapping, "source": config})
    original = BatchStateEngine(branch["branch_id"], events, config, set(bundle.registry.artifacts))
    renamed = BatchStateEngine(branch["branch_id"], events, renamed_config, set(bundle.registry.artifacts))
    rows, graphs = [], []
    for index, saved in enumerate(records):
        actual = original.submit_batch(TransitionBatch.model_validate(saved["batch"]))
        if actual != BatchRecord.model_validate(saved):
            raise ValueError("original lifecycle boundary record replay mismatch")
        changed = renamed.submit_batch(_rename_batch(saved["batch"], mapping, renamed_config))
        reason = changed.rejection_reason
        if reason is not None:
            for old_id, new_id in sorted(mapping.items(), key=lambda pair: len(pair[1]), reverse=True):
                reason = reason.replace(new_id, old_id)
        if (actual.accepted != changed.accepted or actual.consumed_event != changed.consumed_event or
                actual.rejection_reason != reason or original.stopped != renamed.stopped):
            raise ValueError("ID renaming changed transition or stop behavior")
        state = original.snapshot().model_dump()
        if index >= 5 and state != branch["rows"][index - 5]["state"]:
            raise ValueError("saved original lifecycle state replay mismatch")
        left, right = project_graph(state, events), project_graph(renamed.snapshot().model_dump(), events)
        if left["graph"] != right["graph"]:
            raise ValueError("ID renaming changed lifecycle graph")
        rows.append({"event_id": saved["batch"]["source_event_id"], "accepted": actual.accepted,
                     "graph_sha256": left["graph_sha256"], "graphs_equal": True,
                     "original_state_hash": state["state_hash"],
                     "renamed_state_hash": renamed.snapshot().state_hash,
                     "exception_gates": dict(Counter(fact["lifecycle_gate"] for fact in left["graph"]["facts"]
                                                      if fact["kind"] == "exception"))})
        if index >= 5:
            graphs.append({"event_id": saved["batch"]["source_event_id"],
                           "original": left, "renamed": right})
    if original.stopped != branch["stopped_on_rejection"]:
        raise ValueError("saved lifecycle failure boundary mismatch")
    return ({"branch_id": branch["branch_id"], "purpose": branch["purpose"], "prefix_pairs": rows,
             "stopped_on_rejection": original.stopped,
             "not_attempted_extensions": branch["not_attempted_extensions"],
             "terminal_scope_check": None if original.stopped else branch["rows"][-1]["summary"]["scope_check"],
             "model_review": "not_run"},
            {"branch_id": branch["branch_id"], "identity_mapping": mapping,
             "extension_graphs": graphs, "renamed_records": [row.model_dump() for row in renamed.batch_records]})


def build_report(root: Path, source_output: Path) -> tuple[dict, dict]:
    private = root / "evaluation_private/case_001"
    # Validate all source observations and independent expectations before graph analysis.
    source = source_audit.verify_audit(root, private / "s3_4_predicate_review_v1.json",
                                       private / "s3_4_lifecycle_boundaries_v1.json", source_output)
    fixture = _read(private / "s3_4_lifecycle_boundaries_v1.json")
    from writscope.contracts import load_bundle
    bundle = load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact/c002")
    rows, traces = [], []
    for branch, fixture_branch in zip(source["synthetic_boundary_branches"], fixture["branches"], strict=True):
        row, trace = align_branch(branch, fixture_branch, bundle)
        rows.append(row)
        traces.append(trace)
    report = {"schema_version": "0.1-draft", "version": VERSION, "visibility": "evaluator_only",
              "execution_kind": "synthetic_lifecycle_alignment_diagnostic", "new_model_calls": 0,
              "source_audit_sha256": digest((source_output / "audit.json").read_bytes()),
              "fixture_sha256": source["fixture_sha256"], "branches": rows,
              "aligned_prefix_pairs": sum(len(row["prefix_pairs"]) for row in rows),
              "source_hashes": {path: digest((root / path).read_bytes()) for path in (
                  "scripts/s3_4_lifecycle_alignment.py", "scripts/s3_4_semantic_boundary_audit.py",
                  "scripts/s3_4_state_alignment.py", "scripts/m3j_transition_batch.py",
                  "scripts/m3f_scope_policy.py", "src/writscope/state.py")},
              "diagnostic_cost": {"annotation_minutes": None, "offline_wall_ms": None,
                                  "status": "unknown_separate_from_original_method_cost"},
              "limits": ["identity_controls_not_independent_model_outputs", "single_spec_review_not_independent_gold",
                         "native_state_operations_only", "cross_event_supersede_only",
                         "predicate_equivalence_not_inferred",
                         "no_historical_rescoring_or_method_comparison"]}
    return report, {"visibility": "evaluator_only", "branches": traces}


def run_diagnostic(root: Path, source_output: Path, output: Path) -> dict:
    root, source_output, output = (path.resolve() for path in (root, source_output, output))
    if output.exists() or output.parent != root / "reports/local":
        raise ValueError("lifecycle output must be a fresh direct child of reports/local")
    report, trace = build_report(root, source_output)
    output.mkdir()
    write_json(output / "diagnostic.json", report)
    write_json(output / "evaluator_graphs.json", trace)
    return report


def verify_diagnostic(root: Path, source_output: Path, output: Path) -> dict:
    report, trace = build_report(root.resolve(), source_output.resolve())
    if _read(output / "diagnostic.json") != report or _read(output / "evaluator_graphs.json") != trace:
        raise ValueError("saved v3 lifecycle alignment replay mismatch")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-output", type=Path, default=Path("reports/local/s3-4-semantic-boundary-20261025"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    action = run_diagnostic if args.action == "run" else verify_diagnostic
    result = action(args.root, args.source_output, args.output)
    print(f"{VERSION}: {result['aligned_prefix_pairs']} prefix pairs, new_model_calls=0")


if __name__ == "__main__":
    main()
