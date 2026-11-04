"""Evaluator-only R4 entry: independently verify first, then open private labels.

Only explicitly synthetic diagnostic counts are emitted. Formal model scoring
and real sending are unavailable; data/authority are not frozen by this module.
"""
import argparse
import json
from pathlib import Path

from scripts.s4_metric_core import aggregate
from scripts.s4_p2 import checked_bundle
from scripts.s4_prompt_candidate import STAGES, adapter, prompt
from scripts.s4_r4_replay import AXES, artifact_hashes, read_json, require, source_hashes, verify
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINDING = ROOT/'evaluation_private/s4_review/r4-evaluator-binding-v1.json'
PRIVATE_FILES = {
    'reference': 'evaluation_private/s4_p3/references.json',
    'audit': 'evaluation_private/s4_p3/audit-report.json',
    'disposition': 'evaluation_private/s4_review/project-disposition-v1.json',
    'policy': 'evaluation_private/s4_review/method-policy-v1.json',
    'owner': 'evaluation_private/s4_review/r1-owner-decisions-20261030.json',
    'package': 'evaluation_private/s4_review/r1-decision-package-20261030.json',
}


def load_reference_package(binding_path):
    """Called only after settled replay. Paths cannot be chosen by a run record."""
    binding = read_json(binding_path)
    require(binding.get('version') == 's4_r4_evaluator_binding_v1' and
            binding.get('paths') == PRIVATE_FILES, 'unsupported evaluator binding')
    raw = {key: (ROOT/path).read_bytes() for key, path in PRIVATE_FILES.items()}
    require(binding['sha256'] == {key: digest(value) for key, value in raw.items()}, 'private version binding mismatch')
    values = {key: json.loads(value) for key, value in raw.items()}
    refs, audit, disp, wrapped, owner, package = (values[key] for key in PRIVATE_FILES)
    policy = wrapped['policy']
    require(refs['version'] == 's4_p3_author_draft_v1' and disp['version'] == 's4_project_disposition_v1' and
            disp['reference_sha256'] == audit['reference_sha256'] == binding['sha256']['reference'] and
            disp['audit_sha256'] == binding['sha256']['audit'] and
            policy['disposition_sha256'] == binding['sha256']['disposition'] and
            wrapped['sha256'] == canonical_hash(policy) and
            owner['draft_package_sha256'] == binding['sha256']['package'], 'reference cross-binding mismatch')
    require(owner['decision_status'] == 'owner_confirmed_limited_scope_not_frozen' and
            owner['decisions']['incremental_conflict'] == 'main_failed_no_valid_main_output_validated_original_terminal_diagnostic_only' and
            owner['decisions']['primary_comparison'] == 'equal_configuration' and
            owner['decisions']['budget_matched'] == 'defer_separate_plan' and
            owner['user_case_by_case_review'] == disp['user_case_review'] == 'not_received' and
            owner['independent_review'] == disp['independent_review'] == policy['independent_review'] ==
            'not_performed_skipped_by_user', 'unsupported review or denominator policy')
    require(policy['prompt_sha256'] == {s: digest(prompt(s).encode('utf-8')) for s in STAGES} and
            policy['schema_sha256'] == {s: {str(a).lower(): canonical_hash(adapter(s, a).json_schema())
                                           for a in (True, False)} for s in STAGES}, 'method policy version mismatch')
    expected_ids = {f's4_{n:03d}' for n in range(1, 31)}
    require(set(refs['cases']) == set(disp['cases']) == expected_ids and len(disp['pairs']) == 12,
            'reference 30-case/12-pair inventory mismatch')
    require(refs['pairs'] == {key: p['cases'] for key, p in disp['pairs'].items()} and
            all(p['strict_single_variable_claim'] is False for p in disp['pairs'].values()), 'pair binding mismatch')
    members = [c for pair in refs['pairs'].values() for c in pair]
    require(len(members) == len(set(members)) == 24 and set(members) <= expected_ids, 'invalid pair members')
    for case, item in disp['cases'].items():
        expected_scope = 'excluded_ambiguity' if item['expected']['authorization'] == 'ambiguous' else 'include_author_draft'
        require(item['expected'] == refs['cases'][case]['expected'] and item['impact'] == 'include' and
                item['main_authorization_and_decision'] == expected_scope, 'unsupported case disposition')
    require({c for c, item in disp['cases'].items() if item['expected']['authorization'] == 'ambiguous'} ==
            set(owner['decisions']['ambiguous_cases']['case_ids']) == {'s4_018', 's4_020'}, 'ambiguity scope changed')
    return binding, values


def evaluate(directory, bundle_root, scripts_path, *, binding_path=DEFAULT_BINDING, mode='synthetic_diagnostic'):
    if mode != 'synthetic_diagnostic':
        raise PermissionError('formal scoring disabled: authenticated real provenance and frozen design unavailable')
    replay = verify(directory, bundle_root, scripts_path)
    result = {'version': 's4_r4_evaluation_v1', 'replay': replay,
              'evaluator_sources_sha256': replay['evaluator_sources_sha256'],
              'scoring': 'not_scored_scripted_mock', 'accuracy': None, 'model_metrics': None,
              'synthetic_diagnostic_counts': None, 'reference_read': False,
              'actual_model_calls': 0, 'real_transport_allowed': False}
    if replay['status'] != 'verified_settled_synthetic':
        result['status'] = 'incomplete_evidence_no_reference_read'
        return result
    binding, private = load_reference_package(binding_path)
    refs, disp, audit = private['reference'], private['disposition'], private['audit']
    cases = sorted({row['case_id'] for row in replay['rows']})
    audited = {row['case_id']: row for row in audit['cases']}
    require(len(audited) == len(audit['cases']) == 30, 'duplicate or missing audit case')
    for case in cases:
        bundle = checked_bundle(Path(bundle_root)/case)
        require(bundle.sha256 == audited[case]['bundle_sha256'], 'public bundle does not match reference audit: ' + case)
        require(bundle.review_input.decision_point.model_dump(mode='json') == audited[case]['decision_point'],
                'reference decision point mismatch')
    selected = {c: {'expected': refs['cases'][c]['expected'], 'disputed': False} for c in cases}
    pairs = {key: pair['cases'] for key, pair in disp['pairs'].items() if all(c in selected for c in pair['cases'])}
    incomplete_pairs = [key for key, pair in disp['pairs'].items()
                        if sum(c in selected for c in pair['cases']) == 1]
    counts = aggregate(replay['rows'], selected, pairs)
    diagnostics = []
    for row in replay['rows']:
        if row['diagnostic'] is not None:
            raw = row['diagnostic']['prediction']
            diagnostics.append({**{k: row[k] for k in ('case_id', 'method', 'track', 'repeat_index')},
                **row['diagnostic'], 'matches_author_draft': {a: raw[a] == selected[row['case_id']]['expected'][a] for a in AXES},
                'affects_main_result': False})
    require(artifact_hashes(directory) == replay['artifact_sha256'], 'run changed during evaluation')
    result.update(status='offline_evaluator_wiring_verified', reference_read=True,
                  evaluator_binding_sha256=digest(Path(binding_path).read_bytes()), private_sha256=binding['sha256'],
                  reference_status='provisional_author_draft_not_frozen', independent_review='not_performed_skipped_by_user',
                  user_case_by_case_review='not_received', comparison='equal_configuration', budget_matched='deferred',
                  selected_cases=cases, selected_complete_pairs=list(pairs), incomplete_pairs_excluded=incomplete_pairs,
                  strict_single_variable_claim=False, synthetic_diagnostic_counts=counts,
                  diagnostic_terminals=diagnostics, metric_scope='synthetic_only_not_model_accuracy')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('verify', 'evaluate'))
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--bundle-root', type=Path, required=True)
    parser.add_argument('--scripts', type=Path, required=True, help='hash binding only; never replayed')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    require(not any(output.is_relative_to(p.resolve()) for p in (args.run, args.bundle_root, ROOT/'evaluation_private'))
            and output != args.scripts.resolve(), 'output overlaps inputs or evaluator references')
    result = (verify if args.command == 'verify' else evaluate)(args.run, args.bundle_root, args.scripts)
    require(result['evaluator_sources_sha256'] == source_hashes(), 'evaluator changed while reporting')
    with output.open('x', encoding='utf-8', newline='\n') as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
        file.write('\n')
    print(json.dumps({'status': result['status'], 'actual_model_calls': 0, 'accuracy': None}))


if __name__ == '__main__':
    main()
