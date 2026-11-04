"""Publication archives cannot overwrite work or alter frozen originals."""
from copy import deepcopy
import json
import zipfile

import pytest

from scripts import s4_public_release as r


def archive(root,files):
    folder=root/r.BUNDLE;folder.mkdir(parents=True)
    blobs={r.digest(data):data for data in files.values()}
    with zipfile.ZipFile(folder/'evidence.zip','x') as z:
        for sha,data in blobs.items():z.writestr('blobs/'+sha,data)
    manifest={'version':'s4_public_content_addressed_v1','frozen_candidate_sha256':r.CANDIDATE,
        'real_transport_allowed':False,'object_count':len(blobs),
        'archive_sha256':r.digest((folder/'evidence.zip').read_bytes()),
        'files':{p:{'sha256':r.digest(data),'bytes':len(data)} for p,data in files.items()},
        'frozen_files':{p:r.digest(data) for p,data in files.items()},'ci_test_files':[]}
    r.write(folder/'manifest.json',manifest)
    return folder,manifest


@pytest.mark.parametrize('name',['../escape','/absolute','C:/drive','a\\b','a//b','.git/config','.env',
    'config/.env.production','.venv/secret','a/../b','NUL','AUX.log','folder/name.','folder/name '])
def test_unsafe_and_credential_paths_rejected_before_read(tmp_path,name):
    with pytest.raises(ValueError):r.safe(tmp_path,name)


def test_deduplicated_blob_restores_every_original_byte(tmp_path):
    root=tmp_path/'package';folder,manifest=archive(root,{'first/raw':b'original\r\n','second/raw':b'original\r\n'})
    assert manifest['object_count']==1
    restored=tmp_path/'restored';result=r.restore(restored,root)
    assert result['restored_files']==result['frozen_bytes_preserved']==2
    assert (restored/'first/raw').read_bytes()==(restored/'second/raw').read_bytes()==b'original\r\n'
    assert r.read(folder/'manifest.json')==manifest


def test_existing_destination_is_never_overwritten(tmp_path):
    root=tmp_path/'package';archive(root,{'raw':b'archive'})
    destination=tmp_path/'user';destination.mkdir();(destination/'raw').write_bytes(b'user')
    with pytest.raises(ValueError,match='new_unlinked_destination'):r.restore(destination,root)
    assert (destination/'raw').read_bytes()==b'user'


@pytest.mark.parametrize('change',['archive','blob','mapping','extra','duplicate','size','ci'])
def test_all_archive_validation_precedes_creation(tmp_path,change):
    root=tmp_path/'package';folder,manifest=archive(root,{'raw':b'original'})
    if change=='archive':(folder/'evidence.zip').write_bytes(b'corrupt')
    elif change=='blob':
        with zipfile.ZipFile(folder/'evidence.zip','w') as z:
            z.writestr('blobs/'+manifest['files']['raw']['sha256'],b'modified')
        manifest['archive_sha256']=r.digest((folder/'evidence.zip').read_bytes())
    elif change in ('extra','duplicate'):
        with zipfile.ZipFile(folder/'evidence.zip','a') as z:
            z.writestr('blobs/'+('0'*64 if change!='duplicate' else manifest['files']['raw']['sha256']),b'corrupt')
        manifest['archive_sha256']=r.digest((folder/'evidence.zip').read_bytes())
    elif change=='mapping':manifest['frozen_files']['raw']='0'*64
    elif change=='size':manifest['files']['raw']['bytes']+=1
    else:manifest['ci_test_files']=['../../outside.py']
    (folder/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    destination=tmp_path/'out'
    with pytest.raises((ValueError,zipfile.BadZipFile)):r.restore(destination,root)
    assert not destination.exists()


def test_source_root_cannot_be_used_as_recovered_execution_root():
    with pytest.raises(ValueError,match='independently_restored'):r._isolated(r.ROOT,'demo')


@pytest.mark.parametrize('files',[{'A':b'a','a':b'b'},{'folder':b'a','folder/raw':b'b'}])
def test_logical_path_collisions_rejected_before_any_write(tmp_path,files):
    root=tmp_path/'package';archive(root,files);destination=tmp_path/'restored'
    with pytest.raises(ValueError,match='collision'):r.restore(destination,root)
    assert not destination.exists()


def test_new_module_has_no_live_or_credential_entry():
    assert not any(hasattr(r,name) for name in ('start_live','console_gate','getpass','key_reader'))
