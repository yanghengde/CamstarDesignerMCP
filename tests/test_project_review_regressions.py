"""Regression coverage for the five project review findings (isolated writes only)."""
import json
import os
from pathlib import Path
import shutil
import threading
import subprocess
import sys
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import config
from designer import vendor, working, progress_actions
from agent import memory, langgraph_runtime as runtime, safety
from web import routes


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    monkeypatch.setattr(config, 'DESIGNER_PROGRESS_DB', str(tmp_path / 'progress.sqlite'))
    monkeypatch.setattr(memory, 'SESSIONS_DIR', str(tmp_path / 'sessions'))
    monkeypatch.setattr(memory, 'user_memories', {})
    return tmp_path

@pytest.fixture
def sdk_copy(isolated):
    if not os.getenv('DESIGNER_TEST_VENDOR'):
        pytest.skip('set DESIGNER_TEST_VENDOR=1 for native SDK regression')
    seed = Path(os.environ['DESIGNER_TEST_MDB']).resolve()
    before = vendor.digest(seed)
    candidate = isolated / 'isolated.mdb'
    shutil.copyfile(seed, candidate)
    yield candidate
    assert vendor.digest(seed) == before

def apply(candidate, operations):
    from designer.files import artifact_dir
    result = vendor.run(artifact_dir(), {'mode':'apply', 'mdb':str(candidate), 'workspace':'200', 'operations':operations})
    assert result['reload_verified']
    return result

@pytest.mark.parametrize('persistent', [True, False])
def test_paired_field_rename_must_keep_maintenance_name_in_sync(sdk_copy, persistent):
    old, new = 'ExReviewRename', 'ExReviewRenamed'
    apply(sdk_copy, [{'action':'add_field','owner':'Product','name':old,'field_type':'Name','persistent':persistent,'portal_editable':True}])
    apply(sdk_copy, [{'action':'patch','kind':'field','owner':'Product','name':old,
                     'changes':{'FieldName':new},'expected':{'FieldName':old},'portal_editable':False}])
    data = vendor.query(str(sdk_copy), 'get', 'field', name=new, owner='Product')
    maintenance = vendor.query(str(sdk_copy), 'list', 'field', owner='ProductChanges', search='ExReviewRename', limit=100)
    names = [row['name'] for row in maintenance['records']]
    assert new in names and old not in names

@pytest.mark.parametrize('persistent', [True, False])
def test_paired_field_type_change_must_update_maintenance_type(sdk_copy, persistent):
    name = 'ExReviewType'
    apply(sdk_copy, [{'action':'add_field','owner':'Product','name':name,'field_type':'Name','persistent':persistent,'portal_editable':True}])
    apply(sdk_copy, [{'action':'change_field_type','owner':'Product','name':name,
                     'field_type':'[Integer]','expected_field_type':'Name'}])
    data = vendor.query(str(sdk_copy), 'get', 'field', name=name, owner='Product')
    maintenance = vendor.query(str(sdk_copy), 'get', 'field', name=name, owner='ProductChanges')
    assert data['FieldDefId'] == maintenance['FieldDefId']

@pytest.mark.parametrize('persistent', [True, False])
def test_paired_field_delete_must_remove_or_block_the_maintenance_field(sdk_copy, persistent):
    name = 'ExReviewDelete'
    apply(sdk_copy, [{'action':'add_field','owner':'Product','name':name,'field_type':'Name','persistent':persistent,'portal_editable':True}])
    try:
        apply(sdk_copy, [{'action':'delete','kind':'field','owner':'Product','name':name}])
    except ValueError as exc:
        if 'Cannot prove definition is unused' in str(exc):
            pytest.skip('The installed vendor blocks deleting this referenced field')
        raise
    data = vendor.query(str(sdk_copy), 'list', 'field', owner='Product', search=name, limit=100)
    maintenance = vendor.query(str(sdk_copy), 'list', 'field', owner='ProductChanges', search=name, limit=100)
    observed = {'data_fields':[row['name'] for row in data['records']],
                'maintenance_fields':[row['name'] for row in maintenance['records']]}
    assert name not in observed['maintenance_fields']

def test_wcf_subset_must_not_claim_all_changed_fields_verified(isolated):
    result = {'status':'services_generated', 'partial_package':False, 'source_unchanged':True,
              'data_contract_count':2592, 'service_count':798,
              'type_checks':[{'name':'Camstar.WCF.ObjectStack.ProductChanges','properties':['GoodField']}],
              'files_sha256':{'client/Camstar.WCFClient.dll':'a','server/bin/Camstar.WCFService.dll':'b'}}
    state = {'field_expectations':[{'owner':'Product','name':'GoodField','present':True},
                                    {'owner':'OtherChangedObject','name':'MissingField','present':True}]}
    rejected = False
    try:
        progress_actions.verify_wcf_fields(result, state, ['Product'])
    except ValueError:
        rejected = True
    assert rejected

def test_create_approval_must_not_authorize_a_new_delete(isolated, monkeypatch):
    monkeypatch.setattr(runtime, '_adopt_requirements', lambda state: None)
    monkeypatch.setattr(runtime, 'inspect_calls', lambda calls: [])
    monkeypatch.setattr(runtime, '_notes', lambda *args: [])
    monkeypatch.setattr(safety, 'SAFE_DELETE_THRESHOLD', 0)
    prompts = []
    monkeypatch.setattr(runtime, 'interrupt', lambda value: prompts.append(value) or {'approved':False})
    call = {'id':'delete', 'function':{'name':'generate_designer_design_package', 'arguments':json.dumps({
        'mdb_file':'unused.mdb','expected_sha256':'0'*64,
        'operations':[{'action':'delete','kind':'field','owner':'Product','name':'ExOtherField'}]})}}
    threshold = safety.evaluate_mutations(safety.MutationCounts(creates=21), [call])
    assert threshold.requires_approval
    runtime._policy_node({'username':'review-user','session_id':'isolated','messages':[],
        'task_context':{'goal':'create reviewed fields'}, 'pending_tool_calls':[call],
        'approval_granted':True, 'excel_sources':[], 'create_count':21})
    assert prompts

def test_uncredentialed_management_request_must_be_rejected(isolated, monkeypatch):
    monkeypatch.setattr(routes, 'CHAT_USERNAME', 'review-user')
    app = FastAPI(); app.include_router(routes.router)
    from web.local_access import LocalAccessMiddleware
    app.add_middleware(LocalAccessMiddleware)
    client = TestClient(app)
    identity = 'review-user'
    created = client.post('/sessions/' + identity + '/new')
    assert created.status_code in (401, 403)

def test_database_restore_must_always_require_scoped_approval(isolated):
    call = {'id':'restore', 'function':{'name':'restore_designer_test_database',
        'arguments':json.dumps({'backup_receipt':'verified_backup.json','expected_receipt_sha256':'0'*64})}}
    counts = safety.count_tool_calls([call])
    decision = safety.evaluate_mutations(safety.MutationCounts(), [call])
    assert decision.requires_approval

def test_read_during_commit_must_preserve_crash_recovery(isolated, monkeypatch):
    project_id = 'a'*32
    project = working.project_dir(project_id); project.mkdir(parents=True)
    current = project/'InSite.mdb'; current.write_bytes(b'old design')
    before = vendor.digest(current)
    old_state = {'id':project_id,'sha256':before,'latest_manifest':'old','published':None,'cycle_manifests':[]}
    working.write_json(project/'state.json', old_state)
    folder = isolated/'artifacts'/('b'*32); folder.mkdir(parents=True)
    (folder/'modified.mdb').write_bytes(b'new design')
    checksum = vendor.digest(folder/'modified.mdb')
    manifest = folder/'manifest.json'
    manifest.write_text(json.dumps({'artifacts':{'modified.mdb':checksum}}), encoding='utf-8')
    go, done = threading.Event(), threading.Event()
    real_write = working.write_json
    writer = threading.get_ident()
    armed = [False]
    class SimulatedCrash(RuntimeError): pass
    def reader():
        if go.wait(5):
            working.load(project_id)
            done.set()
    def writing(path, value):
        if path.name == 'state.json' and armed[0] and threading.get_ident() == writer:
            raise SimulatedCrash('stopped after MDB replacement, before state update')
        real_write(path, value)
        if path.name == 'pending.json':
            armed[0] = True
            go.set()
            done.wait(1)
    monkeypatch.setattr(working, 'write_json', writing)
    thread = threading.Thread(target=reader, daemon=True); thread.start()
    with pytest.raises(SimulatedCrash):
        with working.project_lock(project_id):
            working.commit(project_id, old_state, manifest, before)
    thread.join(5)
    assert not thread.is_alive()
    recovered = working.load(project_id)
    observed = vendor.digest(current)
    assert recovered['sha256'] == observed


def test_grants_bind_parameters_session_call_and_expiry(monkeypatch):
    call = {'id': 'once', 'function': {'name': 'restore_designer_test_database',
            'arguments': '{"backup_receipt":"backup.json","expected_receipt_sha256":"abc"}'}}
    state = {'username': 'owner', 'session_id': 'session'}
    state['approval_grants'] = safety.grant_calls([call], state)
    assert safety.approved_call(call, state)
    assert not safety.approved_call({**call, 'id': 'another'}, state)
    assert not safety.approved_call(call, {**state, 'session_id': 'another'})
    changed = {**call, 'function': {**call['function'], 'arguments': '{"backup_receipt":"other.json"}'}}
    assert not safety.approved_call(changed, state)
    monkeypatch.setattr(safety.time, 'time', lambda: state['approval_grants'][0]['expires'] + 1)
    assert not safety.approved_call(call, state)


@pytest.mark.parametrize('peer,host,origin,expected', [
    ('127.0.0.1', 'localhost:8032', None, 200),
    ('::1', '[::1]:8032', None, 200),
    ('192.168.1.30', 'localhost:8032', None, 403),
    ('127.0.0.1', 'attacker.example:8032', None, 403),
    ('127.0.0.1', 'localhost:8032', 'https://attacker.example', 403),
    ('127.0.0.1', 'localhost:8032', 'http://[broken', 403),
    ('127.0.0.1', 'localhost:8032', 'http://localhost:8032', 200),
])
def test_management_loopback_and_origin_boundary(peer, host, origin, expected):
    from web.local_access import LocalAccessMiddleware
    app = FastAPI()
    app.add_middleware(LocalAccessMiddleware)
    @app.post('/mutation')
    def mutation(): return {'ok': True}
    client = TestClient(app, client=(peer, 50000))
    headers = {'host': host}
    if origin: headers['origin'] = origin
    assert client.post('/mutation', headers=headers).status_code == expected


def test_public_management_listener_refused(monkeypatch):
    import web.app as app_module
    monkeypatch.setattr(app_module, 'SERVER_HOST', '0.0.0.0')
    with pytest.raises(RuntimeError, match='本机访问'):
        app_module.create_app()


@pytest.mark.parametrize('phase', ['before_replace', 'after_replace', 'unknown_hash'])
def test_orphan_journal_recovery_is_conservative(isolated, phase):
    project_id = 'c'*32
    folder = working.project_dir(project_id); folder.mkdir(parents=True)
    current = folder/'InSite.mdb'
    current.write_bytes(b'old')
    previous = {'id': project_id, 'sha256': vendor.digest(current), 'latest_manifest': 'old'}
    working.write_json(folder/'state.json', previous)
    current.write_bytes(b'new')
    pending = {**previous, 'sha256': vendor.digest(current), 'latest_manifest': 'new'}
    working.write_json(folder/'pending.json', pending)
    if phase == 'before_replace': current.write_bytes(b'old')
    if phase == 'unknown_hash':
        current.write_bytes(b'unexpected')
        with pytest.raises(ValueError, match='pending.json'):
            working.load(project_id)
        assert (folder/'pending.json').is_file()
        assert json.loads((folder/'state.json').read_text()) == previous
    else:
        assert working.load(project_id) == (previous if phase == 'before_replace' else pending)
        assert not (folder/'pending.json').exists()


def test_project_lock_excludes_other_processes_and_is_reentrant(isolated):
    project_id = 'd'*32
    marker = isolated/'acquired'
    code = ('import config; from designer import working; from pathlib import Path; '
            f'config.DESIGNER_ROOT={str(isolated)!r}\n'
            f'with working.project_lock({project_id!r}): Path({str(marker)!r}).write_text("ok")')
    with working.project_lock(project_id):
        with working.project_lock(project_id):
            process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                with pytest.raises(subprocess.TimeoutExpired): process.wait(timeout=1)
                assert not marker.exists()
            finally:
                # Release the outer lock before waiting for the child below.
                pass
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, stderr.decode(errors='replace')
    assert marker.read_text() == 'ok'


def test_wcf_selection_expands_to_all_current_objects():
    package = {'objects': ['Product', 'Other'], 'service_objects': ['Product', 'Other']}
    assert progress_actions.wcf_types(package, ['Product']) == ['Product', 'Other']
    many = [f'ExObject{i}' for i in range(25)]
    assert progress_actions.wcf_types({'objects': many}, None) == many


def test_old_boolean_wcf_receipt_cannot_complete_a_changed_design():
    result = {'status': 'services_generated', 'fields_verified': True}
    assert not progress_actions.complete_wcf_receipt(result, {'service_objects': ['Product']}, [])


def test_reads_and_artifacts_do_not_reprompt_for_prior_mutations():
    completed = safety.MutationCounts(creates=100, updates=100, deletes=100)
    for name in ('get_designer_entity', 'generate_designer_wcf_package', 'compile_designer_mdb'):
        call = {'function': {'name': name, 'arguments': '{}'}}
        assert not safety.evaluate_mutations(completed, [call]).requires_approval


def test_restore_confirmation_displays_target_and_backup(monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_DB_SERVER', 'local-target')
    monkeypatch.setattr(config, 'DESIGNER_DB_NAME', 'test-db')
    call = {'function': {'name': 'restore_designer_test_database', 'arguments': json.dumps({
        'backup_receipt': 'verified.json', 'expected_receipt_sha256': 'a'*64})}}
    prompt = safety.approval_details([call])
    assert 'local-target/test-db' in prompt and 'verified.json' in prompt and 'a'*64 in prompt


@pytest.mark.parametrize('action', ['change_field_type', 'delete'])
def test_direct_maintenance_edit_cannot_break_the_data_contract(sdk_copy, action):
    name = 'ExReviewReverse'
    apply(sdk_copy, [{'action': 'add_field', 'owner': 'Product', 'name': name, 'field_type': 'Name', 'persistent': True}])
    before = vendor.digest(sdk_copy)
    op = {'action': action, 'kind': 'field', 'owner': 'ProductChanges', 'name': name}
    if action == 'change_field_type': op.update(field_type='[Integer]', expected_field_type='Name')
    from designer.files import artifact_dir
    report = vendor.run(artifact_dir(), {'mode': 'preflight', 'mdb': str(sdk_copy), 'workspace': '200', 'operations': [op]})
    assert not report['ready']
    assert any('maintenance field mismatch' in row['message'] for row in report['conflicts'])
    assert vendor.digest(sdk_copy) == before


def test_paired_rename_preview_lists_both_sides(sdk_copy):
    name, new = 'ExReviewPreview', 'ExReviewPreviewNew'
    apply(sdk_copy, [{'action': 'add_field', 'owner': 'Product', 'name': name, 'field_type': 'Name', 'persistent': True}])
    from designer.files import artifact_dir
    op = {'action': 'patch', 'kind': 'field', 'owner': 'Product', 'name': name,
          'changes': {'FieldName': new}, 'expected': {'FieldName': name}}
    report = vendor.run(artifact_dir(), {'mode': 'preflight', 'mdb': str(sdk_copy), 'workspace': '200', 'operations': [op]})
    assert report['ready'], report
    paired = report['associated_operations']
    assert len(paired) == 1 and paired[0]['owner'] == 'ProductChanges'
    assert paired[0]['changes'] == {'FieldName': new}


def test_final_mdb_contract_guard_rejects_drift(sdk_copy):
    name = 'ExReviewDrift'
    apply(sdk_copy, [{'action': 'add_field', 'owner': 'Product', 'name': name, 'field_type': 'Name', 'persistent': True}])
    # Simulate an independently edited Designer file rather than bypassing the
    # guarded tool: alter only the private MDB via ACE, then inspect with SDK.
    from designer.mdb import access_drivers
    import pyodbc
    with pyodbc.connect(f'DRIVER={{{access_drivers()[0]}}};DBQ={sdk_copy};', autocommit=True) as connection:
        cursor = connection.cursor()
        identity = cursor.execute("SELECT FieldDefID FROM FieldDefinitions WHERE FieldDefName='[Integer]'").fetchone()[0]
        owner = cursor.execute("SELECT CDODefID FROM CDODefinition WHERE CDOName='ProductChanges'").fetchone()[0]
        cursor.execute('UPDATE CDOFields SET FieldDefID=? WHERE CDODefID=? AND FieldName=?', identity, owner, name)
        connection.commit()
    with pytest.raises(ValueError, match='maintenance field mismatch'):
        progress_actions.compiled_field_contracts(str(sdk_copy), vendor.digest(sdk_copy), [{'owner': 'Product', 'name': name, 'present': True}])


def test_runtime_consumes_approval_after_one_invocation(isolated, monkeypatch):
    monkeypatch.setattr(runtime, '_adopt_requirements', lambda state: None)
    monkeypatch.setattr(runtime, 'get_stream_writer', lambda: lambda value: None)
    monkeypatch.setattr(runtime, 'record_perf', lambda *args: None)
    monkeypatch.setattr(runtime, 'record_tool_outcome', lambda **kwargs: None)
    calls = []
    async def restore(**kwargs):
        calls.append(kwargs)
        return {'status': 'restored'}
    monkeypatch.setattr(runtime, 'get_tool_func', lambda name: restore)
    call = {'id': 'restore-once', 'function': {'name': 'restore_designer_test_database',
            'arguments': '{"backup_receipt":"verified.json","expected_receipt_sha256":"abc"}'}}
    state = {'username': 'owner', 'session_id': 'session', 'pending_tool_calls': [call]}
    state['approval_grants'] = safety.grant_calls([call], state)
    async def execute():
        state.update(await runtime._execute_tool_node(state))
        assert not state['approval_grants']
        state['pending_tool_calls'] = [call]
        state.update(await runtime._execute_tool_node(state))
        assert '确认缺失或已失效' in state['messages'][-1]['content']
    asyncio.run(execute())
    assert len(calls) == 1


def test_referenced_field_type_shape_requires_explicit_field_migration(sdk_copy):
    from designer.files import artifact_dir
    before = vendor.digest(sdk_copy)
    report = vendor.run(artifact_dir(), {'mode': 'preflight', 'mdb': str(sdk_copy), 'workspace': '200', 'operations': [
        {'action': 'patch', 'kind': 'field_type', 'name': 'Name',
         'changes': {'PrecisionValue': 80}, 'expected': {'PrecisionValue': 40}}]})
    assert not report['ready']
    assert any('Referenced field type shape' in row['message'] for row in report['conflicts'])
    assert vendor.digest(sdk_copy) == before


@pytest.mark.parametrize('property_name', ['AllowClientToRead', 'AllowClientToUpdate'])
def test_portal_pair_cannot_silently_lose_client_access(sdk_copy, property_name):
    name = 'ExReviewAccess'
    apply(sdk_copy, [{'action': 'add_field', 'owner': 'Product', 'name': name, 'field_type': 'Name', 'persistent': True}])
    from designer.files import artifact_dir
    before = vendor.digest(sdk_copy)
    report = vendor.run(artifact_dir(), {'mode': 'preflight', 'mdb': str(sdk_copy), 'workspace': '200', 'operations': [
        {'action': 'patch', 'kind': 'field', 'owner': 'ProductChanges', 'name': name,
         'changes': {property_name: False}, 'expected': {property_name: True}}]})
    assert not report['ready']
    assert any('requires client read/update access' in row['message'] for row in report['conflicts'])
    assert vendor.digest(sdk_copy) == before
