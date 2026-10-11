"""Describe all design mutations consistently in tools, progress and previews."""
CREATE_ACTIONS = {'create_cdo', 'create_field_type', 'add_field', 'copy_clf', 'create_query',
                  'create_map', 'create_clf', 'add_clf_function', 'create_label', 'add_column',
                  'add_query_text', 'create_index', 'add_field_map', 'bind_event'}
UPDATE_ACTIONS = {'patch', 'change_field_type', 'reorder_clf_functions', 'set_clf_parameter',
                  'change_parent', 'change_storage_category', 'replace_event_binding',
                  'replace_clf_function', 'update_field_map', 'sync_query_parameters'}
DELETE_ACTIONS = {'delete', 'unbind_event', 'remove_clf_function', 'remove_field_map', 'remove_field_override'}
DESIGN_ACTIONS = CREATE_ACTIONS | UPDATE_ACTIONS | DELETE_ACTIONS
ACTION_KINDS = {'create_cdo': 'cdo', 'create_field_type': 'field_type', 'add_field': 'field',
                'copy_clf': 'clf', 'create_clf': 'clf', 'create_query': 'query', 'create_map': 'map',
                'create_label': 'label', 'add_column': 'column', 'create_index': 'index',
                'change_field_type': 'field', 'change_parent': 'cdo', 'change_storage_category': 'cdo',
                'bind_event': 'event_binding', 'replace_event_binding': 'event_binding', 'unbind_event': 'event_binding',
                'add_field_map': 'field_map', 'update_field_map': 'field_map', 'remove_field_map': 'field_map',
                'add_query_text': 'query_text', 'sync_query_parameters': 'query',
                'add_clf_function': 'clf_function', 'replace_clf_function': 'clf_function', 'remove_clf_function': 'clf_function',
                'remove_field_override': 'field', 'reorder_clf_functions': 'clf', 'set_clf_parameter': 'clf_parameter'}
LABELS = {'patch': '修改', 'delete': '删除', 'external_edit': '同步修改', 'restore_mdb': '恢复 MDB',
          'change_field_type': '更换字段类型', 'change_parent': '更换父对象', 'change_storage_category': '更换存储分类',
          'bind_event': '绑定事件', 'replace_event_binding': '替换事件绑定', 'unbind_event': '解除事件绑定',
          'add_field_map': '新增字段映射', 'update_field_map': '修改字段映射', 'remove_field_map': '删除字段映射',
          'add_clf_function': '新增函数调用', 'replace_clf_function': '替换函数调用', 'remove_clf_function': '删除函数调用',
          'remove_field_override': '恢复继承字段', 'reorder_clf_functions': '调整调用顺序',
          'set_clf_parameter': '修改调用参数', 'sync_query_parameters': '同步查询参数'}


def kind(op):
    return op.get('kind') or ACTION_KINDS.get(op.get('action'), '')


def summary(operations, affected=()):
    """Track final names and deletions, while retaining every change in the UI."""
    objects, removed, fields, rows, aliases = [], set(), set(), [], {}
    def current(name):
        seen = set()
        while name in aliases and name not in seen:
            seen.add(name); name = aliases[name]
        return name
    for op in operations:
        target_kind, action = kind(op), op.get('action', '')
        owner, name = current(op.get('owner', '')), op.get('name', '')
        if target_kind == 'cdo':
            name = current(name)
            renamed = next((op.get('changes', {}).get(key) for key in ('Name',) if key in op.get('changes', {})), None)
            if renamed:
                aliases[name] = renamed
                objects = [renamed if item == name else item for item in objects]
                fields = {(renamed if item_owner == name else item_owner, item_name) for item_owner, item_name in fields}
                name = renamed
            owner = name
            if action == 'delete': removed.add(owner)
            else: removed.discard(owner)
        elif target_kind in {'field', 'event_binding'}:
            owner = current(op.get('owner', ''))
        elif target_kind == 'field_map':
            for item in (op.get('source_cdo'), op.get('target_cdo')):
                if item and current(item) not in objects: objects.append(current(item))
            owner = ''
        else:
            owner = ''
        if owner and owner not in objects: objects.append(owner)
        if target_kind == 'field':
            final_name = op.get('changes', {}).get('FieldName') or op.get('changes', {}).get('Name') or name
            fields.add((owner, final_name))
        rows.append({'owner': owner or op.get('owner') or op.get('map', ''), 'name': name or op.get('event') or op.get('function', ''),
                     'kind': target_kind, 'action': action,
                     'label': LABELS.get(action, '新增' if action in CREATE_ACTIONS else '修改'),
                     'field_type': op.get('field_type', ''), 'deleted': action in DELETE_ACTIONS})
    objects.extend(item for item in affected if item and current(item) not in objects)
    for row in rows:
        row['owner'] = current(row['owner'])
        if row['kind']=='cdo': row['name']=current(row['name'])
    return {'objects': list(dict.fromkeys(current(item) for item in objects)),
            'service_objects': list(dict.fromkeys(current(item) for item in objects if current(item) not in removed)),
            'field_count': len(fields), 'change_count': len(operations), 'rows': rows}
