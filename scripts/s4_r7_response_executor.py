"""Canonical response-driven S4 method executor; no Script or fixture input.

One versioned replacement for the frozen R3 scripted executor. Scheduling,
journal, prompts, schemas, state kernel and independent R4 verifier stay shared.
"""
import json
import time

from scripts.m3k_history_lookup import HistoryLookupSession, LookupPolicy, LookupRequest
from scripts.s4_p2 import checked_bundle
from scripts.s4_p2_scope import QualifiedEngine
from scripts.s4_prompt_candidate import STAGES, preview, prompt
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_synthetic_cost_wire import SyntheticBudgetStop
from writscope.artifacts import canonical_hash, digest
from writscope.contracts import validate_output
from writscope.events import DecisionPoint


def execute_job(identity, bundle_path, plan, journal, dispatch):
    case, method = identity['case_id'], identity['method']
    bundle = checked_bundle(bundle_path)
    if method not in ('full_direct', 'full_checklist', 'incremental') or bundle.review_input.case_id != case:
        raise SharedIntegrityError('unbound method/case')
    job_id = canonical_hash(identity)
    config = {'cases': [case], 'methods': [method], 'max_attempts': plan['base']['policy']['max_attempts'],
              'prompt_sha256': {s: digest(prompt(s).encode()) for s in STAGES},
              'failure_policy': 'job_error_stops_job_budget_stops_round',
              'terminal_policy': 'incremental_state_agreement_candidate_not_frozen'}
    row = {'case_id': case, 'method': method, 'status': 'not_run', 'failure': None,
           'attempts': [], 'lookups': [], 'transitions': [], 'assessment': None, 'final': None}
    def checkpoint(kind, payload):
        try:
            journal.append_checkpoint(job_id, kind, payload)
        except Exception as exc:
            raise SharedIntegrityError('checkpoint persistence failed') from exc
    def local(kind, start_payload, function):
        checkpoint(kind+'_started', start_payload)
        start = time.perf_counter()
        result = function()
        checkpoint(kind+'_finished', {kind if kind != 'final_validation' else 'final': result,
                                     'elapsed_ms': (time.perf_counter()-start)*1000})
        return result
    def call(request):
        before = len(journal.snapshot()['attempts'])
        try:
            # No expected output, script step or answer enters this interface.
            return dispatch(request)
        except SyntheticBudgetStop:
            raise RuntimeError('round_budget_exhausted') from None
        finally:
            received = journal.snapshot()['attempts'][before:]
            for saved in received:
                if saved['status'] == 'reserved':
                    continue  # Unsent reservation is retained only in the shared ledger.
                text = None
                if saved['response'] is not None:
                    try:
                        text = json.loads(saved['response'])['choices'][0]['message']['content']
                    except (ValueError, KeyError, TypeError, IndexError):
                        pass
                row['attempts'].append({'stage': request['stage'], 'request': request,
                                        'response': text, 'error': saved['error_kind'], 'synthetic_usage': None})
    def stage(name, point, **kwargs):
        allow = len(row['lookups']) < 3
        request = preview(bundle_path, name, allow_lookup=allow, **kwargs)
        response = call(request)
        if response['kind'] != 'lookup':
            return response
        ids = response['event_ids']
        session = HistoryLookupSession(bundle.review_input.events, point,
                    LookupPolicy(max_requests=3, max_events_per_request=2), initial_attempts=len(row['lookups']))
        lookup = local('lookup', {'event_ids': ids}, lambda: session.lookup(LookupRequest(event_ids=ids)).model_dump(mode='json', exclude={'elapsed_ms'}))
        row['lookups'].append(lookup)
        if lookup['status'] != 'succeeded':
            raise ValueError('history_lookup_denied:'+str(lookup['denial_reason']))
        continuation = preview(bundle_path, name, allow_lookup=False, **kwargs)
        continuation['public_input']['lookup_response'] = lookup['events']
        return call(continuation)
    shared_failure = None
    try:
        checkpoint('job_started', {'config_sha256': canonical_hash(config)})
        if method == 'incremental':
            engine = QualifiedEngine(bundle, canonical_hash(config))
            for index, event in enumerate(bundle.review_input.events):
                response = stage('incremental_extract', DecisionPoint(event_id=event.event_id, seq=event.seq),
                                 event_index=index, prior_state=engine.engine.snapshot(),
                                 prior_value_scopes=engine.snapshot()['value_scopes'])
                transition = local('transition', {'event_id': event.event_id, 'event_index': index},
                    lambda: engine.submit(json.dumps({'items': response['items']}, ensure_ascii=False), index, f'{case}_{event.event_id}'))
                row['transitions'].append(transition)
                if not transition['batch']['accepted']:
                    raise ValueError('state_batch_rejected:'+str(transition['batch']['rejection_reason']))
            row['assessment'] = local('assessment', {}, engine.assess)
            response = stage('incremental_final', bundle.review_input.decision_point, maintained_state=engine.snapshot())
        else:
            response = stage(method, bundle.review_input.decision_point)
        def final_check():
            final = validate_output(json.dumps(response['payload'], ensure_ascii=False), bundle,
                    'full_checklist' if method == 'full_checklist' else 'full_direct', output_contract='source_quote_v1')
            if method == 'incremental' and any(getattr(final, a) != row['assessment'][a] for a in ('impact', 'authorization', 'decision')):
                raise ValueError('final_state_disagreement')
            return final.model_dump(mode='json')
        row['final'] = local('final_validation', {}, final_check)
        row['status'] = 'completed'
        checkpoint('job_finished', {'status': 'completed'})
    except SharedIntegrityError as exc:
        shared_failure = str(exc)
        row.update(status='failed' if row['attempts'] else 'not_run', final=None,
                   failure={'kind': 'shared_integrity_failure', 'detail': shared_failure})
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        row.update(status='not_run' if str(exc) == 'round_budget_exhausted' and not row['attempts'] else 'failed',
                   failure={'kind': type(exc).__name__, 'detail': str(exc)})
        checkpoint('job_finished', {'status': row['status'], 'failure': row['failure']})
    return row, shared_failure
