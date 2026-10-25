"""Default-system WCF browsing and edits bound to previously loaded records."""
from typing import Literal

from pydantic import Field

import config
from designer import testing, testing_runtime as native, testing_store as store, working
from designer.testing_models import StrictModel, TestSpec


class ActionRequest(StrictModel):
    username: str
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    action: Literal['query', 'load', 'update']
    service: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*Maint$', max_length=200)
    source_run_id: str = Field(default='', pattern=r'^(?:[0-9a-f]{32})?$')
    row_index: int = Field(default=0, ge=0, le=4)
    changes: dict = Field(default_factory=dict)


def default_context(owner):
    profiles = store.items(owner, 'environment')
    address = native.endpoint(config.DESIGNER_WCF_ADDRESS) if config.DESIGNER_WCF_ADDRESS else ''
    profiles = [p for p in profiles if p.get('protected_password') and
                (not address or native.endpoint(p['endpoint']) == address)]
    if not profiles:
        raise ValueError('系统尚无可复用的 WCF 业务账号，请先检查系统已有的业务连接配置')
    profile = profiles[0]
    projects = working.available()
    project_id = profile.get('project_id') or (projects[0]['id'] if len(projects) == 1 else '')
    if not project_id:
        raise ValueError('系统默认连接尚未关联工作项目')
    context = testing.project(project_id)
    artifact_id = profile.get('artifact_id')
    if not artifact_id:
        candidates = [item for item in native.artifacts() if not item.get('partial_package')]
        artifact_id = candidates[0]['id'] if candidates else ''
    if not artifact_id:
        raise ValueError('系统尚无可用的 WCF 客户端，请先完成客户端生成')
    return context, native.artifact(artifact_id), profile


def bootstrap(owner):
    context, item, profile = default_context(owner)
    catalog = native.invoke(item, 'catalog')
    return {'services': [name for name in catalog['services'] if name.endswith('Maint')],
            'connection': {'name': '系统默认环境', 'endpoint': profile['endpoint']},
            'history': [r for r in store.history(owner) if r['spec'].get('kind') == 'wcf'],
            'write_target_confirmed': config.DESIGNER_TEST_TARGET_CONFIRMED}


def service_schema(item, service):
    schema = native.invoke(item, 'schema', service=service)
    fields = {field['name']: field['schema'] for field in schema['fields']['fields']}
    if 'ObjectToChange' not in fields:
        raise ValueError('此服务不支持维护对象查询')
    changes = fields.get('ObjectChanges', {}).get('fields', [])
    editable = [f for f in changes if f['name'] not in {
        'Name', 'Revision', 'FieldAction', 'IgnoreTypeDifference', 'ListItemAction',
        'ListItemIndex', 'Key', 'CDOTypeName', 'Self', 'InstanceID', 'InstanceId'
    } and f['schema']['kind'] in {'primitive', 'scalar', 'enum'}]
    returns = [f['name'] for f in changes if f['schema']['kind'] in {'primitive', 'scalar', 'enum'} and
               ('return_fields' not in schema or 'ObjectChanges.' + f['name'] in schema['return_fields'])]
    priority = ['Name', 'Revision', 'Description', 'ExtName']
    returns = sorted(returns, key=lambda name: (name not in priority, priority.index(name) if name in priority else name))[:100]
    return schema, editable, ['ObjectChanges.' + name for name in returns]


def view_schema(owner, service):
    _, item, _ = default_context(owner)
    schema, editable, _ = service_schema(item, service)
    return {'service': service, 'editable_fields': editable,
            'can_query': any(m['name'] == 'GetEnvironment' for m in schema['methods']),
            'can_edit': bool(editable) and {'Load', 'ExecuteTransaction'}.issubset({m['name'] for m in schema['methods']})}


def selection_rows(record):
    selection = (record.get('result', {}).get('response') or {}).get('selection') or {}
    headers, rows = selection.get('headers', []), selection.get('rows', [])
    if not isinstance(headers, list) or not isinstance(rows, list):
        raise ValueError('WCF 查询结果格式无效')
    return [dict(zip(headers, row)) for row in rows[:5] if isinstance(row, list)]


def source_record(owner, identity, spec, method):
    record = store.run(owner, identity)
    previous = record['spec']
    if (record['status'] != 'passed' or previous.get('method') != method or
            any(previous.get(key) != spec.get(key) for key in (
                'service', 'project_id', 'source_sha256', 'artifact_id', 'client_sha256',
                'environment_id', 'environment_version'))):
        raise ValueError('查询结果已过期或不属于当前对象，请重新查询')
    return record


def build_spec(owner, request):
    context, item, profile = default_context(owner)
    schema, editable, return_fields = service_schema(item, request.service)
    spec = dict(kind='wcf', project_id=context['id'], source_sha256=context['source_sha256'],
                artifact_id=item['id'], client_sha256=item['client_sha256'],
                environment_id=profile['id'], environment_version=profile['version'], service=request.service)
    methods = {m['name'] for m in schema['methods']}
    if request.action == 'query':
        if request.changes or request.source_run_id:
            raise ValueError('列表查询不接受修改数据')
        if 'GetEnvironment' not in methods:
            raise ValueError('此服务不支持列表查询')
        return TestSpec(**spec, method='GetEnvironment', selection_limit=5)
    if not request.source_run_id:
        raise ValueError('请先查询并选择已有记录')
    if request.action == 'load':
        if request.changes or 'Load' not in methods:
            raise ValueError('不支持该记录的读取请求')
        record = source_record(owner, request.source_run_id, spec, 'GetEnvironment')
        rows = selection_rows(record)
        if request.row_index >= len(rows):
            raise ValueError('所选记录不存在，请重新查询')
        row = rows[request.row_index]
        if not row.get('Name'):
            raise ValueError('查询结果未包含对象名称，无法编辑')
        reference = {'Name': row['Name']}
        ref_schema = next(f['schema'] for f in schema['fields']['fields'] if f['name'] == 'ObjectToChange')
        if any(f['name'] == 'Revision' for f in ref_schema.get('fields', [])):
            if not row.get('Revision'):
                raise ValueError('所选记录缺少版本，无法确定需要编辑的版本')
            reference['Revision'] = row['Revision']
        return TestSpec(**spec, method='Load', data={'ObjectToChange': reference}, return_fields=return_fields)
    record = source_record(owner, request.source_run_id, spec, 'Load')
    reference = record['spec']['data']['ObjectToChange']
    original = (record.get('result', {}).get('response') or {}).get('value') or {}
    original = original.get('ObjectChanges') or {}
    if not original or original.get('Name') != reference['Name']:
        raise ValueError('未取得已有对象内容，请重新查询后编辑')
    if reference.get('Revision') and original.get('Revision') != reference['Revision']:
        raise ValueError('读取的对象版本不一致，请重新查询后编辑')
    if not request.changes:
        raise ValueError('没有需要保存的修改')
    allowed = {f['name'] for f in editable}
    if not set(request.changes).issubset(allowed) or not set(request.changes).issubset(original):
        raise ValueError('只能修改本次已读取的可编辑字段，不能新增、改名或删除对象')
    if 'ExecuteTransaction' not in methods:
        raise ValueError('此服务不支持保存修改')
    if not config.DESIGNER_TEST_TARGET_CONFIRMED:
        raise ValueError('当前系统尚未允许修改业务数据')
    return TestSpec(**spec, method='ExecuteTransaction',
                    data={'ObjectToChange': reference, 'ObjectChanges': request.changes},
                    return_fields=return_fields, allow_write=True, readback=True,
                    readback_data={'ObjectToChange': reference},
                    assertions=[{'path': 'ObjectChanges.' + name, 'operator': 'equals', 'value': value}
                                for name, value in request.changes.items()])
