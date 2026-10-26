"""Declaration lifecycle contracts and optional real installed-SDK regressions."""
import os
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from designer import clf, vendor
from designer.clf_parameters import Declaration, values
from designer.operations import summary
from tools.designer_clf import _operation


DEFINITION = Declaration(Name='Input', Direction='In', DataType='String',
                         ParamMultiplicity='0..1', TypeMultiplicity='Scalar').model_dump()


@pytest.mark.parametrize('action', ['add_clf_declaration', 'update_clf_declaration', 'remove_clf_declaration'])
def test_strict_declaration_contract_and_operation_accounting(action):
    op = {'action': action, 'owner': 'Ex_Logic'}
    if action != 'add_clf_declaration':
        op.update(parameter='Input', expected=DEFINITION)
    if action != 'remove_clf_declaration':
        op['definition'] = DEFINITION
    assert _operation.validate_python(op).action == action
    vendor.validate_operations([op])
    normalized = vendor.normalized_operations([op])[0]
    if 'expected' in normalized:
        assert set(normalized['expected']) == set(Declaration.model_fields)
    if 'definition' in op:
        minimal = {**op, 'definition': {key: value for key, value in DEFINITION.items()
                                      if key in {'Name', 'Direction', 'DataType', 'ParamMultiplicity', 'TypeMultiplicity'}}}
        assert vendor.normalized_operations([minimal])[0]['definition']['Notes'] == ''
        assert 'Notes' not in minimal['definition']
    assert action in clf.PLAN_ACTIONS
    assert summary([op])['rows'][0]['deleted'] == (action == 'remove_clf_declaration')
    with pytest.raises(ValueError):
        vendor.validate_operations([{**op, 'FunctionID': 123}])


@pytest.mark.parametrize('changes', [{'Name': 'bad name'}, {'Direction': 'InOut'},
    {'DataType': 'String', 'CDOType': 'Product'}, {'ParamMultiplicity': 'any'}, {'TypeMultiplicity': 'any'}])
def test_invalid_declarations_cannot_reach_vendor(changes):
    with pytest.raises(ValueError):
        vendor.validate_operations([{'action': 'add_clf_declaration', 'owner': 'Ex_Logic',
                                     'definition': {**DEFINITION, **changes}}])


def test_generic_patch_cannot_bypass_declaration_lifecycle():
    with pytest.raises(ValueError, match='专用'):
        vendor.validate_operations([{'action': 'patch', 'kind': 'clf_parameter', 'owner': 'Ex_Logic',
            'name': 'Input', 'expected': {'Direction': 'In'}, 'changes': {'Direction': 'Out'}}])
    assert values({'Name': 'Input', 'Notes': None})['Notes'] == ''
    with pytest.raises(ValueError, match='专用'):
        vendor.validate_operations([{'action': 'delete', 'kind': 'clf_parameter', 'owner': 'Ex_Logic', 'name': 'Input'}])


@pytest.fixture
def sdk(tmp_path):
    if not os.getenv('DESIGNER_TEST_VENDOR'):
        pytest.skip('requires installed vendor SDK and DESIGNER_TEST_MDB')
    original = Path(os.environ['DESIGNER_TEST_MDB']).resolve()
    checksum = vendor.digest(original)
    source = tmp_path/'private.mdb'
    shutil.copyfile(original, source)
    def call(mode, **payload):
        folder = tmp_path/uuid4().hex
        folder.mkdir()
        return vendor.run(folder, {'mode': mode, 'mdb': str(source), 'workspace': '200', **payload})
    call.private_mdb = source
    yield call
    assert vendor.digest(original) == checksum


def add(name='Input'):
    return {'action': 'add_clf_declaration', 'owner': 'Ex_DeclarationTest',
            'definition': {**DEFINITION, 'Name': name}}


def read(sdk):
    return sdk('get', kind='clf', name='Ex_DeclarationTest', owner='attached_parameters')


def test_sdk_declaration_save_reload_rename_type_direction_multiplicity_and_delete(sdk):
    result = sdk('apply', operations=[{'action': 'copy_clf', 'name': 'Ex_DeclarationTest', 'template': 'ES_ValidateProduct'}, add()])
    assert result['reload_verified']
    original = read(sdk)
    assert original['declaration_editor']['impact']['structural_edit_allowed']
    declared = original['declared_parameters'][0]
    updated = {**values(declared), 'Name': 'Output', 'Direction': 'In/Out', 'DataType': 'Object',
               'CDOType': 'Product', 'ParamMultiplicity': '0..*', 'TypeMultiplicity': 'ListOrScalar',
               'FunctionParmUsage': 'CDOName', 'Description': 'A typed declaration', 'Notes': 'SDK acceptance'}
    result = sdk('apply', operations=[{'action': 'update_clf_declaration', 'owner': 'Ex_DeclarationTest',
        'parameter': 'Input', 'expected': values(declared), 'definition': updated}])
    assert result['reload_verified']
    actual = read(sdk)['declared_parameters'][0]
    assert values(actual) == updated
    assert actual['FunctionParamID'] == declared['FunctionParamID']
    assert actual['CDODefId'] > 0 and actual['DataTypeId'] == 5
    assert sdk('apply', operations=[{'action': 'copy_clf', 'name': 'Ex_CopiedDeclaration', 'template': 'Ex_DeclarationTest'},
        {'action': 'update_clf_declaration', 'owner': 'Ex_CopiedDeclaration', 'parameter': 'Output',
         'expected': updated, 'definition': {**updated, 'Name': 'CopiedOutput'}}])['reload_verified']
    copied = sdk('get', kind='clf', name='Ex_CopiedDeclaration')['declared_parameters'][0]
    assert copied['Name'] == 'CopiedOutput' and copied['FunctionParamID'] != actual['FunctionParamID']
    with pytest.raises(ValueError, match='原值已变化'):
        sdk('apply', operations=[{'action': 'remove_clf_declaration', 'owner': 'Ex_DeclarationTest',
            'parameter': 'Output', 'expected': values(declared)}])
    result = sdk('apply', operations=[{'action': 'remove_clf_declaration', 'owner': 'Ex_DeclarationTest',
        'parameter': 'Output', 'expected': values(actual)}])
    assert result['reload_verified'] and read(sdk)['declared_parameters'] == []


def test_sdk_binding_and_expression_references_block_unsafe_structural_edits(sdk):
    sdk('apply', operations=[{'action': 'copy_clf', 'name': 'Ex_DeclarationTest', 'template': 'ES_ValidateProduct'}, add()])
    original = read(sdk)
    declared = values(original['declared_parameters'][0])
    call = original['functions'][0]
    parameter = call['parameters'][0]
    sdk('apply', operations=[{'action': 'set_clf_parameter', 'owner': 'Ex_DeclarationTest',
        'call_id': call['CLFFunctionID'], 'parameter': parameter['Name'],
        'expected_value': parameter['ValueExpression'], 'value': 'CLF::Input'}])
    with pytest.raises(ValueError, match='仍引用'):
        sdk('apply', operations=[{'action': 'remove_clf_declaration', 'owner': 'Ex_DeclarationTest',
            'parameter': 'Input', 'expected': declared}])
    sdk('apply', operations=[{'action': 'create_cdo', 'name': 'Ex_DeclarationOwner', 'parent': 'ShopFloor'},
        {'action': 'bind_event', 'owner': 'Ex_DeclarationOwner', 'event': 'BeforeInitialize',
         'clf': 'Ex_DeclarationTest', 'feature': 'Base'}])
    impact = read(sdk)['declaration_editor']['impact']
    assert impact['binding_count'] and not impact['structural_edit_allowed']
    result = sdk('preflight', operations=[add('AnotherInput')])
    assert not result['ready'] and '迁移方案' in result['conflicts'][0]['message']
    updated = {**declared, 'Description': 'Documentation only'}
    assert sdk('apply', operations=[{'action': 'update_clf_declaration', 'owner': 'Ex_DeclarationTest',
        'parameter': 'Input', 'expected': declared, 'definition': updated}])['reload_verified']
    assert read(sdk)['declared_parameters'][0]['Description'] == updated['Description']


def test_sdk_function_parameter_alias_cannot_bypass_lifecycle(sdk):
    sdk('apply', operations=[{'action': 'copy_clf', 'name': 'Ex_DeclarationTest', 'template': 'ES_ValidateProduct'}, add()])
    with pytest.raises(ValueError, match='别名'):
        sdk('apply', operations=[{'action': 'patch', 'kind': 'function_parameter',
            'owner': 'CLF Parameters for Ex_DeclarationTest', 'name': 'Input',
            'changes': {'Direction': 'Out'}, 'expected': {'Direction': 'In'}}])
