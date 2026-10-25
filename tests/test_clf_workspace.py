import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
import pytest

import config
from agent import memory, assistant_modes, langgraph_runtime as runtime, llm_client
from designer import clf, vendor, progress_store
from designer.attachments import save_attachment, resolve_attachments
from designer.excel_rules import extract
from tests.test_excel_attachments import isolated_store, workbook_bytes
from web import routes, clf_routes


@pytest.fixture
def api(isolated_store):
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(clf_routes.router)
    return TestClient(app)


def test_dedicated_sessions_are_persisted_filtered_and_scoped(api, isolated_store):
    normal = api.post('/sessions/user/new').json()['session_id']
    sid = api.post('/sessions/user/new?mode=clf').json()['session_id']
    for session_id in (normal, sid):
        memory.get_user_messages('user', session_id).append({'role': 'user', 'content': '查询对象'})
        memory.save_user_session('user', session_id)
    assert [s['id'] for s in api.get('/sessions/user?mode=clf').json()['sessions']] == [sid]
    assert [s['id'] for s in api.get('/sessions/user?mode=designer').json()['sessions']] == [normal]
    assert 'CLF 专业助手' in memory.get_user_messages('user', sid)[0]['content']
    saved = memory.load_memory()
    assert saved['user']['sessions'][sid]['mode'] == 'clf'
    assert api.post('/sessions/user/new?mode=arbitrary').status_code == 422
    assert api.get('/api/clf/projects', params={'username':'other','session_id':sid}).status_code == 404
    assert api.get('/api/clf/projects', params={'username':'user','session_id':normal}).status_code == 409
    assert api.get('/clf').status_code == 200
    assert api.get('/clf/records').status_code == 200
    assert api.get('/clf-workspace.js').status_code == 200
    assert api.get('/clf-workspace.css').status_code == 200
    memory.user_memories['user']['sessions'][normal].pop('mode')
    assert assistant_modes.mode('user', normal) == 'designer'


def test_excel_rules_preserve_scope_cells_hidden_rows_zero_and_missing_values():
    doc={'id':'a'*32,'sha256':'b'*64,'name':'rules.xlsx','sheets':[
        {'name':'Sheet1','hidden':True,'rows':[
            {'row':1,'cells':{'A':'对象名','B':'字段名','C':'业务规则','D':'触发时机','E':'错误提示'}},
            {'row':2,'cells':{'A':'Spec','B':'Status','C':'只允许0/1/2','D':'保存','E':'状态非法'}},
            {'row':3,'cells':{'B':'Other','C':0}},
            {'row':4,'cells':{'A':'Spec','B':'Unchanged'}},
            {'row':6,'cells':{'A':'ObjectName','B':'FieldName','C':'CLF要求'}},
            {'row':7,'cells':{'A':'Product','B':'Status','C':'空值默认0'}},
        ]}]}
    rules=extract(doc)
    assert len(rules)==3
    assert rules[0]['cell']=='C2' and rules[0]['hidden'] is True
    assert rules[0]['owner']=='Spec' and rules[0]['trigger']=='保存'
    assert rules[1]['rule']=='0' and rules[1]['owner'] is None  # No guessed fill-down.
    assert rules[2]['owner']=='Product' and rules[2]['cell']=='C7'


def test_excel_handoff_is_complete_owned_idempotent_and_source_checked(api, monkeypatch):
    sid=api.post('/sessions/user/new').json()['session_id']
    other=api.post('/sessions/user/new').json()['session_id']
    rows=[['对象名','字段名','业务规则']]+[['Spec',f'Field{i}',f'规则{i}'] for i in range(8)]
    attachment=save_attachment(workbook_bytes(rows),'rules.xlsx','user',sid)
    monkeypatch.setattr(clf_routes.working,'available',lambda:[])
    assert attachment['rule_count']==8 and len(attachment['rule_preview'])==5
    payload={'username':'user','source_session_id':sid,'attachment_ids':[attachment['id']]}
    assert api.post('/api/clf/from-excel',json={**payload,'source_session_id':other}).status_code==404
    response=api.post('/api/clf/from-excel',json=payload)
    assert response.status_code==200
    target=response.json()['session_id']
    assert api.post('/api/clf/from-excel',json=payload).json()['session_id']==target
    context=clf.session_context('user',target)
    assert len(context['excel_handoff']['rules'])==8
    assert context['excel_handoff']['rules'][-1]['cell']=='C9'
    source=Path(config.EXCEL_ATTACHMENT_ROOT)/attachment['id']/'source.xlsx'
    source.write_bytes(b'changed')
    assert api.get('/api/clf/context',params={'username':'user','session_id':target}).status_code==409


def raw_clf(name='ExLogic'):
    return {'name':name,'CLFID':42,'WorkspaceCode':'200','ActiveWorkspaceCode':'200','Description':'说明',
            'declared_parameters':[{'name':'Input','Direction':'In'}],
            'functions':[{'CLFFunctionID':2,'Sequence':2,'FunctionName':'Check','IsActive':True,
                          'parameters':[{'Name':'Value','ValueExpression':'1','DynamicParm':True}]},
                         {'CLFFunctionID':1,'Sequence':1,'FunctionName':'Check','IsActive':False,'parameters':[]}]}


def mock_reads(monkeypatch, root):
    monkeypatch.setattr(config,'DESIGNER_ROOT',str(root))
    root.mkdir(exist_ok=True)
    source=root/'read.mdb';source.write_bytes(b'unchanged')
    checksum=vendor.digest(source)
    def batch(*_):
        return {'results':[{'result':raw_clf()},{'result':{'where_used':[{'CDOName':'Spec','MethodName':'Validate'}],
                                                         'total':150,'truncated':True}}]}
    monkeypatch.setattr(vendor,'query_batch',batch)
    return source,checksum


def test_actual_call_identity_order_and_truncated_reference_scope(tmp_path, monkeypatch):
    source,checksum=mock_reads(monkeypatch,tmp_path)
    result=clf.get_clf(str(source),'ExLogic',checksum)
    assert [call['call_id'] for call in result['calls']]==[1,2]
    assert all(call['function']=='Check' for call in result['calls'])  # Same function, distinct calls.
    assert result['calls'][0]['active'] is False
    assert result['declared_parameters'][0]['name']=='Input'
    assert result['references']['truncated'] and result['references']['runtime_usage_verified'] is False
    with pytest.raises(ValueError,match='已更新'):
        clf.get_clf(str(source),'ExLogic','0'*64)


def test_plan_preview_uses_native_preflight_and_never_claims_execution(tmp_path, monkeypatch):
    source,checksum=mock_reads(monkeypatch,tmp_path)
    seen=[]
    def preflight(*args):
        seen.append(args)
        changed=raw_clf();changed['functions'][0]['parameters'][0]['ValueExpression']='2'
        return {'ready':True,'conflicts':[],'workspace':'200','clfs':[changed]}
    monkeypatch.setattr(vendor,'preflight',preflight)
    op={'action':'set_clf_parameter','owner':'ExLogic','call_id':2,'parameter':'Value','value':'2','expected_value':'1'}
    result=clf.inspect_plan(str(source),checksum,[op],test_cases=[{'name':'Zero','inputs':{'value':0},'expected':'Accepted'}])
    assert len(seen)==1
    assert result['before'][0]['calls'][1]['parameters'][0]['value']=='1'
    assert result['after'][0]['calls'][1]['parameters'][0]['value']=='2'
    assert result['test_cases'][0]['status']=='not_run'
    assert result['saved'] is False and result['published'] is False and result['runtime_verified'] is False
    assert vendor.digest(source)==checksum
    with pytest.raises(ValueError,match='仅允许'):
        clf.inspect_plan(str(source),checksum,[{'action':'add_field','name':'Bad'}])


def test_chat_enforces_read_only_scope_before_backend_even_for_unadvertised_calls(api, monkeypatch):
    sid=api.post('/sessions/user/new?mode=clf').json()['session_id']
    seen=[]
    async def model(**kwargs):
        seen.append(kwargs)
        tools=[m for m in kwargs['messages'] if m['role']=='tool']
        call=None if tools else SimpleNamespace(index=0,id='forbidden',function=SimpleNamespace(
            name='generate_designer_design_package',arguments=json.dumps({'mdb_file':'read.mdb','expected_sha256':'a'*64,
            'operations':[{'action':'create_clf','name':'ExForbidden','clf_type':'UserFunction'}]})))
        delta=SimpleNamespace(content='当前只做方案分析。' if tools else None,tool_calls=[call] if call else None)
        async def chunks():yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])
        return chunks()
    async def title(_):return 'CLF 权限测试'
    monkeypatch.setattr(llm_client,'generate_title',title)
    monkeypatch.setattr(runtime,'_client',SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=model))))
    monkeypatch.setattr(runtime,'_graph',runtime._build_graph(InMemorySaver()))
    monkeypatch.setattr(runtime,'_openai_tools',[{'function':{'name':name}} for name in ['get_designer_clf','generate_designer_design_package']])
    monkeypatch.setattr(runtime,'get_tool_func',lambda name:pytest.fail('Forbidden call reached tool backend'))
    monkeypatch.setattr(runtime,'record_perf',lambda *a,**k:None)
    monkeypatch.setattr(runtime,'build_runtime_experience_context',lambda *a:'')
    async def run():return [event async for event in runtime.langgraph_chat_stream('user','确认保存这个CLF',sid)]
    events=asyncio.run(run())
    assert events
    assert [tool['function']['name'] for tool in seen[0]['tools']]==['get_designer_clf']
    assert any('不能保存' in str(message) for message in memory.get_user_messages('user',sid))
    assert not any(event['tool']=='generate_designer_design_package' for event in progress_store.operations('user',sid))


def test_scope_guard_also_runs_at_execution_boundary(api, monkeypatch):
    sid=api.post('/sessions/user/new?mode=clf').json()['session_id']
    monkeypatch.setattr(runtime,'get_stream_writer',lambda:lambda *_:None)
    monkeypatch.setattr(runtime,'get_tool_func',lambda _:pytest.fail('Execution bypassed CLF scope'))
    monkeypatch.setattr(runtime,'record_perf',lambda *a,**k:None)
    monkeypatch.setattr(runtime,'record_tool_outcome',lambda **kwargs:None)
    state={'username':'user','session_id':sid,'messages':[], 'pending_tool_calls':[{
        'id':'direct','function':{'name':'publish_designer_test_database','arguments':'{}'}}]}
    result=asyncio.run(runtime._execute_tool_node(state))
    assert '不能保存' in result['messages'][-1]['content']


def test_clf_results_survive_receipt_compaction_and_recovery(api, tmp_path, monkeypatch):
    sid=api.post('/sessions/user/new?mode=clf').json()['session_id']
    source,checksum=mock_reads(monkeypatch,tmp_path/'metadata')
    result=clf.get_clf(str(source),'ExLogic',checksum)
    identity,_=progress_store.begin('user',sid,'get_designer_clf',{},call_id='clf-read')
    progress_store.finish(identity,result)
    messages=memory.get_user_messages('user',sid)
    progress_store.recover_chat_results('user',sid,messages)
    recovered=next(message['clf_result'] for message in messages if message.get('clf_result'))
    assert recovered['calls']==result['calls'] and recovered['references']==result['references']
    history=api.get('/history/user',params={'session_id':sid}).json()
    assert history['clf_results'][0]['calls']==result['calls']


def test_scope_prevents_model_from_querying_another_working_file(api, isolated_store, monkeypatch):
    root=isolated_store/'designer';monkeypatch.setattr(config,'DESIGNER_ROOT',str(root))
    pid='c'*32
    selected=root/'workspaces'/pid/'InSite.mdb';selected.parent.mkdir(parents=True);selected.write_bytes(b'one')
    other=root/'other.mdb';other.write_bytes(b'two')
    sid=api.post('/sessions/user/new?mode=clf').json()['session_id']
    assistant_modes.session('user',sid)['clf_context']={'project_id':pid}
    assistant_modes.enforce('user',sid,'get_designer_clf',{'mdb_file':str(selected)})
    with pytest.raises(ValueError,match='不一致'):
        assistant_modes.enforce('user',sid,'get_designer_clf',{'mdb_file':str(other)})


def test_plan_can_preview_new_clf_and_subsequent_calls_in_one_batch(tmp_path, monkeypatch):
    source,checksum=mock_reads(monkeypatch,tmp_path)
    queried=[]
    original=vendor.query_batch
    def batch(path,queries):
        queried.extend(query['name'] for query in queries if query['mode']=='get')
        return original(path,queries)
    monkeypatch.setattr(vendor,'query_batch',batch)
    monkeypatch.setattr(vendor,'preflight',lambda *args:{'ready':True,'conflicts':[],'clfs':[raw_clf('ExCopy')]})
    ops=[{'action':'copy_clf','name':'ExCopy','template':'ExLogic'},
         {'action':'add_clf_function','owner':'ExCopy','function':'Check','sequence':3}]
    result=clf.inspect_plan(str(source),checksum,ops)
    assert queried==['ExLogic']
    assert result['before'][0]['name']=='ExLogic' and result['after'][0]['name']=='ExCopy'


def test_direct_tool_boundary_rejects_claimed_test_pass_before_vendor(monkeypatch):
    from tools.designer_clf import inspect_designer_clf_plan
    monkeypatch.setattr(clf,'inspect_plan',lambda *args:pytest.fail('Invalid test case reached vendor'))
    with pytest.raises(ValueError):
        asyncio.run(inspect_designer_clf_plan('unused','a'*64,[{'action':'create_clf','name':'ExTest','clf_type':'UserFunction'}],
                    test_cases=[{'name':'Bad','expected':'Accepted','inputs':{},'status':'passed'}]))
