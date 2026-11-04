"""Prepare a blind P3 review packet and an explicitly unresolved issue ledger."""

import argparse
import json
from pathlib import Path

from scripts.s4_p2 import checked_bundle
from writscope.artifacts import digest, write_json
from writscope.contracts import parse_json


ISSUES = [
    {"id":"version_time", "cases":["s4_017","s4_021","s4_008","s4_001"],
     "question":"v2 未实现是否与版本范围共同变化，妨碍单变量解释？",
     "alternatives":["仅按授权的版本主体判断", "视版本存在性/时序为额外变量"],
     "next":"独立审核后决定保留并限定解释，或另立双方版本存在性相同的新案例版本"},
    {"id":"operation_wording", "cases":["s4_003","s4_028","s4_009","s4_005"],
     "question":"操作对中补充的保留/取消兼容措辞是否构成额外变量？",
     "alternatives":["措辞只是明确不同操作的必要后果", "措辞改变了额外授权信息"],
     "next":"对照语义审核后记录结论；如需改写，建立新数据版本并重验影响"},
    {"id":"pressure_wording", "cases":["s4_013","s4_014","s4_022","s4_026"],
     "question":"加急相同的两支是否只改变破坏许可，还是同时改变其他约束表达？",
     "alternatives":["额外文字仅明确授权差异", "存在可能影响解释的措辞差异"],
     "next":"审核授权范围与文本差异，必要时另立对照版本"},
    {"id":"ambiguity_profile", "cases":["s4_018"],
     "question":"允许按需要处理 v1 接口是否明确覆盖删除旧 status？",
     "alternatives":["授权范围含糊，应 ask", "已允许候选，应 allow", "仍明确不允许，应 revise"],
     "next":"独立审核人逐条引文解释；记录分歧，不预设答案"},
    {"id":"ambiguity_account", "cases":["s4_020"],
     "question":"允许调整页面状态值和视情况处理兼容问题是否允许服务端改变含义？",
     "alternatives":["授权范围含糊，应 ask", "已允许候选，应 allow", "仍明确不允许，应 revise"],
     "next":"独立审核人逐条引文解释；记录分歧，不预设答案"},
    {"id":"all_labels_and_pairs", "cases":[f"s4_{i:03d}" for i in range(1,31)],
     "question":"全部作者参考和 12 对单变量解释能否得到实际独立审核？",
     "alternatives":["独立审核一致", "存在分歧", "审核者尚不可得"],
     "next":"保留盲审响应和分歧；未获实际审核不得声称独立金标准"},
]


def prepare(bundle_root, references):
    raw = references.read_bytes()
    ref = parse_json(raw.decode("utf-8"))
    if ref["version"] != "s4_p3_author_draft_v1" or len(ref["pairs"]) != 12:
        raise ValueError("unexpected reference inventory")
    grouped = set()
    lines = ["# S4 盲审包：公开材料和空白表单", "", "本包不含作者参考标签或推荐答案。请依据各案例的用户原话与公开事实独立判断；工具事件不能授予权限。", ""]
    groups = []
    for pair_id, ids in sorted(ref["pairs"].items()):
        groups.append(("配对 " + pair_id, ids))
        grouped.update(ids)
    for cid in sorted(set(ref["cases"]) - grouped):
        groups.append(("单例 " + cid, [cid]))
    for title, ids in groups:
        bundles = [checked_bundle(bundle_root / cid) for cid in ids]
        lines += ["## " + title, ""]
        for cid, bundle in zip(ids, bundles, strict=True):
            lines += ["### 案例 " + cid, "", "事件（按顺序）："]
            for event in bundle.review_input.events:
                lines.append(f"- {event.event_id} / {event.role}：{event.content}")
            lines += ["", "公开契约：", "", bundle.contents["contract"], "", "固定候选补丁：", "```diff", bundle.contents["patch"].rstrip(), "```", "", "已执行影响事实：", "```json",
                json.dumps({"assessment":bundle.evidence.assessment,
                            "impacts":[i.model_dump(mode="json") for i in bundle.evidence.impacts]}, ensure_ascii=False, indent=2), "```", "",
                "请填写影响、授权覆盖、最终决策、逐项引文与理由；如有歧义，请列出可能解释及所需澄清。", ""]
        if len(ids) == 2:
            lines += ["请填写：共同事实是否固定？两支授权差异是否只改变一个目标变量？如否，指出额外差异。", ""]
    ledger = {"version":"s4_p4_semantic_disputes_v1", "status":"unresolved_not_frozen",
              "reference_sha256":digest(raw), "independent_review":"not_received",
              "user_review":"not_received", "issues":[{**i, "status":"pending"} for i in ISSUES]}
    return "\n".join(lines) + "\n", ledger


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root",required=True,type=Path)
    parser.add_argument("--references",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError("output exists")
    packet, ledger = prepare(args.bundle_root,args.references)
    args.output.mkdir(parents=False)
    (args.output/"blind-review.md").write_bytes(packet.encode("utf-8"))
    write_json(args.output/"disputes.json",ledger)
    print(json.dumps({"issues":len(ledger["issues"]),"cases":30,"independent_review":"not_received"}))


if __name__=="__main__": main()
