"""Allowlisted file access and exact patching for the trusted M1 fixture."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def safe_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if not relative or parts.is_absolute() or ".." in parts.parts or "\\" in relative or ":" in relative:
        raise ValueError("unsafe artifact path")
    if relative != parts.as_posix() or relative == ".":
        raise ValueError("non-canonical artifact path")
    root = root.absolute()
    current = root
    for part in ("", *parts.parts):
        current = current / part if part else current
        if current.is_symlink() or current.is_junction():
            raise ValueError("linked artifact path")
    result = root / relative
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("artifact outside root")
    return result


class ArtifactStore:
    def __init__(self, root: Path, files: dict[str, str]):
        self.root, self.files = root, dict(files)

    def read(self, relative: str) -> bytes:
        if relative not in self.files:
            raise ValueError("artifact not allowlisted")
        data = safe_path(self.root, relative).read_bytes()
        if digest(data) != self.files[relative]:
            raise ValueError("artifact hash mismatch: " + relative)
        return data

    def verify(self):
        for relative in self.files:
            self.read(relative)


def snapshot_hash(root: Path, names=("app.py", "client.py")) -> str:
    return canonical_hash({name: digest(safe_path(root, name).read_bytes()) for name in names})


def apply_patch(snapshot: Path, patch: bytes, expected_base: str) -> str:
    """Support one exact existing-file hunk only; no fuzzy matching or shell."""
    if snapshot_hash(snapshot) != expected_base:
        raise ValueError("patch base hash mismatch")
    lines = patch.decode("utf-8").splitlines(keepends=True)
    if len(lines) < 4 or not lines[0].startswith("--- a/") or not lines[1].startswith("+++ b/"):
        raise ValueError("unsupported patch header")
    old, new = lines[0][6:].rstrip("\n"), lines[1][6:].rstrip("\n")
    if old != new or old not in {"app.py", "client.py"}:
        raise ValueError("patch target not allowed")
    match = re.fullmatch(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@\n", lines[2])
    if match is None or any(line[:1] not in {" ", "+", "-"} for line in lines[3:]):
        raise ValueError("unsupported patch hunk")
    start, count, new_start, new_count = map(int, match.groups())
    before = [line[1:] for line in lines[3:] if line[0] in " -"]
    after = [line[1:] for line in lines[3:] if line[0] in " +"]
    if start < 1 or new_start != start or count != len(before) or new_count != len(after):
        raise ValueError("patch hunk counts mismatch")
    target = safe_path(snapshot, old)
    original = target.read_bytes().decode("utf-8").splitlines(keepends=True)
    if original[start - 1:start - 1 + count] != before:
        raise ValueError("patch context mismatch")
    updated = original[:start - 1] + after + original[start - 1 + count:]
    target.write_bytes("".join(updated).encode("utf-8"))
    return snapshot_hash(snapshot)
