"""Explicit UI-only CLF implementation, using the existing official save pipeline.

This module is not an MCP tool. It never publishes a database or deploys services.
"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from designer import clf_workspace as core, testing, vendor, working
from designer import testing_runtime as native, testing_store as store

_tasks = set()


class DeliveryRequest(core.Strict):
    username: str
    plan_id: str = Field(pattern=core.ID)
    request_id: str = Field(pattern=core.ID)
    action: Literal['save', 'compile', 'check_publish', 'restore']
    expected_sha256: str = Field(pattern=core.SHA)
    reviewed: bool = False


def claim(owner, request):
    # Save is unique per reviewed plan, including across different browser request IDs.
    identity = request.plan_id if request.action == 'save' else request.request_id
    fingerprint = sha256(json.dumps(request.model_dump(exclude={'username', 'request_id'}), sort_keys=True).encode()).hexdigest()
    with store.connection() as conn:
        core._schema(conn)
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT document FROM clf_workspace_items WHERE owner=? AND kind=? AND id=?',
                           (owner, 'delivery', identity)).fetchone()
        if row:
            existing = json.loads(row[0])
            if existing['fingerprint'] != fingerprint:
                raise ValueError('该请求或方案已用于另一项实施操作，请重新核对')
            return existing, False
        record = {'schema_version': 1, 'id': identity, 'revision': 1, 'request_id': request.request_id,
                  'plan_id': request.plan_id, 'action': request.action, 'expected_sha256': request.expected_sha256,
                  'fingerprint': fingerprint, 'status': 'queued', 'created_utc': store.now(),
                  'updated_utc': store.now(), 'database_published': False, 'result': None}
        conn.execute('INSERT INTO clf_workspace_items VALUES (?,?,?,?,?)',
                     (owner, 'delivery', identity, 1, json.dumps(record, ensure_ascii=False)))
        return record, True


def update(owner, identity, **values):
    current = core.item(owner, 'delivery', identity)
    return core.save(owner, 'delivery', identity, current['revision'], {**current, **values})


def saved(owner, plan_id):
    record = core.item(owner, 'delivery', plan_id)
    if record['action'] != 'save' or record['status'] != 'saved':
        raise ValueError('请先完成该方案的官方保存与回读；结果不明确时应先核对，不重复保存')
    return record


async def execute(owner, request):
    identity = request.plan_id if request.action == 'save' else request.request_id
    source = None
    try:
        plan = core.item(owner, 'plan', request.plan_id)
        result = plan['result']
        project = testing.project(plan['project_id'], request.expected_sha256)
        source = Path(project['mdb_file'])
        if request.action == 'save':
            if not result.get('metadata_preflight_passed') or result.get('source_sha256') != request.expected_sha256:
                raise ValueError('方案未通过预检或来源不一致，请重新检查方案')
            from tools.designer_clf import _operation
            operations = [_operation.validate_python(op).model_dump(exclude_unset=True) for op in result['operations']]
            update(owner, identity, status='saving')
            def perform():
                with working.project_lock(plan['project_id']):
                    output = vendor.design(str(source), request.expected_sha256, result.get('workspace', ''), operations, sync_designer=False)
                    if not output.get('execution', {}).get('reload_verified'):
                        raise ValueError('官方保存未提供完整回读证据，请核对项目文件与实施回执')
                    return {**output, 'saved_sha256': vendor.digest(source), 'published': False,
                            'notice': '已通过官方模型保存和回读当前工作 MDB；未发布数据库或部署服务。'}
            output = await asyncio.to_thread(perform)
            update(owner, identity, status='saved', result=output, finished_utc=store.now())
        else:
            receipt = saved(owner, request.plan_id)
            if receipt['result']['saved_sha256'] != request.expected_sha256:
                raise ValueError('实施来源已变化，请使用保存回执中的版本')
            if request.action == 'restore':
                update(owner, identity, status='restoring')
                from designer import publication
                def restore():
                    with working.project_lock(plan['project_id']):
                        manifest, _ = publication.verified_manifest(receipt['result']['files']['manifest.json'])
                        output = vendor.adopt_candidate(str(source), request.expected_sha256, manifest.parent / 'baseline.mdb',
                                                        method='restore_clf_snapshot')
                        return {**output, 'restored_sha256': vendor.digest(source), 'database_published': False,
                                'notice': '已恢复本次保存前的工作 MDB 快照；历史回执保留。未回滚数据库或服务器服务。'}
                output = await asyncio.to_thread(restore)
            elif request.action == 'compile':
                update(owner, identity, status='compiling')
                from tools.designer_design import compile_designer_mdb
                output = await compile_designer_mdb(str(source), request.expected_sha256)
                if not output.get('source_unchanged'):
                    raise ValueError('编译期间工作源发生变化，请重新核对')
                output['input_sha256'] = request.expected_sha256
            else:
                compiles = [r for r in core.items(owner, 'delivery') if r['plan_id'] == request.plan_id
                            and r['action'] == 'compile' and r['status'] == 'completed'
                            and r['expected_sha256'] == request.expected_sha256]
                if not compiles:
                    raise ValueError('请先完成当前保存版本的官方编译')
                from designer.files import source_path
                compiled = compiles[0]['result']
                if vendor.digest(source_path(compiled['compiled_mdb'], '.mdb')) != compiled['compiled_sha256']:
                    raise ValueError('编译产物已变化，请重新编译')
                update(owner, identity, status='checking_publish')
                from designer.cycle import prepare_plan
                output = await asyncio.to_thread(prepare_plan, receipt['result']['files']['manifest.json'])
                output = {**output, 'scope': 'whole_current_project_cycle', 'database_published': False,
                          'notice': '这是整个当前项目周期的发布检查，包含其他已保存改动；仅检查，未执行数据库发布。'}
            update(owner, identity, status='completed', result=output, finished_utc=store.now())
    except Exception as exc:
        try:
            changed = bool(source and (not source.exists() or vendor.digest(source) != request.expected_sha256))
        except OSError:
            changed = request.action in {'save', 'restore'}
        update(owner, identity, status='needs_review' if changed and request.action in {'save', 'restore'} else 'failed',
               error=native.redact(str(exc)), source_may_have_changed=changed,
               notice='先核对工作源和已有回执；不要重复提交可能已经完成的保存。' if changed else '操作未完成，请核对错误。保存失败后需要重新预检形成新方案。',
               finished_utc=store.now())


async def start(owner, request):
    if not request.reviewed:
        raise ValueError('请先核对方案差异、目标工作项目与操作范围')
    plan = await asyncio.to_thread(core.item, owner, 'plan', request.plan_id)
    if request.action == 'save' and (not plan['result'].get('metadata_preflight_passed') or
                                   plan['result'].get('source_sha256') != request.expected_sha256):
        raise ValueError('方案尚未通过当前来源的官方预检')
    if request.action != 'save':
        await asyncio.to_thread(saved, owner, request.plan_id)
    record, created = await asyncio.to_thread(claim, owner, request)
    if created:
        task = asyncio.create_task(execute(owner, request))
        _tasks.add(task)
        def finished(done):
            _tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
    return record


def recover():
    # No automatic replay after process loss: the MDB may already have been committed.
    with store.connection() as conn:
        core._schema(conn)
        rows = conn.execute("SELECT owner,id,document FROM clf_workspace_items WHERE kind='delivery'").fetchall()
        for row in rows:
            document = json.loads(row['document'])
            if document['status'] in {'queued', 'saving', 'compiling', 'checking_publish', 'restoring'}:
                document.update(status='interrupted', notice='服务中断，操作结果需要核对；不会自动重新保存。',
                                revision=document['revision'] + 1, updated_utc=store.now())
                conn.execute("UPDATE clf_workspace_items SET revision=?,document=? WHERE owner=? AND kind='delivery' AND id=?",
                             (document['revision'], json.dumps(document, ensure_ascii=False), row['owner'], row['id']))


async def drain():
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
