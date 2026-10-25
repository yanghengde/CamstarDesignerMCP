"""Default credentials, top-five queries and edits limited to loaded records."""
import base64
from hashlib import sha256
import json
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from designer import testing, testing_runtime as native, testing_store as store, working
from web import routes, testing_routes


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_TESTING_DB', str(tmp_path / 'testing.sqlite'))
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path / 'designer'))
    monkeypatch.setattr(config, 'DESIGNER_TEST_TARGET_CONFIRMED', True)
    monkeypatch.setattr(config, 'DESIGNER_WCF_ADDRESS', 'http://system.invalid/WCF/')
    monkeypatch.setattr(routes, 'CHAT_USERNAME', 'user')
    monkeypatch.setattr(store, 'protect', lambda value, decrypt=False:
                        (base64.b64decode(value).decode() if decrypt else base64.b64encode(value.encode()).decode()) if value else '')
    project_id, artifact_id = 'a' * 32, 'b' * 32
    file = Path(config.DESIGNER_ROOT) / 'workspaces' / project_id / 'InSite.mdb'
    file.parent.mkdir(parents=True)
    file.write_bytes(b'unchanged-design')
    context = {'id': project_id, 'mdb_file': str(file), 'latest_manifest': ''}
    monkeypatch.setattr(working, 'available', lambda: [context])
    monkeypatch.setattr(working, 'summary', lambda identity: context if identity == project_id else None)
    item = {'id': artifact_id, 'client_sha256': 'c' * 64, 'compiled_sha256': 'd' * 64}
    monkeypatch.setattr(native, 'artifact', lambda identity, **kwargs: item if identity == artifact_id else None)
    monkeypatch.setattr(native, 'artifacts', lambda: [item])
    scalar = lambda name: {'name': name, 'schema': {'kind': 'primitive', 'value_type': 'System.String'}}
    schema = {'service': 'ProductMaint', 'methods': [{'name': n} for n in ('Load', 'GetEnvironment', 'ExecuteTransaction')],
              'fields': {'fields': [
                  {'name': 'ObjectToChange', 'schema': {'fields': [scalar('Name'), scalar('Revision')]}},
                  {'name': 'ObjectChanges', 'schema': {'fields': [scalar(n) for n in ('Name', 'Revision', 'Description', 'ExtName')]}}]}}
    state = {'rows': [[f'P{i}', 'A', f'Product {i}'] for i in range(5)], 'calls': [],
             'values': {'Description': 'original', 'ExtName': 'OLD'}, 'outcome': 'success', 'wrong_readback': False}
    def invoke(client, mode, environment=None, **args):
        state['calls'].append((mode, args))
        if mode == 'catalog':
            return {'services': ['ProductMaint', 'EmployeeMaint', 'Authentication']}
        if mode == 'schema':
            return schema
        if mode == 'validate':
            return {'validated': True}
        assert environment['password'] == 'saved-admin-password'
        method = args['method']
        if method == 'GetEnvironment':
            return {'outcome': state['outcome'], 'message': '', 'value': {},
                    'selection': {'headers': ['Name', 'Revision', 'Description'], 'rows': state['rows'], 'limit': 5}}
        reference = args['data']['ObjectToChange']
        if method == 'ExecuteTransaction':
            state['values'].update(args['data']['ObjectChanges'])
        value = {**reference, **state['values']}
        if method == 'Load' and state['wrong_readback']:
            value['Description'] = 'different'
        return {'outcome': 'success', 'message': 'ok', 'value': {'ObjectChanges': value}}
    monkeypatch.setattr(native, 'invoke', invoke)
    app = FastAPI(); app.include_router(routes.router); app.include_router(testing_routes.router)
    with TestClient(app) as client:
        environment = client.post('/api/testing/environments', json={'username': 'user', 'environment': {
            'name': 'existing system', 'endpoint': config.DESIGNER_WCF_ADDRESS, 'business_user': 'CamstarAdmin',
            'password': 'saved-admin-password', 'project_id': project_id, 'artifact_id': artifact_id}}).json()
        yield client, state, file, environment


def action(client, action_name, identity=None, **kwargs):
    from uuid import uuid4
    result = client.post('/api/testing/simple/runs', json={
        'username': 'user', 'request_id': identity or uuid4().hex, 'action': action_name,
        'service': 'ProductMaint', **kwargs})
    if result.status_code != 202:
        return result
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        record = client.get('/api/testing/runs/' + result.json()['id'] + '?username=user').json()
        if record['status'] != 'running':
            return record
        time.sleep(.01)
    pytest.fail('Test did not finish')


def load_first(client):
    query = action(client, 'query')
    return action(client, 'load', source_run_id=query['id'], row_index=0)


def test_default_bootstrap_reuses_account_and_hides_secrets(workbench):
    client, state, *_ = workbench
    result = client.get('/api/testing/simple/bootstrap?username=user')
    assert result.status_code == 200
    assert result.json()['services'] == ['ProductMaint', 'EmployeeMaint']
    assert result.json()['connection']['endpoint'] == config.DESIGNER_WCF_ADDRESS
    assert all(secret not in result.text for secret in ('saved-admin-password', 'protected_password'))
    assert client.get('/api/testing/simple/bootstrap?username=other').status_code == 404
    assert not [call for call in state['calls'] if call[0] == 'run']


def test_list_uses_real_readonly_getenvironment_and_five_row_limit(workbench):
    client, state, *_ = workbench
    record = action(client, 'query')
    assert record['status'] == 'passed'
    call = next(args for mode, args in state['calls'] if mode == 'run')
    assert call['method'] == 'GetEnvironment' and call['selection_limit'] == 5
    assert call['data'] == {} and call['allow_write'] is False
    assert len(record['result']['response']['selection']['rows']) == 5


def test_empty_query_is_success_but_transport_error_is_not(workbench):
    client, state, *_ = workbench
    state['rows'] = []
    record = action(client, 'query')
    assert record['status'] == 'passed' and record['result']['response']['selection']['rows'] == []
    state['outcome'] = 'error'
    assert action(client, 'query')['status'] == 'error'


def test_row_edit_preserves_name_and_exact_revision(workbench):
    client, state, *_ = workbench
    query = action(client, 'query')
    record = action(client, 'load', source_run_id=query['id'], row_index=2)
    assert record['status'] == 'passed'
    assert record['spec']['data'] == {'ObjectToChange': {'Name': 'P2', 'Revision': 'A'}}


def test_update_only_changed_fields_and_independent_readback(workbench):
    client, state, *_ = workbench
    loaded = load_first(client)
    before = len(state['calls'])
    record = action(client, 'update', source_run_id=loaded['id'], changes={'Description': 'changed'}, identity='1' * 32)
    assert record['status'] == 'passed'
    calls = [args for mode, args in state['calls'][before:] if mode == 'run']
    assert [call['method'] for call in calls] == ['ExecuteTransaction', 'Load']
    assert calls[0]['data'] == {'ObjectToChange': {'Name': 'P0', 'Revision': 'A'}, 'ObjectChanges': {'Description': 'changed'}}
    assert calls[1]['selection_limit'] == 0 and calls[1]['allow_write'] is False
    assert record['result']['checks'][-1]['passed'] is True
    assert action(client, 'update', source_run_id=loaded['id'], changes={'Description': 'changed'}, identity='1' * 32)['id'] == record['id']
    assert len([args for mode, args in state['calls'][before:] if mode == 'run']) == 2


def test_readback_difference_fails_verification(workbench):
    client, state, *_ = workbench
    loaded = load_first(client)
    state['wrong_readback'] = True
    assert action(client, 'update', source_run_id=loaded['id'], changes={'Description': 'changed'})['status'] == 'failed'


@pytest.mark.parametrize('changes', [{'Name': 'new'}, {'Revision': 'B'}, {'NotAField': 'x'}, {}])
def test_rejects_create_rename_and_unknown_changes(workbench, changes):
    client, state, *_ = workbench
    loaded = load_first(client)
    before = len(state['calls'])
    assert action(client, 'update', source_run_id=loaded['id'], changes=changes).status_code == 409
    assert not [call for call in state['calls'][before:] if call[0] == 'run']


@pytest.mark.parametrize('name', ['create', 'delete'])
def test_no_create_or_delete_action(workbench, name):
    client, state, *_ = workbench
    assert action(client, name).status_code == 422
    assert not state['calls']


def test_rejects_empty_row_and_unloaded_update(workbench):
    client, state, *_ = workbench
    assert action(client, 'update', changes={'Description': 'x'}).status_code == 409
    state['rows'] = []
    query = action(client, 'query')
    assert action(client, 'load', source_run_id=query['id']).status_code == 409
    assert action(client, 'update', source_run_id=query['id'], changes={'Description': 'x'}).status_code == 409


def test_stale_design_and_environment_stop_editing(workbench):
    client, _, file, environment = workbench
    loaded = load_first(client)
    file.write_bytes(b'new-design')
    assert action(client, 'update', source_run_id=loaded['id'], changes={'Description': 'x'}).status_code == 409
    file.write_bytes(b'unchanged-design')
    old = store.item('user', 'environment', environment['id'])
    old['version'] += 1
    store.save('user', 'environment', old, old['id'])
    assert action(client, 'update', source_run_id=loaded['id'], changes={'Description': 'x'}).status_code == 409


def test_disabled_writes_still_allow_queries(workbench, monkeypatch):
    client, *_ = workbench
    loaded = load_first(client)
    monkeypatch.setattr(config, 'DESIGNER_TEST_TARGET_CONFIRMED', False)
    assert action(client, 'query')['status'] == 'passed'
    assert action(client, 'update', source_run_id=loaded['id'], changes={'Description': 'x'}).status_code == 409


def test_never_send_saved_credentials_to_a_different_default_endpoint(workbench, monkeypatch):
    client, state, *_ = workbench
    monkeypatch.setattr(config, 'DESIGNER_WCF_ADDRESS', 'http://another.invalid/WCF/')
    assert client.get('/api/testing/simple/bootstrap?username=user').status_code == 409
    assert not state['calls']


def test_simple_page_has_only_query_and_row_editor(workbench):
    client, *_ = workbench
    page = client.get('/wcf-test').text
    assert '查询前 5 条' in page and 'editDialog' in page
    assert all(text not in page for text in ('环境设置', 'environmentSelect', 'data-op="create"', 'data-op="delete"', 'objectName'))
