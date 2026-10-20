"""M2a is an offline pipeline test, never a model-quality test."""

import asyncio
import json
import shutil

import pytest

from writscope.artifacts import digest
from writscope.baselines import build_request
from writscope.contracts import load_bundle, validate_output
from writscope.evaluation import read_run, score_run
from writscope.providers import ModelConfig, Usage
from writscope.providers.mock import Script, ScriptedMock, Step, load_script
from writscope.runner import RunPolicy, run_suite


def inputs(root):
    base = root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact"
    return [base / case for case in ("c001", "c002", "c003")]


def prompts(root):
    return {method: (root / "prompts" / (method + ".md")).read_text(encoding="utf-8")
            for method in ("full_direct", "full_checklist")}


def scripts(root):
    return {method: ScriptedMock(load_script(root / "examples" / "m2a" / ("direct_script.json" if method == "full_direct" else "checklist_script.json")))
            for method in ("full_direct", "full_checklist")}


def test_equal_public_evidence_and_label_isolation(root):
    for directory in inputs(root):
        bundle = load_bundle(directory)
        requests = [build_request(bundle, method, prompts(root)[method], ModelConfig())
                    for method in ("full_direct", "full_checklist")]
        assert requests[0].evidence_sha256 == requests[1].evidence_sha256
        assert requests[0].public_input == requests[1].public_input
        assert requests[0].model_config_values == requests[1].model_config_values
        assert requests[0].prompt_sha256 != requests[1].prompt_sha256
        serialized = requests[0].model_dump_json() + requests[1].model_dump_json()
        assert "EVALUATOR_ONLY_M1_REFERENCE" not in serialized
        assert "expected_authorization" not in serialized
        assert "compatible.patch" not in serialized
        assert "hidden_tests" not in serialized
        assert bundle.review_input.events[-1].event_id == bundle.review_input.decision_point.event_id


def test_bundle_hash_and_history_guards(root, tmp_path):
    source = inputs(root)[0]
    copy = tmp_path / "c001"
    shutil.copytree(source, copy)
    (copy / "artifacts" / "base" / "app.py").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_bundle(copy)
    shutil.copytree(source, copy, dirs_exist_ok=True)
    data = json.loads((copy / "input.json").read_text(encoding="utf-8"))
    data["events"].append({**data["events"][-1], "event_id": "e006", "seq": 6})
    (copy / "input.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="history exceeds"):
        load_bundle(copy)
    missing = tmp_path / "missing"
    shutil.copytree(source, missing)
    (missing / "artifacts" / "contract.md").unlink()
    with pytest.raises(FileNotFoundError):
        load_bundle(missing)


def test_output_structure_and_sources(root):
    bundle = load_bundle(inputs(root)[0])
    raw = (root / "examples" / "m2a" / "direct_response.json").read_text(encoding="utf-8")
    result = validate_output(raw, bundle, "full_direct")
    assert result.authorization == "not_covered"
    with pytest.raises(ValueError, match="duplicate JSON key"):
        validate_output('{"impact":"breaking","impact":"breaking"}', bundle, "full_direct")
    with pytest.raises(ValueError, match="checklist"):
        validate_output(raw, bundle, "full_checklist")
    invalid = json.loads(raw)
    invalid["source_refs"][0]["quote"] = "forged"
    with pytest.raises(ValueError, match="source quote mismatch"):
        validate_output(json.dumps(invalid), bundle, "full_direct")
    invalid = json.loads(raw)
    invalid["decision"] = "allow"
    with pytest.raises(ValueError, match="inconsistent three-axis"):
        validate_output(json.dumps(invalid), bundle, "full_direct")
    invalid = json.loads(raw)
    invalid["constraint_refs"] = ["not_defined"]
    with pytest.raises(ValueError, match="unresolved"):
        validate_output(json.dumps(invalid), bundle, "full_direct")
    invalid = json.loads(raw)
    invalid["affected_subjects"][0]["api_version"] = "v2"
    invalid["affected_subjects"][0]["path"] = "/api/v2/profile"
    with pytest.raises(ValueError, match="affected subject outside"):
        validate_output(json.dumps(invalid), bundle, "full_direct")
    invalid = json.loads(raw)
    invalid["derived_items"][0]["source_refs"] = [{"event_id": "e002", "quote": "合成页面测试反馈", "start": 0, "end": 8}]
    with pytest.raises(ValueError, match="only a user source"):
        validate_output(json.dumps(invalid), bundle, "full_direct")


def test_end_to_end_wrong_answer_rescore_and_tamper(root, tmp_path):
    providers = scripts(root)
    run_dir = tmp_path / "run"
    asyncio.run(run_suite(inputs(root), run_dir, prompts(root), lambda method: providers[method]))
    labels = root / "evaluation_private" / "case_001" / "labels.json"
    summary = score_run(run_dir, labels, tmp_path / "score")
    assert len(summary["rows"]) == 6
    assert {row["status"] for row in summary["rows"]} == {"succeeded"}
    for method in ("full_direct", "full_checklist"):
        metric = summary["methods"][method]
        assert metric["axes"]["impact"]["correct"] == 3
        assert metric["axes"]["authorization"]["correct"] == 2
        assert metric["axes"]["decision"]["correct"] == 2
        assert metric["false_block"]["count"] == 1
    assert summary["cost"]["actual_model_calls"] == 0
    assert summary["cost"]["synthetic_test_usage"]["input_tokens"]["unknown_attempts"] == 2
    assert summary["methods"] == score_run(run_dir, labels, tmp_path / "score-again")["methods"]
    before = digest((run_dir / "job_000" / "request.json").read_bytes())
    edited = json.loads(labels.read_text(encoding="utf-8"))
    edited["labels"][0].update(expected_authorization="covered", expected_decision="allow")
    alternate = tmp_path / "alternate-labels.json"
    alternate.write_text(json.dumps(edited), encoding="utf-8")
    changed = score_run(run_dir, alternate, tmp_path / "score-alternate")
    assert changed["methods"]["full_direct"]["axes"]["authorization"]["correct"] == 1
    assert digest((run_dir / "job_000" / "request.json").read_bytes()) == before
    (run_dir / "job_000" / "result.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        read_run(run_dir)


def test_retry_timeout_invalid_output_and_failed_denominator(root, tmp_path):
    direct_raw = (root / "examples" / "m2a" / "direct_response.json").read_text(encoding="utf-8")
    checklist_raw = (root / "examples" / "m2a" / "checklist_response.json").read_text(encoding="utf-8")
    direct = ScriptedMock(Script(schema_version="0.1-draft", execution_kind="offline_mock", description="retry",
                                 steps=[Step(action="error", text="transient", synthetic_usage=Usage(input_tokens=10)),
                                        Step(action="response", text=direct_raw)]))
    checklist = ScriptedMock(Script(schema_version="0.1-draft", execution_kind="offline_mock", description="timeout",
                                    steps=[Step(action="response", text=checklist_raw, delay_seconds=0.05),
                                           Step(action="response", text="not json")]))
    output = tmp_path / "run"
    manifest = asyncio.run(run_suite(inputs(root)[:1], output, prompts(root),
                                      lambda method: {"full_direct": direct, "full_checklist": checklist}[method],
                                      policy=RunPolicy(timeout_seconds=0.005, max_attempts_per_job=2, max_total_attempts=4)))
    jobs = [json.loads((output / f"job_{i:03d}" / "job.json").read_text(encoding="utf-8")) for i in range(2)]
    assert [job["status"] for job in jobs] == ["succeeded", "invalid_output"]
    assert manifest["totals"]["provider_attempts"] == 4
    assert manifest["totals"]["synthetic_test_usage"]["input_tokens"]["known_subtotal"] == 10
    labels = root / "evaluation_private" / "case_001" / "labels.json"
    scored = score_run(output, labels, tmp_path / "score")
    assert scored["methods"]["full_checklist"]["axes"]["impact"] == {"correct": 0, "denominator": 1, "accuracy": 0.0}
    assert scored["methods"]["full_checklist"]["failed"] == 1


def test_budget_exhaustion_is_preserved(root, tmp_path):
    providers = scripts(root)
    output = tmp_path / "run"
    asyncio.run(run_suite(inputs(root)[:1], output, prompts(root), lambda method: providers[method],
                          policy=RunPolicy(max_total_attempts=1)))
    scored = score_run(output, root / "evaluation_private" / "case_001" / "labels.json", tmp_path / "score")
    assert scored["methods"]["full_checklist"]["status_counts"] == {"budget_exhausted": 1}
    assert scored["methods"]["full_checklist"]["axes"]["decision"]["denominator"] == 1


def test_invalid_input_is_preserved_in_denominator(root, tmp_path):
    copy = tmp_path / "c001"
    shutil.copytree(inputs(root)[0], copy)
    (copy / "artifacts" / "candidate.patch").write_text("tampered", encoding="utf-8")
    providers = scripts(root)
    output = tmp_path / "run"
    manifest = asyncio.run(run_suite([copy], output, prompts(root), lambda method: providers[method]))
    assert manifest["totals"]["provider_attempts"] == 0
    scored = score_run(output, root / "evaluation_private" / "case_001" / "labels.json", tmp_path / "score")
    for method in ("full_direct", "full_checklist"):
        assert scored["methods"][method]["status_counts"] == {"invalid_input": 1}
        assert scored["methods"][method]["axes"]["authorization"]["denominator"] == 1
