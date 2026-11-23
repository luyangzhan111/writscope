"""Offline review of one OpenAPI response and an operator-selected ledger."""

from pathlib import Path
import json
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator

from .artifacts import digest
from .contracts import SHA, parse_json
from .events import JudgmentLabels, StrictModel
from .review_openapi import (
    CHANGE_TYPE, METHODS, UnsupportedContract, compare_responses, endpoint_path,
    response_schema,
)


VERSION = "writscope_review_v1"
OPENAPI_LIMIT = 4 * 1024 * 1024
LEDGER_LIMIT = 1024 * 1024
EventId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]
Text = Annotated[str, Field(min_length=1, max_length=2000)]


class LedgerEvent(StrictModel):
    event_id: EventId
    source: Text

    @field_validator("source")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("source must include the operator's decision")
        return value


class Grant(LedgerEvent):
    operation: Literal["grant"]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"]
    path: Text
    field: str
    change_type: Literal["remove_required_response_field"]
    before_sha256: SHA
    after_sha256: SHA

    @field_validator("path")
    @classmethod
    def path_is_endpoint(cls, value):
        return endpoint_path(value)


class Revoke(LedgerEvent):
    operation: Literal["revoke"]
    target_event_id: EventId


def _local_file(path: Path, max_bytes: int) -> bytes:
    if str(path).startswith(("\\\\", "//")) or not path.is_file():
        raise ValueError("输入必须是本地普通文件。")
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"输入超过 {max_bytes} 字节上限。")
    return data


def _object(data: bytes, name: str) -> dict:
    try:
        value = parse_json(data.decode("utf-8-sig"))
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"{name} 不是有效的 UTF-8 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是 JSON 对象。")
    return value


def read_ledger(data: bytes) -> list[dict]:
    """Replay a single local maintainer's ordered grant and revoke records."""
    try:
        lines = data.decode("utf-8-sig").split("\n")
    except UnicodeError as exc:
        raise ValueError("授权台账必须使用 UTF-8。") from exc
    history = {}
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        row = _object(line.encode("utf-8"), f"台账第 {line_number} 行")
        try:
            operation = row.get("operation")
            if operation == "grant":
                event = Grant.model_validate(row)
            elif operation == "revoke":
                event = Revoke.model_validate(row)
            else:
                raise ValueError("未知台账操作。")
        except ValidationError as exc:
            raise ValueError(f"台账第 {line_number} 行的字段不符合契约。") from exc
        if event.event_id in history:
            raise ValueError(f"台账第 {line_number} 行的事件 ID 重复。")
        if isinstance(event, Revoke):
            prior = history.get(event.target_event_id)
            if prior is None or prior["status"] != "active":
                raise ValueError(f"台账第 {line_number} 行必须撤销先前仍有效的一条授权。")
            prior.update(status="revoked", revoked_by=event.event_id)
        history[event.event_id] = {
            **event.model_dump(), "line": line_number,
            "status": "active" if isinstance(event, Grant) else "recorded",
        }
    return list(history.values())


def _cover_impact(row, ledger, method, path, before_sha, after_sha):
    scope = (method, path, row["field"], CHANGE_TYPE)
    related = [
        grant for grant in ledger if
        (grant.get("method"), grant.get("path"), grant.get("field"), grant.get("change_type")) == scope
    ]
    active = [
        grant["event_id"] for grant in related if grant["status"] == "active"
        and (grant["before_sha256"], grant["after_sha256"]) == (before_sha, after_sha)
    ]
    return {
        **row,
        "authorization": "covered" if active else "not_covered",
        "active_grant_ids": active,
        "revoked_grant_ids": [grant["event_id"] for grant in related if grant["status"] == "revoked"],
        "different_revision_grant_ids": [
            grant["event_id"] for grant in related
            if grant["status"] == "active"
            and (grant["before_sha256"], grant["after_sha256"]) != (before_sha, after_sha)
        ],
    }


def build_review(before: bytes, after: bytes, ledger: bytes | None, method: str, path: str) -> dict:
    if method not in METHODS:
        raise ValueError("不支持的 HTTP 方法。")
    endpoint_path(path)
    documents = {"before": _object(before, "变更前契约"), "after": _object(after, "变更后契约")}
    before_sha, after_sha = digest(before), digest(after)
    history = read_ledger(ledger) if ledger is not None else []
    findings, schemas = [], {}
    for name, document in documents.items():
        try:
            schemas[name] = response_schema(document, method, path)
        except UnsupportedContract as exc:
            schemas[name] = None
            findings.append({"code": f"unsupported_{name}_schema", "detail": str(exc)})
    impacts = []
    if schemas["before"] is not None and schemas["after"] is not None:
        rows, outside_scope = compare_responses(schemas["before"], schemas["after"])
        findings.extend(outside_scope)
        impacts = [_cover_impact(row, history, method, path, before_sha, after_sha) for row in rows]
    if findings:
        labels = JudgmentLabels(impact="unknown", authorization="ambiguous" if impacts else "not_needed",
                                decision="investigate")
        explanation = ("存在未判定的契约内容。请逐项核对已识别影响与证据缺口。" if impacts else
                       "尚无可判断的具体影响，授权覆盖也有待评估。请核对报告中的证据缺口。")
    elif not impacts:
        labels = JudgmentLabels(impact="compatible", authorization="not_needed", decision="allow")
        explanation = "当前检查范围内，旧响应必需字段仍有保证，且未发现需要单独复核的结构变化。"
    elif all(row["authorization"] == "covered" for row in impacts):
        labels = JudgmentLabels(impact="breaking", authorization="covered", decision="allow")
        explanation = "旧响应保证发生破坏，每项已识别的影响均有匹配本次输入且仍有效的许可。"
    else:
        labels = JudgmentLabels(impact="breaking", authorization="not_covered", decision="revise")
        explanation = "至少一个旧响应必需字段失去保证，提供的材料中没有覆盖该影响的有效许可。"
    argv = [
        "writscope", "review", "--before", "inputs/before.json", "--after", "inputs/after.json",
        "--method", method, "--path", path,
    ]
    if ledger is not None:
        argv.extend(["--ledger", "inputs/ledger.jsonl"])
    argv.extend(["--output", "reproduced-review"])
    return {
        "schema_version": VERSION, **labels.model_dump(),
        "scope": {"method": method, "path": path, "response_status": "200", "media_type": "application/json"},
        "inputs": {
            "before": {"artifact": "inputs/before.json", "sha256": before_sha},
            "after": {"artifact": "inputs/after.json", "sha256": after_sha},
            "ledger": {"artifact": "inputs/ledger.jsonl", "sha256": digest(ledger)} if ledger is not None else None,
        },
        "schemas": {key: schema.report() if schema is not None else None for key, schema in schemas.items()},
        "impacts": impacts, "findings": findings, "ledger": history,
        "reproduce": {"argv": argv},
        "boundary": "只检查指定 HTTP 200 JSON 响应的简单 OpenAPI 契约。字段失去必需声明不代表推断出改名或运行时行为。"
                    "调用者须独立选择可信维护者台账。工具按原文和顺序回放，未验证人员身份。",
        "instructions": explanation,
        "execution": {"kind": "offline_contract_review", "model_calls": 0, "candidate_code_executions": 0},
    }


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def generate_review(before: Path, after: Path, ledger: Path | None, method: str, path: str,
                    output: Path | None = None) -> tuple[Path, dict]:
    before_bytes = _local_file(before, OPENAPI_LIMIT)
    after_bytes = _local_file(after, OPENAPI_LIMIT)
    ledger_bytes = _local_file(ledger, LEDGER_LIMIT) if ledger is not None else None
    return _save_review(before_bytes, after_bytes, ledger_bytes, method, path, output)


def _save_review(before_bytes: bytes, after_bytes: bytes, ledger_bytes: bytes | None,
                 method: str, path: str, output: Path | None) -> tuple[Path, dict]:
    from .review_html import render_review

    report = build_review(before_bytes, after_bytes, ledger_bytes, method, path)
    outputs = {
        "inputs/before.json": before_bytes, "inputs/after.json": after_bytes,
        "review.json": _json_bytes(report), "index.html": render_review(report).encode("utf-8"),
    }
    if ledger_bytes is not None:
        outputs["inputs/ledger.jsonl"] = ledger_bytes
    manifest = _json_bytes({
        "schema_version": VERSION, "execution": report["execution"],
        "files": {name: digest(data) for name, data in sorted(outputs.items())},
    })
    destination = output if output is not None else Path.cwd() / ".work" / f"writscope-review-{uuid4().hex[:12]}"
    if str(destination).startswith(("\\\\", "//")):
        raise ValueError("输出应使用本地目录。")
    if any(part.lower() in {".git", ".venv", "venv"} for part in destination.resolve().parts):
        raise ValueError("输出目录不能位于 Git 或虚拟环境内部。")
    if any(item.is_symlink() or item.is_junction() for item in (destination, *destination.parents)):
        raise ValueError("输出目录不能经过链接。")
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "inputs").mkdir()
    # Write the manifest last. Its presence marks a complete new report.
    for name, data in {**outputs, "manifest.json": manifest}.items():
        with (destination / name).open("xb") as stream:
            stream.write(data)
    return destination, report


def reproduce_review(directory: Path, output: Path) -> tuple[Path, dict]:
    """Recompute from saved bytes, using only the report's fixed local input names."""
    manifest_file = directory / "manifest.json"
    report_file = directory / "review.json"
    if not manifest_file.is_file() or not report_file.is_file():
        raise ValueError("请在包含 review.json 和 manifest.json 的完整报告目录中运行。")
    manifest = _object(manifest_file.read_bytes(), "manifest.json")
    hashes = manifest.get("files")
    report_bytes = report_file.read_bytes()
    if (manifest.get("schema_version") != VERSION or not isinstance(hashes, dict)
            or digest(report_bytes) != hashes.get("review.json")):
        raise ValueError("报告版本或 review.json 的清单哈希不匹配。")
    report = _object(report_bytes, "review.json")
    inputs = report.get("inputs")
    scope = report.get("scope")
    if (report.get("schema_version") != VERSION or not isinstance(inputs, dict)
            or set(inputs) != {"before", "after", "ledger"} or not isinstance(scope, dict)
            or not isinstance(scope.get("method"), str) or not isinstance(scope.get("path"), str)
            or scope.get("response_status") != "200" or scope.get("media_type") != "application/json"):
        raise ValueError("报告的输入描述或复核范围无效。")

    data = {}
    for key, name, limit in (("before", "inputs/before.json", OPENAPI_LIMIT),
                             ("after", "inputs/after.json", OPENAPI_LIMIT),
                             ("ledger", "inputs/ledger.jsonl", LEDGER_LIMIT)):
        record = inputs[key]
        if key == "ledger" and record is None and name not in hashes:
            data[key] = None
            continue
        if not isinstance(record, dict) or record.get("artifact") != name:
            raise ValueError(f"报告中的 {key} 输入路径无效。")
        raw = _local_file(directory / name, limit)
        if digest(raw) != record.get("sha256") or record["sha256"] != hashes.get(name):
            raise ValueError(f"{name} 的输入哈希不匹配。")
        data[key] = raw
    return _save_review(data["before"], data["after"], data["ledger"], scope["method"], scope["path"], output)


def _replay_main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="从当前目录重算已保存的 WritScope 契约复核。")
    parser.add_argument("--output", type=Path, default=Path("reproduced-review"), help="新的输出目录")
    args = parser.parse_args()
    try:
        destination, report = reproduce_review(Path.cwd(), args.output)
    except FileExistsError:
        print("输出目录已存在。请用 --output 指定一个新目录。", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"WritScope 复现失败：{error}", file=sys.stderr)
        return 1
    print(f'{report["impact"]} / {report["authorization"]} / {report["decision"]}')
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(_replay_main())
