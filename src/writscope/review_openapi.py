"""Read one OpenAPI response contract without executing or importing the service."""

from dataclasses import dataclass
import re


METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE")
CHANGE_TYPE = "remove_required_response_field"
FIELD_TYPES = {"string", "integer", "number", "boolean"}
ANNOTATIONS = {"title", "description", "example", "examples", "deprecated", "default"}
OBJECT_KEYS = {"type", "properties", "required", "additionalProperties"} | ANNOTATIONS


class UnsupportedContract(ValueError):
    """Valid input that this narrow comparator cannot fully interpret."""


def endpoint_path(value: str) -> str:
    if not value.startswith("/") or re.search(r"[\s?#\x00-\x1f]", value):
        raise ValueError("接口路径必须以 / 开头，且不能含查询、片段或空白。")
    return value


def pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _member(parent, key, pointer):
    if not isinstance(parent, dict) or key not in parent:
        raise UnsupportedContract(f"缺少合同节点 {pointer}。")
    return parent[key]


def _resolve_schema(document, schema, pointer):
    seen = set()
    while isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        match = re.fullmatch(r"#/components/schemas/((?:[^/~%]|~[01])+)", ref) if isinstance(ref, str) else None
        if set(schema) != {"$ref"} or not match:
            raise UnsupportedContract(f"{pointer} 仅支持无附加关键字的本地 components/schemas 引用。")
        if ref in seen or len(seen) >= 32:
            raise UnsupportedContract(f"{pointer} 的引用循环或链过长。")
        seen.add(ref)
        key = match[1].replace("~1", "/").replace("~0", "~")
        components = _member(document, "components", "#/components")
        schemas = _member(components, "schemas", "#/components/schemas")
        schema = _member(schemas, key, ref)
        pointer = ref
    return schema, pointer


@dataclass(frozen=True)
class ResponseSchema:
    pointer: str
    required: tuple[str, ...]
    properties: dict[str, str]
    additional_properties: bool
    string_enums: dict[str, tuple[str, ...]]

    def report(self):
        report = {
            "pointer": self.pointer,
            "required": sorted(self.required),
            "properties": dict(sorted(self.properties.items())),
            "additional_properties": self.additional_properties,
        }
        if self.string_enums:
            report["string_enums"] = {
                field: list(values) for field, values in sorted(self.string_enums.items())
            }
        return report


def response_schema(document: dict, method: str, path: str) -> ResponseSchema:
    version = document.get("openapi")
    if not isinstance(version, str) or not re.fullmatch(r"3\.[01]\.\d+", version):
        raise UnsupportedContract("仅支持 OpenAPI 3.0 或 3.1 的 JSON 文档。")
    if "jsonSchemaDialect" in document:
        raise UnsupportedContract("暂不解析自定义 JSON Schema dialect。")
    path_pointer = "#/paths/" + pointer_token(path)
    paths = _member(document, "paths", "#/paths")
    path_item = _member(paths, path, path_pointer)
    if isinstance(path_item, dict) and "$ref" in path_item:
        raise UnsupportedContract(f"{path_pointer} 的路径引用超出支持范围。")
    operation_pointer = path_pointer + "/" + method.lower()
    operation = _member(path_item, method.lower(), operation_pointer)
    responses = _member(operation, "responses", operation_pointer + "/responses")
    response_pointer = operation_pointer + "/responses/200"
    response = _member(responses, "200", response_pointer)
    if isinstance(response, dict) and "$ref" in response:
        raise UnsupportedContract(f"{response_pointer} 的响应引用超出支持范围。")
    content = _member(response, "content", response_pointer + "/content")
    media_pointer = response_pointer + "/content/application~1json"
    media = _member(content, "application/json", media_pointer)
    schema_pointer = media_pointer + "/schema"
    schema = _member(media, "schema", schema_pointer)
    schema, schema_pointer = _resolve_schema(document, schema, schema_pointer)
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise UnsupportedContract(f"{schema_pointer} 必须明确声明顶层对象。")
    if set(schema) - OBJECT_KEYS:
        raise UnsupportedContract(f"{schema_pointer} 含有当前复核未实现的约束关键字。")
    properties = schema.get("properties")
    required = schema.get("required", [])
    additional = schema.get("additionalProperties", True)
    if not isinstance(properties, dict) or type(additional) is not bool:
        raise UnsupportedContract(f"{schema_pointer} 需要 properties 和可选的布尔 additionalProperties。")
    if (not isinstance(required, list) or any(not isinstance(field, str) for field in required)
            or len(required) != len(set(required)) or not set(required) <= properties.keys()):
        raise UnsupportedContract(f"{schema_pointer} 的 required 必须唯一且全部在 properties 声明。")
    field_types = {}
    string_enums = {}
    for field, definition in properties.items():
        field_pointer = schema_pointer + "/properties/" + pointer_token(field)
        if not isinstance(definition, dict) or set(definition) - ({"type", "readOnly", "enum"} | ANNOTATIONS):
            raise UnsupportedContract(f"{field_pointer} 只支持没有额外约束的标量。")
        # A readOnly field still belongs to the response and any required guarantee still applies.
        if "readOnly" in definition and type(definition["readOnly"]) is not bool:
            raise UnsupportedContract(f"{field_pointer} 的 readOnly 必须是布尔值。")
        field_type = definition.get("type")
        if not isinstance(field_type, str) or field_type not in FIELD_TYPES:
            raise UnsupportedContract(f"{field_pointer} 的字段类型超出支持范围。")
        if "enum" in definition:
            values = definition["enum"]
            if (field_type != "string" or not isinstance(values, list) or not values
                    or any(not isinstance(value, str) for value in values) or len(values) != len(set(values))):
                raise UnsupportedContract(f"{field_pointer}/enum 仅支持字符串字段上的非空数组，且各值必须是互异的字符串。")
            string_enums[field] = tuple(sorted(values))
        field_types[field] = field_type
    return ResponseSchema(schema_pointer, tuple(required), field_types, additional, string_enums)


def compare_responses(before: ResponseSchema, after: ResponseSchema):
    """Identify lost required guarantees and declare other uncertain changes."""
    required_after = set(after.required)
    removed = set(before.required) - required_after
    impacts = [
        {
            "impact_id": "required-field:" + pointer_token(field),
            "field": field,
            "change_type": CHANGE_TYPE,
            "before_pointer": before.pointer + "/required/" + str(before.required.index(field)),
            "after_pointer": after.pointer,
            "presence_after": "optional" if field in after.properties else "undeclared",
        }
        for field in sorted(removed)
    ]
    findings = []
    shared_fields = before.properties.keys() & after.properties.keys()
    typed_changes = sorted(
        field for field in shared_fields if before.properties[field] != after.properties[field]
    )
    enum_changes = sorted(
        field for field in shared_fields if before.string_enums.get(field) != after.string_enums.get(field)
    )
    optional_removed = sorted(before.properties.keys() - after.properties.keys() - removed)
    if typed_changes:
        findings.append({"code": "field_type_changed", "detail": "存在类型变化：" + "、".join(typed_changes)})
    if enum_changes:
        findings.append({"code": "field_enum_changed", "detail": "字符串字段的允许取值发生变化：" + "、".join(enum_changes)})
    if optional_removed:
        findings.append({"code": "optional_declaration_removed", "detail": "存在可选字段声明删除：" + "、".join(optional_removed)})
    if before.additional_properties != after.additional_properties:
        findings.append({"code": "additional_properties_changed", "detail": "额外字段策略发生变化，需要单独判断。"})
    if not before.additional_properties and after.properties.keys() - before.properties.keys():
        findings.append({"code": "additions_under_closed_contract", "detail": "旧契约禁止额外字段，需评估新字段。"})
    return impacts, findings
