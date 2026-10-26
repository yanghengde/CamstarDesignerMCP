"""Local workbench APIs. Test execution never uses the language-model tool loop."""
import asyncio
from hashlib import sha256
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

import config
from designer import clf, working, testing, wcf_simple
from designer import testing_runtime as native, testing_store as store
from designer.testing_models import Environment, RunRequest, CaseRequest, BatchRequest, TestSpec
from web.routes import require_progress_owner

router = APIRouter()


async def call(function, *args, **kwargs):
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(409, native.redact(str(exc))) from None


@router.get('/wcf-test')
def wcf_page():
    return FileResponse('static/wcf-test.html', headers={'Cache-Control': 'no-store'})


@router.get('/wcf-simple.js')
def wcf_script():
    return FileResponse('static/wcf-simple.js', media_type='text/javascript')


@router.get('/wcf-simple.css')
def wcf_style():
    return FileResponse('static/wcf-simple.css', media_type='text/css')


@router.get('/wcf-test/settings')
@router.get('/clf-test')
def testing_page():
    return FileResponse('static/testing.html', headers={'Cache-Control': 'no-store'})


@router.get('/testing.js')
def testing_script():
    return FileResponse('static/testing.js', media_type='text/javascript', headers={'Cache-Control': 'no-cache'})


@router.get('/clf-testing-panel')
def testing_panel():
    return FileResponse('static/clf-testing-panel.html', media_type='text/html', headers={'Cache-Control': 'no-store'})


@router.get('/workspace-navigation.js')
def navigation_script():
    return FileResponse('static/workspace-navigation.js', media_type='text/javascript', headers={'Cache-Control': 'no-cache'})


@router.get('/chat-presentation.js')
def chat_presentation_script():
    return FileResponse('static/chat-presentation.js', media_type='text/javascript', headers={'Cache-Control': 'no-cache'})


@router.get('/chat-presentation.css')
def chat_presentation_style():
    return FileResponse('static/chat-presentation.css', media_type='text/css', headers={'Cache-Control': 'no-cache'})


@router.get('/testing.css')
def testing_style():
    return FileResponse('static/testing.css', media_type='text/css', headers={'Cache-Control': 'no-cache'})


@router.get('/api/testing/bootstrap')
async def bootstrap(username: str, session_id: str | None = None):
    require_progress_owner(username, session_id)
    projects, artifacts, profiles, cases, history = await asyncio.gather(
        call(working.available), call(native.artifacts), call(store.items, username, 'environment'),
        call(store.items, username, 'case'), call(store.history, username))
    context = {}
    if session_id:
        from agent.assistant_modes import session
        from designer.progress import snapshot
        context = dict(session(username, session_id).get('clf_context', {}))
        if not context.get('project_id'):
            progress = await call(snapshot, username, session_id)
            context['project_id'] = ((progress.get('package') or {}).get('working') or {}).get('id')
            receipt = progress.get('wcf') or {}
            if receipt.get('result_file'):
                from pathlib import Path
                context['artifact_id'] = Path(receipt['result_file']).parent.name
    default_profile = None
    for profile in profiles:
        try:
            if profile.get('protected_password') and config.DESIGNER_WCF_ADDRESS and native.endpoint(profile['endpoint']) == native.endpoint(config.DESIGNER_WCF_ADDRESS):
                default_profile = profile
                break
        except ValueError:
            continue
    defaults = {'endpoint': config.DESIGNER_WCF_ADDRESS, 'timeout': config.DESIGNER_TEST_TIMEOUT,
                'environment_id': (default_profile or {}).get('id', ''),
                'project_id': (default_profile or {}).get('project_id', ''),
                'artifact_id': (default_profile or {}).get('artifact_id', '')}
    return {'projects': projects, 'artifacts': artifacts, 'environments': [store.public_environment(p) for p in profiles],
            'cases': cases, 'history': history, 'context': {key: context.get(key) for key in ('project_id', 'clf_name', 'artifact_id')},
            'defaults': defaults,
            'write_target_confirmed': config.DESIGNER_TEST_TARGET_CONFIRMED}


@router.get('/api/testing/simple/bootstrap')
async def simple_bootstrap(username: str):
    require_progress_owner(username)
    return await call(wcf_simple.bootstrap, username)


@router.get('/api/testing/simple/schema/{service}')
async def simple_schema(service: str, username: str):
    require_progress_owner(username)
    return await call(wcf_simple.view_schema, username, service)


@router.post('/api/testing/simple/runs', status_code=202)
async def simple_run(req: wcf_simple.ActionRequest):
    require_progress_owner(req.username)
    spec = await call(wcf_simple.build_spec, req.username, req)
    try:
        return await testing.start(req.username, req.request_id, spec)
    except ValueError as exc:
        raise HTTPException(409, native.redact(str(exc))) from None


class EnvironmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str
    environment: Environment


@router.post('/api/testing/environments')
async def create_environment(req: EnvironmentRequest):
    require_progress_owner(req.username)
    return await call(testing.save_environment, req.username, req.environment)


@router.put('/api/testing/environments/{identity}')
async def update_environment(identity: str, req: EnvironmentRequest):
    require_progress_owner(req.username)
    await call(store.item, req.username, 'environment', identity)
    return await call(testing.save_environment, req.username, req.environment, identity)


class CheckEnvironment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str
    artifact_id: str


@router.post('/api/testing/environments/{identity}/check')
async def check_environment(identity: str, req: CheckEnvironment):
    require_progress_owner(req.username)
    profile = await call(testing.environment, req.username, identity)
    item = await call(native.artifact, req.artifact_id)
    result = await call(native.invoke, item, 'login', profile)
    return {**result, 'client_sha256': item['client_sha256'], 'environment_version': profile['version'],
            'deployment_version_verified': False,
            'notice': '业务登录结果不代表生成的程序集已部署或 CLF 已运行。'}


@router.get('/api/testing/project/{identity}')
async def project_context(identity: str, username: str):
    require_progress_owner(username)
    return await call(testing.project, identity)


@router.get('/api/testing/catalog/{identity}')
async def catalog(identity: str, username: str):
    require_progress_owner(username)
    item = await call(native.artifact, identity)
    result = await call(native.invoke, item, 'catalog')
    return {**result, 'client_sha256': item['client_sha256'], 'compiled_sha256': item['compiled_sha256']}


@router.get('/api/testing/catalog/{identity}/{service}')
async def schema(identity: str, service: str, username: str):
    require_progress_owner(username)
    item = await call(native.artifact, identity)
    result = await call(native.invoke, item, 'schema', service=service)
    return {**result, 'client_sha256': item['client_sha256']}


@router.get('/api/testing/clfs/{identity}')
async def clfs(identity: str, username: str, search: str = '', offset: int = Query(default=0, ge=0, le=10000)):
    require_progress_owner(username)
    project = await call(testing.project, identity)
    return await call(clf.list_clfs, project['mdb_file'], search, offset, 50)


@router.get('/api/testing/clfs/{identity}/detail')
async def clf_detail(identity: str, username: str, name: str, source_sha256: str):
    require_progress_owner(username)
    project = await call(testing.project, identity, source_sha256)
    return await call(clf.get_clf, project['mdb_file'], name, source_sha256)


@router.post('/api/testing/cases')
async def save_case(req: CaseRequest):
    require_progress_owner(req.username)
    await call(testing.project, req.spec.project_id, req.spec.source_sha256)
    await call(store.item, req.username, 'environment', req.spec.environment_id)
    revision = 1
    if req.parent_run_id:
        parent = await call(store.run, req.username, req.parent_run_id)
        if parent['spec']['project_id'] != req.spec.project_id:
            raise HTTPException(409, '来源运行不属于当前项目')
    if req.parent_case_id:
        parent = await call(store.item, req.username, 'case', req.parent_case_id)
        if parent['spec']['project_id'] != req.spec.project_id:
            raise HTTPException(409, '来源用例不属于当前项目')
        revision = parent.get('revision', 1) + 1
    return await call(store.save, req.username, 'case', {'name': req.name, 'schema_version': 1, 'revision': revision,
        'group': req.group, 'parent_run_id': req.parent_run_id, 'parent_case_id': req.parent_case_id,
        'spec': native.redact(req.spec.model_copy(update={'allow_write': False}).model_dump())})


@router.delete('/api/testing/cases/{identity}')
async def delete_case(identity: str, username: str):
    require_progress_owner(username)
    await call(store.item, username, 'case', identity)
    children = await call(store.items, username, 'case')
    if any(c.get('parent_case_id') == identity for c in children):
        raise HTTPException(409, '该用例仍被后续修订引用，请保留来源用例')
    await call(store.delete, username, 'case', identity)
    return {'deleted': True}


@router.post('/api/testing/runs', status_code=202)
async def start_run(req: RunRequest):
    require_progress_owner(req.username)
    try:
        return await testing.start(req.username, req.request_id, req.spec)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post('/api/testing/batches', status_code=202)
async def start_batch(req: BatchRequest):
    require_progress_owner(req.username)
    # Resolve every case before starting any operation, including ownership and model validation.
    cases = [await call(store.item, req.username, 'case', identity) for identity in dict.fromkeys(req.case_ids)]
    try:
        specs = [TestSpec.model_validate({**case['spec'], 'allow_write': req.allow_write}) for case in cases]
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, '保存的用例格式需要重新核对：' + native.redact(str(exc))) from None
    runs = []
    for case, spec in zip(cases, specs):
        identity = sha256((req.request_id + case['id']).encode()).hexdigest()[:32]
        try:
            runs.append(await testing.start(req.username, identity, spec))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
    return {'runs': runs}


@router.get('/api/testing/runs')
async def run_history(username: str):
    require_progress_owner(username)
    return {'runs': await call(store.history, username)}


@router.get('/api/testing/runs/{identity}')
async def run_detail(identity: str, username: str):
    require_progress_owner(username)
    return await call(store.run, username, identity)


@router.get('/api/testing/runs/{identity}/export')
async def export_run(identity: str, username: str):
    require_progress_owner(username)
    record = await call(store.run, username, identity)
    return JSONResponse(record, headers={'Content-Disposition': 'attachment; filename="test-' + record['id'] + '.json"'})
