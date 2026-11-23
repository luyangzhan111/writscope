"""Audit the frozen S4 publication separately from the maintained offline CLI.

The old publication inventory remains immutable. Its checks run in a new
restored directory, using archived bytes wherever the checkout has changed.
History scope is HEAD, branches, tags and remotes, excluding application-only
snapshot refs. This does not certify an old audit of every local ref.
No environment values other than the explicit subprocess allowlist are read.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from scripts import s4_public_release as publication
from scripts.s4_release_scan import Limits, scan_files


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR = "reports/public-s4-v0.2/publish-files.json"
SUPPLEMENTARY_FILES = {
    "README.md": "reports/offline-demo-release-sources-v1/README.md",
    "docs/s4/README.md": "reports/offline-demo-release-sources-v1/docs-s4-README.md",
    ".github/workflows/tests.yml": "reports/offline-demo-release-sources-v1/tests-workflow.yml",
    "docs/project-status.md": "reports/offline-demo-release-sources-v1/project-status.md",
}
README_SUPPLEMENTS = {
    "7f673ea0dc98cf6ca5ae96cf1105fef184074671b061f2ffc3267783a15f5972":
        "reports/offline-demo-release-sources-v1/README.md",
    "0073e94949c5da551cab885115416f26f70c2c9b11a4bdf8cdeb1cf3b218506e":
        "reports/offline-demo-release-sources-v1/readme-20261109.md",
}
CURRENT_FILES = (
    "README.md", "pyproject.toml", "tests/test_demo.py",
    "scripts/offline_demo_release_check.py", ".github/workflows/tests.yml",
    "src/writscope/cli.py", "src/writscope/__main__.py",
    "scripts/demo_package_smoke.py", "tests/demo-ui-smoke.cjs", "docs/offline-demo.md",
    "src/writscope/review.py", "src/writscope/review_html.py", "src/writscope/review_openapi.py",
    "tests/test_review.py", "docs/offline-review.md", "docs/case-studies/immich-server-ping.md",
    "docs/case-studies/csc-iso-convert.md", "docs/case-studies/csc-iso-convert.review.json",
    "examples/review_fastapi/__init__.py", "examples/review_fastapi/export.py",
    "examples/review_fastapi/run.py", "examples/review_fastapi/service.py",
    "examples/review_fastapi/README.md",
    "reports/offline-demo-release-sources-v1/manifest.json",
    "reports/offline-demo-release-sources-v1/readme-20261109.md",
    "docs/s4/v8-development-closeout-20261109.md",
    *SUPPLEMENTARY_FILES.values(),
)
ENVIRONMENT_NAMES = ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH")
HISTORICAL_AUDIT_TIMEOUT_SECONDS = 1200
HISTORY_SCOPE = "HEAD_branches_tags_remotes"
SCOPE_CHILD = r'''
import json
import subprocess
from scripts import s4_public_release as publication

original = subprocess.check_output
adapted_calls = 0
def check_output(command, *args, **kwargs):
    global adapted_calls
    if command == ['git', 'rev-list', '--objects', '--all']:
        adapted_calls += 1
        command = ['git', 'rev-list', '--objects', 'HEAD', '--branches', '--tags', '--remotes']
    return original(command, *args, **kwargs)

subprocess.check_output = check_output
try:
    result = publication.audit()
finally:
    subprocess.check_output = original
if adapted_calls != 1:
    raise RuntimeError('publication_scope_adapter_call_mismatch')
result.update(history_scope='HEAD_branches_tags_remotes',
              all_local_refs_certified=False, scope_adapter_calls=adapted_calls)
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(1 if result['findings'] else 0)
'''


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _supplement_bytes(name: str, expected: str) -> bytes:
    # Different published README versions are distinct originals. Select only
    # the exact digest requested by the original inventory, never a fallback.
    relative = (README_SUPPLEMENTS.get(expected) if name == "README.md"
                else SUPPLEMENTARY_FILES.get(name))
    if relative is None:
        raise ValueError("unsupported_publication_supplement_version")
    data = publication.safe(ROOT, relative).read_bytes()
    if _digest(data) != expected:
        raise ValueError("publication_supplement_hash_mismatch")
    return data


def _current_scan() -> dict:
    # Only this explicit new package subtree is expanded. No repository-wide
    # traversal, environment files, runtime records or credential stores.
    folder = publication.safe(ROOT, "src/writscope/demo")
    if not folder.is_dir():
        raise ValueError("offline_demo_package_missing")
    names = list(CURRENT_FILES)
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(ROOT).as_posix()
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        publication.safe(ROOT, relative)  # Reject links before a file read.
        if path.is_file():
            names.append(relative)
    return scan_files(ROOT, names, limits=Limits(max_depth=12))


def audit_restored(destination: Path) -> dict:
    """Apply the exact in-memory ref adapter; keep every scanner finding."""
    env = {name: os.environ[name] for name in ENVIRONMENT_NAMES if name in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    refs = subprocess.check_output(
        ['git', 'for-each-ref', '--format=%(refname)'], cwd=destination,
        env=env, timeout=60,
    ).decode('utf-8').splitlines()
    excluded = {}
    for ref in refs:
        if ref.startswith(('refs/heads/', 'refs/tags/', 'refs/remotes/')):
            continue
        category = ('codex_automatic_snapshot_ref' if ref.startswith('refs/codex/')
                    else 'other_nonpublication_ref')
        excluded[category] = excluded.get(category, 0) + 1
    done = subprocess.run(
        [sys.executable, "-B", "-c", SCOPE_CHILD], cwd=destination, env=env,
        capture_output=True, text=True, encoding="utf-8",
        timeout=HISTORICAL_AUDIT_TIMEOUT_SECONDS,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    # Never echo child output, which may describe input paths. Keep all findings
    # in the decision; no category or location is filtered to manufacture pass.
    try:
        result = json.loads(done.stdout)
    except (ValueError, TypeError):
        result = {}
    findings = result.get("findings")
    valid_scope = (result.get('history_scope') == HISTORY_SCOPE
                   and result.get('all_local_refs_certified') is False
                   and result.get('scope_adapter_calls') == 1)
    okay = done.returncode == 0 and valid_scope and isinstance(findings, list) and not findings
    return {
        "status": "verified_publishable_scope" if okay else "publication_scope_audit_failed",
        "history_scope": HISTORY_SCOPE,
        "all_local_refs_certified": False,
        "excluded_ref_categories": excluded,
        "publication_scope_audit_exit_code": done.returncode,
        "publication_scope_finding_count": len(findings) if isinstance(findings, list) else None,
        "publication_files_requested": result.get('files_requested'),
        "publication_files_scanned": result.get('files_scanned'),
        "publication_history_objects_scanned": result.get('history_objects_scanned'),
        "scope_adapter_calls": result.get('scope_adapter_calls'),
    }


def check(output: Path) -> dict:
    destination = output.absolute()
    # The archived audit intentionally discovers this checkout's Git history
    # by walking upward. Never copy .git or silently audit another repository.
    if destination.resolve() == ROOT or not destination.resolve().is_relative_to(ROOT):
        raise ValueError("new_output_inside_checkout_required")
    descriptor_bytes = publication.safe(ROOT, DESCRIPTOR).read_bytes()
    descriptor = json.loads(descriptor_bytes)
    inventory = descriptor.get("files")
    metadata = descriptor.get("required_metadata_files", [])
    if (descriptor.get("version") != "s4_explicit_publication_inventory_v1"
            or not isinstance(inventory, dict) or not inventory
            or not isinstance(metadata, list)
            or any(not isinstance(name, str) for name in metadata)):
        raise ValueError("invalid_original_publication_inventory")
    for name, expected in inventory.items():
        publication.safe(ROOT, name)
        publication.safe(destination, name)
        if not _sha(expected):
            raise ValueError("invalid_original_publication_hash")
    for name in metadata:
        publication.safe(ROOT, name)
        publication.safe(destination, name)

    current_scan = _current_scan()
    if current_scan["findings"]:
        return {"status": "current_file_scan_failed",
                "current_files_scanned": current_scan["files_scanned"],
                "finding_count": len(current_scan["findings"])}

    manifest, blobs = publication.checked(ROOT)
    original_bytes = {}
    # Preflight every selected original before restore() creates a directory.
    # A newly edited checkout file cannot substitute for a missing old source.
    for name in sorted(set(inventory) | set(metadata) | {DESCRIPTOR}):
        expected = inventory.get(name)
        if name == DESCRIPTOR:
            data = descriptor_bytes
        elif name == "README.md":
            data = _supplement_bytes(name, expected)
        elif (name in manifest["files"]
              and manifest["files"][name]["sha256"] == expected):
            data = blobs[expected]
        elif name in SUPPLEMENTARY_FILES:
            data = _supplement_bytes(name, expected)
        else:
            data = publication.safe(ROOT, name).read_bytes()
        if expected is not None and _digest(data) != expected:
            raise ValueError("original_publication_source_unavailable")
        original_bytes[name] = data

    restored = publication.restore(destination, root=ROOT)
    replacements = 0
    additions = 0
    # restore() has already validated and applied the frozen runtime_overlay.
    # Keep those bytes when the original publication inventory names them;
    # recover original non-overlay sources from the checked CAS archive.
    for name, data in original_bytes.items():
        target = publication.safe(destination, name)
        if target.is_file() and target.read_bytes() == data:
            continue
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        replacements += int(existed)
        additions += int(not existed)
    if publication.safe(destination, DESCRIPTOR).read_bytes() != descriptor_bytes:
        raise ValueError("original_descriptor_bytes_changed")
    if any(_digest(publication.safe(destination, name).read_bytes()) != expected
           for name, expected in inventory.items()):
        raise ValueError("restored_publication_hash_mismatch")

    audit = audit_restored(destination)
    return {
        **audit,
        "scope_definition": "frozen_publication_files_archives_plus_publishable_history_plus_current_maintenance_files",
        "historical_inventory_files": len(inventory),
        "historical_descriptor_sha256": _digest(descriptor_bytes),
        "restored_files": restored["restored_files"],
        "publication_files_added": additions,
        "publication_files_replaced": replacements,
        "current_files_scanned": current_scan["files_scanned"],
        "current_finding_count": 0,
        "original_inventory_modified": False,
        "model_requests": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="new restored audit directory inside this checkout")
    args = parser.parse_args(argv)
    try:
        result = check(args.output)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Do not print exception values or raw file contents.
        result = {"status": "failed", "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "verified_publishable_scope" else 1


if __name__ == "__main__":
    raise SystemExit(main())
