"""Export equal-evidence bundles without labels, reference fixes or future events."""

import json
from pathlib import Path

from .artifacts import ArtifactStore, canonical_hash, digest, safe_path, write_json
from .events import DecisionPoint, load_events

PREFIX = "fixtures/case_001/"
PUBLIC_FILES = {
    "app": PREFIX + "base/app.py",
    "client": PREFIX + "base/client.py",
    "patch": PREFIX + "candidate.patch",
    "contract": PREFIX + "contract.md",
    "public_checks": PREFIX + "public_tests/contract.py",
}
EXPORT_NAMES = {
    "app": "artifacts/base/app.py",
    "client": "artifacts/base/client.py",
    "patch": "artifacts/candidate.patch",
    "contract": "artifacts/contract.md",
    "public_checks": "artifacts/public_tests/contract.py",
}


def load_manifest(root: Path):
    manifest = json.loads((root / PREFIX / "manifest.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] != "0.1-draft":
        raise ValueError("unsupported fixture manifest version")
    return manifest


def export_input(root: Path, destination: Path, case_id: str, *, evidence=None):
    manifest = load_manifest(root)
    if case_id not in manifest["case_ids"]:
        raise ValueError("unknown case ID")
    store = ArtifactStore(root, manifest["files"])
    point = DecisionPoint.model_validate(manifest["decision_point"])
    history_path = PREFIX + "inputs/" + case_id + ".jsonl"
    store.read(history_path)  # Verify trusted fixture bytes before parsing provenance.
    events = load_events(root / history_path, point)
    destination.mkdir(parents=True, exist_ok=False)
    registry = {}
    for artifact_id, source in PUBLIC_FILES.items():
        data = store.read(source)
        relative = EXPORT_NAMES[artifact_id]
        path = safe_path(destination, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        registry[artifact_id] = {"path": relative, "sha256": digest(data)}
    base = {name + ".py": registry[name]["sha256"] for name in ("app", "client")}
    evidence_refs = [{"artifact_id": "public_checks", "sha256": registry["public_checks"]["sha256"]}]
    if evidence is not None:
        if evidence["snapshot_sha256"] != canonical_hash(base) or evidence["patch_sha256"] != registry["patch"]["sha256"]:
            raise ValueError("impact evidence does not match snapshot/patch")
        write_json(destination / "artifacts/impact.json", evidence)
        registry["impact"] = {"path": "artifacts/impact.json", "sha256": digest((destination / "artifacts/impact.json").read_bytes())}
        evidence_refs.append({"artifact_id": "impact", "sha256": registry["impact"]["sha256"]})
    visible_files = sorted(entry["path"] for entry in registry.values())
    data = {
        "schema_version": "0.1-draft", "case_id": case_id,
        "events": [event.model_dump() for event in events],
        "decision_point": point.model_dump(),
        "snapshot": {"artifact_id": "base", "sha256": canonical_hash(base), "files_manifest": base},
        "candidate_patch": {"artifact_id": "patch", "sha256": registry["patch"]["sha256"],
                            "base_sha256": canonical_hash(base), "format": "unified_diff"},
        "contract_refs": [{"artifact_id": "contract", "sha256": registry["contract"]["sha256"]}],
        "evidence_mode": "verified_impact" if evidence is not None else "code_tools",
        "evidence_refs": evidence_refs,
        "tool_policy": {"available_tools": [], "visible_files": visible_files,
                        "execution": "not_enabled_in_m1", "timeout_seconds": None,
                        "per_call_limit": None, "total_limit": None},
    }
    # Snapshot is a group; concrete file access uses the two member artifacts.
    registry["base"] = {"members": ["app", "client"], "sha256": canonical_hash(base)}
    write_json(destination / "input.json", data)
    write_json(destination / "artifact_manifest.json", {"schema_version": "0.1-draft", "artifacts": registry})
    return data
