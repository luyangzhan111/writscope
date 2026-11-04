"""Pinned local tokenizer evidence; never certifies provider billing or sends data."""
import importlib
import json
from pathlib import Path
import sys
import zipfile

from writscope.artifacts import digest

ARCHIVE_SHA = 'e7310d1dafe0a86d8a5629fe78a7c763760f651db9b8682718a1781dcd6fe495'
WHEEL_SHA = '65fd6e3fb11ca1e78a6a93602490f134d1fdeb13bcef99389d5102ea318ed138'
WHEEL = 'tokenizers-0.22.1-cp39-abi3-win_amd64.whl'


def restore_runtime(wheel, target):
    """Install only the pinned local wheel into an isolated directory, offline."""
    wheel, target = Path(wheel), Path(target).resolve()
    if digest(wheel.read_bytes()) != WHEEL_SHA:
        raise ValueError('dependency wheel hash mismatch')
    with zipfile.ZipFile(wheel) as package:
        for name in package.namelist():
            path = (target/name).resolve()
            if not path.is_relative_to(target):
                raise ValueError('unsafe dependency member')
            if not name.endswith('/'):
                data = package.read(name)
                if path.exists() and path.read_bytes() != data:
                    raise ValueError('refusing to overwrite changed runtime')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    destination = target/WHEEL
    if destination.exists() and destination.read_bytes() != wheel.read_bytes():
        raise ValueError('existing wheel mismatch')
    destination.write_bytes(wheel.read_bytes())


class LocalCounter:
    def __init__(self, archive, runtime):
        runtime = Path(runtime).resolve()
        wheel = runtime / WHEEL
        if digest(wheel.read_bytes()) != WHEEL_SHA:
            raise ValueError('tokenizer dependency mismatch')
        # Check the installed executable dependency against the pinned wheel.
        with zipfile.ZipFile(wheel) as package:
            for name in package.namelist():
                if not name.endswith('/'):
                    path = (runtime / name).resolve()
                    if not path.is_relative_to(runtime) or path.read_bytes() != package.read(name):
                        raise ValueError('tokenizer runtime drift')
        sys.path.insert(0, str(runtime))
        try:
            module = importlib.import_module('tokenizers')
        finally:
            sys.path.remove(str(runtime))
        if module.__version__ != '0.22.1' or not Path(module.__file__).resolve().is_relative_to(runtime):
            raise ValueError('unexpected tokenizer module')
        raw = Path(archive).read_bytes()
        if digest(raw) != ARCHIVE_SHA:
            raise ValueError('official tokenizer archive drift')
        with zipfile.ZipFile(archive) as package:
            config = package.read('deepseek_v4_tokenizer/tokenizer_config.json')
            data = package.read('deepseek_v4_tokenizer/tokenizer.json')
        self.config = json.loads(config)
        self.tokenizer = module.Tokenizer.from_str(data.decode('utf-8'))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.binding = {'archive_sha256': ARCHIVE_SHA, 'wheel_sha256': WHEEL_SHA,
                        'tokenizer_sha256': digest(data), 'config_sha256': digest(config),
                        'template_sha256': digest(self.config['chat_template'].encode()),
                        'recipe': 'pinned_system_user_generation_branch_v1'}

    def count(self, wire):
        if type(wire) is not bytes or len(wire) > 500000:
            raise ValueError('request byte boundary; no truncation')
        payload = json.loads(wire)
        messages = payload.pop('messages', None)
        if payload != {'model': 'deepseek-flash', 'thinking': {'type': 'disabled'},
                       'temperature': 0.0, 'max_tokens': 4096,
                       'response_format': {'type': 'json_object'}, 'tool_choice': 'none', 'stream': False}:
            raise ValueError('unbound wire configuration')
        if (not isinstance(messages, list) or len(messages) != 2 or
                [m.get('role') for m in messages] != ['system', 'user'] or
                any(set(m) != {'role', 'content'} or type(m['content']) is not str for m in messages)):
            raise ValueError('unsupported chat template branch')
        # Literal equivalent of the TWO supported branches in the pinned template.
        # No Jinja or downloaded Python is executed; no server-side extras assumed.
        rendered = (self.config['bos_token']['content'] + messages[0]['content'] +
                    '<\uff5cUser\uff5c>' + messages[1]['content'] + '<\uff5cAssistant\uff5c>')
        count = len(self.tokenizer.encode(rendered, add_special_tokens=False).ids)
        return {'kind': 'official_artifact_local_estimate', 'request_sha256': digest(wire),
                'rendered_sha256': digest(rendered.encode()), 'local_input_tokens': count,
                'within_proposed_input_cap': count <= 32768, 'output_cap': 4096,
                'provider_token_bound_verified': False, 'billable_overhead_tokens': None,
                'server_template_match': 'not_verified', 'target_version_match': 'not_verified',
                'binding': self.binding}


def usage_boundary(raw, input_bound=32768):
    """Strict accounting: invalid/absent values stay unknown, never become zero."""
    if type(input_bound) is not int or not 0 <= input_bound <= 32768:
        raise ValueError('invalid proposed input bound')
    raw = raw if isinstance(raw, dict) else {}
    mapping = {'input_tokens': 'prompt_tokens', 'output_tokens': 'completion_tokens',
               'cache_hit_input_tokens': 'prompt_cache_hit_tokens',
               'cache_miss_input_tokens': 'prompt_cache_miss_tokens'}
    values, errors = {}, []
    def number(value, field):
        if value is None:
            return None
        if type(value) is not int or value < 0:
            errors.append('invalid_' + field)
            return None
        return value
    for key, source in mapping.items():
        values[key] = number(raw.get(source), key)
    details = raw.get('completion_tokens_details')
    if details is not None and not isinstance(details, dict):
        errors.append('invalid_completion_details')
    nested = number(details.get('reasoning_tokens'), 'reasoning_tokens') if isinstance(details, dict) else None
    top = number(raw.get('reasoning_tokens'), 'reasoning_tokens')
    if nested is not None and top is not None and nested != top:
        errors.append('reasoning_disagreement')
    values['reasoning_tokens'] = nested if nested is not None else top
    inp, out = values['input_tokens'], values['output_tokens']
    hit, miss = values['cache_hit_input_tokens'], values['cache_miss_input_tokens']
    total = number(raw.get('total_tokens'), 'total_tokens')
    if inp is not None:
        if any(n is not None and n > inp for n in (hit, miss)) or (hit is not None and miss is not None and hit+miss != inp):
            errors.append('cache_sum_mismatch')
        if inp > input_bound:
            errors.append('input_bound_exceeded')
    if out is not None:
        if out > 4096:
            errors.append('output_bound_exceeded')
        if values['reasoning_tokens'] is not None and values['reasoning_tokens'] > out:
            errors.append('reasoning_exceeds_output')
    if total is not None and inp is not None and out is not None and total != inp+out:
        errors.append('total_sum_mismatch')
    # Rates are the inherited proposal, not a fresh quote or a provider invoice.
    estimate = inp*2 + out*8 if inp is not None and out is not None and not errors else None
    return {**values, 'billed_cost': None, 'estimated_cny_micro': estimate,
            'cost_source': 'conditional_candidate_peak_rates' if estimate is not None else 'unknown',
            'accounting_errors': errors, 'usage_source': 'fixed_transport_fixture_not_provider_measurement'}


def admit_production_tokens(*args, **kwargs):
    raise PermissionError('provider model/template/billing bound not independently established; live closed')
