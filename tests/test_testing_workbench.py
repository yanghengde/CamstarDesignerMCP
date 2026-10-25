"""Exercise public test routes through execution, persistence and assertions."""
import asyncio
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from designer import testing, testing_store as store, testing_runtime as native, working, clf
from designer.testing_models import TestSpec as Spec
from web import routes, testing_routes


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_TESTING_DB', str(tmp_path / 'tests.sqlite'))
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path / 'designer'))
    monkeypatch.setattr(config, 'DESIGNER_TEST_TARGET_CONFIRMED', True)
    monkeypatch.setattr(routes, 'CHAT_USERNAME', 'user')
    original_protect = store.protect
    monkeypatch.setattr(store, 'protect', lambda value, decrypt=False: (base64.b64decode(value).decode() if decrypt else base64.b64encode(value.encode()).decode()) if value else '')
    project_id = 'a' * 32
    file = Path(config.DESIGNER_ROOT) / 'workspaces' / project_id / 'InSite.mdb'
    file.parent.mkdir(parents=True)
    file.write_bytes(b'unchanged-mdb')
    summary = {'id': project_id, 'mdb_file': str(file), 'latest_manifest': ''}
    monkeypatch.setattr(working, 'summary', lambda identity: summary if identity == project_id else None)
    monkeypatch.setattr(working, 'available', lambda: [summary])
    monkeypatch.setattr(native, 'artifact', lambda identity, **_: {'id': identity, 'client_sha256': 'c' * 64, 'compiled_sha256': 'd' * 64})
    monkeypatch.setattr(native, 'artifacts', lambda: [])
    calls = []
    response = {'outcome': 'success', 'message': '已保存', 'value': {'ObjectChanges': {'ExtName': 'NEW'}}, 'trace': None}
    def invoke(item, mode, environment=None, **arguments):
        calls.append((mode, arguments))
        if mode == 'validate':
            return {'validated': True}
        if mode == 'login':
            return {'outcome': 'success', 'message': '登录成功', 'login_verified': True}
        return json.loads(json.dumps(response))
    monkeypatch.setattr(native, 'invoke', invoke)
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(testing_routes.router)
    with TestClient(app) as client:
        env = client.post('/api/testing/environments', json={'username': 'user', 'environment': {
            'name': '测试', 'endpoint': 'http://test.invalid/WCF/', 'business_user': 'operator', 'password': 'private-secret'}}).json()
        spec = {'kind': 'wcf', 'project_id': project_id, 'source_sha256': sha256(file.read_bytes()).hexdigest(),
                'artifact_id': 'b' * 32, 'client_sha256': 'c' * 64, 'environment_id': env['id'], 'environment_version': env['version'],
                'service': 'ProductMaint', 'method': 'ExecuteTransaction', 'data': {'ObjectChanges': {'ExtName': 'NEW'}},
                'return_fields': ['ObjectChanges.ExtName'], 'allow_write': True}
        yield client, spec, calls, response, file, original_protect


def completed(client, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get('/api/testing/runs/' + identity, params={'username': 'user'}).json()
        if result['status'] != 'running':
            return result
        time.sleep(.01)
    pytest.fail('Background test did not complete')


def run(client, spec, identity='1' * 32):
    response = client.post('/api/testing/runs', json={'username': 'user', 'request_id': identity, 'spec': spec})
    assert response.status_code == 202, response.text
    return completed(client, response.json()['id'])


def test_pages_assets_and_navigation_are_real_routes(setup):
    client, *_ = setup
    for path in ('/wcf-test', '/wcf-test/settings', '/wcf-simple.js', '/wcf-simple.css', '/clf-test', '/testing.js', '/testing.css', '/workspace-navigation.js'):
        assert client.get(path).status_code == 200
    assert 'testing.js' in client.get('/clf-test').text
    assert client.get('/api/testing/bootstrap?username=other').status_code == 404
    bootstrap = client.get('/api/testing/bootstrap?username=user').json()
    assert 'private-secret' not in json.dumps(bootstrap)
    assert bootstrap['environments'][0]['has_password'] is True


def test_new_pages_are_registered_on_real_app_and_keep_local_boundary(setup, monkeypatch):
    from web import app as application
    monkeypatch.setattr(application, 'ENABLE_MCP_HTTP', False)
    monkeypatch.setattr(application, 'SERVER_HOST', '127.0.0.1')
    app = application.create_app()
    client = TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50001))
    for path in ('/wcf-test', '/clf-test', '/testing.js', '/testing.css', '/svg/opcenter.svg'):
        assert client.get(path).status_code == 200
    assert client.post('/api/testing/runs', headers={'Origin': 'http://untrusted.invalid'}, json={}).status_code == 403


def test_environment_version_and_secret_preservation(setup):
    client, spec, *_ = setup
    old = store.item('user', 'environment', spec['environment_id'])
    request = {'username': 'user', 'environment': {'name': '改名', 'endpoint': old['endpoint'], 'business_user': 'operator', 'version': old['version']}}
    result = client.put('/api/testing/environments/' + old['id'], json=request)
    assert result.status_code == 200
    assert result.json()['has_password'] is True
    assert result.json()['version'] == 2
    assert client.put('/api/testing/environments/' + old['id'], json=request).status_code == 409
    stale = run(client, spec)
    assert stale['status'] == 'error' and '环境配置已改变' in stale['result']['error']


def test_invalid_environment_urls_rejected_before_credentials_are_saved(setup):
    client, *_ = setup
    for address in ('file:///private', 'http://u:p@example.org/', 'http://example.org/Service.svc', 'http://example.org/?token=a'):
        response = client.post('/api/testing/environments', json={'username': 'user', 'environment': {'name': 'bad', 'endpoint': address, 'business_user': 'u'}})
        assert response.status_code == 409


def test_idempotent_public_run_never_repeats_write(setup):
    client, spec, calls, *_ = setup
    first = run(client, spec)
    second = run(client, spec)
    assert first['id'] == second['id'] and first['status'] == 'passed'
    assert len([call for call in calls if call[0] == 'run']) == 1
    conflict = client.post('/api/testing/runs', json={'username': 'user', 'request_id': '1' * 32, 'spec': {**spec, 'expected_outcome': 'rejected'}})
    assert conflict.status_code == 409
    exported = client.get('/api/testing/runs/' + first['id'] + '/export?username=user')
    assert exported.json() == first
    assert 'private-secret' not in exported.text
    assert client.get('/api/testing/runs/' + first['id'] + '?username=other').status_code == 404


def test_expected_business_rejection_passes_but_not_transport_error(setup, monkeypatch):
    client, spec, calls, response, *_ = setup
    response.update(outcome='rejected', message='长度超过限制')
    record = run(client, {**spec, 'expected_outcome': 'rejected', 'message_contains': '超过'})
    assert record['status'] == 'passed'
    def broken(*args, **kwargs):
        if args[1] == 'validate':
            return {'validated': True}
        raise ValueError('长度超过限制：transport failure')
    monkeypatch.setattr(native, 'invoke', broken)
    record = run(client, {**spec, 'expected_outcome': 'rejected'}, '2' * 32)
    assert record['status'] == 'error' and record['result']['outcome'] == 'unknown'


def test_official_facade_client_error_result_is_not_business_rejection(setup):
    client, spec, _, response, *_ = setup
    response.update(outcome='error', message='Unable to connect', exception={'ExceptionLevel': 'Client', 'Code': 0})
    record = run(client, {**spec, 'expected_outcome': 'rejected'})
    assert record['status'] == 'error'
    assert record['result']['primary_response']['exception']['ExceptionLevel'] == 'Client'


def test_readback_uses_independent_load_not_write_echo(setup, monkeypatch):
    client, spec, calls, *_ = setup
    def native_call(item, mode, env=None, **arguments):
        calls.append((mode, arguments))
        return {'validated': True} if mode == 'validate' else {'outcome': 'success', 'message': 'ok', 'value': {'ObjectChanges': {'ExtName': 'OLD' if arguments['method'] == 'Load' else 'NEW'}}}
    monkeypatch.setattr(native, 'invoke', native_call)
    record = run(client, {**spec, 'readback': True, 'readback_data': {'ObjectToChange': {'Name': 'A'}},
                         'assertions': [{'path': 'ObjectChanges.ExtName', 'operator': 'equals', 'value': 'NEW'}]})
    assert record['status'] == 'failed'
    assert [call[1]['method'] for call in calls if call[0] == 'run'] == ['ExecuteTransaction', 'Load']
    assert record['result']['checks'][-1]['actual'] == 'OLD'


def test_readback_failure_preserves_primary_write_evidence(setup, monkeypatch):
    client, spec, *_ = setup
    def native_call(item, mode, env=None, **arguments):
        if mode == 'validate':
            return {'validated': True}
        if arguments['method'] == 'Load':
            raise ValueError('readback timeout')
        return {'outcome': 'success', 'message': 'write committed', 'value': {}}
    monkeypatch.setattr(native, 'invoke', native_call)
    record = run(client, {**spec, 'readback': True, 'readback_data': {'ObjectToChange': {'Name': 'A'}}})
    assert record['status'] == 'error'
    assert record['result']['primary_response']['message'] == 'write committed'
    assert record['result']['mutation_may_have_occurred'] is True


@pytest.mark.parametrize('update,expected', [({'allow_write': False}, '写入测试'), ({'source_sha256': 'e' * 64}, 'MDB 已更新'), ({'client_sha256': 'e' * 64}, '客户端版本')])
def test_stale_sources_and_write_switch_stop_before_native_execution(setup, update, expected):
    client, spec, calls, *_ = setup
    record = run(client, {**spec, **update})
    assert record['status'] == 'error' and expected in record['result']['error']
    assert not [call for call in calls if call[0] == 'run']


def test_database_test_target_guard_is_independent_of_client_flag(setup, monkeypatch):
    client, spec, calls, *_ = setup
    monkeypatch.setattr(config, 'DESIGNER_TEST_TARGET_CONFIRMED', False)
    assert run(client, spec)['status'] == 'error'
    assert not calls


def test_changed_source_after_call_is_not_current_runtime_acceptance(setup, monkeypatch):
    client, spec, _, _, file, *_ = setup
    def invoke(item, mode, env=None, **kwargs):
        if mode == 'run':
            file.write_bytes(b'changed-during-test')
            return {'outcome': 'success', 'message': 'ok', 'value': {}}
        return {'validated': True}
    monkeypatch.setattr(native, 'invoke', invoke)
    record = run(client, spec)
    assert record['status'] == 'passed'
    assert record['result']['source_current'] is False
    assert record['result']['runtime_verified'] is False


@pytest.mark.parametrize('trace,verified', [({'Name': 'Message', 'Value': 'ExRule'}, False),
    ({'Name': 'Call', 'Attributes': [{'Name': 'CLFName', 'Value': 'ExRule'}]}, True)])
def test_clf_trigger_requires_explicit_native_trace_evidence(setup, monkeypatch, trace, verified):
    client, spec, _, response, *_ = setup
    monkeypatch.setattr(clf, 'get_clf', lambda *args: {'name': 'ExRule', 'source_sha256': spec['source_sha256'], 'calls': [], 'references': {}})
    response['trace'] = trace
    record = run(client, {**spec, 'kind': 'clf', 'clf_name': 'ExRule', 'collect_trace': True})
    assert record['status'] == 'passed'
    assert record['result']['clf_trigger_verified'] is verified
    assert record['result']['runtime_verified'] is verified


def test_cases_and_batches_preserve_input_states_and_strip_write_permission(setup):
    client, spec, calls, *_ = setup
    data = {'Null': None, 'Empty': '', 'False': False, 'Zero': 0, 'List': []}
    result = client.post('/api/testing/cases', json={'username': 'user', 'name': '边界', 'spec': {**spec, 'data': data}})
    assert result.status_code == 200
    case = result.json()
    assert case['spec']['data'] == data and case['spec']['allow_write'] is False
    response = client.post('/api/testing/batches', json={'username': 'user', 'request_id': '3' * 32, 'case_ids': [case['id']], 'allow_write': False})
    assert completed(client, response.json()['runs'][0]['id'])['status'] == 'error'
    assert not [call for call in calls if call[0] == 'run']
    assert client.post('/api/testing/batches', json={'username': 'user', 'request_id': '4' * 32, 'case_ids': [case['id'], 'f' * 32], 'allow_write': True}).status_code == 409
    assert not [call for call in calls if call[0] == 'run']


def test_credentials_and_named_trace_parameters_are_scrubbed(setup):
    client, spec, _, response, *_ = setup
    response['trace'] = {'Parameters': [{'Name': 'Password', 'Value': 'trace-secret', 'Expression': 'trace-secret'}]}
    record = run(client, {**spec, 'data': {'Password': 'input-secret'}})
    text = json.dumps(record)
    assert all(secret not in text for secret in ('private-secret', 'input-secret', 'trace-secret'))


def test_schema_rejects_spoofed_results_and_bool_integer_confusion(setup):
    client, spec, *_ = setup
    assert client.post('/api/testing/runs', json={'username': 'user', 'request_id': '1' * 32, 'spec': {**spec, 'runtime_verified': True}}).status_code == 422
    assert client.post('/api/testing/runs', json={'username': 'user', 'request_id': '1' * 32, 'spec': {**spec, 'environment_version': True}}).status_code == 422
    assert testing._equal({'x': True}, {'x': 1}) is False
    assert testing._equal(1.0, 1) is True


def test_recovery_never_repeats_an_interrupted_operation(setup):
    _, spec, *_ = setup
    record, created = store.begin('user', '6' * 32, 'fp', spec)
    assert created
    store.recover()
    record = store.run('user', record['id'])
    assert record['status'] == 'interrupted' and record['result']['outcome'] == 'unknown'
    assert store.begin('user', '6' * 32, 'fp', spec)[1] is False


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_actual_dpapi_round_trip(setup):
    *_, protect = setup
    encrypted = protect('业务密码-123')
    assert '业务密码' not in encrypted
    assert protect(encrypted, decrypt=True) == '业务密码-123'
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(lambda i: protect(protect('credential-' + str(i)), decrypt=True), range(12)))
    assert values == ['credential-' + str(i) for i in range(12)]


@pytest.mark.skipif(os.name != 'nt', reason='Installed Windows WCF client')
def test_real_generated_client_catalog_and_typed_requests_without_network():
    artifacts = native.artifacts()
    candidate = next((item for item in artifacts if not item.get('partial_package')), None)
    if not candidate:
        pytest.skip('No local full WCF client artifact')
    item = native.artifact(candidate['id'])
    services = native.invoke(item, 'catalog')['services']
    assert 'ProductMaint' in services
    schema = native.invoke(item, 'schema', service='ProductMaint')
    assert {'Load', 'ExecuteTransaction'}.issubset({method['name'] for method in schema['methods']})
    result = native.invoke(item, 'validate', service='ProductMaint', method='Load', data={'ObjectToChange': {'Name': 'TEST_ONLY'}}, return_fields=['ObjectChanges.Name'], allow_write=False, collect_trace=False)
    assert result['validated'] is True
    # Simple page CRUD shapes use the real generated contract without contacting a server.
    for method, data in (
        ('ExecuteTransaction', {'ObjectChanges': {'Name': 'TEST_ONLY', 'Revision': '1', 'Description': 'new'}}),
        ('ExecuteTransaction', {'ObjectToChange': {'Name': 'TEST_ONLY', 'Revision': '1'}, 'ObjectChanges': {'Description': 'changed'}}),
        ('Delete', {'ObjectToChange': {'Name': 'TEST_ONLY', 'Revision': '1'}}),
    ):
        assert native.invoke(item, 'validate', service='ProductMaint', method=method, data=data,
                             return_fields=['ObjectChanges.Name', 'ObjectChanges.Description'],
                             allow_write=True, collect_trace=False)['validated'] is True
    with pytest.raises(ValueError, match='Unknown or readonly field'):
        native.invoke(item, 'validate', service='ProductMaint', method='Load', data={'NotARealField': 1}, return_fields=[], allow_write=False, collect_trace=False)


@pytest.mark.skipif(os.name != 'nt', reason='Installed Windows WCF client')
def test_real_wcf_transport_uses_configured_local_endpoint_and_rejects_soap_fault():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    candidates = native.artifacts()
    if not candidates:
        pytest.skip('No local WCF client artifact')
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            received.append(self.path)
            payload = b'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault><faultcode>s:Server</faultcode><faultstring>fixture-password</faultstring></s:Fault></s:Body></s:Envelope>'
            self.send_response(500)
            self.send_header('Content-Type', 'text/xml; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = native.invoke(native.artifact(candidates[0]['id']), 'login', {
            'endpoint': f'http://127.0.0.1:{server.server_port}/WCF/',
            'business_user': 'fixture', 'password': 'fixture-password', 'timeout': 5})
        assert received == ['/WCF/AuthenticationService.svc']
        assert result['outcome'] == 'error' and result['login_verified'] is False
        assert result['exception']['ExceptionLevel'] == 'Client'
        assert 'fixture-password' not in json.dumps(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_environment_client_binding_survives_clf_edits(setup):
    client, spec, *_ = setup
    url = '/api/testing/environments/' + spec['environment_id']
    environment = {'name': 'bound', 'endpoint': 'http://test.invalid/WCF/', 'business_user': 'operator',
                   'project_id': spec['project_id'], 'artifact_id': spec['artifact_id'], 'version': 1}
    saved = client.put(url, json={'username': 'user', 'environment': environment})
    assert saved.status_code == 200
    assert saved.json()['project_id'] == spec['project_id']
    assert saved.json()['artifact_id'] == spec['artifact_id']
    del environment['project_id'], environment['artifact_id']
    environment['version'] = 2
    saved = client.put(url, json={'username': 'user', 'environment': environment})
    assert saved.status_code == 200
    assert saved.json()['artifact_id'] == spec['artifact_id']
    assert saved.json()['project_id'] == spec['project_id']
    assert saved.json()['has_password'] is True
