"""Cumulative mutation limits must never block diagnostic metadata reads."""
import asyncio
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt

from agent import memory, langgraph_runtime as runtime
from tests.test_excel_attachments import isolated_store, revision_runtime


def read_call(identity, name='list_designer_entities'):
    return {'id': identity, 'type': 'function', 'function': {'name': name,
        'arguments': '{"mdb_file":"test.mdb","kind":"field_type","search":"CreatedAt"}'}}


def test_reads_bypass_cumulative_limit_without_validating_rejected_plan(monkeypatch):
    monkeypatch.setattr(runtime, '_adopt_requirements', lambda state: None)
    monkeypatch.setattr(runtime, 'interrupt', lambda *args: pytest.fail('Read query must not ask approval'))
    state = {'pending_tool_calls': [read_call('query')], 'create_count': 54,
             'task_context': {'constraint_version': 2, 'proposed_plan': ['rejected'], 'validated_plan': ['prior']}}
    result = runtime._policy_node(state)
    assert result['task_context']['validated_plan'] == ['prior']
    assert runtime._route_after_policy({**state, **result}) == 'execute_tool'


def test_mixed_read_and_write_still_requires_approval(monkeypatch):
    monkeypatch.setattr(runtime, '_adopt_requirements', lambda state: None)
    class ApprovalRequired(Exception): pass
    def pause(value):
        assert value['type'] == 'approval_required'
        raise ApprovalRequired()
    monkeypatch.setattr(runtime, 'interrupt', pause)
    with pytest.raises(ApprovalRequired):
        runtime._policy_node({'pending_tool_calls': [read_call('query'), {
            'id': 'write', 'function': {'name': 'update_test_definition', 'arguments': '{}'}}],
            'create_count': 54, 'messages': []})


def test_old_readonly_approval_checkpoint_resumes_without_user_approval(isolated_store, revision_runtime, monkeypatch):
    original_policy = runtime._policy_node
    def old_policy(state):
        interrupt({'type': 'approval_required', 'excel_preview': None, 'counts': {'creates': 54}})
        return {}
    queried = []
    async def query(**args):
        queried.append(args)
        return {'read_only': True, 'records': [{'name': 'CreatedAtType', 'CPPDataType': 'TimeStamp'}]}
    async def finish(state):
        return {'final_reply': '已查明类型属性', 'stop_reason': 'complete', 'pending_tool_calls': []}
    monkeypatch.setattr(runtime, 'get_tool_func', lambda name: query if name=='list_designer_entities' else pytest.fail('No writes allowed'))
    monkeypatch.setattr(runtime, '_agent_node', finish)
    sid = memory.create_session('user')
    async def run():
        saver = InMemorySaver()
        monkeypatch.setattr(runtime, '_policy_node', old_policy)
        graph = runtime._build_graph(saver); gc = runtime._graph_config('user', sid)
        values = {'username':'user', 'session_id':sid, 'messages':[], 'pending_tool_calls':[read_call('q1'),read_call('q2')],
                  'create_count':54, 'task_context':{}, 'stop_reason':'', 'read_only':False}
        await graph.aupdate_state(gc, values, as_node='requirements')
        async for _ in graph.astream(None,gc): pass
        assert runtime._has_interrupt(await graph.aget_state(gc)) and not queried
        monkeypatch.setattr(runtime, '_policy_node', original_policy)
        graph = runtime._build_graph(saver)
        async for _ in graph.astream(None,gc): pass
        state = await graph.aget_state(gc)
        assert not runtime._has_interrupt(state) and len(queried)==2
        assert state.values['create_count']==54 and state.values['final_reply']=='已查明类型属性'
    asyncio.run(run())
