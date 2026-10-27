"""Unapproved lookup diagnostic preparation; pure request/response transport only.

No credential access, provider construction, or paid-run command exists here.
Synthetic no_change states are transport fixtures, never reference states.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from scripts.m3k_history_lookup import HistoryLookupSession, LookupPolicy, LookupRequest
from scripts.m3o_interaction_contract import (LookupEvidence, LookupIntent, build_final_request,
                                              build_request, parse_response)
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.baselines import build_request as baseline_request
from writscope.contracts import load_bundle, parse_json
from writscope.events import DecisionPoint
from writscope.providers import ModelConfig
from writscope.providers.deepseek import wire_bytes

VERSION = "s3_lookup_offline_preflight_v1"
CASE = "c003"
MODEL = ModelConfig(model_id="deepseek-flash", temperature=0.0, max_output_tokens=4096)
POLICY = LookupPolicy(max_requests=2, max_events_per_request=2)
MAX_ATTEMPTS = 8
MAX_BYTES = 100_000
SCENARIOS = ("no_lookup", "one_lookup", "two_lookups", "future_lookup", "unknown_lookup", "lookup_limit")
README = """# 真实回查诊断：离线预检草案

状态为 draft_unapproved。真实调用 0 次；本目录不是付费运行入口，也没有新授权。
候选目标 c003；沿用现有交互和终点提示词、完整可见前缀及共同公开证据。
deepseek-flash 只是现有配置候选，本步未检查当前可用性/官方价格，费用上限和授权均留空。

## 此处已经准备

- plan.json：模型/提示词/输入/源码与计划哈希，5 次提取 + 至多 2 次回查续答 + 1 次终点的上限。
- first_request.json：真实方法同契约的首条待请求，不含人工正确状态。
- transport_cases.json：六类同步离线接线记录。回查是脚本注入，不是模型自主行为。
- 每个成功分支只准备终点请求，没有终点模型预测或方法评分。

所有脚本提案为 no_change，仅检验来源、状态衔接、请求与回查传输；其语义不作为正确提取。
模型可见回查耗时按现有运行器归零以保持请求字节确定；不能把它当作实测维护耗时。

## 将来真实诊断的判定

自然回查观察：模型自行选择是否请求回查，提示词不强制必须回查，不隐藏已可见原文。
只有真实运行日志证明模型发出合法 lookup_request、原文返回、真实续答接受和终点有效，
才记 lookup_branch_exercised。完成但从未请求回查记 not_exercised，保留结果且不补跑。
脚本注入或强制触发只提供 transport-only 证据，不能据此关闭模型自主回查缺口。
首次无效/超时/provider/用量/来源/范围/预算失败即停；失败尝试保留费用，无重试。

## 付费前仍缺

当前官方价格、保守换算及最坏预留；新的人民币上限与准确范围授权；
接受本计划的版本化真实入口、入口哈希、授权/预留闸门及离线故障回归。
旧 M3r/M3s 的价格、3 元上限与已用授权不能复用，本目录也不能直接交给旧入口执行。
本预检不读取审核包、参考标签或独立审核者答案。任一真实请求前重新冻结，不能看到结果再改门槛。
"""


def _read(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def classify_lookup_evidence(*, execution_kind: str, trigger_policy: str, completed: bool, requests: int,
                             succeeded: int, continuations: int, final_valid: bool) -> str:
    """Classify runner-owned counters, never accept model self-reported provenance."""
    if (execution_kind not in {"real_model", "offline_scripted"} or
            trigger_policy not in {"model_selected", "forced_control"} or
            type(completed) is not bool or type(final_valid) is not bool or
            any(type(value) is not int or value < 0 for value in (requests, succeeded, continuations)) or
            succeeded > requests or continuations > succeeded):
        raise ValueError("invalid trusted lookup evidence counters")
    if execution_kind != "real_model":
        return "offline_transport_only"
    if trigger_policy != "model_selected":
        return "prompted_transport_control_only"
    if not completed or not final_valid:
        return "failed_or_incomplete_preserved"
    if requests == 0:
        return "not_exercised"
    if requests == succeeded == continuations:
        return "lookup_branch_exercised"
    return "incomplete_lookup_evidence"


def _scripted_response(event, name: str, current_lookup_count: int) -> str:
    if name == "future_lookup" and event.seq == 4:
        value = {"kind": "lookup_request", "event_ids": ["e005"]}
    elif name == "unknown_lookup" and event.seq == 5:
        value = {"kind": "lookup_request", "event_ids": ["unknown_event"]}
    elif event.seq == 5 and name in {"one_lookup", "two_lookups", "lookup_limit"} and current_lookup_count < {
            "one_lookup": 1, "two_lookups": 2, "lookup_limit": 3}[name]:
        value = {"kind": "lookup_request", "event_ids": [["e001"], ["e004"], ["e002"]][current_lookup_count]}
    else:
        value = {"kind": "extract", "task_context": [], "state_items": [{
            "operation": "no_change", "source_quote": event.content, "target_ids": [],
            "constraint": None, "authorization": None}]}
    return json.dumps(value, ensure_ascii=False)


def build_transport_case(bundle, interaction: str, final_prompt: str, name: str) -> dict:
    if name not in SCENARIOS:
        raise ValueError("unknown offline transport case")
    config = canonical_hash({"version": VERSION, "model": MODEL.model_dump(),
                             "prompt_sha256": digest(interaction.encode()), "fixture_kind": "no_change_transport"})
    state = BatchStateEngine(CASE, bundle.review_input.events, config, set(bundle.registry.artifacts))
    context = TaskContextLedger(bundle.review_input.events, set(bundle.registry.artifacts))
    history, requests = [], []
    failure = None
    expected_fault = {"future_lookup": (4, "lookup event outside visible history"),
                      "unknown_lookup": (5, "lookup event outside visible history"),
                      "lookup_limit": (5, "lookup request budget exhausted")}.get(name)
    expected_failure = expected_fault is not None
    for index, event in enumerate(bundle.review_input.events):
        prior_lookups = len(history)
        current_lookups = []
        while True:
            request = build_request(bundle, index, state.snapshot(), context.snapshot(), interaction,
                                    MODEL, POLICY, prior_lookup_attempts=prior_lookups,
                                    lookup_records=current_lookups)
            encoded = wire_bytes(request)
            if len(encoded) > MAX_BYTES or len(requests) >= MAX_ATTEMPTS:
                raise ValueError("offline transport exceeds frozen request bounds")
            raw = _scripted_response(event, name, len(current_lookups))
            requests.append({"stage": "lookup_continuation" if current_lookups else "extract",
                             "event_seq": event.seq, "request": request.model_dump(mode="json"),
                             "wire_bytes": len(encoded), "wire_sha256": digest(encoded),
                             "scripted_response": raw})
            try:
                parsed = parse_response(raw, bundle, index, config, f"transport_{len(requests):03d}",
                                         POLICY, lookup_attempts_used=prior_lookups + len(current_lookups))
                if isinstance(parsed, LookupIntent):
                    session = HistoryLookupSession(bundle.review_input.events,
                        DecisionPoint(event_id=event.event_id, seq=event.seq), POLICY,
                        initial_attempts=prior_lookups + len(current_lookups))
                    record = session.lookup(LookupRequest(event_ids=parsed.event_ids))
                    if record.status != "succeeded":
                        raise ValueError(record.denial_reason)
                    stable = record.model_copy(update={"elapsed_ms": 0.0})
                    current_lookups.append(stable)
                    history.append(LookupEvidence(event_id=event.event_id, seq=event.seq, record=stable))
                    continue
                context.submit(parsed.context_batch)
                record = state.submit_batch(parsed.transition_batch)
                if not record.accepted:
                    raise ValueError(record.rejection_reason)
            except ValueError as exc:
                if (not expected_failure or event.seq != expected_fault[0] or
                        expected_fault[1] not in str(exc)):
                    raise
                failure = {"event_seq": event.seq, "reason": str(exc)}
            break
        if failure is not None:
            break
    if expected_failure != (failure is not None):
        raise ValueError("offline fault fixture did not stop as expected")
    final_request = None
    if failure is None:
        final_request = build_final_request(bundle, state.snapshot(), context.snapshot(), final_prompt,
                                             MODEL, POLICY, history)
        encoded = wire_bytes(final_request)
        if len(encoded) > MAX_BYTES or len(requests) >= MAX_ATTEMPTS:
            raise ValueError("offline final request exceeds frozen bounds")
        requests.append({"stage": "final_review", "event_seq": None,
                         "request": final_request.model_dump(mode="json"),
                         "wire_bytes": len(encoded), "wire_sha256": digest(encoded),
                         "scripted_response": None})
    wire_text = json.dumps(requests, ensure_ascii=False)
    if any(marker in wire_text for marker in ("EVALUATOR_ONLY_M1_REFERENCE", "evaluation_private", "oracle_con_")):
        raise ValueError("private material marker in model-visible transport")
    return {"name": name, "execution_kind": "offline_scripted", "actual_provider_attempts": 0,
            "new_model_calls": 0, "evidence_classification": "offline_transport_only",
            "accepted_events": state.snapshot().through_seq, "lookup_attempts": len(history),
            "scripted_lookup_requests": sum(json.loads(row["scripted_response"])["kind"] == "lookup_request"
                                            for row in requests if row["scripted_response"] is not None),
            "simulated_requests_built": len(requests), "failure": failure,
            "final_request_prepared": final_request is not None, "final_model_prediction": "not_run",
            "synthetic_state_kind": "all_no_change_not_a_reference_state", "requests": requests}


def build_files(root: Path) -> dict:
    bundle = load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact" / CASE)
    interaction_path = root / "prompts/incremental_interaction_v1.md"
    final_path = root / "prompts/incremental_final_review_v1.md"
    interaction, final_prompt = interaction_path.read_text(encoding="utf-8"), final_path.read_text(encoding="utf-8")
    cases = [build_transport_case(bundle, interaction, final_prompt, name) for name in SCENARIOS]
    first = cases[0]["requests"][0]["request"]
    parity = {method: baseline_request(bundle, method, "public evidence parity only", MODEL).evidence_sha256
              for method in ("full_direct", "full_checklist")}
    if any(value != first["evidence_sha256"] for value in parity.values()):
        raise ValueError("full-context baseline public evidence mismatch")
    plan = {"schema_version": VERSION, "status": "draft_unapproved", "paid_run_ready": False,
            "purpose": "one_development_observation_of_model_selected_lookup_no_method_ranking",
            "case_id": CASE, "model_candidate": MODEL.model_dump(),
            "model_availability": "not_checked_offline", "prompt_policy": "existing_prompts_unchanged",
            "trigger_policy": "model_selected",
            "public_bundle_sha256": bundle.sha256, "public_evidence_sha256": first["evidence_sha256"],
            "baseline_evidence_sha256": parity,
            "interaction_prompt_sha256": digest(interaction_path.read_bytes()),
            "final_prompt_sha256": digest(final_path.read_bytes()),
            "bounds": {"max_events": 5, "max_lookup_requests": 2, "max_events_per_lookup": 2,
                       "max_provider_attempts": MAX_ATTEMPTS, "max_output_tokens_per_attempt": 4096,
                       "max_wire_bytes_per_attempt": MAX_BYTES, "max_response_bytes_per_attempt": MAX_BYTES,
                       "timeout_seconds": 90, "concurrency": 1, "retries": 0, "stop_on_first_failure": True},
            "price_ceiling": None, "price_checked_at": None, "local_cap_cny": None,
            "worst_case_reserved_cny": None, "new_authorization": None, "old_authorizations_reusable": False,
            "real_runner_binding": None,
            "blocked_reasons": ["current_price_not_checked", "budget_not_frozen", "new_authorization_missing",
                                "versioned_real_runner_not_bound"],
            "outcomes": {"completed_without_lookup": "not_exercised",
                         "model_lookup_return_continuation_and_final_valid": "lookup_branch_exercised",
                         "scripted_or_forced_trigger": "transport_only_not_autonomous_evidence"},
            "untriggered_policy": "preserve_once_do_not_rerun_without_new_authorization",
            "failure_cost_policy": "reserve_before_each_attempt_never_release_failed_attempts",
            "new_model_calls": 0,
            "source_hashes": {name: digest((root / name).read_bytes()) for name in (
                "scripts/s3_lookup_offline_preflight.py", "scripts/m3i_task_context.py",
                "scripts/m3j_transition_batch.py", "scripts/m3k_history_lookup.py",
                "scripts/m3o_interaction_contract.py", "src/writscope/providers/deepseek.py", "uv.lock")}}
    plan["plan_sha256"] = canonical_hash(plan)
    return {"README.md": README, "plan.json": plan, "first_request.json": first,
            "transport_cases.json": {"version": VERSION, "new_model_calls": 0, "cases": cases}}


def prepare(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.exists() or output.parent != root / "reports/local":
        raise ValueError("preflight requires a fresh direct child of reports/local")
    files = build_files(root)
    output.mkdir()
    for name, value in files.items():
        if isinstance(value, str):
            (output / name).write_text(value, encoding="utf-8", newline="\n")
        else:
            write_json(output / name, value)
    manifest = {"version": VERSION, "status": "offline_prepared_unapproved", "new_model_calls": 0,
                "plan_sha256": files["plan.json"]["plan_sha256"],
                "artifacts": {name: digest((output / name).read_bytes()) for name in files}}
    write_json(output / "run.json", manifest)
    return manifest


def verify(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    files = build_files(root)
    manifest = _read(output / "run.json")
    if manifest != {"version": VERSION, "status": "offline_prepared_unapproved", "new_model_calls": 0,
                    "plan_sha256": files["plan.json"]["plan_sha256"],
                    "artifacts": {name: digest((output / name).read_bytes()) for name in files}}:
        raise ValueError("lookup offline preflight manifest mismatch")
    for name, value in files.items():
        actual = (output / name).read_text(encoding="utf-8") if isinstance(value, str) else _read(output / name)
        if actual != value:
            raise ValueError("lookup offline preflight deterministic replay mismatch")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = (prepare if args.action == "prepare" else verify)(args.root, args.output)
    print(f"{VERSION}: {result['status']}; new_model_calls=0")


if __name__ == "__main__":
    main()
