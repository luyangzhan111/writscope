"""Byte-equivalence guard for immutable offline R5 candidates.

Initial/final semantic admission uses the original R3 check. Each subsequent
check still reads and hashes every bound file; only redundant parsing is avoided.
There is no mtime cache and no live-transport capability.
"""
from pathlib import Path
import os
import stat

from scripts import s4_r3
from scripts.s4_r4_replay import require
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]


def inventory(roots):
    result = []
    def unlinked(info):
        require(not stat.S_ISLNK(info.st_mode) and
                not getattr(info, 'st_file_attributes', 0) & 0x400, 'linked or reparse-point bound input')
    def visit(path, info=None):
        info = path.lstat() if info is None else info
        unlinked(info)
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    visit(path/entry.name, entry.stat(follow_symlinks=False))
        else:
            require(stat.S_ISREG(info.st_mode), 'non-regular bound input')
            result.append(path)
    for parent in {p for root in roots for p in root.parents}:
        unlinked(parent.lstat())
    for root in roots: visit(root)
    return tuple(result)


class ByteEquivalenceGuard:
    def __init__(self, plan, public, scripts, sources):
        self.public, self.scripts = Path(public).resolve(), Path(scripts).resolve()
        self.plan_hash = canonical_hash(plan)
        self.cases = tuple(self.public/c for c in plan['base']['schedule']['plan']['cases'])
        self.sources = tuple(ROOT/p for p in sorted(sources))
        s4_r3.check_plan(plan, self.public, self.scripts)
        self.files = inventory((*self.cases, *self.sources, self.scripts))
        self.hashes = {p: digest(p.read_bytes()) for p in self.files}
        require(all(self.hashes[ROOT/p] == sha for p, sha in sources.items()), 'candidate source mismatch')
        self.globbed = self.source_inventory()
        s4_r3.check_plan(plan, self.public, self.scripts)
        self(plan, self.public, self.scripts)

    @staticmethod
    def source_inventory():
        return (tuple(sorted((ROOT/'src/writscope').rglob('*.py'))),
                tuple(sorted((ROOT/'prompts/s4_candidate').glob('*.md'))))

    def __call__(self, plan, public, scripts):
        require(canonical_hash(plan) == self.plan_hash and Path(public).resolve() == self.public and
                Path(scripts).resolve() == self.scripts, 'guard plan or roots changed')
        require(inventory((*self.cases, *self.sources, self.scripts)) == self.files and
                self.source_inventory() == self.globbed, 'bound file inventory changed')
        for path, expected in self.hashes.items():
            require(digest(path.read_bytes()) == expected, 'bound file bytes changed: '+path.name)
