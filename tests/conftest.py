from pathlib import Path

import pytest

from writscope.m1 import run_validation


@pytest.fixture(scope="session")
def root():
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def validation(root, tmp_path_factory):
    output = tmp_path_factory.mktemp("validation") / "run"
    report = run_validation(root, output)
    return report, output
