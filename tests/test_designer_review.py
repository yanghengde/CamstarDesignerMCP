"""Designer handoff integrity, session ownership, and permission fallback."""
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from designer import review, vendor
from agent import memory
from web import routes


@pytest.fixture
def design_package(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    folder = tmp_path / 'artifacts' / ('a' * 32)
    folder.mkdir(parents=True)
    (folder / 'modified.mdb').write_bytes(b'valid design')
    (folder / 'changes.xml').write_bytes(b'<InSiteMetaData/>')
    manifest = {'status': 'saved_to_test_copy_and_exported', 'workspace_applied_to_mdb': True,
                'workspace': '200', 'operations': [{'action': 'create_cdo', 'name': 'ExSample'},
                                                  {'action': 'add_field', 'owner': 'ExSample', 'name': 'Code'}],
                'artifacts': {name: vendor.digest(folder / name) for name in ('modified.mdb', 'changes.xml')}}
    path = folder / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


def test_summary_and_changed_mdb_refusal(design_package):
    result = review.summary(str(design_package))
    assert result['objects'] == ['ExSample']
    assert result['field_changes'] == 1
    assert result['database_published'] is False
    (design_package.parent / 'modified.mdb').write_bytes(b'edited')
    with pytest.raises(ValueError, match='SHA256'):
        review.prepare(str(design_package), True)


def test_failed_package_and_outside_path_are_rejected(design_package, tmp_path):
    data = json.loads(design_package.read_text())
    data['status'] = 'failed'
    design_package.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='尚未成功'):
        review.summary(str(design_package))
    outside = tmp_path.parent / 'manifest.json'
    with pytest.raises(ValueError, match='DESIGNER_ROOT'):
        review.summary(str(outside))


def test_permission_fallback_does_not_claim_designer_open(design_package, monkeypatch):
    for key, value in {'DESIGNER_DB_SERVER': 'test-host', 'DESIGNER_SERVER_SHARE': r'\\test-host\C',
                       'DESIGNER_WINDOWS_USER': 'test-user', 'DESIGNER_WINDOWS_PASSWORD': 'test-secret',
                       'DESIGNER_UI_EXE': r'C:\tools\Designer.exe'}.items():
        monkeypatch.setattr(config, key, value)
    def transfer(args, **kwargs):
        assert 'test-secret' not in ' '.join(args)
        assert kwargs['env']['DESIGNER_WINDOWS_PASSWORD'] == 'test-secret'
        Path(args[args.index('-ResultFile') + 1]).write_text(json.dumps({
            'activated': False, 'status': 'copied_for_designer', 'server_mdb': r'C:\Temp\Review\InSite.mdb',
            'server_siteinfo': r'C:\Temp\Review\SiteInfo.mdb',
            'copied_sha256': vendor.digest(design_package.parent / 'modified.mdb')}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(review.subprocess, 'run', transfer)
    result = review.prepare(str(design_package), True)
    assert result['status'] == 'copied_for_designer'
    assert result['designer_open_verified'] is False
    assert '选择' in result['instructions']
    assert review.summary(str(design_package))['review']['server_mdb'] == result['server_mdb']


def test_session_controls_download_and_handoff(design_package, monkeypatch, tmp_path):
    monkeypatch.setattr(memory, 'SESSIONS_DIR', str(tmp_path / 'sessions'))
    monkeypatch.setattr(memory, 'user_memories', {})
    session = memory.create_session('review-user')
    other = memory.create_session('review-user')
    messages = memory.get_user_messages('review-user', session)
    messages.append({'role': 'tool', 'name': 'generate_designer_cdo_package',
                     'content': str({'files': {'manifest.json': str(design_package)}})})
    app = FastAPI(); app.include_router(routes.router)
    client = TestClient(app)
    history = client.get('/history/review-user', params={'session_id': session}).json()
    assert history['designer_results'][0]['objects'] == ['ExSample']
    package_id = design_package.parent.name
    url = f'/designer/results/{package_id}/mdb'
    assert client.get(url, params={'username': 'review-user', 'session_id': session}).content == b'valid design'
    assert client.get(url, params={'username': 'review-user', 'session_id': other}).status_code == 404
    calls = []
    monkeypatch.setattr(review, 'prepare', lambda path, activate: calls.append((path, activate)) or {'status': 'copied_for_designer'})
    assert client.post('/designer/review', json={'username': 'review-user', 'session_id': other, 'package_id': package_id}).status_code == 404
    assert not calls
    assert client.post('/designer/review', json={'username': 'review-user', 'session_id': session, 'package_id': package_id}).status_code == 200
    assert calls == [(str(design_package), True)]


def test_session_packages_ignores_failed_or_unrelated_tools(design_package):
    valid = {'role': 'tool', 'name': 'generate_designer_design_package',
             'content': str({'files': {'manifest.json': str(design_package)}})}
    assert len(review.session_packages([valid, valid])) == 1
    assert review.session_packages([{**valid, 'name': 'unrelated'}, {**valid, 'content': 'Error'}]) == []
