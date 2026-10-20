"""M2 public-input and output contracts. No evaluator data is read here."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from .artifacts import ArtifactStore, canonical_hash, digest, safe_path
from .events import ConversationEvent, DecisionPoint, JudgmentLabels, SourceRef, StrictModel, validate_source

Text = Annotated[str, Field(min_length=1)]
SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Version = Literal["0.1-draft"]
Authorization = Literal["covered", "not_covered", "ambiguous", "not_needed"]


def parse_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("non-finite JSON value: " + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


class ArtifactRef(StrictModel):
    artifact_id: Text
    sha256: SHA


class FileArtifact(StrictModel):
    path: Text
    sha256: SHA


class GroupArtifact(StrictModel):
    members: list[Text] = Field(min_length=1)
    sha256: SHA


class Registry(StrictModel):
    schema_version: Version
    artifacts: dict[str, FileArtifact | GroupArtifact]


class Snapshot(ArtifactRef):
    files_manifest: dict[str, SHA]


class PatchRef(ArtifactRef):
    base_sha256: SHA
    format: Literal["unified_diff"]


class ToolPolicy(StrictModel):
    available_tools: list[str]
    visible_files: list[str]
    execution: Literal["not_enabled_in_m1", "disabled"]
    timeout_seconds: float | None
    per_call_limit: int | None
    total_limit: int | None


class ReviewInput(StrictModel):
    schema_version: Version
    case_id: Text
    events: list[ConversationEvent] = Field(min_length=1)
    decision_point: DecisionPoint
    snapshot: Snapshot
    candidate_patch: PatchRef
    contract_refs: list[ArtifactRef] = Field(min_length=1)
    evidence_mode: Literal["verified_impact"]
    evidence_refs: list[ArtifactRef] = Field(min_length=1)
    tool_policy: ToolPolicy

    @model_validator(mode="after")
    def validate_history(self):
        if [e.seq for e in self.events] != list(range(1, len(self.events) + 1)):
            raise ValueError("non-contiguous visible history")
        if len({e.event_id for e in self.events}) != len(self.events):
            raise ValueError("duplicate event ID")
        if (self.events[-1].event_id, self.events[-1].seq) != (self.decision_point.event_id, self.decision_point.seq):
            raise ValueError("history exceeds or misses decision point")
        if self.tool_policy.available_tools:
            raise ValueError("M2a does not enable tools")
        if self.candidate_patch.base_sha256 != self.snapshot.sha256:
            raise ValueError("patch is bound to another snapshot")
        return self


class Subject(StrictModel):
    protocol: Literal["http"]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: Text
    api_version: Text
    location: Literal["response.body"]
    field: Text

    @model_validator(mode="after")
    def version_matches(self):
        if not self.path.startswith("/") or self.api_version not in self.path.split("/"):
            raise ValueError("API path/version mismatch")
        return self


class Impact(StrictModel):
    impact_id: Text
    subject: Subject
    change_type: Literal["add_alias", "remove_field", "replace_field", "change_value_semantics"]
    before: JsonValue
    after: JsonValue
    violated_contract_refs: list[Text]
    evidence_refs: list[Text] = Field(min_length=1)


class Observation(StrictModel):
    artifact_id: Text
    kind: Text
    description: Text
    run_id: Text
    test_id: Text
    exit_code: int
    stdout_ref: str | None
    stderr_ref: str | None
    observed_response: dict[str, JsonValue]
    visibility: Literal["public_input"]


class ExecutionRef(StrictModel):
    run_id: Text
    record: Text


class ImpactEvidence(StrictModel):
    schema_version: Version
    evidence_id: Text
    mode: Literal["verified_impact"]
    snapshot_sha256: SHA
    patch_sha256: SHA
    scope: list[Subject] = Field(min_length=1)
    assessment: Literal["compatible", "breaking", "unknown"]
    impacts: list[Impact]
    observations: list[Observation] = Field(min_length=1)
    limitations: list[Text]
    execution_refs: list[ExecutionRef] = Field(min_length=1)

    @model_validator(mode="after")
    def references(self):
        ids = [o.artifact_id for o in self.observations]
        if len(set(ids)) != len(ids) or len({i.impact_id for i in self.impacts}) != len(self.impacts):
            raise ValueError("duplicate evidence ID")
        for impact in self.impacts:
            if not set(impact.evidence_refs) <= set(ids) or impact.subject not in self.scope:
                raise ValueError("unresolved impact evidence or scope")
        runs = {ref.run_id for ref in self.execution_refs}
        if any(o.run_id not in runs or o.exit_code != 0 for o in self.observations):
            raise ValueError("unverified impact observation")
        if self.assessment == "breaking" and not self.impacts:
            raise ValueError("breaking evidence needs concrete impacts")
        return self


class PublicBundle(StrictModel):
    review_input: ReviewInput
    registry: Registry
    evidence: ImpactEvidence
    contents: dict[str, str]

    @property
    def sha256(self):
        return canonical_hash(self.model_dump())


# Explicit M1 export format, not an arbitrary file browser.
PUBLIC_PATHS = {
    "app": "artifacts/base/app.py", "client": "artifacts/base/client.py",
    "patch": "artifacts/candidate.patch", "contract": "artifacts/contract.md",
    "public_checks": "artifacts/public_tests/contract.py", "impact": "artifacts/impact.json",
}


def load_bundle(directory: Path) -> PublicBundle:
    inp = ReviewInput.model_validate(parse_json(safe_path(directory, "input.json").read_text(encoding="utf-8")))
    registry = Registry.model_validate(parse_json(safe_path(directory, "artifact_manifest.json").read_text(encoding="utf-8")))
    if set(registry.artifacts) != set(PUBLIC_PATHS) | {"base"}:
        raise ValueError("unexpected public artifact IDs")
    files = {}
    for key, path in PUBLIC_PATHS.items():
        entry = registry.artifacts[key]
        if not isinstance(entry, FileArtifact) or entry.path != path:
            raise ValueError("artifact path outside public export schema")
        files[path] = entry.sha256
    if len(inp.tool_policy.visible_files) != len(files) or set(inp.tool_policy.visible_files) != set(files):
        raise ValueError("visible files disagree with public registry")
    store = ArtifactStore(directory, files)
    contents = {key: store.read(path).decode("utf-8") for key, path in PUBLIC_PATHS.items()}
    base = registry.artifacts["base"]
    if not isinstance(base, GroupArtifact) or set(base.members) != {"app", "client"} or len(base.members) != 2:
        raise ValueError("invalid snapshot group")
    expected = {key + ".py": registry.artifacts[key].sha256 for key in ("app", "client")}
    if inp.snapshot.files_manifest != expected or inp.snapshot.sha256 != canonical_hash(expected) or base.sha256 != inp.snapshot.sha256:
        raise ValueError("snapshot content/hash mismatch")
    refs = [inp.snapshot, inp.candidate_patch, *inp.contract_refs, *inp.evidence_refs]
    for ref in refs:
        if ref.artifact_id not in registry.artifacts or registry.artifacts[ref.artifact_id].sha256 != ref.sha256:
            raise ValueError("unresolved artifact reference or hash")
    if inp.snapshot.artifact_id != "base" or inp.candidate_patch.artifact_id != "patch":
        raise ValueError("incorrect snapshot/patch role")
    if [r.artifact_id for r in inp.contract_refs] != ["contract"] or {r.artifact_id for r in inp.evidence_refs} != {"public_checks", "impact"} or len(inp.evidence_refs) != 2:
        raise ValueError("incorrect contract/evidence roles")
    for event in inp.events:
        if not set(event.provenance.artifact_refs) <= set(registry.artifacts):
            raise ValueError("unresolved event artifact")
    evidence = ImpactEvidence.model_validate(parse_json(contents["impact"]))
    if (evidence.snapshot_sha256, evidence.patch_sha256) != (inp.snapshot.sha256, inp.candidate_patch.sha256):
        raise ValueError("impact evidence snapshot/patch mismatch")
    if any(not set(i.violated_contract_refs) <= {r.artifact_id for r in inp.contract_refs} for i in evidence.impacts):
        raise ValueError("unresolved violated contract")
    return PublicBundle(review_input=inp, registry=registry, evidence=evidence, contents=contents)


class DerivedItem(StrictModel):
    item_id: Text
    kind: Literal["constraint", "authorization"]
    subjects: list[Subject] = Field(min_length=1)
    predicate: Text
    change_types: list[Literal["add_alias", "remove_field", "replace_field", "change_value_semantics"]]
    conditions: list[Text]
    lifecycle: Literal["active", "superseded", "revoked"]
    interpretation: Literal["resolved", "ambiguous"]
    source_refs: list[SourceRef] = Field(min_length=1)


class ImpactAuthorization(StrictModel):
    impact_id: Text
    verdict: Authorization
    constraint_refs: list[Text]
    authorization_refs: list[Text]
    source_refs: list[SourceRef] = Field(min_length=1)
    reason: Text


class Checklist(StrictModel):
    # A temporary reconstruction, not a persistent authorization state machine.
    reconstruction: Text
    subject_check: Text
    version_check: Text
    operation_check: Text
    conditions_check: Text
    lifecycle_check: Text


class ReviewPayload(JudgmentLabels):
    affected_subjects: list[Subject]
    per_impact_authorization: list[ImpactAuthorization]
    constraint_refs: list[Text]
    authorization_refs: list[Text]
    source_refs: list[SourceRef] = Field(min_length=1)
    evidence_refs: list[Text] = Field(min_length=1)
    reason: Text
    limitations: list[Text]
    derived_items: list[DerivedItem]
    checklist: Checklist | None


class ReviewResult(ReviewPayload):
    schema_version: Version
    case_id: Text
    decision_point: DecisionPoint


def validate_output(raw: str, bundle: PublicBundle, method: str) -> ReviewResult:
    payload = ReviewPayload.model_validate(parse_json(raw))
    inp = bundle.review_input
    if method == "full_checklist" and payload.checklist is None:
        raise ValueError("checklist method must reconstruct and check scope")
    items = {item.item_id: item for item in payload.derived_items}
    if len(items) != len(payload.derived_items):
        raise ValueError("duplicate derived item ID")

    def item_refs(constraints, authorizations):
        for refs, kind in ((constraints, "constraint"), (authorizations, "authorization")):
            for ref in refs:
                if ref not in items or items[ref].kind != kind:
                    raise ValueError("unresolved or wrong-kind derived reference")

    for item in items.values():
        for ref in item.source_refs:
            validate_source(ref, inp.events, authorization=True)
    for ref in payload.source_refs:
        validate_source(ref, inp.events)
    item_refs(payload.constraint_refs, payload.authorization_refs)
    known_impacts = {i.impact_id for i in bundle.evidence.impacts}
    rows = payload.per_impact_authorization
    if any(subject not in bundle.evidence.scope for subject in payload.affected_subjects):
        raise ValueError("affected subject outside verified scope")
    if payload.impact == "breaking" and (len(payload.affected_subjects) != len(bundle.evidence.impacts) or
                                          any(impact.subject not in payload.affected_subjects for impact in bundle.evidence.impacts)):
        raise ValueError("breaking review must identify verified affected subjects")
    if len({r.impact_id for r in rows}) != len(rows):
        raise ValueError("duplicate per-impact verdict")
    for row in rows:
        if row.impact_id not in known_impacts:
            raise ValueError("unknown impact reference")
        item_refs(row.constraint_refs, row.authorization_refs)
        for ref in row.source_refs:
            validate_source(ref, inp.events, authorization=True)
        if row.verdict == "covered" and not row.authorization_refs:
            raise ValueError("covered verdict needs an explicit derived authorization")
    if payload.impact == "breaking":
        if {r.impact_id for r in rows} != known_impacts or not rows:
            raise ValueError("breaking review must account for every known impact")
        verdicts = {r.verdict for r in rows}
        combined = "not_covered" if "not_covered" in verdicts else "ambiguous" if "ambiguous" in verdicts else "covered"
        if "not_needed" in verdicts or payload.authorization != combined:
            raise ValueError("per-impact verdicts disagree with overall authorization")
    known_evidence = set(bundle.registry.artifacts) | known_impacts | {bundle.evidence.evidence_id} | {o.artifact_id for o in bundle.evidence.observations}
    if not set(payload.evidence_refs) <= known_evidence:
        raise ValueError("unknown evidence reference")
    # Valid source syntax does NOT establish semantic authorization coverage.
    return ReviewResult(schema_version="0.1-draft", case_id=inp.case_id,
                        decision_point=inp.decision_point, **payload.model_dump())
