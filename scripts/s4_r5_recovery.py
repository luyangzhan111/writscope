"""Windows-compatible offline delivery recovery addendum; no method execution.

Create asyncio's internal wake-up sockets before the connection guard. All
demo/replay coroutines execute under the same fail-closed connection guard.
The frozen delivery/runtime sources and previous recovery failure remain intact.
"""
import argparse
import asyncio
from importlib.metadata import distributions
from pathlib import Path
import sys

from scripts import public_release, s4_r3
from scripts.s4_r5_acceptance import ROOT, SCENARIOS, check_case, check_version, offline_guard, write
from scripts.s4_r4_replay import read_json, require
from writscope.artifacts import digest


def legacy_checks():
    # Windows socketpair uses a local connect to initialize the event loop.
    # No task/provider is run while this loop is being created.
    with asyncio.Runner() as runner:
        with offline_guard():
            demo = runner.run(public_release.demo(ROOT))
            legacy = runner.run(public_release.replay(ROOT))
            require(demo['new_model_calls'] == legacy['new_model_calls'] == 0, 'unexpected calls')
            touched = []
            try:
                s4_r3.start_real(credential_reader=lambda: touched.append('key'),
                                provider=lambda: touched.append('send'))
            except PermissionError:
                pass
            else:
                raise AssertionError('real sending enabled')
            require(not touched, 'real callback invoked')
    return demo, legacy


def recover(suite, output, *, reuse_recomputed=False):
    import writscope
    require(Path(writscope.__file__).resolve().is_relative_to(ROOT) and
            Path(s4_r3.__file__).resolve().is_relative_to(ROOT), 'imports escaped restored source tree')
    suite = Path(suite)
    check_version(suite)
    evidence = {}
    with offline_guard():
        for name in SCENARIOS:
            path = suite/name/'restored-evaluation.json'
            report = read_json(path) if reuse_recomputed else check_case(suite, name, path.name)
            require(report == read_json(suite/name/'evaluation.json'), 'restored report differs: '+name)
            evidence[name] = digest(path.read_bytes())
            print('checked restored report', name, flush=True)
    demo, legacy = legacy_checks()
    result = {
        'version': 's4_r5_recovery_addendum_v1', 'status': 'passed',
        'recomputed_scenarios': list(SCENARIOS), 'recomputed_reports_sha256': evidence,
        'report_check': ('compared_existing_full_replays_from_preserved_initial_recovery_attempt'
                         if reuse_recomputed else 'fresh_full_replay'),
        'demo': demo, 'historical_replay': legacy, 'source_imports': 'restored_tree',
        'addendum_source_sha256': digest(Path(__file__).read_bytes()),
        'python': sys.version,
        'dependency_versions': sorted({(d.metadata['Name'], d.version) for d in distributions()}),
        'environment_installation': 'reused_local_venv_dependencies_no_new_install',
        'event_loop_setup': 'local_asyncio_wakeup_sockets_before_guard_no_provider_tasks',
        'actual_model_calls': 0, 'real_transport_allowed': False, 'raw_evidence_modified': False,
        'verification_after_recovery': 'separate_verify_restored_required',
    }
    write(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reuse-recomputed', action='store_true',
                        help='only finish a preserved attempt that already wrote every full replay report')
    args = parser.parse_args()
    result = recover(args.suite, args.output, reuse_recomputed=args.reuse_recomputed)
    print(result['status'])


if __name__ == '__main__':
    main()
