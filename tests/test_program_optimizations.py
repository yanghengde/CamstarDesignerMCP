"""Regression coverage for cancelled saves, cumulative limits and read resources."""
import asyncio
import json
from pathlib import Path
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from agent import memory, langgraph_runtime as runtime, safety
from designer import progress_store as store, vendor
from web import routes


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    monkeypatch.setattr(memory, 'SESSIONS_DIR', str(tmp_path / 'sessions'))
    monkeypatch.setattr(memory, 'user_memories', {})
    monkeypatch.setattr(routes, 'CHAT_USERNAME', 'user')
    dll = tmp_path / 'metadata.dll'; dll.write_bytes(b'vendor-v1')
    monkeypatch.setattr(vendor, 'assembly', lambda: dll)
    return tmp_path, dll


def test_stopped_response_retains_save_and_recovers_tool_result(isolated, monkeypatch):
    root, _ = isolated
    sid = memory.create_session('user')
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(runtime, 'get_stream_writer', lambda: lambda value: None)
    monkeypatch.setattr(runtime, 'record_perf', lambda *args: None)
    monkeypatch.setattr(runtime, 'record_tool_outcome', lambda **kwargs: None)
    def save():
        started.set()
        assert release.wait(3)
        (root / 'saved.mdb').write_bytes(b'completed save')
        return {'status': 'saved', 'source_unchanged': True}
    async def tool():
        return await asyncio.to_thread(save)
    monkeypatch.setattr(runtime, 'get_tool_func', lambda name: tool)
    call = {'id': 'save-1', 'function': {'name': 'test_save', 'arguments': '{}'}}
    async def execute():
        caller = asyncio.create_task(runtime._execute_tool_node({
            'username': 'user', 'session_id': sid, 'pending_tool_calls': [call]}))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert store.background_running('user', sid)
            assert store.operations('user', sid)[0]['status'] == 'running'
        finally:
            release.set()
            await store.drain()
    asyncio.run(execute())
    assert (root / 'saved.mdb').read_bytes() == b'completed save'
    assert store.operations('user', sid)[0]['status'] == 'done'
    messages = memory.get_user_messages('user', sid)
    before = memory.get_sessions('user')[0]['updated_at']
    store.recover_chat_results('user', sid, messages)
    store.recover_chat_results('user', sid, messages)
    assert [item.get('tool_call_id') for item in messages if item['role'] == 'tool'] == ['save-1']
    assert 'completed save' not in messages[-1]['content']
    assert 'saved' in messages[-1]['content']
    assert memory.get_sessions('user')[0]['updated_at'] == before


def test_same_active_call_runs_only_once_and_failure_is_durable(isolated):
    sid = memory.create_session('user')
    async def execute():
        ready = asyncio.Event()
        calls = []
        async def tool():
            calls.append(1)
            await ready.wait()
            raise ValueError('vendor rejected save')
        one = asyncio.create_task(store.run_tool('user', sid, 'write', {}, 'same-id', tool))
        await asyncio.sleep(0)
        two = asyncio.create_task(store.run_tool('user', sid, 'write', {}, 'same-id', tool))
        await asyncio.sleep(0)
        ready.set()
        results = await asyncio.gather(one, two, return_exceptions=True)
        assert all(isinstance(result, ValueError) for result in results)
        assert calls == [1]
        with pytest.raises(ValueError, match='vendor rejected'):
            await store.run_tool('user', sid, 'write', {}, 'same-id', tool)
        assert calls == [1]
    asyncio.run(execute())
    receipt, = store.operations('user', sid)
    assert receipt['status'] == 'failed'
    assert 'vendor rejected' in receipt['error']


def test_completed_write_call_is_not_repeated_after_reconnect(isolated):
    sid = memory.create_session('user')
    calls = []
    async def tool():
        calls.append(1)
        return {'status': 'saved', 'files': {'manifest.json': 'test-manifest.json'}}
    async def execute():
        first = await store.run_tool('user', sid, 'write', {}, 'completed-id', tool)
        await asyncio.sleep(0)
        assert await store.run_tool('user', sid, 'write', {}, 'completed-id', tool) == first
        with pytest.raises(ValueError, match='另一项操作'):
            await store.run_tool('user', sid, 'write', {'different': True}, 'completed-id', tool)
    asyncio.run(execute())
    assert calls == [1]
    assert len(store.operations('user', sid)) == 1


def test_runtime_counts_repeated_and_mixed_design_mutations(isolated, monkeypatch):
    monkeypatch.setattr(runtime, 'get_stream_writer', lambda: lambda value: None)
    monkeypatch.setattr(runtime, 'record_perf', lambda *args: None)
    monkeypatch.setattr(runtime, 'record_tool_outcome', lambda **kwargs: None)
    monkeypatch.setattr(safety, 'SAFE_UPDATE_THRESHOLD', 3)
    async def tool(**kwargs): return {'status': 'accepted'}
    monkeypatch.setattr(runtime, 'get_tool_func', lambda name: tool)
    def call(index, operations):
        return {'id': str(index), 'function': {'name': 'generate_designer_design_package',
            'arguments': json.dumps({'operations': operations})}}
    async def execute():
        state = {'username': 'user', 'session_id': memory.create_session('user')}
        op = {'action': 'patch', 'kind': 'cdo', 'name': 'ExA'}
        for index in range(3):
            state['pending_tool_calls'] = [call(index, [op])]
            state.update(await runtime._execute_tool_node(state))
        assert state['update_count'] == 3
        assert safety.evaluate_mutations(runtime._completed_counts(state), [call(4, [op])]).requires_approval
        state['pending_tool_calls'] = [call(5, [op, {'action': 'add_field'}, {'action': 'remove_field_override'}])]
        state.update(await runtime._execute_tool_node(state))
        assert (state['create_count'], state['update_count'], state['delete_count']) == (1, 4, 1)
    asyncio.run(execute())


@pytest.mark.parametrize('username', ['../outside', '..', '.', r'..\outside', 'C:\\outside', 'CON', 'nul.txt', 'name.'])
def test_user_directory_rejects_escaping_and_windows_aliases(isolated, username):
    with pytest.raises(ValueError):
        memory.create_session(username)
    assert username not in memory.user_memories


def test_session_and_chat_api_only_accept_configured_owner(isolated):
    app = FastAPI(); app.include_router(routes.router)
    client = TestClient(app)
    assert client.post('/sessions/other/new').status_code == 404
    assert client.get('/sessions/other').status_code == 404
    assert client.post('/chat', json={'username': '../outside', 'message': 'hello'}).status_code == 404
    assert not memory.user_memories


@pytest.mark.parametrize('session_id', ['../outside', '..', 'metadata', 'NUL', 'invalid:name'])
def test_session_filenames_cannot_escape_or_replace_metadata(isolated, session_id):
    sid = memory.create_session('user')
    memory.user_memories['user']['sessions'][session_id] = {'id': session_id, 'messages': []}
    with pytest.raises(ValueError):
        memory.save_session('user', session_id)
    assert (Path(memory.get_user_dir('user')) / (sid + '.json')).is_file()


def test_atomic_session_save_retries_transient_windows_file_lock(isolated, monkeypatch):
    replace = Path.replace
    attempts = []
    def locked(file, target):
        attempts.append(file)
        if len(attempts) == 1:
            raise PermissionError('transient Windows sharing violation')
        return replace(file, target)
    monkeypatch.setattr(Path, 'replace', locked)
    sid = memory.create_session('user')
    data = json.loads((Path(memory.get_user_dir('user')) / (sid + '.json')).read_text(encoding='utf-8'))
    assert data['id'] == sid
    assert not list(Path(memory.get_user_dir('user')).glob('*.tmp'))


def test_read_cache_reuses_version_and_invalidates_mdb_and_sdk_changes(isolated, monkeypatch):
    root, dll = isolated
    source = root / 'source.mdb'; source.write_bytes(b'version1')
    calls = []
    def run(folder, request):
        calls.append(request)
        assert Path(request['mdb']).is_file()
        return {'items': [{'name': Path(request['mdb']).read_text()}], 'total': 1}
    monkeypatch.setattr(vendor, 'run', run)
    first = vendor.query(str(source), 'list', 'cdo')
    assert vendor.query(str(source), 'list', 'cdo') == first
    assert len(calls) == 1
    source.write_bytes(b'version2')
    assert vendor.query(str(source), 'list', 'cdo')['items'][0]['name'] == 'version2'
    dll.write_bytes(b'vendor-v2')
    vendor.query(str(source), 'list', 'cdo')
    vendor.query(str(source), 'list', 'cdo', search='different')
    assert len(calls) == 4
    assert not list((root / 'artifacts').rglob('read.mdb'))


def test_failed_reads_and_source_changes_release_private_mdb(isolated, monkeypatch):
    root, _ = isolated
    source = root / 'source.mdb'; source.write_bytes(b'original')
    def fail(folder, request): raise ValueError('backend failure')
    monkeypatch.setattr(vendor, 'run', fail)
    with pytest.raises(ValueError, match='backend failure'):
        vendor.query(str(source), 'get', 'cdo', 'Product')
    assert not list((root / 'artifacts').rglob('read.mdb'))
    def drift(folder, request):
        source.write_bytes(b'changed')
        return {'name': 'Product'}
    monkeypatch.setattr(vendor, 'run', drift)
    with pytest.raises(ValueError, match='来源 MDB'):
        vendor.query(str(source), 'get', 'cdo', 'Product')
    assert not list((root / 'cache').rglob('*.json'))
    assert not list((root / 'artifacts').rglob('read.mdb'))


@pytest.mark.parametrize('operations,properties', [
    ([{'action': 'patch', 'kind': 'field', 'owner': 'ExA', 'name': 'Old',
       'changes': {'FieldName': 'New'}}], ['New']),
    ([{'action': 'remove_field_override', 'owner': 'ExA', 'name': 'Inherited'}], ['Inherited']),
    ([{'action': 'add_field', 'owner': 'ExA', 'name': 'Temporary'},
      {'action': 'delete', 'kind': 'field', 'owner': 'ExA', 'name': 'Temporary'}], []),
    ([{'action': 'patch', 'kind': 'field', 'owner': 'ExA', 'name': 'Old', 'changes': {'FieldName': 'Middle'}},
      {'action': 'patch', 'kind': 'field', 'owner': 'ExA', 'name': 'Old', 'changes': {'FieldName': 'Final'}}], ['Final']),
    ([{'action': 'patch', 'kind': 'field', 'owner': 'ExA', 'name': 'Old', 'changes': {'FieldName': 'New'}},
      {'action': 'add_field', 'owner': 'ExA', 'name': 'Old'}], ['Old', 'New']),
])
def test_wcf_checks_final_field_names_and_presence(operations, properties):
    from designer import operations as changes, progress_actions
    state = {'field_expectations': changes.final_fields(operations)}
    result = {'status': 'services_generated', 'partial_package': False, 'source_unchanged': True,
              'type_checks': [{'name': 'ObjectStack.ExA', 'properties': properties},
                              {'name': 'ObjectStack.ExAChanges', 'properties': properties}],
              'data_contract_count': 2, 'service_count': 1,
              'files_sha256': {'client/Camstar.WCFClient.dll': 'hash', 'server/bin/Camstar.WCFService.dll': 'hash'}}
    assert progress_actions.verify_wcf_fields(result, state, ['ExA']) == result
    unexpected = set(properties) ^ {state['field_expectations'][-1]['name']}
    result['type_checks'] = [{'name': 'ObjectStack.ExA', 'properties': list(unexpected)}]
    with pytest.raises(ValueError, match='当前字段'):
        progress_actions.verify_wcf_fields(result, state, ['ExA'])


def test_wcf_expectations_use_compiled_inheritance_and_reject_missing_write(isolated, monkeypatch):
    from designer import progress_actions
    root, _ = isolated
    compiled = root / 'compiled.mdb'; compiled.write_bytes(b'compiled')
    pages = []
    def query(*args, **kwargs):
        pages.append(kwargs['offset'])
        return {'total': 2, 'records': [{'name': 'OldInherited' if kwargs['offset'] == 0 else 'Renamed'}]}
    monkeypatch.setattr(vendor, 'query', query)
    fields = [{'owner': 'ExA', 'name': 'OldInherited', 'present': False},
              {'owner': 'ExA', 'name': 'Renamed', 'present': True}]
    actual = progress_actions.final_wcf_fields(str(compiled), vendor.digest(compiled), fields, ['ExA'])
    assert pages == [0, 1]
    assert all(row['present'] for row in actual)
    with pytest.raises(ValueError, match='最终 MDB 缺少'):
        progress_actions.final_wcf_fields(str(compiled), vendor.digest(compiled),
            [{'owner': 'ExA', 'name': 'NotSaved', 'present': True}], ['ExA'])
