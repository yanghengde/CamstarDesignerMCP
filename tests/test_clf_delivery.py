"""Durable explicit save commands: scope, drift, duplicate requests and recovery."""
from hashlib import sha256
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from designer import clf_delivery as delivery, clf_workspace as core, vendor
from tests.test_clf_core import setup
from tests.test_excel_attachments import isolated_store


def plan(client, op):
    response = client.post('/api/clf/workspace/plans', json={'username': 'user', 'project_id': op['project_id'],
        'source_sha256': op['source_sha256'], 'operations': [{'action': 'copy_clf', 'name': 'ExCopy', 'template': 'ExLogic'}]})
    assert response.status_code == 200, response.text
    return response.json()


def finish(client, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        r = client.get('/api/clf/workspace/delivery/'+identity+'?username=user').json()
        if r['status'] not in {'queued', 'saving', 'compiling', 'checking_publish', 'restoring'}:
            return r
        time.sleep(.01)
    pytest.fail('Delivery did not complete')


def payload(p, op, **changes):
    return {'username': 'user', 'plan_id': p['id'], 'request_id': '1'*32, 'action': 'save',
            'expected_sha256': op['source_sha256'], 'reviewed': True, **changes}


def fake_save(monkeypatch, op, path, calls, *, fail_after=False, verified=True):
    def design(source, expected, workspace, operations, *, sync_designer=True):
        assert sync_designer is False, 'Core save must not implicitly copy to the Designer server path'
        assert sha256(path.read_bytes()).hexdigest() == expected
        calls.append(operations)
        path.write_bytes(b'saved-clf-project')
        if fail_after:
            raise RuntimeError('保存后回执中断')
        return {'working_project_id': op['project_id'], 'working_mdb': str(path),
                'execution': {'reload_verified': verified}, 'files': {'manifest.json': str(path.parent/'manifest.json')}}
    monkeypatch.setattr(vendor, 'design', design)


def test_save_requires_review_and_owned_checked_plan(setup, monkeypatch):
    client, op, path, _ = setup
    p = plan(client, op)
    calls = []
    fake_save(monkeypatch, op, path, calls)
    assert client.post('/api/clf/workspace/delivery', json=payload(p, op, reviewed=False)).status_code == 409
    assert client.post('/api/clf/workspace/delivery', json=payload(p, op, username='other')).status_code == 404
    assert client.post('/api/clf/workspace/delivery', json={**payload(p, op), 'operations': []}).status_code == 422
    assert calls == []


def test_one_save_per_plan_and_retries_return_original_receipt(setup, monkeypatch):
    client, op, path, _ = setup
    p = plan(client, op)
    calls = []
    fake_save(monkeypatch, op, path, calls)
    with TestClient(client.app) as running:
        first = running.post('/api/clf/workspace/delivery', json=payload(p, op))
        assert first.status_code == 202
        result = finish(running, first.json()['id'])
        assert result['status'] == 'saved'
        assert result['database_published'] is False
        assert result['result']['saved_sha256'] == sha256(path.read_bytes()).hexdigest()
        again = running.post('/api/clf/workspace/delivery', json=payload(p, op, request_id='2'*32)).json()
        assert again['id'] == result['id'] and again['status'] == 'saved'
        assert len(calls) == 1
        assert core.item('user', 'plan', p['id']) == p


def test_source_drift_blocks_writer_and_does_not_relabel_old_plan(setup, monkeypatch):
    client, op, path, _ = setup
    p = plan(client, op)
    calls = []
    fake_save(monkeypatch, op, path, calls)
    path.write_bytes(b'external-edit')
    with TestClient(client.app) as running:
        start = running.post('/api/clf/workspace/delivery', json=payload(p, op)).json()
        result = finish(running, start['id'])
    assert result['status'] == 'failed' and calls == []


@pytest.mark.parametrize('fail_after,verified', [(True, True), (False, False)])
def test_partial_save_keeps_unknown_result_and_never_replays(setup, monkeypatch, fail_after, verified):
    client, op, path, _ = setup
    p = plan(client, op)
    calls = []
    fake_save(monkeypatch, op, path, calls, fail_after=fail_after, verified=verified)
    with TestClient(client.app) as running:
        start = running.post('/api/clf/workspace/delivery', json=payload(p, op)).json()
        result = finish(running, start['id'])
        assert result['status'] == 'needs_review' and result['source_may_have_changed']
        running.post('/api/clf/workspace/delivery', json=payload(p, op, request_id='3'*32))
        assert len(calls) == 1


def test_recovery_never_reexecutes_unfinished_save(setup):
    client, op, _, _ = setup
    p = plan(client, op)
    request = delivery.DeliveryRequest.model_validate(payload(p, op))
    record, created = delivery.claim('user', request)
    assert created
    delivery.recover()
    interrupted = core.item('user', 'delivery', record['id'])
    assert interrupted['status'] == 'interrupted'
    duplicate, created = delivery.claim('user', request)
    assert not created and duplicate['status'] == 'interrupted'


def test_compile_binds_to_saved_source_and_publish_check_is_read_only(setup, monkeypatch):
    client, op, path, _ = setup
    p = plan(client, op)
    calls = []
    fake_save(monkeypatch, op, path, calls)
    from tools import designer_design
    from designer import cycle, publication
    async def compile_copy(source, expected):
        assert sha256(path.read_bytes()).hexdigest() == expected
        output = path.parent/'compiled.mdb'
        output.write_bytes(b'compiled-source')
        return {'source_unchanged': True, 'compiled_mdb': str(output), 'compiled_sha256': sha256(output.read_bytes()).hexdigest()}
    monkeypatch.setattr(designer_design, 'compile_designer_mdb', compile_copy)
    monkeypatch.setattr(cycle, 'prepare_plan', lambda manifest: {'ready_for_publish': False, 'blockers': ['目标未配置']})
    monkeypatch.setattr(publication, 'publish_database', lambda *a, **k: pytest.fail('Check must never publish'))
    with TestClient(client.app) as running:
        started = running.post('/api/clf/workspace/delivery', json=payload(p, op)).json()
        saved = finish(running, started['id'])
        current = saved['result']['saved_sha256']
        compiled = running.post('/api/clf/workspace/delivery', json=payload(p, op, request_id='2'*32, action='compile', expected_sha256=current)).json()
        assert finish(running, compiled['id'])['status'] == 'completed'
        checked = running.post('/api/clf/workspace/delivery', json=payload(p, op, request_id='3'*32, action='check_publish', expected_sha256=current)).json()
        result = finish(running, checked['id'])
        assert result['status'] == 'completed'
        assert result['result']['scope'] == 'whole_current_project_cycle'
        assert result['result']['ready_for_publish'] is False and not result['result']['database_published']


def test_restore_snapshot_is_guarded_by_current_hash_and_keeps_original_receipt(setup, monkeypatch):
    from designer import publication
    client, op, path, _ = setup
    p = plan(client, op)
    calls = []
    original = path.read_bytes()
    fake_save(monkeypatch, op, path, calls)
    baseline = path.parent/'baseline.mdb'
    baseline.write_bytes(original)
    monkeypatch.setattr(publication, 'verified_manifest', lambda value: (path.parent/'manifest.json', {}))
    adopted = []
    def adopt(source, expected, candidate, *, method):
        assert expected == sha256(path.read_bytes()).hexdigest()
        adopted.append(method)
        path.write_bytes(Path(candidate).read_bytes())
        return {'files': {'manifest.json': str(path.parent/'restore.json')}}
    monkeypatch.setattr(vendor, 'adopt_candidate', adopt)
    with TestClient(client.app) as running:
        start = running.post('/api/clf/workspace/delivery', json=payload(p, op)).json()
        saved = finish(running, start['id'])
        restored = running.post('/api/clf/workspace/delivery', json=payload(p, op, action='restore',
            request_id='4'*32, expected_sha256=saved['result']['saved_sha256'])).json()
        result = finish(running, restored['id'])
        assert result['status'] == 'completed' and path.read_bytes() == original
        assert result['result']['restored_sha256'] == op['source_sha256']
        assert core.item('user', 'delivery', saved['id']) == saved
        assert adopted == ['restore_clf_snapshot']


def test_downloads_require_owned_save_and_verified_artifacts(setup, monkeypatch):
    from designer import publication
    client, op, path, _ = setup
    p = plan(client, op)
    manifest = path.parent/'manifest.json'
    for name, value in [('baseline.mdb', b'baseline'), ('changes.xml', b'<Changes/>'), ('manifest.json', b'{}')]:
        (path.parent/name).write_bytes(value)
    verified = []
    def verify(value):
        verified.append(value)
        return manifest, {}
    monkeypatch.setattr(publication, 'verified_manifest', verify)
    core.save('user', 'delivery', p['id'], 0, {'action': 'save', 'status': 'saved',
        'result': {'files': {'manifest.json': str(manifest)}}})
    base = '/api/clf/workspace/delivery/'+p['id']+'/files/'
    assert client.get(base+'baseline?username=other').status_code == 404
    assert client.get(base+'arbitrary?username=user').status_code == 404
    assert verified == []
    response = client.get(base+'baseline?username=user')
    assert response.status_code == 200 and response.content == b'baseline'
    assert 'attachment' in response.headers['content-disposition']
    assert verified == [str(manifest)]
    def tampered(value):
        raise ValueError('设计包发生变化')
    monkeypatch.setattr(publication, 'verified_manifest', tampered)
    assert client.get(base+'changes?username=user').status_code == 409
