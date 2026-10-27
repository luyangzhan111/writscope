"""Release restoration must reject unsafe, corrupted, or conflicting evidence."""
from hashlib import sha256
import json
from pathlib import Path
import zipfile

import pytest

from scripts import public_release as release


def make_archive(root, members):
    folder = root / release.BUNDLE
    folder.mkdir(parents=True)
    archive = folder / "evidence.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    release.write(folder / "manifest.json", {
        "version": "public_evidence_v1", "archive_sha256": sha256(archive.read_bytes()).hexdigest(),
        "file_count": len(members), "files": [
            {"path": name, "sha256": sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in members.items()],
    })
    return archive


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/drive", "reports/local/../../escape",
                                  "reports\\local\\file", "reports/local//file"])
def test_unsafe_archive_paths_are_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        release.safe_path(tmp_path, name)


def test_corrupted_archive_fails_before_restoring_any_file(tmp_path):
    archive = make_archive(tmp_path, {"reports/local/sample/raw.json": b"original"})
    archive.write_bytes(archive.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="hash mismatch"):
        release.restore(tmp_path)
    assert not (tmp_path / "reports/local").exists()


def test_restore_prechecks_every_conflict_and_preserves_existing_bytes(tmp_path):
    make_archive(tmp_path, {"reports/local/a/first": b"first", "reports/local/b/second": b"second"})
    conflict = tmp_path / "reports/local/b/second"
    conflict.parent.mkdir(parents=True)
    conflict.write_bytes(b"user work")
    with pytest.raises(ValueError, match="restore conflict"):
        release.restore(tmp_path)
    assert conflict.read_bytes() == b"user work"
    assert not (tmp_path / "reports/local/a/first").exists()


def test_restore_is_byte_exact_and_idempotent(tmp_path):
    make_archive(tmp_path, {"reports/local/sample/raw.json": b'{"original":true}\r\n'})
    assert release.restore(tmp_path)["restored_files"] == 1
    assert release.restore(tmp_path)["restored_files"] == 0
    assert (tmp_path / "reports/local/sample/raw.json").read_bytes() == b'{"original":true}\r\n'


def test_unlisted_archive_member_is_rejected(tmp_path):
    archive = make_archive(tmp_path, {"reports/local/sample/one": b"one"})
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("reports/local/sample/unlisted", b"extra")
    manifest = release.read(tmp_path / release.BUNDLE / "manifest.json")
    manifest["archive_sha256"] = sha256(archive.read_bytes()).hexdigest()
    release.write(tmp_path / release.BUNDLE / "manifest.json", manifest)
    with pytest.raises(ValueError, match="inventory mismatch"):
        release.restore(tmp_path)


def test_launch_claim_relocation_does_not_change_raw_file(tmp_path):
    from scripts import s3_acceptance_v8 as runner
    claim = tmp_path / "reports/local/s3-v5-paid-claims" / ("a" * 64 + ".json")
    run = tmp_path / "reports/local/saved"
    raw = {"plan_sha256": "a" * 64, "output": "D:\\old\\reports\\local\\saved", "authorization_ref": "consumed"}
    release.write(claim, raw)
    release.write(run / "run.json", {"plan_sha256": "a" * 64, "authorization_ref": "consumed"})
    before, original_reader = claim.read_bytes(), runner.read
    with release.relocated_claims(tmp_path) as changes:
        view = runner.read(claim)
        assert view["output"] == str(run.resolve())
        assert len(changes) == 1
    assert runner.read is original_reader
    assert claim.read_bytes() == before and release.read(claim) == raw


def test_launch_claim_with_wrong_plan_is_rejected(tmp_path):
    from scripts import s3_acceptance_v8 as runner
    claim = tmp_path / "reports/local/s3-v5-paid-claims" / ("a" * 64 + ".json")
    release.write(claim, {"plan_sha256": "b" * 64, "output": "D:\\old\\reports\\local\\saved", "authorization_ref": "consumed"})
    with release.relocated_claims(tmp_path), pytest.raises(ValueError, match="plan mismatch"):
        runner.read(claim)


def test_evidence_selection_keeps_adjacent_frozen_source_and_dynamic_seed(tmp_path):
    run = tmp_path / "reports/local/example-run"
    run.mkdir(parents=True)
    (run / "run.json").write_text("{}", encoding="utf-8")
    frozen = run.with_name(run.name + "-runner-source.py")
    frozen.write_text("# original runner", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "seed.py").write_text("RUN = 'reports/local/example-run'", encoding="utf-8")
    assert set(release.evidence_files(tmp_path)) == {
        "reports/local/example-run/run.json", "reports/local/example-run-runner-source.py"}
