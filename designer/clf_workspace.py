"""Conversation-independent CLF workspace and read-only business inspections.

The existing testing executor remains the sole business execution boundary.
Neither inspecting a receipt nor copying it into a case invokes a service.
"""
from copy import deepcopy
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from designer import clf, working, testing, testing_runtime as native, testing_store as store
from designer.testing_models import TestSpec

ID = r'^[0-9a-f]{32}$'
OPTIONAL_ID = r'^(?:[0-9a-f]{32})?$'
SHA = r'^[0-9a-f]{64}$'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Context(Strict):
    revision: int = Field(default=0, ge=0)
    project_id: str = Field(default='', pattern=OPTIONAL_ID)
    mode: Literal['business', 'engineer'] = 'business'
    view: Literal['logic', 'runs', 'plan'] = 'logic'
    clf_name: str = Field(default='', max_length=200)
    object_name: str = Field(default='', max_length=200)
    operation_id: str = Field(default='', pattern=OPTIONAL_ID)
    run_id: str = Field(default='', pattern=OPTIONAL_ID)
    binding_id: int | None = Field(default=None, ge=1)
    call_id: int | None = Field(default=None, ge=1)
    source_sha256: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')


class Operation(Strict):
    revision: int = Field(default=0, ge=0)
    name: str = Field(min_length=1, max_length=120)
    project_id: str = Field(pattern=ID)
    source_sha256: str = Field(pattern=SHA)
    environment_id: str = Field(pattern=ID)
    environment_version: int = Field(ge=1)
    artifact_id: str = Field(pattern=ID)
    client_sha256: str = Field(pattern=SHA)
    service: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=200)
    method: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=200)
    clf_names: list[str] = Field(min_length=1, max_length=10)
    status: Literal['draft', 'active', 'disabled'] = 'draft'


def connection():
    # Additive schema: existing environments, cases and immutable runs are untouched.
    manager = store.connection()
    return manager


def _schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS clf_workspace_items (
      owner TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
      revision INTEGER NOT NULL, document TEXT NOT NULL,
      PRIMARY KEY(owner,kind,id))''')


def items(owner, kind):
    with connection() as conn:
        _schema(conn)
        result = [json.loads(row[0]) for row in conn.execute(
            'SELECT document FROM clf_workspace_items WHERE owner=? AND kind=? ORDER BY id', (owner, kind))]
        return sorted(result, key=lambda row: row.get('updated_utc', ''), reverse=True)


def item(owner, kind, identity):
    with connection() as conn:
        _schema(conn)
        row = conn.execute('SELECT document FROM clf_workspace_items WHERE owner=? AND kind=? AND id=?',
                           (owner, kind, identity)).fetchone()
    if not row:
        raise ValueError('工作区记录不存在或不属于当前用户')
    return json.loads(row[0])


def save(owner, kind, identity, revision, payload):
    with connection() as conn:
        _schema(conn)
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT revision FROM clf_workspace_items WHERE owner=? AND kind=? AND id=?',
                           (owner, kind, identity)).fetchone()
        if revision != (row[0] if row else 0):
            raise ValueError('工作区内容已在其他页面更新，请刷新后重新核对')
        document = {**native.redact(payload), 'schema_version': 1, 'id': identity,
                    'revision': revision + 1, 'updated_utc': store.now()}
        conn.execute('INSERT INTO clf_workspace_items VALUES (?,?,?,?,?) ON CONFLICT(owner,kind,id) '
                     'DO UPDATE SET revision=excluded.revision,document=excluded.document',
                     (owner, kind, identity, revision + 1, json.dumps(document, ensure_ascii=False)))
    return document


def context(owner):
    found = items(owner, 'context')
    return found[0] if found else {**Context().model_dump(), 'id': 'current', 'schema_version': 1}


def select_context(owner, request: Context):
    data = request.model_dump()
    if request.project_id and not working.summary(request.project_id):
        raise ValueError('工作项目不存在，请重新选择')
    if request.operation_id:
        op = item(owner, 'operation', request.operation_id)
        if op['project_id'] != request.project_id:
            raise ValueError('业务操作不属于当前工作项目')
    if request.run_id:
        run = store.run(owner, request.run_id)
        if run.get('spec', {}).get('project_id') != request.project_id:
            raise ValueError('运行记录不属于当前工作项目')
    return save(owner, 'context', 'current', request.revision, data)


def operation_state(owner, operation):
    result = deepcopy(operation)
    reasons = []
    try:
        testing.project(operation['project_id'], operation['source_sha256'])
        env = store.item(owner, 'environment', operation['environment_id'])
        if env['version'] != operation['environment_version']:
            reasons.append('环境配置已改变')
        artifact = native.artifact(operation['artifact_id'])
        if artifact['client_sha256'] != operation['client_sha256']:
            reasons.append('客户端产物已改变')
    except (ValueError, OSError, KeyError) as exc:
        reasons.append(native.redact(str(exc)))
    result['effective_status'] = 'needs_review' if reasons and operation['status'] == 'active' else operation['status']
    result['limitations'] = reasons + ['服务签名已核对不等于已证明该业务必定触发目标规则；部署版本待核对。']
    result['entry_runtime_verified'] = False
    return result


def save_operation(owner, request: Operation, identity=None):
    if identity:
        item(owner, 'operation', identity)
    if len(set(request.clf_names)) != len(request.clf_names) or any(not n.strip() or len(n) > 200 for n in request.clf_names):
        raise ValueError('目标 CLF 名称不能为空、重复或过长')
    project = testing.project(request.project_id, request.source_sha256)
    env = store.item(owner, 'environment', request.environment_id)
    if env['version'] != request.environment_version:
        raise ValueError('环境配置已变化，请重新核对')
    artifact = native.artifact(request.artifact_id)
    if artifact['client_sha256'] != request.client_sha256:
        raise ValueError('客户端产物已变化，请重新核对')
    schema = native.invoke(artifact, 'schema', service=request.service)
    if request.method not in {m['name'] for m in schema.get('methods', [])}:
        raise ValueError('真实客户端契约中没有所选方法')
    definitions = [clf.get_clf(project['mdb_file'], name, request.source_sha256) for name in request.clf_names]
    payload = {**request.model_dump(), 'evidence': {
        'source_sha256': request.source_sha256, 'client_sha256': request.client_sha256,
        'contract_checked': True, 'runtime_verified': False,
        'binding_ids': {d['name']: [b.get('CLFEventMapID') for b in d.get('bindings', [])] for d in definitions},
        'scope': 'verified_contract_and_existing_target_definitions_not_runtime_mapping'}}
    return operation_state(owner, save(owner, 'operation', identity or uuid4().hex, request.revision, payload))


def records(owner, project_id='', operation_id='', search='', offset=0, limit=20):
    operation = item(owner, 'operation', operation_id) if operation_id else None
    if operation and project_id and operation['project_id'] != project_id:
        raise ValueError('业务操作与项目不一致')
    query = search.casefold().strip()
    rows = []
    # Page metadata is kept small; raw responses are fetched only by explicit selection.
    with connection() as conn:
        for row in conn.execute('SELECT document FROM test_runs WHERE owner=? ORDER BY updated DESC', (owner,)):
            record = json.loads(row[0])
            spec = record.get('spec', {})
            if spec.get('kind') != 'clf' or project_id and spec.get('project_id') != project_id:
                continue
            if operation and not all(spec.get(key) == operation.get(key) for key in
                                     ('project_id', 'environment_id', 'artifact_id', 'service', 'method')):
                continue
            if operation and spec.get('clf_name') not in operation['clf_names']:
                continue
            summary = {'id': record['id'], 'status': record['status'], 'created_utc': record.get('created_utc'),
                       'clf_name': spec.get('clf_name'), 'service': spec.get('service'), 'method': spec.get('method'),
                       'project_id': spec.get('project_id'), 'environment_id': spec.get('environment_id'),
                       'source_sha256': spec.get('source_sha256'),
                       'input_summary': json.dumps(native.redact(spec.get('data', {})), ensure_ascii=False)[:500]}
            if query and query not in json.dumps(summary, ensure_ascii=False).casefold():
                continue
            rows.append(summary)
    return {'records': rows[offset:offset + limit], 'total': len(rows), 'offset': offset,
            'source': 'local_test_receipts', 'notice': '仅包含本工具的真实 CLF 测试回执，不是 MES 全部业务历史。'}


def inspect_run(owner, identity):
    record = store.run(owner, identity)
    result = record.get('result') or {}
    spec = record.get('spec') or {}
    response = result.get('response') or result.get('primary_response') or {}
    outcome = response.get('outcome') or result.get('outcome') or 'unknown'
    if outcome not in {'success', 'rejected'}:
        outcome = 'unknown'
    explicit = testing.trace_evidence(response.get('trace'), spec.get('clf_name', '')) if spec.get('clf_name') else []
    truncated = bool(response.get('output_truncated') or (result.get('readback') or {}).get('output_truncated'))
    if record['status'] == 'running':
        title, next_step = '请求仍在处理中', '查看同一请求的后续回执，不重新提交。'
    elif result.get('execution_phase') == 'rejected_before_invoke':
        title, next_step = '请求在执行前被阻止，尚未调用业务服务', '修正来源、输入或授权缺项后，再明确提交验证。'
    elif outcome == 'unknown':
        title, next_step = '实际业务结果尚未确认', '核对原请求与业务记录，确认结果后再决定是否复现。'
    elif outcome == 'success' and result.get('primary_response'):
        title, next_step = '原业务返回成功，后续核对未完成', '保留成功回执，先处理回读或后续错误，避免重复写入。'
    elif outcome == 'rejected':
        title, next_step = '业务已拒绝本次操作', '核对业务提示和相关输入；是否符合预期由用例断言单独判断。'
    elif not explicit:
        title, next_step = '业务返回成功，目标规则执行尚未确认', '核对实际入口和服务器轨迹，不据此判定规则验证通过。'
    else:
        title, next_step = '业务返回成功，已取得目标 CLF 名称证据', '继续核对用例预期与版本；具体函数行和运行值按实际证据查看。'
    checks = result.get('checks', [])
    assertion = 'partial' if truncated else 'not_evaluated' if not checks or outcome == 'unknown' else 'passed' if all(c.get('passed') for c in checks) else 'failed'
    return {'schema_version': 1, 'record': native.redact(record), 'title': title, 'next_step': next_step,
            'business_outcome': outcome, 'assertion_result': assertion, 'target_evidence': explicit,
            'trace': response.get('trace'), 'message': response.get('message') or result.get('message') or result.get('error', ''),
            'provenance': {'source_current_at_execution': result.get('source_current'),
                           'deployment': 'unknown', 'artifact_project_link': 'unknown'},
            'limitations': ['本次证据仅限该请求和已配置断言。', '部署来源与当前 MDB 的关系仍待核对。'] +
                          (['回执已截断，未覆盖内容不能判为通过。'] if truncated else [])}


def copy_case(owner, identity, name):
    record = store.run(owner, identity)
    if record.get('status') == 'running':
        raise ValueError('请求尚未结束，请等待原回执后再复制')
    spec = TestSpec.model_validate(record['spec']).model_copy(update={'allow_write': False})
    return store.save(owner, 'case', {'name': name, 'spec': native.redact(spec.model_dump()),
                                     'schema_version': 1, 'revision': 1, 'status': 'draft',
                                     'parent_run_id': identity, 'group': 'normal' if spec.expected_outcome == 'success' else 'exception',
                                     'notice': '沿用原运行版本；仅准备输入，未执行，写入授权未复制。'})


def compare_runs(owner, left, right):
    first, second = inspect_run(owner, left), inspect_run(owner, right)
    a, b = first['record']['spec'], second['record']['spec']
    if a.get('project_id') != b.get('project_id'):
        raise ValueError('请选择同一项目的两次运行')
    fields = [('目标规则', 'clf_name'), ('实际服务', 'service'), ('实际方法', 'method'),
              ('业务输入', 'data'), ('工作源', 'source_sha256'), ('客户端', 'client_sha256'),
              ('环境', 'environment_id'), ('环境修订', 'environment_version'), ('业务预期', 'expected_outcome'),
              ('字段断言', 'assertions')]
    rows = [{'name': label, 'left': a.get(key), 'right': b.get(key),
             'same': testing._equal(a.get(key), b.get(key))} for label, key in fields]
    for label, key in [('业务返回', 'business_outcome'), ('用例断言', 'assertion_result'), ('目标规则证据', 'target_evidence')]:
        rows.append({'name': label, 'left': first[key], 'right': second[key], 'same': testing._equal(first[key], second[key])})
    return {'left': left, 'right': right, 'rows': rows,
            'notice': '先核对输入、入口、环境及版本差异。结果不同不能直接归因于规则修改；部署关系仍待核对。'}
