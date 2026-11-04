"""Versioned S4 publication tools; content-addressed, independent offline roots.

Never imports an approval, reads credentials, or launches real model transport.
Historical files and the frozen first public-release tool remain untouched.
"""
import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
BUNDLE=Path('reports/public-s4-v0.2')
FROZEN=Path('reports/s4-contract-convergence-v2/manifest.json')
CANDIDATE='0ef9be84a94f272109535b483813b5eb91f4ddb7fc158f70a8d9c89e340c17d7'
NEW_FILES=('scripts/s4_public_release.py','scripts/s4_release_scan.py','tests/test_s4_public_release.py',
    'tests/test_s4_release_scan.py','docs/s4/README.md','docs/s4/public-release.md')


def digest(data):return sha256(data).hexdigest()


def read(path):return json.loads(Path(path).read_text(encoding='utf8'))


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')


def safe(root,name):
    rel=PurePosixPath(name)
    if (not name or '\\' in name or rel.is_absolute() or any(x in ('','.','..') or ':' in x for x in name.split('/')) or
            any(x in ('.git','.venv','venv','__pycache__') or x=='.env' or x.startswith('.env.') and x!='.env.example' for x in rel.parts)):
        raise ValueError('unsafe_or_credential_path')
    if any(x.rstrip(' .')!=x or re.search(r'[\x00-\x1f<>"|?*]',x) or
            re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',x,re.I) for x in rel.parts):
        raise ValueError('nonportable_or_device_path')
    target=Path(root).joinpath(*rel.parts)
    for p in (target,*target.parents):
        if p.is_symlink() or p.is_junction():raise ValueError('linked_path')
    if not target.resolve().is_relative_to(Path(root).resolve()):raise ValueError('path_escape')
    return target


def tracked(root):
    return subprocess.check_output(['git','ls-files','-z'],cwd=root).decode('utf8').split('\0')[:-1]


def document_closure(root,names):
    found=set(names);pending=[n for n in found if n.endswith('.md')]
    while pending:
        name=pending.pop();path=safe(root,name)
        for link in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)',path.read_text(encoding='utf8')):
            if '://' in link or link.startswith('#'):continue
            target=(path.parent/link).resolve()
            if target.is_relative_to(Path(root).resolve()) and target.is_file() and target.suffix=='.md':
                relative=target.relative_to(Path(root).resolve()).as_posix();safe(root,relative)
                if relative not in found:found.add(relative);pending.append(relative)
    return found


def build(root=ROOT):
    """Curate the frozen closure plus explicit publication source overlays."""
    root=Path(root);frozen=read(root/FROZEN)
    if frozen['candidate_sha256']!=CANDIDATE:raise ValueError('unexpected_frozen_candidate')
    original=frozen['files']; base_files=tracked(root)
    documents=document_closure(root,set(base_files)|set(NEW_FILES)|{'docs/s4-contract-convergence-offline-20261104.md'})
    names=set(original)|documents|{FROZEN.as_posix()}
    ci=sorted(n for n in base_files if n.startswith('tests/test_') and n.endswith('.py'))
    if len(ci)!=47:raise ValueError('legacy_test_inventory_changed')
    ci+=['tests/test_s4_contract_convergence.py','tests/test_s4_public_release.py','tests/test_s4_release_scan.py']
    objects={};files={}
    for name in sorted(names):
        data=safe(root,name).read_bytes();sha=digest(data)
        if name in original and sha!=original[name]:raise ValueError('frozen_bytes_changed:'+name)
        objects.setdefault(sha,data);files[name]={'sha256':sha,'bytes':len(data)}
    folder=root/BUNDLE;folder.mkdir(exist_ok=False)
    archive=folder/'evidence.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for sha,data in sorted(objects.items()):
            item=zipfile.ZipInfo('blobs/'+sha,date_time=(2026,11,4,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED
            item.external_attr=0o100644<<16;z.writestr(item,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    if archive.stat().st_size>=95_000_000:raise ValueError('archive_exceeds_publication_size_policy')
    manifest={'version':'s4_public_content_addressed_v1','frozen_candidate_sha256':CANDIDATE,
        'frozen_manifest_sha256':digest((root/FROZEN).read_bytes()),'frozen_files':original,
        'files':files,'object_count':len(objects),'archive_sha256':digest(archive.read_bytes()),
        'archive_bytes':archive.stat().st_size,'restored_bytes':sum(v['bytes'] for v in files.values()),
        'ci_test_files':ci,'ci_scope':'explicit_public_candidate_suite_not_all_S4_tests',
        'scan_policy':{'max_depth':12,'max_total_expanded':1073741824},
        'real_transport_allowed':False,'new_model_calls':0,'independent_review':'not_performed_skipped_by_user',
        'reference_status':'provisional','paid_approval':'none_new'}
    write(folder/'manifest.json',manifest)
    # Only these workspace files are candidates for a future Git commit.
    # Local raw runs/claims stay inside the byte-preserving evidence archive.
    publish=documents|set(read(root/'reports/s4-contract-convergence-v2/offline-candidate.json')['sources_sha256'])
    publish|={FROZEN.as_posix(),'tests/test_s4_contract_convergence.py','docs/s4-contract-convergence-offline-20261104.md',
        (BUNDLE/'manifest.json').as_posix(),(BUNDLE/'evidence.zip').as_posix()}
    for name in ('acceptance.json','completion.json','controls-summary.json','recovery-result.json','preservation-verification.json','tests-final.xml'):
        publish.add('reports/s4-contract-convergence-v2/'+name)
    write(folder/'publish-files.json',{'version':'s4_explicit_publication_inventory_v1',
        'files':{n:digest(safe(root,n).read_bytes()) for n in sorted(publish)},
        'required_metadata_files':[(BUNDLE/'publish-files.json').as_posix()],
        'git_add_or_commit_performed':False,'github_updated':False})
    return {'files':len(files),'objects':len(objects),'frozen_files':len(original),'archive_bytes':archive.stat().st_size}


def checked(root):
    root=Path(root);manifest=read(safe(root,(BUNDLE/'manifest.json').as_posix()))
    archive=safe(root,(BUNDLE/'evidence.zip').as_posix())
    if (manifest.get('version')!='s4_public_content_addressed_v1' or manifest['frozen_candidate_sha256']!=CANDIDATE or
            manifest['real_transport_allowed'] is not False or digest(archive.read_bytes())!=manifest['archive_sha256']):
        raise ValueError('archive_or_manifest_binding_mismatch')
    folded={}
    for name,row in manifest['files'].items():
        safe(root,name)
        if name.casefold() in folded:raise ValueError('casefold_path_collision')
        folded[name.casefold()]=name
        if set(row)!={'sha256','bytes'} or len(row['sha256'])!=64 or any(c not in '0123456789abcdef' for c in row['sha256']):
            raise ValueError('invalid_object_descriptor')
    for name in folded:
        if any(parent.as_posix().casefold() in folded for parent in PurePosixPath(name).parents if parent.as_posix()!='.'):
            raise ValueError('file_directory_collision')
    for name in manifest.get('ci_test_files',[]):
        safe(root,name)
        if name not in manifest['files'] or not name.startswith('tests/test_') or not name.endswith('.py'):
            raise ValueError('unsafe_CI_test_inventory')
    for name,sha in manifest['frozen_files'].items():
        if manifest['files'].get(name,{}).get('sha256')!=sha:raise ValueError('frozen_mapping_changed')
    expected={row['sha256'] for row in manifest['files'].values()}
    if len(expected)!=manifest['object_count']:raise ValueError('object_count_mismatch')
    blobs={}
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or set(names)!={'blobs/'+s for s in expected}:raise ValueError('archive_inventory_mismatch')
        for sha in expected:
            info=z.getinfo('blobs/'+sha)
            sizes={r['bytes'] for r in manifest['files'].values() if r['sha256']==sha}
            if len(sizes)!=1 or sizes!={info.file_size} or not 0<=info.file_size<=64*1024*1024:
                raise ValueError('object_size_mismatch')
            data=z.read(info)
            if digest(data)!=sha:raise ValueError('object_hash_mismatch')
            blobs[sha]=data
    return manifest,blobs


def restore(destination,root=ROOT):
    destination=Path(destination)
    if ('.git' in destination.parts or any(p.is_symlink() or p.is_junction() for p in (destination,*destination.parents)) or destination.exists()):
        raise ValueError('new_unlinked_destination_required')
    manifest,blobs=checked(root)  # preflight all bytes and paths before writing
    for name in manifest['files']:safe(destination,name)
    destination.mkdir(parents=True)
    for name,row in manifest['files'].items():
        target=safe(destination,name);target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:stream.write(blobs[row['sha256']])
    # The archive itself is a publication artifact, not needed for the methods.
    return {'restored_files':len(manifest['files']),'frozen_bytes_preserved':len(manifest['frozen_files']),
        'destination':str(destination.resolve()),'new_model_calls':0}


def _isolated(root,command,report=None):
    root=Path(root).resolve()
    if root==ROOT.resolve():raise ValueError('use_an_independently_restored_root')
    script=safe(root,'scripts/s4_public_release.py')
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','TEMP','TMP','PATH') if k in os.environ}
    env['DEEPSEEK_API_KEY']='';env['PYTHONIOENCODING']='utf-8'
    args=[sys.executable,'-I',str(script),'_child',command]
    if report:args.append(str(Path(report).resolve()))
    done=subprocess.run(args,cwd=root,env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if done.returncode:raise RuntimeError('isolated_check_failed:'+str(done.returncode))


def audit(root=ROOT):
    from scripts.s4_release_scan import scan_files,scan_bytes,Limits
    root=Path(root);descriptor=read(root/BUNDLE/'publish-files.json');inventory=descriptor['files']
    if any(digest(safe(root,n).read_bytes())!=sha for n,sha in inventory.items()):
        raise ValueError('publication_inventory_bytes_changed')
    # Historical CAS archives include several container/JSON/SQLite layers.
    # The explicit depth is bounded; reaching any limit remains a finding.
    policy=read(root/BUNDLE/'manifest.json')['scan_policy']
    metadata=descriptor.get('required_metadata_files',[])
    for name in metadata:
        if not safe(root,name).is_file():raise ValueError('required_publication_metadata_missing')
    result=scan_files(root,list(inventory)+[n for n in metadata if n not in inventory],limits=Limits(**policy))
    rows=subprocess.check_output(['git','rev-list','--objects','--all'],cwd=root).decode().splitlines()
    ids=[line.split(' ',1)[0] for line in rows]
    raw=subprocess.check_output(['git','cat-file','--batch'],cwd=root,input=('\n'.join(ids)+'\n').encode())
    cursor=0;counts={}
    for oid in ids:
        end=raw.index(b'\n',cursor);header=raw[cursor:end].split();size=int(header[2])
        data=raw[end+1:end+1+size];cursor=end+size+2
        kind=header[1].decode();counts[kind]=counts.get(kind,0)+1
        result['findings']+=scan_bytes('git-object:'+oid,data,limits=Limits(**policy))
    result.update(history_objects_scanned=counts,scan_policy=policy,github_updated=False)
    return result


def child(command,report=None):
    # -I does not trust inherited PYTHONPATH or caller module caches.
    sys.path[:0]=[str(ROOT),str(ROOT/'src')]
    import writscope
    if not Path(writscope.__file__).resolve().is_relative_to(ROOT):raise ValueError('mixed_import_roots')
    from scripts import s4_contract_convergence_offline as r
    from scripts import s4_contract_convergence_acceptance as h
    from scripts import s4_owner_pilot as b
    from scripts.s4_r5_acceptance import offline_guard
    from uuid import uuid4
    if command=='test':
        import pytest
        from scripts import public_release
        public_release.restore(ROOT)
        # The CI inventory is stored alongside restored sources below.
        manifest=read(ROOT/'s4-publication-inventory.json')
        temp=ROOT/'.work'/('public-pytest-'+uuid4().hex);output=Path(report) if report else ROOT/'.work'/('pytest-'+uuid4().hex+'.xml')
        original=os.mkdir
        def compatible(path,mode=0o777,*,dir_fd=None):
            return original(path,0o777 if os.name=='nt' and mode==0o700 else mode,dir_fd=dir_fd)
        import socket,ipaddress
        original_connect,original_connect_ex,original_getaddrinfo=socket.socket.connect,socket.socket.connect_ex,socket.getaddrinfo
        def local(address):
            if not isinstance(address,tuple):return True  # local Unix socket
            try:return ipaddress.ip_address(address[0]).is_loopback
            except ValueError:return address[0]=='localhost'
        def connect(sock,address):
            if not local(address):raise AssertionError('public_CI_forbids_external_connections')
            return original_connect(sock,address)
        def connect_ex(sock,address):
            if not local(address):raise AssertionError('public_CI_forbids_external_connections')
            return original_connect_ex(sock,address)
        def getaddrinfo(host,*args,**kwargs):
            if not local((host,0)):raise AssertionError('public_CI_forbids_external_DNS')
            return original_getaddrinfo(host,*args,**kwargs)
        from unittest.mock import patch
        with ExitStack() as stack:
            stack.enter_context(patch('os.mkdir',compatible));stack.enter_context(patch('socket.socket.connect',connect))
            stack.enter_context(patch('socket.socket.connect_ex',connect_ex));stack.enter_context(patch('socket.getaddrinfo',getaddrinfo))
            status=pytest.main([*manifest['ci_test_files'],'-q','-p','no:cacheprovider','--basetemp',str(temp),'--junitxml',str(output)])
        raise SystemExit(status)
    output={}
    with offline_guard():
        plan=b.read(ROOT/'reports/s4-contract-convergence-v2/offline-candidate.json')
        if r.prepare(plan['price'])!=plan:raise ValueError('restored_candidate_mismatch')
        if command=='verify':
            for name in h.SCENARIOS:
                folder=ROOT/'reports/s4-contract-convergence-v2/controls'
                value=r.evaluate(folder/name)
                if value!=b.read(folder/(name+'-evaluation.json')):raise ValueError('report_mismatch:'+name)
                output[name]=value['replay']['status']
            for version in ('v2','v5'):
                value=r.evaluate(ROOT/f'reports/local/s4-owner-pilot-real-{version}')
                if value!=b.read(ROOT/f'reports/s4-owner-pilot-real-{version}-review-v1/evaluation.json'):raise ValueError('historical_report_mismatch')
                output['historical-'+version]=[row['status'] for row in value['replay']['rows']]
        elif command=='demo':
            destination=ROOT/'reports/local'/('public-s4-demo-'+uuid4().hex)
            value=r.run(plan,destination,fixed_responses=h.fixtures(lookup=False));result=r.evaluate(destination)
            if [row['status'] for row in value['rows']]!=['completed']*3 or result['replay']['status']!='verified_settled':raise ValueError('demo_failed')
            output={'statuses':[row['status'] for row in value['rows']],'fixed_attempts':result['replay']['sent_or_uncertain'],
                'replay':result['replay']['status'],'model_metrics':None}
        else:raise ValueError('unknown_isolated_operation')
    output={'version':'s4_public_offline_check_v1','operation':command,'results':output,'new_model_calls':0,'real_transport_allowed':False}
    if report:write(report,output)
    print(json.dumps(output,ensure_ascii=False))


def main():
    if len(sys.argv)>2 and sys.argv[1]=='_child':
        return child(sys.argv[2],sys.argv[3] if len(sys.argv)>3 else None)
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('build');sub.add_parser('audit')
    x=sub.add_parser('restore');x.add_argument('--destination',type=Path,required=True)
    for name in ('verify','demo','test'):
        x=sub.add_parser(name);x.add_argument('--root',type=Path,required=True);x.add_argument('--report',type=Path)
    args=p.parse_args()
    if args.command=='build':print(json.dumps(build()))
    elif args.command=='restore':
        value=restore(args.destination);manifest=read(ROOT/BUNDLE/'manifest.json')
        write(args.destination/'s4-publication-inventory.json',{'ci_test_files':manifest['ci_test_files']})
        print(json.dumps(value))
    elif args.command=='audit':
        value=audit();print(json.dumps(value,ensure_ascii=False))
        if value['findings']:raise SystemExit(1)
    else:_isolated(args.root,args.command,args.report)


if __name__=='__main__':main()
