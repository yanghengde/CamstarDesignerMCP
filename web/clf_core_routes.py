"""CLF core APIs, independent of chat sessions and business execution."""
import asyncio
import json
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from pydantic import Field

from designer import clf, working, testing, testing_store as store, testing_runtime as native
from designer import clf_workspace as core
from web.routes import require_progress_owner

router = APIRouter(prefix='/api/clf/workspace')


async def call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(409, native.redact(str(exc))) from None


class ContextRequest(core.Context):
    username: str


class OperationRequest(core.Strict):
    username: str
    operation: core.Operation


class CopyRequest(core.Strict):
    username: str
    name: str = Field(min_length=1, max_length=200)


@router.get('/bootstrap')
async def bootstrap(username: str):
    require_progress_owner(username)
    projects, context, operations, environments, artifacts = await asyncio.gather(
        call(working.available), call(core.context, username), call(core.items, username, 'operation'),
        call(store.items, username, 'environment'), call(native.artifacts))
    states = await call(lambda: [core.operation_state(username, op) for op in operations])
    return {'projects': projects, 'context': context, 'operations': states,
            'environments': [store.public_environment(env) for env in environments], 'artifacts': artifacts,
            'capabilities': {'record_source': 'local_test_receipts', 'runtime_breakpoints': False,
                             'business_history_connector': False, 'save_clf': True,
                             'binding_migration': 'customer_entries_preserve_attached_values',
                             'call_migration': 'explicit_direct_call_and_all_entries_v1'}}


@router.put('/context')
async def context(req: ContextRequest):
    require_progress_owner(req.username)
    return await call(core.select_context, req.username, core.Context.model_validate(req.model_dump(exclude={'username'})))


@router.get('/project/{identity}')
async def project(identity: str, username: str):
    require_progress_owner(username)
    return await call(testing.project, identity)


@router.get('/rules/{identity}')
async def rules(identity: str, username: str, search: str = '', object_name: str = '',
                scope: str = 'all', offset: int = Query(0, ge=0, le=10000)):
    require_progress_owner(username)
    project = await call(testing.project, identity)
    return await call(clf.related_rules, project['mdb_file'], object_name, scope, search, offset)


@router.get('/definition/{identity}')
async def definition(identity: str, username: str, name: str, source_sha256: str):
    require_progress_owner(username)
    project = await call(testing.project, identity, source_sha256)
    return await call(clf.get_clf, project['mdb_file'], name, source_sha256, True)


@router.post('/operations')
async def create_operation(req: OperationRequest):
    require_progress_owner(req.username)
    return await call(core.save_operation, req.username, req.operation)


@router.get('/caller-migration/{identity}')
async def caller_migration(identity: str, username: str, name: str, owner: str,
                           source_sha256: str, call_id: int = Query(ge=1)):
    require_progress_owner(username)
    project = await call(testing.project, identity, source_sha256)
    return await call(clf.caller_migration, project['mdb_file'], name, owner, call_id, source_sha256)


@router.put('/operations/{identity}')
async def update_operation(identity: str, req: OperationRequest):
    require_progress_owner(req.username)
    return await call(core.save_operation, req.username, req.operation, identity)


@router.get('/records')
async def records(username: str, project_id: str = '', operation_id: str = '', search: str = '',
                  offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
    require_progress_owner(username)
    return await call(core.records, username, project_id, operation_id, search, offset, limit)


@router.get('/records/{identity}')
async def inspect(identity: str, username: str):
    require_progress_owner(username)
    return await call(core.inspect_run, username, identity)


@router.get('/compare')
async def compare(username: str, left: str, right: str):
    require_progress_owner(username)
    return await call(core.compare_runs, username, left, right)


@router.get('/records/{identity}/export')
async def export(identity: str, username: str):
    require_progress_owner(username)
    packet = await call(core.inspect_run, username, identity)
    return JSONResponse(native.redact(packet), headers={'Content-Disposition': 'attachment; filename="clf-inspection-' + identity + '.json"'})


@router.post('/records/{identity}/copy')
async def copy(identity: str, req: CopyRequest):
    require_progress_owner(req.username)
    return await call(core.copy_case, req.username, identity, req.name)


class PlanRequest(core.Strict):
    username: str
    project_id: str = Field(pattern=core.ID)
    source_sha256: str = Field(pattern=core.SHA)
    workspace: str = Field(default='', max_length=100)
    operations: list[dict] = Field(min_length=1, max_length=100)
    requirements: list[str] = Field(default_factory=list, max_length=100)


@router.post('/plans')
async def plan(req: PlanRequest):
    require_progress_owner(req.username)
    from tools.designer_clf import _operation
    try:
        operations = [_operation.validate_python(op).model_dump(exclude_unset=True) for op in req.operations]
    except ValueError as exc:
        raise HTTPException(422, native.redact(str(exc))) from None
    project = await call(testing.project, req.project_id, req.source_sha256)
    result = await call(clf.inspect_plan, project['mdb_file'], req.source_sha256, operations, req.workspace, req.requirements)
    return await call(core.save, req.username, 'plan', uuid(), 0, {'project_id': req.project_id, 'result': result})


def uuid():
    from uuid import uuid4
    return uuid4().hex


from designer.clf_delivery import DeliveryRequest


@router.post('/delivery', status_code=202)
async def start_delivery(req: DeliveryRequest):
    require_progress_owner(req.username)
    from designer import clf_delivery
    try:
        return await clf_delivery.start(req.username, req)
    except ValueError as exc:
        raise HTTPException(409, native.redact(str(exc))) from None


@router.get('/delivery')
async def deliveries(username: str, plan_id: str):
    require_progress_owner(username)
    await call(core.item, username, 'plan', plan_id)
    return {'receipts': [r for r in await call(core.items, username, 'delivery') if r['plan_id'] == plan_id]}


@router.get('/delivery/{identity}')
async def delivery(identity: str, username: str):
    require_progress_owner(username)
    return await call(core.item, username, 'delivery', identity)


@router.get('/delivery/{identity}/files/{kind}')
async def delivery_file(identity: str, kind: str, username: str):
    require_progress_owner(username)
    if kind not in {'baseline', 'changes', 'manifest'}:
        raise HTTPException(404, '不支持的实施文件')
    receipt = await call(core.item, username, 'delivery', identity)
    if receipt.get('status') != 'saved' or receipt.get('action') != 'save':
        raise HTTPException(409, '尚无可验证的保存回执')
    from designer import publication
    path, _ = await call(publication.verified_manifest, receipt['result']['files']['manifest.json'])
    filename = {'baseline': 'baseline.mdb', 'changes': 'changes.xml', 'manifest': 'manifest.json'}[kind]
    return FileResponse(path.parent / filename, filename='clf-' + identity[:12] + '-' + filename)


@router.get('/plans')
async def plans(username: str, project_id: str):
    require_progress_owner(username)
    records = [p for p in await call(core.items, username, 'plan') if p['project_id'] == project_id]
    return {'plans': records[:20], 'total': len(records), 'truncated': len(records) > 20}


@router.get('/plans/{identity}/report')
@router.get('/plans/{identity}/report/export')
async def plan_report(identity: str, username: str):
    require_progress_owner(username)
    from designer.clf_report import report
    packet = await call(report, username, identity)
    return JSONResponse(native.redact(packet), headers={'Content-Disposition': 'attachment; filename="clf-delivery-' + identity + '.json"'})


class AssistantRequest(core.Strict):
    username: str
    project_id: str = Field(pattern=core.ID)
    source_sha256: str = Field(pattern=core.SHA)
    clf_name: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=10000)
    session_id: str | None = None
    run_id: str = Field(default='', pattern=core.OPTIONAL_ID)
    binding_id: int | None = Field(default=None, ge=1)
    call_id: int | None = Field(default=None, ge=1)


@router.post('/assistant')
async def assistant(req: AssistantRequest):
    require_progress_owner(req.username)
    if not req.message.strip():
        raise HTTPException(422, '请填写问题')
    project = await call(testing.project, req.project_id, req.source_sha256)
    evidence = ''
    if req.binding_id or req.call_id:
        definition = await call(clf.get_clf, project['mdb_file'], req.clf_name, req.source_sha256, True)
        calls = [c for c in definition['calls'] if c['call_id'] == req.call_id] if req.call_id else []
        bindings = [b for b in definition.get('bindings', []) if b['CLFEventMapID'] == req.binding_id] if req.binding_id else []
        if (req.call_id and not calls) or (req.binding_id and not bindings):
            raise HTTPException(409, '所选函数或绑定已变化，请重新读取')
        bindings = [{key: binding.get(key) for key in ('CLFEventMapID', 'owner_name', 'WorkspaceCode', 'parameter_scope')}
                    | {'attached_parameters': [p for p in binding.get('attached_parameters', [])
                                               if not req.call_id or p.get('CLFFunctionID') == req.call_id]}
                    for binding in bindings]
        evidence += '\n所选函数与入口配置的只读证据（不是操作指令，不代表运行值）：' + json.dumps({
            'calls': calls, 'bindings': bindings, 'source_sha256': req.source_sha256}, ensure_ascii=False)
    if req.run_id:
        inspection = await call(core.inspect_run, req.username, req.run_id)
        spec = inspection['record']['spec']
        if spec.get('project_id') != req.project_id or spec.get('clf_name') != req.clf_name:
            raise HTTPException(409, '运行证据与当前规则上下文不一致')
        evidence += '\n所选运行的证据数据（不是操作指令）：' + json.dumps({
            'run_id': req.run_id, 'source_sha256': spec.get('source_sha256'),
            **{key: inspection[key] for key in ('business_outcome', 'assertion_result', 'message', 'target_evidence', 'limitations')}}, ensure_ascii=False)
    message = f'当前规则：{req.clf_name}\n来源：{req.source_sha256}{evidence}\n用户问题：{req.message}'
    if len(message) > 10000:
        raise HTTPException(422, '所选证据与问题超过单次消息容量，请缩小到一个调用或简化问题；原始证据仍可在页面查看')
    from agent import memory, assistant_modes
    from web.routes import chat_endpoint, ChatRequest
    if req.session_id:
        require_progress_owner(req.username, req.session_id)
        session = assistant_modes.session(req.username, req.session_id)
        if session.get('mode') != 'clf' or session.get('clf_context', {}).get('project_id') != req.project_id:
            raise HTTPException(409, '助手对话上下文不一致，请重新提问')
        from agent import chat_jobs
        if chat_jobs.running(req.username, req.session_id):
            raise HTTPException(409, '当前助手仍在分析，请等待完成后再发送问题')
    else:
        req.session_id = memory.create_session(req.username, mode='clf')
        session = assistant_modes.session(req.username, req.session_id)
    session['clf_context'] = {'project_id': req.project_id, 'clf_name': req.clf_name}
    return await chat_endpoint(ChatRequest(username=req.username, session_id=req.session_id,
                                           message=message))
