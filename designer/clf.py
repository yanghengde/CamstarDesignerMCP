"""CLF read models and native in-memory plan previews shared by MCP and UI."""
import json
from hashlib import sha256
from pathlib import Path

from designer import vendor, working

PLAN_ACTIONS = frozenset({'create_clf', 'copy_clf', 'add_clf_function', 'replace_clf_function',
                         'remove_clf_function', 'reorder_clf_functions', 'set_clf_parameter',
                         'bind_event', 'replace_event_binding', 'unbind_event'})


def source(mdb_file, expected_sha256=''):
    path = working.resolve(mdb_file)
    checksum = vendor.digest(path)
    if expected_sha256 and checksum != expected_sha256:
        raise ValueError('工作 MDB 已更新，请重新读取 CLF 后再预览')
    return path, checksum


def _unchanged(path, checksum):
    if vendor.digest(path) != checksum:
        raise ValueError('CLF 读取期间工作 MDB 已变化，请重新读取')


def list_clfs(mdb_file, search='', offset=0, limit=50):
    path, checksum = source(mdb_file)
    result = vendor.query(str(path), 'list', 'clf', search=search, offset=offset, limit=limit)
    _unchanged(path, checksum)
    return {**result, 'source_sha256': checksum, 'records': [
        {'name': row['name'], 'description': row.get('Description', ''),
         'workspace': row.get('WorkspaceCode'), 'id': row.get('CLFID')}
        for row in result['records']]}


def rule_index(mdb_file):
    """One compact, version-cached vendor read; never scan each CLF separately."""
    path, checksum = source(mdb_file)
    result = vendor.query(str(path), 'catalog', 'clf', name='rule_browser')
    _unchanged(path, checksum)
    return {**result, 'source_sha256': checksum}


def rule_objects(mdb_file, search='', selected=''):
    index = rule_index(mdb_file)
    objects = [o for o in index['objects'] if search.casefold() in (o['name'] + ' ' + (o.get('label') or '')).casefold()]
    selected_object = next((o for o in index['objects'] if o['name'] == selected), None)
    records = objects[:50]
    if selected_object and selected_object not in records:
        records.insert(0, selected_object)
    return {'records': records, 'total': len(objects), 'source_sha256': index['source_sha256']}


def related_rules(mdb_file, object_name='', scope='related', search='', offset=0):
    if scope not in {'related', 'customer', 'all'} or not 0 <= offset <= 10000:
        raise ValueError('规则筛选范围或分页无效')
    index = rule_index(mdb_file)
    objects = {o['name']: o for o in index['objects']}
    owners = set()
    if object_name:
        if object_name not in objects:
            raise ValueError('业务对象不存在，请从真实对象目录选择')
        pending = [object_name, objects[object_name].get('maintenance')]
        while pending:
            name = pending.pop()
            if not name or name in owners or name not in objects:
                continue
            owners.add(name)
            pending.append(objects[name].get('parent'))
    bindings = [b for b in index['bindings'] if not owners or b['object'] in owners]
    by_name = {}
    for binding in bindings:
        by_name.setdefault(binding['clf'], []).append(binding)
    records = []
    for rule in index['rules']:
        if scope == 'customer' and not rule['customer']:
            continue
        if scope == 'related' and owners and rule['name'] not in by_name:
            continue
        if search.casefold() not in (rule['name'] + ' ' + (rule.get('description') or '')).casefold():
            continue
        records.append({**rule, 'bindings': by_name.get(rule['name'], [])})
    records.sort(key=lambda r: (not r['customer'], r['name'].casefold()))
    return {'records': records[offset:offset+20], 'total': len(records), 'offset': offset,
            'source_sha256': index['source_sha256'], 'object_name': object_name,
            'scope': 'declared_object_maintenance_and_parent_bindings', 'dynamic_usage_verified': False}


def clf_view(record):
    calls = []
    for item in sorted(record.get('functions', []), key=lambda row: (row.get('Sequence', 0), row.get('CLFFunctionID', 0))):
        calls.append({'call_id': item.get('CLFFunctionID'), 'sequence': item.get('Sequence'),
                      'function': item.get('FunctionName') or (item.get('FunctionDefinition') or {}).get('name'),
                      'active': item.get('IsActive'), 'workspace': item.get('WorkspaceCode'),
                      'parameters': [{'name': p.get('Name') or p.get('name'), 'value': p.get('ValueExpression', ''),
                                      'dynamic': p.get('DynamicParm'), 'usage': p.get('FunctionParmUsage')}
                                     for p in item.get('parameters', [])]})
    return {'name': record['name'], 'description': record.get('Description', ''),
            'id': record.get('CLFID'), 'workspace': record.get('WorkspaceCode'),
            'active_workspace': record.get('ActiveWorkspaceCode'), 'clf_type': record.get('CLFType'),
            'calls': calls, 'declared_parameters': record.get('declared_parameters', [])}


def get_clf(mdb_file, name, expected_sha256=''):
    path, checksum = source(mdb_file, expected_sha256)
    batch = vendor.query_batch(str(path), [{'mode': 'get', 'kind': 'clf', 'name': name},
                                         {'mode': 'impact', 'kind': 'clf', 'name': name}])
    detail, references = [entry['result'] for entry in batch['results']]
    if detail.get('status') == 'failed':
        raise ValueError(detail.get('error', 'CLF 读取失败'))
    _unchanged(path, checksum)
    return {'format': 'designer_clf_v1', 'status': 'read', 'read_only': True,
            'source_file': str(path), 'source_sha256': checksum, **clf_view(detail),
            'references': {'available': references.get('status') != 'failed',
                           'rows': references.get('where_used', []), 'total': references.get('total'),
                           'truncated': references.get('truncated', False), 'error': references.get('error', ''),
                           'scope': 'vendor_where_used', 'runtime_usage_verified': False}}


def inspect_plan(mdb_file, expected_sha256, operations, workspace='', requirements=None, test_cases=None):
    if not isinstance(operations, list) or not operations:
        raise ValueError('请提供完整的 CLF 方案操作列表')
    if any(not isinstance(op, dict) or op.get('action') not in PLAN_ACTIONS for op in operations):
        raise ValueError('CLF 预检仅允许 CLF 调用、参数及事件绑定操作')
    vendor.validate_operations(operations)
    path, checksum = source(mdb_file, expected_sha256)
    created = {op['name'].casefold() for op in operations if op['action'] in {'create_clf', 'copy_clf'}}
    names = []
    for op in operations:
        candidates = [op.get('template'), op.get('clf'), op.get('expected_clf')]
        if op['action'] not in {'create_clf', 'copy_clf', 'bind_event', 'replace_event_binding', 'unbind_event'}:
            candidates.append(op.get('owner'))
        for name in candidates:
            if name and name.casefold() not in created and name not in names:
                names.append(name)
    before = [get_clf(str(path), name, checksum) for name in names]
    preflight = vendor.preflight(str(path), checksum, operations, workspace)
    _unchanged(path, checksum)
    requirements = list(requirements or [])
    test_cases = list(test_cases or [])
    # All test cases are proposals; no expression interpreter or runtime is invoked.
    tests = [{**case, 'status': 'not_run'} for case in test_cases]
    payload = {'format': 'designer_clf_plan_v1', 'status': 'plan_previewed', 'read_only': True,
               'source_file': str(path), 'source_sha256': checksum,
               'workspace': preflight.get('workspace', workspace),
               'operations': operations, 'requirements': requirements, 'test_cases': tests,
               'before': before, 'after': [clf_view(record) for record in preflight.get('clfs', [])],
               'metadata_preflight_passed': preflight['ready'], 'conflicts': preflight['conflicts'],
               'runtime_verified': False, 'saved': False, 'published': False,
               'notice': '仅完成官方内存模型预检，未保存 MDB；表达式语义及业务用例尚未运行验证。'}
    payload['id'] = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload


def session_context(username, session_id):
    from agent.assistant_modes import session
    context = dict(session(username, session_id).get('clf_context', {}))
    project_id = context.get('project_id')
    if project_id:
        project = working.summary(project_id)
        if not project:
            raise ValueError('选定的 CLF 工作项目不存在，请重新选择')
        path, checksum = source(project['mdb_file'])
        context.update(mdb_file=str(path), source_sha256=checksum)
    handoff = context.get('excel_handoff')
    from agent.chat_intent import new_task
    for message in reversed(session(username, session_id).get('messages', [])):
        if message.get('role') != 'user':
            continue
        if message.get('attachments'):
            from designer.attachments import resolve_attachments
            from designer.excel_rules import extract
            documents = resolve_attachments([item['id'] for item in message['attachments']], username, session_id)
            context.pop('excel_handoff', None)
            context['uploaded_rules'] = [rule for doc in documents for rule in extract(doc)]
            handoff = None
            break
        if new_task(message.get('content', '')):
            context.pop('excel_handoff', None)
            handoff = None
            break
    if handoff:
        from designer.attachments import resolve_attachments
        documents = resolve_attachments([item['id'] for item in handoff['attachments']], username, handoff['source_session_id'])
        if [doc['sha256'] for doc in documents] != [item['sha256'] for item in handoff['attachments']]:
            raise ValueError('Excel 来源已变化，请从原设计对话重新带入规则')
    return context
