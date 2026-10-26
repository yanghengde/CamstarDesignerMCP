"""Direct caller migration requires exact old scope and explicit target expressions."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from designer import clf, vendor
from designer.operations import summary
from tests.test_clf_declarations import sdk, DEFINITION
from tests.test_clf_core import setup
from tests.test_excel_attachments import isolated_store
from tests.test_chat_presentation import node, ROOT
from tools.designer_clf import _operation


PARAMETER = {'value_id': 1, 'call_id': 10, 'parameter_id': 100, 'parameter': 'Input', 'value': '"library"', 'dynamic': True}
BINDING = {'binding_id': 2, 'cdo_id': 30, 'caller_id': 30, 'event_id': 4, 'feature_id': 0,
           'owner': 'Ex_Entry', 'field': '', 'event': 'BeforeInitialize', 'parameters': [
               {**PARAMETER, 'value_id': 3, 'value': '"entry"', 'dynamic': False},
               {**PARAMETER, 'value_id': 4, 'call_id': 20, 'value': '"other call"'}]}
OPERATION = {'action': 'migrate_clf_call', 'owner': 'Ex_Caller', 'call_id': 10,
             'expected_clf': 'Ex_Callee', 'clf': 'Ex_CalleeV2', 'expected_sequence': 1, 'expected_active': False,
             'expected_parameters': [PARAMETER], 'expected_bindings': [BINDING],
             'target_declarations': [{**DEFINITION, 'Name': 'Output', 'Direction': 'In/Out'}],
             'parameters': [{'parameter': 'Output', 'value': '', 'dynamic': True}],
             'bindings': [{'binding_id': 2, 'parameters': [{'parameter': 'Output', 'value': '"entry"', 'dynamic': False}]}]}


def test_complete_direct_call_contract_and_update_accounting():
    vendor.validate_operations([OPERATION])
    assert _operation.validate_python(OPERATION).action == 'migrate_clf_call'
    assert vendor.normalized_operations([OPERATION])[0] == OPERATION
    assert 'migrate_clf_call' in clf.PLAN_ACTIONS
    assert summary([OPERATION])['rows'][0]['kind'] == 'clf_function'


@pytest.mark.parametrize('change', [
    {'clf': 'ex_callee'}, {'clf': 'Ex_Caller'}, {'owner': 'Ex_Callee'}, {'call_id': True},
    {'expected_active': 'false'}, {'force': True}, {'bindings': []}, {'bindings': [OPERATION['bindings'][0]] * 2},
    {'parameters': []}, {'parameters': OPERATION['parameters'] * 2},
    {'parameters': [{**OPERATION['parameters'][0], 'value': None}]},
    {'expected_parameters': [PARAMETER, PARAMETER]},
    {'expected_bindings': [{**BINDING, 'parameters': [PARAMETER, PARAMETER]}]},
    {'target_declarations': OPERATION['target_declarations'] * 2},
    {'target_declarations': [{**OPERATION['target_declarations'][0], 'CDOType': 'Product'}]},
    {'bindings': [{'binding_id': 2, 'parameters': []}]},
])
def test_incomplete_ambiguous_and_untyped_call_migrations_are_rejected(change):
    with pytest.raises(ValueError):
        vendor.validate_operations([{**OPERATION, **change}])


def test_read_is_scoped_to_exact_callee_call_and_source(tmp_path, monkeypatch):
    from tests.test_clf_workspace import mock_reads
    path, checksum = mock_reads(monkeypatch, tmp_path)
    seen = []
    def query(*args, **kwargs):
        seen.append(kwargs)
        return {'caller_migration': {'operation': OPERATION, 'supported': True}}
    monkeypatch.setattr(vendor, 'query', query)
    result = clf.caller_migration(str(path), 'Ex_Callee', 'Ex_Caller', 10, checksum)
    assert result['read_only'] and result['source_sha256'] == checksum
    assert seen == [{'name': 'Ex_Caller', 'owner': 'caller_migration:10'}]
    with pytest.raises(ValueError, match='不再引用'):
        clf.caller_migration(str(path), 'Another', 'Ex_Caller', 10, checksum)
    with pytest.raises(ValueError, match='已更新'):
        clf.caller_migration(str(path), 'Ex_Callee', 'Ex_Caller', 10, '0' * 64)


def test_workbench_reads_and_prepares_call_migration_without_executing(setup, monkeypatch):
    client, context, _, _ = setup
    seen = []
    monkeypatch.setattr(clf, 'caller_migration', lambda *args: seen.append(args) or {'operation': OPERATION, 'read_only': True})
    url='/api/clf/workspace/caller-migration/'+context['project_id']
    params={'username':'user','name':'Ex_Callee','owner':'Ex_Caller','call_id':10,'source_sha256':context['source_sha256']}
    assert client.get(url, params={**params,'username':'other'}).status_code == 404
    assert client.get(url, params={**params,'call_id':0}).status_code == 422
    assert client.get(url, params={**params,'source_sha256':'0'*64}).status_code == 409
    assert not seen
    assert client.get(url, params=params).json()['read_only']
    assert seen[0][1:] == ('Ex_Callee','Ex_Caller',10,context['source_sha256'])
    monkeypatch.setattr(clf,'inspect_plan',lambda *args:{'metadata_preflight_passed':True,'saved':False,'operations':args[2]})
    response=client.post('/api/clf/workspace/plans',json={'username':'user','project_id':context['project_id'],'source_sha256':context['source_sha256'],'operations':[OPERATION]})
    assert response.status_code == 200, response.text
    assert not response.json()['result']['saved']
    assert client.get('/api/clf/workspace/bootstrap?username=user').json()['capabilities']['call_migration']=='explicit_direct_call_and_all_entries_v1'
    assert 'window.ClfCallMigration' in client.get('/clf-core.js').text


def test_caller_ui_keeps_entry_specific_values_renames_and_explicit_selection():
    source = (ROOT/'static/clf-call-migration.js').read_text(encoding='utf-8')
    contract = {'supported': True, 'operation': {k: v for k, v in OPERATION.items() if k not in {'clf','parameters','bindings','target_declarations'}}}
    node(r"""
const assert=require('node:assert/strict');
class Element {constructor(tag){this.tag=tag;this.children=[];this.checked=false;this.value='';}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=[...items];}setAttribute(k,v){this[k]=v;}}
const document={createElement:t=>new Element(t),createTextNode:t=>t},window={};
""" + source + '\nconst contract=' + json.dumps(contract) + ';const original=JSON.stringify(contract);const definition=' + json.dumps(OPERATION['target_declarations'][0]) + r""";
(async()=>{
 let current=true;const host=new Element('section'),declarations=[{key:'Input',from:'Input',definition}];
 const component=window.ClfCallMigration.mount(host,{callers:[{clf:'Ex_Caller',call_id:10,sequence:1,workspace:'200'}],enabled:true,current:()=>current,declarations:()=>declarations,load:async()=>contract,error:e=>{throw e;}});
 const row=host.children.find(x=>x.tag==='section');await row.children.find(x=>x.tag==='button').onclick();
 const body=row.children.find(x=>x.tag==='div'),check=body.children.find(x=>x.tag==='label').children[0],forms=body.children.find(x=>x.tag==='div');
 assert.deepEqual(component.operations('Ex_CalleeV2',[]),[]);check.checked=true;
 let op=component.operations('Ex_CalleeV2',[])[0];assert.equal(op.parameters[0].value,'"library"');assert.equal(op.bindings[0].parameters[0].value,'"entry"');
 assert.equal(op.parameters[0].dynamic,true);assert.equal(op.bindings[0].parameters[0].dynamic,false);
 const library=forms.children[0].children.find(x=>x.tag==='div'),input=library.children.find(x=>x.tag==='label').children[0];
 input.value='manual';declarations[0].definition.Name='RenamedAgain';component.repaint();
 op=component.operations('Ex_CalleeV2',[])[0];assert.equal(op.parameters[0].parameter,'RenamedAgain');assert.equal(op.parameters[0].value,'manual');
 assert.equal(JSON.stringify(contract),original);assert.throws(()=>component.operations('Ex_CalleeV2',[op]),/同一调用者/);
 current=false;assert.throws(()=>component.operations('Ex_CalleeV2',[]),/上下文已变化/);
 check.checked=false;assert.deepEqual(component.operations('Ex_CalleeV2',[]),[]);
})().catch(e=>{process.stderr.write(e.stack);process.exitCode=1;});
""")


def prepare(sdk):
    sdk('apply', operations=[{'action':'create_clf','name':'Ex_Callee','clf_type':'UserFunctions'},
        {'action':'add_clf_declaration','owner':'Ex_Callee','definition':DEFINITION},
        {'action':'create_clf','name':'Ex_Caller','clf_type':'UserFunctions'},
        *[{'action':'create_cdo','name':'Ex_CallEntry'+suffix,'parent':'ShopFloor'} for suffix in ('A','B')]])
    callee=sdk('get',kind='clf',name='Ex_Callee')
    sdk('apply', operations=[{'action':'add_clf_function','owner':'Ex_Caller','function':callee['DefaultFunctionDefinition']['name'],'sequence':i} for i in (1,2)])
    parent=sdk('get',kind='clf',name='Ex_Caller');first,second=parent['functions']
    previous=['','']
    for suffix in ('A','B','Library'):
        operations=[]
        for index,call in enumerate((first,second)):
            value='"'+suffix+str(index)+'"'
            operations.append({'action':'set_clf_parameter','owner':'Ex_Caller','call_id':call['CLFFunctionID'],'parameter':'Input','expected_value':previous[index],'value':value})
            previous[index]=value
        if suffix!='Library':operations.append({'action':'bind_event','owner':'Ex_CallEntry'+suffix,'event':'BeforeInitialize','clf':'Ex_Caller','feature':'Base'})
        sdk('apply',operations=operations)
    # Preserve a disabled call, even though both calls have the same function name.
    sdk('apply',operations=[{'action':'patch','kind':'clf_function','owner':'Ex_Caller','name':'id:'+str(first['CLFFunctionID']),'expected':{'IsActive':True},'changes':{'IsActive':False}}])
    record=sdk('get',kind='clf',name='Ex_Caller',owner='caller_migration:'+str(first['CLFFunctionID']))
    contract=record['caller_migration'];assert contract['supported'],contract['reason']
    return record,contract,second['CLFFunctionID']


def migration(contract):
    original=contract['source_declarations'][0]['definition']
    target={**original,'Name':'Output','Direction':'In/Out','DataType':'Integer'}
    flag={**DEFINITION,'Name':'Flag','DataType':'Boolean'}
    def parameters(value):return [{'parameter':'Output','value':value,'dynamic':True},{'parameter':'Flag','value':'true','dynamic':False}]
    operation={**deepcopy(contract['operation']),'clf':'Ex_CalleeV2','target_declarations':[target,flag],
        'parameters':parameters('123'),'bindings':[{'binding_id':b['binding_id'],'parameters':parameters('101' if b['owner'].endswith('A') else '202')} for b in contract['operation']['expected_bindings']]}
    return [{'action':'copy_clf','name':'Ex_CalleeV2','template':'Ex_Callee'},
            {'action':'update_clf_declaration','owner':'Ex_CalleeV2','parameter':'Input','expected':original,'definition':target},
            {'action':'add_clf_declaration','owner':'Ex_CalleeV2','definition':flag},operation]


def test_sdk_typed_call_and_all_entries_saved_with_other_repeat_call_unchanged(sdk):
    before,contract,other_id=prepare(sdk);operations=migration(contract)
    checksum=vendor.digest(sdk.private_mdb)
    preview=sdk('preflight',operations=operations);assert preview['ready'],preview['conflicts']
    assert vendor.digest(sdk.private_mdb)==checksum
    baseline=sdk.private_mdb.parent/'baseline.mdb';shutil.copyfile(sdk.private_mdb,baseline)
    saved=sdk('apply',operations=operations);assert saved['reload_verified']
    receipt=saved['call_migrations'][0];assert receipt['reload_verified'] and receipt['entry_count']==2
    after=sdk('get',kind='clf',name='Ex_Caller',owner='attached_parameters')
    calls={c['CLFFunctionID']:c for c in after['functions']}
    assert contract['operation']['call_id'] not in calls
    selected=calls[receipt['call_id']];assert selected['Sequence']==1 and selected['IsActive'] is False
    assert calls[other_id] == next(c for c in before['functions'] if c['CLFFunctionID']==other_id)
    for binding in after['bindings']:
        original=next(b for b in contract['operation']['expected_bindings'] if b['binding_id']==binding['CLFEventMapID'])
        old_other=[p for p in original['parameters'] if p['call_id']==other_id]
        new_other=[{k:p[v] for k,v in [('value_id','CLFFunctionParmValueID'),('call_id','CLFFunctionID'),('parameter_id','FunctionParmID'),('parameter','Name'),('value','ValueExpression'),('dynamic','DynamicParm')]} for p in binding['attached_parameters'] if p['CLFFunctionID']==other_id]
        assert new_other==old_other
        proposed=next(b for b in operations[-1]['bindings'] if b['binding_id']==binding['CLFEventMapID'])['parameters']
        actual=[p for p in binding['attached_parameters'] if p['CLFFunctionID']==receipt['call_id']]
        assert {p['Name']:(p['ValueExpression'],p['DynamicParm']) for p in actual}=={p['parameter']:(p['value'],p['dynamic']) for p in proposed}
    old=sdk('get',kind='clf',name='Ex_Callee');assert [p['Name'] for p in old['declared_parameters']]==['Input']
    folder=sdk.private_mdb.parent/'export';folder.mkdir();exported=vendor.export(folder,baseline,sdk.private_mdb)
    assert exported['validation']['valid']
    # Removing the last declaration must not leave generated values for a deleted parameter.
    zero=sdk.private_mdb.parent/'zero-target';zero.mkdir();shutil.copyfile(baseline,zero/'private.mdb')
    original=contract['source_declarations'][0]['definition']
    no_arguments={**deepcopy(contract['operation']),'clf':'Ex_CalleeV2','target_declarations':[],'parameters':[],
        'bindings':[{'binding_id':b['binding_id'],'parameters':[]} for b in contract['operation']['expected_bindings']]}
    zero_ops=[{'action':'copy_clf','name':'Ex_CalleeV2','template':'Ex_Callee'},
        {'action':'remove_clf_declaration','owner':'Ex_CalleeV2','parameter':'Input','expected':original},no_arguments]
    result=vendor.run(zero,{'mode':'apply','mdb':str(zero/'private.mdb'),'workspace':'200','operations':zero_ops})
    receipt=result['call_migrations'][0]
    assert receipt['reload_verified'] and receipt['parameter_count']==0
    assert all(len(b['parameters'])==1 and b['parameters'][0]['call_id']==other_id for b in receipt['entries'])


def test_sdk_call_migration_refuses_missing_stale_typed_or_later_changed_scope(sdk):
    _,contract,_=prepare(sdk);operations=migration(contract);checksum=vendor.digest(sdk.private_mdb)
    for kind in ('missing_entry','stale_value','type','active','later_change','duplicate_parent'):
        proposed=deepcopy(operations);op=proposed[-1]
        if kind=='missing_entry':op['expected_bindings'].pop();op['bindings'].pop()
        elif kind=='stale_value':op['expected_bindings'][0]['parameters'][0]['value']='stale'
        elif kind=='type':
            op['target_declarations']=deepcopy(op['target_declarations'])
            op['target_declarations'][0]['DataType']='String'
        elif kind=='active':op['expected_active']=True
        elif kind=='duplicate_parent':proposed.append(deepcopy(op))
        else:proposed.append({'action':'add_clf_function','owner':'Ex_Caller','function':'if','sequence':3})
        result=sdk('preflight',operations=proposed);assert not result['ready'],kind
        assert vendor.digest(sdk.private_mdb)==checksum
