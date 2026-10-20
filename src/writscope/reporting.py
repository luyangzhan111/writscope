"""Human-readable view of the independent scoring output."""

from pathlib import Path


def write_report(summary: dict, path: Path):
    real = summary["execution_kind"] == "real_model"
    lines = [
        "# M2b 真实接口接通记录" if real else "# M2a 离线流程验证报告", "",
        f"运行 ID：`{summary['run_id']}`。类型：`{summary['execution_kind']}`；" +
        ("仅为六任务接通试跑，**不是正式方法比较**。" if real else "仅验证流程，**不是模型效果**。"), "",
        f"参考标签 SHA-256：`{summary['reference_sha256']}`；状态：项目编制草稿。", "",
        "| 方法 | 完成/总数 | 影响 | 授权 | 决策 | 三轴全对 | 误放行 | 误拦截 | 诊断配对 | 调用尝试 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for method, data in summary["methods"].items():
        fraction = lambda item: f"{item['correct']}/{item['denominator']}"
        lines.append("| " + " | ".join([
            method, f"{data['succeeded']}/{data['jobs']}",
            *(fraction(data["axes"][axis]) for axis in ("impact", "authorization", "decision")),
            fraction(data["all_axes"]),
            f"{data['false_allow']['count']}/{data['false_allow']['denominator']}",
            f"{data['false_block']['count']}/{data['false_block']['denominator']}",
            fraction(data["diagnostic_pairs"]), str(data["cost"]["provider_attempts"]),
        ]) + " |")
    cost = summary["cost"]
    if real:
        budget = summary["budget"]
        lines += ["", f"实际发起模型请求：{cost['actual_model_calls']}；提供商 token 与各方法成本明细见 `summary.json`。",
                  f"本地预留费用估计：{budget['reserved_estimated_cny']} 元 / 授权上限 {budget['authorized_max_cny']} 元。提供商最终账单未知，不以估计冒充实付。", ""]
    else:
        lines += ["", f"实际模型调用：{cost['actual_model_calls']}；实际模型 token / 费用：0 / 0 USD。",
                  f"脚本化 provider 尝试：{cost['provider_attempts']}；各方法合成用量单独记录在 `summary.json`，未知值保留 null。", ""]
    lines += ["三种历史属于同一场景，配对有重叠；参考标签不是独立金标准。", "",
              "逐任务状态、错误、三轴对照及完整成本见 `summary.json`；请求与原始输出见运行目录。", ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
