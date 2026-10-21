"""Business-rule browsing uses real bindings and preserves source/owner boundaries."""
from copy import deepcopy
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from agent import assistant_modes
from designer import clf, vendor, working, testing_runtime as native, testing_store as store
from tests.test_excel_attachments import isolated_store
from web import routes, clf_routes, testing_routes


@pytest.fixture
def browser(isolated_store, monkeypatch):
    root = isolated_store / 'designer'
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(root))
    identity = 'a' * 32
    file = root / 'workspaces' / identity / 'InSite.mdb'
    file.parent.mkdir(parents=True); file.write_bytes(b'unchanged-rules')
    context = {'id': identity, 'mdb_file': str(file), 'latest_manifest': ''}
    monkeypatch.setattr(working, 'available', lambda: [context])
    monkeypatch.setattr(working, 'summary', lambda key: context if key == identity else None)
    index = {
        'objects': [
            {'name': 'Product', 'label': '产品', 'parent': 'Base', 'maintenance': 'ProductMaint'},
            {'name': 'ProductMaint', 'label': '产品维护', 'parent': 'Base', 'maintenance': ''},
            {'name': 'Base', 'label': '基础对象', 'parent': '', 'maintenance': ''},
            {'name': 'Other', 'label': '其他', 'parent': '', 'maintenance': ''}],
        'rules': [
            {'name': 'ExCheck', 'description': '检查产品', 'customer': True, 'workspace': '200'},
            {'name': 'BaseRule', 'description': '继承校验', 'customer': False, 'workspace': 'csi'},
            {'name': 'Product_LooksRelated', 'description': '仅名称相似', 'customer': False, 'workspace': 'csi'}],
        'bindings': [
            {'clf': 'ExCheck', 'object': 'ProductMaint', 'event': 'Validate', 'field_level': False},
            {'clf': 'BaseRule', 'object': 'Base', 'event': 'AfterValidate', 'field_level': False},
            {'clf': 'Product_LooksRelated', 'object': 'Other', 'event': 'Validate', 'field_level': False}]}
    calls = []
    def query(path, mode, kind, **kwargs):
        calls.append((mode, kind, kwargs))
        assert mode == 'catalog' and kind == 'clf' and kwargs['name'] == 'rule_browser'
        return deepcopy(index)
    monkeypatch.setattr(vendor, 'query', query)
    app = FastAPI(); app.include_router(routes.router); app.include_router(clf_routes.router); app.include_router(testing_routes.router)
    with TestClient(app) as client:
        sid = client.post('/sessions/user/new?mode=clf').json()['session_id']
        assistant_modes.session('user', sid)['clf_context'] = {'project_id': identity, 'object_name': 'Product'}
        yield client, sid, file, index, calls, context


def test_rules_follow_maintenance_and_parent_bindings_not_name_guessing(browser):
    _, _, file, _, calls, _ = browser
    before = vendor.digest(file)
    result = clf.related_rules(str(file), 'Product')
    assert [r['name'] for r in result['records']] == ['ExCheck', 'BaseRule']
    assert result['dynamic_usage_verified'] is False
    assert len(calls) == 1
    assert vendor.digest(file) == before


def test_customer_scope_search_and_paging_do_not_truncate_total(browser):
    _, _, file, index, *_ = browser
    result = clf.related_rules(str(file), scope='customer', search='产品')
    assert [r['name'] for r in result['records']] == ['ExCheck']
    index['rules'] += [dict(name='Extra' + str(i), description='', customer=False, workspace='csi') for i in range(25)]
    result = clf.related_rules(str(file), scope='all', offset=20)
    assert result['total'] == 28 and len(result['records']) == 8


def test_object_search_preserves_selected_object_and_real_label(browser):
    _, _, file, *_ = browser
    result = clf.rule_objects(str(file), search='产品', selected='Other')
    assert {o['name'] for o in result['records']} == {'Product', 'ProductMaint', 'Other'}
    assert result['total'] == 2


def test_cycle_and_invalid_object_scope_are_handled(browser):
    _, _, file, index, *_ = browser
    index['objects'][2]['parent'] = 'Product'
    assert clf.related_rules(str(file), 'Product')['total'] == 2
    with pytest.raises(ValueError, match='不存在'):
        clf.related_rules(str(file), 'Nonexistent')
    with pytest.raises(ValueError, match='范围'):
        clf.related_rules(str(file), scope='arbitrary')


def test_source_change_during_catalog_read_is_rejected(browser, monkeypatch):
    _, _, file, index, *_ = browser
    def change(*args, **kwargs):
        file.write_bytes(b'changed-during-read')
        return deepcopy(index)
    monkeypatch.setattr(vendor, 'query', change)
    with pytest.raises(ValueError, match='已变化'):
        clf.related_rules(str(file), 'Product')


def test_new_catalog_routes_enforce_owner_mode_and_current_project(browser):
    client, sid, file, *_ = browser
    for route in ('objects', 'rules'):
        path = '/api/clf/' + route
        result = client.get(path, params={'username': 'user', 'session_id': sid, 'object_name': 'Product'} if route == 'rules' else {'username': 'user', 'session_id': sid})
        assert result.status_code == 200
        assert result.json()['source_sha256'] == vendor.digest(file)
        assert client.get(path, params={'username': 'other', 'session_id': sid}).status_code == 404
    normal = client.post('/sessions/user/new').json()['session_id']
    assert client.get('/api/clf/rules', params={'username': 'user', 'session_id': normal}).status_code == 409


def test_context_preserves_object_when_optional_field_is_omitted(browser):
    client, sid, _, _, _, context = browser
    request = {'username': 'user', 'session_id': sid, 'project_id': context['id'], 'clf_name': 'ExCheck'}
    result = client.put('/api/clf/context', json=request)
    assert result.status_code == 200 and result.json()['object_name'] == 'Product'
    result = client.put('/api/clf/context', json={**request, 'object_name': ''})
    assert result.json()['object_name'] == ''


def test_test_bootstrap_selects_matching_default_without_exposing_password(browser, monkeypatch):
    client, sid, _, _, _, context = browser
    monkeypatch.setattr(config, 'DESIGNER_WCF_ADDRESS', 'https://system.invalid/WCF/')
    profile = {'id': 'b' * 32, 'endpoint': 'https://system.invalid/WCF/', 'protected_password': 'never-plaintext',
               'project_id': context['id'], 'artifact_id': 'c' * 32, 'name': 'system', 'version': 1}
    monkeypatch.setattr(store, 'items', lambda owner, kind: [profile] if kind == 'environment' else [])
    monkeypatch.setattr(store, 'history', lambda owner: [])
    monkeypatch.setattr(native, 'artifacts', lambda: [])
    response = client.get('/api/testing/bootstrap', params={'username': 'user', 'session_id': sid})
    assert response.status_code == 200
    assert response.json()['defaults']['environment_id'] == profile['id']
    assert response.json()['context']['project_id'] == context['id']
    assert 'never-plaintext' not in response.text
