"""Reject misleading plans before SDK execution and keep private types distinct."""
import json

import pytest

from designer.design_contract import cdo_operations, private_field_type_name, validate_calls
from designer.vendor import validate_operations
from agent import excel_preview


def test_private_types_are_scoped_by_owner_and_names_are_stable():
    def types(owner):
        return [op['name'] for op in cdo_operations(owner, 'NamedDataObject', [
            {'name': 'CreatedAt', 'data_type': 'TimeStamp'},
            {'name': 'Code', 'data_type': 'String', 'max_length': 24}]) if op['action'] == 'create_field_type']
    assert types('ExFirst') == ['ExFirst_CreatedAt_Type', 'ExFirst_Code_24']
    assert set(types('ExFirst')).isdisjoint(types('ExSecond'))
    assert types('ExFirst') == types('ExFirst')
    first = private_field_type_name('ExCustomerComplaintLong', 'ExDescriptionLong', 'String', 500)
    second = private_field_type_name('ExCustomerComplaintLong', 'ExDescriptionLonger', 'String', 500)
    assert len(first) == len(second) == 30 and first != second
    assert first.casefold() == private_field_type_name('EXCUSTOMERCOMPLAINTLONG', 'EXDESCRIPTIONLONG', 'String', 500).casefold()
    assert private_field_type_name('ExOwner', 'Code1', 'String', 2) != private_field_type_name('ExOwner', 'Code', 'String', 12)


def test_existing_type_expectations_survive_into_vendor_plan():
    ops = cdo_operations('ExObject', 'NamedDataObject', [
        {'name': 'Quantity', 'field_type': 'ExQuantity', 'data_type': 'Decimal', 'precision': 18, 'scale': 3}])
    assert len(ops) == 2
    assert ops[-1]['expected_type'] == {'data_type': 'Decimal', 'precision': 18, 'scale': 3}
    validate_operations(ops)


@pytest.mark.parametrize('field,match', [
    ({'name': 'Code', 'data_type': 'String', 'max_lenght': 24}, '未知参数'),
    ({'name': 'Code', 'field_type': 'Name', 'persistant': True}, '未知参数'),
    ({'name': 'Code', 'field_type': ''}, '非空名称'),
    ({'name': 'Code', 'field_type': 12}, '非空名称'),
])
def test_specialized_fields_never_ignore_typo_or_invalid_reference(field, match):
    with pytest.raises(ValueError, match=match):
        cdo_operations('ExObject', 'NamedDataObject', [field])


def test_case_insensitive_same_owner_duplicate_is_rejected_before_sdk():
    with pytest.raises(ValueError, match='字段名称重复'):
        cdo_operations('ExObject', 'NamedDataObject', [
            {'name': 'Code', 'field_type': 'Name'}, {'name': 'code', 'field_type': 'Name'}])


@pytest.mark.parametrize('options', [{'create_table': 0}, {'create_maintenance': 'false'}, {'create_revision_base': None}])
def test_false_like_options_are_not_silently_dropped(options):
    with pytest.raises(ValueError, match='布尔值'):
        cdo_operations('ExObject', 'NamedDataObject', [{'name': 'Code', 'field_type': 'Name'}], **options)


@pytest.mark.parametrize('options', [{'table_name': 0}, {'table_description': None}])
def test_false_like_table_parameters_are_not_silently_dropped(options):
    with pytest.raises(ValueError, match='字符串'):
        cdo_operations('ExObject', 'NamedDataObject', [{'name': 'Code', 'field_type': 'Name'}], **options)


@pytest.mark.parametrize('operation,match', [
    ({'action': 'create_cdo', 'name': 'ExObject', 'parent': 'NamedDataObject', 'create_tables': True}, '未知参数'),
    ({'action': 'add_field', 'name': 'Code', 'owner': 'ExObject', 'field_type': 'Name', 'persistant': True}, '未知参数'),
    ({'action': 'create_field_type', 'name': 'ExNumber', 'data_type': 'Decimal', 'precision': True}, 'precision'),
    ({'action': 'create_field_type', 'name': 'ExNumber', 'data_type': 'Decimal', 'precision': 18.2}, 'precision'),
    ({'action': 'create_field_type', 'name': 'ExNumber', 'data_type': 'Decimal', 'scale': -1}, 'scale'),
    ({'action': 'create_field_type', 'name': 'ExNumber', 'data_type': 'Decimal', 'precision': 3, 'scale': 4}, 'scale'),
    ({'action': 'create_field_type', 'name': 'ExNumber', 'data_type': 'Integer', 'max_length': 18}, '仅用于 String'),
    ({'action': 'create_field_type', 'name': 'ExNumber'}, 'data_type'),
    ({'action': 'create_cdo', 'name': 'ExObject', 'parent': 'NamedDataObject', 'create_table': True}, 'table_description'),
    ({'action': 'create_cdo', 'name': 'ExObject', 'parent': 'NamedDataObject', 'create_table': True, 'table_description': ' '}, 'table_description'),
    ({'action': 'create_cdo', 'name': 'ExObject', 'parent': 'NamedDataObject', 'table_name': 'T_ExObject'}, 'create_table=true'),
    ({'action': 'create_cdo', 'name': 'ExObject', 'parent': 'NamedDataObject', 'create_table': True,
      'table_name': 'x'*31, 'table_description': 'Object'}, '表名'),
    ({'action': 'add_field', 'name': 'Code', 'owner': 'ExObject', 'field_type': 'Name',
      'expected_type': {'scale': False}}, 'scale'),
    ({'action': 'add_field', 'name': 'Code', 'owner': 'ExObject', 'field_type': 'Name',
      'expected_type': {'max_length': 40}}, 'expected_type'),
])
def test_creation_edges_rejected_before_backend(operation, match):
    with pytest.raises(ValueError, match=match):
        validate_operations([operation])


def test_chat_validation_uses_same_field_contract():
    args = {'mdb_file': 'source.mdb', 'expected_sha256': 'a'*64, 'cdo_name': 'ExObject',
            'parent_cdo': 'NamedDataObject', 'fields': [{'name': 'Code', 'field_type': 'Name', 'is_lsit': True}]}
    with pytest.raises(ValueError, match='未知参数'):
        validate_calls([{'function': {'name': 'generate_designer_cdo_package', 'arguments': json.dumps(args)}}])


def test_preview_shows_the_actual_generated_type_reference():
    args = {'mdb_file': 'source.mdb', 'expected_sha256': 'a'*64, 'cdo_name': 'ExObject',
            'parent_cdo': 'NamedDataObject', 'fields': [{'name': 'CreatedAt', 'data_type': 'TimeStamp'}]}
    preview = excel_preview.build([{'id': 'preview', 'function': {
        'name': 'generate_designer_cdo_package', 'arguments': json.dumps(args)}}], [])
    field = next(row for row in preview['rows'] if row['action'] == '新增字段')
    assert field['details']['field_type'] == 'ExObject_CreatedAt_Type'
    assert field['data_type'] == 'TimeStamp'


@pytest.mark.parametrize('value', [None, True, 42, ['Code']])
def test_invalid_identifier_types_have_a_clear_validation_error(value):
    with pytest.raises(ValueError, match='标识符'):
        validate_operations([{'action': 'create_cdo', 'name': value, 'parent': 'NamedDataObject'}])


def test_field_type_only_plan_is_preflighted_before_preview_and_save(tmp_path, monkeypatch):
    import config
    from designer import vendor
    from designer.design_contract import inspect_calls
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    source = tmp_path/'source.mdb'; source.write_bytes(b'unchanged')
    operations = [{'action': 'create_field_type', 'name': 'ExExistingType', 'data_type': 'Integer'}]
    report = {'ready': False, 'objects': [], 'conflicts': [{'name': 'ExExistingType', 'message': '类型已存在'}]}
    calls = []
    def preflight(*args):
        calls.append(args)
        return report
    monkeypatch.setattr(vendor, 'preflight', preflight)
    monkeypatch.setattr(vendor, 'run', lambda *args: pytest.fail('rejected type must not be saved'))
    args = {'mdb_file': str(source), 'expected_sha256': vendor.digest(source), 'operations': operations}
    inspected = inspect_calls([{'id': 'type', 'function': {
        'name': 'generate_designer_design_package', 'arguments': json.dumps(args)}}])
    assert inspected[0]['ready'] is False and inspected[0]['call_id'] == 'type'
    with pytest.raises(ValueError, match='类型已存在'):
        vendor.design(str(source), vendor.digest(source), '', operations)
    assert len(calls) == 2 and source.read_bytes() == b'unchanged'
