"""Bridge existing scripted executor to R3's synthetic-only provider boundary."""
import json

from scripts.s4_r3_provider_mock_v1 import dispatch
from scripts.s4_shared_transport_offline import SharedIntegrityError
from writscope.artifacts import canonical_hash


class MockProviderBridge:
    def __init__(self, journal, repeat_index):
        if type(repeat_index) is not int or repeat_index < 1:
            raise ValueError('invalid repeat index')
        self.journal = journal
        self.repeat_index = repeat_index

    def __call__(self, case, method, stage, request, step):
        if request.get('stage') != stage:
            raise SharedIntegrityError('stage mismatch')
        identity = {'case_id':case, 'method':method, 'track':'A', 'repeat_index':self.repeat_index}
        status, body, timeout = 200, b'', False
        if step is None or step.action == 'error':
            timeout = step is not None and step.text == 'synthetic_timeout'
            status, body = 503, b'{"synthetic_error":true}'
        elif step.text.startswith('S4_MOCK_RAW:'):
            body = step.text.removeprefix('S4_MOCK_RAW:').encode('utf-8')
        else:
            finish = 'length' if step.text.startswith('S4_MOCK_TRUNCATED:') else 'stop'
            body = json.dumps({'choices':[{'message':{'content':step.text.removeprefix('S4_MOCK_TRUNCATED:')},
                                            'finish_reason':finish}], 'usage':None},
                              ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        fixture = {'kind':'synthetic_response_v1','status':status,'body':body,'timeout':timeout}
        dispatch(self.journal, canonical_hash(identity), request, fixture)
