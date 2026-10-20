"""Offline checks for the versioned model-facing source-quote contract."""

import json

import pytest

from writscope.baselines import build_request
from writscope.contracts import load_bundle, validate_output
from writscope.providers import ModelConfig, ModelRequest


def _without_offsets(value):
    if isinstance(value, dict):
        return {key: _without_offsets(item) for key, item in value.items()
                if key not in {"start", "end"}}
    if isinstance(value, list):
        return [_without_offsets(item) for item in value]
    return value


@pytest.fixture
def bundle(root):
    return load_bundle(root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact" / "c001")


@pytest.mark.parametrize("method,filename", [
    ("full_direct", "direct_response.json"),
    ("full_checklist", "checklist_response.json"),
])
def test_quote_only_resolves_unique_spans_and_legacy_still_works(root, bundle, method, filename):
    old = json.loads((root / "examples" / "m2a" / filename).read_text(encoding="utf-8"))
    quote_only = _without_offsets(old)
    result = validate_output(json.dumps(quote_only, ensure_ascii=False), bundle, method,
                             output_contract="source_quote_v1")
    legacy = validate_output(json.dumps(old, ensure_ascii=False), bundle, method)
    assert result == legacy
    events = {event.event_id: event for event in bundle.review_input.events}
    for ref in result.source_refs:
        assert events[ref.event_id].content[ref.start:ref.end] == ref.quote


def test_new_request_advertises_quote_only_schema_and_equal_evidence(root, bundle):
    requests = []
    for method in ("full_direct", "full_checklist"):
        prompt = (root / "prompts" / f"{method}.md").read_text(encoding="utf-8")
        requests.append(build_request(bundle, method, prompt, ModelConfig()))
    assert all(request.output_contract == "source_quote_v1" for request in requests)
    assert requests[0].public_input == requests[1].public_input
    assert requests[0].evidence_sha256 == requests[1].evidence_sha256
    for request in requests:
        schema = request.output_schema
        assert set(schema["$defs"]["SourceQuote"]["properties"]) == {"event_id", "quote"}
        assert "SourceRef" not in schema["$defs"]
    historical = requests[0].model_dump()
    del historical["output_contract"]
    assert ModelRequest.model_validate(historical).output_contract == "source_span_v0"


def test_bad_quote_and_duplicate_quote_are_rejected(root, bundle):
    old = json.loads((root / "examples" / "m2a" / "direct_response.json").read_text(encoding="utf-8"))
    draft = _without_offsets(old)
    draft["source_refs"][0]["quote"] = "not in this event"
    with pytest.raises(ValueError, match="source quote mismatch"):
        validate_output(json.dumps(draft, ensure_ascii=False), bundle, "full_direct")
    draft = _without_offsets(old)
    draft["source_refs"][0] = {"event_id": "e001", "quote": "v1"}
    assert bundle.review_input.events[0].content.count("v1") > 1
    with pytest.raises(ValueError, match="not unique"):
        validate_output(json.dumps(draft, ensure_ascii=False), bundle, "full_direct")


def test_authorization_role_and_decision_cutoff_are_enforced(root, bundle):
    old = json.loads((root / "examples" / "m2a" / "direct_response.json").read_text(encoding="utf-8"))
    draft = _without_offsets(old)
    event = next(e for e in bundle.review_input.events if e.event_id == "e002")
    draft["derived_items"][0]["source_refs"][0] = {"event_id": event.event_id, "quote": event.content}
    with pytest.raises(ValueError, match="only a user source"):
        validate_output(json.dumps(draft, ensure_ascii=False), bundle, "full_direct")
    draft = _without_offsets(old)
    draft["source_refs"][0]["event_id"] = "e006"
    with pytest.raises(ValueError, match="outside visible history"):
        validate_output(json.dumps(draft, ensure_ascii=False), bundle, "full_direct")


def test_mixed_or_wrong_legacy_offsets_are_not_silently_repaired(root, bundle):
    old = json.loads((root / "examples" / "m2a" / "direct_response.json").read_text(encoding="utf-8"))
    mixed = _without_offsets(old)
    mixed["source_refs"][0]["start"] = old["source_refs"][0]["start"]
    with pytest.raises(ValueError):
        validate_output(json.dumps(mixed, ensure_ascii=False), bundle, "full_direct")
    wrong = old
    wrong["source_refs"][0]["start"] += 1
    with pytest.raises(ValueError, match="source quote mismatch"):
        validate_output(json.dumps(wrong, ensure_ascii=False), bundle, "full_direct")
    with pytest.raises(ValueError, match="forbids model-supplied offsets"):
        validate_output(json.dumps(old, ensure_ascii=False), bundle, "full_direct",
                        output_contract="source_quote_v1")
