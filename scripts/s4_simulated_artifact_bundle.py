"""Persist and replay one synthetic transport attempt, strictly offline.

This is a development fixture, not a live execution record or authorization.
Hashes detect inconsistent files, not a malicious rewrite of an entire bundle.
"""
import hashlib
import json
import os
from pathlib import Path

from scripts.s4_recorded_transport_simulation import call, replay
from scripts.s4_simulated_reservation_store import ReservationStore
from writscope.artifacts import canonical_hash

FILES = ("plan.json", "request.json", "response.json", "record.json", "ledger.sqlite")


def _bytes(value):
    # The provider wire serializer preserves insertion order, so persistence must
    # preserve it too; canonical hashes alone intentionally ignore key order.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _write(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def create(directory, request, response, *, job_id, http_status=200):
    """Write fixture inputs before dispatch; never access real provider or secrets."""
    if not isinstance(job_id, str) or not job_id or type(http_status) is not int or not 100 <= http_status <= 599:
        raise ValueError("invalid synthetic attempt")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise ValueError("synthetic request and response must be objects")
    # Fail before creating files if inputs cannot be represented as JSON.
    request_bytes, response_bytes = _bytes(request), _bytes(response)
    plan = {"version": "s4_single_attempt_fixture_v1", "execution_kind": "offline_simulation",
            "job_id": job_id, "http_status": http_status, "max_attempts": 1, "virtual_units": 1,
            "request_sha256": canonical_hash(request), "response_sha256": canonical_hash(response)}
    plan_hash = canonical_hash(plan)
    directory = Path(directory)
    directory.mkdir(parents=False, exist_ok=False)
    _write(directory / "plan.json", _bytes(plan))
    _write(directory / "request.json", request_bytes)
    _write(directory / "response.json", response_bytes)
    ledger = ReservationStore.create(directory / "ledger.sqlite", plan_hash, 1, 1)
    record = await call(ledger, plan_hash, job_id, request, response, http_status=http_status)
    _write(directory / "record.json", _bytes(record))
    manifest = {"version": "s4_simulated_artifact_bundle_v1", "execution_kind": "offline_simulation",
                "plan_sha256": plan_hash, "files": {name: _digest(directory / name) for name in FILES},
                "actual_model_calls": 0}
    # The manifest is written last; its absence means the bundle is incomplete.
    _write(directory / "manifest.json", _bytes(manifest))
    return manifest


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


async def verify(directory):
    """Reopen saved files and replay the mock; does not authenticate origin."""
    directory = Path(directory)
    for name in (*FILES, "manifest.json"):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("incomplete or unsafe simulated bundle")
    manifest = _load(directory / "manifest.json")
    if (manifest.get("version") != "s4_simulated_artifact_bundle_v1" or
            manifest.get("execution_kind") != "offline_simulation" or manifest.get("actual_model_calls") != 0 or
            set(manifest.get("files", {})) != set(FILES)):
        raise ValueError("invalid simulated manifest")
    if any(_digest(directory / name) != manifest["files"][name] for name in FILES):
        raise ValueError("simulated artifact hash mismatch")
    plan, request, response, record = (_load(directory / name) for name in FILES[:4])
    if (plan.get("version") != "s4_single_attempt_fixture_v1" or
            plan.get("execution_kind") != "offline_simulation" or
            plan.get("max_attempts") != 1 or plan.get("virtual_units") != 1 or
            canonical_hash(plan) != manifest["plan_sha256"] or
            canonical_hash(request) != plan.get("request_sha256") or
            canonical_hash(response) != plan.get("response_sha256") or
            record.get("job_id") != plan.get("job_id")):
        raise ValueError("simulated plan binding mismatch")
    ledger = ReservationStore(directory / "ledger.sqlite")
    snapshot = ledger.snapshot()
    if (snapshot["max_attempts"] != 1 or snapshot["max_units"] != 1 or
            len(snapshot["attempts"]) != 1 or snapshot["attempts"][0]["reserved_units"] != 1 or
            snapshot["stopped"] is not None):
        raise ValueError("simulated ledger mismatch")
    await replay(ledger, record, request, response, http_status=plan["http_status"])
    return {"status": "simulated_bundle_replay_verified", "outcome": record["status"],
            "actual_model_calls": 0, "provider_billed_cost": None}
