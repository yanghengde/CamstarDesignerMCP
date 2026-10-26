"""A completed stage must not imply publication, deployment or business acceptance."""
from hashlib import sha256

from designer import clf_workspace as core, publication, testing_store as store
from tests.test_clf_core import setup, receipt
from tests.test_excel_attachments import isolated_store


def plan(op):
    return core.save('user', 'plan', 'f'*32, 0, {'project_id': op['project_id'],
        'result': {'metadata_preflight_passed': True, 'source_sha256': op['source_sha256']}})


def saved(op, path):
    return core.save('user', 'delivery', 'f'*32, 0, {'plan_id': 'f'*32, 'action': 'save', 'status': 'saved',
        'result': {'saved_sha256': op['source_sha256'], 'files': {'manifest.json': str(path.parent/'manifest.json')}}})


def report(client):
    response = client.get('/api/clf/workspace/plans/'+'f'*32+'/report?username=user')
    assert response.status_code == 200, response.text
    return response.json()


def test_report_requires_owner_and_never_claims_release_from_preflight(setup):
    client, op, _, seen = setup
    plan(op)
    result = report(client)
    assert not result['ready_for_release']
    assert result['stages'][0]['status'] == 'passed'
    assert next(s for s in result['stages'] if s['id'] == 'database')['status'] == 'not_recorded'
    assert next(s for s in result['stages'] if s['id'] == 'wcf')['status'] == 'applicability_unknown'
    assert result['test_scope']['total'] == 0
    assert client.get('/api/clf/workspace/plans/'+'f'*32+'/report?username=other').status_code == 404
    assert seen == []


def test_report_revalidates_saved_compile_and_source_instead_of_trusting_flags(setup, monkeypatch):
    client, op, path, _ = setup
    plan(op)
    saved(op, path)
    monkeypatch.setattr(publication, 'verified_manifest', lambda value: (path.parent/'manifest.json', {}))
    output = path.parent/'compiled.mdb'
    output.write_bytes(b'compiled')
    core.save('user', 'delivery', '1'*32, 0, {'plan_id': 'f'*32, 'action': 'compile', 'status': 'completed',
        'expected_sha256': op['source_sha256'], 'result': {'source_unchanged': True,
            'input_sha256': op['source_sha256'], 'compiled_mdb': str(output), 'compiled_sha256': sha256(output.read_bytes()).hexdigest()}})
    first = report(client)
    assert first['save_package_verified'] and first['current_matches_saved']
    assert next(s for s in first['stages'] if s['id'] == 'compile')['status'] == 'verified'
    assert not first['ready_for_release']
    output.write_bytes(b'tampered')
    assert report(client)['overall'] == 'needs_review'
    path.write_bytes(b'new-work')
    assert not report(client)['current_matches_saved']


def test_report_filters_owned_project_source_and_omits_business_input(setup, monkeypatch):
    client, op, path, _ = setup
    plan(op)
    saved(op, path)
    monkeypatch.setattr(publication, 'verified_manifest', lambda value: (path.parent/'manifest.json', {}))
    receipt(op)
    receipt({**op, 'project_id': '9'*32}, identity='2'*32)
    receipt({**op, 'source_sha256': '0'*64}, identity='3'*32)
    result = report(client)
    assert result['test_scope']['total'] == 1 and len(result['test_evidence']) == 1
    assert result['test_evidence'][0]['deployment_source'] == 'unknown'
    assert not result['ready_for_release']
    exported = client.get('/api/clf/workspace/plans/'+'f'*32+'/report/export?username=user')
    assert 'attachment' in exported.headers['content-disposition']
    assert 'business_input' not in exported.text and '"data"' not in exported.text
    core.save('user', 'delivery', '4'*32, 0, {'plan_id': 'f'*32, 'action': 'restore', 'status': 'completed'})
    assert next(s for s in report(client)['stages'] if s['id'] == 'save')['status'] == 'restored'
