"""Customer-sized field sets survive validation, preview and SDK round trips."""
import asyncio
import json
import shutil

import pytest

from agent import excel_preview
from designer import catalog, vendor
from designer.design_contract import cdo_operations
from tests.test_designer_creation_preflight import installed_sdk
from tools import designer_design


def customer_fields(count):
    return [{'name': f'ExField{i:03}', 'data_type': 'String', 'max_length': 80,
             'description': f'Customer field {i}'} for i in range(count)]


@pytest.mark.parametrize('count', [200, 300, 501])
@pytest.mark.parametrize('tool_name', ['generate_designer_cdo_package', 'generate_designer_design_package'])
def test_all_customer_fields_reach_preview_and_design(monkeypatch, count, tool_name):
    fields = customer_fields(count)
    args = {'mdb_file': 'source.mdb', 'expected_sha256': 'a' * 64, 'workspace': '200'}
    if tool_name == 'generate_designer_cdo_package':
        args.update(cdo_name='ExLargeCustomer', parent_cdo='NamedDataObject', fields=fields)
    else:
        args['operations'] = cdo_operations('ExLargeCustomer', 'NamedDataObject', fields)
    call = {'id': 'large-plan', 'function': {'name': tool_name, 'arguments': json.dumps(args)}}
    preview = excel_preview.build([call], [])
    rows = [row for row in preview['rows'] if row['action'] == '新增字段']
    assert preview['field_count'] == len(rows) == count
    assert [row['name'] for row in rows] == [field['name'] for field in fields]
    assert all(row['max_length'] == 80 for row in rows)
    captured = []

    def design(mdb_file, checksum, workspace, operations):
        vendor.validate_operations(operations)
        captured.extend(operations)
        return {'status': 'saved_to_test_copy_and_exported'}

    monkeypatch.setattr(vendor, 'design', design)
    asyncio.run(getattr(designer_design, tool_name)(**args))
    assert len(captured) == 1 + 2 * count
    additions = [op for op in captured if op['action'] == 'add_field']
    assert [op['name'] for op in additions] == [field['name'] for field in fields]
    assert {op['field_type'] for op in additions} == {
        op['name'] for op in captured if op['action'] == 'create_field_type'}


@pytest.mark.parametrize('value', [[], None, {}, 'fields', True])
def test_removing_count_caps_still_rejects_invalid_lists(value):
    with pytest.raises(ValueError, match='非空字段定义列表'):
        cdo_operations('ExLargeCustomer', 'NamedDataObject', value)
    with pytest.raises(ValueError, match='非空设计操作列表'):
        vendor.validate_operations(value)


def test_large_plans_still_validate_the_last_field_and_operation():
    fields = customer_fields(300)
    fields[-1]['persistant'] = True
    with pytest.raises(ValueError, match='未知参数'):
        cdo_operations('ExLargeCustomer', 'NamedDataObject', fields)
    operations = cdo_operations('ExLargeCustomer', 'NamedDataObject', customer_fields(300))
    operations[-1]['persistent'] = 'false'
    with pytest.raises(ValueError, match='布尔值'):
        vendor.validate_operations(operations)


@pytest.mark.parametrize('count', [200, 300, 501])
def test_large_entity_pages_are_accepted(count):
    catalog.bounds(0, count)


def test_installed_sdk_returns_all_inherited_fields(installed_sdk):
    result = vendor.query(str(installed_sdk), 'get', 'cdo', 'Product')
    assert result['total_fields'] > 100
    assert len(result['fields']) == result['total_fields']
    assert result['fields_truncated'] is False


def test_installed_sdk_saves_and_reloads_300_customer_fields(installed_sdk, tmp_path):
    fields = customer_fields(300)
    operations = cdo_operations('ExLargeCustomer', 'NamedDataObject', fields)
    before = vendor.digest(installed_sdk)
    preflight = vendor.preflight(str(installed_sdk), before, operations)
    assert preflight['ready'] and not preflight['conflicts']
    # Only the disposable copy is saved; the fixture verifies the input hashes.
    modified = tmp_path / 'large.mdb'
    shutil.copyfile(installed_sdk, modified)
    folder = tmp_path / 'large-apply'
    folder.mkdir()
    saved = vendor.run(folder, {'mode': 'apply', 'mdb': str(modified),
                               'workspace': preflight['workspace'], 'operations': operations})
    assert saved['reload_verified'] is True
    expected = {field['name'] for field in fields}
    result = vendor.query(str(modified), 'get', 'cdo', 'ExLargeCustomer')
    assert len(result['fields']) == result['total_fields'] >= 300
    assert result['fields_truncated'] is False
    assert expected <= {field['name'] for field in result['fields']}
    page = vendor.query(str(modified), 'list', 'field', owner='ExLargeCustomer', limit=300)
    assert len(page['records']) == 300
    remaining = vendor.query(str(modified), 'list', 'field', owner='ExLargeCustomer', offset=300, limit=300)
    all_fields = page['records'] + remaining['records']
    assert len(all_fields) == page['total'] == result['total_fields']
    assert expected <= {field['name'] for field in all_fields}
    last = vendor.query(str(modified), 'get', 'field', fields[-1]['name'], owner='ExLargeCustomer')
    assert last['FieldDefinition']['name'] == operations[-1]['field_type']
    assert vendor.digest(installed_sdk) == before
