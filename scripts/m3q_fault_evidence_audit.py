"""M3q offline fault and equal-evidence audit; no network or evaluator labels."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path

from scripts import m3p_scripted_runner as runner
from scripts.m3k_history_lookup import LookupPolicy
from scripts.m3n_budget_envelope import AttemptLedger
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.baselines import build_request as baseline_request
from writscope.budget import PriceCeiling
from writscope.contracts import load_bundle, parse_json
from writscope.m3b import _artifacts
from writscope.m3c import verify_run as verify_m3c
from writscope.providers import ModelConfig, Usage
from writscope.providers.mock import Script, Step
from writscope.state import AuthorizationDraft, ChangeDetails, Condition, ConstraintDraft, Predicate
from scripts.m3d_gate import verify_run as verify_m3d
from scripts.m3e_gate import verify_run as verify_m3e


VERSION = "m3q_fault_evidence_audit_v1"
CASES = ("c001", "c002", "c003")
MODEL = ModelConfig(model_id="deepseek-flash")
POLICY = LookupPolicy(max_requests=2, max_events_per_request=2)
PRICE = PriceCeiling(input_cny_per_million=Decimal("2"),
                     output_cny_per_million=Decimal("8"),
                     source_url="offline-test-price", checked_at_utc="offline-test")
CAP = Decimal("10")
TOKEN_USAGE = Usage(input_tokens=20, output_tokens=10)
TIMEOUT_INJECTION_SECONDS = 0.01
PRIVATE_MARKERS = ("EVALUATOR_ONLY_M1_REFERENCE", "evaluation_private")


def _input(root: Path, case: str) -> Path:
    return root / "reports/m1-2026-10-18/inputs/verified_impact" / case


def _prompts(root: Path) -> tuple[str, str]:
    return ((root / "prompts/incremental_interaction_v1.md").read_text(encoding="utf-8"),
            (root / "prompts/incremental_final_review_v1.md").read_text(encoding="utf-8"))


@contextmanager
def _timeout_override(enabled: bool):
    original = runner.TIMEOUT_SECONDS
    try:
        if enabled:
            runner.TIMEOUT_SECONDS = TIMEOUT_INJECTION_SECONDS
        yield
    finally:
        runner.TIMEOUT_SECONDS = original


def _single(description: str, *, text: str = "", action: str = "response",
            usage: Usage | None = TOKEN_USAGE, delay: float = 0.0) -> Script:
    return Script(schema_version="0.1-draft", execution_kind="offline_mock",
                  description=description, steps=[Step(
                      action=action, text=text, synthetic_usage=usage,
                      delay_seconds=delay)])


def _extract(event, context: list[dict] | None = None,
             items: list[dict] | None = None) -> dict:
    return {"kind": "extract", "task_context": context or [],
            "state_items": items or [{"operation": "no_change",
                "source_quote": event.content, "target_ids": [],
                "constraint": None, "authorization": None}]}


def _final(bundle) -> dict:
    source = {"event_id": "e001", "quote": "保持旧客户端所使用的 v1 status 字段"}
    return {"impact": "breaking", "authorization": "ambiguous", "decision": "ask",
            "affected_subjects": [bundle.evidence.impacts[0].subject.model_dump()],
            "per_impact_authorization": [{"impact_id": "impact_001",
                "verdict": "ambiguous", "constraint_refs": [],
                "authorization_refs": [], "source_refs": [source],
                "reason": "scripted uncertainty"}],
            "constraint_refs": [], "authorization_refs": [], "source_refs": [source],
            "evidence_refs": ["impact_001"], "reason": "scripted interface check",
            "limitations": ["Scripted response, no model inference"],
            "derived_items": [], "checklist": None}


def _script_from_values(description: str, values: list[dict]) -> Script:
    return Script(schema_version="0.1-draft", execution_kind="offline_mock",
                  description=description,
                  steps=[Step(action="response", text=json.dumps(value, ensure_ascii=False),
                              synthetic_usage=TOKEN_USAGE) for value in values])


def _basic_script(bundle) -> Script:
    values = []
    for index, event in enumerate(bundle.review_input.events):
        if index in (3, 4):
            values.append({"kind": "lookup_request",
                           "event_ids": ["e001" if index == 3 else "e004"]})
        context = []
        if index == 0:
            context = [{"source_quote": "修复新页面的状态显示", "verification_refs": []}]
        elif index == 3:
            context = [{"source_quote": "不增加接口或网络请求。", "verification_refs": []}]
        values.append(_extract(event, context))
    values.append(_final(bundle))
    return _script_from_values("M3q public-fixture scripted interface check", values)


def _rich_script(bundle, *, wrong_scope: bool = False) -> Script:
    events = bundle.review_input.events
    subject = bundle.evidence.impacts[0].subject
    constraints = [ConstraintDraft(
        constraint_id=key, subjects=[subject],
        predicate=Predicate(kind="field_exists", expected=True,
                            qualifiers={"contract_ref": "contract"}),
        interpretation="resolved", supersedes=[])
        for key in ("con_status", "con_shape")]
    grant_subject = subject.model_copy(update={"api_version": "v2", "path": "/api/v2/profile"}) if wrong_scope else subject
    grant = AuthorizationDraft(
        authorization_id="auth_v1_replace", subjects=[grant_subject],
        change_type="replace_field",
        change_details=ChangeDetails(old_field="status", new_field="state",
                                     preserve_value_semantics=True),
        conditions=[Condition(kind="candidate_sha256",
                              value=bundle.review_input.candidate_patch.sha256,
                              resolution="unknown", evidence_refs=["patch"])],
        interpretation="resolved", constraint_refs=["con_status", "con_shape"],
        supersedes=[])
    values = []
    for index, event in enumerate(events):
        if index in (3, 4):
            values.append({"kind": "lookup_request",
                           "event_ids": ["e001" if index == 3 else "e004"]})
        context = []
        if index == 0:
            context = [{"source_quote": "修复新页面的状态显示", "verification_refs": []}]
        elif index == 3:
            context = [{"source_quote": "不增加接口或网络请求。", "verification_refs": []}]
        items = None
        if index == 0:
            items = [{"operation": "add_constraint", "source_quote": quote,
                      "target_ids": [], "constraint": draft.model_dump(),
                      "authorization": None}
                     for quote, draft in zip(
                         ("保持旧客户端所使用的 v1 status 字段", "响应对象只能包含 status"),
                         constraints, strict=True)]
        elif index == 4:
            items = [{"operation": "grant", "source_quote": event.content,
                      "target_ids": [], "constraint": None,
                      "authorization": grant.model_dump()}]
        values.append(_extract(event, context, items))
    values.append(_final(bundle))
    return _script_from_values("M3q public-fixture scripted state check", values)


def _sources(root: Path, output: Path, *, create: bool) -> dict:
    interaction, final = _prompts(root)
    result = {}
    for case in CASES:
        destination = output / "sources" / case
        expected_script = _basic_script(load_bundle(_input(root, case)))
        if create:
            manifest = runner.run_offline(_input(root, case), expected_script, destination,
                interaction, final, MODEL, POLICY, PRICE, CAP)
        else:
            manifest = runner.verify_run(_input(root, case), destination)
            if parse_json((destination / "script.json").read_text(encoding="utf-8")) != expected_script.model_dump():
                raise ValueError("source fixture changed: " + case)
        if manifest["status"] != "completed":
            raise ValueError("source script did not complete: " + case)
        result[case] = manifest["artifact_set_sha256"]
    destination = output / "sources" / "c002_state"
    expected_script = _rich_script(load_bundle(_input(root, "c002")))
    if create:
        manifest = runner.run_offline(_input(root, "c002"), expected_script, destination,
            interaction, final, MODEL, POLICY, PRICE, CAP)
    else:
        manifest = runner.verify_run(_input(root, "c002"), destination)
        if parse_json((destination / "script.json").read_text(encoding="utf-8")) != expected_script.model_dump():
            raise ValueError("source state fixture changed")
    if manifest["status"] != "completed":
        raise ValueError("source state script did not complete")
    result["c002_state"] = manifest["artifact_set_sha256"]
    return result


def _scenario_scripts(root: Path) -> dict[str, tuple[str, Script, str, bool]]:
    first = _basic_script(load_bundle(_input(root, "c001")))
    first_text = first.steps[0].text
    invalid_final = first.model_copy(deep=True)
    invalid_final.steps[-1].text = "{}"
    wrong_scope = _rich_script(load_bundle(_input(root, "c002")), wrong_scope=True)
    return {
        "timeout": ("c001", _single("local timeout injection", text=first_text,
                                     delay=0.05), "timeout", True),
        "provider_error": ("c001", _single("scripted provider error",
                                            action="error", text="simulated transport failure",
                                            usage=None), "provider_error", False),
        "usage_missing": ("c001", _single("scripted missing usage",
                                           text=first_text, usage=None), "usage_missing", False),
        "bad_json": ("c001", _single("invalid JSON", text="{"),
                     "invalid_output", False),
        "future_lookup": ("c001", _single("future event lookup",
                          text='{"kind":"lookup_request","event_ids":["e005"]}'),
                          "invalid_output", False),
        "unknown_lookup": ("c001", _single("unknown event lookup",
                           text='{"kind":"lookup_request","event_ids":["e999"]}'),
                           "invalid_output", False),
        "scope_mismatch": ("c002", wrong_scope, "batch_rejected", False),
        "invalid_final": ("c001", invalid_final, "invalid_output", False),
    }


def _run_faults(root: Path, output: Path) -> dict:
    interaction, final = _prompts(root)
    rows = []
    for name, (case, script, expected, timeout) in _scenario_scripts(root).items():
        destination = output / "scenarios" / name
        with _timeout_override(timeout):
            manifest = runner.run_offline(_input(root, case), script, destination,
                                          interaction, final, MODEL, POLICY, PRICE, CAP)
            if runner.verify_run(_input(root, case), destination) != manifest:
                raise ValueError("fault trajectory replay mismatch: " + name)
        if (manifest["status"] != "stopped_on_failure" or
                manifest["failure"]["status"] != expected or
                manifest["final_review_valid"] or
                manifest["actual_model_calls"] != 0):
            raise ValueError("fault scenario did not stop as expected: " + name)
        rows.append(_fault_row(name, case, expected, timeout, manifest, destination))
    # M3n's full-run floor is a preflight refusal, before any provider object.
    insufficient = Decimal("1")
    try:
        AttemptLedger(PRICE, insufficient)
    except ValueError as exc:
        if "full-run reservation floor" not in str(exc):
            raise
        rows.append({"scenario": "budget_insufficient", "case_id": "c001",
                     "status": "preflight_rejected", "event_seq": None,
                     "simulated_provider_attempts": 0, "events_accepted": 0,
                     "reserved_cny": "0", "unknown_billed_attempts": 0,
                     "raw_response_saved": False, "final_result_absent": True,
                     "artifact_set_sha256": None, "simulated_cap_cny": str(insufficient),
                     "required_floor_cny": str(AttemptLedger(PRICE, CAP).full_run_floor())})
    else:
        raise ValueError("insufficient cap did not block preflight")
    return {"schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "offline_fault_audit", "actual_model_calls": 0,
            "rows": rows}


def _fault_row(name: str, case: str, expected: str, timeout: bool,
               manifest: dict, destination: Path) -> dict:
    calls = parse_json((destination / "calls.json").read_text(encoding="utf-8"))
    if (len(calls) != manifest["simulated_provider_attempts"] or
            any(call["attempt"] is None for call in calls) or
            not all(call["attempt"]["reserved_cny"] for call in calls)):
        raise ValueError("fault reservation missing: " + name)
    return {"scenario": name, "case_id": case, "status": expected,
            "event_seq": manifest["failure"]["event_seq"],
            "simulated_provider_attempts": manifest["simulated_provider_attempts"],
            "events_accepted": manifest["events_accepted"],
            "reserved_cny": manifest["simulated_reservation_cny"],
            "unknown_billed_attempts": manifest["unknown_billed_attempts"],
            "raw_response_saved": any(call["response_text"] is not None for call in calls),
            "final_result_absent": not (destination / "final_result.json").exists(),
            "artifact_set_sha256": manifest["artifact_set_sha256"],
            "timeout_injection_seconds": TIMEOUT_INJECTION_SECONDS if timeout else None}


def _usage_field(calls: list[dict], field: str) -> dict:
    values = [(call.get("synthetic_usage") or {}).get(field)
              for call in calls if call["attempt"] is not None]
    known = [value for value in values if value is not None]
    subtotal = sum(known)
    return {"known_subtotal": subtotal, "unknown_attempts": len(values) - len(known),
            "complete_total": subtotal if len(values) == len(known) else None}


def _alignment(root: Path, output: Path) -> dict:
    rows = []
    for case in CASES:
        bundle = load_bundle(_input(root, case))
        source = output / "sources" / case
        saved = runner.verify_run(_input(root, case), source)
        calls = parse_json((source / "calls.json").read_text(encoding="utf-8"))
        result = parse_json((source / "final_result.json").read_text(encoding="utf-8"))
        final = parse_json((source / "final_request.json").read_text(encoding="utf-8"))
        baseline = {}
        for method in ("full_direct", "full_checklist"):
            prompt = (root / "prompts" / (method + ".md")).read_text(encoding="utf-8")
            baseline[method] = baseline_request(bundle, method, prompt, MODEL)
        base = baseline["full_direct"].public_input
        base_keys = {"review_input", "artifacts", "artifact_registry"}
        event_keys = base_keys | {"prior_state", "prior_task_context", "history_lookup"}
        final_keys = base_keys | {"current_state", "task_context", "history_lookup"}
        if (baseline["full_direct"].evidence_sha256 != baseline["full_checklist"].evidence_sha256 or
                final["evidence_sha256"] != baseline["full_direct"].evidence_sha256 or
                set(final["public_input"]) != final_keys or
                any(final["public_input"][key] != base[key] for key in base_keys)):
            raise ValueError("final/baseline public evidence mismatch: " + case)
        for call in calls:
            request = call["request"]
            public = request["public_input"]
            expected_keys = final_keys if call["stage"] == "final_review" else event_keys
            if (set(public) != expected_keys or
                    request["evidence_sha256"] != baseline["full_direct"].evidence_sha256 or
                    any(public[key] != base[key] for key in ("artifacts", "artifact_registry")) or
                    public["review_input"]["tool_policy"] != base["review_input"]["tool_policy"]):
                raise ValueError("incremental/baseline input mismatch: " + case)
            if call["stage"] != "final_review":
                seq = public["review_input"]["decision_point"]["seq"]
                if (len(public["review_input"]["events"]) != seq or
                        public["review_input"]["events"] != base["review_input"]["events"][:seq]):
                    raise ValueError("event request has wrong history cutoff: " + case)
            serialized = json.dumps(public, ensure_ascii=False)
            if any(marker in serialized for marker in PRIVATE_MARKERS):
                raise ValueError("evaluator marker leaked into request: " + case)
        rows.append({"case_id": case, "method": "incremental_review",
                     "execution_kind": "offline_scripted_provider_trajectory",
                     "claim_scope": "pipeline_test_only_not_model_performance",
                     "status": saved["status"],
                     "public_evidence_sha256": final["evidence_sha256"],
                     "tool_policy_sha256": canonical_hash(base["review_input"]["tool_policy"]),
                     "baseline_request_hashes": {method: canonical_hash(request.model_dump())
                                                 for method, request in baseline.items()},
                     "incremental_final_request_sha256": canonical_hash(final),
                     "observed": {axis: result[axis] for axis in
                                  ("impact", "authorization", "decision")},
                     "expected": None, "correct": None,
                     "cost": {"actual_model_calls": 0,
                              "provider_attempts": saved["simulated_provider_attempts"],
                              "synthetic_test_usage": {field: _usage_field(calls, field)
                                                       for field in ("input_tokens", "output_tokens", "cost_usd")},
                              "simulated_reservation_cny": saved["simulated_reservation_cny"],
                              "provider_billed_cost_cny": "unknown",
                              "unknown_billed_attempts": saved["unknown_billed_attempts"]},
                     "run_artifact_set_sha256": saved["artifact_set_sha256"]})
    return {"schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "offline_input_report_alignment",
            "actual_model_calls": 0, "rows": rows,
            "aligned_fields": ["case_id", "method", "status", "observed_three_axes",
                               "public_evidence_sha256", "tool_policy", "provider_attempts",
                               "synthetic_token_known_unknown", "run_artifact_set_sha256"],
            "remaining_differences": [
                "M3p has no real provider usage, bill, or formal score",
                "M3p does not yet persist provider elapsed time or M2-style run_id",
                "full_direct and full_checklist are request-only in this audit; no cross-method ranking",
                "same public evidence and tool policy do not establish equal total budgets"],
            "private_labels_read": False}


def _history(root: Path) -> dict:
    rows = []
    for name, verify, relative in (
            ("M3c", verify_m3c, "reports/local/m3c-live-20261021"),
            ("M3d", verify_m3d, "reports/local/m3d-c002-v2-live-20261021"),
            ("M3e", verify_m3e, "reports/local/m3e-c003-v2-live-20261021")):
        result = verify(root, root / relative)
        rows.append({"milestone": name, "status": result["status"],
                     "artifact_set_sha256": result["artifact_set_sha256"],
                     "replay": "passed", "new_model_calls": 0})
    return {"schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "offline_historical_replay", "rows": rows}


def _tamper(root: Path, output: Path) -> dict:
    interaction, final = _prompts(root)
    destination = output / "scenarios" / "tampered_request"
    script = _basic_script(load_bundle(_input(root, "c001")))
    runner.run_offline(_input(root, "c001"), script, destination,
                       interaction, final, MODEL, POLICY, PRICE, CAP)
    path = destination / "calls.json"
    calls = parse_json(path.read_text(encoding="utf-8"))
    calls[0]["request"]["public_input"]["review_input"]["events"][0]["content"] = "forged"
    write_json(path, calls)
    manifest_path = destination / "run.json"
    manifest = parse_json(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"] = _artifacts(destination)
    manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
    write_json(manifest_path, manifest)
    try:
        runner.verify_run(_input(root, "c001"), destination)
    except ValueError as exc:
        if "trajectory replay mismatch" not in str(exc):
            raise
    else:
        raise ValueError("rewritten hash hid request tampering")
    return {"scenario": "tampered_request", "status": "replay_rejected",
            "manifest_hash_rewritten": True,
            "tampered_artifact_set_sha256": manifest["artifact_set_sha256"]}


def _report(root: Path, output: Path, *, create: bool) -> dict:
    if create:
        output.mkdir(parents=True, exist_ok=False)
        sources = _sources(root, output, create=True)
        faults = _run_faults(root, output)
        alignment = _alignment(root, output)
        history = _history(root)
        tamper = _tamper(root, output)
        for name, value in (("faults", faults), ("alignment", alignment),
                            ("history", history), ("tamper", tamper)):
            write_json(output / (name + ".json"), value)
    else:
        sources = _sources(root, output, create=False)
        faults = parse_json((output / "faults.json").read_text(encoding="utf-8"))
        alignment = parse_json((output / "alignment.json").read_text(encoding="utf-8"))
        history = parse_json((output / "history.json").read_text(encoding="utf-8"))
        tamper = parse_json((output / "tamper.json").read_text(encoding="utf-8"))
        expected_names = set(_scenario_scripts(root)) | {"budget_insufficient"}
        names = [row["scenario"] for row in faults["rows"]]
        if len(names) != len(expected_names) or set(names) != expected_names:
            raise ValueError("fault scenario set mismatch")
        for row in faults["rows"]:
            if row["scenario"] == "budget_insufficient":
                expected_budget = {"scenario": "budget_insufficient", "case_id": "c001",
                    "status": "preflight_rejected", "event_seq": None,
                    "simulated_provider_attempts": 0, "events_accepted": 0,
                    "reserved_cny": "0", "unknown_billed_attempts": 0,
                    "raw_response_saved": False, "final_result_absent": True,
                    "artifact_set_sha256": None, "simulated_cap_cny": "1",
                    "required_floor_cny": str(AttemptLedger(PRICE, CAP).full_run_floor())}
                if row != expected_budget:
                    raise ValueError("budget floor changed")
                try:
                    AttemptLedger(PRICE, Decimal(row["simulated_cap_cny"]))
                except ValueError:
                    pass
                else:
                    raise ValueError("budget preflight replay mismatch")
                continue
            case, expected_script, expected, timeout = _scenario_scripts(root)[row["scenario"]]
            destination = output / "scenarios" / row["scenario"]
            if parse_json((destination / "script.json").read_text(encoding="utf-8")) != expected_script.model_dump():
                raise ValueError("fault fixture changed: " + row["scenario"])
            with _timeout_override(timeout):
                result = runner.verify_run(_input(root, case), destination)
            if (result["failure"]["status"] != expected or
                    _fault_row(row["scenario"], case, expected, timeout,
                               result, destination) != row or
                    result["final_review_valid"]):
                raise ValueError("fault audit replay mismatch: " + row["scenario"])
        if alignment != _alignment(root, output) or history != _history(root):
            raise ValueError("evidence alignment or historical replay mismatch")
        tampered = output / "scenarios" / "tampered_request"
        tampered_manifest = parse_json((tampered / "run.json").read_text(encoding="utf-8"))
        if (tamper["status"] != "replay_rejected" or
                tamper["tampered_artifact_set_sha256"] != tampered_manifest["artifact_set_sha256"] or
                parse_json((tampered / "script.json").read_text(encoding="utf-8")) !=
                _basic_script(load_bundle(_input(root, "c001"))).model_dump()):
            raise ValueError("tamper audit status mismatch")
        try:
            runner.verify_run(_input(root, "c001"), tampered)
        except ValueError as exc:
            if "trajectory replay mismatch" not in str(exc):
                raise
        else:
            raise ValueError("tampered request passed replay")
    artifacts = _artifacts(output)
    report = {"schema_version": "0.1-draft", "version": VERSION,
              "execution_kind": "offline_fault_evidence_audit",
              "status": "passed", "actual_model_calls": 0,
              "fault_scenarios": len(faults["rows"]),
              "alignment_cases": len(alignment["rows"]),
              "source_runs": sources,
              "historical_replays": len(history["rows"]),
              "tamper_rejected": tamper["status"] == "replay_rejected",
              "runner_sha256": digest(Path(__file__).read_bytes()),
              "m3p_runner_sha256": digest(Path(runner.__file__).read_bytes()),
              "artifacts": artifacts,
              "artifact_set_sha256": canonical_hash(artifacts)}
    if create:
        write_json(output / "run.json", report)
    elif parse_json((output / "run.json").read_text(encoding="utf-8")) != report:
        raise ValueError("audit manifest mismatch")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3q offline fault and evidence audit")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = _report(args.root.resolve(), args.output.resolve(),
                     create=args.command == "run")
    print(json.dumps({"status": result["status"],
                      "fault_scenarios": result["fault_scenarios"],
                      "actual_model_calls": 0}))


if __name__ == "__main__":
    main()
