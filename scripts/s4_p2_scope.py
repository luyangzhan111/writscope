"""S4 value-qualified adapter around the historical atomic state engine.

Only provider-proposed structure is consumed. Source, lifecycle, exception and
condition checks remain in the existing engine; this module cannot establish
that a natural-language extraction is semantically correct.
"""

import json

from pydantic import Field

from scripts.m3j_transition_batch import BatchStateEngine, batch_from_response
from scripts.s4_value_scope import ExactValueScope, match_exact_value_scope
from writscope.artifacts import canonical_hash
from writscope.contracts import ImpactEvidence, parse_json
from writscope.events import StrictModel
from writscope.m3b import ExtractionPayload
from writscope.state import assess_impacts


class QualifiedItem(ExtractionPayload):
    value_scope: ExactValueScope | None


class QualifiedBatch(StrictModel):
    items: list[QualifiedItem] = Field(min_length=1, max_length=8)


class QualifiedEngine:
    def __init__(self, bundle, config_hash):
        self.bundle = bundle
        self.config_hash = config_hash
        self.engine = BatchStateEngine(bundle.review_input.case_id, bundle.review_input.events,
                                       config_hash, set(bundle.registry.artifacts))
        self.value_scopes = {}
        self.records = []

    def snapshot(self):
        state = self.engine.snapshot().model_dump(mode="json")
        scopes = {key: value for key, value in sorted(self.value_scopes.items())}
        return {"policy": "s4_value_scope_v1", "state": state, "value_scopes": scopes,
                "qualified_state_sha256": canonical_hash({"state": state, "value_scopes": scopes})}

    def submit(self, raw, index, call_id):
        draft = QualifiedBatch.model_validate(parse_json(raw))
        event = self.bundle.review_input.events[index]
        pending = {}
        for item in draft.items:
            scope, auth = item.value_scope, item.authorization
            if scope is None:
                continue
            if (item.operation not in {"grant", "supersede"} or auth is None
                    or auth.change_type != "change_value_semantics"
                    or len(auth.subjects) != 1 or auth.subjects[0] != scope.subject
                    or scope.subject.field != auth.change_details.old_field):
                raise ValueError("value scope does not bind exactly to this semantic authorization")
            # These are only grounding checks, not semantic verification.
            if scope.old_value not in item.source_quote or scope.new_value not in item.source_quote:
                raise ValueError("value scope is not grounded in its current-event quote")
            if auth.authorization_id in pending or auth.authorization_id in self.value_scopes:
                raise ValueError("duplicate value scope authorization ID")
            pending[auth.authorization_id] = {"scope": scope.model_dump(mode="json"),
                                              "source_event_id": event.event_id,
                                              "source_quote": item.source_quote}
        plain = {"items": [item.model_dump(exclude={"value_scope"}, mode="json") for item in draft.items]}
        batch = batch_from_response(json.dumps(plain, ensure_ascii=False), self.bundle, index,
                                    self.config_hash, call_id)
        record = self.engine.submit_batch(batch)
        if record.accepted:
            self.value_scopes.update(pending)
        result = {"batch": record.model_dump(mode="json"), "accepted_value_scopes": pending if record.accepted else {},
                  "qualified_state_sha256": self.snapshot()["qualified_state_sha256"]}
        self.records.append(result)
        return result

    def assess(self):
        state = self.engine.snapshot()
        evidence = self.bundle.evidence
        rows = []
        for impact in evidence.impacts:
            # Examine each grant separately: a mismatching early grant must not
            # conceal a later matching grant. The old engine handles conditions,
            # local exceptions, trusted citations, ambiguity and lifecycle.
            per_evidence = evidence.model_copy(update={"impacts": [impact]})
            verdicts = []
            for authorization in [None, *state.authorizations]:
                auths = [] if authorization is None else [authorization]
                ids = {a.authorization_id for a in auths}
                exceptions = [e for e in state.exceptions if e.authorization_id in ids]
                data = state.model_dump(exclude={"state_id", "state_hash"})
                data["authorizations"] = [a.model_dump(mode="json") for a in auths]
                data["exceptions"] = [e.model_dump(mode="json") for e in exceptions]
                state_hash = canonical_hash(data)
                isolated = state.model_validate({**data, "state_id": "state_" + state_hash[:16],
                                                 "state_hash": state_hash})
                row = assess_impacts(isolated, per_evidence, evidence.patch_sha256).per_impact[0]
                value_verdict = None
                if authorization is not None and row.verdict in {"covered", "ambiguous"} and impact.change_type == "change_value_semantics":
                    stored = self.value_scopes.get(authorization.authorization_id)
                    field = impact.subject.field
                    before = impact.before.get(field) if isinstance(impact.before, dict) else None
                    after = impact.after.get(field) if isinstance(impact.after, dict) else None
                    observed = {"subject": impact.subject.model_dump(), "change_type": impact.change_type,
                                "old_value": before, "new_value": after}
                    value_verdict = match_exact_value_scope(stored["scope"] if stored else None, observed)
                    if value_verdict == "not_covered":
                        row = row.model_copy(update={"verdict": "not_covered", "reason": "exact_value_scope_mismatch"})
                    elif value_verdict == "ambiguous":
                        row = row.model_copy(update={"verdict": "ambiguous", "reason": "exact_value_scope_unknown"})
                verdicts.append((row, value_verdict))
            chosen = next(((r, v) for r, v in verdicts if r.verdict == "covered"), None)
            chosen = chosen or next(((r, v) for r, v in verdicts if r.verdict == "ambiguous"), None)
            chosen = chosen or verdicts[0]
            rows.append({**chosen[0].model_dump(mode="json"), "exact_value_verdict": chosen[1]})
        if evidence.assessment == "compatible":
            auth, decision = "not_needed", "allow"
        elif evidence.assessment == "unknown":
            auth, decision = ("ambiguous" if rows else "not_needed"), "investigate"
        else:
            if not rows:
                raise ValueError("breaking evidence has no impacts")
            verdicts = {r["verdict"] for r in rows}
            auth = "not_covered" if "not_covered" in verdicts else "ambiguous" if "ambiguous" in verdicts else "covered"
            decision = {"not_covered": "revise", "ambiguous": "ask", "covered": "allow"}[auth]
        return {"impact": evidence.assessment, "authorization": auth, "decision": decision,
                "per_impact": rows, "qualified_state_sha256": self.snapshot()["qualified_state_sha256"],
                "limitation": "structural_only_extraction_semantics_unverified"}
