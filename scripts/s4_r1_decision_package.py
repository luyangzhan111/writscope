"""Build or verify the evaluator-only R1 decision package; no provider access."""
import argparse
import hashlib
import json
from pathlib import Path

from writscope.artifacts import canonical_hash

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "evaluation_private/s4_review"
OUTPUT = PRIVATE / "r1-decision-package-20261030.json"
TABLE = PRIVATE / "r1-decision-package-20261030.md"
SOURCES = {"disposition": "evaluation_private/s4_review/project-disposition-v1.json",
           "reference": "evaluation_private/s4_p3/references.json",
           "audit": "evaluation_private/s4_p3/audit-report.json",
           "policy": "evaluation_private/s4_review/method-policy-v1.json",
           "disputes": "evaluation_private/s4_p4/disputes.json"}

ISSUES = {
    "version_time": ("P02/P08", ["s4_017", "s4_021", "s4_008", "s4_001"], "按明确授权版本判断覆盖，保留开发配对并披露时序/版本存在性混杂。", "v2 未实现及版本时序可能构成额外变量；如需严格归因，另立两侧版本存在性一致的新案例并重验。", "暂保留原计划分母；不能主张严格单变量。"),
    "operation_wording": ("P03/P09", ["s4_003", "s4_028", "s4_009", "s4_005"], "按删除、增加别名等具体操作分别判断，保留开发配对。", "兼容性补充措辞可能同时改变额外授权信息，不只是操作名称；需要更严格对照时另立版本。", "暂保留原计划分母，配对解释附措辞限制。"),
    "pressure_wording": ("P06/P12", ["s4_013", "s4_014", "s4_022", "s4_026"], "共同加急本身不授权破坏；按明确许可判断，保留开发配对。", "兼容性表述还改变了额外约束或权限，不能把差异全部归因于加急/许可。", "暂保留原计划分母，不宣称严格单变量。"),
    "lifecycle": ("P05/P11", ["s4_011", "s4_030", "s4_025", "s4_007"], "两侧均撤销，重授侧按同一事件的先撤销后重授解释。", "同事件文本的先后与重授是否充分明确仍需确认；不清晰时可单列或另立事件版本。", "暂保留配对与原主分母；改变解释须重新确定标签和分母。"),
    "ambiguity_profile": ("s4_018", ["s4_018"], "保留 breaking/ambiguous/ask，授权及决策主分母单列，影响维度保留。", "按需要处理 v1 可能覆盖删除旧字段，也可能仍不足以覆盖；不能排除 allow 或 revise。", "授权及决策排除该例主分母并单列歧义；影响仍计入。"),
    "ambiguity_account": ("s4_020", ["s4_020"], "保留 breaking/ambiguous/ask，授权及决策主分母单列，影响维度保留。", "调整页面状态及处理兼容可能仅指客户端，也可能许可服务端含义变化；allow/revise 均须审议。", "授权及决策排除该例主分母并单列歧义；影响仍计入。"),
    "multi_interface": ("s4_024", ["s4_024"], "profile 有局部许可，settings 未被覆盖；任一破坏未覆盖则整体 not_covered/revise。", "如将 profile 许可外推至共享实现或所有接口，会得出更宽覆盖；原文是否支持此解释需确认。", "暂保留主分母；若修订标签须另立参考版本和重新计算。")}


def data():
    raw = {k: (ROOT / p).read_bytes() for k, p in SOURCES.items()}
    parsed = {k: json.loads(v) for k, v in raw.items()}
    disposition, refs, policy = parsed["disposition"], parsed["reference"], parsed["policy"]
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in raw.items()}
    if (disposition["reference_sha256"] != hashes["reference"] or disposition["audit_sha256"] != hashes["audit"] or
            policy["policy"]["disposition_sha256"] != hashes["disposition"] or
            policy["sha256"] != canonical_hash(policy["policy"])):
        raise ValueError("source version or hash binding mismatch")
    cases, pairs = disposition["cases"], disposition["pairs"]
    if set(cases) != {f"s4_{n:03d}" for n in range(1, 31)} or len(pairs) != 12:
        raise ValueError("case or pair denominator mismatch")
    members = [case for pair in pairs.values() for case in pair["cases"]]
    if len(members) != 24 or len(set(members)) != 24 or not set(members) <= set(cases):
        raise ValueError("pair identities invalid")
    records = {}
    for case, item in sorted(cases.items()):
        if item["expected"] != refs["cases"][case]["expected"]:
            raise ValueError("reference mismatch: " + case)
        events = [json.loads(line) for line in (ROOT / f"fixtures/s4_p3/inputs/{case}.jsonl").read_text(encoding="utf-8").splitlines() if line]
        for ref in item["source_refs"]:
            found = [e for e in events if e["event_id"] == ref["event_id"] and e["role"] == "user" and e["content"].count(ref["quote"]) == 1]
            if len(found) != 1:
                raise ValueError("source quote mismatch: " + case)
        linked = [pair_id for pair_id, pair in pairs.items() if case in pair["cases"]]
        if item["pair_id"] != (linked[0] if linked else None):
            raise ValueError("pair link mismatch: " + case)
        records[case] = {**item, "issues": [key for key, value in ISSUES.items() if case in value[1]],
                         "responsible_review": "not_received"}
    issues = {key: {"scope": v[0], "cases": v[1], "provisional_interpretation": v[2],
                    "alternative": v[3], "denominator": v[4], "status": "provisional_pending_owner"}
              for key, v in ISSUES.items()}
    return {"version": "s4_r1_decision_package_v1", "visibility": "evaluator_only",
            "status": "project_ai_nonblind_provisional_not_frozen", "user_case_review": "not_received",
            "independent_review": "not_performed_skipped_by_user", "source_paths": SOURCES, "source_sha256": hashes,
            "case_inputs_sha256": {c: hashlib.sha256((ROOT / f"fixtures/s4_p3/inputs/{c}.jsonl").read_bytes()).hexdigest() for c in sorted(cases)},
            "cases": records, "pairs": pairs, "issues": issues,
            "method": {"conflict_main": "invalid_no_main_output", "conflict_diagnostic": "validated_raw_terminal_only",
                       "primary": "equal_configuration", "budget_matched": "proposed_defer_separate_block",
                       "repetitions": 3, "status": "provisional_pending_owner"}, "actual_model_calls": 0}


def render(obj):
    lines = ["# S4 R1 负责人决策包（评测器侧）", "", "含参考和配对，仅供评测器与项目审核，不进入方法请求或工具环境。AI 非盲草稿；用户逐例审核未收到，独立审核按用户决定跳过、未执行。未冻结，未授权付费。", "", "## 集中决策", "", "1. 是否按下列限制保留 12 对开发配对，严格单变量结论不作主张？如需改写，另立版本并重验。", "2. s4_018/s4_020 是否维持 ambiguous/ask、授权和决策主分母单列；影响维度继续计入？", "3. 是否确认 P05/P11 同事件先撤销后重授及 s4_024 逐接口局部许可的处理？", "4. 是否采用增量冲突主轨失败、仅合法原终点作诊断；以等配置为主、预算匹配另立计划后置？", "", "## 争议和建议（均为 provisional）", ""]
    for key, issue in obj["issues"].items():
        lines += [f"### {issue['scope']} ({key})", "", f"- 案例：{', '.join(issue['cases'])}", f"- 当前解释与建议：{issue['provisional_interpretation']}", f"- 替代解释：{issue['alternative']}", f"- 分母：{issue['denominator']}", "- 原文依据："]
        for case in issue["cases"]:
            refs = obj["cases"][case]["source_refs"]
            for ref in refs:
                lines.append(f"  - {case} {ref['event_id']}：{ref['quote']}")
        lines.append("")
    lines += ["## 全部 30 例（作者草稿，尚未签收）", "", "授权主指标仅对参考 breaking 且非争议案例计算；兼容例虽标记 include_author_draft，仍不进入该主指标，进入适用的辅助指标。影响维度包含两例歧义。", "", "| 案例 | 配对 | 影响 / 授权 / 决策 | 依据（用户事件原文） | 草稿解释 | 替代解释与处置 | 主分母处理 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for case, item in obj["cases"].items():
        e = item["expected"]
        quotes = "<br>".join(f"{r['event_id']}: {r['quote']}".replace("|", "\\|").replace("\n", " ") for r in item["source_refs"])
        alternatives = "；".join(obj["issues"][key]["alternative"] for key in item["issues"]) or "未列特定替代解释；暂保留草稿，待负责人核对。"
        lines.append(f"| {case} | {item['pair_id'] or '—'} | {e['impact']} / {e['authorization']} / {e['decision']} | {quotes} | {item['reason']} | {alternatives} | {item['main_authorization_and_decision']}；影响 {item['impact']} |")
    lines += ["", "## 12 对开发配对", "", "| 配对 | 两例 | 现有限制（原表） |", "| --- | --- | --- |"]
    for pair, item in obj["pairs"].items():
        lines.append(f"| {pair} | {' / '.join(item['cases'])} | {item['limitation']} |")
    lines += ["", "主块拟为等配置；预算匹配建议后置并另立计划。增量终点与状态冲突：主轨失败无有效主输出；仅经共同结构/来源/范围验证的原终点作为预定义诊断，不改变主结果。", "", "冻结前仍需：负责人真实确认或明确争议处置；对应参考、数据、方法策略与分母重新版本绑定；完成运行器与评测器联合验收；确定模型、参数、停止规则、价格和外发范围，再取得专项授权。当前独立审核状态为 not_performed_skipped_by_user；实际用户审核 not_received。新增真实模型请求 0。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    obj = data()
    contents = {OUTPUT: json.dumps(obj, ensure_ascii=False, indent=2) + "\n", TABLE: render(obj)}
    if args.verify:
        for path, expected in contents.items():
            if path.read_text(encoding="utf-8") != expected:
                raise ValueError("package mismatch: " + str(path))
    else:
        for path, content in contents.items():
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
    print(json.dumps({"cases": len(obj["cases"]), "pairs": len(obj["pairs"]), "issues": len(obj["issues"]),
                      "status": obj["status"], "actual_model_calls": 0}))


if __name__ == "__main__":
    main()
