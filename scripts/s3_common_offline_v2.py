"""Nine-job V2 offline round. ScriptedMock only; no credentials or paid mode."""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path

from scripts import s3_scripted_trajectory_v2 as core
from scripts import m3q_fault_evidence_audit as fixtures
from scripts import s3_interaction_v2 as contract
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from scripts.m3k_history_lookup import LookupPolicy
from scripts.m3n_budget_envelope import AttemptLedger
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.baselines import build_request as baseline_request
from writscope.budget import PriceCeiling
from writscope.contracts import load_bundle, parse_json, validate_output
from writscope.m3b import _artifacts, _code_provenance
from writscope.providers import ModelConfig, ProviderError
from writscope.providers.deepseek import wire_bytes
from writscope.providers.mock import Script, ScriptedMock, Step

VERSION = "s3_common_offline_v2"
CASES = ("c001", "c002", "c003")
METHODS = ("full_direct", "full_checklist", "incremental")
PROFILES = ("success_lookup", "success_no_lookup", "first_error", "bad_grant", "bare_supersede",
            "future_lookup", "final_invalid", "missing_usage", "scope_rejection")
MODEL = ModelConfig(model_id="deepseek-flash", temperature=0.0, max_output_tokens=4096)
POLICY = LookupPolicy(max_requests=2, max_events_per_request=2)
PRICE = PriceCeiling(input_cny_per_million=Decimal("1"), output_cny_per_million=Decimal("1"),
                     source_url="offline_synthetic_not_an_actual_price", checked_at_utc="not_price_checked")
SIM_CAP = Decimal("10")


def read(path):
    return parse_json(path.read_text(encoding="utf-8"))


def input_dir(root, case):
    return root / "reports/m1-2026-10-18/inputs/verified_impact" / case


def prompts(root):
    return {key: (root / "prompts" / name).read_text(encoding="utf-8") for key, name in (
        ("full_direct", "full_direct.md"), ("full_checklist", "full_checklist.md"),
        ("interaction", "incremental_interaction_v2.md"), ("final", "incremental_final_review_v1.md"))}


def baseline_script(data, method):
    value = fixtures._final(data)
    if method == "full_checklist":
        value["checklist"] = {key: "Synthetic transport control, not a model answer" for key in (
            "reconstruction", "subject_check", "version_check", "operation_check", "conditions_check", "lifecycle_check")}
    return fixtures._script_from_values("V2 public baseline transport control", [value])


def job_script(data, method, profile):
    if profile not in PROFILES:
        raise ValueError("unknown offline fixture profile")
    case = data.review_input.case_id
    if method != "incremental":
        result = baseline_script(data, method)
        if case == "c001" and method == "full_direct":
            if profile == "first_error":
                result.steps = [Step(action="error", text="offline first-provider failure", synthetic_usage=fixtures.TOKEN_USAGE)]
            elif profile == "missing_usage":
                result.steps[0].synthetic_usage = None
        return result
    result = (fixtures._rich_script(data, wrong_scope=case == "c003" and profile == "scope_rejection")
              if case == "c002" or (case == "c003" and profile == "scope_rejection")
              else fixtures._basic_script(data))
    if profile == "success_no_lookup":
        result.steps = [step for step in result.steps if parse_json(step.text).get("kind") != "lookup_request"]
    if case == "c002" and profile in {"bad_grant", "bare_supersede"}:
        for step in result.steps:
            value = parse_json(step.text)
            if value.get("kind") == "extract" and value["state_items"][0]["operation"] == "grant":
                item = value["state_items"][0]
                item["target_ids"] = ["con_status"]
                if profile == "bad_grant":
                    item["authorization"]["supersedes"] = ["con_status"]
                else:
                    item.update(operation="supersede", authorization=None)
                step.text = json.dumps(value, ensure_ascii=False)
    if case == "c001":
        if profile == "future_lookup":
            first = next(step for step in result.steps if parse_json(step.text).get("kind") == "lookup_request")
            first.text = '{"kind":"lookup_request","event_ids":["e005"]}'
        elif profile == "final_invalid":
            result.steps[-1].text = "{}"
    return result


def prepare(root, profile):
    if profile not in PROFILES:
        raise ValueError("unknown offline fixture profile")
    instructions = prompts(root)
    config = core._config_hash(instructions["interaction"], instructions["final"], MODEL, POLICY)
    jobs, sources = [], {}
    for index, case in enumerate(CASES):
        data = load_bundle(input_dir(root, case))
        state = BatchStateEngine(case, data.review_input.events, config, set(data.registry.artifacts)).snapshot()
        context = TaskContextLedger(data.review_input.events, set(data.registry.artifacts)).snapshot()
        requests = {method: baseline_request(data, method, instructions[method], MODEL) for method in METHODS[:2]}
        requests["incremental"] = contract.build_request(data, 0, state, context, instructions["interaction"], MODEL, POLICY)
        if len({value.evidence_sha256 for value in requests.values()}) != 1:
            raise ValueError("common public evidence mismatch")
        sources[case] = {"bundle_sha256": data.sha256, "evidence_sha256": requests["full_direct"].evidence_sha256,
                         "tool_policy_sha256": canonical_hash(data.review_input.tool_policy.model_dump())}
        for method in METHODS[index:]+METHODS[:index]:
            request = requests[method]
            if len(wire_bytes(request)) > 100000:
                raise ValueError("full evidence exceeds wire envelope")
            script = job_script(data, method, profile)
            jobs.append({"job_id": f"job_{len(jobs)+1:03d}", "case_id": case, "method": method,
                         "repeat_index": 0, "max_attempts": 8 if method == "incremental" else 1,
                         "first_request_sha256": canonical_hash(request.model_dump()),
                         "fixture_sha256": canonical_hash(script.model_dump())})
    components = [Path(__file__), Path(core.__file__), Path(fixtures.__file__), Path(contract.__file__)]
    components += [Path(module.__file__) for module in core.COMPONENTS]
    return {"version": VERSION, "execution_kind": "offline_only", "profile": profile,
            "jobs": jobs, "public_sources": sources, "response_contract": "interaction_quote_v2",
            "model_wire_config": MODEL.model_dump(), "prompt_sha256": {key: digest(value.encode()) for key, value in instructions.items()},
            "config_hash": config, "max_simulated_attempts": 30, "concurrency": 1, "retries": 0,
            "stop": "first_failure_stops_entire_round", "new_actual_model_calls": 0,
            "simulated_price": PRICE.model_dump(mode="json"), "simulated_cap_cny": str(SIM_CAP),
            "paid_price": None, "paid_budget": None, "new_authorization": None,
            "real_runner": "not_implemented", "reference_labels": "not_read",
            "source_sha256": {path.relative_to(root).as_posix(): digest(path.read_bytes()) for path in components},
            **_code_provenance()}


async def execute_baseline(data, script, request):
    if len(script.steps) != 1:
        raise ValueError("one scripted attempt per baseline")
    mock = ScriptedMock(script)
    amount = AttemptLedger(PRICE, SIM_CAP).quote(len(wire_bytes(request)))
    call = {"call_id": "scripted_call_001", "stage": "review", "event_seq": None,
            "request": request.model_dump(), "response_text": None, "synthetic_usage": None,
            "status": "running", "error": None, "attempt": {"reserved_cny": str(amount)}}
    result = None
    try:
        response = await asyncio.wait_for(mock.generate(request.model_copy(deep=True)), timeout=90)
        call["response_text"] = response.text
        usage = response.synthetic_usage
        call["synthetic_usage"] = usage.model_dump() if usage is not None else None
        if len(response.text.encode()) > 100000:
            raise ValueError("response byte limit exceeded")
        if usage is None or usage.input_tokens is None or usage.output_tokens is None:
            call.update(status="usage_missing", error="scripted usage missing")
        else:
            result = validate_output(response.text, data, request.method, output_contract="source_quote_v1").model_dump()
            call["status"] = "succeeded"
    except TimeoutError:
        call.update(status="timeout", error="offline baseline timeout")
    except ProviderError as exc:
        call.update(status="provider_error", error=str(exc),
                    synthetic_usage=exc.synthetic_usage.model_dump() if exc.synthetic_usage else None)
    except (ValueError, TypeError) as exc:
        call.update(status="invalid_output", error=str(exc))
    failure = None if result is not None else {"stage": "review", "status": call["status"], "error": call["error"]}
    return {"status": "completed" if failure is None else "stopped_on_failure", "failure": failure,
            "actual_model_calls": 0, "simulated_provider_attempts": 1,
            "final_review_valid": result is not None, "final_result": result, "calls": [call]}


def synthetic_totals(call_rows):
    def usage(field):
        values = [(row.get("synthetic_usage") or {}).get(field) for row in call_rows]
        known = [value for value in values if value is not None]
        return {"known_subtotal": sum(known), "unknown_attempts": len(values)-len(known),
                "complete_total": sum(known) if len(values) == len(known) else None}
    stages = {stage: [row for row in call_rows if row["stage"] == stage and row["attempt"] is not None]
              for stage in ("review", "extract", "lookup_continuation", "final_review")}
    return {"simulated_provider_attempts": sum(len(rows) for rows in stages.values()),
            "synthetic_input_tokens": usage("input_tokens"), "synthetic_output_tokens": usage("output_tokens"),
            "simulated_reservation_cny": str(sum((Decimal(row["attempt"]["reserved_cny"]) for rows in stages.values() for row in rows), Decimal("0"))),
            "by_stage": {stage: {"attempts": len(rows), "simulated_reservation_cny": str(sum(
                (Decimal(row["attempt"]["reserved_cny"]) for row in rows), Decimal("0")))} for stage, rows in stages.items()}}


def report_text(summary):
    lines = ["# V2 三方法离线同轮报告", "", "本报告全部为脚本演练，无真实模型调用、评分或费用。",
             "参考标签未读取；独立审核与真实回查证据状态不变。", "",
             "| 案例 | 方法 | 状态 | 脚本尝试 | 事件接受 | 终点有效 | 回查 |",
             "| --- | --- | --- | ---: | ---: | --- | --- |"]
    for row in summary["jobs"]:
        lines.append(f"| {row['case_id']} | {row['method']} | {row['status']} | {row.get('simulated_provider_attempts',0)} | {row.get('events_accepted','—')} | {row.get('final_review_valid',False)} | {row.get('lookup_evidence','not_run')} |")
    lines += ["", "synthetic_usage 与模拟预留仅用于核对接线账表，不能作为模型用量、准确率或成本优势。",
              "详细状态/回查/原子转换及脚本响应保留于各 job 子目录；未知实际价格和新预算均为空。", ""]
    return "\n".join(lines)


def run(root, output, profile="success_lookup", *, verify=False):
    root, output = root.resolve(), output.resolve()
    if output.parent != root / "reports/local":
        raise ValueError("offline output must be a direct child of reports/local")
    plan = prepare(root, profile)
    if verify:
        if read(output/"plan.json") != plan:
            raise ValueError("v2 offline plan mismatch")
    else:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output/"plan.json", plan)
    instructions = prompts(root)
    jobs, calls, failure = [], [], None
    for unit in plan["jobs"]:
        if failure is not None:
            jobs.append({**unit, "status": "not_run"})
            continue
        data = load_bundle(input_dir(root, unit["case_id"]))
        script = job_script(data, unit["method"], profile)
        destination = output/unit["job_id"]
        if unit["method"] == "incremental":
            if verify:
                manifest = core.verify_run(input_dir(root, unit["case_id"]), destination)
                if read(destination/"script.json") != script.model_dump():
                    raise ValueError("offline fixture changed")
                result = read(destination/"final_result.json") if manifest["final_review_valid"] else None
            else:
                manifest = core.run_offline(input_dir(root, unit["case_id"]), script, destination,
                    instructions["interaction"], instructions["final"], MODEL, POLICY, PRICE, SIM_CAP)
                result = read(destination/"final_result.json") if manifest["final_review_valid"] else None
            local_calls = read(destination/"calls.json")
            row = {**unit, "status": manifest["status"], "failure": manifest["failure"],
                   "simulated_provider_attempts": manifest["simulated_provider_attempts"],
                   "events_accepted": manifest["events_accepted"], "lookup_attempts": manifest["lookup_attempts"],
                   "final_review_valid": manifest["final_review_valid"], "lookup_evidence": "offline_transport_only",
                   "final_result": result}
        else:
            request = baseline_request(data, unit["method"], instructions[unit["method"]], MODEL)
            baseline = asyncio.run(execute_baseline(data, script, request))
            local_calls = baseline.pop("calls")
            row = {**unit, **baseline, "lookup_evidence": "not_applicable"}
            if verify:
                if read(destination/"calls.json") != local_calls or read(destination/"outcome.json") != row:
                    raise ValueError("baseline request/response replay mismatch")
            else:
                destination.mkdir()
                write_json(destination/"calls.json", local_calls)
                write_json(destination/"outcome.json", row)
        if row["simulated_provider_attempts"] > unit["max_attempts"]:
            raise ValueError("per-job attempt limit exceeded")
        if not local_calls or canonical_hash(local_calls[0]["request"]) != unit["first_request_sha256"]:
            raise ValueError("first request differs from offline plan")
        calls.extend({**call, "job_id": unit["job_id"]} for call in local_calls)
        jobs.append(row)
        if row["status"] != "completed":
            failure = {"job_id": unit["job_id"], "detail": row["failure"]}
    totals = synthetic_totals(calls)
    if totals["simulated_provider_attempts"] > 30:
        raise ValueError("whole round attempt limit exceeded")
    summary = {"version": VERSION, "execution_kind": "offline_scripted_round_v2",
               "status": "completed" if failure is None else "stopped_on_failure", "failure": failure,
               "profile": profile, "actual_model_calls": 0, "metrics": None,
               "actual_provider_usage": None, "paid_budget": None, "actual_price": None,
               "reference_labels": "not_read", "independent_review": "pending_external_person",
               "plan_sha256": canonical_hash(plan), "jobs": jobs, "synthetic_totals": totals}
    text = report_text(summary)
    if verify:
        if read(output/"summary.json") != summary or (output/"report.md").read_text(encoding="utf-8") != text:
            raise ValueError("v2 whole-round reconstruction mismatch")
    else:
        write_json(output/"summary.json", summary)
        (output/"report.md").write_text(text, encoding="utf-8", newline="\n")
    manifest = {"version": VERSION, "actual_model_calls": 0, "status": summary["status"],
                "plan_sha256": canonical_hash(plan), "artifacts": _artifacts(output)}
    if verify:
        if read(output/"run.json") != manifest:
            raise ValueError("v2 whole-round artifact hash mismatch")
    else:
        write_json(output/"run.json", manifest)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=PROFILES, default="success_lookup")
    args = parser.parse_args(argv)
    result = run(args.root, args.output, args.profile, verify=args.command == "verify")
    print(json.dumps({"status": result["status"], "actual_model_calls": 0,
                      "simulated_provider_attempts": result["synthetic_totals"]["simulated_provider_attempts"]}))


if __name__ == "__main__":
    main()
