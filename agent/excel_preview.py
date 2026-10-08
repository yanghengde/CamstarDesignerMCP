"""Exact, attachment-bound previews for Designer write calls."""
from hashlib import sha256
import json
import re

from designer.attachments import resolve_attachments

DESIGN_TOOLS = {'generate_designer_cdo_package', 'generate_designer_design_package', 'generate_designer_field_package'}


def confirmation(text):
    normalized = re.sub(r'[\s，。！？、,.!?;；:：]+', '', text or '')
    return normalized in {'确定', '确认', '确认导入', '确定导入', '确认创建', '确定创建', '确认执行', '确定执行'}


def fingerprint(call):
    function = call.get('function') or {}
    return sha256(json.dumps({'id': call.get('id'), 'name': function.get('name'),
                             'arguments': function.get('arguments')}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sources(messages):
    for message in reversed(messages):
        if message.get('role') == 'user' and message.get('attachments'):
            return message['attachments']
    return []


def validate_sources(items, username, session_id):
    documents = resolve_attachments([item['id'] for item in items], username, session_id)
    if [item['sha256'] for item in documents] != [item['sha256'] for item in items]:
        raise ValueError('Excel 附件已变化，请重新上传并预览')


def build(calls, attachments):
    rows, targets, objects = [], [], []
    def row(action, owner='', name='', parent='', values=None):
        value = values or {}
        rows.append({'action': action, 'owner': owner, 'name': name, 'parent': parent,
                     'data_type': value.get('data_type') or value.get('field_type', ''),
                     'max_length': value.get('max_length', ''), 'persistent': value.get('persistent', True) if action == '新增字段' else None,
                     'is_list': value.get('is_list', False) if action == '新增字段' else None,
                     'description': value.get('description', ''),
                     'details': {key: val for key, val in value.items() if key not in {
                         'action', 'owner', 'name', 'parent', 'data_type', 'max_length',
                         'persistent', 'is_list', 'description', 'fields', 'mdb_file',
                         'expected_sha256', 'workspace', 'cdo_name', 'parent_cdo'}}})
    actions = {'create_cdo': '新增对象', 'add_field': '新增字段', 'create_field_type': '新增字段类型',
               'patch': '修改属性', 'delete': '删除定义', 'change_field_type': '修改字段类型'}
    for call in calls:
        function = call['function']
        args = json.loads(function.get('arguments') or '{}')
        if not isinstance(args, dict):
            raise ValueError('设计参数不是对象，无法生成预览')
        target = {key: args.get(key, '') for key in ('mdb_file', 'xml_file', 'workspace', 'expected_sha256')}
        if target not in targets:
            targets.append(target)
        if function['name'] == 'generate_designer_cdo_package':
            owner = args.get('cdo_name', '')
            objects.append({'name': owner, 'parent': args.get('parent_cdo', '')})
            row('新增对象', name=owner, parent=args.get('parent_cdo', ''), values=args)
            for field in args.get('fields', []):
                row('新增字段', owner, field.get('name', ''), values=field)
        elif function['name'] == 'generate_designer_design_package':
            types = {op['name']: op for op in args.get('operations', []) if op.get('action') == 'create_field_type'}
            for op in args.get('operations', []):
                if op.get('action') == 'create_cdo':
                    objects.append({'name': op.get('name', ''), 'parent': op.get('parent', '')})
                values = {**types.get(op.get('field_type'), {}), **op}
                if op.get('action') == 'add_field':
                    values['persistent'] = op.get('persistent', False)
                row(actions.get(op.get('action'), op.get('action', '设计变更')), op.get('owner', ''), op.get('name', ''), op.get('parent', ''), values)
        else:
            row('新增字段', args.get('target_cdo', ''), args.get('field_name', ''), values={**args, 'persistent': False})
    payload = {'rows': rows, 'objects': objects, 'targets': targets,
               'attachments': [{'id': item['id'], 'name': item['name'], 'sha256': item['sha256']} for item in attachments],
               'call_fingerprints': [fingerprint(call) for call in calls]}
    identity = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {**payload, 'id': identity, 'status': 'pending',
            'object_count': len(objects), 'field_count': sum(row['action'] == '新增字段' for row in rows),
            'message': '请核对 Excel 设计预览，输入“确定”或“确认”后开始创建；输入“取消”可停止。'}


def attach_preview(messages, preview, calls):
    updated = [dict(message) for message in messages]
    ids = {call.get('id') for call in calls}
    for message in reversed(updated):
        if message.get('role') == 'assistant' and any(call.get('id') in ids for call in message.get('tool_calls', [])):
            message['excel_preview'] = preview
            break
    return updated
