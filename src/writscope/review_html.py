"""Self-contained, escaped HTML for a deterministic offline contract review."""

from __future__ import annotations

from html import escape
import json


_LABELS = {
    "impact": "实际影响",
    "authorization": "有效授权",
    "decision": "最终决定",
    "breaking": "破坏兼容",
    "compatible": "兼容",
    "unknown": "尚不能确认",
    "covered": "授权覆盖",
    "not_covered": "未找到覆盖许可",
    "not_needed": "无需破坏许可",
    "ambiguous": "授权含糊",
    "allow": "允许",
    "revise": "需修改",
    "investigate": "需调查",
    "ask": "需澄清",
    "grant": "授予许可",
    "revoke": "撤销许可",
    "active": "有效",
    "revoked": "已撤销",
    "recorded": "已记录",
    "remove_required_response_field": "移除响应字段的必需保证",
}


def _string(value: object) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _e(value: object) -> str:
    return escape(_string(value), quote=True)


def _code(value: object) -> str:
    return f"<code>{_e(value)}</code>"


def _label(value: object) -> str:
    return _LABELS.get(_string(value), _string(value))


def _status(value: object) -> str:
    return f'<span class="status-label">{_e(_label(value))}</span>{_code(value)}'


def _tone(value: object) -> str:
    if value in {"allow", "covered", "compatible", "active", "not_needed"}:
        return "positive"
    if value in {"unknown", "ambiguous", "ask", "investigate"}:
        return "uncertain"
    if value in {"breaking", "not_covered", "revise", "revoked"}:
        return "negative"
    return "neutral"


def _dl(rows: list[tuple[str, str]]) -> str:
    return '<dl class="facts">' + "".join(
        f"<div><dt>{_e(label)}</dt><dd>{content}</dd></div>"
        for label, content in rows
    ) + "</dl>"


def _hash(value: object) -> str:
    return f'<code class="hash">{_e(value)}</code>'


def _schema(title: str, schema: dict | None) -> str:
    if schema is None:
        body = '<p class="empty">未找到可解析的响应对象契约。</p>'
    else:
        required = schema.get("required") or []
        properties = schema.get("properties") or {}
        fields = sorted(set(properties) | set(required))
        field_rows = "".join(
            f"<tr><th scope=\"row\">{_code(field)}</th>"
            f"<td>{_e(properties.get(field, '未声明'))}</td>"
            f"<td>{'必填' if field in required else '非必填'}</td></tr>"
            for field in fields
        )
        field_table = (
            '<div class="table-wrap"><table><thead><tr><th scope="col">字段</th>'
            '<th scope="col">类型</th><th scope="col">存在性保证</th></tr></thead>'
            f"<tbody>{field_rows}</tbody></table></div>"
            if fields else '<p class="empty">未声明字段。</p>'
        )
        body = (
            f'<p class="schema-pointer">{_code(schema.get("pointer"))}</p>'
            + field_table
            + '<p class="schema-extra">'
            + "额外字段规则 "
            + _code(schema.get("additional_properties"))
            + "</p>"
        )
    return f'<article class="schema-block"><h3>{_e(title)}</h3>{body}</article>'


def _impacts(report: dict) -> str:
    rows = []
    for item in report.get("impacts", []):
        refs = item.get("active_grant_ids") or []
        grant_text = "、".join(_code(ref) for ref in refs) if refs else "无匹配的有效许可"
        pointers = _dl([
            ("原始证据位置", _code(item.get("before_pointer"))),
            ("候选证据位置", _code(item.get("after_pointer"))),
            ("匹配的有效许可", grant_text),
        ])
        excluded = []
        for key, label in (
            ("revoked_grant_ids", "已撤销"),
            ("different_revision_grant_ids", "输入版本不匹配"),
        ):
            if item.get(key):
                excluded.append((label, "、".join(_code(ref) for ref in item[key])))
        if excluded:
            pointers += _dl(excluded)
        change_type = item.get("change_type")
        authorization = item.get("authorization")
        rows.append(
            '<article class="impact-item">'
            '<div class="item-heading"><div>'
            f'<span class="small-label">{_e(item.get("impact_id"))}</span>'
            f'<h3>字段 {_code(item.get("field"))}</h3></div>'
            f'<span class="badge {_tone(authorization)}">{_status(authorization)}</span></div>'
            f'<p class="change-type">{_e(_label(change_type))} {_code(change_type)}</p>'
            f"{pointers}</article>"
        )
    if not rows:
        return '<p class="empty">没有记录具体字段影响；请结合结论与检查发现判断覆盖范围。</p>'
    return '<div class="impact-list">' + "".join(rows) + "</div>"


def _binding(event: dict, inputs: dict) -> str:
    rows = []
    for key, title in (("before", "原始输入"), ("after", "候选输入")):
        bound = event.get(key + "_sha256")
        current = (inputs.get(key) or {}).get("sha256")
        if bound is None:
            rows.append((title + "绑定", "未提供绑定哈希"))
        else:
            matched = bool(current) and bound == current
            rows.append((title + "绑定", ("已匹配当前输入" if matched else "与当前输入不匹配") + _hash(bound)))
    return '<div class="binding">' + _dl(rows) + "</div>"


def _ledger(report: dict) -> str:
    events = report.get("ledger") or []
    if not events:
        return '<p class="empty">未提供授权记录。对破坏性影响，不自动推定用户已经许可。</p>'
    rows = []
    for event in events:
        operation = event.get("operation")
        status = event.get("status")
        summary = (
            '<div class="item-heading"><div>'
            f'<span class="small-label">{_e(event.get("event_id"))}</span>'
            f'<h3>{_e(_label(operation))} {_code(operation)}</h3></div>'
            f'<span class="badge {_tone(status)}">{_status(status)}</span></div>'
        )
        source = '<blockquote class="source-quote">' + _e(event.get("source")) + "</blockquote>"
        if operation == "grant":
            details = _dl([
                ("作用接口", _code(event.get("method")) + " " + _code(event.get("path"))),
                ("作用字段", _code(event.get("field"))),
                ("允许操作", _e(_label(event.get("change_type"))) + " " + _code(event.get("change_type"))),
            ]) + _binding(event, report.get("inputs") or {})
            if event.get("revoked_by"):
                details += '<p class="revoked-note">已被 ' + _code(event["revoked_by"]) + " 撤销，当前不再提供授权覆盖。</p>"
        else:
            details = _dl([("撤销目标", _code(event.get("target_event_id")))])
        rows.append(f'<li class="ledger-item">{summary}{source}{details}</li>')
    return '<ol class="ledger-list">' + "".join(rows) + "</ol>"


def _findings(report: dict) -> str:
    findings = report.get("findings") or []
    if not findings:
        return '<p class="empty">没有额外检查发现。</p>'
    return '<ul class="findings">' + "".join(
        f'<li>{_code(item.get("code"))}<p>{_e(item.get("detail"))}</p></li>'
        for item in findings
    ) + "</ul>"


def _inputs(report: dict) -> str:
    inputs = report.get("inputs") or {}
    rows = []
    for key, title in (("before", "原始契约输入"), ("after", "候选契约输入"), ("ledger", "授权记录输入")):
        item = inputs.get(key)
        if item is None:
            content = '<p class="empty">未提供</p>'
        else:
            content = f'<p>{_code(item.get("artifact"))}</p>{_hash(item.get("sha256"))}'
        rows.append(f'<div class="input-item"><h3>{_e(title)}</h3>{content}</div>')
    return "".join(rows)


def _command() -> str:
    return (
        '<p class="command-label">在安装了 WritScope 的 Python 环境中，从本报告目录运行</p>'
        '<pre class="command"><code>python -m writscope.review</code></pre>'
        '<p>若用 uv 安装且报告位于项目内，可直接运行 '
        '<code>uv run --offline --locked python -m writscope.review</code>。报告在其他目录时，'
        '请先激活项目的虚拟环境，再从报告目录运行上面的 Python 命令。</p>'
        '<p>复现会校验保存的输入，并生成新的 <code>reproduced-review</code> 目录。'
        '再次复现时请追加 <code>--output reproduced-review-2</code> 等新目录名。</p>'
    )


_CSS = """
:root {
  color-scheme: light;
  --paper: #f7f8f6;
  --white: #fff;
  --ink: #182e3b;
  --muted: #526670;
  --line: #d9e2e1;
  --green: #14634f;
  --green-bg: #e8f3ed;
  --red: #943f33;
  --red-bg: #faece8;
  --amber: #875217;
  --amber-bg: #fcf2de;
  font-family: 'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
  font-size: 15px;
  line-height: 1.7;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  color: var(--ink);
  background: var(--paper);
}
main {
  max-width: 1080px;
  margin: 0 auto;
  padding: 30px 44px 45px;
}
h1,h2,h3,p,ul,ol,blockquote,dl,dd {
  margin: 0;
}
h1,h2,h3 {
  line-height: 1.4;
}
h1 {
  font-size: 32px;
  font-weight: 650;
  letter-spacing: -1px;
}
h2 {
  font-size: 19px;
  font-weight: 650;
}
h3 {
  font-size: 14px;
  font-weight: 650;
}
code,pre {
  font-family: Consolas,'SFMono-Regular',monospace;
}
code {
  font-size: .86em;
  overflow-wrap: anywhere;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
}
.brand {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: .5px;
}
.header-note {
  font-size: 12px;
  color: var(--muted);
}
.intro {
  padding: 30px 0 22px;
}
.eyebrow,.small-label {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: .4px;
}
.intro-copy {
  max-width: 790px;
  margin-top: 11px;
  color: var(--muted);
  font-size: 13px;
}
.result {
  display: grid;
  grid-template-columns: repeat(3,minmax(0,1fr));
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  background: var(--white);
}
.axis {
  padding: 19px 21px;
  min-width: 0;
}
.axis+.axis {
  border-left: 1px solid var(--line);
}
.axis-label {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  color: var(--muted);
  font-size: 12px;
}
.axis-value {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 7px 10px;
  margin-top: 8px;
}
.axis-value strong {
  font-size: 23px;
  font-weight: 650;
  line-height: 1.4;
}
.axis-value code {
  color: var(--muted);
  font-size: 11px;
}
.decision.positive {
  background: var(--green-bg);
}
.decision.negative {
  background: var(--red-bg);
}
.decision.uncertain {
  background: var(--amber-bg);
}
.decision.positive strong {
  color: var(--green);
}
.decision.negative strong {
  color: var(--red);
}
.decision.uncertain strong {
  color: var(--amber);
}
.scope {
  margin-top: 19px;
  padding: 14px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.scope-main {
  font-size: 15px;
  font-weight: 600;
}
.scope-meta {
  font-size: 12px;
  color: var(--muted);
}
section {
  margin-top: 30px;
}
.section-heading {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 15px;
  margin-bottom: 15px;
}
.schemas {
  display: grid;
  grid-template-columns: minmax(0,1fr) minmax(0,1fr);
  gap: 22px;
}
.schema-block {
  min-width: 0;
}
.schema-block h3 {
  margin-bottom: 6px;
}
.schema-pointer {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 9px;
}
.table-wrap {
  overflow-x: auto;
}
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 12px;
  text-align: left;
}
th,td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
  overflow-wrap: anywhere;
}
thead th {
  background: #edf1ef;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
}
tbody th {
  font-weight: 500;
}
.schema-extra {
  margin-top: 9px;
  color: var(--muted);
  font-size: 11px;
}
.impact-list {
  display: grid;
  gap: 13px;
}
.impact-item,.ledger-item {
  padding: 17px 19px;
  background: var(--white);
  border: 1px solid var(--line);
  border-radius: 7px;
  min-width: 0;
}
.item-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 15px;
}
.item-heading h3 {
  margin-top: 3px;
}
.badge {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 11px;
}
.badge code {
  font-size: 10px;
}
.badge.positive {
  color: var(--green);
  background: var(--green-bg);
}
.badge.negative {
  color: var(--red);
  background: var(--red-bg);
}
.badge.uncertain {
  color: var(--amber);
  background: var(--amber-bg);
}
.badge.neutral {
  background: #edf1ef;
  color: var(--muted);
}
.change-type {
  font-size: 13px;
  margin: 12px 0 8px;
}
.change-type code {
  margin-left: 5px;
  color: var(--muted);
}
.facts {
  display: grid;
  gap: 7px;
}
.facts>div {
  display: grid;
  grid-template-columns: 140px minmax(0,1fr);
  gap: 12px;
  font-size: 12px;
}
.facts dt {
  color: var(--muted);
}
.facts dd {
  min-width: 0;
  overflow-wrap: anywhere;
}
.empty {
  font-size: 13px;
  color: var(--muted);
  padding: 13px 0;
}
.findings {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 10px;
}
.findings li {
  border-left: 2px solid var(--line);
  padding-left: 14px;
}
.findings li>code {
  display: block;
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 4px;
}
.findings p {
  font-size: 13px;
  white-space: pre-wrap;
}
.ledger-list {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 14px;
}
.source-quote {
  padding: 9px 0 9px 12px;
  border-left: 2px solid var(--line);
  margin: 13px 0;
  color: var(--ink);
  font-size: 13px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.binding {
  padding-top: 10px;
  margin-top: 10px;
  border-top: 1px solid var(--line);
}
.hash {
  display: block;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.9;
  overflow-wrap: anywhere;
  margin-top: 4px;
}
.revoked-note {
  font-size: 12px;
  margin-top: 11px;
  color: var(--red);
}
.next-step {
  padding: 16px 19px;
  background: #edf2ef;
  border-radius: 6px;
  font-size: 13px;
  white-space: pre-wrap;
}
.input-grid {
  display: grid;
  grid-template-columns: repeat(3,minmax(0,1fr));
  gap: 18px;
}
.input-item {
  min-width: 0;
}
.input-item>p {
  margin-top: 5px;
  font-size: 12px;
}
.input-item .empty {
  padding-top: 5px;
}
.command-label {
  font-size: 11px;
  color: var(--muted);
  margin: 20px 0 7px;
}
.command {
  background: #172f39;
  color: #eef4f2;
  padding: 15px 17px;
  border-radius: 5px;
  font-size: 12px;
}
.command code {
  font-size: inherit;
}
.boundary {
  margin-top: 27px;
  border-top: 1px solid var(--line);
  padding-top: 17px;
}
.boundary p {
  font-size: 12px;
  color: var(--muted);
  white-space: pre-wrap;
}
.boundary p+p {
  margin-top: 8px;
}
.footer {
  margin-top: 25px;
  font-size: 11px;
  color: var(--muted);
  display: flex;
  justify-content: space-between;
  gap: 15px;
}
details {
  margin-top: 24px;
  border-top: 1px solid var(--line);
}
summary {
  padding: 13px 0;
  cursor: pointer;
  font-size: 13px;
  color: var(--muted);
}
summary:focus-visible {
  outline: 3px solid #23798b;
  outline-offset: 3px;
}
summary::marker {
  color: var(--green);
}
@media(max-width:700px) {
  main {
    padding: 22px 21px 30px;
  }
  h1 {
    font-size: 27px;
  }
  .intro {
    padding-top: 24px;
  }
  .axis {
    padding: 15px 12px;
  }
  .axis-label {
    flex-direction: column;
    gap: 1px;
  }
  .axis-value {
    gap: 4px;
  }
  .axis-value strong {
    font-size: 20px;
  }
  .schemas {
    grid-template-columns: 1fr;
    gap: 21px;
  }
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 5px;
  }
  .input-grid {
    grid-template-columns: 1fr;
    gap: 17px;
  }
  .facts>div {
    grid-template-columns: 110px minmax(0,1fr);
  }
  .item-heading {
    gap: 10px;
  }
  .badge {
    font-size: 10px;
    max-width: 50%;
  }
  .header-note {
    font-size: 11px;
  }
  .scope-main {
    font-size: 14px;
  }
  .footer {
    flex-direction: column;
    gap: 4px;
  }
}
@media(max-width:380px) {
  main {
    padding-inline: 14px;
  }
  .axis {
    padding-inline: 9px;
  }
  .axis-value strong {
    font-size: 18px;
  }
  .axis-value code {
    font-size: 9px;
  }
  .facts>div {
    grid-template-columns: 90px minmax(0,1fr);
    gap: 8px;
  }
  .impact-item,.ledger-item {
    padding: 14px;
  }
  .badge {
    padding-inline: 5px;
  }
}
@media print {
  :root {
    --paper: #fff;
    --muted: #364750;
  }
  main {
    max-width: none;
    padding: 0;
  }
  header {
    padding-bottom: 10px;
  }
  .intro {
    padding-top: 18px;
  }
  h1 {
    font-size: 25px;
  }
  .result,.impact-item,.ledger-item,.schema-block,.input-item {
    break-inside: avoid;
  }
  .axis {
    padding: 13px 16px;
  }
  section {
    margin-top: 21px;
  }
  .command {
    background: #eef1ef;
    color: var(--ink);
    border: 1px solid var(--line);
  }
  details {
    display: block;
  }
  details>* {
    display: block;
  }
  summary {
    font-weight: 600;
  }
  .hash {
    font-size: 9px;
  }
}
"""


def render_review(report: dict) -> str:
    """Return a static HTML document; report text is always HTML-escaped."""
    scope = report.get("scope") or {}
    schemas = report.get("schemas") or {}
    decision = report.get("decision")
    labels = _LABELS
    if report.get("impact") == "unknown" and report.get("authorization") == "not_needed":
        labels = {**_LABELS, "not_needed": "尚无具体影响可评估授权"}
    axes = "".join(
        f'<div class="axis{" decision " + _tone(value) if axis == "decision" else ""}">'
        f'<div class="axis-label"><span>{_e(_LABELS[axis])}</span>{_code(axis)}</div>'
        f'<div class="axis-value"><strong>{_e(labels.get(value, value))}</strong>{_code(value)}</div></div>'
        for axis in ("impact", "authorization", "decision")
        for value in [report.get(axis)]
    )
    next_steps = {
        "revise": "修改候选契约，优先保留原有字段与已承诺的类型、存在性保证；若确需破坏性变更，应提供精确覆盖当前输入与操作的有效许可，再重新复核。",
        "allow": "本次报告允许所评估的契约字段变更。后续修改输入或授权记录后，请重新运行复核；本次允许不代表其他质量检查已经通过。",
        "investigate": "先处理检查发现中的未知或不支持项，确认目标响应契约能够解析，再运行复核。影响证据不足时，不据此推定兼容。",
        "ask": "先澄清具体接口、字段、变更操作和输入绑定的授权范围，再重新运行复核。",
    }
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        'style-src \'unsafe-inline\'; connect-src \'none\'; base-uri \'none\'; form-action \'none\'">'
        '<title>WritScope · 离线契约复核报告</title>'
        f"<style>{_CSS}</style></head><body><main>"
        '<header><span class="brand">WritScope 约界</span><span class="header-note">离线契约复核 · 无模型调用</span></header>'
        '<div class="intro"><h1>API 契约复核报告</h1>'
        '<p class="intro-copy">分别判断契约字段影响、有效许可和最终决定。授权可以允许破坏性变更，破坏事实仍然保留。</p></div>'
        f'<div class="result" aria-label="三轴结果">{axes}</div>'
        '<div class="scope"><p class="scope-main">'
        + _code(scope.get("method")) + " " + _code(scope.get("path"))
        + '</p><p class="scope-meta">响应 ' + _code(scope.get("response_status"))
        + " · " + _code(scope.get("media_type")) + "</p></div>"
        '<section aria-labelledby="impact-heading"><div class="section-heading"><h2 id="impact-heading">具体影响与授权匹配</h2></div>'
        + _impacts(report) + "</section>"
        '<section aria-labelledby="schema-heading"><div class="section-heading"><h2 id="schema-heading">原始契约与候选契约</h2></div><div class="schemas">'
        + _schema("原始契约", schemas.get("before"))
        + _schema("候选契约", schemas.get("after")) + "</div></section>"
        '<section aria-labelledby="ledger-heading"><div class="section-heading"><h2 id="ledger-heading">授权来源、输入绑定与撤销</h2></div>'
        + _ledger(report) + "</section>"
        '<section aria-labelledby="findings-heading"><div class="section-heading"><h2 id="findings-heading">检查发现与判断依据</h2></div>'
        + _findings(report) + "</section>"
        '<section aria-labelledby="next-heading"><div class="section-heading"><h2 id="next-heading">修复与下一步</h2></div>'
        f'<p class="next-step">{_e(next_steps.get(decision, "请结合检查发现补充证据后重新复核。"))}</p></section>'
        '<details open><summary>输入指纹与可复现命令</summary><div class="input-grid">'
        + _inputs(report) + "</div>" + _command() + "</details>"
        '<div class="boundary"><p>' + _e(report.get("boundary")) + "</p><p>"
        + _e(report.get("instructions")) + "</p></div>"
        '<div class="footer"><span>只覆盖本报告声明的契约字段规则。</span><span>'
        + _code(report.get("schema_version")) + "</span></div>"
        "</main></body></html>"
    )
