"""CLF workspace endpoints, using the same owner checks and working project."""
import asyncio
import json
from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from agent import assistant_modes, memory
from designer import clf, working
from designer.attachments import resolve_attachments, AttachmentError
from web.routes import require_progress_owner

router = APIRouter()


def require_clf(username, session_id):
    require_progress_owner(username, session_id)
    if assistant_modes.mode(username, session_id) != 'clf':
        raise HTTPException(409, '请在 CLF 助手中创建或打开专用对话')


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str
    session_id: str
    project_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    clf_name: str = Field(default='', max_length=200)
    object_name: str = Field(default='', max_length=200)


@router.get('/clf-workspace.js')
def workspace_script():
    return FileResponse('static/clf-workspace.js', media_type='text/javascript', headers={'Cache-Control': 'no-cache'})


@router.get('/clf-workspace.css')
def workspace_style():
    return FileResponse('static/clf-workspace.css', media_type='text/css', headers={'Cache-Control': 'no-cache'})


@router.get('/api/clf/projects')
async def projects(username: str, session_id: str):
    require_clf(username, session_id)
    return {'projects': await asyncio.to_thread(working.available)}


@router.get('/api/clf/context')
async def current_context(username: str, session_id: str):
    require_clf(username, session_id)
    try:
        return await asyncio.to_thread(clf.session_context, username, session_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put('/api/clf/context')
async def select_context(req: ContextRequest):
    require_clf(req.username, req.session_id)
    from agent import chat_jobs
    from designer import progress_store
    if chat_jobs.running(req.username, req.session_id) or progress_store.background_running(req.username, req.session_id):
        raise HTTPException(409, '当前对话正在分析，请完成后再切换上下文')
    project = await asyncio.to_thread(working.summary, req.project_id)
    if not project:
        raise HTTPException(404, '工作项目不存在')
    session = assistant_modes.session(req.username, req.session_id)
    previous = session.get('clf_context', {})
    session['clf_context'] = {**previous, 'project_id': req.project_id, 'clf_name': req.clf_name,
                              'object_name': req.object_name if 'object_name' in req.model_fields_set else
                              previous.get('object_name', '') if previous.get('project_id') == req.project_id else ''}
    memory.save_session(req.username, req.session_id)
    return await asyncio.to_thread(clf.session_context, req.username, req.session_id)


def selected_project(username, session_id):
    require_clf(username, session_id)
    project_id = assistant_modes.session(username, session_id).get('clf_context', {}).get('project_id')
    project = working.summary(project_id) if project_id else None
    if not project:
        raise ValueError('请先选择项目工作 MDB')
    return project


@router.get('/api/clf/list')
async def search_clfs(username: str, session_id: str, search: str = '', offset: int = 0):
    try:
        project = await asyncio.to_thread(selected_project, username, session_id)
        return await asyncio.to_thread(clf.list_clfs, project['mdb_file'], search, offset, 50)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/api/clf/detail')
async def detail(username: str, session_id: str, name: str, expected_sha256: str):
    try:
        project = await asyncio.to_thread(selected_project, username, session_id)
        return await asyncio.to_thread(clf.get_clf, project['mdb_file'], name, expected_sha256)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/api/clf/objects')
async def rule_objects(username: str, session_id: str, search: str = '', selected: str = ''):
    try:
        project = await asyncio.to_thread(selected_project, username, session_id)
        return await asyncio.to_thread(clf.rule_objects, project['mdb_file'], search, selected)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/api/clf/rules')
async def rule_list(username: str, session_id: str, object_name: str = '', scope: str = 'related', search: str = '', offset: int = 0):
    try:
        project = await asyncio.to_thread(selected_project, username, session_id)
        return await asyncio.to_thread(clf.related_rules, project['mdb_file'], object_name, scope, search, offset)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str
    source_session_id: str
    attachment_ids: list[str] = Field(min_length=1, max_length=3)


@router.post('/api/clf/from-excel')
async def handoff(req: HandoffRequest):
    require_progress_owner(req.username, req.source_session_id)
    try:
        documents = await asyncio.to_thread(resolve_attachments, req.attachment_ids, req.username, req.source_session_id)
    except AttachmentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    from designer.excel_rules import extract
    rules = [rule for document in documents for rule in extract(document)]
    if not rules:
        raise HTTPException(409, '未识别到非空规则列；可使用“业务规则”“CLF要求”或“校验规则”表头')
    source_session = assistant_modes.session(req.username, req.source_session_id)
    attachments = [{'id': doc['id'], 'sha256': doc['sha256'], 'name': doc['name']} for doc in documents]
    projects = await asyncio.to_thread(working.available)
    key = sha256(json.dumps(attachments, sort_keys=True).encode()).hexdigest()
    old = source_session.get('clf_handoffs', {}).get(key)
    if old and assistant_modes.mode(req.username, old) == 'clf' and assistant_modes.session(req.username, old):
        return {'session_id': old, 'url': f'/clf?session_id={old}', 'reused': True}
    sid = memory.create_session(req.username, mode='clf')
    session = assistant_modes.session(req.username, sid)
    context = {'excel_handoff': {'source_session_id': req.source_session_id, 'attachments': attachments, 'rules': rules}}
    if len(projects) == 1:
        context['project_id'] = projects[0]['id']
    session['clf_context'] = context
    session['title'] = f'Excel 规则 · {documents[0]["name"]}'[:60]
    memory.save_session(req.username, sid)
    source_session.setdefault('clf_handoffs', {})[key] = sid
    memory.save_session(req.username, req.source_session_id)
    return {'session_id': sid, 'url': f'/clf?session_id={sid}', 'rule_count': len(rules)}
