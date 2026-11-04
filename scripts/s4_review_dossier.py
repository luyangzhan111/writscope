"""Build an evaluator-only evidence index; never promote author drafts to reviews."""

import argparse
from pathlib import Path

from writscope.artifacts import digest
from writscope.contracts import parse_json


def build(reference_path, audit_path):
    raw = reference_path.read_bytes()
    refs = parse_json(raw.decode("utf-8"))
    audit = parse_json(audit_path.read_text(encoding="utf-8"))
    if refs.get("version") != "s4_p3_author_draft_v1" or audit.get("version") != "s4_p3_audit_v1":
        raise ValueError("unexpected input version")
    if digest(raw) != audit.get("reference_sha256"):
        raise ValueError("reference does not match saved audit")
    cases = {item["case_id"]: item for item in audit["cases"]}
    pairs = {item["pair_id"]: item for item in audit["pairs"]}
    if (set(cases) != set(refs["cases"]) or len(cases) != 30 or
            set(pairs) != set(refs["pairs"]) or len(pairs) != 12):
        raise ValueError("inventory mismatch")
    lines = ["# S4 P3 逐例证据索引（评测器侧）", "",
             "含作者参考与配对映射，禁止进入方法请求及工具环境。此索引整理已有作者草稿与结构审计，"
             "不是新的语义审核、用户确认或独立审核；所有争议仍待处理。", "",
             f"参考文件 SHA-256：`{digest(raw)}`；结构审计文件 SHA-256：`{digest(audit_path.read_bytes())}`。", "",
             "## 逐例", ""]
    for case_id in sorted(cases):
        ref, case = refs["cases"][case_id], cases[case_id]
        labels = ref["expected"]
        if case["impact"] != labels["impact"] or ref["semantic_review"] != "pending":
            raise ValueError("reference and saved audit mismatch")
        lines += [f"### {case_id}", "",
                  f"- 已记录影响：`{case['impact']}`，{case['impact_count']} 项；公开包哈希 `{case['bundle_sha256']}`。",
                  f"- 作者草稿：`{labels['impact']} / {labels['authorization']} / {labels['decision']}`；"
                  f"配对：`{ref.get('pair_id') or '无'}`；语义审核待定。",
                  f"- 作者理由：{ref['rationale']}", "- 来源链（原文摘录）："]
        for source in ref["source_refs"]:
            lines.append(f"  - `{source['event_id']}`：{source['quote']}")
        lines.append("")
    lines += ["## 配对文字差异", "", "以下逐字差异只用于定位文本；不证明语义上只有一个变量。", ""]
    for pair_id in sorted(pairs):
        pair = pairs[pair_id]
        if pair["cases"] != refs["pairs"][pair_id]:
            raise ValueError("pair mismatch")
        left, right = pair["cases"]
        lines += [f"### {pair_id}：{left} / {right}（{pair['variable']}）", "",
                  f"- 左侧末事件引文：{refs['cases'][left]['source_quote']}",
                  f"- 右侧末事件引文：{refs['cases'][right]['source_quote']}",
                  "- 共同前缀、候选、契约及影响已由保存的 P3 结构审计核对；单变量语义仍待审核。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.references, args.audit)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(result)
    print("Saved evaluator-only index; semantic review remains pending.")


if __name__ == "__main__":
    main()
