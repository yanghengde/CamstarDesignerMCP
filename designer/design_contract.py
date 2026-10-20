"""Pure argument construction and validation shared by preview and execution."""
import json
import inspect
import os
from hashlib import sha256
from pathlib import Path


def private_field_type_name(owner, name, data_type, max_length=None):
    """Stable owner-scoped names fit Designer's 30-character definition limit."""
    suffix = str(max_length) if data_type == 'String' and max_length is not None else 'Type'
    full = f'{owner}_{name}_{suffix}'
    identity = json.dumps([owner, name, data_type, max_length], separators=(',', ':')).casefold()
    return full if len(full) <= 30 else full[:21] + '_' + sha256(identity.encode()).hexdigest()[:8]


def cdo_operations(cdo_name, parent_cdo, fields, description='', *, create_table=False,
                   table_name='', table_description='', create_revision_base=False, create_maintenance=False):
    from designer.metadata import identifier
    identifier(cdo_name)
    for key, value in [('create_table', create_table), ('create_revision_base', create_revision_base),
                       ('create_maintenance', create_maintenance)]:
        if not isinstance(value, bool):
            raise ValueError(f'{key} 必须是布尔值')
    for key, value in [('table_name', table_name), ('table_description', table_description)]:
        if not isinstance(value, str):
            raise ValueError(f'{key} 必须是字符串')
    operations = [{'action': 'create_cdo', 'name': cdo_name, 'parent': parent_cdo, 'description': description}]
    options = {'create_table': create_table, 'table_name': table_name, 'table_description': table_description,
               'create_revision_base': create_revision_base, 'create_maintenance': create_maintenance}
    operations[0].update({key: value for key, value in options.items() if value})
    if not isinstance(fields, list) or not fields:
        raise ValueError('fields 必须为非空字段定义列表')
    seen = set()
    for field in fields:
        if not isinstance(field, dict) or not field.get('name'):
            raise ValueError('每个字段需要name')
        unknown = set(field) - {'name', 'data_type', 'max_length', 'precision', 'scale', 'field_type',
                                'persistent', 'is_list', 'description', 'portal_editable'}
        if unknown:
            raise ValueError(f"字段 {field['name']} 包含未知参数：{', '.join(sorted(unknown))}")
        name = field['name']
        identifier(name)
        if name.casefold() in seen:
            raise ValueError(f'同一对象的字段名称重复（不区分大小写）：{name}')
        seen.add(name.casefold())
        field_type = field.get('field_type')
        if 'field_type' in field and (not isinstance(field_type, str) or not field_type.strip()):
            raise ValueError(f'字段 {name} 的 field_type 必须是非空名称')
        if field_type and 'max_length' in field:
            raise ValueError('使用现有field_type时不能同时指定max_length；请创建专用类型')
        if not field_type:
            if not field.get('data_type'):
                raise ValueError('每个字段需要data_type或现有field_type')
            field_type = private_field_type_name(cdo_name, name, field['data_type'], field.get('max_length'))
            type_op = {'action': 'create_field_type', 'name': field_type, 'data_type': field['data_type']}
            for key in ('max_length', 'precision', 'scale'):
                if key in field:
                    type_op[key] = field[key]
            operations.append(type_op)
        operations.append({'action': 'add_field', 'name': name, 'owner': cdo_name, 'field_type': field_type,
                           'persistent': field.get('persistent', True), 'is_list': field.get('is_list', False),
                           'description': field.get('description', '')})
        if 'portal_editable' in field:
            operations[-1]['portal_editable'] = field['portal_editable']
        if field.get('field_type'):
            expected = {key: field[key] for key in ('data_type', 'precision', 'scale') if key in field}
            if expected:
                operations[-1]['expected_type'] = expected
    return operations


def validate_calls(calls):
    from designer.vendor import validate_operations
    from designer.metadata import identifier
    mdb_targets = []
    for call in calls:
        function = call.get('function') or {}
        name = function.get('name')
        if name not in {'generate_designer_cdo_package', 'generate_designer_design_package', 'generate_designer_field_package'}:
            continue
        args = json.loads(function.get('arguments') or '{}')
        if not isinstance(args, dict):
            raise ValueError('设计参数必须是 JSON 对象')
        if name == 'generate_designer_design_package':
            operations = args.get('operations')
            if isinstance(operations, list):
                for index, op in enumerate(operations, 1):
                    if not isinstance(op, dict) or 'action' not in op:
                        raise ValueError(f'operations 第 {index} 项缺少 action；例如 {{"action":"create_cdo","name":"ExExample","parent":"NamedDataObject"}}。不要使用 op 键或操作名作为包装键。')
            validate_operations(operations)
        elif name == 'generate_designer_cdo_package':
            validate_operations(cdo_operations(args.get('cdo_name'), args.get('parent_cdo'), args.get('fields'), args.get('description', ''),
                **{key: args[key] for key in ('create_table', 'table_name', 'table_description', 'create_revision_base', 'create_maintenance') if key in args}))
        else:
            for key in ('target_cdo', 'template_cdo', 'template_field', 'field_name'):
                identifier(args.get(key, ''))
            if not args.get('workspace'):
                raise ValueError('必须指定拟导入的客户工作区')
        from tools import get_tool_func
        try:
            inspect.signature(get_tool_func(name)).bind(**args)
        except TypeError as exc:
            raise ValueError(f'{name} 参数不完整或包含未知参数：{exc}') from exc
        if name in {'generate_designer_cdo_package', 'generate_designer_design_package'}:
            mdb_targets.append(args['mdb_file'])
    if len(mdb_targets) > 1:
        from designer import working
        from designer.files import root_dir
        seen = set()
        for target in mdb_targets:
            path = Path(target)
            path = path if path.is_absolute() else root_dir() / path
            try:
                path.stat()
            except FileNotFoundError:
                # Pure argument tests may not have an MDB. Existing metadata
                # must still pass the normal source and lineage checks below.
                resolved = path
            else:
                resolved = working.resolve(target)
            identity = os.path.normcase(os.path.abspath(resolved))
            if identity in seen:
                raise ValueError('同一批工具调用不能多次修改同一个工作 MDB：首次保存会使后续调用的来源 SHA256 失效。'
                                 '请合并为一个 generate_designer_design_package，将该 MDB 的全部变更放入同一 operations 后重新预检。')
            seen.add(identity)


def inspect_calls(calls):
    """Bind plans to complete vendor profiles before confirmation or execution."""
    from designer import vendor
    reports = []
    for call in calls:
        function = call.get('function') or {}
        args = json.loads(function.get('arguments') or '{}')
        if function.get('name') == 'generate_designer_cdo_package':
            operations = cdo_operations(args['cdo_name'], args['parent_cdo'], args['fields'], args.get('description', ''),
                **{key: args[key] for key in ('create_table', 'table_name', 'table_description', 'create_revision_base', 'create_maintenance') if key in args})
        elif function.get('name') == 'generate_designer_design_package':
            operations = args['operations']
        else:
            continue
        report = vendor.preflight(args['mdb_file'], args['expected_sha256'], operations, args.get('workspace', ''))
        reports.append({**report, 'call_id': call.get('id')})
    return reports
