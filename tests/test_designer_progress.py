import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from agent import memory
from designer import progress, progress_actions, progress_store as store, vendor, publication
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
    assert len(state['stages']) == 11
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
    assert state['workflow_active'] is None
    store.finish(identity, {})


def test_reading_current_design_file_does_not_change_workflow_steps(workflow):
    sid, folder = workflow
    before = progress.snapshot('user', sid)
    identity, _ = store.begin('user', sid, 'inspect_designer_mdb', {'mdb_file': str(folder/'modified.mdb')})
    state = progress.snapshot('user', sid)
    assert [(item['status'], item['detail']) for item in state['stages']] == [(item['status'], item['detail']) for item in before['stages']]
    assert state['current'] == before['current']
    assert state['active']['status'] == 'running'
    assert state['workflow_active'] is None
    store.finish(identity, {})


def test_read_only_conversation_never_creates_design_progress(workflow):
    sid, _ = workflow
    messages = memory.get_user_messages('user', sid)
    messages[:] = messages[:1] + [{'role': 'user', 'content': '查询 Product 字段'}]
    add_tool(sid, 'get_designer_entity', {'name': 'Product'}, {'name': 'Product'})
    identity, _ = store.begin('user', sid, '__llm_inference', {})
    assert progress.tasks('user') == []
    state = progress.snapshot('user', sid)
    assert not state['has_design']
    assert state['completed'] == 0
    assert state['requirements'] == []
    assert state['workflow_active'] is None
    store.finish(identity, {})


def test_design_requirements_exclude_previous_and_followup_chat(workflow):
    sid, _ = workflow
    messages = memory.get_user_messages('user', sid)
    messages[1:1] = [{'role': 'user', 'content': '前面查询 Product'}]
    messages.append({'role': 'user', 'content': '后面继续查询 ExStatus'})
    add_tool(sid, 'get_designer_entity', {'name': 'ExStatus'}, {'name': 'ExStatus'})
    state = progress.snapshot('user', sid)
    assert state['has_design']
    assert state['requirements'] == ['创建 ExSample 对象及字段']
    assert state['current'] == 5
    assert progress.tasks('user')[0]['title'] == 'ExSample 设计'


def test_unrelated_historical_reads_do_not_complete_design_check(workflow):
    sid, _ = workflow
    messages = memory.get_user_messages('user', sid)
    # The only object read belongs to an earlier, independent query.
    request = messages.pop(1)
    messages.insert(1, {'role': 'user', 'content': '只查询 Product'})
    messages.insert(4, request)
    state = progress.snapshot('user', sid)
    assert state['stages'][2]['status'] == 'skipped'
    assert state['requirements'] == ['创建 ExSample 对象及字段']
    assert state['current'] == 5


def test_new_generation_is_visible_but_does_not_reuse_previous_package(workflow):
    sid, _ = workflow
    memory.get_user_messages('user', sid).append({'role': 'user', 'content': '本次新增另一个对象'})
    identity, _ = store.begin('user', sid, 'generate_designer_cdo_package', {'cdo_name': 'ExNext'})
    state = progress.snapshot('user', sid)
    assert progress.tasks('user')[0]['id'] == sid
    assert state['package'] is None
    assert state['current'] == 4
    assert state['stages'][3]['status'] == 'running'
    assert state['workflow_active']['id'] == identity
    assert state['requirements'] == ['本次新增另一个对象']
    store.finish(identity, error='生成失败', status='failed')
    state = progress.snapshot('user', sid)
    assert state['package'] is None
    assert state['stages'][3]['status'] == 'failed'


def test_cancelled_preview_without_execution_does_not_create_progress(workflow):
    sid, _ = workflow
    messages = memory.get_user_messages('user', sid)
    messages[:] = messages[:1] + [{'role': 'user', 'content': '预览新对象'}]
    add_tool(sid, 'generate_designer_cdo_package', {}, 'Action cancelled by the user before execution.')
    assert progress.tasks('user') == []
    assert not progress.snapshot('user', sid)['has_design']


def test_earlier_failed_execution_cannot_replace_a_later_saved_design(workflow):
    sid, folder = workflow
    identity, _ = store.begin('user', sid, 'generate_designer_cdo_package', {}, call_id='old-failure')
    store.finish(identity, error='先前失败', status='failed')
    call_id = memory.get_user_messages('user', sid)[-1]['tool_call_id']
    identity, _ = store.begin('user', sid, 'generate_designer_design_package', {}, call_id=call_id)
    store.finish(identity, {'files': {'manifest.json': str(folder/'manifest.json')}})
    state = progress.snapshot('user', sid)
    assert state['package']['id'] == folder.name
    assert state['stages'][3]['status'] == 'done'


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
    assert state['completed'] == 11
    assert state['stages'][7]['detail'] == '用户确认'
    assert state['stages'][8]['status'] == 'skipped'
    (folder/'modified.mdb').write_bytes(b'changed')
    state = progress.snapshot('user', sid)
    assert state['completed'] < 11
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


def ready_for_publish(sid, folder, monkeypatch, require_backup=True):
    from datetime import datetime, timezone
    monkeypatch.setattr(config, 'DESIGNER_TEST_TARGET_CONFIRMED', True)
    monkeypatch.setattr(config, 'DESIGNER_REQUIRE_DATABASE_BACKUP', require_backup)
    monkeypatch.setattr(config, 'DESIGNER_DB_SERVER', 'test-server')
    monkeypatch.setattr(config, 'DESIGNER_DB_NAME', 'test-db')
    check_receipt(sid, folder)
    (folder/'designer_review.json').write_text(json.dumps({'copied_sha256':vendor.digest(folder/'modified.mdb'),
        'server_mdb':r'C:\Temp\DesignerMCP\Review_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\InSite.mdb',
        'server_siteinfo':r'C:\Temp\DesignerMCP\Review_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\SiteInfo.mdb'}))
    progress.start_action('user', sid, folder.name, 'confirm_review')
    progress.start_action('user', sid, folder.name, 'confirm_compile')
    created = datetime.now(timezone.utc).isoformat()
    plan = {'status':'preflight_only','ready_for_publish':True,'target':progress_actions.target(),'created_utc':created,
        'manifest_file':str(folder/'manifest.json'),'manifest_sha256':vendor.digest(folder/'manifest.json'),
        'design_sha256':vendor.digest(folder/'modified.mdb'),'target_fingerprint':'catalog-digest','changes':[], 'blockers':[]}
    plan_file = folder/'publish_plan.json';plan_file.write_text(json.dumps(plan))
    plan.update(plan_file=str(plan_file), plan_sha256=vendor.digest(plan_file))
    backup = {'status':'backup_verified','target':progress_actions.target(),'created_utc':created,'server_backup_file':r'C:\Backup\test.bak'}
    backup_file=folder/'backup_receipt.json';backup_file.write_text(json.dumps(backup))
    backup.update(receipt_file=str(backup_file),receipt_sha256=vendor.digest(backup_file))
    receipts = [('prepare_designer_publish_plan',plan)]
    if require_backup:
        receipts.append(('backup_designer_test_database',backup))
    for tool, result in receipts:
        identity, _=store.begin('user',sid,tool,{'manifest_file':str(folder/'manifest.json')},package_id=folder.name)
        store.finish(identity,result)
    return plan,backup


def test_test_environment_publish_is_ready_without_backup(workflow, monkeypatch):
    sid, folder = workflow
    plan, _ = ready_for_publish(sid, folder, monkeypatch, require_backup=False)
    state = progress.snapshot('user', sid)
    assert state['publication']['ready']
    assert state['publication']['backup'] is None
    assert state['publication']['backup_required'] is False
    actions = {item['id']: item['enabled'] for item in state['stages'][7]['actions']}
    assert actions['publish']
    assert 'backup' not in actions
    progress_actions.validate_publish(state, {'expected_plan_sha256': plan['plan_sha256']})
    with pytest.raises(ValueError, match='确认内容'):
        progress_actions.validate_publish(state, {'expected_plan_sha256': 'changed'})
    (folder/'publish_plan.json').write_text('{}')
    assert not progress.snapshot('user', sid)['publication']['ready']


def test_optional_backup_receipt_cannot_block_test_publish(workflow, monkeypatch):
    sid, folder = workflow
    plan, _ = ready_for_publish(sid, folder, monkeypatch)
    monkeypatch.setattr(config, 'DESIGNER_REQUIRE_DATABASE_BACKUP', False)
    (folder/'backup_receipt.json').write_text('{}')
    state = progress.snapshot('user', sid)
    assert state['publication']['ready']
    progress_actions.validate_publish(state, {'expected_plan_sha256': plan['plan_sha256']})


def test_required_backup_still_blocks_publish_without_receipt(workflow, monkeypatch):
    sid, folder = workflow
    ready_for_publish(sid, folder, monkeypatch, require_backup=False)
    monkeypatch.setattr(config, 'DESIGNER_REQUIRE_DATABASE_BACKUP', True)
    state = progress.snapshot('user', sid)
    assert not state['publication']['ready']
    assert state['publication']['reason'] == '请先备份数据库'
    assert any(item['id'] == 'backup' for item in state['stages'][7]['actions'])


def test_publish_requires_exact_reviewed_plan_backup_and_target(workflow,monkeypatch):
    sid,folder=workflow
    plan,backup=ready_for_publish(sid,folder,monkeypatch)
    assert progress.snapshot('user',sid)['publication']['ready']
    with pytest.raises(ValueError,match='确认内容'):
        progress.start_action('user',sid,folder.name,'publish')
    options={'expected_plan_sha256':plan['plan_sha256'],'expected_backup_sha256':backup['receipt_sha256']}
    state=progress.snapshot('user',sid)
    progress_actions.validate_publish(state,options)
    (folder/'backup_receipt.json').write_text('{}')
    state=progress.snapshot('user',sid)
    assert not state['publication']['ready']
    assert '凭证已变化' in state['publication']['reason']
    with pytest.raises(ValueError):
        progress.start_action('user',sid,folder.name,'publish',options)


@pytest.mark.parametrize('require_backup', [False, True])
def test_database_update_runs_in_background_and_does_not_complete_wcf(workflow,monkeypatch,require_backup):
    from tools import designer
    sid,folder=workflow
    plan,backup=ready_for_publish(sid,folder,monkeypatch,require_backup=require_backup)
    async def check(path):return {'intact':True}
    monkeypatch.setattr(designer,'check_designer_package',check)
    monkeypatch.setattr(progress.review,'sync_file',lambda path:{'unchanged':True})
    monkeypatch.setattr(progress_actions,'copy_siteinfo',lambda package:folder/'baseline.mdb')
    calls=[]
    def publish(*args,**kwargs):
        calls.append((args,kwargs))
        return {'status':'database_published','target':progress_actions.target()}
    monkeypatch.setattr(publication,'publish_database',publish)
    async def scenario():
        result=progress.start_action('user',sid,folder.name,'publish',{'expected_plan_sha256':plan['plan_sha256'],'expected_backup_sha256':backup['receipt_sha256']})
        assert result['status']=='running'
        await asyncio.gather(*list(progress._tasks))
        state=progress.snapshot('user',sid)
        assert state['stages'][7]['status']=='done'
        assert state['stages'][8]['status']=='pending'
        assert state['stages'][9]['status']=='pending'
        assert state['current']==9
    asyncio.run(scenario())
    assert calls[0][0][:3]==(str(folder/'manifest.json'),plan['manifest_sha256'],backup['receipt_file'] if require_backup else '')
    assert calls[0][1]['expected_target_fingerprint']=='catalog-digest'


def test_wcf_generation_verifies_fields_and_provides_owned_download(workflow,monkeypatch):
    from tools import designer, designer_design
    sid,folder=workflow
    ready_for_publish(sid,folder,monkeypatch)
    progress.start_action('user',sid,folder.name,'confirm_publish')
    async def check(path):return {'intact':True}
    async def compile(path,checksum):return {'compiled_mdb':str(folder/'modified.mdb'),'compiled_sha256':checksum}
    monkeypatch.setattr(designer,'check_designer_package',check)
    monkeypatch.setattr(progress.review,'sync_file',lambda path:{'unchanged':True})
    monkeypatch.setattr(designer_design,'compile_designer_mdb',compile)
    output=folder/'client';output.mkdir();(output/'Camstar.WCFClient.dll').write_bytes(b'valid-dll')
    server=folder/'server'/'bin';server.mkdir(parents=True);(server/'Camstar.WCFService.dll').write_bytes(b'service-dll')
    (output/'App.config').write_text('private server settings')
    result={'status':'services_generated','deployed':False,'partial_package':False,'source_unchanged':True,
            'data_contract_count':10,'service_count':2,'type_checks':[{'name':'Camstar.WCF.ObjectStack.ExSampleChanges','properties':['Code']}],
            'files_sha256':{str(file.relative_to(folder)):vendor.digest(file) for file in [output/'Camstar.WCFClient.dll',server/'Camstar.WCFService.dll',output/'App.config']},'result_file':str(folder/'generation_result.json')}
    (folder/'generation_result.json').write_text(json.dumps(result))
    async def generate(mdb,checksum,names,service_names=None):
        assert names==['ExSample'];assert service_names is None
        return dict(result)
    monkeypatch.setattr(designer_design,'generate_designer_wcf_package',generate)
    async def scenario():
        progress.start_action('user',sid,folder.name,'wcf',{'verify_types':['ExSample']})
        await asyncio.gather(*list(progress._tasks))
        state=progress.snapshot('user',sid)
        assert state['stages'][8]['status']=='done'
        assert state['stages'][9]['status']=='pending'
        assert state['current']==10
        archive=progress_actions.wcf_archive(state['wcf'])
        import zipfile
        with zipfile.ZipFile(archive) as file:
            assert file.read('client/Camstar.WCFClient.dll')==b'valid-dll'
            assert file.read('server/bin/Camstar.WCFService.dll')==b'service-dll'
            assert 'client/App.config' not in file.namelist()
    asyncio.run(scenario())
    app=FastAPI();app.include_router(routes.router);client=TestClient(app)
    assert client.get(f'/api/progress/{sid}/wcf',params={'username':'user','package_id':folder.name}).status_code==200
    other=memory.create_session('user')
    assert client.get(f'/api/progress/{other}/wcf',params={'username':'user','package_id':folder.name}).status_code==404
    (output/'Camstar.WCFClient.dll').write_bytes(b'changed')
    assert client.get(f'/api/progress/{sid}/wcf',params={'username':'user','package_id':folder.name}).status_code==409


def test_wcf_rejects_missing_fields_partial_output_and_foreign_objects(workflow):
    sid,folder=workflow
    state=progress.snapshot('user',sid)
    with pytest.raises(ValueError,match='1～20'):
        progress_actions.wcf_types(state['package'],[])
    with pytest.raises(ValueError,match='来自当前设计'):
        progress_actions.wcf_types(state['package'],['OtherUserObject'])
    result={'status':'services_generated','source_unchanged':True,'partial_package':True}
    with pytest.raises(ValueError,match='全量生成'):
        progress_actions.verify_wcf_fields(result,state,['ExSample'])
    result.update(partial_package=False,type_checks=[{'name':'Camstar.WCF.ObjectStack.ExSampleChanges','properties':[]}])
    with pytest.raises(ValueError,match='当前字段'):
        progress_actions.verify_wcf_fields(result,state,['ExSample'])


def test_new_wcf_generation_clears_previous_deployment_and_acceptance(workflow,monkeypatch):
    from tools import designer
    sid,folder=workflow
    ready_for_publish(sid,folder,monkeypatch)
    for action in ('confirm_publish','confirm_wcf','confirm_services','confirm_complete'):
        progress.start_action('user',sid,folder.name,action)
    assert progress.snapshot('user',sid)['completed']==11
    async def check(path):raise ValueError('generation stopped before execution')
    monkeypatch.setattr(designer,'check_designer_package',check)
    async def scenario():
        progress.start_action('user',sid,folder.name,'wcf',{'verify_types':['ExSample']})
        await asyncio.gather(*list(progress._tasks))
        state=progress.snapshot('user',sid)
        assert state['stages'][8]['status']=='failed'
        assert state['stages'][9]['status']=='pending'
        assert state['stages'][10]['status']=='pending'
    asyncio.run(scenario())


def test_old_service_and_acceptance_confirmations_migrate_without_loss(workflow):
    sid,folder=workflow
    with store.connection() as conn:
        for stage in (9,10):
            conn.execute('INSERT INTO confirmations VALUES (?,?,?,?,?,?,?)',('user',sid,folder.name,stage,'confirmed',time.time(),1))
    assert store.confirmations('user',sid,folder.name)=={9:'confirmed',10:'confirmed',11:'confirmed'}
    store.confirm('user',sid,folder.name,10,'confirmed')
    assert store.confirmations('user',sid,folder.name)[11]=='confirmed'


def test_catalog_drift_stops_update_before_official_processor(workflow,monkeypatch):
    from contextlib import contextmanager
    sid,folder=workflow
    plan,backup=ready_for_publish(sid,folder,monkeypatch)
    class Cursor:
        def execute(self,sql,*args):
            if 'IS_MEMBER' in sql:self.row=('test-db',1,1)
            elif 'SCHEMA_NAME' in sql:self.row=('dbo',1)
            elif 'sp_getapplock' in sql:self.row=(0,)
            else:raise AssertionError(f'Unexpected database statement: {sql}')
            return self
        def fetchone(self):return self.row
    class Connection:
        def cursor(self):return Cursor()
    @contextmanager
    def connection(*args,**kwargs):yield Connection()
    monkeypatch.setattr(publication,'target_connection',connection)
    monkeypatch.setattr(publication,'target_schema',lambda cursor:'dbo')
    monkeypatch.setattr(publication,'metadata_fingerprint',lambda cursor,schema:'changed-catalog')
    dlls=folder/'dlls';dlls.mkdir()
    for name in ('Camstar.Metadata.dll','Camstar.Data.dll','OECAdmin.dll','CIMS.DBUpdate.dll'):(dlls/name).write_bytes(b'test')
    monkeypatch.setattr(vendor,'assembly',lambda:dlls/'Camstar.Metadata.dll')
    def compile(output,request):
        file=output/'compiled.mdb';file.write_bytes(b'compiled');return {'compiled_mdb':str(file)}
    monkeypatch.setattr(vendor,'run',compile)
    def no_processor(*args,**kwargs):raise AssertionError('Official update must not execute after catalog drift')
    monkeypatch.setattr(publication.subprocess,'run',no_processor)
    with pytest.raises(ValueError,match='目标设计已变化'):
        publication.publish_database(str(folder/'manifest.json'),plan['manifest_sha256'],backup['receipt_file'],str(folder/'baseline.mdb'),expected_target_fingerprint='catalog-digest')


def test_official_update_without_backup_never_executes_backup_sql(workflow, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    sid, folder = workflow
    plan, _ = ready_for_publish(sid, folder, monkeypatch, require_backup=False)
    (folder/'backup_receipt.json').unlink()
    statements = []
    class Cursor:
        def execute(self, sql, *args):
            statements.append(sql)
            if 'IS_MEMBER' in sql: self.row = ('test-db', 1, 1)
            elif 'SCHEMA_NAME' in sql: self.row = ('dbo', 1)
            elif 'sp_getapplock' in sql: self.row = (0,)
            elif 'SELECT CDOName' in sql: self.row = ('ExSample',)
            else: raise AssertionError(f'Unexpected database statement: {sql}')
            return self
        def fetchone(self): return self.row
    class Connection:
        def cursor(self): return Cursor()
    @contextmanager
    def connection(*args, **kwargs): yield Connection()
    monkeypatch.setattr(publication, 'target_connection', connection)
    monkeypatch.setattr(publication, 'target_schema', lambda cursor: 'dbo')
    monkeypatch.setattr(publication, 'metadata_fingerprint', lambda *args: 'catalog-digest')
    monkeypatch.setattr(publication, 'record_published_baseline', lambda *args: {'status': 'verified'})
    dlls = folder/'dlls'; dlls.mkdir()
    for name in ('Camstar.Metadata.dll', 'Camstar.Data.dll', 'OECAdmin.dll', 'CIMS.DBUpdate.dll'):
        (dlls/name).write_bytes(b'test')
    monkeypatch.setattr(vendor, 'assembly', lambda: dlls/'Camstar.Metadata.dll')
    def compile(output, request):
        file = output/'compiled.mdb'; file.write_bytes(b'compiled')
        return {'compiled_mdb': str(file)}
    monkeypatch.setattr(vendor, 'run', compile)
    def processor(args, **kwargs):
        (Path(kwargs['cwd'])/'result.json').write_text(json.dumps({'ok': True, 'result': {}}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(publication.subprocess, 'run', processor)
    result = publication.publish_database(str(folder/'manifest.json'), plan['manifest_sha256'],
        siteinfo_mdb=str(folder/'baseline.mdb'), expected_target_fingerprint='catalog-digest')
    assert result['status'] == 'database_published'
    assert result['backup_receipt'] is None
    assert result['backup_required'] is False
    assert all('BACKUP' not in sql and 'RESTORE' not in sql for sql in statements)
    monkeypatch.setattr(config, 'DESIGNER_REQUIRE_DATABASE_BACKUP', True)
    with pytest.raises(ValueError, match='请先备份数据库'):
        publication.publish_database(str(folder/'manifest.json'), plan['manifest_sha256'], siteinfo_mdb=str(folder/'baseline.mdb'))


@pytest.mark.parametrize('require_backup', [False, True])
def test_batch_publish_plan_compares_latest_mdb_to_original_owned_baseline(workflow,monkeypatch,require_backup):
    from contextlib import contextmanager
    sid,original=workflow
    monkeypatch.setattr(config,'DESIGNER_TEST_TARGET_CONFIRMED',True)
    monkeypatch.setattr(config,'DESIGNER_REQUIRE_DATABASE_BACKUP',require_backup)
    latest=original.parent/('b'*32);latest.mkdir()
    manifest=json.loads((original/'manifest.json').read_text())
    manifest.update(source_file=str(original/'modified.mdb'),source_sha256=vendor.digest(original/'modified.mdb'),
        operations=[{'action':'add_field','owner':'ExSample','name':'NextField','field_type':'String40'}])
    for filename in ('baseline.mdb','modified.mdb','changes.xml'):(latest/filename).write_bytes((original/filename).read_bytes())
    (latest/'manifest.json').write_text(json.dumps(manifest))
    add_tool(sid,'generate_designer_design_package',{}, {'files':{'manifest.json':str(latest/'manifest.json')}})
    exports=[]
    def export(folder,baseline,modified):
        exports.append((baseline.read_bytes(),modified.read_bytes()))
        file=folder/'exported.xml';file.write_bytes(b'<InSiteMetaData><Header><Version>1.0</Version></Header><Import/></InSiteMetaData>')
        return {'xml_file':str(file),'validation':{'valid':True}}
    monkeypatch.setattr(vendor,'export',export)
    monkeypatch.setattr(publication,'inspect_target',lambda *args:{**progress_actions.target(),'columns':[]})
    class Connection:
        def cursor(self):return None
    @contextmanager
    def connection():yield Connection()
    monkeypatch.setattr(publication,'target_connection',connection)
    monkeypatch.setattr(publication,'target_schema',lambda cursor:'dbo')
    monkeypatch.setattr(publication,'metadata_fingerprint',lambda *args:'catalog-digest')
    state=progress.snapshot('user',sid)
    result=progress_actions.prepare_plan(state['package'],state['_manifests'])
    assert exports==[(b'base',b'new design')]
    merged=json.loads(Path(result['manifest_file']).read_text())
    assert merged['source_file']==str(original.parent.parent/'source.mdb')
    assert {op['name'] for op in merged['operations']}=={'ExSample','Code','NextField'}
    assert result['ready_for_publish']
    assert result['verified_backup_required'] is require_backup
    assert any('备份' in step for step in result['required_steps']) is require_backup
