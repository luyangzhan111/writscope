"""Explicit offline source/evidence package and fresh-directory recovery checks."""
import argparse
import asyncio
from importlib.metadata import distributions
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

from scripts import public_release
from scripts.s4_r5_acceptance import ROOT, SCENARIOS, check_case, check_version, offline_guard, write
from scripts.s4_r4_replay import read_json, require
from writscope.artifacts import digest

ROOT_FILES = ('README.md', 'AGENTS.md', 'LICENSE', 'pyproject.toml', 'uv.lock', '.gitignore')
SOURCE_DIRS = ('src', 'scripts', 'tests', 'prompts', 'specs', 'docs', 'fixtures', 'evaluation_private')
CURATED_DIRS = ('reports/m1-2026-10-18', 'reports/m2a-2026-10-20', 'reports/m2b-2026-10-20', 'reports/public-v0.1')
FORBIDDEN = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.aws', '.codex', '.agents'}


def safe_target(root, name):
    require(isinstance(name, str) and name and '\\' not in name, 'invalid archive name')
    parts = name.split('/')
    require(not PurePosixPath(name).is_absolute() and all(p not in ('', '.', '..') and ':' not in p for p in parts),
            'unsafe archive path')
    require(not any(p in FORBIDDEN or p.startswith('.env') for p in parts), 'forbidden archive member')
    target = Path(root).joinpath(*parts)
    require(not any(p.is_symlink() or p.is_junction() for p in (target, *target.parents)), 'linked recovery path')
    require(target.resolve().is_relative_to(Path(root).resolve()), 'archive path escapes destination')
    return target


def build(suite, package):
    suite, package = Path(suite).resolve(), Path(package).resolve()
    check_version(suite)
    require(not package.exists() and not package.is_relative_to(suite), 'new separate package directory required')
    require(suite.is_relative_to(ROOT/'reports/local'), 'suite outside explicit local evidence tree')
    paths = {ROOT/name for name in ROOT_FILES}
    for folder in (*SOURCE_DIRS, *CURATED_DIRS):
        paths.update(p for p in (ROOT/folder).rglob('*') if p.is_file() and
                     not any(part in FORBIDDEN for part in p.relative_to(ROOT).parts) and
                     p.suffix not in ('.pyc', '.pyo'))
    paths.update(p for p in suite.rglob('*') if p.is_file())
    rows = []
    for path in sorted(paths):
        name = path.relative_to(ROOT).as_posix()
        safe_target(ROOT, name)
        raw = path.read_bytes()
        require(not public_release.findings(name, raw), 'credential-like content in selected package: '+name)
        rows.append({'path': name, 'bytes': len(raw), 'sha256': digest(raw),
                     'role': 'evaluator_only' if name.startswith('evaluation_private/') else
                             'saved_evidence' if path.is_relative_to(suite) else 'source_or_curated_history'})
    package.mkdir(parents=True)
    with zipfile.ZipFile(package/'delivery.zip', 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for row in rows:
            raw = (ROOT/row['path']).read_bytes()
            require(digest(raw) == row['sha256'], 'input changed during packaging')
            info = zipfile.ZipInfo(row['path'], date_time=(2026, 11, 2, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
    manifest = {'version': 's4_r5_delivery_v1', 'archive_sha256': digest((package/'delivery.zip').read_bytes()),
                'suite': suite.relative_to(ROOT).as_posix(), 'files': rows,
                'scope': 'local offline source plus evidence; evaluator-only data must never enter method requests',
                'actual_model_calls': 0, 'real_transport_allowed': False,
                'dependencies': 'Python 3.12 and installed uv.lock dependencies; no venv or credentials bundled'}
    write(package/'manifest.json', manifest)
    return {'files': len(rows), 'uncompressed_bytes': sum(r['bytes'] for r in rows), 'archive_sha256': manifest['archive_sha256']}


def checked_members(package, destination):
    package = Path(package)
    manifest = read_json(package/'manifest.json')
    require(manifest['version'] == 's4_r5_delivery_v1' and
            digest((package/'delivery.zip').read_bytes()) == manifest['archive_sha256'], 'delivery archive hash mismatch')
    rows = manifest['files']; expected = {r['path']: r for r in rows}
    require(0 < len(rows) == len(expected) <= 20000 and sum(r['bytes'] for r in rows) <= 2_000_000_000,
            'invalid delivery inventory or size')
    # Validate every name and byte before any extraction or directory creation.
    with zipfile.ZipFile(package/'delivery.zip') as archive:
        require(len(archive.namelist()) == len(expected) and set(archive.namelist()) == set(expected), 'archive inventory mismatch')
        for name, row in expected.items():
            safe_target(destination, name)
            info = archive.getinfo(name)
            require(0 <= row['bytes'] == info.file_size <= 128_000_000 and
                    (info.external_attr >> 16) & 0o170000 != 0o120000, 'unsupported archive member')
            require(digest(archive.read(name)) == row['sha256'], 'delivery member hash mismatch: '+name)
    return manifest


def restore(package, destination):
    destination = Path(destination)
    require(not destination.exists(), 'recovery destination must not exist')
    manifest = checked_members(package, destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(Path(package)/'delivery.zip') as archive:
        for row in manifest['files']:
            path = safe_target(destination, row['path'])
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream: stream.write(archive.read(row['path']))
    verify_restored(package, destination)
    return {'restored_files': len(manifest['files']), 'actual_model_calls': 0}


def verify_restored(package, destination):
    manifest = checked_members(package, destination)
    for row in manifest['files']:
        path = safe_target(destination, row['path'])
        require(path.is_file() and digest(path.read_bytes()) == row['sha256'], 'restored bytes changed: '+row['path'])
    return manifest


def recovery_checks(suite, output):
    import writscope
    from scripts import s4_r3
    require(Path(writscope.__file__).resolve().is_relative_to(ROOT) and
            Path(s4_r3.__file__).resolve().is_relative_to(ROOT), 'imports escaped restored source tree')
    check_version(suite)
    recomputed = []
    with offline_guard():
        for name in SCENARIOS:
            report = check_case(suite, name, 'restored-evaluation.json')
            require(report == read_json(Path(suite)/name/'evaluation.json'), 'report changed after relocation: '+name)
            recomputed.append(name)
            print('restored report', name, flush=True)
        demo = asyncio.run(public_release.demo(ROOT))
        legacy = asyncio.run(public_release.replay(ROOT))
        require(demo['new_model_calls'] == legacy['new_model_calls'] == 0, 'unexpected calls')
        touched = []
        try: s4_r3.start_real(credential_reader=lambda: touched.append('key'), provider=lambda: touched.append('send'))
        except PermissionError: pass
        else: raise AssertionError('real sending enabled')
        require(not touched, 'real callback invoked')
    record = {'version': 's4_r5_recovery_check_v1', 'status': 'passed', 'recomputed_scenarios': recomputed,
        'demo': demo, 'historical_replay': legacy, 'source_imports': 'restored_tree',
        'python': sys.version, 'dependency_versions': sorted({(d.metadata['Name'], d.version) for d in distributions()}),
        'environment_installation': 'reused_local_venv_dependencies_no_new_install',
        'actual_model_calls': 0, 'real_transport_allowed': False, 'raw_evidence_modified': False}
    write(output, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'restore', 'verify-restored', 'recovery-check'))
    parser.add_argument('--suite', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with offline_guard():
        if args.command == 'build': result = build(args.suite, args.package)
        elif args.command == 'restore': result = restore(args.package, args.destination)
        elif args.command == 'verify-restored': result = {'verified_files': len(verify_restored(args.package, args.destination)['files'])}
        else:
            result = recovery_checks(args.suite, args.output)
            result = {'status': result['status'], 'recomputed_scenarios': len(result['recomputed_scenarios']), 'actual_model_calls': 0}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
