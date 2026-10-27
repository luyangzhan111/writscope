"""Prepare public-source-only independent-review materials; no gold or model I/O."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import load_bundle, parse_json

VERSION = "s3_independent_review_pack_v1"
CASES = ("c001", "c002", "c003")
RESPONSE = "reviewer_response.json"
README = """# S3 独立审核材料（待审核）

本包只有合成案例的公开原文、候选及已公开影响证据，没有项目作者的参考状态或参考判断。
审核结果属于评测器私有材料，不得传给被测模型。准备材料不代表独立审核已完成。
阅读内容已内嵌在各 JSON 的 artifacts 中；registry 原路径是来源标识，无需打开仓库其他文件。

## 操作顺序

1. 先填写 reviewer_response.json 的审核者身份、参与本项目/见过答案的情况。
2. 按顺序打开 stage1/shared_prefix_001.json 至 004.json，填写四个共同前缀的状态。
   三例前四事件完全相同，因此只审核一次；每步只依据当前前缀，不用后续消息改写早期状态。
3. 保存阶段一结果，再打开 stage2 的三个分支文件，分别填写 e005 后状态及三轴判断。
4. 每项状态写自己的 ID、对象/版本/字段/操作/条件、有效性和精确引文；可写空数组表示没有条目。
   null 表示未审核，不能用 null 代替明确判断。保留歧义和疑问，不为通过检查而扩大许可。
5. 全部判断和理由填写后，再由项目负责人另行组织与作者材料对照、分歧处理和版本确认。
   本包不自动接纳答案，也不能仅凭表单自报身份证明审核者独立。

## 表单含义

- task_context：任务要求原文及公开验证引用；不能把它自动当成破坏许可。
- constraints：约束对象、predicate、来源、生命周期和条件等，由审核者自行描述。
- authorizations：授权对象、操作范围、条件、关联约束和来源；不要依据工具自称的身份授权。
- exceptions：局部例外及关联约束/授权；lifecycle_changes 保存撤销、替代或重授权关系。
- ambiguities：尚不能唯一判断的语义；rationale 和 source_refs 说明依据。
- terminal.impact：compatible / breaking / unknown。
- terminal.authorization：covered / not_covered / ambiguous / not_needed。
- terminal.decision：allow / revise / ask / investigate。三个轴分别判断并解释组合。

## 独立性与材料完整性

共同前缀 + 三分支共七个审核单元，对应三历史的十五个前缀位置；不是七个独立场景。
如果已经看过作者判断，仍须如实记录，不能把非盲审写成盲审；不同审核者与盲审是两件事。
目录分阶段只帮助操作顺序，不提供访问控制，不能证明审核者没看过后续材料。
stage1_completed_before_branch_review 是审核者声明，还须结合真实审核记录核实。
身份/独立性采用前需要可信的人类来源证据；表单自身的 role/attestation 字段不构成授权。

reviewer_response.template.json、公开视图和本说明为冻结材料；reviewer_response.json 是可填写副本。
verify 只检查冻结材料及公开来源，允许填写副本变化，不宣称已审核或语义正确。
不要在填表前打开项目作者的 labels、annotation 或诊断结论，避免答案锚定。
"""


def _read(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def _view(bundle, cutoff: int) -> dict:
    review = bundle.review_input.model_dump()
    review["events"] = review["events"][:cutoff]
    event = bundle.review_input.events[cutoff - 1]
    review["decision_point"] = {"event_id": event.event_id, "seq": event.seq}
    return {"visibility": "evaluator_only_review_material", "source_kind": "synthetic_public_evidence",
            "review_input": review, "artifact_registry": bundle.registry.model_dump(),
            "artifacts": dict(bundle.contents)}


def _unit(event_id: str, seq: int, prefix_hash: str) -> dict:
    return {"event_id": event_id, "seq": seq, "visible_prefix_sha256": prefix_hash,
            "task_context": None, "constraints": None, "authorizations": None,
            "exceptions": None, "lifecycle_changes": None, "ambiguities": None,
            "source_refs": None, "rationale": None, "confidence": None}


def build_files(root: Path) -> dict:
    bundles = {case: load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact" / case)
               for case in CASES}
    first = bundles["c001"]
    common = [event.model_dump() for event in first.review_input.events[:4]]
    evidence = {key: value for key, value in _view(first, 5).items()
                if key in {"artifacts", "artifact_registry"}}
    for case, bundle in bundles.items():
        if (bundle.review_input.case_id != case or len(bundle.review_input.events) != 5 or
                [event.model_dump() for event in bundle.review_input.events[:4]] != common or
                {key: value for key, value in _view(bundle, 5).items()
                 if key in {"artifacts", "artifact_registry"}} != evidence):
            raise ValueError("review cases must share the exact public prefix and evidence")
    shared_units, branch_units, files = [], [], {"README.md": README}
    for index in range(4):
        view = _view(first, index + 1)
        event = first.review_input.events[index]
        files[f"stage1/shared_prefix_{index + 1:03d}.json"] = view
        shared_units.append(_unit(event.event_id, event.seq,
                                  canonical_hash(view["review_input"]["events"])))
    for case, bundle in bundles.items():
        view = _view(bundle, 5)
        files[f"stage2/{case}_after_e005.json"] = view
        branch_units.append({"case_id": case,
                             "poststate": _unit("e005", 5, canonical_hash(view["review_input"]["events"])),
                             "terminal": {"impact": None, "authorization": None, "decision": None,
                                          "source_refs": None, "rationale": None}})
    files["reviewer_response.template.json"] = {
        "schema_version": "s3_independent_review_response_v1", "visibility": "evaluator_only",
        "review_status": "pending", "reviewer": {"reviewer_id": None, "reviewed_at": None,
            "prior_project_involvement": None, "prior_exposure_to_author_answers": None,
            "independence_attestation": None, "trusted_identity_evidence_ref": None},
        "stage1_completed_before_branch_review": None,
        "shared_prefix_reviews": shared_units, "branch_reviews": branch_units,
        "disagreements_and_questions": None}
    files["source_manifest.json"] = {
        "schema_version": VERSION, "visibility": "evaluator_only", "status": "awaiting_independent_reviewer",
        "public_bundles": {case: bundle.sha256 for case, bundle in bundles.items()},
        "source_spec_sha256": digest((root / "specs/first-case.md").read_bytes()),
        "shared_review_units": 4, "branch_review_units": 3, "represented_case_prefix_positions": 15,
        "author_reference_material_included": False, "new_model_calls": 0,
        "builder_sha256": digest(Path(__file__).read_bytes())}
    return files


def prepare(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.exists() or output.parent != root / "reports/local":
        raise ValueError("review package requires a fresh direct child of reports/local")
    files = build_files(root)
    output.mkdir()
    for name, value in files.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            target.write_text(value, encoding="utf-8", newline="\n")
        else:
            write_json(target, value)
    write_json(output / RESPONSE, deepcopy(files["reviewer_response.template.json"]))
    manifest = {"version": VERSION, "visibility": "evaluator_only",
                "status": "prepared_not_reviewed", "new_model_calls": 0,
                "mutable_response_file": RESPONSE, "response_assessed": False,
                "artifacts": {name: digest((output / name).read_bytes()) for name in files}}
    write_json(output / "run.json", manifest)
    return manifest


def verify(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    files = build_files(root)
    manifest = _read(output / "run.json")
    expected = {"version": VERSION, "visibility": "evaluator_only",
                "status": "prepared_not_reviewed", "new_model_calls": 0,
                "mutable_response_file": RESPONSE, "response_assessed": False,
                "artifacts": {name: digest((output / name).read_bytes()) for name in files}}
    if manifest != expected or not (output / RESPONSE).is_file():
        raise ValueError("review package manifest or response-file presence mismatch")
    for name, value in files.items():
        actual = (output / name).read_text(encoding="utf-8") if isinstance(value, str) else _read(output / name)
        if actual != value:
            raise ValueError("review package public source/template replay mismatch")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = (prepare if args.action == "prepare" else verify)(args.root, args.output)
    print(f"{VERSION}: {result['status']}; response_assessed=False; new_model_calls=0")


if __name__ == "__main__":
    main()
