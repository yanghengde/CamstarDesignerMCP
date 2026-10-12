"""Pure argument construction and validation shared by preview and execution."""
import json
import inspect


def cdo_operations(cdo_name, parent_cdo, fields, description=''):
    operations = [{'action': 'create_cdo', 'name': cdo_name, 'parent': parent_cdo, 'description': description}]
    if not isinstance(fields, list) or not 1 <= len(fields) <= 20:
        raise ValueError('fields 必须为1～20个字段定义')
    for field in fields:
        if not isinstance(field, dict) or not field.get('name'):
            raise ValueError('每个字段需要name')
        name = field['name']
        field_type = field.get('field_type')
        if field_type and 'max_length' in field:
            raise ValueError('使用现有field_type时不能同时指定max_length；请创建专用类型')
        if not field_type:
            if not field.get('data_type'):
                raise ValueError('每个字段需要data_type或现有field_type')
            field_type = name + (str(field['max_length']) if field.get('data_type') == 'String' and 'max_length' in field else 'Type')
            type_op = {'action': 'create_field_type', 'name': field_type, 'data_type': field['data_type']}
            for key in ('max_length', 'precision', 'scale'):
                if key in field:
                    type_op[key] = field[key]
            operations.append(type_op)
        operations.append({'action': 'add_field', 'name': name, 'owner': cdo_name, 'field_type': field_type,
                           'persistent': field.get('persistent', True), 'is_list': field.get('is_list', False),
                           'description': field.get('description', '')})
    return operations


def validate_calls(calls):
    from designer.vendor import validate_operations
    from designer.metadata import identifier
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
            validate_operations(cdo_operations(args.get('cdo_name'), args.get('parent_cdo'), args.get('fields'), args.get('description', '')))
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
