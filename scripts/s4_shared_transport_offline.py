"""Shared-budget mock transport for development, never a live-call gate."""
from pathlib import Path

from scripts.s4_provider_transport_simulation import simulate
from scripts.s4_repeat_transport_offline import run_in_memory
from scripts.s4_simulated_artifact_bundle import _bytes, _load, _write
from scripts.s4_simulated_reservation_store import ReservationStore
from writscope.artifacts import canonical_hash


class SharedIntegrityError(Exception):
    """A local journal or transport invariant failed; stop the whole round."""


class SharedTransport:
    def __init__(self, directory, plan_hash, max_attempts):
        self.root = Path(directory)
        self.root.mkdir(parents=False, exist_ok=False)
        self.plan_hash = plan_hash
        self.store = ReservationStore.create(self.root / "ledger.sqlite", plan_hash, max_attempts, max_attempts)

    @classmethod
    def open_existing(cls, directory, plan_hash):
        root = Path(directory)
        if root.is_symlink() or not root.is_dir():
            raise SharedIntegrityError("missing or unsafe shared journal")
        if (root / "ledger.sqlite").is_symlink():
            raise SharedIntegrityError("unsafe shared ledger")
        instance = cls.__new__(cls)
        instance.root, instance.plan_hash = root, plan_hash
        instance.store = ReservationStore(root / "ledger.sqlite")
        return instance

    def dispatch(self, job_id, stage, request, step):
        """Persist reservation and mock input before invoking MockTransport."""
        response = ({"offline_surrogate": "scripted_error"} if step is None or step.action == "error" else
                    {"choices": [{"message": {"content": step.text}, "finish_reason": "stop"}], "usage": None})
        http_status = 503 if step is None or step.action == "error" else 200
        try:
            attempt = self.store.reserve(self.plan_hash, job_id, canonical_hash(request), 1)
            path = self.root / f"attempt-{attempt:06d}.json"
            _write(path, _bytes({"version": "s4_shared_transport_input_v1", "job_id": job_id,
                                 "stage": stage, "request": request, "response": response,
                                 "http_status": http_status}))
            self.store.dispatch_started(attempt)
            try:
                result = run_in_memory(simulate(request, response, http_status=http_status))
                output = {"status": "completed", "result": result, "error_kind": None}
            except Exception as exc:
                output = {"status": "failed", "result": None, "error_kind": type(exc).__name__}
            _write(self.root / f"attempt-{attempt:06d}.result.json", _bytes(output))
            self.store.finish(attempt, output["status"], canonical_hash(output))
        except Exception as exc:
            raise SharedIntegrityError("shared mock journal failed: " + type(exc).__name__) from exc
        if output["status"] == "failed" and step is not None and step.action == "response":
            raise ValueError("mock_transport_rejected:" + output["error_kind"])

    def verify(self):
        snapshot = self.store.snapshot()
        if snapshot["plan_hash"] != self.plan_hash or snapshot["stopped"] is not None:
            raise SharedIntegrityError("shared plan or stop state mismatch")
        for row in snapshot["attempts"]:
            attempt = row["id"]
            if row["status"] not in {"completed", "failed"}:
                raise SharedIntegrityError("unresolved simulated dispatch")
            try:
                input_path = self.root / f"attempt-{attempt:06d}.json"
                output_path = self.root / f"attempt-{attempt:06d}.result.json"
                if input_path.is_symlink() or output_path.is_symlink():
                    raise ValueError("linked attempt artifact")
                data = _load(input_path)
                output = _load(output_path)
                if (data["job_id"] != row["job_id"] or canonical_hash(data["request"]) != row["request_hash"] or
                        canonical_hash(output) != row["response_hash"] or output["status"] != row["status"] or
                        row["reserved_units"] != 1 or row["usage"] is not None):
                    raise ValueError("binding mismatch")
                try:
                    replayed = run_in_memory(simulate(data["request"], data["response"], http_status=data["http_status"]))
                    rebuilt = {"status": "completed", "result": replayed, "error_kind": None}
                except Exception as exc:
                    rebuilt = {"status": "failed", "result": None, "error_kind": type(exc).__name__}
                if rebuilt != output:
                    raise ValueError("mock replay mismatch")
            except Exception as exc:
                raise SharedIntegrityError("shared simulated attempt invalid") from exc
        return {"status": "shared_mock_replayed", "attempts": len(snapshot["attempts"]),
                "reserved_units": sum(a["reserved_units"] for a in snapshot["attempts"]),
                "actual_model_calls": 0, "provider_billed_cost": None}
