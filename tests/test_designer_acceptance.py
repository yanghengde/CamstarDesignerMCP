"""Acceptance must check changed values and prove removals, beyond existence."""
import json
from types import SimpleNamespace
import pytest
from designer import acceptance, operations, publication, vendor
from agent.safety import count_tool_calls


def test_compiled_delta_tracks_renames_changes_and_removals():
    before={'CDODefinition':[{'cdodefid':1,'cdoname':'Old','parentcdoid':10,'workspacecode':'200'}],
            'QueryDef':[{'querydefid':5,'queryname':'Gone'}]}
    after={'CDODefinition':[{'cdodefid':1,'cdoname':'New','parentcdoid':11,'workspacecode':'csi'}],
           'CDOFieldMapDefinition':[{'cdofieldmapid':7,'sourcecdofieldid':100,'targetcdofieldid':101}]}
    planned=acceptance.changes(before,after)
    cdo=next(item for item in planned if item['table']=='CDODefinition')
    assert cdo['expected']=={'cdoname':'New','parentcdoid':11}
    assert next(item for item in planned if item['table']=='QueryDef')['deleted']
    assert not next(item for item in planned if item['table']=='CDOFieldMapDefinition')['deleted']


def test_duplicate_compiled_identity_cannot_pass_acceptance():
    with pytest.raises(ValueError,match='重复'):
        acceptance.changes({}, {'QueryDef':[{'querydefid':1},{'querydefid':1}]})


class Cursor:
    def __init__(self, rows, available=True):
        self.rows=rows;self.available=available;self.queries=[]
    def execute(self, sql, *args):
        self.queries.append((sql,args))
        if 'INFORMATION_SCHEMA' in sql:
            self.result=[('CLFFunctions',)] if self.available else []
        else:
            self.result=self.rows;self.description=[('CLFFunctionID',),('FunctionID',),('Sequence',)]
        return self
    def fetchall(self):return self.result


@pytest.mark.parametrize('rows,deleted,expected,passed', [
    ([(12,20,2)],False,{'functionid':20,'sequence':2},True),
    ([(12,20,1)],False,{'sequence':2},False),
    ([],True,{},True), ([(12,20,1)],True,{},False),
    ([],False,{'sequence':2},False),
])
def test_sql_verification_proves_expected_values_and_absence(rows,deleted,expected,passed):
    cursor=Cursor(rows)
    result=acceptance.verify_changes(cursor,'dbo',[{'table':'CLFFunctions','keys':{'CLFFunctionID':12},'deleted':deleted,'expected':expected}])
    assert result[0]['passed'] is passed
    assert cursor.queries[-1][1]==(12,)


def test_missing_metadata_table_is_failure():
    result=acceptance.verify_changes(Cursor([],False),'dbo',[{'table':'CLFFunctions','keys':{'CLFFunctionID':12},'deleted':True,'expected':{}}])
    assert not result[0]['passed']


def test_workflow_tracks_patch_delete_and_final_object_name():
    result=operations.summary([
        {'action':'patch','kind':'cdo','name':'Old','changes':{'Name':'New'}},
        {'action':'patch','kind':'field','owner':'Old','name':'Code','changes':{'IsListType':True}},
        {'action':'delete','kind':'cdo','name':'Gone'},
        {'action':'replace_event_binding','owner':'New','event':'BeforeInitialize'},
    ], ['Other'])
    assert result['objects']==['New','Gone','Other']
    assert result['service_objects']==['New','Other']
    assert result['rows'][1]['owner']=='New'
    assert result['rows'][2]['deleted']
    assert result['change_count']==4 and result['field_count']==1


def test_safety_counts_mutations_inside_generic_tool():
    calls=[{'function':{'name':'generate_designer_design_package','arguments':json.dumps({'operations':[
        {'action':'create_cdo'}, {'action':'add_field'}, {'action':'change_parent'},
        {'action':'patch'}, {'action':'delete'}, {'action':'unbind_event'}, {'action':'remove_field_override'}]})}}]
    counts=count_tool_calls(calls)
    assert (counts.creates,counts.updates,counts.deletes)==(2,2,3)


def test_specialized_tool_counts_private_field_types():
    calls=[{'function':{'name':'generate_designer_cdo_package','arguments':json.dumps({'fields':[
        {'name':'NewType'}, {'name':'ExistingType','field_type':'String80'}]})}}]
    assert count_tool_calls(calls).creates==4


@pytest.mark.parametrize('payload',[{'fields':1},{'operations':1},[],None])
def test_safety_handles_invalid_payload_without_crashing(payload):
    count_tool_calls([{'function':{'name':'generate_designer_design_package','arguments':json.dumps(payload)}}])


def test_legacy_baseline_uses_legacy_fingerprint(monkeypatch):
    seen=[]
    monkeypatch.setattr(publication,'metadata_fingerprint',lambda cursor,schema,version=2:seen.append(version) or ('old' if version==1 else 'new'))
    assert publication.baseline_matches(None,'dbo',{'metadata_fingerprint':'old'})
    assert publication.baseline_matches(None,'dbo',{'metadata_fingerprint':'new','fingerprint_version':2})
    assert seen==[1,2]


def test_official_delete_xml_is_supported():
    from xml.etree.ElementTree import fromstring
    from designer.metadata import validate
    root=fromstring('<InSiteMetaData><Header><Version>1.0</Version></Header><Import><FieldDefinitions><FieldDefinition Name="Unused" Action="Delete"/></FieldDefinitions></Import></InSiteMetaData>')
    assert validate(root)['valid']


@pytest.mark.parametrize('action,extra',[
    ('change_parent',{'name':'ExThing','parent':'Product'}),
    ('remove_field_override',{'owner':'ExThing','name':'Field'}),
    ('replace_event_binding',{'owner':'ExThing','event':'BeforeInitialize','clf':'CLF','feature':'Base'}),
    ('replace_clf_function',{'owner':'CLF','call_id':1,'function':'FieldCreateCDO'}),
    ('update_field_map',{'owner':'Map','name':'MapField','source_cdo':'Source','source_field':'F','target_cdo':'Target','target_field':'G'}),
])
def test_relationship_operations_require_original_values(action,extra):
    with pytest.raises(ValueError):vendor.validate_operations([{'action':action,**extra}])
