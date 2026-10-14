"""Creation edge cases checked against an isolated installed Designer SDK graph."""
import pytest

from designer import vendor
from designer.design_contract import cdo_operations
from tests.test_designer_creation_preflight import installed_sdk


def test_owner_scoped_long_private_types_pass_real_factories(installed_sdk):
    operations = []
    for owner in ('ExPrivateTypeOwnerAlpha', 'ExPrivateTypeOwnerBeta'):
        operations += cdo_operations(owner, 'NamedDataObject', [
            {'name': 'CreatedAt', 'data_type': 'TimeStamp'},
            {'name': 'ExMeasurementLabel', 'data_type': 'String', 'max_length': 80}])
    names = [op['name'] for op in operations if op['action'] == 'create_field_type']
    assert len(set(names)) == 4 and all(len(name) == 30 for name in names)
    result = vendor.preflight(str(installed_sdk), vendor.digest(installed_sdk), operations)
    assert result['ready'] and not result['conflicts']


@pytest.mark.parametrize('expectation,reason', [
    ({'data_type': 'String'}, '数据类型不符'),
    ({'precision': 18}, '长度/精度不符'),
    ({'scale': 3}, '小数位不符'),
])
def test_existing_field_type_expectations_fail_before_creating_field(installed_sdk, expectation, reason):
    result = vendor.preflight(str(installed_sdk), vendor.digest(installed_sdk), [
        {'action': 'create_cdo', 'name': 'ExExpectedTypeProbe', 'parent': 'NamedDataObject'},
        {'action': 'add_field', 'owner': 'ExExpectedTypeProbe', 'name': 'ExProbeStatus',
         'field_type': '[Integer]', 'expected_type': expectation},
    ])
    assert not result['ready']
    assert any(reason in conflict['message'] for conflict in result['conflicts'])


def test_matching_existing_field_type_expectations_are_read_only(installed_sdk):
    result = vendor.preflight(str(installed_sdk), vendor.digest(installed_sdk), [
        {'action': 'create_cdo', 'name': 'ExExpectedTypeProbe', 'parent': 'NamedDataObject'},
        {'action': 'add_field', 'owner': 'ExExpectedTypeProbe', 'name': 'ExProbeStatus',
         'field_type': '[Integer]', 'expected_type': {'data_type': 'Integer', 'precision': 0, 'scale': 0}},
    ])
    assert result['ready'] and not result['conflicts']


@pytest.mark.parametrize('length,compatible', [(40, True), (80, False)])
def test_existing_global_type_collision_reports_actual_shape(installed_sdk, length, compatible):
    result = vendor.preflight(str(installed_sdk), vendor.digest(installed_sdk), [
        {'action': 'create_field_type', 'name': 'Name', 'data_type': 'String', 'max_length': length},
    ])
    assert not result['ready']
    conflict = result['conflicts'][0]
    assert conflict['kind'] == 'field_type_name'
    assert conflict['existing_type']['data_type'] == 'String'
    assert conflict['existing_type']['precision'] == 40
    assert conflict['existing_type']['workspace']
    assert conflict['requested_type']['precision'] == length
    assert conflict['requested_type']['scale'] is None
    assert conflict['compatible_shape'] is compatible
    assert 'Name' in vendor.preflight_error(result)


def test_same_batch_global_type_collision_is_structured(installed_sdk):
    result = vendor.preflight(str(installed_sdk), vendor.digest(installed_sdk), [
        {'action': 'create_field_type', 'name': 'ExSameBatchTypeProbe', 'data_type': 'String', 'max_length': 24},
        {'action': 'create_field_type', 'name': 'exsamebatchtypeprobe', 'data_type': 'String', 'max_length': 30},
    ])
    assert not result['ready']
    conflict = result['conflicts'][0]
    assert conflict['operation'] == 2 and conflict['kind'] == 'field_type_name'
    assert conflict['existing_type']['precision'] == 24
    assert conflict['existing_type']['workspace'] == result['workspace']
    assert conflict['compatible_shape'] is False


@pytest.mark.parametrize('description', ['', '   '])
def test_bridge_rejects_empty_table_description_before_factory(installed_sdk, tmp_path, description):
    # Exercise the bridge's own guard as well as Python's public validation.
    operations = [{'action': 'create_cdo', 'name': 'ExNoDescriptionProbe', 'parent': 'NamedDataObject',
                   'create_table': True, 'table_name': 'T_ExNoDescriptionProbe', 'table_description': description}]
    with pytest.raises(ValueError, match='table_description'):
        vendor.validate_operations(operations)
    folder = tmp_path / 'bridge_preflight'; folder.mkdir()
    result = vendor.run(folder, {'mode': 'preflight', 'mdb': str(installed_sdk), 'workspace': '200',
                                 'operations': operations})
    assert not result['ready'] and not result['objects']
    assert 'table_description' in result['conflicts'][0]['message']
