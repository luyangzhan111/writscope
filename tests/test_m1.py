import json
from pathlib import Path
import shutil

import pytest

from writscope.artifacts import ArtifactStore, apply_patch, canonical_hash, digest, snapshot_hash
from writscope.events import DecisionPoint, JudgmentLabels, SourceRef, load_events, validate_source
from writscope.inputs import PREFIX, export_input, load_manifest
from writscope.m1 import run_validation


def test_executed_validation_succeeds_without_model(validation):
    report, output = validation
    assert report["status"] == "succeeded", report["failures"]
    assert report["execution_kind"] == "fixture_validation"
    assert report["model_calls"] == report["model_tokens"] == report["model_cost"] == 0
    assert report["authorization_evaluation"] == "not_run"
    assert (output / "report.md").is_file()


@pytest.mark.parametrize("name,payload,page_ok", [
    ("original", {"status": "active"}, False),
    ("candidate", {"state": "active"}, True),
    ("compatible", {"status": "active"}, True),
])
def test_observed_response_and_page(validation, name, payload, page_ok):
    report, _ = validation
    obs = report["variants"][name]["observation"]
    assert obs["http_status"] == 200
    assert obs["response"] == payload
    assert (obs["page"]["status"] == "succeeded") == page_ok
    if page_ok:
        assert obs["page"]["model"] == {"state": "active"}
    else:
        assert obs["page"]["error"] == {"type": "KeyError", "missing_key": "state"}
    assert obs["page_requests"] == [{"method": "GET", "path": "/api/v1/profile"}]
    assert obs["business_routes"] == ["GET /api/v1/profile"]


def test_same_protected_tests_prove_candidate_breaks(validation):
    report, output = validation
    for name, variant in report["variants"].items():
        failures = {test["test_id"] for test in variant["contract_tests"] if test["status"] == "failed"}
        assert len(variant["contract_tests"]) == 3
        assert failures == ({"test_status_exists", "test_exact_response"} if name == "candidate" else set())
        assert variant["pytest"]["exit_code"] == (1 if name == "candidate" else 0)
        assert (output / variant["pytest"]["stdout_ref"]).is_file()
        assert (output / variant["junit_ref"]).is_file()


def test_reference_labels_are_not_measured_predictions(root, validation):
    report, output = validation
    labels = json.loads((root / "evaluation_private/case_001/labels.json").read_text(encoding="utf-8"))
    assert labels["kind"] == "reference_labels_not_predictions"
    expected = [("not_covered", "revise"), ("covered", "allow"), ("not_covered", "revise")]
    for row, (auth, decision) in zip(labels["labels"], expected, strict=True):
        JudgmentLabels(impact=row["expected_impact"], authorization=row["expected_authorization"], decision=row["expected_decision"])
        assert row["expected_impact"] == "breaking"
        assert (row["expected_authorization"], row["expected_decision"]) == (auth, decision)
        events = load_events(root / PREFIX / "inputs" / (row["case_id"] + ".jsonl"), DecisionPoint(event_id="e005", seq=5))
        validate_source(SourceRef.model_validate(row["reference_source_refs"][0]), events, authorization=True)
    # Measured records and public evidence contain neither inferred labels nor a score.
    assert "expected_authorization" not in json.dumps(report)
    evidence = json.loads((output / "impact.json").read_text(encoding="utf-8"))
    assert evidence["assessment"] == "breaking"
    assert "authorization" not in evidence and "decision" not in evidence
    assert report["variants"]["candidate"]["observation"]["old_contract_checks"]["status_exists"] is False


def test_histories_only_change_final_content(root):
    histories = [(root / PREFIX / "inputs" / (cid + ".jsonl")).read_bytes().splitlines(keepends=True) for cid in ("c001", "c002", "c003")]
    assert histories[0][:-1] == histories[1][:-1] == histories[2][:-1]
    finals = [json.loads(lines[-1]) for lines in histories]
    assert len({row["content"] for row in finals}) == 3
    assert all({k: v for k, v in row.items() if k != "content"} == {k: v for k, v in finals[0].items() if k != "content"} for row in finals)


@pytest.mark.parametrize("mode", ["verified_impact", "code_tools"])
def test_equal_evidence_and_no_private_material(validation, mode):
    report, output = validation
    inputs = []
    for cid in ("c001", "c002", "c003"):
        bundle = output / "inputs" / mode / cid
        data = json.loads((bundle / "input.json").read_text(encoding="utf-8"))
        registry = json.loads((bundle / "artifact_manifest.json").read_text(encoding="utf-8"))["artifacts"]
        for file in bundle.rglob("*"):
            if file.is_file():
                text = file.read_text(encoding="utf-8")
                assert "EVALUATOR_ONLY_M1_REFERENCE" not in text
                assert "expected_authorization" not in text
                assert "compatible.patch" not in text and "hidden_tests" not in text
        files = {item["path"]: item["sha256"] for item in registry.values() if "path" in item}
        ArtifactStore(bundle, files).verify()
        assert (bundle / "artifacts/impact.json").exists() == (mode == "verified_impact")
        assert data["events"][-1]["seq"] == 5
        assert not data["tool_policy"]["available_tools"]
        inputs.append({k: v for k, v in data.items() if k not in {"case_id", "events"}})
    assert inputs[0] == inputs[1] == inputs[2]
    assert len({row["common_evidence_sha256"] for row in report["input_bundles"][mode].values()}) == 1


def make_history(root, tmp_path, mutate):
    path = tmp_path / "events.jsonl"
    rows = [json.loads(line) for line in (root / PREFIX / "inputs/c001.jsonl").read_text(encoding="utf-8").splitlines()]
    mutate(rows)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return path


def test_cutoff_excludes_future_and_invalid_refs(root, tmp_path):
    def add_future(rows):
        rows.append(dict(rows[-1], event_id="e006", seq=6, content="FUTURE_PRIVATE_SENTINEL"))
    path = make_history(root, tmp_path, add_future)
    events = load_events(path, DecisionPoint(event_id="e005", seq=5))
    assert len(events) == 5
    assert "FUTURE_PRIVATE_SENTINEL" not in str(events)
    with pytest.raises(ValueError, match="outside visible"):
        validate_source(SourceRef(event_id="e006", quote="FUTURE", start=0, end=6), events, authorization=True)


@pytest.mark.parametrize("change", ["duplicate", "gap", "extra", "version", "role", "authority"])
def test_reject_invalid_event_structure(root, tmp_path, change):
    def mutate(rows):
        if change == "duplicate": rows[-1]["event_id"] = "e001"
        if change == "gap": rows[-1]["seq"] = 8
        if change == "extra": rows[-1]["expected_authorization"] = "covered"
        if change == "version": rows[-1]["schema_version"] = "999"
        if change == "role": rows[-1]["role"] = "developer"
        if change == "authority": rows[1]["provenance"]["authority_level"] = "user"
    with pytest.raises(ValueError):
        load_events(make_history(root, tmp_path, mutate), DecisionPoint(event_id="e005", seq=5))


def test_quote_and_role_validation(root):
    events = load_events(root / PREFIX / "inputs/c001.jsonl", DecisionPoint(event_id="e005", seq=5))
    tool = events[1]
    ref = SourceRef(event_id=tool.event_id, quote=tool.content, start=0, end=len(tool.content))
    validate_source(ref, events)
    with pytest.raises(ValueError, match="only a user"):
        validate_source(ref, events, authorization=True)
    with pytest.raises(ValueError, match="quote mismatch"):
        validate_source(SourceRef(event_id="e005", quote="not the source", start=0, end=5), events)


@pytest.mark.parametrize("labels", [
    ("breaking", "not_needed", "allow"),
    ("breaking", "covered", "revise"),
    ("unknown", "covered", "allow"),
    ("compatible", "covered", "allow"),
])
def test_reject_inconsistent_label_combinations(labels):
    with pytest.raises(ValueError):
        JudgmentLabels(impact=labels[0], authorization=labels[1], decision=labels[2])


@pytest.mark.parametrize("target", ["../labels.json", "/tmp/file", "C:/secret", "a\\b", "evaluation_private/labels.json", "test_contract.py"])
def test_patch_cannot_target_scoring_or_outside_files(root, tmp_path, target):
    for name in ("app.py", "client.py"):
        shutil.copyfile(root / PREFIX / "base" / name, tmp_path / name)
    before = snapshot_hash(tmp_path)
    patch = f"--- a/{target}\n+++ b/{target}\n@@ -1,1 +1,1 @@\n-x\n+y\n".encode()
    with pytest.raises(ValueError, match="not allowed"):
        apply_patch(tmp_path, patch, before)
    assert snapshot_hash(tmp_path) == before


def test_patch_rejects_wrong_base_and_context(root, tmp_path):
    for name in ("app.py", "client.py"):
        shutil.copyfile(root / PREFIX / "base" / name, tmp_path / name)
    patch = (root / PREFIX / "candidate.patch").read_bytes()
    before = snapshot_hash(tmp_path)
    with pytest.raises(ValueError, match="base hash"):
        apply_patch(tmp_path, patch, "0" * 64)
    with pytest.raises(ValueError, match="context mismatch"):
        apply_patch(tmp_path, patch.replace(b'"status"', b'"missing"'), before)
    assert snapshot_hash(tmp_path) == before


def test_artifact_store_rejects_private_reads_and_tampering(tmp_path):
    (tmp_path / "public.txt").write_bytes(b"public")
    store = ArtifactStore(tmp_path, {"public.txt": digest(b"public")})
    assert store.read("public.txt") == b"public"
    for target in ("../private.txt", "private.txt", "C:/private.txt"):
        with pytest.raises(ValueError, match="not allowlisted"):
            store.read(target)
    (tmp_path / "public.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.read("public.txt")


def test_link_escape_is_rejected(tmp_path):
    target, root = tmp_path / "private", tmp_path / "public"
    target.mkdir()
    root.mkdir()
    (target / "secret").write_bytes(b"secret")
    link = root / "linked"
    import os
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
    store = ArtifactStore(root, {"linked/secret": digest(b"secret")})
    with pytest.raises(ValueError, match="linked artifact"):
        store.read("linked/secret")


def test_fixture_and_scoring_files_unchanged(root, validation):
    ArtifactStore(root, load_manifest(root)["files"]).verify()


def test_failed_run_is_saved_without_breaking_claim(root, tmp_path):
    broken_root = tmp_path / "repo"
    (broken_root / PREFIX).mkdir(parents=True)
    manifest = load_manifest(root)
    (broken_root / PREFIX / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = run_validation(broken_root, tmp_path / "failed-output")
    assert report["status"] == "failed"
    assert report["failures"] and not report["variants"]
    assert not (tmp_path / "failed-output/impact.json").exists()
    assert (tmp_path / "failed-output/report.json").is_file()


def test_export_rejects_evidence_for_another_patch(root, tmp_path, validation):
    _, output = validation
    evidence = json.loads((output / "impact.json").read_text(encoding="utf-8"))
    evidence["patch_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="does not match"):
        export_input(root, tmp_path / "input", "c001", evidence=evidence)


def test_existing_output_is_never_overwritten(root, tmp_path):
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_validation(root, tmp_path)
    assert (tmp_path / "keep.txt").read_text() == "keep"
