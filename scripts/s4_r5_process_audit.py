"""Read-only adjudication of R5's specifically observed isolation exit.

The frozen runner is unchanged. This accepts only the recorded whitelist
rejection with one completed mock receipt, never arbitrary nonzero exits.
"""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

from scripts.s4_r5_acceptance import check_case, check_version, read_json, require, write
from writscope.artifacts import digest


def audit(suite, evaluation_output=None):
    suite = Path(suite)
    descriptor = check_version(suite)
    target = suite/'isolation'
    process, fault, plan = [read_json(target/name) for name in ('process.json', 'fault-fired.json', 'candidate-plan.json')]
    require(process['exit_code'] == 1 and process['stderr'].rstrip().endswith(
        'ValueError: bundle file set is not the public whitelist'), 'not the observed isolation failure')
    require(fault == {'scenario': 'isolation', 'point': 'after_first_validation', 'synthetic_only': True},
            'isolation injection evidence mismatch')
    require(plan['base']['schedule']['plan']['jobs'] == descriptor['jobs'] and
            read_json(target/'run/plan.json') == plan, 'isolation planned denominator changed')
    with closing(sqlite3.connect((target/'run/journal.sqlite').resolve().as_uri()+'?mode=ro', uri=True)) as db:
        require(db.execute('SELECT id,status FROM attempt ORDER BY id').fetchall() == [(1, 'completed')],
                'unexpected additional or missing attempts')
    output = evaluation_output or ('process-audit-evaluation.json' if (target/'evaluation.json').exists() else 'evaluation.json')
    require(Path(output).name == output and output.endswith('.json'), 'evaluation output must be a new JSON filename')
    result = check_case(suite, 'isolation', output)
    require(result['status'] == 'verification_rejected' and not result['reference_read'] and
            result['error'] == 'bundle file set is not the public whitelist', 'unexpected evaluator outcome')
    return {'version': 's4_r5_isolation_exit_audit_v1', 'status': 'expected_isolation_rejection_nonzero_exit_recorded',
            'planned_jobs': len(descriptor['jobs']), 'worker_exit_code': 1, 'saved_mock_attempts': 1,
            'root_summary_present': (target/'run/result.json').exists(), 'reference_read': False,
            'source_sha256': digest(Path(__file__).read_bytes()), 'actual_model_calls': 0,
            'boundary': 'post-run audit only; fixed runner and original error/trace remain unchanged; no retry or scoring'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evaluation-output', help='new derived report filename inside the isolation scenario')
    args = parser.parse_args()
    result = audit(args.suite, args.evaluation_output)
    write(args.output, result)
    print(result['status'])


if __name__ == '__main__': main()
