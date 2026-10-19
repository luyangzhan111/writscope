"""Evaluator-owned assertions, executed unchanged against all three copies."""

import os
from pathlib import Path
import runpy

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def response():
    snapshot = Path(os.environ["WRITSCOPE_SNAPSHOT"])
    app = runpy.run_path(str(snapshot / "app.py"))["app"]
    with TestClient(app) as client:
        return client.get("/api/v1/profile")


def test_http_200(response):
    assert response.status_code == 200


def test_status_exists(response):
    assert "status" in response.json(), "missing status"


def test_exact_response(response):
    assert response.json() == {"status": "active"}, "response contract changed"
    assert type(response.json()["status"]) is str
