"""Exercise real workspace bookkeeping with a deterministic vendor adapter."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from agent import memory
from designer import progress, progress_actions, publication, review, vendor, working
from tools.designer import check_designer_package
from web import routes


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    monkeypatch.setattr(config, 'DESIGNER_PROGRESS_DB', str(tmp_path/'progress.sqlite'))
    monkeypatch.setattr(memory, 'SESSIONS_DIR', str(tmp_path/'sessions'))
    monkeypatch.setattr(memory, 'user_memories', {})
    monkeypatch.setattr(routes, 'CHAT_USERNAME', 'user')
    source = tmp_path/'seed.mdb'; source.write_bytes(b'published seed')
    dll = tmp_path/'metadata.dll'; dll.write_bytes(b'vendor')
    monkeypatch.setattr(vendor, 'assembly', lambda: dll)
    monkeypatch.setattr(vendor, 'select_workspace', lambda source, workspace: workspace or '200')
    def run(folder, request):
        if request['mode'] == 'apply':
            mdb = Path(request['mdb'])
            mdb.write_bytes(mdb.read_bytes() + b'|' + request['operations'][0]['name'].encode())
            return {'reloaded_definitions': True}
        return {'total': 1, 'items': []}
    monkeypatch.setattr(vendor, 'run', run)
    def export(folder, baseline, modified):
        xml = folder/'diff.xml'; xml.write_text('<InSiteMetaData><Header><Version>1.0</Version></Header><Import/></InSiteMetaData>', encoding='utf-8')
        return {'xml_file': str(xml), 'validation': {}}
    monkeypatch.setattr(vendor, 'export', export)
    return source


def generate(source, name):
    current = working.resolve(str(source))
    return vendor.design(str(source), vendor.digest(current), '200', [{'action':'create_cdo', 'name':name, 'parent':'Product'}])


def manifest(result):
    return Path(result['files']['manifest.json'])


def record(sid, result):
    memory.get_user_messages('user', sid).extend([
        {'role':'user', 'content':'新增对象'},
        {'role':'tool', 'name':'generate_designer_design_package', 'content':str(result)}])


def test_continuous_design_reuses_one_file_and_retains_all_changes(project):
    seed = project.read_bytes()
    first = generate(project, 'ExFirst')
    second = generate(project, 'ExSecond')
    assert first['working_mdb'] == second['working_mdb'] == second['files']['modified.mdb']
    assert Path(second['working_mdb']).read_bytes() == seed + b'|ExFirst|ExSecond'
    assert working.resolve(first['files']['modified.mdb']) == Path(second['working_mdb'])
    assert working.resolve(str(manifest(first).parent/'modified.mdb')) == Path(second['working_mdb'])
    assert project.read_bytes() == seed
    assert json.loads(manifest(second).read_text())['parent_manifest_file'] == str(manifest(first))
    assert working.backups(first['working_project_id']) == []
    assert asyncio.run(check_designer_package(str(manifest(second))))['intact']
    assert not asyncio.run(check_designer_package(str(manifest(first))))['intact']


def test_release_backup_is_delayed_exact_and_once_per_cycle(project):
    first = generate(project, 'ExFirst')
    siteinfo = project.parent/'SiteInfo.mdb'; siteinfo.write_bytes(b'site configuration')
    working.mark_published(manifest(first), siteinfo_file=str(siteinfo))
    assert working.backups(first['working_project_id']) == []
    working.mark_published(manifest(first), siteinfo_file=str(siteinfo))
    siteinfo.write_bytes(b'changed later')
    second = generate(project, 'ExSecond')
    versions = working.backups(first['working_project_id'])
    assert len(versions) == 1
    assert working.backup_file(first['working_project_id'], versions[0]['id'], 'mdb').read_bytes() == b'published seed|ExFirst'
    assert working.backup_file(first['working_project_id'], versions[0]['id'], 'siteinfo').read_bytes() == b'site configuration'
    assert not json.loads(manifest(second).read_text())['parent_manifest_file']
    generate(project, 'ExThird')
    assert working.backups(first['working_project_id']) == versions
    assert Path(first['working_mdb']).read_bytes().endswith(b'|ExFirst|ExSecond|ExThird')


def test_retention_removes_only_oldest_backups_and_keeps_work_file(project):
    result = generate(project, 'Ex0')
    recorded = []
    for index in range(1, 13):
        working.mark_published(manifest(result))
        result = generate(project, 'Ex'+str(index))
        recorded.append(working.backups(result['working_project_id'])[0]['id'])
    versions = working.backups(result['working_project_id'])
    assert [item['id'] for item in versions] == list(reversed(recorded[2:]))
    for identity in recorded[:2]:
        assert not (working.project_dir(result['working_project_id'])/'backups'/identity).exists()
    assert len(list((working.project_dir(result['working_project_id'])/'backups').iterdir())) == 10
    assert Path(result['working_mdb']).read_bytes().endswith(b'|Ex12')


@pytest.mark.parametrize('stage', ['apply','export','backup'])
def test_failed_design_or_backup_never_overwrites_work_file(project, monkeypatch, stage):
    first = generate(project, 'ExFirst')
    before = Path(first['working_mdb']).read_bytes()
    working.mark_published(manifest(first))
    def fail(*args, **kwargs):
        raise ValueError('expected failure')
    monkeypatch.setattr(vendor if stage != 'backup' else working,
                        {'apply':'run','export':'export','backup':'backup_release'}[stage], fail)
    with pytest.raises(ValueError, match='expected failure'):
        generate(project, 'ExFailed')
    assert Path(first['working_mdb']).read_bytes() == before
    state = working.load(first['working_project_id'])
    assert state['latest_manifest'] == str(manifest(first))
    assert state['backup_pending']


def test_wrong_hash_and_archived_packages_cannot_replace_current_file(project, monkeypatch):
    first = generate(project, 'ExFirst')
    second = generate(project, 'ExSecond')
    with pytest.raises(ValueError, match='SHA256'):
        vendor.design(str(project), vendor.digest(project), '200', [{'action':'create_cdo','name':'ExOld','parent':'Product'}])
    archived = review.summary(str(manifest(first)))
    assert not archived['is_current']
    assert archived['mdb_file'] == str(manifest(first).parent/'modified.mdb')
    with pytest.raises(ValueError, match='最新设计'):
        review.prepare(str(manifest(first)), True)
    with pytest.raises(ValueError, match='最新设计'):
        publication.preflight(str(manifest(first)))
    assert review.summary(str(manifest(second)))['mdb_file'] == second['working_mdb']


def test_progress_tracks_owned_batches_and_resets_after_release(project):
    sid = memory.create_session('user')
    first = generate(project, 'ExFirst'); record(sid, first)
    second = generate(project, 'ExSecond'); record(sid, second)
    state = progress.snapshot('user', sid)
    assert len(state['_manifests']) == 2
    assert state['package']['objects'] == ['ExFirst','ExSecond']
    working.mark_published(manifest(second))
    third = generate(project, 'ExThird'); record(sid, third)
    state = progress.snapshot('user', sid)
    assert len(state['_manifests']) == 1
    assert state['package']['objects'] == ['ExThird']
    assert state['current'] == 5


def test_manual_publish_confirmation_triggers_next_design_backup(project):
    sid = memory.create_session('user')
    first = generate(project, 'ExFirst'); record(sid, first)
    messages = memory.get_user_messages('user', sid)
    messages.append({'role':'tool','name':'check_designer_package','content':str({'intact':True}),
                     'tool_call_id':'checked'})
    messages.insert(-1, {'role':'assistant','tool_calls':[{'id':'checked','function':{'arguments':json.dumps({'manifest_file':str(manifest(first))})}}]})
    for action in ('confirm_review','confirm_compile','confirm_publish'):
        assert progress.start_action('user', sid, manifest(first).parent.name, action)['status'] == 'confirmed'
    assert working.load(first['working_project_id'])['published']['method'] == 'manual'
    generate(project, 'ExSecond')
    assert len(working.backups(first['working_project_id'])) == 1


def test_backup_download_requires_owned_package_and_intact_version(project):
    sid = memory.create_session('user'); other = memory.create_session('user')
    first = generate(project, 'ExFirst'); record(sid, first)
    working.mark_published(manifest(first))
    second = generate(project, 'ExSecond'); record(sid, second)
    version = working.backups(first['working_project_id'])[0]
    app = FastAPI(); app.include_router(routes.router); client = TestClient(app)
    url = f"/designer/results/{manifest(second).parent.name}/backups/{version['id']}/mdb"
    assert client.get(url, params={'username':'user','session_id':sid}).content == b'published seed|ExFirst'
    assert client.get(url, params={'username':'user','session_id':other}).status_code == 404
    assert client.get(url.replace(version['id'], 'invalid-id'), params={'username':'user','session_id':sid}).status_code == 404
    with pytest.raises(ValueError, match='无效'):
        working.backup_file(first['working_project_id'], '..', 'mdb')
    working.backup_file(first['working_project_id'], version['id'], 'mdb').write_bytes(b'corrupt')
    assert client.get(url, params={'username':'user','session_id':sid}).status_code == 404


def test_designer_prepares_fixed_path_then_refreshes_it_on_next_generation(project, monkeypatch):
    for key,value in {'DESIGNER_DB_SERVER':'test-host','DESIGNER_SERVER_SHARE':r'\\test-host\C',
                      'DESIGNER_WINDOWS_USER':'user','DESIGNER_WINDOWS_PASSWORD':'secret',
                      'DESIGNER_UI_EXE':r'C:\tools\Designer.exe'}.items():
        monkeypatch.setattr(config,key,value)
    paths = []; remote = {}
    def transfer(args, **kwargs):
        if '-ResultFile' in args:
            project_id = args[args.index('-ProjectId')+1]
            server_mdb = rf'C:\DesignerWorkspace\{project_id}\InSite.mdb'
            paths.append(server_mdb)
            remote[server_mdb] = Path(args[args.index('-LocalMdb')+1]).read_bytes()
            Path(args[args.index('-ResultFile')+1]).write_text(json.dumps({
                'copied_sha256': args[args.index('-ExpectedSha256')+1], 'server_mdb':server_mdb,
                'server_siteinfo':rf'C:\DesignerWorkspace\{project_id}\SiteInfo.mdb', 'activated':False}),encoding='utf-8')
        else:
            Path(args[args.index('-LocalFile')+1]).write_bytes(b'SiteInfo')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(review.subprocess,'run',transfer)
    first = generate(project,'ExFirst')
    receipt = review.prepare(str(manifest(first)),True)
    assert receipt['working']['server_mdb'] == paths[0]
    working.mark_published(manifest(first),method='manual')
    second = generate(project,'ExSecond')
    assert paths == [paths[0], paths[0]]
    assert remote[paths[0]] == Path(second['working_mdb']).read_bytes()
    assert review.valid_server_file(paths[0], 'InSite.mdb')
    assert review.valid_server_file(receipt['server_siteinfo'], 'SiteInfo.mdb')
    assert working.backups(first['working_project_id'])[0]['siteinfo_sha256']


def test_server_sync_failure_keeps_local_success_and_reports_retry(project, monkeypatch):
    first = generate(project,'ExFirst')
    state = working.load(first['working_project_id'])
    working.write_json(working.project_dir(state['id'])/'state.json', {**state,'server_mdb':'configured'})
    def fail(*args):
        raise ValueError('server locked')
    monkeypatch.setattr(review,'prepare',fail)
    second = generate(project,'ExSecond')
    assert second['designer_sync_error'] == 'server locked'
    assert Path(second['working_mdb']).read_bytes().endswith(b'|ExSecond')


def test_pending_file_commit_is_recovered_after_state_write_interruption(project):
    first = generate(project,'ExFirst')
    folder = working.project_dir(first['working_project_id'])
    state = working.load(first['working_project_id'])
    working.write_json(folder/'pending.json', {**state,'updated_utc':'recovered'})
    assert working.load(first['working_project_id'])['updated_utc'] == 'recovered'
    assert not (folder/'pending.json').exists()


def test_new_chat_publish_plan_compares_entire_work_file_to_cycle_baseline(project, monkeypatch):
    first = generate(project,'ExFirst')
    second = generate(project,'ExSecond')
    captured = {}
    def preflight(path):
        captured.update(json.loads(Path(path).read_text()))
        return {'target':{'server':'test-host','database':'test-db'}, 'blockers':[],
                'plan_file':str(Path(path).parent/'plan.json')}
    monkeypatch.setattr(publication,'preflight',preflight)
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def cursor(self): return None
    monkeypatch.setattr(publication,'target_connection',lambda:Connection())
    monkeypatch.setattr(publication,'target_schema',lambda cursor:'schema')
    monkeypatch.setattr(publication,'metadata_fingerprint',lambda *args:'fingerprint')
    package = review.summary(str(manifest(second)))
    plan = progress_actions.prepare_plan(package,[str(manifest(second))])
    assert plan['ready_for_publish']
    assert captured['source_sha256'] == vendor.digest(project)
    assert [op['name'] for op in captured['operations']] == ['ExFirst','ExSecond']
    assert captured['artifacts']['modified.mdb'] == vendor.digest(Path(second['working_mdb']))
    working.mark_published(manifest(second))
    third = generate(project,'ExThird')
    progress_actions.prepare_plan(review.summary(str(manifest(third))),[str(manifest(third))])
    assert captured['source_sha256'] == json.loads(manifest(second).read_text())['artifacts']['modified.mdb']
    assert [op['name'] for op in captured['operations']] == ['ExThird']


def test_backup_cannot_be_used_as_design_input(project):
    first = generate(project,'ExFirst'); working.mark_published(manifest(first))
    generate(project,'ExSecond')
    version = working.backups(first['working_project_id'])[0]
    file = working.backup_file(first['working_project_id'],version['id'],'mdb')
    with pytest.raises(ValueError,match='当前工作'):
        working.resolve(str(file))


def test_manual_update_refreshes_existing_published_baseline_without_database_writes(project, monkeypatch):
    first = generate(project,'ExFirst')
    monkeypatch.setattr(config,'DESIGNER_TEST_TARGET_CONFIRMED',True)
    monkeypatch.setattr(config,'DESIGNER_DB_SERVER','test-host')
    monkeypatch.setattr(config,'DESIGNER_DB_NAME','test-db')
    checkpoint=project.parent/'published_baseline.json'
    checkpoint.write_text(json.dumps({'target':{'server':'test-host','database':'test-db'},'sha256':'previous'}))
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def cursor(self): return None
    monkeypatch.setattr(publication,'target_connection',lambda:Connection())
    monkeypatch.setattr(publication,'target_schema',lambda cursor:'schema')
    monkeypatch.setattr(publication,'metadata_fingerprint',lambda *args:'current catalog')
    publication.record_manual_baseline(str(manifest(first)))
    saved=json.loads(checkpoint.read_text())
    assert saved['sha256'] == vendor.digest(Path(first['working_mdb']))
    assert saved['metadata_fingerprint'] == 'current catalog'
    assert saved['method'] == 'manual'
