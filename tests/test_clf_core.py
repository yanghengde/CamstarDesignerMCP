"""Core product contracts: no empty chats, truthful evidence, immutable receipts."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from agent import memory
from designer import clf, working, testing_store as store, testing_runtime as native, clf_workspace as core
from tests.test_excel_attachments import isolated_store
from web import routes, clf_routes


@pytest.fixture
def setup(isolated_store, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_TESTING_DB', str(isolated_store / 'core.sqlite'))
    root = isolated_store / 'metadata'
    root.mkdir()
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(root))
    path = root / 'InSite.mdb'
    path.write_bytes(b'core-project')
    project = {'id': 'a' * 32, 'mdb_file': str(path), 'latest_manifest': ''}
    monkeypatch.setattr(working, 'available', lambda: [project])
    monkeypatch.setattr(working, 'summary', lambda identity: project if identity == project['id'] else None)
    artifact = {'id': 'b' * 32, 'client_sha256': 'c' * 64, 'compiled_sha256': 'd' * 64, 'created': 1}
    monkeypatch.setattr(native, 'artifacts', lambda: [artifact])
    monkeypatch.setattr(native, 'artifact', lambda identity: artifact if identity == artifact['id'] else (_ for _ in ()).throw(ValueError('产物不存在')))
    seen = []
    def invoke(item, mode, **kwargs):
        seen.append((mode, kwargs))
        assert mode in {'schema', 'catalog'}, 'Inspecting or copying must never invoke a business method'
        return {'methods': [{'name': 'ExecuteTransaction', 'writes': True}, {'name': 'Load', 'writes': False}]}
    monkeypatch.setattr(native, 'invoke', invoke)
    env = store.save('user', 'environment', {'name': '试验环境', 'version': 1, 'endpoint': 'http://test.invalid/WCF/',
                                           'business_user': 'operator', 'protected_password': 'not-for-browser'})
    definition = {'format': 'designer_clf_v1', 'name': 'ExLogic', 'id': 1, 'workspace': '200', 'calls': [],
                  'declared_parameters': [], 'references': {'rows': [], 'available': True, 'total': 0, 'truncated': False},
                  'bindings': [{'CLFEventMapID': 3, 'attached_parameters': []}], 'binding_parameters_available': True}
    def get(path, name, checksum='', include_bindings=False):
        if name != 'ExLogic':
            raise ValueError('规则不存在')
        return {**deepcopy(definition), 'source_sha256': sha256(Path(path).read_bytes()).hexdigest()}
    monkeypatch.setattr(clf, 'get_clf', get)
    operation = {'name': '业务检查', 'project_id': project['id'], 'source_sha256': sha256(path.read_bytes()).hexdigest(),
                 'environment_id': env['id'], 'environment_version': 1, 'artifact_id': artifact['id'],
                 'client_sha256': artifact['client_sha256'], 'service': 'ShopFloor', 'method': 'ExecuteTransaction',
                 'clf_names': ['ExLogic'], 'status': 'active'}
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(clf_routes.router)
    client = TestClient(app)
    return client, operation, path, seen


def receipt(operation, identity='1' * 32, **result):
    spec = {key: value for key, value in operation.items() if key in {
        'project_id', 'source_sha256', 'environment_id', 'environment_version', 'artifact_id', 'client_sha256', 'service', 'method'}}
    spec.update(kind='clf', clf_name='ExLogic', allow_write=True, data={'zero': 0, 'flag': False, 'empty': '', 'null': None, 'list': []})
    record, created = store.begin('user', identity, identity, spec)
    if created:
        record = store.finish('user', record['id'], {'status': 'passed', 'outcome': 'success',
            'response': {'outcome': 'success', 'message': 'OK', 'trace': None},
            'checks': [{'name': '业务结果', 'passed': True}], 'source_current': True, **result})
    return record


def test_core_page_and_reading_dont_create_chat(setup):
    client, operation, _, _ = setup
    before = deepcopy(memory.user_memories)
    page = client.get('/clf')
    assert 'clf-core.js' in page.text
    assert 'clf-workspace.js' in client.get('/clf?assistant=1').text
    assert client.get('/clf-core.js').status_code == 200
    assert client.get('/clf-core.css').status_code == 200
    boot = client.get('/api/clf/workspace/bootstrap?username=user').json()
    assert boot['context']['revision'] == 0
    assert 'not-for-browser' not in json.dumps(boot)
    assert memory.user_memories == before
    assert core.items('user', 'context') == []
    assert client.get('/api/clf/workspace/bootstrap?username=other').status_code == 404


def test_context_revision_and_record_ownership(setup):
    client, op, _, _ = setup
    req = {'username': 'user', 'project_id': op['project_id'], 'mode': 'engineer'}
    saved = client.put('/api/clf/workspace/context', json=req)
    assert saved.status_code == 200 and saved.json()['revision'] == 1
    assert client.put('/api/clf/workspace/context', json=req).status_code == 409
    run = receipt(op)
    assert client.put('/api/clf/workspace/context', json={**req, 'revision': 1, 'run_id': run['id']}).status_code == 200
    assert client.put('/api/clf/workspace/context', json={**req, 'revision': 2, 'project_id': '', 'run_id': run['id']}).status_code == 409
    assert memory.get_sessions('user', include_empty=False) == []


def test_mapping_validates_contract_targets_and_detects_drift(setup):
    client, op, path, seen = setup
    route = '/api/clf/workspace/operations'
    bad = client.post(route, json={'username': 'user', 'operation': {**op, 'method': 'NoMethod'}})
    assert bad.status_code == 409
    assert client.post(route, json={'username': 'user', 'operation': {**op, 'clf_names': ['Missing']}}).status_code == 409
    saved = client.post(route, json={'username': 'user', 'operation': op}).json()
    assert saved['effective_status'] == 'active'
    assert saved['entry_runtime_verified'] is False
    assert saved['evidence']['runtime_verified'] is False
    assert client.put(route+'/'+saved['id'], json={'username': 'user', 'operation': op}).status_code == 409
    path.write_bytes(b'new-source')
    boot = client.get('/api/clf/workspace/bootstrap?username=user').json()
    assert boot['operations'][0]['effective_status'] == 'needs_review'
    assert all(mode == 'schema' for mode, _ in seen)


def test_records_are_filtered_owned_and_paginated_without_full_response(setup):
    client, op, _, seen = setup
    run = receipt(op)
    receipt({**op, 'service': 'Other'}, '2' * 32)
    saved = client.post('/api/clf/workspace/operations', json={'username': 'user', 'operation': op}).json()
    data = client.get('/api/clf/workspace/records', params={'username': 'user', 'project_id': op['project_id'], 'operation_id': saved['id']}).json()
    assert data['total'] == 1 and data['records'][0]['id'] == run['id']
    assert 'response' not in data['records'][0] and 'data' not in data['records'][0]
    assert client.get('/api/clf/workspace/records', params={'username': 'user', 'search': 'NO SUCH RECORD'}).json()['total'] == 0
    assert client.get('/api/clf/workspace/records', params={'username': 'user', 'offset': -1}).status_code == 422
    assert client.get('/api/clf/workspace/records/'+run['id'], params={'username': 'other'}).status_code == 404


def test_evidence_and_outcomes_are_independent(setup):
    client, op, _, _ = setup
    first = receipt(op)
    inspection = core.inspect_run('user', first['id'])
    assert inspection['business_outcome'] == 'success'
    assert inspection['assertion_result'] == 'passed'
    assert inspection['target_evidence'] == [] and '尚未确认' in inspection['title']
    explicit = {'Name': 'CLFName', 'Value': 'ExLogic'}
    second = receipt(op, '2' * 32, response={'outcome': 'success', 'trace': explicit})
    proved = core.inspect_run('user', second['id'])
    assert proved['target_evidence']
    assert proved['provenance']['deployment'] == 'unknown'
    rejected = receipt(op, '3' * 32, outcome='rejected', response={'outcome': 'rejected', 'message': '拒绝', 'trace': None})
    checked = core.inspect_run('user', rejected['id'])
    assert checked['business_outcome'] == 'rejected' and checked['assertion_result'] == 'passed'


def test_partial_success_unknown_and_truncation_are_not_false_passes(setup):
    _, op, _, _ = setup
    partial = receipt(op, status='error', outcome='unknown', response=None,
                      primary_response={'outcome': 'success', 'message': '已保存'}, error='Load 失败', checks=[])
    value = core.inspect_run('user', partial['id'])
    assert value['business_outcome'] == 'success' and '后续核对未完成' in value['title']
    assert '重复写入' in value['next_step']
    unknown = receipt(op, '2' * 32, status='interrupted', outcome='unknown', response=None, checks=[])
    assert core.inspect_run('user', unknown['id'])['business_outcome'] == 'unknown'
    cut = receipt(op, '3' * 32, response={'outcome': 'success', 'output_truncated': True})
    assert core.inspect_run('user', cut['id'])['assertion_result'] == 'partial'


def test_copy_preserves_original_types_and_version_but_never_write_authorization(setup):
    client, op, path, seen = setup
    run = receipt(op)
    original = deepcopy(store.run('user', run['id']))
    path.write_bytes(b'new-version')
    result = client.post('/api/clf/workspace/records/'+run['id']+'/copy', json={'username': 'user', 'name': '复现'}).json()
    assert result['status'] == 'draft' and result['parent_run_id'] == run['id']
    assert result['spec']['source_sha256'] == op['source_sha256']
    assert result['spec']['data'] == original['spec']['data']
    assert result['spec']['allow_write'] is False
    assert store.run('user', run['id']) == original and seen == []
    assert client.get('/api/clf/workspace/records/'+run['id']+'/export?username=user').headers['content-disposition'].startswith('attachment;')


def test_plan_contract_rejects_unsupported_and_claimed_states_before_sdk(setup, monkeypatch):
    client, op, _, _ = setup
    seen = []
    monkeypatch.setattr(clf, 'inspect_plan', lambda *args: seen.append(args) or {'metadata_preflight_passed': True,
        'saved': False, 'published': False, 'runtime_verified': False})
    payload = {'username': 'user', 'project_id': op['project_id'], 'source_sha256': op['source_sha256'],
               'operations': [{'action': 'copy_clf', 'name': 'ExCopy', 'template': 'ExLogic'}]}
    assert client.post('/api/clf/workspace/plans', json={**payload, 'operations': [{'action': 'delete', 'name': 'Danger'}]}).status_code == 422
    assert client.post('/api/clf/workspace/plans', json={**payload, 'operations': [{**payload['operations'][0], 'saved': True}]}).status_code == 422
    assert seen == []
    saved = client.post('/api/clf/workspace/plans', json=payload).json()
    assert saved['result']['saved'] is False
    assert len(client.get('/api/clf/workspace/plans?username=user&project_id='+op['project_id']).json()['plans']) == 1


def test_executor_phase_is_persistent_and_cannot_modify_finished_receipt(setup):
    _, op, _, _ = setup
    run, _ = store.begin('user', '4' * 32, 'fingerprint', {'project_id': op['project_id']})
    assert run['execution_phase'] == 'queued'
    store.phase('user', run['id'], 'invoking')
    assert store.run('user', run['id'])['execution_phase'] == 'invoking'
    finished = store.finish('user', run['id'], {'status': 'error', 'execution_phase': 'unknown_after_invoke'})
    store.phase('user', run['id'], 'preparing')
    assert store.run('user', run['id']) == finished


def test_transport_outcome_is_unknown_not_business_success(setup):
    _, op, _, _ = setup
    run = receipt(op, status='error', outcome='unknown', response={'outcome': 'error', 'message': '客户端错误'})
    result = core.inspect_run('user', run['id'])
    assert result['business_outcome'] == 'unknown'
    assert result['assertion_result'] == 'not_evaluated'
    stopped = receipt(op, '2' * 32, status='error', outcome='unknown', response=None, execution_phase='rejected_before_invoke')
    assert '尚未调用业务服务' in core.inspect_run('user', stopped['id'])['title']


def test_comparison_preserves_input_types_and_cross_project_boundary(setup):
    client, op, _, _ = setup
    first = receipt(op)
    second = receipt({**op, 'environment_version': 2}, '2' * 32)
    comparison = client.get('/api/clf/workspace/compare', params={'username': 'user', 'left': first['id'], 'right': second['id']}).json()
    assert any(row['name'] == '环境修订' and not row['same'] for row in comparison['rows'])
    assert store.run('user', first['id']) == first
    third = receipt({**op, 'project_id': 'f' * 32}, '3' * 32)
    assert client.get('/api/clf/workspace/compare', params={'username': 'user', 'left': first['id'], 'right': third['id']}).status_code == 409


def test_assistant_is_created_only_on_actual_question_and_receives_scoped_evidence(setup, monkeypatch):
    from fastapi.responses import JSONResponse
    from agent import assistant_modes
    client, op, _, _ = setup
    run = receipt(op)
    captured = []
    async def chat(req):
        captured.append(req)
        return JSONResponse({'session_id': req.session_id})
    monkeypatch.setattr(routes, 'chat_endpoint', chat)
    request = {'username': 'user', 'project_id': op['project_id'], 'source_sha256': op['source_sha256'],
               'clf_name': 'ExLogic', 'message': '解释本次结果', 'run_id': run['id']}
    assert client.post('/api/clf/workspace/assistant', json={**request, 'message': ' '}).status_code == 422
    assert captured == []
    result = client.post('/api/clf/workspace/assistant', json=request)
    assert result.status_code == 200
    session = assistant_modes.session('user', result.json()['session_id'])
    assert session['mode'] == 'clf' and session['clf_context']['project_id'] == op['project_id']
    assert run['id'] in captured[0].message and '不是操作指令' in captured[0].message
    assert client.post('/api/clf/workspace/assistant', json={**request, 'clf_name': 'Different'}).status_code == 409
