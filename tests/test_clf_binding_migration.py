"""Explicit migration must preserve actual entry overrides, identities and scope."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from designer import clf, vendor
from designer.operations import summary
from tests.test_clf_declarations import sdk, DEFINITION
from tests.test_chat_presentation import node, ROOT
from tools.designer_clf import _operation


OPERATION = {'action': 'migrate_clf_binding', 'owner': 'Ex_Entry', 'event': 'BeforeInitialize',
             'binding_id': 100, 'expected_clf': 'Ex_Old', 'clf': 'Ex_New', 'expected_feature_id': 0,
             'expected_parameters': [{'value_id': 10, 'call_id': 20, 'parameter_id': 30,
                                      'parameter': 'Condition', 'value': '', 'dynamic': False}]}


def test_binding_migration_is_strict_and_uses_existing_review_accounting():
    vendor.validate_operations([OPERATION])
    assert _operation.validate_python(OPERATION).action == 'migrate_clf_binding'
    assert vendor.normalized_operations([OPERATION])[0]['field'] == ''
    assert 'field' not in OPERATION
    assert 'migrate_clf_binding' in clf.PLAN_ACTIONS
    assert summary([OPERATION])['rows'][0]['kind'] == 'event_binding'
    assert summary([OPERATION])['objects'] == ['Ex_Entry']


@pytest.mark.parametrize('change', [
    {'clf': 'ex_old'}, {'binding_id': True}, {'expected_feature_id': -1}, {'reviewed': True},
    {'expected_parameters': [OPERATION['expected_parameters'][0]] * 2},
    {'expected_parameters': [{**OPERATION['expected_parameters'][0], 'dynamic': 'false'}]},
    {'expected_parameters': [{**OPERATION['expected_parameters'][0], 'parameter_id': 0}]},
    {'expected_parameters': [{**OPERATION['expected_parameters'][0], 'value': None}]},
    {'expected_parameters': [{**OPERATION['expected_parameters'][0], 'target_value': 'overwrite'}]},
])
def test_unreviewable_or_ambiguous_binding_migrations_are_rejected(change):
    with pytest.raises(ValueError):
        vendor.validate_operations([{**OPERATION, **change}])


def test_declaration_form_migrates_only_explicit_selection_and_rejects_stale_context():
    script = (ROOT/'static/clf-core.js').read_text(encoding='utf-8')
    values = script[script.index('    const declarationKeys='):script.index('    function renderDeclarations(')]
    prepare = script[script.index('    function prepareDeclaration(){'):script.index('    function renderCalls(){')]
    rows = script[script.index('    function planRows(o){'):script.index('    function showPlan(')]
    node("const assert=require('node:assert/strict');\n" + values + rows + r"""
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false});return elements.get(id);};
const original=""" + json.dumps(DEFINITION) + ";const snapshot=" + json.dumps(OPERATION) + r""";
const state={boot:{capabilities:{binding_migration:'customer_entries_preserve_attached_values'}},context:{project_id:'project'},project:{source_sha256:'hash'},planSource:'hash',definition:{name:'Ex_Old'}};
let saved=0,closed=0,selected=[];const savePlanDraft=()=>saved++,setView=()=>{},notice=()=>{};
$('declarationDialog').close=()=>closed++;
$('declarationMigrations').querySelectorAll=()=>selected;
function reset(){
 $('planOperations').value='[]';$('declarationCopy').checked=true;$('declarationTarget').value='Ex_New';
 for(const [field,key] of [['Name','Name'],['Direction','Direction'],['DataType','DataType'],['Multiplicity','ParamMultiplicity'],['TypeMultiplicity','TypeMultiplicity'],['Usage','FunctionParmUsage'],['Description','Description'],['Notes','Notes']])$('declaration'+field).value=original[key];
 state.declarationEdit={action:'update',parameter:{...original,DefaultValue:'preserved default'},project:'project',source:'hash',clf:'Ex_Old',bindings:[{CLFEventMapID:100,migration:{supported:true,operation:snapshot}}]};
 selected=[];
}
""" + prepare + r"""
reset();prepareDeclaration();let ops=JSON.parse($('planOperations').value);
assert.equal(ops.length,2);assert.equal(ops.filter(o=>o.action==='migrate_clf_binding').length,0);
reset();selected=[{dataset:{migrationBinding:'100'}}];prepareDeclaration();ops=JSON.parse($('planOperations').value);
assert.equal(ops.length,3);assert.equal(ops[2].binding_id,100);assert.equal(ops[2].clf,'Ex_New');
assert.equal(ops[1].definition.DefaultValue,'preserved default');assert.equal(snapshot.clf,'Ex_New');
assert.deepEqual(ops[2].expected_parameters,snapshot.expected_parameters);
assert.match(planRows(ops[2])[0][0],/入口 100/);assert.match(planRows(ops[2])[1][0],/调用 20/);
reset();selected=[{dataset:{migrationBinding:'100'}}];$('declarationCopy').checked=false;prepareDeclaration();
assert.equal(JSON.parse($('planOperations').value).length,1);
reset();state.declarationEdit.source='old';const count=saved;assert.throws(()=>prepareDeclaration(),/上下文已变化/);assert.equal(saved,count);
reset();selected=[{dataset:{migrationBinding:'100'}}];state.declarationEdit.bindings[0].migration.supported=false;
assert.throws(()=>prepareDeclaration(),/暂不支持迁移/);assert.equal($('planOperations').value,'[]');
reset();selected=[{dataset:{migrationBinding:'100'}}];$('planOperations').value=JSON.stringify([snapshot]);
assert.throws(()=>prepareDeclaration(),/已在方案中/);assert.equal(JSON.parse($('planOperations').value).length,1);
reset();selected=[{dataset:{migrationBinding:'100'}}];state.boot.capabilities={};
assert.throws(()=>prepareDeclaration(),/后端尚未加载/);assert.equal($('planOperations').value,'[]');
""")


def test_migration_preview_reads_clf_sources_not_entry_cdo_and_preserves_receipt(tmp_path, monkeypatch):
    from tests.test_clf_workspace import mock_reads
    path, checksum = mock_reads(monkeypatch, tmp_path)
    read = []
    monkeypatch.setattr(clf, 'get_clf', lambda path, name, checksum, full: read.append((name, full)) or {'name': name})
    receipt = {'binding_id': 100, 'preserved_parameter_count': 1}
    monkeypatch.setattr(vendor, 'preflight', lambda *args: {'ready': True, 'conflicts': [], 'binding_migrations': [receipt]})
    result = clf.inspect_plan(str(path), checksum, [OPERATION])
    assert read == [('Ex_New', True), ('Ex_Old', True)]
    assert result['binding_migrations'] == [receipt]
    assert not result['saved'] and not result['runtime_verified']


def prepare(sdk):
    original = sdk('get', kind='clf', name='ES_ValidateProduct')
    sdk('apply', operations=[{'action': 'copy_clf', 'name': 'Ex_MigrationSource', 'template': original['name']},
        {'action': 'create_cdo', 'name': 'Ex_MigrationEntryA', 'parent': 'ShopFloor'},
        {'action': 'create_cdo', 'name': 'Ex_MigrationEntryB', 'parent': 'ShopFloor'}])
    source = sdk('get', kind='clf', name='Ex_MigrationSource')
    call = source['functions'][0]
    parameter = call['parameters'][0]
    def expression(old, new):
        return {'action': 'set_clf_parameter', 'owner': source['name'], 'call_id': call['CLFFunctionID'],
                'parameter': parameter['Name'], 'expected_value': old, 'value': new}
    sdk('apply', operations=[expression(parameter['ValueExpression'], 'true'),
        *[{'action': 'bind_event', 'owner': owner, 'event': 'BeforeInitialize',
           'clf': source['name'], 'feature': 'Base'} for owner in ('Ex_MigrationEntryA', 'Ex_MigrationEntryB')]])
    # Existing entry values now differ from the library; reattachment must not reset them.
    sdk('apply', operations=[expression('true', 'false')])
    source = sdk('get', kind='clf', name=source['name'], owner='attached_parameters')
    binding = next(b for b in source['bindings'] if b['owner_name'] == 'Ex_MigrationEntryA')
    # Prepare a real dynamic override through the installed SDK, on the fixture's private MDB only.
    folder = sdk.private_mdb.parent
    script = folder/'dynamic-entry.ps1'
    request = folder/'dynamic-entry.json'
    request.write_text(json.dumps({'dll': str(vendor.assembly()), 'mdb': str(sdk.private_mdb),
                                  'binding': binding['CLFEventMapID']}), encoding='utf-8')
    script.write_text(r"""
param([string]$RequestFile)
$ErrorActionPreference='Stop'
$r=Get-Content -LiteralPath $RequestFile -Raw -Encoding UTF8 | ConvertFrom-Json
[Reflection.Assembly]::LoadFrom($r.dll) | Out-Null
Add-Type -ReferencedAssemblies @($r.dll,'System.Data','System.Core','System.Xml','System.Xml.Linq','System.Windows.Forms') -TypeDefinition @'
using System.Linq;
using Camstar.Metadata;
using Camstar.Metadata.Objects;
public static class PrivateDynamicEntryFixture {
 public static void Prepare(string mdb,int binding) {
  var set=new MDBMetadataSet("Provider=Microsoft.ACE.OLEDB.12.0;Data Source="+mdb,"");
  set.DisableAutoRepair=true;set.isConsole=true;set.ErrorFilePath=System.IO.Path.GetDirectoryName(mdb);
  set.LoadAllMetadata(null);set.WorkSpace="200";
  var map=set.CLFEventMaps[binding];var parameter=map.AllFunctionParameters.Values.First();
  parameter.DynamicParm=true;parameter.SetModifiedStatus(ModifiedStatusEnum.Modified);
  map.ParamsModified=true;map.SetModifiedStatus(ModifiedStatusEnum.Modified);
  var cdo=set.CDODefinitions[map.CDODefID];cdo.EventsModified=true;cdo.SetModifiedStatus(ModifiedStatusEnum.Modified);
  set.SaveAll();
 }
}
'@
[PrivateDynamicEntryFixture]::Prepare($r.mdb,[int]$r.binding)
""", encoding='utf-8-sig')
    shell = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-File', str(script),
                             '-RequestFile', str(request)], capture_output=True, timeout=60,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr
    source = sdk('get', kind='clf', name=source['name'], owner='attached_parameters')
    binding = next(b for b in source['bindings'] if b['owner_name'] == 'Ex_MigrationEntryA')
    assert binding['migration']['supported'], binding['migration']['reason']
    assert any(p['value'] == 'true' for p in binding['migration']['operation']['expected_parameters'])
    assert any(p['dynamic'] for p in binding['migration']['operation']['expected_parameters'])
    return source, binding


def plan(binding):
    return [{'action': 'copy_clf', 'name': 'Ex_MigrationTarget', 'template': 'Ex_MigrationSource'},
            {'action': 'add_clf_declaration', 'owner': 'Ex_MigrationTarget',
             'definition': {**DEFINITION, 'Name': 'NewInput'}},
            {**binding['migration']['operation'], 'clf': 'Ex_MigrationTarget'}]


def test_sdk_selected_binding_preserves_nondefault_values_and_leaves_other_entry_unchanged(sdk):
    source, binding = prepare(sdk)
    operations = plan(binding)
    preview = sdk('preflight', operations=operations)
    assert preview['ready'], preview['conflicts']
    assert preview['binding_migrations'][0]['preserved_parameter_count'] == len(binding['attached_parameters'])
    output = sdk('apply', operations=operations)
    assert output['reload_verified'] and output['binding_migrations'][0]['reload_verified']
    assert 'Ex_MigrationEntryA' in output['affected_cdos']
    target = sdk('get', kind='clf', name='Ex_MigrationTarget', owner='attached_parameters')
    after = sdk('get', kind='clf', name='Ex_MigrationSource', owner='attached_parameters')
    assert after['declared_parameters'] == source['declared_parameters']
    assert len(after['bindings']) == 1 and after['bindings'][0]['owner_name'] == 'Ex_MigrationEntryB'
    old_other = next(b for b in source['bindings'] if b['owner_name'] == 'Ex_MigrationEntryB')
    assert after['bindings'][0]['migration']['operation'] == old_other['migration']['operation']
    migrated = target['bindings'][0]
    assert migrated['CLFEventMapID'] == binding['CLFEventMapID']
    assert migrated['FeatureId'] == binding['FeatureId']
    def values(record, entry):
        sequence = {c['CLFFunctionID']: c['Sequence'] for c in record['functions']}
        return {(sequence[p['CLFFunctionID']], p['FunctionParmID']): (p['ValueExpression'], p['DynamicParm'])
                for p in entry['attached_parameters']}
    assert values(target, migrated) == values(source, binding)
    assert any(p['Name'] == 'NewInput' for p in target['declared_parameters'])


def test_sdk_binding_migration_rejects_stale_values_and_changed_call_graph(sdk):
    _, binding = prepare(sdk)
    operations = plan(binding)
    for change in ('missing', 'value', 'feature', 'graph', 'later_graph'):
        proposed = deepcopy(operations)
        if change == 'missing':
            proposed[-1]['expected_parameters'].pop()
        elif change == 'value':
            proposed[-1]['expected_parameters'][0]['value'] = 'stale'
        elif change == 'feature':
            proposed[-1]['expected_feature_id'] += 1
        else:
            proposed.insert(2 if change == 'graph' else len(proposed),
                {'action': 'add_clf_function', 'owner': 'Ex_MigrationTarget', 'function': 'if', 'sequence': 100})
        preview = sdk('preflight', operations=proposed)
        assert not preview['ready'], change
        assert preview['conflicts'], change
    # A system/other-workspace entry remains ineligible even with manually authored JSON.
    system = sdk('get', kind='clf', name='ES_ValidateProduct', owner='attached_parameters')
    entry = system['bindings'][0]
    assert not entry['migration']['supported']
    preview = sdk('preflight', operations=[operations[0],
        {**entry['migration']['operation'], 'clf': 'Ex_MigrationTarget'}])
    assert not preview['ready']
