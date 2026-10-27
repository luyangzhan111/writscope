"""Evaluator-only common report; reference labels never enter execution."""
import argparse
import asyncio
from pathlib import Path

from scripts import s3_acceptance_v4 as common
from writscope.artifacts import canonical_hash, digest, write_json


async def prior_coverage(root):
    """Rebuild the old nine-job round without writing to frozen artifacts."""
    from scripts import s3_coverage_v1 as old
    from scripts.s3_coverage_replay_compat_v1 import install_compat_read
    original = install_compat_read()
    try:
        run = root / "reports/local/s3-coverage-followup-live-20261026"
        verified = await old.verify(root, root / "reports/local/s3-coverage-followup-preflight-20261026", run)
        jobs = old.read(run / "jobs.json")
        attempted = len(jobs) == 9 and all(row["status"] != "not_run" for row in jobs)
        if not attempted or verified["execution_kind"] != "real_model":
            raise ValueError("prior real nine-job development coverage missing")
        return {"status": "verified_all_nine_attempted", "run_sha256": digest((run / "run.json").read_bytes()),
                "plan_sha256": verified["plan_sha256"], "actual_model_calls": verified["actual_model_calls"],
                "cross_round_ranking": False}
    finally:
        old.read = original


def metrics(rows):
    fraction = lambda count, denominator: {"count": count, "denominator": denominator}
    axes = ("impact", "authorization", "decision")
    valid = [row for row in rows if row["observed"] is not None]
    negative = [r for r in rows if r["expected"]["impact"] == "breaking" and r["expected"]["authorization"] == "not_covered"]
    positive = [r for r in rows if r["expected"]["decision"] == "allow"]
    return {"valid": fraction(len(valid), len(rows)),
            "axes": {axis: fraction(sum(r["observed"] is not None and r["observed"][axis] == r["expected"][axis] for r in rows), len(rows)) for axis in axes},
            "all_axes": fraction(sum(r["observed"] == r["expected"] for r in rows), len(rows)),
            "false_allow": fraction(sum(r["observed"] is not None and r["observed"]["decision"] == "allow" for r in negative), len(negative)),
            "false_block": fraction(sum(r["observed"] is not None and r["observed"]["decision"] in {"revise", "ask", "investigate"} for r in positive), len(positive)),
            "positive_not_allowed_including_missing": fraction(sum(r["observed"] is None or r["observed"]["decision"] != "allow" for r in positive), len(positive)),
            "no_valid_output": fraction(len(rows)-len(valid), len(rows))}


async def report(root, prepared, output, *, run_dir=None):
    if output.resolve().parent != root.resolve() / "reports/local":
        raise ValueError("report must be a fresh direct child of reports/local")
    plan = common.preflight(root, prepared, verify=True)
    manifest = await common.verify(root, prepared, run_dir) if run_dir is not None else None
    real = manifest is not None and manifest["execution_kind"] == "real_model"
    coverage = await prior_coverage(root) if real else None
    if coverage is not None and coverage["run_sha256"] != plan["prior_coverage_manifest_sha256"]:
        raise ValueError("prior coverage evidence differs from frozen plan")
    jobs = common.read(run_dir / "jobs.json") if manifest is not None else [
        {**unit, "status": "not_run", "result": None} for unit in plan["jobs"]]
    calls = common.read(run_dir / "calls.json") if manifest is not None else []
    labels, reference_hash = {}, None
    if real:
        # No path supplied by a method. Only after replay, in this evaluator command.
        path = root / "evaluation_private/case_001/s3_acceptance_labels_v1.json"
        reference = common.read(path)
        if reference.get("kind") != "reference_labels_not_predictions":
            raise ValueError("unexpected evaluator reference")
        for case in common.CASES:
            selected = [row for row in reference["labels"] if row["case_id"] == case]
            if len(selected) != 1:
                raise ValueError("reference must be unique")
            labels[case] = {axis: selected[0]["expected_"+axis] for axis in ("impact", "authorization", "decision")}
        reference_hash = digest(path.read_bytes())
    rows = []
    for job in jobs:
        own = [call for call in calls if call["job_id"] == job["job_id"]]
        observed = ({axis: job["result"][axis] for axis in ("impact", "authorization", "decision")}
                    if job["result"] is not None else None)
        rows.append({"job_id": job["job_id"], "case_id": job["case_id"], "method": job["method"],
                     "status": job["status"], "observed": observed,
                     "expected": labels.get(job["case_id"]), "failure": job.get("failure"),
                     "cost": common.totals(own) if manifest is not None else None,
                     "local_rebuild_elapsed_ms": job.get("local_rebuild_elapsed_ms"),
                     "lookup_evidence": job.get("lookup_evidence", "not_run"),
                     "public_sources": plan["public_sources"][job["case_id"]]})
    real_lookup = real and any(row["lookup_evidence"] == "lookup_branch_exercised" for row in rows)
    summary = {"version": "s3_acceptance_report_v4", "execution_kind": manifest["execution_kind"] if manifest else "planned_not_run",
               "plan_sha256": canonical_hash(plan), "reference_status": "project_authored_draft_external_review_pending",
               "reference_sha256": reference_hash, "independent_review": "pending_external_person",
               "repeat_count": 1, "cases": len(common.CASES), "independent_families": 1, "planned_jobs": len(plan["jobs"]),
               "actual_model_calls": manifest["actual_model_calls"] if manifest else 0,
               "totals": manifest["totals"] if manifest else None,
               "local_rebuild_elapsed_ms": sum(job.get("local_rebuild_elapsed_ms", 0) for job in jobs) if manifest else None,
               "metrics": {"incremental": metrics(rows)} if real else None,
               "metrics_status": "development_draft_reference" if real else "no_model_performance_metrics",
               "source_run_sha256": digest((run_dir / "run.json").read_bytes()) if manifest else None,
               "rows": rows, "budget_matched": False, "s3_full_exit": "not_satisfied",
               "prior_coverage": coverage,
               "s3_development_exit": "satisfied_author_review_only" if real_lookup and coverage else "not_satisfied",
               "real_lookup_branch_verified": bool(real_lookup),
               "bill_cny": "unknown", "human_review_cost": "unknown", "shared_preparation_cost": "unknown",
               "claim_scope": "one_family_development_round_no_generalization_or_cost_advantage_claim"}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "summary.json", summary)
    lines = ["# S3 增量覆盖补充报告", "", f"类型：{summary['execution_kind']}；计划两条增量任务、两条开发历史（c002 原历史；c004 回查依赖合成历史）、一个场景家族、R=1。",
             "参考：项目作者草稿，实际独立审核待补。完整 S3 尚未签收；未知账单/人工与共享准备成本保持 unknown。", "",
             "| 案例 | 方法 | 状态 | 观察三轴 | 调用数 | 本地预留/元 | 回查 |",
             "| --- | --- | --- | --- | ---: | ---: | --- |"]
    for row in rows:
        axes = "/".join(row["observed"].values()) if row["observed"] else "未运行/无有效输出"
        cost = row["cost"]
        lines.append(f"| {row['case_id']} | {row['method']} | {row['status']} | {axes} | {cost['provider_attempts'] if cost else '—'} | {cost['reserved_cny'] if cost else '—'} | {row['lookup_evidence']} |")
    if summary["metrics"] is not None:
        lines += ["", "增量指标保留失败和缺失任务的原计划分母；无效输出率与误放行/误拦截同时报告。", "",
                  "| 方法 | 有效 | 三轴全对 | 误放行 | 误拦截 | 无有效输出 |", "| --- | --- | --- | --- | --- | --- |"]
        for method, value in summary["metrics"].items():
            cell = lambda field: f"{value[field]['count']}/{value[field]['denominator']}"
            lines.append(f"| {method} | {cell('valid')} | {cell('all_axes')} | {cell('false_allow')} | {cell('false_block')} | {cell('no_valid_output')} |")
    else:
        lines += ["", "未计算模型准确率：未执行真实同轮运行，或本报告仅是脚本接线演练。"]
    lines += ["", "本轮只补充两条增量轨迹，不重跑基线，不与跨轮结果拼接做方法排名。",
              "c004为专门设计的开发回查诊断，同属已调试家族，不是泛化样本。旧事件原文从初始请求隐藏，仅提供事件ID/序号/角色；模型可按需请求历史回查。未请求时保留 not_exercised。脚本回查只能是 offline_transport_only。",
              "完整费用、三轴分子分母、缺失和错误见 summary.json。", ""]
    lines += [f"开发验收：{summary['s3_development_exit']}；真实自主回查分支：{summary['real_lookup_branch_verified']}。",
              "开发验收允许记录模型误判和失败；实际独立审核及完整研究验收另列待办。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    write_json(output / "run.json", {"summary_sha256": digest((output/"summary.json").read_bytes()),
                                     "report_sha256": digest((output/"report.md").read_bytes())})
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", type=Path)
    args = parser.parse_args(argv)
    result = asyncio.run(report(args.root, args.preflight, args.output, run_dir=args.run))
    print(result["execution_kind"], "actual_model_calls", result["actual_model_calls"])


if __name__ == "__main__":
    main()
