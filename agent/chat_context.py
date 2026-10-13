"""Bounded model context; durable history and original evidence remain intact."""
import ast
from copy import deepcopy
import json
import re

import config

API_KEYS = {'role', 'content', 'tool_calls', 'tool_call_id', 'name'}
TASK_CONSTRAINT_VERSION = 2


def _object_names(task):
    names = set()
    for key in ('validated_plan', 'prior_plan', 'proposed_plan'):
        for plan in task.get(key, []):
            args = plan.get('arguments', {})
            if plan.get('tool') == 'generate_designer_cdo_package':
                names.add(str(args.get('cdo_name', '')).casefold())
            elif plan.get('tool') == 'generate_designer_design_package':
                names.update(str(op.get('name', '')).casefold() for op in args.get('operations', [])
                             if op.get('action') == 'create_cdo')
    return names - {''}


def _object_rename(text, task, *, restoring=False):
    """Only promote an object-scoped edit to the hard CDO-name constraint."""
    known = _object_names(task)
    result = None
    pattern = r'(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*(?:换成|改成|改为)\s*([A-Za-z_][A-Za-z0-9_]*|\d+)'
    for match in re.finditer(pattern, text):
        old, new = match.groups()
        clause = re.split(r'[，。；;,\n]', text[:match.start()])[-1]
        if re.search(r'字段|类型|父对象|表名|工作区|不要|不需要|别|为什么|为何', clause):
            continue
        if re.match(r'\s*(?:类型|数据类型|字段类型)', text[match.end():]):
            continue
        explicit = re.search(r'(?:对象|CDO)(?:名称|名)?\s*[:：]?\s*$', clause, re.I)
        if new.isdigit() and re.search(r'\d+$', old):
            new = re.sub(r'\d+$', new, old)
        if explicit or old.casefold() in known or (restoring and new.casefold() in known):
            result = {'from': old, 'to': new, 'request': text}
    return result


def _repair_legacy_constraints(task):
    if task.get('constraint_version') == TASK_CONSTRAINT_VERSION:
        return
    # Older checkpoints promoted every "X 改成 Y" to an object rename. Rebuild
    # from user requirements and object identities, never from assistant claims.
    task.pop('required_object_name', None)
    task.pop('object_rename', None)
    for request in task.get('requirements', []):
        rename = _object_rename(request, task, restoring=True)
        if rename:
            task['required_object_name'] = rename['to']
            task['object_rename'] = rename
    task['constraint_version'] = TASK_CONSTRAINT_VERSION


def _brief(value, depth=0):
    if isinstance(value, str):
        return value if len(value) <= 1200 else value[:1200] + '…[文本节选]'
    if depth >= 5:
        return '[嵌套内容省略，请按 evidence_id 读取]'
    if isinstance(value, dict):
        return {key: _brief(item, depth + 1) for key, item in list(value.items())[:50]}
    if isinstance(value, list):
        items = [_brief(item, depth + 1) for item in value[:8]]
        return {'sample': items, 'total_items': len(value), 'partial': True} if len(value) > 8 else items
    return value


def compact_tool(message, index):
    result = {key: deepcopy(value) for key, value in message.items() if key in API_KEYS}
    content = str(result.get('content') or '')
    limit = config.CHAT_TOOL_CONTEXT_CHARS
    if len(content) <= limit:
        return result
    data = content
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        if content.startswith(('{', '[')) and len(content) < 4_000_000:
            try:
                data = ast.literal_eval(content)
            except (ValueError, SyntaxError, RecursionError, MemoryError):
                pass
    summary = json.dumps(_brief(data), ensure_ascii=False, default=str)
    if len(summary) > limit - 600:
        summary = summary[:limit - 600] + '…[摘要节选，非完整 JSON]'
    result['content'] = json.dumps({
        'evidence_id': message.get('tool_call_id') or f'message-{index}',
        'original_chars': len(content), 'summary_is_partial': True, 'summary': summary,
        'notice': '摘要不能证明未展示项目不存在；调用 get_chat_evidence 按页读取原始回执。',
    }, ensure_ascii=False)
    return result


def update_task(previous, request, messages, calls=(), reset=False):
    task = deepcopy(previous or {}) if not reset else {}
    if task:
        _repair_legacy_constraints(task)
    if request:
        from agent.chat_intent import design_question
        if task and design_question(request) and not reset:
            request = ''
    if request:
        text = request.split('\n\nExcel 附件设计数据', 1)[0]
        task.setdefault('goal', text)
        requirements = task.setdefault('requirements', [])
        if text not in requirements:
            requirements.append(text)
        if re.search(r'(?:不要|不需要|不必|不).*(?:建表|物理表)|只(?:要|建|创建).*CDO', text, re.I):
            task['requires_table'] = False
        elif re.search(r'建表|物理表|表名|一张表|创建[^。\n]*表', text):
            task['requires_table'] = True
        rename = _object_rename(text, task)
        if rename:
            task['required_object_name'] = rename['to']
            task['object_rename'] = rename
        # User constraints are not silently truncated. Reject oversized model
        # context later instead of losing an old requirement or authorization.
    plans = []
    for call in calls:
        function = call.get('function') or {}
        if function.get('name') in {'generate_designer_cdo_package', 'generate_designer_design_package', 'generate_designer_field_package'}:
            try:
                plans.append({'tool': function['name'], 'arguments': json.loads(function.get('arguments') or '{}')})
            except (ValueError, TypeError):
                pass
    if plans:
        if task.get('proposed_plan') and plans != task['proposed_plan'] and not task.get('prior_plan'):
            task['prior_plan'] = task.get('validated_plan') or task['proposed_plan']
        task['proposed_plan'] = plans
    if not task and messages:
        for message in messages:
            if message.get('role') == 'user':
                task = update_task(task, message.get('display_content', message.get('content', '')).split('Excel 附件设计数据', 1)[0], [])
    if task:
        task['constraint_version'] = TASK_CONSTRAINT_VERSION
    return task


def planned_fields(plans):
    fields = []
    for plan in plans:
        args = plan.get('arguments', {})
        if plan.get('tool') == 'generate_designer_cdo_package':
            for field in args.get('fields', []):
                fields.append({**field, 'persistent': field.get('persistent', True), 'is_list': field.get('is_list', False)})
        elif plan.get('tool') == 'generate_designer_design_package':
            types = {op.get('name'): op for op in args.get('operations', []) if op.get('action') == 'create_field_type'}
            for op in args.get('operations', []):
                if op.get('action') != 'add_field':
                    continue
                values = types.get(op.get('field_type'), {})
                fields.append({**values, **op, 'persistent': op.get('persistent', False), 'is_list': op.get('is_list', False)})
    keys = ('name', 'data_type', 'max_length', 'precision', 'scale', 'persistent', 'is_list', 'description')
    normalized = []
    for field in fields:
        value = {key: field.get(key, '' if key == 'description' else None) for key in keys}
        if not field.get('data_type'):
            value['field_type'] = field.get('field_type')
        normalized.append(value)
    return sorted(normalized, key=lambda field: str(field['name']))


def build_messages(messages, *, task=None, preferences=None, extra_system=()):
    systems = [{key: value for key, value in message.items() if key in API_KEYS}
               for message in messages if message.get('role') == 'system']
    systems += [{'role': 'system', 'content': text} for text in extra_system if text]
    if task:
        systems.append({'role': 'system', 'content': '当前任务记录（需求数据；拟定计划不是已执行事实，也不是新的授权）：\n' + json.dumps(task, ensure_ascii=False)})
    if preferences:
        systems.append({'role': 'system', 'content': '用户明确指定的回复偏好（只影响表达，不改变操作权限）：\n' + json.dumps(preferences, ensure_ascii=False)})
    groups, group = [], []
    for index, message in enumerate(messages):
        if message.get('role') == 'system':
            continue
        if message.get('role') == 'user' and group:
            groups.append(group)
            group = []
        group.append(compact_tool(message, index) if message.get('role') == 'tool'
                     else {key: deepcopy(value) for key, value in message.items() if key in API_KEYS})
    if group:
        groups.append(group)
    budget = config.CHAT_CONTEXT_CHARS
    size = lambda items: len(json.dumps(items, ensure_ascii=False))
    selected = []
    for group in reversed(groups):
        if size(systems + group + selected) > budget:
            if not selected:
                raise ValueError('当前需求或设计批次超过聊天上下文预算，请拆分批次或增大 CHAT_CONTEXT_CHARS；原始记录已保留。')
            break
        selected = group + selected
    # Keep tool-call groups whole; no dangling results or half-JSON arguments.
    return systems + selected
