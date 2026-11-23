"""Regression checks for evidence scope, revocation, and offline replay."""

from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
from uuid import uuid4

import pytest

from writscope.cli import main
from writscope.review import build_review, read_ledger, reproduce_review
from writscope.review_html import render_review


PATH = "/api/v1/orders/{order_id}"
CHANGE = "remove_required_response_field"


def contract(properties, required, *, additional=True, schema_override=None):
    schema = schema_override if schema_override is not None else {
        "type": "object", "properties": {key: {"type": value} for key, value in properties.items()},
        "required": required, "additionalProperties": additional,
    }
    return json.dumps({
        "openapi": "3.1.0",
        "paths": {PATH: {"get": {"responses": {"200": {"content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/Order"},
        }}}}}}},
        "components": {"schemas": {"Order": schema}},
    }).encode()


BEFORE = contract({"status": "string", "order_id": "string"}, ["order_id", "status"])
BREAKING = contract({"state": "string", "order_id": "string"}, ["order_id", "state"])
FIXED = contract({"status": "string", "state": "string", "order_id": "string"},
                 ["order_id", "status", "state"])


def grant(before=BEFORE, after=BREAKING, **changes):
    return {
        "event_id": "grant-1", "source": "Maintainer accepted the exact field removal.",
        "operation": "grant", "method": "GET", "path": PATH, "field": "status",
        "change_type": CHANGE, "before_sha256": sha256(before).hexdigest(),
        "after_sha256": sha256(after).hexdigest(), **changes,
    }


REVOKE = {"event_id": "revoke-1", "source": "Maintainer withdrew the permission.",
          "operation": "revoke", "target_event_id": "grant-1"}


def ledger(*events):
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


def axes(report):
    return tuple(report[key] for key in ("impact", "authorization", "decision"))


def test_approval_revocation_and_regrant_only_change_the_decision():
    pending = build_review(BEFORE, BREAKING, None, "GET", PATH)
    allowed = build_review(BEFORE, BREAKING, ledger(grant()), "GET", PATH)
    revoked = build_review(BEFORE, BREAKING, ledger(grant(), REVOKE), "GET", PATH)
    regranted = build_review(BEFORE, BREAKING, ledger(grant(), REVOKE, grant(event_id="grant-2")), "GET", PATH)
    fixed = build_review(BEFORE, FIXED, ledger(grant(), REVOKE), "GET", PATH)
    assert [axes(result) for result in (pending, allowed, revoked, regranted, fixed)] == [
        ("breaking", "not_covered", "revise"), ("breaking", "covered", "allow"),
        ("breaking", "not_covered", "revise"), ("breaking", "covered", "allow"),
        ("compatible", "not_needed", "allow"),
    ]
    assert [row["change_type"] for row in allowed["impacts"]] == [CHANGE]
    assert allowed["impacts"][0]["before_pointer"] == "#/components/schemas/Order/required/1"
    assert allowed["impacts"][0]["active_grant_ids"] == ["grant-1"]
    assert revoked["impacts"][0]["revoked_grant_ids"] == ["grant-1"]
    assert revoked["ledger"][0]["revoked_by"] == "revoke-1"
    assert fixed["impacts"] == []


@pytest.mark.parametrize("overrides", [
    {"before_sha256": "a" * 64}, {"after_sha256": "a" * 64}, {"field": "state"},
    {"method": "POST"}, {"path": "/api/v2/orders/{order_id}"},
])
def test_approval_requires_every_scope_and_binding_match(overrides):
    data = build_review(BEFORE, BREAKING, ledger(grant(**overrides)), "GET", PATH)
    assert axes(data) == ("breaking", "not_covered", "revise")
    assert data["impacts"][0]["active_grant_ids"] == []


def test_unjudged_change_cannot_be_allowed_by_a_partial_grant():
    changed = contract({"state": "string", "order_id": "integer"}, ["order_id", "state"])
    data = build_review(BEFORE, changed, ledger(grant(after=changed)), "GET", PATH)
    assert axes(data) == ("unknown", "ambiguous", "investigate")
    assert data["impacts"][0]["authorization"] == "covered"
    assert data["findings"][0]["code"] == "field_type_changed"


def response_field(readonly, *, required=True):
    definition = {"type": "string", **readonly}
    return contract({}, [], schema_override={"type": "object", "properties": {"status": definition},
                                             "required": ["status"] if required else []})


@pytest.mark.parametrize("old,new", [({}, {"readOnly": True}),
                                      ({"readOnly": True}, {"readOnly": False}),
                                      ({"readOnly": True}, {})])
def test_boolean_readonly_keeps_response_only_guarantees(old, new):
    before = response_field(old)
    unchanged = build_review(before, response_field(new), None, "GET", PATH)
    assert axes(unchanged) == ("compatible", "not_needed", "allow")
    lost = build_review(before, response_field(new, required=False), None, "GET", PATH)
    assert axes(lost) == ("breaking", "not_covered", "revise")
    assert lost["impacts"][0]["field"] == "status"


def test_string_enum_order_keeps_the_same_guarantee():
    before = response_field({"enum": ["open", "", "done"]})
    after = response_field({"enum": ["done", "open", ""]})
    report = build_review(before, after, None, "GET", PATH)
    assert axes(report) == ("compatible", "not_needed", "allow")
    for side in ("before", "after"):
        assert report["schemas"][side]["string_enums"] == {"status": ["", "done", "open"]}


@pytest.mark.parametrize("old,new", [
    ({}, {"enum": ["open"]}),
    ({"enum": ["open"]}, {}),
    ({"enum": ["open"]}, {"enum": ["open", "closed"]}),
    ({"enum": ["open", "closed"]}, {"enum": ["open"]}),
])
def test_changed_enum_requires_review_even_with_a_grant_for_field_optionality(old, new):
    before = response_field(old)
    after = response_field(new, required=False)
    report = build_review(before, after, ledger(grant(before, after)), "GET", PATH)
    assert axes(report) == ("unknown", "ambiguous", "investigate")
    assert [row["field"] for row in report["impacts"]] == ["status"]
    assert report["impacts"][0]["authorization"] == "covered"
    assert [row["code"] for row in report["findings"]] == ["field_enum_changed"]


def test_unchanged_sibling_enum_preserves_a_required_field_removal():
    def version(field):
        return contract({}, [], schema_override={
            "type": "object", "required": ["role", field],
            "properties": {"role": {"type": "string", "enum": ["buyer", "seller"]},
                           field: {"type": "string"}},
        })

    report = build_review(version("status"), version("state"), None, "GET", PATH)
    assert axes(report) == ("breaking", "not_covered", "revise")
    assert [(row["field"], row["presence_after"]) for row in report["impacts"]] == [
        ("status", "undeclared")
    ]


@pytest.mark.parametrize("definition", [
    {"enum": []}, {"enum": "open"}, {"enum": ["open", "open"]}, {"enum": ["open", {}]},
    {"type": "integer", "enum": [1, 2]},
])
def test_unsupported_enums_leave_schema_unassessed(definition):
    report = build_review(response_field({}), response_field(definition), None, "GET", PATH)
    assert axes(report) == ("unknown", "not_needed", "investigate")
    assert "string_enums" not in report["schemas"]["before"]
    assert report["schemas"]["after"] is None
    assert report["findings"][0]["code"] == "unsupported_after_schema"
    assert "#/components/schemas/Order/properties/status/enum" in report["findings"][0]["detail"]


@pytest.mark.parametrize("annotations", [{"readOnly": "true"}, {"writeOnly": True},
                                          {"readOnly": True, "writeOnly": True}])
def test_unjudged_response_annotations_leave_authorization_unassessed(annotations):
    data = build_review(response_field({}), response_field(annotations), None, "GET", PATH)
    assert axes(data) == ("unknown", "not_needed", "investigate")
    assert "尚无具体影响可评估授权" in render_review(data)
    assert "授权覆盖也有待评估" in data["instructions"]


@pytest.mark.parametrize("schema", [
    {"$ref": "https://example.invalid/openapi.json"},
    {"$ref": "#/components/schemas/Order"},
    {"oneOf": [{"type": "object"}]},
])
def test_unresolved_schemas_are_investigated_without_network(schema, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("review attempted network access")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    broken = contract({}, [], schema_override=schema)
    report = build_review(BEFORE, broken, None, "GET", PATH)
    assert report["decision"] == "investigate"
    assert report["schemas"]["after"] is None


def test_schema_does_not_supply_its_own_authorization():
    injected = json.loads(BREAKING)
    injected["x-maintainer-ledger"] = [grant()]
    report = build_review(BEFORE, json.dumps(injected).encode(), None, "GET", PATH)
    assert report["ledger"] == []
    assert report["decision"] == "revise"


@pytest.mark.parametrize("bad_events", [
    ledger(REVOKE, grant()),
    ledger(grant(), grant()),
    ledger(grant(), REVOKE, {**REVOKE, "event_id": "revoke-2"}),
    b'{"event_id":"a","event_id":"b","operation":"grant"}\n',
    ledger(grant(role="user")),
])
def test_invalid_or_injected_ledger_is_rejected(bad_events):
    with pytest.raises(ValueError):
        read_ledger(bad_events)


def test_cli_reports_are_portable_deterministic_and_never_overwrite(monkeypatch):
    # Ordinary ACLs avoid pytest's Windows 0700 temporary directory issue.
    root = Path(__file__).resolve().parents[1] / ".work" / f"review-test-{uuid4().hex}"
    root.mkdir(parents=True)
    before, after, approvals = (root / name for name in ("before.json", "after.json", "approval.jsonl"))
    before.write_bytes(BEFORE)
    after.write_bytes(BREAKING)
    approvals.write_bytes(ledger(grant(source="<script>alert(1)</script>")))

    def forbidden(*args, **kwargs):
        pytest.fail("review attempted network access or ran a subprocess")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    destination = root / "original"
    argv = ["review", "--before", str(before), "--after", str(after), "--ledger", str(approvals),
            "--method", "get", "--path", PATH, "--output", str(destination)]
    assert main(argv) == 0
    record = json.loads((destination / "review.json").read_bytes())
    manifest = json.loads((destination / "manifest.json").read_bytes())
    html = (destination / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "connect-src 'none'" in html
    assert "python -m writscope.review" in html
    assert record["execution"] == {"kind": "offline_contract_review", "model_calls": 0, "candidate_code_executions": 0}
    for name, checksum in manifest["files"].items():
        assert sha256((destination / name).read_bytes()).hexdigest() == checksum
    assert main(argv) == 1
    monkeypatch.chdir(destination)
    assert main(record["reproduce"]["argv"][1:]) == 0
    for name in ("review.json", "index.html", "manifest.json"):
        assert (destination / name).read_bytes() == (destination / "reproduced-review" / name).read_bytes()

    copied, _ = reproduce_review(destination, root / "copy")
    for name in ("review.json", "index.html", "manifest.json"):
        assert (destination / name).read_bytes() == (copied / name).read_bytes()

    # Only the declared scope, fixed input files and their hashes participate in replay.
    record["reproduce"]["argv"] = []
    (destination / "review.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="review.json"):
        reproduce_review(destination, root / "bad-report")
    manifest["files"]["review.json"] = sha256((destination / "review.json").read_bytes()).hexdigest()
    (destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _, rechecked = reproduce_review(destination, root / "ignored-argv")
    assert axes(rechecked) == ("breaking", "covered", "allow")
    (destination / "inputs" / "before.json").write_bytes(FIXED)
    with pytest.raises(ValueError, match="before.json"):
        reproduce_review(destination, root / "bad-input")
