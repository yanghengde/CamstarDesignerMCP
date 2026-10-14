import json
import sys

import pytest

from designer import read_worker, vendor
from tests.test_designer_creation_preflight import installed_sdk


@pytest.fixture(autouse=True)
def cleanup():
    read_worker.close()
    yield
    read_worker.close()


def test_worker_reuses_identity_restarts_on_change_and_recovers_timeout(tmp_path):
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
