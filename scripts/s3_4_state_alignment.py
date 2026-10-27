"""Evaluator-only v2 reference compilation and legacy local-exception alignment.

Original observations replay under their original policy before annotations are
read. Reference state never enters a model request. Only active add/grant states
and no_change are supported; predicate representations are compared literally.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from scripts import m3d_gate
from scripts.m3i_task_context import ContextBatch, TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine, TransitionBatch
from writscope import m3c
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import SourceQuote, load_bundle, parse_json
from writscope.state import StateEngine, TransitionProposal, assess_impacts

VERSION = "s3_4_state_alignment_v2"
POLICY = "task_context_quote_v1+transition_batch_v1_offline+scope_ref_v2_offline"


def _read(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def _sorted(values: list) -> list:
    return sorted(values, key=canonical_hash)


def _anchor(kind: str, item: dict) -> str:
    return canonical_hash({"kind": kind, "source_event_id": item["source_event_id"],
                           "source_quote": item["source_quote"],
                           "subjects": _sorted(item["subjects"])})


def _refs(refs: list[str], mapping: dict[str, str]) -> list[str]:
    if len(refs) != len(set(refs)) or any(ref not in mapping for ref in refs):
        raise ValueError("alignment_invalid: duplicate, dangling, or wrong-type reference")
    return sorted(mapping[ref] for ref in refs)


def project_graph(state: dict) -> dict:
    """Remove runtime IDs while preserving source, scope, conditions, and edges.

    Anchors are identity candidates, not semantic equivalence judgements. Two
    items with one anchor are ambiguous even if their predicates differ.
    """
    maps = {"constraint": {}, "authorization": {}, "exception": {}}
    seen_ids, anchors = set(), set()
    facts = []
    for kind, collection in (("constraint", "constraints"), ("authorization", "authorizations")):
        for item in state[collection]:
            item_id = item[kind + "_id"]
            anchor = _anchor(kind, item)
            if item_id in seen_ids or anchor in anchors:
                raise ValueError("alignment_ambiguous: duplicate ID or source/scope anchor")
            if item["supersedes"] or item["updated_by"] or item["lifecycle"] != "active":
                raise ValueError("alignment_unsupported: lifecycle update requires a new contract")
            seen_ids.add(item_id)
            anchors.add(anchor)
            maps[kind][item_id] = anchor
    for item in state["constraints"]:
        facts.append({"kind": "constraint", "anchor": maps["constraint"][item["constraint_id"]],
                      "source_event_id": item["source_event_id"], "source_quote": item["source_quote"],
                      "subjects": _sorted(item["subjects"]), "predicate": item["predicate"],
                      "lifecycle": item["lifecycle"], "interpretation": item["interpretation"],
                      "valid_from": item["valid_from"], "supersedes": [], "updated_by": []})
    for item in state["authorizations"]:
        facts.append({"kind": "authorization", "anchor": maps["authorization"][item["authorization_id"]],
                      "source_event_id": item["source_event_id"], "source_quote": item["source_quote"],
                      "subjects": _sorted(item["subjects"]), "change_type": item["change_type"],
                      "change_details": item["change_details"], "conditions": _sorted(item["conditions"]),
                      "constraint_refs": _refs(item["constraint_refs"], maps["constraint"]),
                      "lifecycle": item["lifecycle"], "interpretation": item["interpretation"],
                      "valid_from": item["valid_from"], "supersedes": [], "updated_by": []})
    for item in state["exceptions"]:
        constraint_refs = _refs(item["constraint_ids"], maps["constraint"])
        authorization_ref = _refs([item["authorization_id"]], maps["authorization"])[0]
        anchor = canonical_hash({"kind": "exception", "authorization_ref": authorization_ref,
                                 "constraint_refs": constraint_refs})
        if item["exception_id"] in seen_ids or anchor in anchors:
            raise ValueError("alignment_ambiguous: duplicate exception identity")
        seen_ids.add(item["exception_id"])
        anchors.add(anchor)
        maps["exception"][item["exception_id"]] = anchor
        facts.append({"kind": "exception", "anchor": anchor,
                      "authorization_ref": authorization_ref, "constraint_refs": constraint_refs,
                      "subjects": _sorted(item["subjects"]), "change_types": sorted(item["change_types"]),
                      "conditions": _sorted(item["conditions"]), "valid_from": item["valid_from"]})
    return {"facts": _sorted(facts), "ids": maps}


def _context_facts(snapshot: dict) -> list[dict]:
    return _sorted([{"kind": "task_context", "source_event_id": item["source_ref"]["event_id"],
                     "source_quote": item["source_ref"]["quote"], "requirement": item["requirement"],
                     "verification_refs": sorted(item["verification_refs"])}
                    for item in snapshot["task_context"]])


def _catalog_projection(catalog: dict, keys: list[str]) -> tuple[dict, list[dict]]:
    if len(keys) != len(set(keys)) or any(key not in catalog for key in keys):
        raise ValueError("annotation contains duplicate or unknown expected fact")
    state = {"constraints": [], "authorizations": [], "exceptions": []}
    context = []
    for key in keys:
        item = dict(catalog[key])
        kind = item.pop("kind")
        if kind == "task_context":
            if set(item) != {"source_event_id", "source_quote", "verification_refs"}:
                raise ValueError("task-context fact shape mismatch")
            context.append({"kind": kind, **item, "requirement": item["source_quote"]})
        elif kind in {"constraint", "authorization"}:
            common = {"source_event_id", "source_quote", "subjects", "lifecycle", "interpretation",
                      "valid_from", "supersedes", "updated_by"}
            allowed = common | ({"predicate"} if kind == "constraint" else
                                {"change_type", "change_details", "conditions", "constraint_refs"})
            if set(item) != allowed:
                raise ValueError("state fact shape mismatch")
            item[kind + "_id"] = key
            state["constraints" if kind == "constraint" else "authorizations"].append(item)
        elif kind == "exception":
            if set(item) != {"authorization_ref", "constraint_refs", "subjects", "change_types",
                             "conditions", "valid_from"}:
                raise ValueError("exception fact shape mismatch")
            item["exception_id"] = key
            item["authorization_id"] = item.pop("authorization_ref")
            item["constraint_ids"] = item.pop("constraint_refs")
            state["exceptions"].append(item)
        else:
            raise ValueError("unknown expected fact kind")
    return project_graph(state), _sorted(context)


def validate_annotation(annotation: dict, bundle, root: Path) -> None:
    if set(annotation) != {"schema_version", "case_id", "decision_point", "public_bundle_hash",
                           "source_spec_sha256", "policy_id", "annotation_status", "reviewer_ids",
                           "review_notes", "annotation_hash", "fact_catalog", "events"}:
        raise ValueError("annotation top-level shape mismatch")
    body = {key: value for key, value in annotation.items() if key != "annotation_hash"}
    ri = bundle.review_input
    if (annotation["annotation_hash"] != canonical_hash(body) or
            annotation["schema_version"] != "s3_4_annotation_v2" or
            annotation["case_id"] != ri.case_id or ri.case_id not in {"c001", "c002"} or
            annotation["decision_point"] != ri.decision_point.model_dump() or
            annotation["public_bundle_hash"] != canonical_hash(ri.model_dump()) or
            annotation["source_spec_sha256"] != digest((root / "specs/first-case.md").read_bytes()) or
            annotation["policy_id"] != POLICY or annotation["annotation_status"] != "reviewed" or
            not annotation["reviewer_ids"] or not annotation["review_notes"] or
            len(annotation["events"]) != len(ri.events)):
        raise ValueError("annotation identity, hash, or review mismatch")
    events = {event.event_id: event for event in ri.events}
    catalog = annotation["fact_catalog"]
    _catalog_projection(catalog, list(catalog))  # typed refs, identity ambiguity, exact fact shapes
    for fact in catalog.values():
        if fact["kind"] == "exception":
            auth = catalog[fact["authorization_ref"]]
            if fact["valid_from"] != auth["valid_from"]:
                raise ValueError("exception source time disagrees with authorization")
            continue
        event = events.get(fact["source_event_id"])
        quote = fact["source_quote"]
        if (event is None or event.role != "user" or event.provenance.authority_level != "user" or
                not isinstance(quote, str) or not quote or event.content.count(quote) != 1):
            raise ValueError("annotation requires unique trusted user source quote")
        if fact["kind"] != "task_context" and fact["valid_from"] != {
                "event_id": event.event_id, "seq": event.seq}:
            raise ValueError("annotation state time disagrees with trusted source")
    for index, (row, event) in enumerate(zip(annotation["events"], ri.events)):
        if (set(row) != {"event_id", "seq", "visible_prefix_hash", "context_items", "proposals",
                         "expected_poststate_facts", "rationale", "item_review_status"} or
                row["event_id"] != event.event_id or row["seq"] != event.seq or
                row["visible_prefix_hash"] != canonical_hash([
                    item.model_dump() for item in ri.events[:index + 1]]) or
                row["item_review_status"] != "reviewed" or not row["rationale"]):
            raise ValueError("annotation event identity or review mismatch")
        keys = row["expected_poststate_facts"]
        _catalog_projection(catalog, keys)
        for key in keys:
            fact = catalog[key]
            source_seq = (fact["valid_from"]["seq"] if fact["kind"] != "task_context" else
                          events[fact["source_event_id"]].seq)
            if source_seq > event.seq:
                raise ValueError("future expected fact in visible prefix")


def _batch(row: dict, case: str, config: str) -> TransitionBatch:
    if (not isinstance(row["proposals"], list) or any(
            set(draft) != {"operation", "source_quote", "target_ids", "constraint", "authorization"}
            for draft in row["proposals"])):
        raise ValueError("annotation proposal shape mismatch")
    proposals = [TransitionProposal(
        schema_version="0.1-draft", case_id=case, source_event_id=row["event_id"],
        source_seq=row["seq"], extractor_config_hash=config, extraction_call_id=None,
        operation=draft["operation"], source=SourceQuote(event_id=row["event_id"],
                                                       quote=draft["source_quote"]),
        target_ids=draft["target_ids"], constraint=draft["constraint"],
        authorization=draft["authorization"]) for draft in row["proposals"]]
    if any(item.operation not in {"no_change", "add_constraint", "grant"} for item in proposals):
        raise ValueError("annotation lifecycle operation unsupported in v2")
    return TransitionBatch(schema_version="0.1-draft", case_id=case,
                           source_event_id=row["event_id"], source_seq=row["seq"],
                           extractor_config_hash=config, extraction_call_id=None, proposals=proposals)


def compile_reference(annotation: dict, bundle, root: Path) -> dict:
    validate_annotation(annotation, bundle, root)
    case, events = bundle.review_input.case_id, bundle.review_input.events
    config = canonical_hash({"version": VERSION, "annotation": annotation["annotation_hash"]})
    engine = BatchStateEngine(case, events, config, set(bundle.registry.artifacts))
    context = TaskContextLedger(events, set(bundle.registry.artifacts))
    prefixes = []
    for row in annotation["events"]:
        before = engine.snapshot().model_dump()
        batch = _batch(row, case, config)
        context_record = context.submit(ContextBatch(event_id=row["event_id"], seq=row["seq"],
                                                     items=row["context_items"]))
        record = engine.submit_batch(batch)
        if not record.accepted:
            raise ValueError(f"oracle_compile_mismatch at {row['event_id']}: {record.rejection_reason}")
        state = engine.snapshot().model_dump()
        context_state = context.snapshot().model_dump()
        expected_graph, expected_context = _catalog_projection(
            annotation["fact_catalog"], row["expected_poststate_facts"])
        if (project_graph(state)["facts"] != expected_graph["facts"] or
                _context_facts(context_state) != expected_context):
            raise ValueError(f"oracle_compile_mismatch at {row['event_id']}: independent expected facts differ")
        prefixes.append({"event_id": row["event_id"], "before": before, "state": state,
                         "context": context_state, "batch": batch.model_dump(),
                         "batch_record": record.model_dump(), "context_record": context_record.model_dump()})
    assessment = assess_impacts(engine.snapshot(), bundle.evidence,
                                bundle.review_input.candidate_patch.sha256).model_dump()
    return {"visibility": "evaluator_only", "prefixes": prefixes,
            "deterministic_scope_check": assessment}


def _proposal_projection(proposal: dict, prior: dict) -> dict:
    if proposal["operation"] == "no_change":
        return {"operation": "no_change"}
    if proposal["target_ids"]:
        raise ValueError("proposal lifecycle targets unsupported in v2")
    result = {"operation": proposal["operation"], "source": proposal["source"]}
    for kind in ("constraint", "authorization"):
        if proposal[kind] is not None:
            body = dict(proposal[kind])
            body.pop(kind + "_id")
            if body["supersedes"]:
                raise ValueError("proposal lifecycle references unsupported in v2")
            body["subjects"] = _sorted(body["subjects"])
            if kind == "authorization":
                body["constraint_refs"] = _refs(body["constraint_refs"], prior["ids"]["constraint"])
                body["conditions"] = _sorted(body["conditions"])
            result[kind] = body
    return result


def _difference(observed: dict, reference: dict) -> dict:
    left = {fact["anchor"]: fact for fact in observed["facts"]}
    right = {fact["anchor"]: fact for fact in reference["facts"]}
    changed = [{"anchor": anchor, "kind": right[anchor]["kind"],
                "fields": sorted(key for key in right[anchor] if left[anchor].get(key) != right[anchor][key])}
               for anchor in sorted(set(left) & set(right)) if left[anchor] != right[anchor]]
    return {"missing_anchors": sorted(set(right) - set(left)),
            "extra_anchors": sorted(set(left) - set(right)), "changed": changed}


def build_report(root: Path, source_run: Path, annotation_path: Path, case: str) -> tuple[dict, dict]:
    if case not in {"c001", "c002"}:
        raise ValueError("unsupported observation case")
    verifier = m3c.verify_run if case == "c001" else m3d_gate.verify_run
    original = verifier(root, source_run)  # before private reads or reference compilation
    if original["cases"][case]["status"] != "completed":
        raise ValueError("requires a completed original observation")
    bundle = load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact" / case)
    annotation = _read(annotation_path)
    reference = compile_reference(annotation, bundle, root)
    entries = [parse_json(line) for line in
               (source_run / case / "transitions.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(entries) != len(reference["prefixes"]):
        raise ValueError("observed prefix count mismatch")
    observed = StateEngine(case, bundle.review_input.events,
                           entries[0]["proposal"]["extractor_config_hash"], set(bundle.registry.artifacts))
    rows, observed_prefixes = [], []
    for saved, ref, annotated in zip(entries, reference["prefixes"], annotation["events"]):
        before = project_graph(observed.snapshot().model_dump())
        entry = observed.submit(TransitionProposal.model_validate(saved["proposal"]))
        if entry.transition.model_dump() != saved["transition"] or not entry.transition.accepted:
            raise ValueError("original observed transition replay mismatch")
        snapshot = observed.snapshot().model_dump()
        actual, expected = project_graph(snapshot), project_graph(ref["state"])
        difference = _difference(actual, expected)
        prior_reference = project_graph(ref["before"])
        candidate_matches = [_proposal_projection(saved["proposal"], before)] == [
            _proposal_projection(item, prior_reference) for item in ref["batch"]["proposals"]]
        labels = []
        if not candidate_matches:
            labels.append("candidate_representation_difference_unresolved")
        elif actual["facts"] != expected["facts"]:
            labels.append("inherited_state_representation_difference")
        if (candidate_matches and before["facts"] == prior_reference["facts"] and
                actual["facts"] != expected["facts"]):
            labels.append("state_update_or_policy_error_candidate")
        if annotated["context_items"]:
            labels.append("observed_contract_task_context_unavailable")
        rows.append({"event_id": ref["event_id"], "candidate_projection_matches": candidate_matches,
                     "graph_matches": actual["facts"] == expected["facts"], "difference": difference,
                     "observed_state_hash": snapshot["state_hash"],
                     "reference_state_hash": ref["state"]["state_hash"],
                     "reference_context_hash": ref["context"]["state_hash"],
                     "exactly_matched_facts_by_kind": dict(Counter(
                         fact["kind"] for fact in actual["facts"] if fact in expected["facts"])),
                     "observed_ids": actual["ids"], "reference_ids": expected["ids"],
                     "labels": labels})
        observed_prefixes.append({"event_id": ref["event_id"], "state": snapshot})
    observed_assessment = _read(source_run / case / "assessment.json")
    axes = lambda assessment: {key: assessment[key] for key in ("impact", "authorization", "decision")}
    report = {"schema_version": "0.1-draft", "version": VERSION, "visibility": "evaluator_only",
              "execution_kind": "oracle_diagnostic", "case_id": case, "new_model_calls": 0,
              "annotation_review_kind": "single_project_spec_review", "prefixes": rows,
              "source_artifact_set_sha256": original["artifact_set_sha256"],
              "original_case_model_calls": original["cases"][case]["attempted_events"],
              "original_run_model_calls": original["attempts"],
              "annotation_sha256": digest(annotation_path.read_bytes()),
              "annotation_hash": annotation["annotation_hash"],
              "public_bundle_hash": annotation["public_bundle_hash"],
              "reference_deterministic_scope_check": axes(reference["deterministic_scope_check"]),
              "observed_structural_assessment": axes(observed_assessment),
              "observed_task_context_status": "unsupported_in_legacy_contract",
              "oracle_conditioned_model_review": "not_run", "observed_final_model_review": "not_run",
              "diagnostic_cost": {"annotation_minutes": None, "offline_wall_ms": None,
                                  "status": "unknown_separate_from_original_method_cost"},
              "source_hashes": {path: digest((root / path).read_bytes()) for path in (
                  "scripts/s3_4_state_alignment.py", "scripts/m3i_task_context.py",
                  "scripts/m3j_transition_batch.py", "scripts/m3f_scope_policy.py",
                  "src/writscope/state.py", "src/writscope/m3c.py", "scripts/m3d_gate.py",
                  "specs/first-case.md")},
              "limits": ["single_spec_review_not_independent_gold", "active_local_exception_only",
                         "predicate_equivalence_not_inferred", "legacy_observations_not_same_configuration",
                         "no_new_method_score_or_cost_comparison"]}
    trace = {"visibility": "evaluator_only", "reference": reference,
             "observed_original_policy": observed_prefixes}
    return report, trace


def run_diagnostic(root: Path, source_run: Path, annotation_path: Path, case: str, output: Path) -> dict:
    root, source_run, annotation_path, output = (
        path.resolve() for path in (root, source_run, annotation_path, output))
    if (output.exists() or output.is_relative_to(source_run) or
            output.is_relative_to(annotation_path.parent)):
        raise ValueError("output must be fresh and outside observation and annotation")
    report, trace = build_report(root, source_run, annotation_path, case)
    output.mkdir(parents=True)
    write_json(output / "diagnostic.json", report)
    write_json(output / "evaluator_trace.json", trace)
    return report


def verify_diagnostic(root: Path, source_run: Path, annotation_path: Path, case: str, output: Path) -> dict:
    report, trace = build_report(root.resolve(), source_run.resolve(), annotation_path.resolve(), case)
    if _read(output / "diagnostic.json") != report or _read(output / "evaluator_trace.json") != trace:
        raise ValueError("saved v2 state diagnostic replay mismatch")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--case", choices=("c001", "c002"), required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    action = run_diagnostic if args.action == "run" else verify_diagnostic
    result = action(args.root, args.source_run, args.annotation, args.case, args.output)
    print(f"{result['version']}: {args.case} reference compiled, new_model_calls=0")


if __name__ == "__main__":
    main()
