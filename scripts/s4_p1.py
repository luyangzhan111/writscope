"""Offline S4 P1: execute reviewed fixture copies and export four public bundles.

No model or arbitrary candidate code is run. The fixed Python copies are trusted;
copying them is not a security sandbox. Output contains a private validation
subdirectory, so only individual bundles may be given to a method.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from writscope.artifacts import ArtifactStore, apply_patch, canonical_hash, digest, safe_path, write_json
from writscope.contracts import ImpactEvidence, load_bundle
from writscope.events import DecisionPoint, load_events

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "fixtures/s4_p1/manifest.json"
MANIFEST_SHA256 = "571a7c8dea471eb2ff0ba4468f868e2307fe7f007a0c1bc5919f3ff53d6b3025"
EXPORT_NAMES = {"app": "artifacts/base/app.py", "client": "artifacts/base/client.py",
                "patch": "artifacts/candidate.patch", "contract": "artifacts/contract.md",
                "public_checks": "artifacts/public_tests/contract.py"}
PRIVATE_PATCH = {"profile": "evaluation_private/case_001/compatible.patch",
                 "account": "evaluation_private/s4_p1/account/compatible.patch"}
CASES = {"s4_027": "profile", "s4_006": "profile", "s4_010": "account", "s4_004": "account"}


def read_manifest(root):
    raw = safe_path(root, MANIFEST).read_bytes()
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError("P1 manifest does not match reviewed hash")
    manifest = json.loads(raw)
    if manifest["version"] != "s4_p1_inputs_v1" or manifest["cases"] != CASES:
        raise ValueError("unexpected P1 case manifest")
    store = ArtifactStore(root, manifest["sha256"] | manifest["private_patches"])
    store.verify()
    return manifest, store


def verify_observations(app, rows):
    endpoint = "/api/v1/" + app
    old = {"status": "active"}
    expected = {"original": old, "compatible": old,
                "candidate": {"state": "active"} if app == "profile" else {"status": "inactive"}}
    for name, obs in rows.items():
        if (obs["http_status"] != 200 or obs["response"] != expected[name]
                or obs["business_routes"] != [f"GET {endpoint}"]
                or obs["page_requests"] != [{"method": "GET", "path": endpoint}]):
            raise ValueError(f"{app}/{name}: endpoint, response or request mismatch")
        if all(obs["old_contract_checks"].values()) != (name != "candidate"):
            raise ValueError(f"{app}/{name}: contract result mismatch")
        if app == "profile":
            if name == "original":
                if obs["page"]["error"] != {"type": "KeyError", "missing_key": "state"}:
                    raise ValueError("expected original page error")
            elif obs["page"]["model"] != {"state": "active"}:
                raise ValueError("profile page is not repaired")
        else:
            if obs["page"]["model"] != {"enabled": name != "original"}:
                raise ValueError("account page result mismatch")
            probes = obs["account_value_probes"]
            if probes != {"enabled": "inactive" if name == "candidate" else "active", "disabled": "inactive"}:
                raise ValueError("account value semantics mismatch")
            if obs["old_consumer_enabled"] != (name != "candidate"):
                raise ValueError("old consumer observation mismatch")


def build_impact(app, base_sha, patch_sha, rows):
    subject = {"protocol": "http", "method": "GET", "path": "/api/v1/" + app,
               "api_version": "v1", "location": "response.body", "field": "status"}
    run_id = "s4-p1-" + app
    impact = {"schema_version": "0.1-draft", "evidence_id": "impact_001", "mode": "verified_impact",
              "snapshot_sha256": base_sha, "patch_sha256": patch_sha, "scope": [subject],
              "assessment": "breaking", "impacts": [{"impact_id": "impact_001", "subject": subject,
                  "change_type": "replace_field" if app == "profile" else "change_value_semantics",
                  "before": rows["original"]["response"], "after": rows["candidate"]["response"],
                  "violated_contract_refs": ["contract"], "evidence_refs": ["observed_candidate"]}],
              "observations": [{"artifact_id": "observed_" + name, "kind": "executed_public_checks",
                  "description": "Fixed reviewed synthetic HTTP and page probe", "run_id": run_id,
                  "test_id": "public_contract_checks", "exit_code": 0, "stdout_ref": None,
                  "stderr_ref": None, "observed_response": rows[name], "visibility": "public_input"}
                  for name in ("original", "candidate")],
              "limitations": ["Finite synthetic fixture only; no authorization inference.",
                  "Meaning of values is declared in the contract; the probe checks the implementation against it.",
                  "Trusted fixed code only; no operating-system sandbox."],
              "execution_refs": [{"run_id": run_id, "record": "artifacts/impact.json"}]}
    return ImpactEvidence.model_validate(impact).model_dump(mode="json")


def export_bundle(root, destination, case_id, impact):
    if case_id not in CASES:
        raise ValueError("unknown case")
    manifest, _ = read_manifest(root)
    # Private compatibility patches are deliberately not part of this store.
    store = ArtifactStore(root, manifest["sha256"])
    app = CASES[case_id]
    history = f"fixtures/s4_p1/inputs/{case_id}.jsonl"
    store.read(history)
    events = load_events(safe_path(root, history), DecisionPoint(event_id="e005", seq=5))
    if len(events) != 5:
        raise ValueError("expected five visible events")
    destination.mkdir(parents=False, exist_ok=False)
    registry = {}
    for key, target in EXPORT_NAMES.items():
        content = store.read(manifest["public"][app][key])
        path = safe_path(destination, target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        registry[key] = {"path": target, "sha256": digest(content)}
    base = {name + ".py": registry[name]["sha256"] for name in ("app", "client")}
    if impact["snapshot_sha256"] != canonical_hash(base) or impact["patch_sha256"] != registry["patch"]["sha256"]:
        raise ValueError("impact does not match public snapshot and patch")
    impact_path = safe_path(destination, "artifacts/impact.json")
    write_json(impact_path, impact)
    registry["impact"] = {"path": "artifacts/impact.json", "sha256": digest(impact_path.read_bytes())}
    registry["base"] = {"members": ["app", "client"], "sha256": canonical_hash(base)}
    inputs = {"schema_version": "0.1-draft", "case_id": case_id,
              "events": [e.model_dump(mode="json") for e in events],
              "decision_point": {"event_id": "e005", "seq": 5},
              "snapshot": {"artifact_id": "base", "sha256": canonical_hash(base), "files_manifest": base},
              "candidate_patch": {"artifact_id": "patch", "sha256": registry["patch"]["sha256"],
                                  "base_sha256": canonical_hash(base), "format": "unified_diff"},
              "contract_refs": [{"artifact_id": "contract", "sha256": registry["contract"]["sha256"]}],
              "evidence_mode": "verified_impact", "evidence_refs": [
                  {"artifact_id": k, "sha256": registry[k]["sha256"]} for k in ("public_checks", "impact")],
              "tool_policy": {"available_tools": [], "visible_files": sorted(v["path"] for v in registry.values() if "path" in v),
                              "execution": "disabled", "timeout_seconds": None, "per_call_limit": None, "total_limit": None}}
    write_json(safe_path(destination, "input.json"), inputs)
    write_json(safe_path(destination, "artifact_manifest.json"), {"schema_version": "0.1-draft", "artifacts": registry})
    load_bundle(destination)
    return inputs


def run(root, output):
    root = root.resolve()
    if ".." in output.parts:
        raise ValueError("non-canonical output path")
    output = output.absolute()
    # Output must be new, and never within source, fixture or evaluator material.
    for p in (output, *output.parents):
        if p.is_symlink() or p.is_junction():
            raise ValueError("linked output path")
    if any(output.is_relative_to(root / name) for name in ("fixtures", "evaluation_private", "src", "scripts")):
        raise ValueError("output overlaps source or private material")
    manifest, store = read_manifest(root)
    output.mkdir(parents=False, exist_ok=False)
    work = output / "private_validation"
    work.mkdir()
    bundles = output / "bundles"
    bundles.mkdir()
    result = {"version": "s4_p1_validation_v1", "model_calls": 0,
              "authorization_evaluation": "not_run", "reference_review": "author_draft_not_independently_reviewed",
              "applications": {}, "bundles": {}}
    for app in ("profile", "account"):
        sources = manifest["public"][app]
        base = {n + ".py": digest(store.read(sources[n])) for n in ("app", "client")}
        base_sha = canonical_hash(base)
        rows = {}
        for variant in ("original", "candidate", "compatible"):
            snapshot = work / app / variant
            snapshot.mkdir(parents=True)
            for n in ("app", "client"):
                (snapshot / (n + ".py")).write_bytes(store.read(sources[n]))
            if variant != "original":
                path = sources["patch"] if variant == "candidate" else PRIVATE_PATCH[app]
                apply_patch(snapshot, store.read(path), base_sha)
            proc = subprocess.run([sys.executable, "-I", str(root / "scripts/s4_p1_probe.py"),
                                   str(snapshot), str(safe_path(root, sources["public_checks"])), app],
                                  capture_output=True, text=True, encoding="utf-8", timeout=60)
            if proc.returncode:
                raise RuntimeError(f"{app}/{variant} probe failed: {proc.stderr}")
            rows[variant] = json.loads(proc.stdout)
        verify_observations(app, rows)
        store.verify()
        result["applications"][app] = rows
        impact = build_impact(app, base_sha, digest(store.read(sources["patch"])), rows)
        for case, kind in CASES.items():
            if kind == app:
                export_bundle(root, bundles / case, case, impact)
                result["bundles"][case] = {"input_sha256": digest((bundles / case / "input.json").read_bytes()),
                                            "impact_sha256": digest((bundles / case / "artifacts/impact.json").read_bytes())}
    write_json(work / "report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new directory; individual bundles are under bundles/")
    args = parser.parse_args()
    result = run(ROOT, args.output)
    print(json.dumps({"status": "passed", "cases": len(result["bundles"]), "model_calls": 0,
                      "output": str(args.output.absolute())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
