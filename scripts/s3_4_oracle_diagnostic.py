"""Evaluator-only S3.4 diagnosis of a saved c003 run; no provider path.

Reference annotations are read only after the observed run has passed its
original no-network verifier. This module never constructs a model request.
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import time

from scripts import m3s_real_runner
from scripts.m3i_task_context import ContextBatch, TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine, TransitionBatch
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import SourceQuote, load_bundle, parse_json
from writscope.state import TransitionProposal


VERSION = "s3_4_oracle_diagnostic_v1"
ANNOTATION_VERSION = "s3_4_annotation_v1"
POLICY_ID = "task_context_quote_v1+transition_batch_v1_offline+scope_ref_v2_offline"
CASE = "c003"


def _read(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def _fact_constraint(item: dict) -> dict:
    return {"kind": "constraint", "source_event_id": item["source_event_id"],
            "source_quote": item["source_quote"], "subjects": item["subjects"],
            "predicate": item["predicate"], "lifecycle": item["lifecycle"],
            "interpretation": item["interpretation"], "supersedes": item["supersedes"]}


def _fact_authorization(item: dict) -> dict:
    return {"kind": "authorization", "source_event_id": item["source_event_id"],
            "source_quote": item["source_quote"], "subjects": item["subjects"],
            "change_type": item["change_type"], "change_details": item["change_details"],
            "conditions": item["conditions"], "lifecycle": item["lifecycle"],
            "interpretation": item["interpretation"],
            "constraint_refs": item["constraint_refs"], "supersedes": item["supersedes"]}


def _facts(state: dict, context: dict) -> list[dict]:
    result = [_fact_constraint(item) for item in state["constraints"]]
    result.extend(_fact_authorization(item) for item in state["authorizations"])
    result.extend({"kind": "task_context",
                   "source_event_id": item["source_ref"]["event_id"],
                   "source_quote": item["source_ref"]["quote"]}
                  for item in context["task_context"])
    # v1 intentionally refuses to guess cross-trajectory ID equivalence for
    # lifecycle references. A new annotation contract is needed for those.
    if state["exceptions"] or any(item["supersedes"] for item in state["constraints"]):
        raise ValueError("S3.4 v1 cannot align exception or supersede identities")
    if any(item["constraint_refs"] or item["supersedes"]
           for item in state["authorizations"]):
        raise ValueError("S3.4 v1 cannot align authorization reference identities")
    return result


def _fact_counter(facts: list[dict]) -> Counter:
    return Counter(canonical_hash(fact) for fact in facts)


def _diff_by_kind(left: list[dict], right: list[dict]) -> tuple[dict, dict]:
    """Return facts expected but missing in left, then extra in left."""
    left_counts, right_counts = _fact_counter(left), _fact_counter(right)
    mapping = {canonical_hash(fact): fact["kind"] for fact in [*left, *right]}
    missing = right_counts - left_counts
    extra = left_counts - right_counts
    return ({kind: sum(n for key, n in missing.items() if mapping[key] == kind)
             for kind in sorted(set(mapping.values())) if any(
                 mapping[key] == kind for key in missing)},
            {kind: sum(n for key, n in extra.items() if mapping[key] == kind)
             for kind in sorted(set(mapping.values())) if any(
                 mapping[key] == kind for key in extra)})


def _proposal_semantics(proposal: TransitionProposal) -> dict:
    if proposal.operation == "no_change":
        return {"operation": "no_change"}
    value = {"operation": proposal.operation, "source_quote": proposal.source.quote,
             "target_ids": proposal.target_ids}
    if proposal.constraint is not None:
        body = proposal.constraint.model_dump()
        body.pop("constraint_id")
        value["constraint"] = body
    if proposal.authorization is not None:
        body = proposal.authorization.model_dump()
        body.pop("authorization_id")
        if body["constraint_refs"] or body["supersedes"] or proposal.target_ids:
            raise ValueError("S3.4 v1 cannot align proposal reference identities")
        value["authorization"] = body
    return value


def _validate_annotation(annotation: dict, bundle, spec_path: Path) -> None:
    expected_keys = {"schema_version", "case_id", "decision_point", "public_bundle_hash",
                     "source_spec_sha256", "policy_id", "annotation_status",
                     "reviewer_ids", "review_notes", "annotation_hash", "fact_catalog",
                     "events"}
    if set(annotation) != expected_keys:
        raise ValueError("annotation top-level fields mismatch")
    body = {key: value for key, value in annotation.items() if key != "annotation_hash"}
    if annotation["annotation_hash"] != canonical_hash(body):
        raise ValueError("annotation hash mismatch")
    ri = bundle.review_input
    if (annotation["schema_version"] != ANNOTATION_VERSION or
            annotation["case_id"] != CASE or ri.case_id != CASE or
            annotation["decision_point"] != ri.decision_point.model_dump() or
            annotation["public_bundle_hash"] != canonical_hash(ri.model_dump()) or
            annotation["source_spec_sha256"] != digest(spec_path.read_bytes()) or
            annotation["policy_id"] != POLICY_ID or
            annotation["annotation_status"] != "reviewed" or
            not annotation["reviewer_ids"] or
            len(annotation["events"]) != len(ri.events)):
        raise ValueError("annotation identity, review, or public prefix mismatch")
    catalog = annotation["fact_catalog"]
    if not isinstance(catalog, dict) or not catalog:
        raise ValueError("empty fact catalog")
    events_by_id = {event.event_id: event for event in ri.events}
    for key, fact in catalog.items():
        if not isinstance(key, str) or not key or not isinstance(fact, dict):
            raise ValueError("invalid fact catalog entry")
        event = events_by_id.get(fact.get("source_event_id"))
        quote = fact.get("source_quote")
        if (event is None or event.role != "user" or
                event.provenance.authority_level != "user" or
                not isinstance(quote, str) or not quote or
                event.content.find(quote) < 0 or
                event.content.find(quote, event.content.find(quote) + 1) >= 0):
            raise ValueError("catalog fact lacks a unique trusted source quote")
        kind = fact.get("kind")
        if kind == "task_context":
            if set(fact) != {"kind", "source_event_id", "source_quote"}:
                raise ValueError("task-context fact shape mismatch")
        elif kind == "constraint":
            if (set(fact) != set(_fact_constraint(fact)) or fact["supersedes"]):
                raise ValueError("constraint fact shape or reference mismatch")
        elif kind == "authorization":
            if (set(fact) != set(_fact_authorization(fact)) or
                    fact["constraint_refs"] or fact["supersedes"]):
                raise ValueError("authorization fact shape or reference mismatch")
        else:
            raise ValueError("unknown fact kind")
    for index, (row, event) in enumerate(zip(annotation["events"], ri.events)):
        if (set(row) != {"event_id", "seq", "visible_prefix_hash", "context_items",
                         "proposals", "expected_poststate_facts", "rationale",
                         "item_review_status"} or
                row["event_id"] != event.event_id or row["seq"] != event.seq or
                row["visible_prefix_hash"] != canonical_hash([
                    item.model_dump() for item in ri.events[:index + 1]]) or
                row["item_review_status"] != "reviewed" or
                not row["rationale"] or not isinstance(row["proposals"], list) or
                not 1 <= len(row["proposals"]) <= 8):
            raise ValueError("event annotation identity or review mismatch")
        refs = row["expected_poststate_facts"]
        if (len(refs) != len(set(refs)) or any(key not in catalog for key in refs) or
                any(events_by_id[catalog[key]["source_event_id"]].seq > event.seq
                    for key in refs)):
            raise ValueError("future, duplicate, or unknown expected state fact")


def _batch(row: dict, event, config_hash: str) -> TransitionBatch:
    proposals = [TransitionProposal(
        schema_version="0.1-draft", case_id=CASE,
        source_event_id=event.event_id, source_seq=event.seq,
        extractor_config_hash=config_hash, extraction_call_id=None,
        operation=draft["operation"],
        source=SourceQuote(event_id=event.event_id, quote=draft["source_quote"]),
        target_ids=draft["target_ids"], constraint=draft["constraint"],
        authorization=draft["authorization"]) for draft in row["proposals"]]
    return TransitionBatch(schema_version="0.1-draft", case_id=CASE,
                           source_event_id=event.event_id, source_seq=event.seq,
                           extractor_config_hash=config_hash, extraction_call_id=None,
                           proposals=proposals)


def _compile_and_compare(annotation: dict, bundle, saved_steps: list[dict]) -> tuple[dict, dict]:
    events, catalog = bundle.review_input.events, annotation["fact_catalog"]
    artifact_ids = set(bundle.registry.artifacts)
    config_hash = canonical_hash({"schema": ANNOTATION_VERSION, "case_id": CASE,
                                  "annotation_hash": annotation["annotation_hash"]})
    ref_state = BatchStateEngine(CASE, events, config_hash, artifact_ids)
    ref_context = TaskContextLedger(events, artifact_ids)
    observed_config = saved_steps[0]["batch_record"]["batch"]["extractor_config_hash"]
    observed_state = BatchStateEngine(CASE, events, observed_config, artifact_ids)
    observed_context = TaskContextLedger(events, artifact_ids)
    rows = []
    for index, (event, annotated, saved) in enumerate(zip(events, annotation["events"], saved_steps)):
        if saved["seq"] != event.seq or saved["event_id"] != event.event_id:
            raise ValueError("saved step event mismatch")
        expected_batch = _batch(annotated, event, config_hash)
        reference_before = ref_state.snapshot().model_dump()
        reference_context_before = ref_context.snapshot().model_dump()
        observed_before = observed_state.snapshot().model_dump()
        observed_context_before = observed_context.snapshot().model_dump()
        ref_context_record = ref_context.submit(ContextBatch(
            event_id=event.event_id, seq=event.seq, items=annotated["context_items"]))
        ref_batch_record = ref_state.submit_batch(expected_batch)
        if not ref_batch_record.accepted:
            raise ValueError(f"oracle_compile_mismatch at {event.event_id}: " +
                             str(ref_batch_record.rejection_reason))
        reference_facts = _facts(ref_state.snapshot().model_dump(),
                                 ref_context.snapshot().model_dump())
        expected_facts = [catalog[key] for key in annotated["expected_poststate_facts"]]
        missing, extra = _diff_by_kind(reference_facts, expected_facts)
        if missing or extra:
            raise ValueError(f"oracle_compile_mismatch at {event.event_id}: "
                             f"missing={missing}, extra={extra}")
        if saved["context_record"] is None or saved["batch_record"] is None:
            raise ValueError("S3.4 c003 requires accepted saved events")
        observed_ctx_record = observed_context.submit(
            ContextBatch.model_validate(saved["context_record"]["batch"]))
        observed_batch = TransitionBatch.model_validate(saved["batch_record"]["batch"])
        observed_batch_record = observed_state.submit_batch(observed_batch)
        if (observed_ctx_record.model_dump() != saved["context_record"] or
                observed_batch_record.model_dump() != saved["batch_record"] or
                not observed_batch_record.accepted or
                observed_state.snapshot().state_hash != saved["state_sha256"] or
                observed_context.snapshot().state_hash != saved["context_sha256"]):
            raise ValueError("saved observed step does not replay")
        observed_facts = _facts(observed_state.snapshot().model_dump(),
                                observed_context.snapshot().model_dump())
        missing, extra = _diff_by_kind(observed_facts, reference_facts)
        observed_proposals = [_proposal_semantics(p) for p in observed_batch.proposals]
        expected_proposals = [_proposal_semantics(p) for p in expected_batch.proposals]
        proposals_match = observed_proposals == expected_proposals
        prior_facts_match = (_fact_counter(_facts(observed_before, observed_context_before)) ==
                             _fact_counter(_facts(reference_before, reference_context_before)))
        labels = []
        if not proposals_match or (
                [item["source_quote"] for item in saved["context_record"]["batch"]["items"]] !=
                [item["source_quote"] for item in annotated["context_items"]]):
            labels.append("extraction_semantic_error_candidate")
        if prior_facts_match and proposals_match and (missing or extra):
            labels.append("state_update_or_policy_error_candidate")
        if not labels and (missing or extra):
            labels.append("downstream_state_difference")
        rows.append({"event_id": event.event_id, "seq": event.seq,
                     "observed_operations": [p.operation for p in observed_batch.proposals],
                     "reference_operations": [p.operation for p in expected_batch.proposals],
                     "candidate_semantics_match": proposals_match,
                     "observed_state_hash": observed_state.snapshot().state_hash,
                     "reference_state_hash": ref_state.snapshot().state_hash,
                     "observed_context_hash": observed_context.snapshot().state_hash,
                     "reference_context_hash": ref_context.snapshot().state_hash,
                     "missing_reference_facts_by_kind": missing,
                     "extra_observed_facts_by_kind": extra,
                     "labels": labels})
    if len(saved_steps) != len(events):
        raise ValueError("saved trajectory did not cover all annotated events")
    return ({"rows": rows, "observed_final_facts": observed_facts,
             "reference_final_facts": reference_facts},
            {"reference_state_hash": ref_state.snapshot().state_hash,
             "reference_context_hash": ref_context.snapshot().state_hash,
             "reference_context_records_hash": canonical_hash([
                 row.model_dump() for row in ref_context.records]),
             "reference_batch_records_hash": canonical_hash([
                 row.model_dump() for row in ref_state.batch_records])})


def _cost_reconciliation(manifest: dict, calls: list[dict], steps: list[dict]) -> dict:
    if manifest["actual_model_calls"] != len(calls):
        raise ValueError("actual call count mismatch")
    stages = ("extract", "lookup_continuation", "final_review")
    by_stage = {}
    for stage in stages:
        subset = [row for row in calls if row["stage"] == stage]
        usage = [row["usage"] or {} for row in subset]
        def total(field):
            values = [item.get(field) for item in usage]
            return sum(values) if all(value is not None for value in values) else None
        estimated = [item.get("estimated_cost_cny") for item in usage]
        by_stage[stage] = {
            "attempts": len(subset), "statuses": [row["status"] for row in subset],
            "reserved_cny": str(sum((Decimal(row["reserved_cny"]) for row in subset),
                                    Decimal("0"))),
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "cache_hit_input_tokens": total("cache_hit_input_tokens"),
            "cache_miss_input_tokens": total("cache_miss_input_tokens"),
            "reasoning_tokens": total("reasoning_tokens"),
            "estimated_cost_cny": str(sum((Decimal(str(value)) for value in estimated),
                                           Decimal("0"))) if all(
                                               value is not None for value in estimated) else None,
            "provider_billed_cny": None if subset else "0",
            "provider_elapsed_ms": round(sum(row["provider_elapsed_ms"] or 0
                                              for row in subset), 3),
        }
        if (by_stage[stage]["input_tokens"] is not None and
                by_stage[stage]["cache_hit_input_tokens"] is not None and
                by_stage[stage]["cache_miss_input_tokens"] is not None and
                by_stage[stage]["input_tokens"] !=
                by_stage[stage]["cache_hit_input_tokens"] +
                by_stage[stage]["cache_miss_input_tokens"]):
            raise ValueError("cache token split does not reconcile with input")
        saved = manifest["totals"]["by_stage"][stage]
        if (by_stage[stage]["attempts"] != saved["attempts"] or
                by_stage[stage]["reserved_cny"] != saved["reserved_cny"] or
                by_stage[stage]["provider_elapsed_ms"] != saved["provider_elapsed_ms"]):
            raise ValueError("stage cost mismatch")
        for field in ("input_tokens", "output_tokens", "cache_hit_input_tokens",
                      "cache_miss_input_tokens", "reasoning_tokens", "estimated_cost_cny"):
            if by_stage[stage][field] != saved[field]["complete_total"]:
                raise ValueError(f"stage {stage} {field} mismatch")
    totals = manifest["totals"]
    for field in ("input_tokens", "output_tokens", "cache_hit_input_tokens",
                  "cache_miss_input_tokens", "reasoning_tokens", "estimated_cost_cny"):
        values = [by_stage[stage][field] for stage in stages]
        combined = (str(sum((Decimal(value) for value in values), Decimal("0")))
                    if field == "estimated_cost_cny" else sum(values)) if all(
                        value is not None for value in values) else None
        if combined != totals[field]["complete_total"]:
            raise ValueError(f"total {field} mismatch")
    if (str(sum((Decimal(by_stage[stage]["reserved_cny"]) for stage in stages),
                Decimal("0"))) != totals["reserved_cny"] or
            sum(by_stage[stage]["attempts"] for stage in stages) != totals["provider_attempts"]):
        raise ValueError("total reservation or attempts mismatch")
    if any(row["usage"] is None or row["usage"].get("provider_billed_cny") is not None
           for row in calls):
        raise ValueError("S3.4 c003 billing provenance changed")
    local = {"task_context": round(sum(row["context_elapsed_ms"] or 0 for row in steps), 3),
             "state_batch": round(sum(row["batch_elapsed_ms"] or 0 for row in steps), 3),
             "history_lookup": round(sum(lookup["elapsed_ms"] for row in steps
                                         for lookup in row["lookups"]), 3)}
    if local != manifest["local_maintenance_elapsed_ms"]:
        raise ValueError("local maintenance timing mismatch")
    return {"execution_kind": "real_model_observed_cost", "provider_attempts": len(calls),
            "by_stage": by_stage, "reserved_cny": totals["reserved_cny"],
            "estimated_cost_cny": totals["estimated_cost_cny"]["complete_total"],
            "provider_billed_cny": None, "provider_billed_status": "unknown",
            "reasoning_tokens": totals["reasoning_tokens"]["complete_total"],
            "local_maintenance_elapsed_ms": local,
            "provider_elapsed_ms": totals["provider_elapsed_ms"],
            "wall_elapsed_ms": manifest["wall_elapsed_ms"],
            "diagnostic_cost": {"annotation_minutes": None, "offline_cpu_ms": None,
                                "status": "not_measured_separate_from_observed_method"},
            "reconciled": True}


def build_report(root: Path, preflight: Path, source_run: Path,
                 annotation_path: Path) -> tuple[dict, dict, dict, str]:
    # Preserve the trust boundary: original replay precedes any private read.
    original = m3s_real_runner.verify_run(root, preflight, source_run)
    if (original["execution_kind"] != "real_model" or original["case_id"] != CASE or
            original["status"] != "completed"):
        raise ValueError("S3.4 requires the completed c003 real observation")
    bundle = load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact" / CASE)
    annotation = _read(annotation_path)
    _validate_annotation(annotation, bundle, root / "specs/first-case.md")
    steps = _read(source_run / "core_steps.json")
    calls = _read(source_run / "calls.json")
    compiled, reference = _compile_and_compare(annotation, bundle, steps)
    cost = _cost_reconciliation(original, calls, steps)
    final = _read(source_run / "final_result.json")
    labels = _read(root / "evaluation_private/case_001/labels.json")
    label = next(row for row in labels["labels"] if row["case_id"] == CASE)
    axes_match = all(final[name] == label["expected_" + name]
                     for name in ("impact", "authorization", "decision"))
    state_match = (_fact_counter(compiled["observed_final_facts"]) ==
                   _fact_counter(compiled["reference_final_facts"]))
    terminal = {"observed_axes_match_project_draft": axes_match,
                "observed_state_matches_single_review_reference": state_match,
                "classification": ("masked_state_error_candidate" if axes_match and not state_match
                                   else "no_masked_state_error_observed"),
                "oracle_conditioned_model_review": "not_run",
                "saved_final_response_reused_on_reference_state": False}
    report = {"schema_version": "0.1-draft", "version": VERSION,
              "execution_kind": "oracle_diagnostic", "case_id": CASE,
              "source_run_id": original["run_id"],
              "source_run_artifact_set_sha256": original["artifact_set_sha256"],
              "annotation_sha256": digest(annotation_path.read_bytes()),
              "annotation_review_kind": "single_project_spec_review",
              "new_model_calls": 0, "observed_model_calls": len(calls),
              "status": "completed_single_case_diagnostic",
              "prefixes": compiled["rows"], "terminal": terminal,
              "limits": ["synthetic_dev_case_only", "project_authored_reference_not_independent_gold",
                         "no_oracle_conditioned_model_call", "not_a_method_comparison"]}
    provenance = {"schema_version": "0.1-draft", "version": VERSION,
                  "source_run_artifact_set_sha256": original["artifact_set_sha256"],
                  "source_run_plan_sha256": original["plan_sha256"],
                  "public_bundle_hash": annotation["public_bundle_hash"],
                  "annotation_hash": annotation["annotation_hash"],
                  "annotation_file_sha256": digest(annotation_path.read_bytes()),
                  "source_spec_sha256": annotation["source_spec_sha256"],
                  "diagnostic_runner_sha256": digest(Path(__file__).read_bytes()),
                  "labels_file_sha256": digest((root / "evaluation_private/case_001/labels.json").read_bytes()),
                  **reference}
    lines = ["# S3.4 c003 隔离诊断", "",
             "执行类型：oracle_diagnostic；新增模型调用 0 次；项目单人规格审核，非独立金标准。", "",
             "| 事件 | 观察操作 | 审核操作 | 候选语义一致 | 观察状态多出 / 缺少事实 | 诊断标签 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in compiled["rows"]:
        lines.append("| {event_id} | {obs} | {ref} | {same} | {extra} / {missing} | {labels} |".format(
            event_id=row["event_id"], obs=", ".join(row["observed_operations"]),
            ref=", ".join(row["reference_operations"]),
            same="是" if row["candidate_semantics_match"] else "否",
            extra=json.dumps(row["extra_observed_facts_by_kind"], ensure_ascii=False),
            missing=json.dumps(row["missing_reference_facts_by_kind"], ensure_ascii=False),
            labels=", ".join(row["labels"]) or "无"))
    lines.extend(["", "原运行：5 次提取、0 次回查续答、1 次终点；提供商账单 unknown。",
                  "终点三轴命中项目草稿参考，但状态与本次单人审核状态不等；旧终点响应未移植或改分。",
                  "本报告不能用于三方法排名、泛化或状态维护收益主张。", ""])
    return report, cost, provenance, "\n".join(lines)


def run_diagnostic(root: Path, preflight: Path, source_run: Path,
                   annotation_path: Path, output: Path) -> dict:
    root, preflight, source_run = root.resolve(), preflight.resolve(), source_run.resolve()
    annotation_path, output = annotation_path.resolve(), output.resolve()
    if (output.exists() or output == source_run or output.is_relative_to(source_run) or
            output == annotation_path.parent or output.is_relative_to(annotation_path.parent)):
        raise ValueError("diagnostic output must be a fresh directory outside source and private data")
    started = time.perf_counter()
    report, cost, provenance, review = build_report(root, preflight, source_run,
                                                     annotation_path)
    cost["diagnostic_cost"]["offline_wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
    cost["diagnostic_cost"]["status"] = "offline_wall_measured_annotation_time_unknown"
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "diagnostic.json", report)
    write_json(output / "cost_reconciliation.json", cost)
    write_json(output / "provenance.json", provenance)
    (output / "review.md").write_text(review, encoding="utf-8")
    manifest = {"schema_version": "0.1-draft", "version": VERSION,
                "execution_kind": "oracle_diagnostic", "status": "completed",
                "new_model_calls": 0, "artifacts": {name: digest((output / name).read_bytes())
                for name in ("diagnostic.json", "cost_reconciliation.json", "provenance.json",
                             "review.md")}}
    manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
    write_json(output / "run.json", manifest)
    return report


def verify_diagnostic(root: Path, preflight: Path, source_run: Path,
                      annotation_path: Path, output: Path) -> dict:
    report, cost, provenance, review = build_report(
        root.resolve(), preflight.resolve(), source_run.resolve(), annotation_path.resolve())
    saved = _read(output / "run.json")
    artifacts = {name: digest((output / name).read_bytes()) for name in
                 ("diagnostic.json", "cost_reconciliation.json", "provenance.json", "review.md")}
    if (saved["version"] != VERSION or saved["execution_kind"] != "oracle_diagnostic" or
            saved["status"] != "completed" or saved["new_model_calls"] != 0 or
            saved["artifacts"] != artifacts or
            saved["artifact_set_sha256"] != canonical_hash(artifacts) or
            _read(output / "diagnostic.json") != report or
            _read(output / "provenance.json") != provenance or
            (output / "review.md").read_text(encoding="utf-8") != review):
        raise ValueError("S3.4 saved diagnostic replay mismatch")
    saved_cost = _read(output / "cost_reconciliation.json")
    saved_diagnostic = saved_cost.pop("diagnostic_cost")
    cost.pop("diagnostic_cost")
    if (saved_cost != cost or saved_diagnostic["status"] !=
            "offline_wall_measured_annotation_time_unknown" or
            saved_diagnostic["annotation_minutes"] is not None or
            saved_diagnostic["offline_cpu_ms"] is not None or
            not isinstance(saved_diagnostic["offline_wall_ms"], (int, float)) or
            saved_diagnostic["offline_wall_ms"] < 0):
        raise ValueError("S3.4 cost replay mismatch")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="S3.4 evaluator-only offline diagnosis")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    action = run_diagnostic if args.command == "run" else verify_diagnostic
    report = action(args.root, args.preflight, args.source_run, args.annotation, args.output)
    print(json.dumps({"status": report["status"],
                      "new_model_calls": report["new_model_calls"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
