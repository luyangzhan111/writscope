"""Scanner controls use generated dummy strings, never actual credentials."""
import base64
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import stat
from unittest.mock import patch
import zipfile

import pytest

from scripts import s4_release_scan as scan


def dummy():
    return ('sk-'+'X'*24).encode()


def zipped(members):
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for key,value in members:z.writestr(key,value)
    return stream.getvalue()


def test_nested_zip_base64_and_sqlite_are_inspected_without_echo():
    payload=json.dumps({'body_base64':base64.b64encode(dummy()).decode()}).encode()
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE attempt (response BLOB, value TEXT)')
    db.execute('INSERT INTO attempt VALUES (?,?)',(payload,dummy().decode()))
    raw=db.serialize();db.close()
    outer=zipped([('nested.zip',zipped([('journal.sqlite',raw)]))])
    issues=scan.scan_bytes('evidence.zip',outer)
    assert any(item['kind']=='provider_token' for item in issues)
    assert dummy().decode() not in json.dumps(issues)
    assert any('#base64:' in item['path'] for item in issues)


@pytest.mark.parametrize('name',['.env','a/.env.production','.git/config','.venv/a.py','venv/a.py','../escape','a/../escape','C:/drive','a\\b','a//b'])
def test_excluded_or_unsafe_files_are_refused_before_any_read(tmp_path,name):
    with patch.object(Path,'read_bytes',side_effect=AssertionError('must not read')):
        report=scan.scan_files(tmp_path,[name])
    assert report['findings'] and report['files_scanned']==0


def test_env_example_allowed_and_valid_clean_file_has_no_findings(tmp_path):
    (tmp_path/'.env.example').write_text('API_KEY=replace_me',encoding='utf8')
    report=scan.scan_files(tmp_path,['.env.example'])
    assert report['files_scanned']==1 and not report['findings']
    assert 'not_a_security' in report['limitation']


def test_link_is_refused_before_read_even_if_resolves_inside_root(tmp_path):
    source=tmp_path/'public.json';source.write_text('{}',encoding='utf8')
    original=Path.is_symlink
    with patch.object(Path,'is_symlink',lambda p: p==source or original(p)):
        with patch.object(Path,'read_bytes',side_effect=AssertionError('must not read')):
            report=scan.scan_files(tmp_path,['public.json'])
    assert report['findings']==[{'path':'public.json','kind':'linked_file_or_parent'}]


@pytest.mark.parametrize('name',['../escape','a/../../escape','C:/drive','/absolute','.env','folder/.git/config'])
def test_zip_member_paths_rejected_without_member_read(name):
    raw=zipped([(name,dummy())])
    with patch.object(zipfile.ZipFile,'open',side_effect=AssertionError('unsafe member read')):
        issues=scan.scan_bytes('evidence.zip',raw)
    assert issues and all(item['kind']!='provider_token' for item in issues)


def test_duplicate_casefold_and_symlink_zip_members_refused():
    issues=scan.scan_bytes('evidence.zip',zipped([('A',b'a'),('a',b'b')]))
    assert any(i['kind']=='duplicate_archive_member' for i in issues)
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        info=zipfile.ZipInfo('linked');info.external_attr=(stat.S_IFLNK|0o777)<<16
        z.writestr(info,b'target')
    assert any(i['kind']=='linked_archive_member' for i in scan.scan_bytes('evidence.zip',stream.getvalue()))


def test_expansion_size_and_depth_fail_closed():
    raw=zipped([('large',b'a'*1000)])
    assert any(i['kind']=='member_size_limit' for i in scan.scan_bytes('evidence.zip',raw,limits=scan.Limits(max_member_bytes=100)))
    nested=zipped([('nested.zip',zipped([('public.json',b'{}')]))])
    assert any(i['kind']=='container_depth_limit' for i in scan.scan_bytes('evidence.zip',nested,limits=scan.Limits(max_depth=1)))
    assert any(i['kind']=='expanded_byte_limit' for i in scan.scan_bytes('evidence.zip',raw,limits=scan.Limits(max_total_expanded=500,max_file_bytes=2000)))


def test_duplicate_content_deduplicated_but_each_original_path_checked(tmp_path):
    for name in ('one.json','two.json'):(tmp_path/name).write_text('{}',encoding='utf8')
    report=scan.scan_files(tmp_path,['one.json','two.json'])
    assert report['files_scanned']==2 and report['duplicate_content_references']==1
    report=scan.scan_files(tmp_path,['one.json','../one.json'])
    assert report['files_scanned']==0 and report['findings']


def test_unicode_json_escape_and_base64_size_are_checked():
    text='"'+dummy().decode().replace('-','\\u002d')+'"'
    issues=scan.scan_bytes('public.json',text.encode())
    assert any(i['kind']=='provider_token' for i in issues)
    raw=json.dumps({'body_base64':'x'*200}).encode()
    assert any(i['kind']=='base64_size_or_type_limit' for i in scan.scan_bytes('public.json',raw,limits=scan.Limits(max_member_bytes=10)))


def test_sensitive_path_is_redacted():
    issues=scan.scan_bytes(dummy().decode()+'.txt',dummy())
    assert issues and dummy().decode() not in json.dumps(issues)


def test_virtual_table_is_not_executed():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE VIRTUAL TABLE records USING fts5(value)')
    raw=db.serialize();db.close()
    issues=scan.scan_bytes('journal.sqlite',raw)
    assert any(i['kind']=='unsupported_sqlite_table' for i in issues)


def test_extensionless_jsonl_decodes_base64_and_uses_one_node_budget():
    rows=[{'body_base64':base64.b64encode(dummy()).decode()}, {'body_base64':base64.b64encode(b'plain').decode()}]
    raw=b'\n'.join(json.dumps(row).encode() for row in rows)+b'\n'
    issues=scan.scan_bytes('blobs/'+'a'*64,raw)
    assert any(i['kind']=='provider_token' and '#base64:' in i['path'] for i in issues)
    assert dummy().decode() not in json.dumps(issues)
    assert any(i['kind']=='json_node_limit' for i in scan.scan_bytes('blobs/'+'a'*64,raw,limits=scan.Limits(max_json_nodes=1)))


def test_partial_or_malformed_jsonl_is_reported_and_valid_lines_still_scanned():
    raw=json.dumps({'body_base64':base64.b64encode(dummy()).decode()}).encode()+b'\n{"incomplete":'
    issues=scan.scan_bytes('blobs/'+'b'*64,raw)
    assert any(i['kind']=='provider_token' for i in issues)
    assert any(i['kind']=='unreadable_jsonl_record' for i in issues)
    assert any(i['kind']=='unreadable_json_or_jsonl' for i in issues)
    assert dummy().decode() not in json.dumps(issues)


def test_unreadable_zip_and_json_like_views_are_findings():
    assert any(i['kind']=='unreadable_archive' for i in scan.scan_bytes('blobs/'+'c'*64,b'PK\x03\x04broken'))
    assert any(i['kind']=='unreadable_json_or_jsonl' for i in scan.scan_bytes('blobs/'+'d'*64,b'{"partial":'))


@pytest.mark.parametrize('name',['pyproject.toml','blobs/'+'e'*64])
def test_toml_is_raw_only_for_named_and_content_addressed_views(name):
    raw=b'[project]\nname = "example"\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    scanner=scan.Scanner(scan.Limits());scanner.scan(name,raw)
    assert not scanner.issues
    assert scanner.stats['raw_only_contents_scanned']==1
    assert scanner.stats['structured_parse_failures']==scanner.stats['jsonl_contents_scanned']==0


@pytest.mark.parametrize('raw',[b'[tool]\nvalue = "example"',b'[package]\nname = "example"',b'[files]\nvalue = "example"'])
def test_other_toml_headers_are_not_json_literal_array_starts(raw):
    assert not scan._json_content_hint(raw)
    assert not scan.scan_bytes('blobs/'+'f'*64,raw)


@pytest.mark.parametrize('name',['view.json','view.jsonl'])
def test_explicit_json_extensions_remain_strict_despite_non_json_start(name):
    assert any(i['kind']=='unreadable_json_or_jsonl' for i in scan.scan_bytes(name,b'[project]\nname="example"'))
