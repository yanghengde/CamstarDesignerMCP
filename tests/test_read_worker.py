import json
import sys
import time

import pytest

from designer import read_worker, vendor
from tests.test_designer_creation_preflight import installed_sdk


@pytest.fixture(autouse=True)
def cleanup():
    read_worker.close()
    yield
    read_worker.close()


def test_worker_reuses_identity_restarts_on_change_and_recovers_timeout(tmp_path, monkeypatch):
    script = """
import sys,json,pathlib,time
for line in sys.stdin:
 c=json.loads(line)
 req=json.loads(pathlib.Path(c['input']).read_text())
 if req.get('hang'): time.sleep(10)
 pathlib.Path(c['output']+'.done').write_text('ok')
"""
    args = [sys.executable, '-u', '-c', script]
    request = tmp_path / 'request.json'
    request.write_text('{}')
    assert not read_worker.execute(args, tmp_path, 'mdb1-sdk1', 5)
    pid = read_worker._process.pid
    assert read_worker.execute(args, tmp_path, 'mdb1-sdk1', 5)
    assert read_worker._process.pid == pid
    monkeypatch.setattr(read_worker.config, 'DESIGNER_READ_WORKER_IDLE_SECONDS', 900)
    monkeypatch.setattr(read_worker, '_last_used', time.monotonic() - 600)
    assert read_worker.execute(args, tmp_path, 'mdb1-sdk1', 5)
    assert read_worker._process.pid == pid
    monkeypatch.setattr(read_worker, '_last_used', time.monotonic() - 901)
    assert not read_worker.execute(args, tmp_path, 'mdb1-sdk1', 5)
    assert read_worker._process.pid != pid
    pid = read_worker._process.pid
    assert not read_worker.execute(args, tmp_path, 'mdb2-sdk1', 5)
    assert read_worker._process.pid != pid
    assert not read_worker.execute(args, tmp_path, 'mdb2-sdk2', 5)
    request.write_text('{"hang":true}')
    with pytest.raises(ValueError, match='超时'):
        read_worker.execute(args, tmp_path, 'mdb2-sdk2', .1)
    assert read_worker._process is None
    request.write_text('{}')
    assert not read_worker.execute(args, tmp_path, 'mdb2-sdk2', 5)


def test_installed_worker_matches_fresh_process_and_isolates_mutation(installed_sdk, tmp_path):
    first = vendor.query(str(installed_sdk), 'get', 'cdo', 'Product')
    pid = read_worker._process.pid
    second = vendor.query(str(installed_sdk), 'inheritance', 'cdo', 'Workflow')
    assert read_worker._process.pid == pid
    folder = tmp_path / 'fresh'; folder.mkdir()
    fresh = vendor.run(folder, {'mode': 'inheritance', 'kind': 'cdo', 'name': 'Workflow', 'mdb': str(installed_sdk)})
    assert all(second[key] == value for key, value in fresh.items())
    vendor.preflight(str(installed_sdk), vendor.digest(installed_sdk),
                     [{'action': 'create_cdo', 'name': 'ExWorkerProbe', 'parent': 'Product'}])
    assert read_worker._process.pid == pid
    result = vendor.query(str(installed_sdk), 'list', 'cdo', search='ExWorkerProbe')
    assert result['total'] == 0
    assert first['name'] == 'Product'


def test_resident_mdb_survives_request_cleanup_and_is_removed_with_worker(tmp_path):
    script = '''
import sys,json,pathlib
resident=None
for line in sys.stdin:
 c=json.loads(line);req=json.loads(pathlib.Path(c['input']).read_text())
 if resident is None: resident=pathlib.Path(req['mdb'])
 # Model the SDK's delayed reopening after the first request's copy is removed.
 assert resident.read_bytes()==b'original metadata'
 pathlib.Path(c['output']+'.done').write_text('ok')
'''
    args=[sys.executable,'-u','-c',script]
    first=tmp_path/'first';first.mkdir()
    original=first/'read.mdb';original.write_bytes(b'original metadata')
    (first/'request.json').write_text(json.dumps({'mdb':str(original)}))
    assert not read_worker.execute(args,first,'same-version',5)
    resident=read_worker._resident
    assert resident.is_file() and resident!=original
    original.unlink()
    second=tmp_path/'second';second.mkdir()
    current=second/'read.mdb';current.write_bytes(b'original metadata')
    (second/'request.json').write_text(json.dumps({'mdb':str(current)}))
    assert read_worker.execute(args,second,'same-version',5)
    resident.write_bytes(b'changed unexpectedly')
    with pytest.raises(ValueError,match='只读查询失败'):
        read_worker.execute(args,second,'same-version',5)
    assert read_worker._process is None and not resident.exists()


def test_worker_cannot_take_ownership_of_original_mdb(tmp_path):
    original=tmp_path/'original.mdb';original.write_bytes(b'keep')
    (tmp_path/'request.json').write_text(json.dumps({'mdb':str(original)}))
    with pytest.raises(ValueError):
        read_worker.execute([sys.executable,'-c','pass'],tmp_path,'version',5)
    assert original.read_bytes()==b'keep' and read_worker._process is None
