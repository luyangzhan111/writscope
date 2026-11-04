"""Single-process, offline-only durable journal exercise; not a paid-call gate."""
import json
import os
from pathlib import Path

from writscope.artifacts import canonical_hash


def _save_new(path, value):
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class MockJournal:
    def __init__(self, directory, max_attempts):
        self.root = Path(directory)
        if not self.root.is_dir() or max_attempts < 1:
            raise ValueError("invalid journal")
        self.max_attempts = max_attempts

    def reserve(self, request):
        if self.pending():
            raise ValueError("unresolved earlier attempt")
        reserved = sorted(self.root.glob("*.reserved.json"))
        if len(reserved) >= self.max_attempts:
            raise ValueError("mock attempt budget exhausted")
        attempt = len(reserved) + 1
        path = self.root / f"{attempt:06}.reserved.json"
        _save_new(path, {"version": "s4_mock_journal_v1", "attempt": attempt,
                         "request_sha256": canonical_hash(request), "execution_kind": "offline_mock"})
        return attempt

    def mark_sent(self, attempt):
        reserved = self.root / f"{attempt:06}.reserved.json"
        if not reserved.is_file():
            raise ValueError("missing reservation")
        _save_new(self.root / f"{attempt:06}.sent.json", {"attempt": attempt, "status": "simulated_sent"})

    def finish(self, attempt, response):
        if not (self.root / f"{attempt:06}.sent.json").is_file():
            raise ValueError("not marked sent")
        _save_new(self.root / f"{attempt:06}.result.json", {"attempt": attempt,
                  "response_sha256": canonical_hash(response), "usage": None, "execution_kind": "offline_mock"})

    def pending(self):
        return [p.name[:6] for p in sorted(self.root.glob("*.reserved.json"))
                if not (self.root / (p.name[:6] + ".result.json")).exists()]
