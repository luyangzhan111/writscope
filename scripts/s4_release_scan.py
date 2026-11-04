"""Bounded offline inspection of explicit release files and encoded evidence.

Does not inspect the environment or credential stores. Pattern matches are
reported by location/category only; a clean scan is not a security guarantee.
"""
from __future__ import annotations

import base64
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import zipfile

from scripts.public_release import PATTERNS, findings


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 256 * 1024 * 1024
    max_member_bytes: int = 64 * 1024 * 1024
    max_total_expanded: int = 1024 * 1024 * 1024
    max_archive_members: int = 20000
    max_depth: int = 4
    max_json_nodes: int = 1_000_000
    max_sqlite_cells: int = 1_000_000


def allowed_name(name):
    if (type(name) is not str or not name or '\\' in name or PurePosixPath(name).is_absolute()
            or any(p in ('', '.', '..') or ':' in p or '\x00' in p for p in name.split('/'))):
        raise ValueError('unsafe_path')
    parts = [p.casefold() for p in name.split('/')]
    if any(p in ('.git', '.venv', 'venv', 'node_modules', '__pycache__') for p in parts):
        raise ValueError('excluded_environment_or_internals')
    if any((p == '.env' or p.startswith('.env.')) and p != '.env.example' for p in parts):
        raise ValueError('excluded_credential_source')
    if parts[-1] in ('id_rsa', 'id_ed25519', 'id_ecdsa') or parts[-1].endswith(('.pfx', '.p12')):
        raise ValueError('excluded_credential_source')
    return name


def _display(name):
    raw = name.encode('utf8', errors='replace')
    if any(pattern.search(raw) for pattern in PATTERNS.values()):
        return 'redacted-path-sha256:' + sha256(raw).hexdigest()
    return name


def _linked(path):
    return path.is_symlink() or path.is_junction()


def _json_content_hint(data):
    """Recognize JSON starts without treating TOML [project] as an array.

    Explicit .json/.jsonl names bypass this hint and remain strict. Other
    unrecognized formats still receive raw pattern scanning and are counted.
    """
    value = data.lstrip()
    if value.startswith(b'\xef\xbb\xbf'):
        value = value[3:].lstrip()
    if value.startswith(b'{'):
        return value[1:].lstrip().startswith((b'"', b'}'))
    if value.startswith(b'['):
        rest = value[1:].lstrip()
        if rest and rest[0] in b'"{[]-0123456789':
            return True
        for token in (b'true', b'false', b'null'):
            if token.startswith(rest) and rest:
                return True  # A truncated JSON literal is still a JSON view.
            if rest.startswith(token) and (len(rest)==len(token) or rest[len(token):len(token)+1] in b' \t\r\n,]'):
                return True
        return False
    if value.startswith(b'"'):
        try:
            return isinstance(json.loads(data), str)
        except (UnicodeError, ValueError, RecursionError):
            return False
    return False


class Scanner:
    def __init__(self, limits):
        self.limits = limits
        self.issues = []
        self.issue_keys = set()
        self.seen = set()
        self.stats = dict(unique_contents_scanned=0, duplicate_content_references=0,
            unique_bytes_scanned=0, expanded_bytes_read=0, archive_members_scanned=0,
            json_base64_values_scanned=0, json_contents_scanned=0,
            jsonl_contents_scanned=0, jsonl_lines_scanned=0,
            structured_parse_failures=0, raw_only_contents_scanned=0, sqlite_cells_scanned=0)

    def issue(self, name, kind):
        key = (_display(name), kind)
        if key not in self.issue_keys:
            self.issue_keys.add(key)
            self.issues.append({'path': key[0], 'kind': kind})

    def expanded(self, name, size):
        if size > self.limits.max_member_bytes:
            self.issue(name, 'member_size_limit')
            return False
        self.stats['expanded_bytes_read'] += size
        if self.stats['expanded_bytes_read'] > self.limits.max_total_expanded:
            self.issue(name, 'expanded_byte_limit')
            return False
        return True

    def raw(self, name, data):
        for item in findings(name, data):
            self.issue(name, item['kind'])

    def scan(self, name, data, depth=0):
        if depth > self.limits.max_depth:
            self.issue(name, 'container_depth_limit')
            return
        if len(data) > self.limits.max_file_bytes:
            self.issue(name, 'file_size_limit')
            return
        content = sha256(data).digest()
        if content in self.seen:
            self.stats['duplicate_content_references'] += 1
            return
        self.seen.add(content)
        self.stats['unique_contents_scanned'] += 1
        self.stats['unique_bytes_scanned'] += len(data)
        if self.stats['unique_bytes_scanned'] > self.limits.max_total_expanded:
            self.issue(name, 'unique_byte_limit')
            return
        self.raw(name, data)
        if data.startswith(b'SQLite format 3\x00'):
            self.sqlite(name, data, depth)
        elif data.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')) or name.lower().endswith('.zip'):
            self.zip(name, data, depth)
        elif name.lower().endswith('.jsonl'):
            self.json(name, data, depth, required=True, jsonl=True)
        elif name.lower().endswith('.json') or _json_content_hint(data):
            self.json(name, data, depth, required=name.lower().endswith('.json'))
        else:
            self.stats['raw_only_contents_scanned'] += 1

    def zip(self, name, data, depth):
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > self.limits.max_archive_members:
                    self.issue(name, 'archive_member_count_limit')
                    return
                seen = set()
                # Precheck every member before reading any member contents.
                valid = True
                for index, info in enumerate(infos):
                    label = name+'!member:'+str(index)
                    member = info.filename[:-1] if info.is_dir() else info.filename
                    try:
                        allowed_name(member)
                    except ValueError as exc:
                        self.issue(label, str(exc)); valid = False
                    folded = info.filename.casefold()
                    if folded in seen:
                        self.issue(label, 'duplicate_archive_member'); valid = False
                    seen.add(folded)
                    mode = (info.external_attr >> 16) & 0xffff
                    if stat.S_ISLNK(mode):
                        self.issue(label, 'linked_archive_member'); valid = False
                    if info.flag_bits & 1:
                        self.issue(label, 'encrypted_archive_member'); valid = False
                    if info.file_size > self.limits.max_member_bytes:
                        self.issue(label, 'member_size_limit'); valid = False
                if not valid:
                    return
                for info in infos:
                    if info.is_dir():
                        continue
                    label = name+'!'+info.filename
                    if not self.expanded(label, info.file_size):
                        return
                    with archive.open(info) as stream:
                        contents = stream.read(self.limits.max_member_bytes+1)
                    if len(contents) != info.file_size:
                        self.issue(label, 'archive_member_size_mismatch')
                        continue
                    self.stats['archive_members_scanned'] += 1
                    self.scan(label, contents, depth+1)
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
            self.issue(name, 'unreadable_archive')

    def json(self, name, data, depth, *, required=False, jsonl=False):
        try:
            value = json.loads(data)
        except (UnicodeError, ValueError, RecursionError):
            # CAS blobs have no extension. A JSON-looking multi-record view
            # must not silently lose its per-record encoded payloads. Ordinary
            # source/doc files starting with triple quotes are not JSONL.
            lines = [line for line in data.splitlines() if line.strip()]
            looks_json = _json_content_hint(data)
            if not (required or looks_json):
                return
            pending = []
            failed = False
            if len(lines) > 1 or jsonl:
                for index, line in enumerate(lines):
                    try:
                        pending.append(json.loads(line))
                        self.stats['jsonl_lines_scanned'] += 1
                    except (UnicodeError, ValueError, RecursionError):
                        failed = True
                        self.issue(name+'#line:'+str(index), 'unreadable_jsonl_record')
                if not failed:
                    self.stats['jsonl_contents_scanned'] += 1
            else:
                failed = True
            if failed:
                self.stats['structured_parse_failures'] += 1
                self.issue(name, 'unreadable_json_or_jsonl')
            if not pending:
                return
        else:
            self.stats['json_contents_scanned'] += 1
            pending = [value]
        # One node budget for the entire content view, including all JSONL
        # records. Depth and byte budgets remain shared with ZIP/base64/SQLite.
        nodes = 0
        while pending:
            nodes += 1
            if nodes > self.limits.max_json_nodes:
                self.issue(name, 'json_node_limit')
                return
            item = pending.pop()
            if isinstance(item, dict):
                for key, child in item.items():
                    self.raw(name+'#json-key', str(key).encode('utf8'))
                    if str(key).lower().endswith('_base64'):
                        label = name+'#base64:'+str(nodes)
                        if not isinstance(child, str) or len(child) > (self.limits.max_member_bytes+2)//3*4:
                            self.issue(label, 'base64_size_or_type_limit')
                            continue
                        try:
                            decoded = base64.b64decode(child, validate=True)
                        except (ValueError, TypeError):
                            self.issue(label, 'invalid_base64')
                            continue
                        if self.expanded(label, len(decoded)):
                            self.stats['json_base64_values_scanned'] += 1
                            self.scan(label, decoded, depth+1)
                    else:
                        pending.append(child)
            elif isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, str):
                encoded = item.encode('utf8', errors='replace')
                self.raw(name+'#json-value:'+str(nodes), encoded)
                if encoded.lstrip().startswith((b'{', b'[')):
                    if self.expanded(name, len(encoded)):
                        self.scan(name+'#embedded-json:'+str(nodes), encoded, depth+1)

    def sqlite(self, name, data, depth):
        try:
            with closing(sqlite3.connect(':memory:')) as db:
                db.enable_load_extension(False)
                db.deserialize(data)
                db.execute('PRAGMA trusted_schema=OFF')
                db.execute('PRAGMA query_only=ON')
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, self.limits.max_member_bytes)
                db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 512)
                tables = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                if len(tables) > 1000:
                    self.issue(name, 'sqlite_table_count_limit'); return
                ticks = [0]
                def progress():
                    ticks[0] += 1
                    return int(ticks[0] > 20000)
                db.set_progress_handler(progress, 1000)
                db.set_authorizer(lambda action,*args: sqlite3.SQLITE_OK if action in
                    (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ) else sqlite3.SQLITE_DENY)
                for table_index, (table, definition) in enumerate(tables):
                    label = name+'#sqlite-table:'+str(table_index)
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table) or 'VIRTUAL TABLE' in (definition or '').upper():
                        self.issue(label, 'unsupported_sqlite_table'); continue
                    # Identifier is a validated simple name; views, triggers,
                    # functions and database-provided SQL are never executed.
                    for row_index, row in enumerate(db.execute('SELECT * FROM "'+table+'"')):
                        for column_index, cell in enumerate(row):
                            self.stats['sqlite_cells_scanned'] += 1
                            if self.stats['sqlite_cells_scanned'] > self.limits.max_sqlite_cells:
                                self.issue(name, 'sqlite_cell_count_limit'); return
                            if isinstance(cell, (str, bytes)):
                                contents = cell.encode('utf8', errors='replace') if isinstance(cell,str) else cell
                                cell_name = label+'/row:'+str(row_index)+'/col:'+str(column_index)
                                if not self.expanded(cell_name, len(contents)):
                                    return
                                self.scan(cell_name, contents, depth+1)
        except (sqlite3.Error, ValueError, OverflowError):
            self.issue(name, 'unreadable_or_rejected_sqlite')


def scan_bytes(name, data, *, limits=None):
    scanner = Scanner(limits or Limits())
    scanner.scan(name, data)
    return scanner.issues


def scan_files(root, names, *, limits=None):
    """Inspect only named files; reject every unsafe path before any reads."""
    scanner = Scanner(limits or Limits())
    root = Path(root)
    names = list(names)
    paths = []
    seen = set()
    for index, name in enumerate(names):
        label = name if isinstance(name,str) else 'file:'+str(index)
        try:
            allowed_name(name)
            folded = name.casefold()
            if folded in seen:
                raise ValueError('duplicate_file_reference')
            seen.add(folded)
            path = root/Path(*name.split('/'))
            if any(_linked(part) for part in (path,*path.parents)):
                raise ValueError('linked_file_or_parent')
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('unsafe_path')
            if not path.is_file():
                raise ValueError('missing_or_non_file')
            if path.stat().st_size > scanner.limits.max_file_bytes:
                raise ValueError('file_size_limit')
            paths.append((name,path))
        except (ValueError, OSError) as exc:
            scanner.issue(label, str(exc) if isinstance(exc,ValueError) else 'unreadable_path')
    scanned = 0
    if not scanner.issues:
        for name, path in paths:
            try:
                data = path.read_bytes()
            except OSError:
                scanner.issue(name,'file_read_failed'); continue
            scanner.scan(name,data); scanned += 1
    return {'version':'s4_bounded_release_scan_v1','files_requested':len(names),'files_scanned':scanned,
        **scanner.stats,'findings':scanner.issues,'pattern_categories':list(PATTERNS),
        'new_model_calls':0,'credential_stores_accessed':False,
        'limitation':'bounded_pattern_scan_not_a_security_or_absence_guarantee; unsupported_or_limit_failures_are_findings',
        'structured_format_scope':'ZIP_magic_or_extension; SQLite_magic; plausible_JSON_start_or_explicit_JSON_extension; JSONL_extension_or_multiline_plausible_JSON_object_or_array_content; unrecognized_formats_raw_patterns_only_and_counted',
        'deduplication':'content_sha256_after_each_path_safety_check; findings_reference_first_scanned_content_path'}
