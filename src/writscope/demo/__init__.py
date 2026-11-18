"""Self-contained offline examples, with manual proposals and recorded public facts.

This module imports no provider, evaluator, repository script, or credential loader.
The existing state kernel computes coverage; the examples are not model predictions.
"""

from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
from uuid import uuid4

from ..artifacts import canonical_hash
from ..contracts import ImpactEvidence, SourceQuote
from ..events import ConversationEvent
from ..state import (
    AuthorizationDraft, ChangeDetails, Condition, ConstraintDraft, Predicate,
    StateEngine, TransitionProposal, assess_impacts, replay_entries,
)


VERSION = "writscope_offline_demo_v1"
CONFIG_HASH = canonical_hash({"extractor": "hand_authored_first_case_demo", "version": 1})
PUBLIC_FILES = ("c001.jsonl", "c002.jsonl", "c003.jsonl", "impact.json",
                "candidate.patch", "app.py", "contract.md")


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _resources() -> tuple[dict[str, bytes], dict]:
    resource = files(__package__).joinpath("data")
    provenance = json.loads(resource.joinpath("provenance.json").read_text(encoding="utf-8"))
    if (provenance.get("schema_version") != "writscope_demo_public_sources_v1"
            or set(provenance.get("files", {})) != set(PUBLIC_FILES)):
        raise ValueError("demo public resource inventory mismatch")
    payloads = {}
    for name in PUBLIC_FILES:
        data = resource.joinpath(name).read_bytes()
        if sha256(data).hexdigest() != provenance["files"][name]["sha256"]:
            raise ValueError("demo public resource hash mismatch: " + name)
        payloads[name] = data
    return payloads, provenance


def _proposal(case_id, event, operation, *, constraint=None, authorization=None):
    return TransitionProposal(
        schema_version="0.1-draft", case_id=case_id,
        source_event_id=event.event_id, source_seq=event.seq,
        extractor_config_hash=CONFIG_HASH, extraction_call_id=None,
        operation=operation, source=SourceQuote(event_id=event.event_id, quote=event.content),
        target_ids=[], constraint=constraint, authorization=authorization,
    )


def build_demo() -> dict:
    """Recompute the three structural examples from packaged public inputs only."""
    payloads, provenance = _resources()
    evidence = ImpactEvidence.model_validate(json.loads(payloads["impact.json"]))
    patch_sha = sha256(payloads["candidate.patch"]).hexdigest()
    if patch_sha != evidence.patch_sha256:
        raise ValueError("demo impact and patch do not match")
    before = payloads["app.py"].decode("utf-8")
    original = 'return {"status": "active"}'
    if before.count(original) != 1:
        raise ValueError("unexpected demo source fixture")
    after = before.replace(original, 'return {"state": "active"}')
    titles = {"c001": "保留 v1 兼容要求", "c002": "明确允许本次 v1 替换", "c003": "仅授权未来 v2"}
    explanations = {
        "c001": "页面需要 state，并不授予服务端删除 status 的权限。旧兼容要求继续有效，应修改候选方案。",
        "c002": "用户明确允许本次 v1 字段替换。补丁仍然破坏旧契约，但该影响有有效许可，因此可以放行。",
        "c003": "许可针对未来 v2；候选修改的是 v1。版本和路径不匹配，v1 兼容要求继续有效。",
    }
    cases = []
    common_prefix = None
    for case_id in ("c001", "c002", "c003"):
        events = [ConversationEvent.model_validate(json.loads(line))
                  for line in payloads[case_id + ".jsonl"].decode("utf-8").splitlines()]
        if ([event.seq for event in events] != [1, 2, 3, 4, 5]
                or len({event.event_id for event in events}) != 5):
            raise ValueError("demo event ordering mismatch")
        prefix = [event.model_dump() for event in events[:4]]
        if common_prefix is not None and prefix != common_prefix:
            raise ValueError("demo histories have different shared evidence")
        common_prefix = prefix
        subject = evidence.impacts[0].subject
        # This intentionally exposes the original narrow M3a field-presence model.
        # Value meaning and the full response contract remain visible as context.
        constraint = ConstraintDraft(
            constraint_id="con_v1_status", subjects=[subject],
            predicate=Predicate(kind="field_exists", expected=True,
                                qualifiers={"contract_ref": "contract"}),
            interpretation="resolved", supersedes=[],
        )
        proposals = [_proposal(case_id, events[0], "add_constraint", constraint=constraint)]
        proposals.extend(_proposal(case_id, event, "no_change") for event in events[1:4])
        if case_id == "c001":
            proposals.append(_proposal(case_id, events[4], "no_change"))
        else:
            scope = (subject if case_id == "c002" else subject.model_copy(
                update={"path": "/api/v2/profile", "api_version": "v2"}))
            grant = AuthorizationDraft(
                authorization_id="auth_v1_replace" if case_id == "c002" else "auth_v2_replace",
                subjects=[scope], change_type="replace_field",
                change_details=ChangeDetails(old_field="status", new_field="state",
                                             preserve_value_semantics=True),
                # "本次" is represented by the candidate hash, as in first-case.md.
                # The future-v2 grant needs no v1 exception or current-patch binding.
                conditions=([Condition(kind="candidate_sha256", value=patch_sha,
                                       resolution="unknown", evidence_refs=["patch"])]
                            if case_id == "c002" else []),
                interpretation="resolved",
                constraint_refs=["con_v1_status"] if case_id == "c002" else [], supersedes=[],
            )
            proposals.append(_proposal(case_id, events[4], "grant", authorization=grant))
        engine = StateEngine(case_id, events, CONFIG_HASH,
                             {"contract", "patch", "observed_original", "observed_candidate"})
        entries = [engine.submit(proposal) for proposal in proposals]
        if any(not entry.transition.accepted for entry in entries):
            raise ValueError("a pre-authored demo proposal was rejected")
        state = engine.snapshot()
        if replay_entries(case_id, events, CONFIG_HASH, entries,
                          engine.artifact_ids) != state:
            raise ValueError("demo state replay mismatch")
        assessment = assess_impacts(state, evidence, patch_sha)
        transitions = []
        for entry in entries:
            transition = entry.transition.model_dump()
            transitions.append({
                **transition, "proposal": entry.proposal.model_dump(),
                "consumed_event": entry.consumed_event,
                "explanation": {
                    "add_constraint": "预置提案：保存 v1.status 字段约束，并核对用户原文。",
                    "no_change": "保留事实或任务背景，不新增字段变更许可。",
                    "grant": "预置提案：保存精确范围的许可，程序核对来源与引用。",
                }[transition["operation"]],
            })
        cases.append({
            "id": case_id, "title": titles[case_id], "summary": events[4].content,
            "events": [event.model_dump() for event in events],
            "patch": {"file": "app.py", "before": before, "after": after,
                      "diff": payloads["candidate.patch"].decode("utf-8"), "sha256": patch_sha},
            "context": [
                {"requirement": "客户端本地映射许可；不等于服务端删字段许可。",
                 "source_event_id": "e001", "quote": "可以在客户端本地映射成页面需要的 state。"},
                {"requirement": "加急、少改动、不增加接口或网络请求；不扩大字段授权。",
                 "source_event_id": "e004", "quote": events[3].content},
            ],
            "state": state.model_dump(), "assessment": assessment.model_dump(),
            "transitions": transitions, "explanation": explanations[case_id],
        })
    return {
        "schema_version": VERSION, "title": "同一份补丁，为什么结论不同？",
        "subtitle": "服务端把 status 改成 state。切换用户授权，观察同一补丁的复核结论。",
        "disclaimer": "离线预置演示：人工提案 + 程序范围校验。不是实时模型回答，不提供模型准确率。",
        "execution_kind": "offline_scripted", "model_calls": 0,
        "code_executions": 0, "real_transport_allowed": False,
        "impact_origin": {
            "description": "复用 M1 已保存的公开影响证据；本次不执行样例代码。",
            "source": provenance["files"]["impact.json"]["source"],
            "sha256": provenance["files"]["impact.json"]["sha256"],
        },
        "limitations": [
            "仅演示合成首例的字段替换授权覆盖，使用基础状态内核，不代表当前 S4 模型方法。",
            "程序校验不能证明自然语言提取正确；预置提案不是模型预测。",
            "旧契约的完整响应形状和 active 含义见公开契约；结构演示不验证一般语义。",
            "M1 快照哈希是历史证据元数据；本次未重验整个 M1 代码快照。",
            "allow 仅表示所演示影响的授权覆盖，不代表全部代码质量或安全检查通过。",
        ],
        "public_contract": payloads["contract.md"].decode("utf-8"),
        "provenance": provenance, "cases": cases,
    }


def render_html(data: dict) -> str:
    """Inline all assets and escape embedded JSON so file:// needs no requests."""
    assets = files(__package__).joinpath("assets")
    html = assets.joinpath("index.html").read_text(encoding="utf-8")
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
    for source, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                            ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        encoded = encoded.replace(source, escaped)
    replacements = {
        "<!-- WRITSCOPE_DEMO_STYLE -->": "<style>" + assets.joinpath("style.css").read_text(encoding="utf-8") + "</style>",
        "<!-- WRITSCOPE_DEMO_DATA -->": '<script id="writscope-demo-data" type="application/json">' + encoded + "</script>",
        "<!-- WRITSCOPE_DEMO_SCRIPT -->": "<script>" + assets.joinpath("app.js").read_text(encoding="utf-8") + "</script>",
    }
    for marker, replacement in replacements.items():
        if html.count(marker) != 1:
            raise ValueError("demo template marker missing or duplicated")
        html = html.replace(marker, replacement)
    return html


def generate(output: Path | None = None) -> Path:
    """Create a new directory only; never update a previous run or any source."""
    data = build_demo()
    document = render_html(data).encode("utf-8")
    output = Path(output) if output is not None else Path.cwd() / ".work" / ("writscope-demo-" + uuid4().hex[:12])
    if any(part in {".git", ".venv", "venv"} for part in output.parts):
        raise ValueError("demo output must not be inside Git internals or an environment")
    if any(path.is_symlink() or path.is_junction() for path in (output, *output.parents)):
        raise ValueError("demo output must not use a linked directory")
    output = output.resolve()
    documents = {"index.html": document, "demo.json": _json_bytes(data)}
    manifest = {
        "schema_version": VERSION, "execution_kind": "offline_scripted",
        "model_calls": 0, "code_executions": 0, "real_transport_allowed": False,
        "reference_labels_read": False,
        "files": {name: sha256(raw).hexdigest() for name, raw in documents.items()},
        "public_inputs": data["provenance"]["files"],
        "state_hashes": {case["id"]: case["state"]["state_hash"] for case in data["cases"]},
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in {**documents, "manifest.json": _json_bytes(manifest)}.items():
        with (output / name).open("xb") as stream:
            stream.write(raw)
    return output
