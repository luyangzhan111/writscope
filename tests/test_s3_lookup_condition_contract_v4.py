"""Offline replay and structural controls for the v4 lookup continuation contract."""
import asyncio
import json
from pathlib import Path
import re

import pytest

from scripts import s3_acceptance_v5 as frozen
from scripts import s3_interaction_v4 as interaction
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from scripts.m3i_task_context import ContextSnapshot
from writscope.state import AuthorizationState, assess_impacts

ROOT = Path(__file__).resolve().parents[1]
PROMPT_V7 = (ROOT / "prompts/incremental_interaction_v7.md").read_text(encoding="utf-8")


def _real_calls():
    return frozen.read(ROOT / "reports/local/s3-development-v5-live-20261026/calls.json")


def test_saved_real_continuation_is_rejected_without_rewriting_response():
    bundle = frozen.bundle(ROOT, "c004")
    row = _real_calls()[-1]
    original = row["response_text"]
    assert "candidate_patch_sha256" in original and '"e002"' in original
    with pytest.raises(ValueError, match="unsupported condition kind"):
        interaction.parse_response(original, bundle, 4, "a"*64, row["call_id"], frozen.POLICY,
                                   lookup_attempts_used=1)
    assert row["response_text"] == original
    assert row["status"] == "state_rejected"
    assert row["error"] == "unknown public condition evidence reference"


def test_dynamic_schema_enumerates_condition_kind_and_public_artifact_ids():
    bundle = frozen.bundle(ROOT, "c004")
    unit = next(row for row in frozen.prepare(ROOT)["jobs"] if row["case_id"] == "c004")
    cursor = frozen.Cursor(ROOT, unit)
    asyncio.run(cursor.start())
    request = interaction.build_request(bundle, 0,
                                        AuthorizationState.model_validate(cursor.result["state"]),
                                        ContextSnapshot.model_validate(cursor.result["task_context"]),
                                        "candidate prompt", frozen.MODEL, frozen.POLICY)
    condition = request.output_schema["$defs"]["Condition"]["properties"]
    assert condition["kind"]["const"] == "candidate_sha256"
    assert condition["value"]["pattern"] == "^[0-9a-f]{64}$"
    assert set(condition["evidence_refs"]["items"]["enum"]) == set(bundle.registry.artifacts)
    assert "e002" not in condition["evidence_refs"]["items"]["enum"]


def test_v7_prompt_distinguishes_event_ids_from_artifact_ids():
    assert "candidate_sha256" in PROMPT_V7
    assert "must never appear in `Condition.evidence_refs`" in PROMPT_V7
    assert "candidate_patch_sha256" in PROMPT_V7


def test_corrected_control_commits_grant_but_digest_mismatch_stays_not_covered():
    bundle = frozen.bundle(ROOT, "c004")
    engine = BatchStateEngine("c004", bundle.review_input.events, "b"*64,
                              set(bundle.registry.artifacts))
    context = TaskContextLedger(bundle.review_input.events, set(bundle.registry.artifacts))
    calls = _real_calls()
    for index, row in enumerate(calls[:4]):
        parsed = interaction.parse_response(row["response_text"], bundle, index, "b"*64,
                                            row["call_id"], frozen.POLICY)
        context.submit(parsed.context_batch)
        assert engine.submit_batch(parsed.transition_batch).accepted
    reported = re.search(r"[0-9a-f]{64}", bundle.review_input.events[1].content).group(0)
    event = bundle.review_input.events[4]
    quote = event.content
    constraints = sorted(engine.constraints)
    raw = json.dumps({"kind":"extract","task_context":[],"state_items":[{
        "source_quote":quote,"target_ids":[],"constraint":None,
        "authorization":{"authorization_id":"offline_v4_control_grant",
          "subjects":[{"protocol":"http","method":"GET","path":"/api/v1/profile",
                       "api_version":"v1","location":"response.body","field":"status"}],
          "change_type":"replace_field","change_details":{"old_field":"status","new_field":"state",
             "preserve_value_semantics":True},
          "conditions":[{"kind":"candidate_sha256","value":reported,"resolution":"unsatisfied",
                          "evidence_refs":["patch"]}],"interpretation":"resolved",
          "constraint_refs":constraints,"supersedes":[]},"operation":"grant"}]},ensure_ascii=False)
    parsed = interaction.parse_response(raw, bundle, 4, "b"*64, "offline_v4_control", frozen.POLICY,
                                        lookup_attempts_used=1)
    assert engine.submit_batch(parsed.transition_batch).accepted
    assessment = assess_impacts(engine.snapshot(), bundle.evidence, bundle.review_input.candidate_patch.sha256)
    assert assessment.authorization == "not_covered"
    assert assessment.decision == "revise"
    assert bundle.review_input.candidate_patch.sha256 != reported
