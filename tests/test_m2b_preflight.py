from pathlib import Path

import pytest

from writscope.preflight import prepare_m2b_requests
from writscope.providers import ModelConfig


def test_m2b_preflight_six_equal_evidence_requests(root):
    requests, manifest = prepare_m2b_requests(root, ModelConfig(model_id="candidate-model", temperature=None),
                                               max_request_bytes=1_000_000)
    assert len(requests) == manifest["jobs"] == 6
    for index in (0, 2, 4):
        direct, checklist = requests[index:index + 2]
        assert direct.public_input == checklist.public_input
        assert direct.evidence_sha256 == checklist.evidence_sha256
        assert direct.method == "full_direct" and checklist.method == "full_checklist"
        assert direct.model_config_values == checklist.model_config_values
        assert "expected_authorization" not in direct.model_dump_json()


def test_m2b_preflight_rejects_mock_and_truncation(root):
    with pytest.raises(ValueError, match="real model"):
        prepare_m2b_requests(root, ModelConfig(), max_request_bytes=1_000_000)
    with pytest.raises(ValueError, match="no truncation"):
        prepare_m2b_requests(root, ModelConfig(model_id="candidate-model"), max_request_bytes=10)
