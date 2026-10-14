"""Field type revisions must not poison object-name constraints or old chats."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent import chat_context, langgraph_runtime as runtime
from tests.test_excel_attachments import isolated_store, api, upload, revision_runtime
from tests.test_chat_optimization import text_stream


def test_data_type_definition_name_and_existing_reference_have_distinct_validation():
    from designer import vendor
    vendor.validate_operations([{'action': 'create_field_type', 'name': 'CC_Status_Int', 'data_type': 'Integer'}])
    vendor.validate_operations([{'action': 'add_field', 'owner': 'CustomerComplaint', 'name': 'Status', 'field_type': '[Integer]'}])
    with pytest.raises(ValueError, match='保留字'):
        vendor.validate_operations([{'action': 'create_field_type', 'name': 'Integer', 'data_type': 'Integer'}])


def task_plan():
    return {'requirements': ['创建 CustomerComplaint'], 'validated_plan': [{
        'tool': 'generate_designer_cdo_package', 'arguments': {
            'cdo_name': 'CustomerComplaint', 'parent_cdo': 'NamedDataObject',
            'fields': [{'name': 'Status', 'data_type': 'String', 'max_length': 20}]}}]}


@pytest.mark.parametrize('text', [
    'Status 改成Integer类型', 'Status 改成 [Integer] 类型', '字段 Status 改为 StatusCode',
    '表名 T_CustomerComplaint 改为 T_Complaint', '父对象 NamedDataObject 改为 Product',
    'CustomerComplaint 的字段 Status 改为 Integer 类型',
])
def test_non_object_edits_never_become_object_name_constraints(text):
    task = chat_context.update_task(task_plan(), text, [])
    assert 'required_object_name' not in task
    assert task['requirements'][-1] == text


@pytest.mark.parametrize('text,expected', [
    ('CustomerComplaint改为CustomerComplaint2', 'CustomerComplaint2'),
    ('对象 CustomerComplaint 改为 Complaint', 'Complaint'),
    ('CDO AssetTrace_6381换成6382', 'AssetTrace_6382'),
])
def test_real_object_rename_remains_enforced(text, expected):
    task = chat_context.update_task(task_plan(), text, [])
    assert task['required_object_name'] == expected
    after = chat_context.update_task(task, 'Status 改成Integer类型', [])
    assert after['required_object_name'] == expected


def test_legacy_poison_is_repaired_on_continue_without_changing_plan():
    old = task_plan()
    old.update(required_object_name='Integer')
    old['requirements'] += ['Status 改成Integer类型', '失败的原因是什么，请帮我继续']
    original = deepcopy(old)
    fixed = chat_context.update_task(old, '继续', [])
    assert 'required_object_name' not in fixed
    assert fixed['validated_plan'] == old['validated_plan'] and old == original
    assert chat_context.update_task(fixed, '', []) == fixed


def test_legacy_real_rename_is_preserved_even_when_plan_already_uses_new_name():
    old = task_plan()
    old['requirements'] += ['CustomerComplaint 改成 Complaint']
    old['required_object_name'] = 'Complaint'
    old['validated_plan'][0]['arguments']['cdo_name'] = 'Complaint'
    assert chat_context.update_task(old, '继续', [])['required_object_name'] == 'Complaint'


def test_integer_field_revision_produces_new_preview_without_write(api, revision_runtime, monkeypatch):
    calls, _ = revision_runtime
    requests = []
    async def model(**kwargs):
        requests.append(kwargs['messages'])
        latest = next(m['content'] for m in reversed(kwargs['messages']) if m['role'] == 'user')
        integer = 'Integer' in latest
        fields = [{'name': 'Status', **({'field_type': '[Integer]'} if integer else
                  {'data_type': 'String', 'max_length': 20})},
                  {'name': 'ComplaintCode', 'data_type': 'String', 'max_length': 24}]
        args = {'mdb_file': 'base.mdb', 'expected_sha256': 'a' * 64, 'workspace': '200',
                'cdo_name': 'CustomerComplaint', 'parent_cdo': 'NamedDataObject',
                'fields': fields, 'create_table': True, 'table_name': 'T_CustomerComplaint',
                'table_description': 'Customer complaint'}
        return text_stream(calls=[SimpleNamespace(index=0, id=f'plan-{len(requests)}',
            function=SimpleNamespace(name='generate_designer_cdo_package', arguments=json.dumps(args)))])
    monkeypatch.setattr(runtime._client.chat.completions, 'create', model)
    sid = api.post('/sessions/user/new').json()['session_id']
    file = upload(api, sid).json()
    api.post('/chat', json={'username': 'user', 'session_id': sid, 'message': '按附件创建', 'attachment_ids': [file['id']]})
    old = api.get(f'/history/user?session_id={sid}').json()['pending_excel_preview']
    response = api.post('/chat', json={'username': 'user', 'session_id': sid, 'message': 'Status 改成Integer类型'})
    new = api.get(f'/history/user?session_id={sid}').json()['pending_excel_preview']
    assert new and new['id'] != old['id'] and not calls
    assert '对象名称未采用' not in response.text
    assert next(row for row in new['rows'] if row['name'] == 'Status')['data_type'] == '[Integer]'
    assert new['objects'] == old['objects'] and new['targets'] == old['targets']
    assert next(row for row in new['rows'] if row['name'] == 'ComplaintCode') == next(row for row in old['rows'] if row['name'] == 'ComplaintCode')
