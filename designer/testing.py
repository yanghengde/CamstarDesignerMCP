"""Real service execution, assertions and provenance for both test pages."""
import asyncio
from hashlib import sha256
import json
import threading
import time
from uuid import uuid4

import config
from designer import clf, working, vendor
from designer.testing_models import Environment, TestSpec
from designer import testing_runtime as native, testing_store as store

_tasks = set()
_execution_lock = threading.Lock()


def project(identity, expected=''):
    summary = working.summary(identity)
    if not summary:
        raise ValueError('工作项目不存在，请先在设计助手中建立项目')
    path, checksum = clf.source(summary['mdb_file'], expected)
    changes = []
    paths = (working.load(identity) or {}).get('cycle_manifests') or ([summary['latest_manifest']] if summary.get('latest_manifest') else [])
    for manifest_file in paths:
        manifest = json.loads(working.source_path(manifest_file, '.json').read_text(encoding='utf-8'))
        for operation in manifest.get('operations', []):
            changes.append({key: operation[key] for key in ('action', 'name', 'owner', 'field', 'clf', 'event') if key in operation})
    return {**summary, 'source_sha256': checksum, 'changes': changes, 'source_file': str(path)}


def save_environment(owner, request: Environment, identity=None):
    data = request.model_dump(exclude={'password', 'version'})
    data['endpoint'] = native.endpoint(data['endpoint'])
    identity = identity or uuid4().hex
    # Compare and replace within one write transaction, including password preservation.
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT document FROM test_items WHERE id=? AND owner=? AND kind=?', (identity, owner, 'environment')).fetchone()
        old = json.loads(row[0]) if row else None
        if request.version != (old or {}).get('version', 0):
            raise ValueError('环境配置已变化，请刷新后重新核对')
        password = store.protect(request.password) if request.password is not None else (old or {}).get('protected_password', '')
        document = {**data, 'id': identity, 'version': request.version + 1, 'protected_password': password, 'updated_utc': store.now()}
        conn.execute('INSERT INTO test_items VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document,updated=excluded.updated WHERE test_items.owner=excluded.owner AND test_items.kind=excluded.kind',
                     (identity, owner, 'environment', json.dumps(document, ensure_ascii=False), store.now()))
    return store.public_environment(document)


def environment(owner, identity, version=None):
    document = store.item(owner, 'environment', identity)
    if version is not None and document['version'] != version:
        raise ValueError('测试环境配置已改变，请重新核对后更新用例')
    return {**document, 'password': store.protect(document.get('protected_password', ''), decrypt=True)}


def _arguments(spec):
    return {key: getattr(spec, key) for key in ('service', 'method', 'data', 'return_fields', 'collect_trace', 'allow_write')}


def prepare(owner, spec):
    context = project(spec.project_id, spec.source_sha256)
    item = native.artifact(spec.artifact_id)
    if item['client_sha256'] != spec.client_sha256:
        raise ValueError('客户端版本与测试用例不一致，请重新核对')
    env = environment(owner, spec.environment_id, spec.environment_version)
    if spec.method not in native.READ_METHODS:
        if not spec.allow_write:
            raise ValueError('此方法可能写入业务数据，请在页面明确启用写入测试')
        if not config.DESIGNER_TEST_TARGET_CONFIRMED:
            raise ValueError('写入测试需要 DESIGNER_TEST_TARGET_CONFIRMED=true')
    if spec.readback and not spec.readback_data:
        raise ValueError('保存后回读需要提供独立的 Load 请求参数')
    if spec.kind == 'clf':
        definition = clf.get_clf(context['mdb_file'], spec.clf_name, spec.source_sha256)
        context['clf_definition'] = {'name': definition['name'], 'source_sha256': definition['source_sha256'],
                                     'calls': definition['calls'], 'references': definition['references']}
    native.invoke(item, 'validate', env, **_arguments(spec))
    if spec.readback:
        native.invoke(item, 'validate', env, **{**_arguments(spec), 'method': 'Load', 'data': spec.readback_data, 'allow_write': False})
    return context, item, env


def _get(value, path):
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _equal(left, right):
    # JSON boolean and integer are distinct, including inside arrays/objects.
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def assertions(spec, response, readback=None):
    checks = [{'name': '业务结果', 'passed': response.get('outcome') == spec.expected_outcome,
               'expected': spec.expected_outcome, 'actual': response.get('outcome')}]
    if spec.message_contains:
        checks.append({'name': '业务提示', 'passed': spec.message_contains in response.get('message', ''),
                       'expected': spec.message_contains, 'actual': response.get('message', '')})
    value = response.get('value')
    if readback is not None:
        checks.append({'name': 'Load 回读', 'passed': readback.get('outcome') == 'success',
                       'expected': 'success', 'actual': readback.get('outcome')})
        value = readback.get('value')
    for assertion in spec.assertions:
        exists, actual = _get(value, assertion.path)
        passed = exists if assertion.operator == 'exists' else exists and _equal(actual, assertion.value) if assertion.operator == 'equals' else exists and isinstance(actual, str) and isinstance(assertion.value, str) and assertion.value in actual
        checks.append({'name': assertion.path + ' · ' + assertion.operator, 'passed': passed,
                       'expected': assertion.value if assertion.operator != 'exists' else '字段存在', 'actual': actual, 'exists': exists})
    return checks


def trace_evidence(trace, name):
    """Only an explicit native CLF-name attribute is evidence; text mentions are not."""
    matches = []
    def visit(node):
        if not isinstance(node, dict):
            return
        if node.get('Name') in {'CLF', 'CLFName', 'CLF Name'} and node.get('Value') == name:
            matches.append({'name': node['Name'], 'value': node['Value']})
        for attribute in node.get('Attributes', []) or []:
            if isinstance(attribute, dict) and attribute.get('Name') in {'CLF', 'CLFName', 'CLF Name'} and attribute.get('Value') == name:
                matches.append(attribute)
        for child in node.get('Children', []) or []:
            visit(child)
    visit(trace)
    return matches


def execute(owner, spec):
    started = time.monotonic()
    response = None
    attempted = False
    env = {}
    try:
        with _execution_lock:
            context, item, env = prepare(owner, spec)
            attempted = True
            response = native.invoke(item, 'run', env, **_arguments(spec))
            if response.get('outcome') not in {'success', 'rejected'}:
                raise ValueError(response.get('message') or '客户端或服务通信失败，未取得明确业务返回')
            readback = None
            if spec.readback and response['outcome'] == 'success':
                readback = native.invoke(item, 'run', env, **{**_arguments(spec), 'method': 'Load', 'data': spec.readback_data, 'allow_write': False})
                if readback.get('outcome') not in {'success', 'rejected'}:
                    raise ValueError('Load 回读未完成：' + (readback.get('message') or '客户端或服务通信失败'))
            checks = assertions(spec, response, readback)
            if response.get('output_truncated') or (readback or {}).get('output_truncated'):
                checks.append({'name': '完整回执', 'passed': False, 'expected': '完整返回内容', 'actual': '返回内容已截断，请缩小请求范围'})
            current = vendor.digest(working.resolve(context['mdb_file'])) == spec.source_sha256
            matches = trace_evidence(response.get('trace'), spec.clf_name) if spec.kind == 'clf' else []
            passed = all(check['passed'] for check in checks)
            result = {'status': 'passed' if passed else 'failed', 'outcome': response['outcome'],
                      'message': response.get('message', ''), 'checks': checks, 'response': response, 'readback': readback,
                      'source_current': current, 'source_sha256': spec.source_sha256, 'client_sha256': spec.client_sha256,
                      'compiled_sha256': item['compiled_sha256'], 'environment': store.public_environment({key: value for key, value in env.items() if key != 'password'}),
                      'deployment_version_verified': False, 'artifact_project_link_verified': False,
                      'clf_trigger_verified': bool(matches), 'clf_evidence': matches,
                      'runtime_verified': passed and current and (spec.kind != 'clf' or bool(matches)),
                      'notice': '结果仅覆盖本次请求与断言；已部署程序集版本、产物与当前 MDB 的编译来源关系仍需独立核对。'}
            if spec.kind == 'clf':
                result['definition'] = context['clf_definition']
            return native.redact({**result, 'elapsed_ms': round((time.monotonic() - started) * 1000)}, [env.get('password', '')])
    except Exception as exc:
        return native.redact({'status': 'error', 'outcome': 'unknown', 'runtime_verified': False,
                              'error': str(exc), 'primary_response': response,
                              'mutation_may_have_occurred': attempted and spec.method not in native.READ_METHODS and spec.allow_write,
                              'elapsed_ms': round((time.monotonic() - started) * 1000)}, [env.get('password', '')])


async def start(owner, request_id, spec):
    payload = spec.model_dump()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode('utf-8')) > 1024 * 1024:
        raise ValueError('单次测试请求不能超过 1 MB')
    record, created = await asyncio.to_thread(store.begin, owner, request_id, sha256(encoded.encode()).hexdigest(), native.redact(payload))
    if created:
        async def background():
            result = await asyncio.to_thread(execute, owner, spec)
            await asyncio.to_thread(store.finish, owner, record['id'], result)
        task = asyncio.create_task(background())
        _tasks.add(task)
        def completed(done):
            _tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(completed)
    return record


async def drain():
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
