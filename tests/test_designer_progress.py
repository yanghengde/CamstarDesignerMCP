import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from agent import memory
from designer import progress, progress_store as store, vendor
from web import routes


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path / 'designer'))
    monkeypatch.setattr(memory, 'SESSIONS_DIR', str(tmp_path / 'sessions'))
    monkeypatch.setattr(memory, 'user_memories', {})
    monkeypatch.setattr(routes, 'CHAT_USERNAME', 'user')
    sid = memory.create_session('user')
    memory.get_user_messages('user', sid).append({'role': 'user', 'content': '创建 ExSample 对象及字段'})
    root = Path(config.DESIGNER_ROOT); root.mkdir()
    source = root / 'source.mdb'; source.write_bytes(b'base')
    folder = root / 'artifacts' / ('a'*32); folder.mkdir(parents=True)
    for name, data in {'baseline.mdb': b'base', 'modified.mdb': b'new design', 'changes.xml': b'<InSiteMetaData/>',}.items():
        (folder/name).write_bytes(data)
    manifest = {'format_version': 2, 'status': 'saved_to_test_copy_and_exported', 'workspace_applied_to_mdb': True,
                'source_file': str(source), 'source_sha256': vendor.digest(source), 'workspace': '200',
                'operations': [{'action': 'create_cdo', 'name': 'ExSample'}, {'action': 'add_field', 'name': 'Code', 'owner': 'ExSample', 'field_type': 'String40'}],
                'artifacts': {name: vendor.digest(folder/name) for name in ('baseline.mdb', 'modified.mdb', 'changes.xml')}}
    (folder/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    add_tool(sid, 'get_designer_entity', {'name': 'Product'}, {'name': 'Product', 'read_only': True})
    add_tool(sid, 'generate_designer_design_package', {}, {'files': {'manifest.json': str(folder/'manifest.json')}, 'status': manifest['status']})
    return sid, folder


def add_tool(sid, name, args, result):
    messages = memory.get_user_messages('user', sid)
    identity = f'call-{len(messages)}'
    messages.append({'role': 'assistant', 'tool_calls': [{'id': identity, 'function': {'name': name, 'arguments': json.dumps(args)}}]})
    messages.append({'role': 'tool', 'tool_call_id': identity, 'name': name, 'content': str(result)})


def check_receipt(sid, folder):
    add_tool(sid, 'check_designer_package', {'manifest_file': str(folder/'manifest.json')}, {'intact': True})


def test_saved_design_does_not_imply_review_compile_or_publish(workflow):
    sid, folder = workflow
    state = progress.snapshot('user', sid)
    assert state['current'] == 5
    assert state['completed'] == 4
    assert len(state['stages']) == 10
    check_receipt(sid, folder)
    (folder/'designer_review.json').write_text(json.dumps({'copied_sha256': vendor.digest(folder/'modified.mdb'),
         'server_mdb': r'C:\Temp\DesignerMCP\Review_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\InSite.mdb', 'server_siteinfo': r'C:\Temp\SiteInfo.mdb', 'activated': True}))
    state = progress.snapshot('user', sid)
    assert state['current'] == 6
    assert state['stages'][5]['status'] == 'waiting'
    assert state['stages'][6]['status'] == 'pending'
    assert state['stages'][7]['status'] == 'pending'


def test_latest_design_counts_owned_ancestors_without_sibling_designs(workflow):
    sid, original = workflow

    def child(identity, name):
        folder = original.parent / (identity * 32)
        folder.mkdir()
        manifest = json.loads((original / 'manifest.json').read_text())
        manifest['source_file'] = str(original / 'modified.mdb')
        manifest['source_sha256'] = vendor.digest(original / 'modified.mdb')
        manifest['operations'] = [{'action': 'add_field', 'owner': 'ExSample', 'name': name, 'field_type': 'String40'}]
        for filename in ('baseline.mdb', 'modified.mdb', 'changes.xml'):
            (folder / filename).write_bytes((original / filename).read_bytes())
        (folder / 'manifest.json').write_text(json.dumps(manifest))
        add_tool(sid, 'generate_designer_design_package', {}, {'files': {'manifest.json': str(folder / 'manifest.json')}})
        return folder

    child('b', 'SiblingOnly')
    latest = child('c', 'LatestField')
    state = progress.snapshot('user', sid)
    assert state['package']['id'] == latest.name
    assert state['package']['field_count'] == 2
    assert {row['name'] for row in state['design_rows']} == {'Code', 'LatestField'}
    assert len(state['_manifests']) == 2


def test_model_followup_does_not_regress_completed_design_steps(workflow):
    sid, folder = workflow
    check_receipt(sid, folder)
    identity, _ = store.begin('user', sid, '__llm_inference', {})
    state = progress.snapshot('user', sid)
    assert state['stages'][1]['status'] == 'done'
    assert state['stages'][3]['status'] == 'done'
    assert state['completed'] == 5
    assert state['active']['status'] == 'running'
    store.finish(identity, {})


def test_manual_confirmations_are_scoped_ordered_and_labelled(workflow):
    sid, folder = workflow
    with pytest.raises(ValueError, match='前面'):
        progress.start_action('user', sid, folder.name, 'confirm_publish')
    check_receipt(sid, folder)
    progress.start_action('user', sid, folder.name, 'confirm_review')
    assert progress.snapshot('user', sid)['stages'][5]['status'] == 'confirmed'
    progress.start_action('user', sid, folder.name, 'confirm_compile')
    progress.start_action('user', sid, folder.name, 'confirm_publish')
    progress.start_action('user', sid, folder.name, 'skip_services')
    progress.start_action('user', sid, folder.name, 'confirm_complete')
    state = progress.snapshot('user', sid)
    assert state['completed'] == 10
    assert state['stages'][7]['detail'] == '用户确认'
    assert state['stages'][8]['status'] == 'skipped'
    (folder/'modified.mdb').write_bytes(b'changed')
    state = progress.snapshot('user', sid)
    assert state['completed'] < 10
    assert state['package'] is None
    with pytest.raises(ValueError, match='已更新'):
        progress.start_action('user', sid, folder.name, 'confirm_complete')


def test_api_only_returns_configured_users_tasks_and_files(workflow):
    sid, folder = workflow
    empty = memory.create_session('user')
    memory.create_session('other')
    app = FastAPI(); app.include_router(routes.router)
    client = TestClient(app)
    assert client.get('/api/progress/tasks', params={'username': 'user'}).json()['tasks'][0]['id'] == sid
    assert len(client.get('/api/progress/tasks', params={'username': 'user'}).json()['tasks']) == 1
    assert client.get('/api/progress/tasks', params={'username': 'other'}).status_code == 404
    assert client.get('/api/progress/missing', params={'username': 'user'}).status_code == 404
    result = client.get(f'/api/progress/{sid}', params={'username': 'user'}).json()
    assert '_manifests' not in result
    assert result['package']['objects'] == ['ExSample']
    assert client.get(f'/api/progress/{sid}/changes', params={'username':'user','package_id':folder.name}).content == b'<InSiteMetaData/>'
    assert client.get(f'/api/progress/{empty}/changes', params={'username':'user','package_id':folder.name}).status_code == 404
    assert client.post('/api/progress/action', json={'username':'user','session_id':sid,'package_id':folder.name,'action':'confirm_complete'}).status_code == 409
    assert client.get('/progress').status_code == 200


def test_background_operation_survives_polling_and_deduplicates(workflow, monkeypatch):
    from tools import designer
    sid, folder = workflow
    calls = []
    async def check(manifest):
        calls.append(manifest)
        await asyncio.sleep(.03)
        return {'intact': True}
    monkeypatch.setattr(designer, 'check_designer_package', check)
    async def scenario():
        first = progress.start_action('user', sid, folder.name, 'check')
        second = progress.start_action('user', sid, folder.name, 'check')
        assert first['id'] == second['id']
        assert progress.snapshot('user', sid)['active']['status'] == 'running'
        await asyncio.gather(*list(progress._tasks))
        state = progress.snapshot('user', sid)
        assert state['active'] is None
        assert state['stages'][4]['status'] == 'done'
        assert state['last_job']['status'] == 'done'
    asyncio.run(scenario())
    assert len(calls) == 1


def test_expired_operation_is_interrupted_not_completed(workflow):
    sid, folder = workflow
    identity, _ = store.begin('user', sid, 'compile_designer_mdb', {'manifest_file': str(folder/'manifest.json')}, package_id=folder.name, action='compile')
    with store.connection() as conn:
        conn.execute('UPDATE operations SET updated=? WHERE id=?', (time.time()-60, identity))
    state = progress.snapshot('user', sid)
    assert state['active'] is None
    assert state['stages'][6]['status'] == 'failed'
    assert state['last_job']['status'] == 'interrupted'


def test_server_file_drift_blocks_downstream(workflow):
    sid, folder = workflow
    check_receipt(sid, folder)
    path = r'C:\Temp\DesignerMCP\Review_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\InSite.mdb'
    (folder/'designer_review.json').write_text(json.dumps({'copied_sha256':vendor.digest(folder/'modified.mdb'), 'server_mdb':path}))
    identity, _ = store.begin('user', sid, 'sync_designer_file', {'manifest_file':str(folder/'manifest.json')}, package_id=folder.name, action='sync')
    store.finish(identity, {'unchanged': False, 'server_mdb': path})
    state = progress.snapshot('user', sid)
    assert all(item['status'] == 'blocked' for item in state['stages'][4:])
    with pytest.raises(ValueError, match='前面'):
        progress.start_action('user', sid, folder.name, 'compile')
