"""Post-run scoring. This module alone reads reference labels."""

from pathlib import Path

from .artifacts import canonical_hash, digest, safe_path, write_json
from .contracts import parse_json
from .events import JudgmentLabels


def read_run(run_dir: Path) -> dict:
    run = parse_json(safe_path(run_dir, "run.json").read_text(encoding="utf-8"))
    if run.get("status") != "completed" or run.get("execution_kind") not in {"offline_mock", "real_model"}:
        raise ValueError("only completed M2 runs can be scored")
    artifacts = run.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("missing run artifact manifest")
    if canonical_hash(artifacts) != run.get("artifact_set_sha256"):
        raise ValueError("artifact set hash mismatch")
    for relative, expected in artifacts.items():
        if digest(safe_path(run_dir, relative).read_bytes()) != expected:
            raise ValueError("run artifact hash mismatch: " + relative)
    return run


def _ratio(numerator: int, denominator: int):
    return None if denominator == 0 else numerator / denominator


def _synthetic_cost(attempts: list[dict]) -> dict:
    fields = {}
    for field in ("input_tokens", "output_tokens", "cost_usd"):
        values = [(attempt.get("synthetic_usage") or {}).get(field) for attempt in attempts]
        known = [value for value in values if value is not None]
        fields[field] = {"known_subtotal": sum(known), "unknown_attempts": len(values) - len(known),
                         "complete_total": sum(known) if len(values) == len(known) else None}
    return {"actual_model_calls": 0, "provider_attempts": len(attempts), "synthetic_test_usage": fields,
            "provider_elapsed_ms": round(sum(attempt["elapsed_ms"] for attempt in attempts), 3)}


def _real_cost(attempts: list[dict]) -> dict:
    from decimal import Decimal

    fields = {}
    for field in ("input_tokens", "output_tokens", "cache_hit_input_tokens", "cache_miss_input_tokens",
                  "reasoning_tokens", "estimated_cost_cny", "billed_cost_cny"):
        values = [(attempt.get("actual_usage") or {}).get(field) for attempt in attempts]
        known = [Decimal(str(value)) for value in values if value is not None]
        subtotal = sum(known, Decimal("0"))
        fields[field] = {"known_subtotal": str(subtotal), "unknown_attempts": len(values) - len(known),
                         "complete_total": str(subtotal) if len(known) == len(values) else None}
    return {"provider_attempts": len(attempts), "provider_reported_usage_and_estimates": fields,
            "provider_elapsed_ms": round(sum(attempt["elapsed_ms"] for attempt in attempts), 3)}


def score_run(run_dir: Path, labels_path: Path, output: Path) -> dict:
    """Score saved immutable predictions; never import the runner/provider here."""
    run = read_run(run_dir)
    raw_labels = labels_path.read_bytes()
    references = parse_json(raw_labels.decode("utf-8"))
    if references.get("kind") != "reference_labels_not_predictions":
        raise ValueError("unexpected reference label kind")
    reference_rows = references.get("labels")
    if not isinstance(reference_rows, list):
        raise ValueError("missing reference labels")
    labels = {}
    for row in reference_rows:
        values = {axis: row["expected_" + axis] for axis in ("impact", "authorization", "decision")}
        JudgmentLabels.model_validate(values)
        case = row["case_id"]
        if case in labels:
            raise ValueError("duplicate reference case")
        labels[case] = row
    rows = []
    attempts_by_method = {}
    for plan in run["jobs"]:
        job_id = plan["job_id"]
        job = parse_json(safe_path(run_dir, f"{job_id}/job.json").read_text(encoding="utf-8"))
        if job["job_id"] != job_id or job["method"] != plan["method"]:
            raise ValueError("job/manifest mismatch")
        attempts_by_method.setdefault(job["method"], []).extend(
            parse_json(safe_path(run_dir, f"{job_id}/{name}").read_text(encoding="utf-8"))
            for name in job["attempt_refs"])
        case = job["case_id"] or plan["case_id_hint"]
        if job["case_id"] is not None and job["case_id"] != plan["case_id_hint"]:
            raise ValueError("case directory/input mismatch")
        if case not in labels:
            raise ValueError("missing reference case: " + str(case))
        reference = labels[case]
        result = None
        if job["status"] == "succeeded":
            if not job["result_ref"]:
                raise ValueError("successful job lacks result")
            result = parse_json(safe_path(run_dir, f"{job_id}/{job['result_ref']}").read_text(encoding="utf-8"))
            if result["case_id"] != case:
                raise ValueError("result case mismatch")
            JudgmentLabels.model_validate({axis: result[axis] for axis in ("impact", "authorization", "decision")})
        rows.append({
            "job_id": job_id, "case_id": case, "method": job["method"],
            "status": job["status"], "family_id": reference["family_id"],
            "pair_ids": reference["pair_ids"], "split": reference["split"],
            "annotation_status": reference["annotation_status"],
            "expected": {axis: reference["expected_" + axis] for axis in ("impact", "authorization", "decision")},
            "observed": None if result is None else {axis: result[axis] for axis in ("impact", "authorization", "decision")},
            "correct": {axis: bool(result is not None and result[axis] == reference["expected_" + axis]) for axis in ("impact", "authorization", "decision")},
        })
    methods = {}
    for method in sorted({row["method"] for row in rows}):
        subset = [row for row in rows if row["method"] == method]
        total = len(subset)
        correct = {axis: sum(row["correct"][axis] for row in subset) for axis in ("impact", "authorization", "decision")}
        all_correct = sum(all(row["correct"].values()) for row in subset)
        false_allow_denominator = sum(row["expected"]["impact"] == "breaking" and row["expected"]["authorization"] == "not_covered" for row in subset)
        false_allow = sum(row["expected"]["impact"] == "breaking" and row["expected"]["authorization"] == "not_covered" and row["observed"] is not None and row["observed"]["decision"] == "allow" for row in subset)
        false_block_denominator = sum(row["expected"]["decision"] == "allow" for row in subset)
        false_block = sum(row["expected"]["decision"] == "allow" and row["observed"] is not None and row["observed"]["decision"] in {"revise", "ask", "investigate"} for row in subset)
        pairs = sorted({pair for row in subset for pair in row["pair_ids"]})
        pair_rows = {pair: [row for row in subset if pair in row["pair_ids"]] for pair in pairs}
        pair_correct = sum(len(items) == 2 and all(all(row["correct"].values()) for row in items) for items in pair_rows.values())
        methods[method] = {
            "jobs": total, "succeeded": sum(row["status"] == "succeeded" for row in subset),
            "failed": sum(row["status"] != "succeeded" for row in subset),
            "status_counts": {status: sum(row["status"] == status for row in subset) for status in sorted({row["status"] for row in subset})},
            "axes": {axis: {"correct": correct[axis], "denominator": total, "accuracy": _ratio(correct[axis], total)} for axis in correct},
            "all_axes": {"correct": all_correct, "denominator": total, "accuracy": _ratio(all_correct, total)},
            "false_allow": {"count": false_allow, "denominator": false_allow_denominator, "rate": _ratio(false_allow, false_allow_denominator)},
            "false_block": {"count": false_block, "denominator": false_block_denominator, "rate": _ratio(false_block, false_block_denominator)},
            "diagnostic_pairs": {"correct": pair_correct, "denominator": len(pairs), "rate": _ratio(pair_correct, len(pairs))},
            "cost": _synthetic_cost(attempts_by_method[method]) if run["execution_kind"] == "offline_mock" else _real_cost(attempts_by_method[method]),
        }
    summary = {
        "schema_version": "0.1-draft", "execution_kind": run["execution_kind"],
        "claim_scope": "pipeline_test_only_not_model_performance" if run["execution_kind"] == "offline_mock" else "six_task_connection_pilot_not_formal_comparison",
        "run_id": run["run_id"], "run_artifact_set_sha256": run["artifact_set_sha256"],
        "reference_sha256": digest(raw_labels), "reference_status": "project_authored_draft",
        "cost": run["totals"], "budget": run.get("budget"), "methods": methods, "rows": rows,
        "limitations": ["One synthetic scenario with three authorization histories; pairs overlap.",
                        "Source syntax is not semantic evidence quality.",
                        "Offline responses are scripted; real six-task pilot is not a formal method comparison."],
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "summary.json", summary)
    return summary
