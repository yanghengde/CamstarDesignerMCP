"""Complete vendor creation plans must be checked before confirmation and save."""
import json
import os
from pathlib import Path
import shutil

import pytest
import config
from designer import vendor
from designer.design_contract import cdo_operations, inspect_calls
from agent import excel_preview


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    source = tmp_path/'source.mdb'; source.write_bytes(b'original')
    dll = tmp_path/'metadata.dll'; dll.write_bytes(b'sdk')
    monkeypatch.setattr(vendor, 'assembly', lambda: dll)
    monkeypatch.setattr(vendor, 'select_workspace', lambda source, workspace: workspace or '200')
    return source, dll


def report(ready=True):
    return {'ready': ready, 'complete': True, 'read_only': True, 'objects': [], 'conflicts': [], 'categories': []}


@pytest.mark.parametrize('property_name', ['CDOUsageMask','CDOUsageMaskId','ParentCDOName','ParentCDO',
                                          'ParentCDOID','StorageCategoryName','StorageCategoryId'])
def test_generic_patch_cannot_bypass_semantic_object_operations(property_name):
    with pytest.raises(ValueError, match='不能通过 patch'):
        vendor.validate_operations([{'action':'patch','kind':'cdo','name':'ExObject',
            'changes':{property_name:'new'},'expected':{property_name:'old'}}])


def test_specialized_cdo_options_are_forwarded_without_changing_defaults():
    fields=[{'name':'Code','field_type':'Name'}]
    default=cdo_operations('ExObject','NamedDataObject',fields)
    assert default[0]=={'action':'create_cdo','name':'ExObject','parent':'NamedDataObject','description':''}
    chosen=cdo_operations('ExObject','RevisionedObject',fields,create_table=True,
        table_name='T_ExObject',table_description='Object',create_revision_base=True,create_maintenance=True)
    assert chosen[0]['table_name']=='T_ExObject'
    assert chosen[0]['create_revision_base'] is True and chosen[0]['create_maintenance'] is True


def test_preflight_reuses_exact_plan_but_invalidates_source_sdk_and_parameters(isolated, monkeypatch):
    source,dll=isolated; calls=[]
    def run(folder, request):
        assert request['mode']=='preflight'
        assert Path(request['mdb'])!=source
        assert Path(request['mdb']).read_bytes()==source.read_bytes()
        calls.append(request); return report()
    monkeypatch.setattr(vendor, 'run', run)
    operations=[{'action':'create_cdo','name':'ExObject','parent':'Product'}]
    first=vendor.preflight(str(source),vendor.digest(source),operations)
    assert vendor.preflight(str(source),vendor.digest(source),operations)==first
    assert len(calls)==1
    source.write_bytes(b'new source')
    vendor.preflight(str(source),vendor.digest(source),operations)
    dll.write_bytes(b'new sdk')
    vendor.preflight(str(source),vendor.digest(source),operations)
    vendor.preflight(str(source),vendor.digest(source),[{**operations[0],'parent':'NamedDataObject'}])
    assert len(calls)==4
    assert not list(source.parent.rglob('read.mdb'))


@pytest.mark.parametrize('drift', ['private','source','sdk'])
def test_preflight_mutations_or_drift_are_never_cached(isolated, monkeypatch, drift):
    source,dll=isolated
    def run(folder, request):
        changed=Path(request['mdb']) if drift=='private' else source if drift=='source' else dll
        changed.write_bytes(b'changed')
        return report()
    monkeypatch.setattr(vendor,'run',run)
    with pytest.raises(ValueError,match='来源已变化'):
        vendor.preflight(str(source),vendor.digest(source),[])
    assert not list(source.parent.rglob('read.mdb'))
    assert not list((source.parent/'cache').rglob('*.json'))


def test_truncated_fields_are_rejected_before_preview(isolated, monkeypatch):
    source,_=isolated
    monkeypatch.setattr(vendor,'run',lambda *args: {**report(),'objects':[{'complete':True,'total_fields':160,'fields':[{}]*100}]})
    with pytest.raises(ValueError,match='基础字段列表不完整'):
        vendor.preflight(str(source),vendor.digest(source),[])


def test_blocked_preflight_stops_actual_design_before_apply(isolated, monkeypatch):
    source,_=isolated; conflicts=[{'owner':'ExObject','name':'Name','message':'已有同名字段'}]
    monkeypatch.setattr(vendor,'preflight',lambda *args: {**report(False),'conflicts':conflicts})
    monkeypatch.setattr(vendor,'run',lambda *args: pytest.fail('must not apply or save'))
    with pytest.raises(ValueError,match='ExObject.Name'):
        vendor.design(str(source),vendor.digest(source),'200',[{'action':'create_cdo','name':'ExObject','parent':'NamedDataObject'}])
    assert source.read_bytes()==b'original'
    assert not (source.parent/'artifacts').exists()


def test_plan_inspection_and_preview_bind_profile_to_call_source_and_options(monkeypatch):
    captured=[]
    def inspect(mdb,checksum,operations,workspace):
        captured.append(operations)
        return {**report(),'source_sha256':checksum,'vendor_assembly_sha256':'sdk1','objects':[
            {'name':'ExObject','parent':'Product','total_fields':160,'complete':True,'fields':[{}]*160}]}
    monkeypatch.setattr(vendor,'preflight',inspect)
    call={'id':'call-1','function':{'name':'generate_designer_cdo_package','arguments':json.dumps({
        'mdb_file':'source.mdb','expected_sha256':'a'*64,'cdo_name':'ExObject','parent_cdo':'Product',
        'fields':[{'name':'Code','field_type':'Name'}],'create_table':True,'table_name':'T_ExObject','table_description':'Object'})}}
    checks=inspect_calls([call]); preview=excel_preview.build([call],[],checks)
    assert captured[0][0]['create_table'] is True
    assert preview['creation_checks'][0]['call_id']=='call-1'
    assert preview['creation_checks'][0]['objects'][0]['total_fields']==160
    changed=excel_preview.build([call],[],[{**checks[0],'vendor_assembly_sha256':'sdk2'}])
    assert preview['id']!=changed['id']


@pytest.fixture
def installed_sdk(tmp_path, monkeypatch):
    if not os.getenv('DESIGNER_TEST_VENDOR'):
        pytest.skip('set DESIGNER_TEST_VENDOR=1 for isolated installed-SDK checks')
    original=Path(os.environ['DESIGNER_TEST_MDB']).resolve(); checksum=vendor.digest(original)
    source=tmp_path/'source.mdb';shutil.copyfile(original,source)
    monkeypatch.setattr(config,'DESIGNER_ROOT',str(tmp_path))
    yield source
    assert vendor.digest(original)==checksum
    assert vendor.digest(source)==checksum


@pytest.mark.parametrize('field_name,length,compatible', [('Name',40,True),('name',80,False),('Description',500,False)])
def test_installed_sdk_reports_inherited_collision_without_save(installed_sdk,field_name,length,compatible):
    result=vendor.preflight(str(installed_sdk),vendor.digest(installed_sdk),
        cdo_operations('ExPreflightProbe','NamedDataObject',[{'name':field_name,'data_type':'String','max_length':length}]))
    assert not result['ready']
    conflict=next(c for c in result['conflicts'] if c['kind']=='field_name')
    assert conflict['existing']['origin']=='NamedDataObject'
    assert conflict['compatible_shape'] is compatible
    assert result['objects'][0]['total_fields']==17


def test_installed_sdk_checks_all_160_fields_and_same_batch_duplicates(installed_sdk):
    result=vendor.preflight(str(installed_sdk),vendor.digest(installed_sdk),[
        {'action':'create_cdo','name':'ExFullFieldProbe','parent':'Product'},
        {'action':'add_field','owner':'ExFullFieldProbe','name':'ExBatchCode','field_type':'Name','persistent':True},
        {'action':'add_field','owner':'ExFullFieldProbe','name':'exbatchcode','field_type':'Name','persistent':True}])
    assert len(result['objects'][0]['fields'])==result['objects'][0]['total_fields']>=160
    assert not result['ready']
    assert any(c['kind']=='field_name' for c in result['conflicts'])


def test_installed_sdk_preview_includes_generated_tables_and_maintenance_objects(installed_sdk):
    result=vendor.preflight(str(installed_sdk),vendor.digest(installed_sdk),[
        {'action':'create_cdo','name':'ExStructureProbe','parent':'NamedDataObject',
         'create_table':True,'table_name':'T_ExStructureProbe','table_description':'Structure probe','create_maintenance':True}])
    assert result['ready']
    profile=result['objects'][0]
    assert 'T_ExStructureProbe' in profile['tables']
    assert {'ExStructureProbeMaint','ExStructureProbeChanges'} <= {c['name'] for c in profile['related_objects']}


def test_installed_sdk_schema_denies_generic_relationship_setters(installed_sdk):
    result=vendor.query(str(installed_sdk),'schema','cdo','NamedDataObject')
    properties={p['name']:p for p in result['properties']}
    for name in ('CDOUsageMask','ParentCDOName','StorageCategoryName'):
        assert properties[name]['editable'] is False


def test_installed_sdk_where_used_reads_vendor_datatable(installed_sdk):
    result = vendor.query(str(installed_sdk), 'impact', 'field_type', '[Integer]')
    assert result['total'] > 0 and result['where_used']
    assert result['truncated'] == (result['total'] > 100)
    assert len(result['where_used']) == min(result['total'], 100)
    assert all(isinstance(row, dict) and row for row in result['where_used'])


@pytest.mark.parametrize('collision', ['maintenance', 'table'])
def test_installed_sdk_rejects_generated_structure_collisions(installed_sdk, collision):
    operations = ([{'action':'create_cdo','name':'ExDerivedProbeMaint','parent':'Service'},
                   {'action':'create_cdo','name':'ExDerivedProbe','parent':'NamedDataObject','create_maintenance':True}]
                  if collision == 'maintenance' else
                  [{'action':'create_cdo','name':'ExTableProbe','parent':'NamedDataObject',
                    'create_table':True,'table_name':'Product','table_description':'Collision probe'}])
    result = vendor.preflight(str(installed_sdk), vendor.digest(installed_sdk), operations)
    assert not result['ready'] and result['conflicts']
    if collision == 'maintenance':
        assert any(c.get('existing_name') == 'ExDerivedProbeMaint' and '自动改名' in c['message']
                   for c in result['conflicts'])


def test_installed_sdk_allows_same_field_name_on_different_objects(installed_sdk):
    operations = []
    for name, table in (('ExOwnerOneProbe','T_ExOwnerProbe'), ('ExOwnerTwoProbe','T_ExOwnerProbe_1')):
        operations += [{'action':'create_cdo','name':name,'parent':'NamedDataObject','create_table':True,'table_name':table,'table_description':'Owner probe'},
                       {'action':'add_field','owner':name,'name':'ExOwnerCode','field_type':'Name','persistent':True}]
    result = vendor.preflight(str(installed_sdk), vendor.digest(installed_sdk), operations)
    assert result['ready'] and not result['conflicts']
    assert {choice['parent'] for choice in result['categories']} == {
        'NamedDataObject','RevisionedObject','Subentity','Service','Inquiry','HistoryMainline','HistoryDetails'}
