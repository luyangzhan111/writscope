"""Prepare and inspect the first public snapshot without calling a model.

Historical evidence stays byte-identical. Only the verifier's in-memory view
of a saved launch path may be relocated; this is recorded separately.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from unittest.mock import patch
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = Path("reports/public-v0.1")
SEEDS = (
    "s3-development-v8-live-20261026", "s3-development-v8-live-report-20261026",
    "s3-development-v7-live-20261026", "s3-development-v7-live-report-20261026",
    "s3-development-v7-preflight-final3-20261026",
    "s3-development-final-audit-20261027",
    "s3-4-lifecycle-v3-20261025", "s3-4-c001-historical-20261025-verified",
    "s3-4-c002-historical-20261025-final", "s3-4-c003-isolated-20261024-final",
    "s3-4-c001-state-v2-20261025", "s3-4-c002-state-v2-20261025",
)
PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider_token": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "credential_assignment": re.compile(
        rb"(?i)(?:api[_-]?key|access[_-]?token|authorization)[\"']?\s*[:=]\s*[\"'](?:Bearer\s+)?[A-Za-z0-9_-]{24,}[\"']"),
}


def digest(data):
    return sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_path(root, name):
    """Reject unsafe archive members before touching the destination."""
    rel = PurePosixPath(name)
    if (not name or "\\" in name or rel.is_absolute() or
            any(part in {"", "..", "."} or ":" in part for part in name.split("/"))):
        raise ValueError("unsafe archive member")
    lexical = root / Path(*rel.parts)
    if any(path.is_symlink() for path in [lexical, *lexical.parents] if path != root.parent):
        raise ValueError("archive paths cannot traverse symlinks")
    target = lexical.resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("archive path escapes destination")
    return target


def findings(name, data):
    return [{"path": name, "kind": key} for key, pattern in PATTERNS.items() if pattern.search(data)]


def evidence_files(root):
    """Explicit historical seeds plus existing static test/runner dependencies.

    JSON references expand to complete local run directories. Generated test
    directories are not selected merely because they exist on this machine.
    """
    selected = set()

    def add(path):
        path = path.resolve()
        local = (root / "reports/local").resolve()
        if not path.exists() or not path.is_relative_to(local) or path == local:
            return
        relative = path.relative_to(local)
        top = local / relative.parts[0]
        if top.is_symlink():
            raise ValueError("evidence symlinks are unsupported")
        files = top.rglob("*") if top.is_dir() else [top]
        for item in files:
            if item.is_symlink():
                raise ValueError("evidence symlinks are unsupported")
            if item.is_file():
                selected.add(item.relative_to(root).as_posix())
        if top.is_dir():
            for suffix in ("-runner-source.py", "-report-source.py"):
                add(top.with_name(top.name + suffix))
            if not top.name.endswith("-report"):
                add(top.with_name(top.name + "-report"))

    for seed in SEEDS:
        add(root / "reports/local" / seed)
    for source in sorted((root / "tests").glob("*.py")) + sorted((root / "scripts").glob("*.py")):
        if source.name == Path(__file__).name:
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.replace("\\", "/")
                if value.startswith("reports/local/"):
                    add(root / value)
                elif value.startswith(("s3-", "m3")):
                    add(root / "reports/local" / value)
    # Private evaluator JSON may name a saved observation by basename rather
    # than a full path. It is read only by this packaging step, never a method.
    pending = set(selected) | {p.relative_to(root).as_posix()
                               for p in (root / "evaluation_private").rglob("*.json")}
    seen = set()
    while pending:
        name = pending.pop()
        seen.add(name)
        path = root / name
        if path.suffix not in {".json", ".jsonl", ".md"}:
            continue
        text = path.read_text(encoding="utf-8").replace("\\\\", "/").replace("\\", "/")
        for value in re.findall(r"reports/local/[^\s\"'<>`\],}]+", text):
            add(root / value.rstrip(".;)"))
        for value in re.findall(r'"((?:s3-|m3)[^"/]+)"', text):
            add(root / "reports/local" / value)
        pending |= selected - seen
    return sorted(selected)


def build(root, *, refresh=False):
    output = root / BUNDLE
    if output.exists() and not refresh:
        raise ValueError("public evidence destination already exists")
    if output.exists():
        checked_members(root)
    names = evidence_files(root)
    rows, issues = [], []
    for name in names:
        data = (root / name).read_bytes()
        issues.extend(findings(name, data))
        rows.append({"path": name, "sha256": digest(data), "bytes": len(data)})
    if issues:
        raise ValueError("potential credentials in evidence: " + json.dumps(issues))
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "evidence.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for row in rows:
            item = zipfile.ZipInfo(row["path"], date_time=(2026, 10, 27, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            z.writestr(item, (root / row["path"]).read_bytes())
    manifest = {"version": "public_evidence_v1", "new_model_calls": 0,
                "selection": "static_test_runner_dependencies_and_declared_representative_evidence",
                "original_bytes_preserved": True, "archive_sha256": digest(archive.read_bytes()),
                "file_count": len(rows), "uncompressed_bytes": sum(r["bytes"] for r in rows),
                "files": rows, "credential_scan_findings": issues,
                "limitations": ["pattern_scan_is_not_a_proof_of_absence",
                                "historical_machine_paths_and_consumed_authorization_receipts_retained",
                                "selected_development_evidence_not_all_local_experiments",
                                "author_review_only_no_method_ranking"]}
    write(output / "manifest.json", manifest)
    return {key: manifest[key] for key in ("file_count", "uncompressed_bytes", "archive_sha256")}


def checked_members(root):
    folder = root / BUNDLE
    manifest = read(folder / "manifest.json")
    archive = folder / "evidence.zip"
    if manifest.get("version") != "public_evidence_v1" or digest(archive.read_bytes()) != manifest["archive_sha256"]:
        raise ValueError("evidence archive hash mismatch")
    expected = {row["path"]: row for row in manifest["files"]}
    if len(expected) != manifest["file_count"] or len(expected) != len(manifest["files"]):
        raise ValueError("duplicate or inconsistent manifest entries")
    result = []
    with zipfile.ZipFile(archive) as z:
        if len(z.namelist()) != len(expected) or set(z.namelist()) != set(expected):
            raise ValueError("archive inventory mismatch")
        for name, row in expected.items():
            if not name.startswith("reports/local/"):
                raise ValueError("evidence must stay under reports/local")
            safe_path(root, name)
            data = z.read(name)
            if digest(data) != row["sha256"] or len(data) != row["bytes"]:
                raise ValueError("evidence member hash mismatch: " + name)
            result.append((name, data))
    return result


def restore(root):
    members = checked_members(root)
    # Check every conflict before any writes; never overwrite different bytes.
    for name, data in members:
        target = safe_path(root, name)
        if target.exists() and (not target.is_file() or target.read_bytes() != data):
            raise ValueError("restore conflict: " + name)
    restored = 0
    for name, data in members:
        target = safe_path(root, name)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            restored += 1
    return {"verified_files": len(members), "restored_files": restored, "new_model_calls": 0}


@contextmanager
def relocated_claims(root):
    """Read-only, disclosed compatibility view, never a new paid authorization."""
    from scripts import s3_acceptance_v7, s3_acceptance_v8, s3_coverage_v1, s3_common_real_v2
    from writscope.providers.deepseek import DeepSeekProvider
    changes, originals = [], []

    async def forbidden(*args, **kwargs):
        raise AssertionError("public replay cannot call a real provider")

    def wrap(original):
        def reader(path):
            value = original(path)
            if Path(path).parent.name.endswith("paid-claims") and isinstance(value, dict):
                if set(value) != {"plan_sha256", "output", "authorization_ref"}:
                    raise ValueError("unexpected launch claim fields")
                if Path(path).stem != value["plan_sha256"]:
                    raise ValueError("launch claim plan mismatch")
                parts = re.split(r"[\\/]+", value["output"])
                if len(parts) < 4 or parts[-3:-1] != ["reports", "local"]:
                    raise ValueError("unrecognized historical launch path")
                run = root / "reports/local" / parts[-1]
                manifest = read(run / "run.json")
                if (manifest["plan_sha256"] != value["plan_sha256"] or
                        manifest["authorization_ref"] != value["authorization_ref"]):
                    raise ValueError("launch claim does not match saved run")
                changes.append({"claim": Path(path).relative_to(root).as_posix(),
                                "original_output": value["output"], "relocated_output": str(run.resolve())})
                return {**value, "output": str(run.resolve())}
            return value
        return reader

    try:
        for module in (s3_acceptance_v7, s3_acceptance_v8, s3_coverage_v1, s3_common_real_v2):
            originals.append((module, module.read))
            module.read = wrap(module.read)
        with patch.object(DeepSeekProvider, "generate", forbidden):
            yield changes
    finally:
        for module, original in originals:
            module.read = original


async def replay(root):
    restore(root)
    from scripts import s3_acceptance_v7, s3_acceptance_v8, s3_acceptance_report_v8

    with relocated_claims(root) as changes:
        success = await s3_acceptance_v8.verify(root,
            root / "reports/local/s3-development-v8-preflight-final-20261026",
            root / "reports/local/s3-development-v8-live-20261026")
        failure = await s3_acceptance_v7.verify(root,
            root / "reports/local/s3-development-v7-preflight-final3-20261026",
            root / "reports/local/s3-development-v7-live-20261026")
        coverage = await s3_acceptance_report_v8.prior_coverage(root)
        output = root / "reports/local" / ("public-replay-report-" + uuid4().hex)
        report = await s3_acceptance_report_v8.report(root,
            root / "reports/local/s3-development-v8-preflight-final-20261026", output,
            run_dir=root / "reports/local/s3-development-v8-live-20261026")
    # Verify originals again after the compatibility view and report generation.
    restore(root)
    result = {"version": "public_portable_replay_v1", "new_model_calls": 0,
              "v8_saved_calls": success["actual_model_calls"], "v8_status": success["status"],
              "v7_saved_calls": failure["actual_model_calls"], "v7_status": failure["status"],
              "v7_failures": failure["failures"], "nine_job_coverage": coverage,
              "s3_development_exit": report["s3_development_exit"],
              "s3_full_exit": report["s3_full_exit"], "claim_path_view_changes": changes,
              "evidence_bytes_after_replay": "unchanged", "report": output.relative_to(root).as_posix()}
    write(output / "portable-replay.json", result)
    return result


async def demo(root):
    restore(root)
    from scripts import s3_acceptance_v8 as runner
    from scripts.s3_acceptance_simulation_v8 import simulate
    token = uuid4().hex
    prepared = root / "reports/local" / ("public-demo-preflight-" + token)
    output = root / "reports/local" / ("public-demo-" + token)
    runner.preflight(root, prepared)
    manifest = await simulate(root, prepared, output, "v8_lookup")
    summary = read(output.parent / (output.name + "-report") / "summary.json")
    return {"execution_kind": "offline_simulation", "new_model_calls": 0,
            "status": manifest["status"], "scripted_attempts": manifest["simulated_provider_attempts"],
            "lookup_evidence": summary["rows"][0]["lookup_evidence"],
            "model_performance_metrics": summary["metrics"],
            "report": (output.parent / (output.name + "-report") / "report.md").relative_to(root).as_posix()}


def candidate_files(root):
    data = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root)
    return sorted({name.decode("utf-8") for name in data.split(b"\0") if name})


def audit(root):
    issues, machine_paths = [], []
    names = candidate_files(root)
    for name in names:
        path = safe_path(root, name)
        if not path.is_file():
            continue
        if path.is_symlink() or path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
            issues.append({"path": name, "kind": "excluded_configuration"})
        data = path.read_bytes()
        issues.extend(findings(name, data))
        if re.search(rb"[CD]:[\\/](?:Users|AI-Project)", data):
            machine_paths.append(name)
    # Inspect historical blobs without printing possible credentials.
    objects = subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=root).decode().splitlines()
    history_blobs = 0
    ids = [row.split(" ", 1)[0] for row in objects]
    packed = subprocess.check_output(["git", "cat-file", "--batch"], cwd=root,
                                     input=("\n".join(ids) + "\n").encode())
    offset = 0
    for oid in ids:
        end = packed.index(b"\n", offset)
        header = packed[offset:end].split()
        size = int(header[2])
        data = packed[end+1:end+1+size]
        offset = end + size + 2
        if header[1] == b"blob":
            history_blobs += 1
            issues.extend(findings("git-object:" + oid, data))
    members = checked_members(root)
    for name, data in members:
        issues.extend(findings(name, data))
    result = {"version": "public_snapshot_audit_v1", "candidate_files": len(names),
              "history_blobs_scanned": history_blobs, "archived_evidence_files": len(members),
              "findings": issues, "historical_machine_path_files": machine_paths,
              "scan_scope": list(PATTERNS), "new_model_calls": 0,
              "limitation": "pattern_scan_not_a_security_guarantee; historical_paths_retained_in_evidence"}
    if issues:
        raise ValueError("public candidate scan findings: " + json.dumps(issues))
    return result


def snapshot(root, destination):
    """Export the current candidate without Git internals, local runs, or envs."""
    if destination.exists():
        raise ValueError("snapshot destination already exists")
    names = candidate_files(root)
    if destination.resolve().is_relative_to(root.resolve()) and not destination.resolve().is_relative_to((root / ".work").resolve()):
        raise ValueError("in-repository snapshots must be under ignored .work")
    for name in names:
        source = safe_path(root, name)
        if not source.is_file() or source.is_symlink():
            raise ValueError("unsupported candidate: " + name)
    destination.mkdir(parents=True)
    for name in names:
        target = safe_path(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    return {"snapshot_files": len(names), "destination": str(destination), "new_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build-evidence", "restore", "demo", "replay", "audit", "snapshot"))
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--refresh", action="store_true", help="refresh only a verified local candidate evidence archive")
    args = parser.parse_args()
    root = ROOT.resolve()
    commands = {"build-evidence": lambda: build(root, refresh=args.refresh), "restore": lambda: restore(root),
                "demo": lambda: asyncio.run(demo(root)), "replay": lambda: asyncio.run(replay(root)),
                "audit": lambda: audit(root), "snapshot": lambda: snapshot(root, args.destination.resolve())}
    if args.command == "snapshot" and args.destination is None:
        parser.error("snapshot requires --destination")
    result = commands[args.command]()
    if args.report:
        write(args.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
