"""Execute reviewed P3 fixtures and export 30 public bundles, without a model.

Only fixed hash-checked fixture code/patches are executed. This is not a sandbox.
Private verification copies are siblings of public bundles and must remain private.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from writscope.artifacts import ArtifactStore, apply_patch, canonical_hash, digest, safe_path, write_json
from writscope.contracts import ImpactEvidence, load_bundle, parse_json
from writscope.events import DecisionPoint, load_events

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "fixtures/s4_p3/manifest.json"
MANIFEST_SHA256 = "a6b50af587fb1b93e13d762ef6994ef3f845b2a08958881cf5c9b161abe3ddcf"
EXPORT_NAMES = {"app": "artifacts/base/app.py", "client": "artifacts/base/client.py",
                "patch": "artifacts/candidate.patch", "contract": "artifacts/contract.md",
                "public_checks": "artifacts/public_tests/contract.py"}


def read_manifest(root):
    raw = safe_path(root, MANIFEST).read_bytes()
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError("P3 manifest hash mismatch")
    manifest = parse_json(raw.decode("utf-8"))
    if manifest["version"] != "s4_p3_inputs_v1" or set(manifest["cases"]) != {f"s4_{n:03d}" for n in range(1, 31)}:
        raise ValueError("unexpected P3 manifest")
    store = ArtifactStore(root, manifest["sha256"] | manifest["private_patches"])
    store.verify()
    return manifest, store


def verify_observations(name, routes, rows):
    expected_business = sorted("GET " + route for route in routes)
    page_path = "/api/v1/account" if "/api/v1/account" in routes else "/api/v1/profile"
    for variant, obs in rows.items():
        if obs["business_routes"] != expected_business or obs["page_requests"] != [{"method": "GET", "path": page_path}]:
            raise ValueError(f"{name}/{variant}: business routes or request count mismatch")
        if set(obs["routes"]) != set(routes):
            raise ValueError("route coverage mismatch")
        if variant in {"original", "compatible"} and any(not all(r["old_contract_checks"].values()) for r in obs["routes"].values()):
            raise ValueError(f"{name}/{variant}: old contract failed")
        if variant in {"candidate", "compatible"}:
            model = obs["page"]["model"]
            expected = {"enabled": True} if page_path.endswith("account") else {"state": "active"}
            if obs["page"]["status"] != "succeeded" or model != expected:
                raise ValueError(f"{name}/{variant}: page not repaired")
    if name in {"account", "account_two_fields"}:
        if rows["original"]["value_probes"] != {"enabled": "active", "disabled": "inactive"}:
            raise ValueError("account original value semantics mismatch")
        if rows["candidate"]["value_probes"] != {"enabled": "inactive", "disabled": "inactive"}:
            raise ValueError("account candidate value semantics mismatch")
    if name == "session_semantics":
        old, new = rows["original"]["value_probes"], rows["candidate"]["value_probes"]
        if old != {"enabled_0_session_0":"inactive", "enabled_0_session_1":"inactive",
                   "enabled_1_session_0":"active", "enabled_1_session_1":"active"}:
            raise ValueError("account semantic control baseline mismatch")
        if new != {"enabled_0_session_0":"inactive", "enabled_0_session_1":"active",
                   "enabled_1_session_0":"inactive", "enabled_1_session_1":"active"}:
            raise ValueError("account semantic control candidate mismatch")


def impact_from(name, routes, base_sha, patch_sha, rows):
    run_id = "s4-p3-" + name
    scope, impacts = [], []
    for route in routes:
        subject = {"protocol": "http", "method": "GET", "path": route, "api_version": "v1",
                   "location": "response.body", "field": "status"}
        scope.append(subject)
        before = rows["original"]["routes"][route]["response"]
        after_row = rows["candidate"]["routes"][route]
        after = after_row["response"]
        if all(after_row["old_contract_checks"].values()):
            continue
        if "status" in before and "status" not in after and "state" in after:
            change = "replace_field"
        elif "status" in before and "status" in after and before["status"] != after["status"]:
            change = "change_value_semantics"
        else:
            raise ValueError("failed contract has unsupported or unresolved concrete impact")
        impacts.append({"impact_id": f"impact_{len(impacts)+1:03d}", "subject": subject,
                        "change_type": change, "before": before, "after": after,
                        "violated_contract_refs": ["contract"], "evidence_refs": ["observed_candidate"]})
    impact = {"schema_version": "0.1-draft", "evidence_id": "impact_001", "mode": "verified_impact",
              "snapshot_sha256": base_sha, "patch_sha256": patch_sha, "scope": scope,
              "assessment": "breaking" if impacts else "compatible", "impacts": impacts,
              "observations": [{"artifact_id": "observed_" + variant, "kind": "executed_public_checks",
                  "description": "Fixed reviewed synthetic HTTP and page probe", "run_id": run_id,
                  "test_id": "public_contract_checks", "exit_code": 0, "stdout_ref": None,
                  "stderr_ref": None, "observed_response": rows[variant], "visibility": "public_input"}
                  for variant in ("original", "candidate")],
              "limitations": ["Finite synthetic fixtures; no authorization inference.",
                  "Value meanings are declared by the public contract, not inferred from code.",
                  "Trusted fixed code only; no operating-system sandbox."],
              "execution_refs": [{"run_id": run_id, "record": "artifacts/impact.json"}]}
    return ImpactEvidence.model_validate(impact).model_dump(mode="json")


def export_bundle(root, destination, case_id, impact):
    manifest, _ = read_manifest(root)
    if case_id not in manifest["cases"]:
        raise ValueError("unknown case ID")
    public_store = ArtifactStore(root, manifest["sha256"])
    variant = manifest["variants"][manifest["cases"][case_id]]
    history_path = f"fixtures/s4_p3/inputs/{case_id}.jsonl"
    raw = public_store.read(history_path)
    raw_events = [parse_json(line) for line in raw.decode("utf-8").splitlines()]
    if not raw_events:
        raise ValueError("empty case history")
    point = DecisionPoint(event_id=raw_events[-1]["event_id"], seq=raw_events[-1]["seq"])
    events = load_events(safe_path(root, history_path), point)
    destination.mkdir(parents=False, exist_ok=False)
    registry = {}
    for key, target in EXPORT_NAMES.items():
        content = public_store.read(variant["public"][key])
        path = safe_path(destination, target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        registry[key] = {"path": target, "sha256": digest(content)}
    base = {name + ".py": registry[name]["sha256"] for name in ("app", "client")}
    if impact["snapshot_sha256"] != canonical_hash(base) or impact["patch_sha256"] != registry["patch"]["sha256"]:
        raise ValueError("impact does not match public source and patch")
    path = safe_path(destination, "artifacts/impact.json")
    write_json(path, impact)
    registry["impact"] = {"path": "artifacts/impact.json", "sha256": digest(path.read_bytes())}
    registry["base"] = {"members": ["app", "client"], "sha256": canonical_hash(base)}
    inputs = {"schema_version": "0.1-draft", "case_id": case_id,
              "events": [e.model_dump(mode="json") for e in events], "decision_point": point.model_dump(),
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
    if any(p.is_symlink() or p.is_junction() for p in (output, *output.parents)):
        raise ValueError("linked output path")
    if any(output.is_relative_to(root / name) for name in ("fixtures", "evaluation_private", "src", "scripts")):
        raise ValueError("output overlaps source or private material")
    manifest, store = read_manifest(root)
    output.mkdir(parents=False, exist_ok=False)
    private = output / "private_validation"
    bundles = output / "bundles"
    private.mkdir(); bundles.mkdir()
    report = {"version": "s4_p3_fixture_validation_v1", "model_calls": 0,
              "authorization_evaluation": "not_run", "variants": {}, "bundles": {}}
    for name, variant in manifest["variants"].items():
        sources = variant["public"]
        base = {n + ".py": digest(store.read(sources[n])) for n in ("app", "client")}
        base_sha = canonical_hash(base)
        rows = {}
        for version in ("original", "candidate", "compatible"):
            snapshot = private / name / version
            snapshot.mkdir(parents=True)
            for n in ("app", "client"):
                (snapshot / (n + ".py")).write_bytes(store.read(sources[n]))
            if version != "original":
                path = sources["patch"] if version == "candidate" else variant["private_patch"]
                apply_patch(snapshot, store.read(path), base_sha)
            proc = subprocess.run([sys.executable, "-I", str(root / "scripts/s4_p3_probe.py"),
                                   str(snapshot), str(safe_path(root, sources["public_checks"])), *variant["routes"]],
                                  capture_output=True, text=True, encoding="utf-8", timeout=60)
            if proc.returncode:
                raise RuntimeError(f"{name}/{version}: probe failed: {proc.stderr}")
            rows[version] = parse_json(proc.stdout)
        verify_observations(name, variant["routes"], rows)
        store.verify()
        impact = impact_from(name, variant["routes"], base_sha, digest(store.read(sources["patch"])), rows)
        report["variants"][name] = {"observations": rows, "impact": impact}
        for case, kind in manifest["cases"].items():
            if kind == name:
                export_bundle(root, bundles / case, case, impact)
                report["bundles"][case] = {"input_sha256": digest((bundles / case / "input.json").read_bytes()),
                                            "impact_sha256": digest((bundles / case / "artifacts/impact.json").read_bytes())}
    write_json(private / "fixture-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(ROOT, args.output)
    print(json.dumps({"status": "passed", "variants": len(result["variants"]),
                      "cases": len(result["bundles"]), "model_calls": 0}))


if __name__ == "__main__":
    main()
