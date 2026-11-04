"""Read-only, response-driven replay of R3 v2; never executes a scripted runner.

The shared parsers/state kernel are reused, but requests, state and terminal
judgments are derived from received bytes, not from the expected mock script.
Hashes establish consistency only, not authentic provider/human provenance.
"""
import json
import math
from contextlib import closing
from datetime import datetime
from pathlib import Path

from scripts import s4_r3 as r3
from scripts.s4_accounting_core import aggregate as aggregate_cost
from scripts.m3k_history_lookup import HistoryLookupSession, LookupPolicy, LookupRequest
from scripts.s4_p2 import checked_bundle
from scripts.s4_p2_scope import QualifiedEngine
from scripts.s4_prompt_candidate import STAGES, adapter, preview, prompt
from scripts.s4_synthetic_cost_ledger import quote
from scripts.s4_synthetic_cost_wire import mock_wire
from writscope.artifacts import canonical_hash, digest
from writscope.contracts import parse_json, validate_output
from writscope.events import DecisionPoint

AXES = ('impact', 'authorization', 'decision')
ROOT = Path(__file__).resolve().parents[1]


def source_hashes():
    return {p: digest((ROOT/p).read_bytes()) for p in (
        'scripts/s4_r4_replay.py', 'scripts/s4_r4_evaluator.py', 'scripts/s4_metric_core.py',
        'specs/s4-r4-evaluation-v1.md')}


class ReplayError(ValueError):
    """Record cannot be reconciled with public evidence and saved responses."""


class Incomplete(Exception):
    """Only a prefix is durable; never a valid main output."""


def require(condition, message):
    if not condition:
        raise ReplayError(message)


def read_json(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'missing or linked artifact')
    return parse_json(path.read_text(encoding='utf-8'))


def artifact_hashes(directory):
    directory = Path(directory)
    require(not any(p.is_symlink() or p.is_junction() for p in (directory, *directory.parents)),
            'linked run directory')
    paths = [directory/'plan.json', directory/'journal.sqlite', *sorted(directory.glob('job-*.json'))]
    if (directory/'result.json').exists():
        paths.append(directory/'result.json')
    require(not any(directory.glob('journal.sqlite-*')), 'journal must be a closed durable snapshot')
    require(all(p.is_file() and not p.is_symlink() for p in paths), 'missing or linked artifact')
    return {p.name: digest(p.read_bytes()) for p in paths}


class Cursor:
    def __init__(self, identity, bundle_path, plan, attempts, events, before, stopped, partial, last_time):
        self.identity, self.path, self.plan = identity, bundle_path, plan
        self.bundle = checked_bundle(bundle_path)
        self.attempts, self.events, self.before = attempts, events, before
        self.stopped, self.partial, self.last_time = stopped, partial, last_time
        self.ai = self.ei = self.lookups = 0
        self.transitions, self.lookup_records = [], []
        self.assessment = self.final = self.diagnostic = None
        self.citations = {'syntax': 'not_evaluated', 'semantic_support': 'not_evaluated'}
        self.shared_budget_stop = False

    def time(self, value):
        try:
            point = datetime.fromisoformat(value)
            require(point.tzinfo is not None, 'timestamp lacks timezone')
            require(self.last_time is None or point >= self.last_time, 'causal timestamp order mismatch')
        except (TypeError, ValueError) as exc:
            raise ReplayError('invalid or reversed timestamp') from exc
        self.last_time = point

    def checkpoint(self, kind, payload):
        if self.ei == len(self.events) and self.partial:
            raise Incomplete('missing_checkpoint:' + kind)
        require(self.ei < len(self.events), 'missing checkpoint: ' + kind)
        event = self.events[self.ei]
        require(event['kind'] == kind, 'checkpoint order mismatch: ' + kind)
        actual = json.loads(event['payload'])
        if kind.endswith('_finished') and kind != 'job_finished':
            elapsed = actual.pop('elapsed_ms', None)
            require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0,
                    'invalid local elapsed time')
        require(actual == payload, 'checkpoint derived payload mismatch: ' + kind)
        self.time(event['created_at'])
        self.ei += 1

    def call(self, request):
        wire = mock_wire(request)
        # The bound R3 executor also has a local attempt cap. Hitting it before
        # dispatch does not write the shared stop; the next job may record one
        # unsent shared-budget rejection. It must never send another request.
        if self.ai >= self.plan['base']['policy']['max_attempts']:
            raise RuntimeError('round_budget_exhausted')
        if self.ai == len(self.attempts):
            policy = self.plan['base']['policy']
            previous = self.before + self.attempts
            exhausted = (len(previous) >= policy['max_attempts'] or
                         quote(policy, len(wire)) > policy['cap_micro'] - sum(a['reserved'] for a in previous))
            if self.stopped == 'synthetic_budget_exhausted' and exhausted:
                self.shared_budget_stop = True
                raise RuntimeError('round_budget_exhausted')
            if self.partial:
                raise Incomplete('request_not_durably_recorded')
            raise ReplayError('missing attempt without demonstrated budget stop')
        saved = self.attempts[self.ai]
        require(saved['request'] == wire and saved['stage'] == request['stage'] and
                saved['is_continuation'] == int('lookup_response' in request['public_input']),
                'request bytes/stage/continuation differ from public-state reconstruction')
        require(saved['cost_source'] == 'synthetic_policy', 'unknown cost provenance')
        self.ai += 1
        self.time(saved['created_at'])
        for key in ('sent_at', 'received_at'):
            if saved[key] is not None:
                self.time(saved[key])
        if saved['status'] in ('reserved', 'dispatch_started', 'received'):
            require(self.partial, 'unresolved attempt in settled result')
            require(all(saved[k] is None for k in ('finish_reason', 'error_kind', 'validation', 'usage')),
                    'pending attempt contains invented validation')
            if saved['status'] != 'received':
                require(all(saved[k] is None for k in ('response', 'received_at', 'http_status')),
                        'unreceived attempt contains response fields')
            else:
                require(saved['received_at'] is not None and type(saved['http_status']) is int,
                        'received attempt lacks receipt metadata')
            raise Incomplete('unresolved_' + saved['status'])
        usage = json.loads(saved['usage']) if saved['usage'] else {}
        require(all(usage.get(key) is None for key in ('input_tokens', 'output_tokens', 'cache_hit_input_tokens',
                'cache_miss_input_tokens', 'reasoning_tokens', 'billed_cost')), 'invented mock usage')
        elapsed = usage.get('provider_elapsed_ms')
        require(type(elapsed) in (float, int) and math.isfinite(elapsed) and elapsed >= 0,
                'invalid provider elapsed time')
        if saved['response'] is None:
            require(saved['status'] == 'failed' and saved['error_kind'] == 'synthetic_timeout' and
                    saved['validation'] == 'not_performed' and saved['http_status'] is None and
                    saved['finish_reason'] is None and saved['received_at'] is not None,
                    'unexplained missing response')
            raise ValueError('synthetic_timeout')
        require(saved['received_at'] is not None, 'response without receipt timestamp')
        error, finish, parsed = None, None, None
        try:
            if 300 <= saved['http_status'] < 400:
                raise ValueError('redirect_rejected')
            if saved['http_status'] != 200:
                raise ValueError('synthetic_http_error')
            if len(saved['response']) > 100000:
                raise ValueError('response_size_exceeded')
            body = json.loads(saved['response'])
            finish = body['choices'][0]['finish_reason']
            if finish != 'stop':
                raise ValueError('truncated_or_unknown_finish')
            parsed = parse_json(body['choices'][0]['message']['content'])
            adapter(request['stage'], request['lookup_allowed']).validate_python(parsed)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            error = str(exc)
        require(saved['finish_reason'] == finish and saved['error_kind'] == error and
                saved['status'] == ('failed' if error else 'completed') and
                saved['validation'] == ('rejected' if error else 'accepted'),
                'response validation metadata mismatch')
        if error:
            raise ValueError(error)
        return parsed

    def stage(self, stage, point, **kwargs):
        request = preview(self.path, stage, allow_lookup=self.lookups < 3, **kwargs)
        response = self.call(request)
        if response['kind'] != 'lookup':
            return response
        self.checkpoint('lookup_started', {'event_ids': response['event_ids']})
        session = HistoryLookupSession(self.bundle.review_input.events, point,
                    LookupPolicy(max_requests=3, max_events_per_request=2), initial_attempts=self.lookups)
        record = session.lookup(LookupRequest(event_ids=response['event_ids']))
        self.lookups += 1
        lookup = record.model_dump(mode='json', exclude={'elapsed_ms'})
        self.checkpoint('lookup_finished', {'lookup': lookup})
        self.lookup_records.append(lookup)
        if record.status != 'succeeded':
            raise ValueError('history_lookup_denied:' + str(record.denial_reason))
        continuation = preview(self.path, stage, allow_lookup=False, **kwargs)
        continuation['public_input']['lookup_response'] = [e.model_dump(mode='json') for e in record.events]
        return self.call(continuation)

    def replay(self):
        case, method = self.identity['case_id'], self.identity['method']
        config = {'cases': [case], 'methods': [method], 'max_attempts': self.plan['base']['policy']['max_attempts'],
                  'prompt_sha256': {s: digest(prompt(s).encode('utf-8')) for s in STAGES},
                  'failure_policy': 'job_error_stops_job_budget_stops_round',
                  'terminal_policy': 'incremental_state_agreement_candidate_not_frozen'}
        failure, incomplete = None, None
        try:
            self.checkpoint('job_started', {'config_sha256': canonical_hash(config)})
            if method == 'incremental':
                engine = QualifiedEngine(self.bundle, canonical_hash(config))
                for index, event in enumerate(self.bundle.review_input.events):
                    response = self.stage('incremental_extract', DecisionPoint(event_id=event.event_id, seq=event.seq),
                               event_index=index, prior_state=engine.engine.snapshot(),
                               prior_value_scopes=engine.snapshot()['value_scopes'])
                    self.checkpoint('transition_started', {'event_id': event.event_id, 'event_index': index})
                    transition = engine.submit(json.dumps({'items': response['items']}, ensure_ascii=False),
                                               index, f'{case}_{event.event_id}')
                    self.checkpoint('transition_finished', {'transition': transition})
                    self.transitions.append(transition)
                    if not transition['batch']['accepted']:
                        raise ValueError('state_batch_rejected:' + str(transition['batch']['rejection_reason']))
                self.checkpoint('assessment_started', {})
                self.assessment = engine.assess()
                self.checkpoint('assessment_finished', {'assessment': self.assessment})
                response = self.stage('incremental_final', self.bundle.review_input.decision_point,
                                      maintained_state=engine.snapshot())
            else:
                response = self.stage(method, self.bundle.review_input.decision_point)
            self.checkpoint('final_validation_started', {})
            try:
                final = validate_output(json.dumps(response['payload'], ensure_ascii=False), self.bundle,
                        'full_checklist' if method == 'full_checklist' else 'full_direct', output_contract='source_quote_v1')
            except (ValueError, TypeError, KeyError):
                self.citations['syntax'] = 'invalid_terminal'
                raise
            refs = list(final.source_refs)
            refs += [r for item in final.derived_items for r in item.source_refs]
            refs += [r for item in final.per_impact_authorization for r in item.source_refs]
            self.citations.update(syntax='valid', parsed_citations=len(refs), total_citations=len(refs))
            differences = [a for a in AXES if method == 'incremental' and getattr(final, a) != self.assessment[a]]
            if differences:
                self.diagnostic = {'kind': 'valid_terminal_state_conflict', 'different_axes': differences,
                                   'prediction': {a: getattr(final, a) for a in AXES},
                                   'response_sha256': self.attempts[self.ai-1]['response_hash']}
                raise ValueError('final_state_disagreement')
            self.checkpoint('final_validation_finished', {'final': final.model_dump(mode='json')})
            self.final = final.model_dump(mode='json')
            self.checkpoint('job_finished', {'status': 'completed'})
        except ReplayError:
            raise
        except Incomplete as exc:
            incomplete = str(exc)
        except (ValueError, TypeError, KeyError, RuntimeError) as exc:
            failure = {'kind': type(exc).__name__, 'detail': str(exc)}
            status = 'not_run' if str(exc) == 'round_budget_exhausted' and not self.attempts else 'failed'
            try:
                self.checkpoint('job_finished', {'status': status, 'failure': failure})
            except Incomplete as pending:
                incomplete = str(pending)
        require(self.ai == len(self.attempts) and self.ei == len(self.events), 'extra attempts or checkpoints after stop')
        sent = sum(a['status'] != 'reserved' for a in self.attempts)
        if incomplete:
            self.final = None
            status = 'failed' if sent else 'not_run'
        else:
            status = 'completed' if self.final else 'failed' if sent else 'not_run'
        return {**self.identity, 'status': status, 'prediction': {a: self.final[a] for a in AXES} if self.final else None,
                'failure': failure, 'incomplete': incomplete, 'diagnostic': self.diagnostic,
                'citations': self.citations, 'sent_or_uncertain': sent,
                'unsent_reservations': len(self.attempts)-sent}


def cost_entries(attempts, events):
    entries, pending = [], None
    for event in events:
        kind = event['kind']
        if kind in ('job_started', 'job_finished'):
            continue
        operation = kind.rsplit('_', 1)[0]
        if kind.endswith('_started'):
            pending = operation
        else:
            entries.append({'entry_id': f"local/{event['seq']}", 'operation': operation,
                            'stage': 'local_update' if operation == 'transition' else 'local_validation',
                            'elapsed_ms': json.loads(event['payload'])['elapsed_ms']})
            pending = None
    if pending:
        entries.append({'entry_id': 'local/pending', 'operation': pending,
                        'stage': 'local_update' if pending == 'transition' else 'local_validation', 'elapsed_ms': None})
    for attempt in attempts:
        if attempt['status'] == 'reserved':
            continue
        usage = json.loads(attempt['usage']) if attempt['usage'] else {}
        entries.append({'entry_id': f"attempt/{attempt['id']}",
                        'stage': 'lookup_continuation' if attempt['is_continuation'] else
                                 'extract' if attempt['stage'] == 'incremental_extract' else 'final',
                        **{k: usage.get(k) for k in ('input_tokens', 'output_tokens', 'cache_hit_input_tokens',
                                                    'cache_miss_input_tokens', 'reasoning_tokens')},
                        'elapsed_ms': usage.get('provider_elapsed_ms'),
                        'billed_cost_cny': None, 'estimated_cost_cny': None})
    return entries


def verify(directory, bundle_root, scripts_path):
    """Verify public-only evidence. scripts_path is hashed, never parsed/reexecuted."""
    directory, bundle_root = Path(directory), Path(bundle_root)
    hashes = artifact_hashes(directory)
    plan = read_json(directory/'plan.json')
    require(plan.get('execution_kind') == 'offline_mock' and plan.get('actual_model_calls') == 0,
            'unsupported or unauthenticated real provenance')
    r3.check_plan(plan, bundle_root, scripts_path)
    journal = r3.ReadOnlyJournal(directory/'journal.sqlite', plan, plan['base']['policy'])
    with closing(journal.connect()) as db:
        require(db.execute('SELECT count(*) FROM run').fetchone()[0] == 1, 'duplicate run ledger header')
    snapshot, events = journal.snapshot(), journal.checkpoints()
    identities = plan['base']['schedule']['plan']['jobs']
    ids = [canonical_hash(j) for j in identities]
    require(len(set(ids)) == len(ids), 'duplicate planned identities')
    for items in (snapshot['attempts'], events):
        order = [ids.index(i['job']) if i['job'] in ids else -1 for i in items]
        require(-1 not in order and order == sorted(order), 'unplanned or reordered journal')
    summary = read_json(directory/'result.json') if 'result.json' in hashes else None
    if summary is not None:
        require(summary.get('version') == 's4_r3_result_v2' and summary['plan_sha256'] == canonical_hash(plan) and
                summary['actual_model_calls'] == 0 and summary['accuracy'] is None and summary['provider_billed_cost'] is None and
                [{k: row[k] for k in identities[0]} for row in summary['rows']] == identities and
                summary['journal_stop'] == snapshot['stopped'], 'summary identity/provenance mismatch')
    partial = summary is None or (summary['stop_reason'] not in (None, 'synthetic_budget_exhausted'))
    rows, before, halted, last_time, costs = [], [], False, None, {}
    for index, (identity, job_id) in enumerate(zip(identities, ids), 1):
        attempts = [a for a in snapshot['attempts'] if a['job'] == job_id]
        own = [e for e in events if e['job'] == job_id]
        path = directory/f'job-{index:06d}.json'
        record = read_json(path) if path.exists() else None
        require(not halted or not (attempts or own or record), 'work continued after round stop')
        if not own and not attempts and record is None:
            require(halted or partial, 'missing planned task without round stop')
            row = {**identity, 'status': 'not_run', 'prediction': None, 'failure': None,
                   'incomplete': 'run_incomplete' if partial else None, 'diagnostic': None,
                   'citations': {'syntax': 'not_evaluated', 'semantic_support': 'not_evaluated'},
                   'sent_or_uncertain': 0, 'unsent_reservations': 0}
            halted = True
        else:
            cursor = Cursor(identity, bundle_root/identity['case_id'], plan, attempts, own, before,
                            snapshot['stopped'], partial, last_time)
            row = cursor.replay()
            last_time = cursor.last_time
            halted = row['incomplete'] is not None or cursor.shared_budget_stop
            if record is not None:
                job = record['trajectory']
                require(record['identity'] == identity and job['case_id'] == identity['case_id'] and
                        job['method'] == identity['method'], 'job identity mismatch')
                if not row['incomplete']:
                    require(record['round_integrity_failure'] is None and job['status'] == row['status'] and
                            job['final'] == cursor.final and job['failure'] == row['failure'] and
                            job['transitions'] == cursor.transitions and job['assessment'] == cursor.assessment and
                            job['lookups'] == cursor.lookup_records, 'saved trajectory differs from independent reconstruction')
            else:
                require(partial, 'missing executed job file')
                row['incomplete'] = row['incomplete'] or 'missing_job_file'
                row['status'], row['prediction'] = ('failed' if row['sent_or_uncertain'] else 'not_run'), None
                halted = True
        if summary is not None and not partial:
            saved = summary['rows'][index-1]
            require(saved['status'] == row['status'] and saved['attempts'] == row['sent_or_uncertain'] and
                    saved['final_present'] == (row['prediction'] is not None), 'summary result mismatch')
        entries = cost_entries(attempts, own)
        row['cost'] = aggregate_cost(entries)
        for entry in entries:
            entry['entry_id'] = job_id + '/' + entry['entry_id']
        costs.setdefault(identity['method']+'/'+identity['track'], []).extend(entries)
        rows.append(row)
        before += attempts
    require(len(list(directory.glob('job-*.json'))) <= len(identities), 'extra job files')
    if not partial:
        expected_stop = 'synthetic_budget_exhausted' if halted else None
        require(summary['stop_reason'] == snapshot['stopped'] == expected_stop, 'unjustified round stop')
    elif summary is not None:
        require(halted and any(read_json(p).get('round_integrity_failure') == summary['stop_reason']
                               for p in directory.glob('job-*.json')), 'unsubstantiated integrity stop')
    persistence = r3.inspect(directory, bundle_root, scripts_path)
    combined_cost = aggregate_cost([entry for group in costs.values() for entry in group])
    require(combined_cost == persistence['cost'], 'independent cost reconciliation mismatch')
    require(artifact_hashes(directory) == hashes, 'run changed during replay')
    return {'version': 's4_r4_replay_v1', 'status': 'verified_prefix_only' if partial else 'verified_settled_synthetic',
            'evaluator_sources_sha256': source_hashes(),
            'execution_kind': 'offline_mock', 'provenance': 'synthetic_not_provider_authenticated',
            'plan_sha256': canonical_hash(plan), 'artifact_sha256': hashes, 'planned_jobs': len(rows), 'rows': rows,
            'cost': combined_cost, 'cost_by_method_track': {key: aggregate_cost(group) for key, group in costs.items()},
            'reserved_virtual_micro': persistence['reserved_virtual_micro'],
            'shared_evidence_elapsed_ms': None, 'provider_billed_cost': None,
            'actual_model_calls': 0, 'real_transport_allowed': False, 'accuracy': None}
