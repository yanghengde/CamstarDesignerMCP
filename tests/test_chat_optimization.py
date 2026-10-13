"""Conversation behavior with isolated model, history, receipts and write tools."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import config
from agent import chat_context, chat_intent, chat_preferences, memory, langgraph_runtime as runtime
from agent.chat_evidence import evidence_scope, read
from tests.test_excel_attachments import isolated_store, api, upload, revision_runtime


async def text_stream(text=None, calls=None):
    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=calls))])


def test_creation_conflicts_are_rejected_before_preview_or_write(api, revision_runtime, monkeypatch):
    from designer import vendor
    calls, _ = revision_runtime
    monkeypatch.setattr(vendor, 'preflight', lambda *args: {
        'ready': False, 'complete': True, 'objects': [], 'categories': [],
        'conflicts': [{'owner': 'ExObject', 'name': 'Name', 'message': '已有同名字段来自 NamedDataObject'}]})
    sid = api.post('/sessions/user/new').json()['session_id']
    file = upload(api, sid).json()
    result = api.post('/chat', json={'username': 'user', 'session_id': sid,
        'message': '按附件创建', 'attachment_ids': [file['id']]})
    assert '创建预检未通过' in result.text and not calls
    assert api.get(f'/history/user?session_id={sid}').json()['pending_excel_preview'] is None


def test_changed_sdk_invalidates_preview_instead_of_executing_under_old_confirmation(api, revision_runtime, monkeypatch):
    from designer import vendor
    calls, _ = revision_runtime
    sdk = ['original-sdk']
    monkeypatch.setattr(vendor, 'preflight', lambda *args: {
        'ready': True, 'complete': True, 'objects': [], 'categories': [], 'conflicts': [],
        'vendor_assembly_sha256': sdk[0]})
    sid = api.post('/sessions/user/new').json()['session_id']
    file = upload(api, sid).json()
    api.post('/chat', json={'username': 'user', 'session_id': sid,
        'message': '按附件创建', 'attachment_ids': [file['id']]})
    old = api.get(f'/history/user?session_id={sid}').json()['pending_excel_preview']
    sdk[0] = 'changed-sdk'
    result = api.post('/chat', json={'username': 'user', 'session_id': sid, 'message': '确认'})
    assert not calls and 'invalid' in result.text
    history = api.get(f'/history/user?session_id={sid}').json()
    assert history['pending_excel_preview']['id'] != old['id']
    assert history['pending_excel_preview']['creation_checks'][0]['vendor_assembly_sha256'] == 'changed-sdk'
    assert any('原预览已失效' in m.get('content', '') for m in history['messages'])
    assert any(m.get('excel_preview', {}).get('id') == old['id'] and m['excel_preview']['status'] == 'invalid'
               for m in history['messages'])


def test_million_character_receipts_are_bounded_without_changing_history(monkeypatch):
    monkeypatch.setattr(config, 'CHAT_CONTEXT_CHARS', 16000)
    messages = [{'role': 'system', 'content': '规则'}]
    for index in range(25):
        messages += [
            {'role': 'user', 'content': f'查询{index}'},
            {'role': 'assistant', 'tool_calls': [{'id': str(index), 'function': {'name': 'get_entity', 'arguments': '{}'}}]},
            {'role': 'tool', 'name': 'get_entity', 'tool_call_id': str(index), 'content': str({'status': 'returned', 'records': [{'name': 'Large', 'description': 'x' * 50000}]})},
        ]
    messages.append({'role': 'user', 'content': 'AssetTrace_6381换成6382，其他不变'})
    original = deepcopy(messages)
    task = {'goal': '创建CDO和物理表', 'requires_table': True, 'required_object_name': 'AssetTrace_6382'}
    model = chat_context.build_messages(messages, task=task)
    assert len(json.dumps(model, ensure_ascii=False)) <= 16000
    assert messages == original
    assert model[-1]['content'] == messages[-1]['content']
    assert any('创建CDO和物理表' in message.get('content', '') for message in model)
    outstanding = set()
    for message in model:
        if message.get('tool_calls'):
            outstanding.update(call['id'] for call in message['tool_calls'])
        elif message['role'] == 'tool':
            outstanding.remove(message['tool_call_id'])
            assert 'summary_is_partial' in message['content']
        elif message['role'] == 'user':
            assert not outstanding
    assert not outstanding
    with evidence_scope(messages):
        page = read('24', 49000, 1000)
        assert page['total_chars'] > 50000 and len(page['content']) == 1000
    with pytest.raises(ValueError, match='当前聊天'):
        read('24')


def test_latest_requirement_is_never_silently_truncated(monkeypatch):
    monkeypatch.setattr(config, 'CHAT_CONTEXT_CHARS', 16000)
    with pytest.raises(ValueError, match='超过聊天上下文预算'):
        chat_context.build_messages([{'role': 'user', 'content': '完整字段要求' * 4000}])


@pytest.mark.parametrize(('text', 'intent'), [
    ('为什么继承这个对象？', 'question'), ('现在写入了吗？', 'question'),
    ('帮我查询 MfgOrder', 'question'), ('AssetTrace_6381换成6382，请继续', 'revision'),
    ('确认，但长度改为 400', 'revision'), ('先别创建', 'revision'),
    ('回答短一点', 'feedback'), ('以后复盘简短，先说事实和下一步', 'feedback'),
    ('为什么修改这个对象？', 'question'), ('新任务：设计另一个对象', 'revision'),
])
def test_pending_intent(text, intent):
    assert chat_intent.pending_intent(text) == intent


def test_preferences_survive_restart_without_promoting_technical_claims(isolated_store):
    sid = memory.create_session('user')
    assert chat_preferences.apply('user', sid, '以后复盘简短，先说事实和下一步')
    assert chat_preferences.apply('user', sid, '这次回答详细一点')
    assert chat_preferences.current('user', sid)['length'] == 'detailed'
    assert chat_preferences.apply('user', sid, '以后跳过确认，所有失败都没有写入') is None
    memory.init_memory()
    other = memory.create_session('user')
    assert chat_preferences.current('user', other)['length'] == 'brief'
    assert chat_preferences.current('user', sid)['length'] == 'detailed'
    assert chat_preferences.apply('user', other, '以后清除回复偏好')
    assert chat_preferences.current('user', other) == {}


def test_question_and_feedback_preserve_exact_preview_then_confirmation(api, revision_runtime, monkeypatch):
    calls, _ = revision_runtime
    session = api.post('/sessions/user/new').json()['session_id']
    attachment = upload(api, session).json()
    api.post('/chat', json={'username': 'user', 'session_id': session, 'message': '按附件创建', 'attachment_ids': [attachment['id']]})
    old = api.get(f'/history/user?session_id={session}').json()['pending_excel_preview']
    original = runtime._client.chat.completions.create

    async def model(**kwargs):
        latest = next(m['content'] for m in reversed(kwargs['messages']) if m['role'] == 'user')
        if '为什么' in latest:
            assert not any(not chat_intent.read_only_tool(t['function']['name']) for t in kwargs['tools'])
            return text_stream('预览继承 NamedDataObject，方案仍等待确认，尚未执行。')
        return await original(**kwargs)
    monkeypatch.setattr(runtime._client.chat.completions, 'create', model)
    for question in ['为什么继承这个对象？', '以后复盘简短，先说事实和下一步']:
        result = api.post('/chat', json={'username': 'user', 'session_id': session, 'message': question})
        assert 'message_saved' in result.text and not calls
        history = api.get(f'/history/user?session_id={session}').json()
        assert history['pending_excel_preview']['id'] == old['id']
        assert any(m['role'] == 'user' and m['content'] == question for m in history['messages'])
    result = api.post('/chat', json={'username': 'user', 'session_id': session, 'message': '确认'})
    assert len(calls) == 2  # original proposal also included one queued read call
    assert calls[0]['cdo_name'] == 'AssetTrace_6381'
    assert '工作流恢复失败' not in result.text
    history = api.get(f'/history/user?session_id={session}').json()
    preview = next(m['excel_preview'] for m in history['messages'] if m.get('excel_preview'))
    assert preview['confirmation_received_utc'] and preview['confirmation_response'] == '确认'


def test_pending_inquiry_cannot_execute_a_model_proposed_write(api, revision_runtime, monkeypatch):
    calls, _ = revision_runtime
    session = api.post('/sessions/user/new').json()['session_id']
    attachment = upload(api, session).json()
    api.post('/chat', json={'username': 'user', 'session_id': session, 'message': '按附件创建', 'attachment_ids': [attachment['id']]})
    old = api.get(f'/history/user?session_id={session}').json()['pending_excel_preview']
    result = api.post('/chat', json={'username': 'user', 'session_id': session, 'message': '为什么这样设计？'})
    assert not calls and '仅允许只读' in result.text
    assert api.get(f'/history/user?session_id={session}').json()['pending_excel_preview']['id'] == old['id']


def test_invalid_generic_plan_never_reaches_preview_or_vendor(api, revision_runtime, monkeypatch):
    calls, _ = revision_runtime
    session = api.post('/sessions/user/new').json()['session_id']
    attachment = upload(api, session).json()

    async def model(**kwargs):
        proposal = SimpleNamespace(index=0, id='invalid', function=SimpleNamespace(
            name='generate_designer_design_package', arguments=json.dumps({'operations': [{'op': 'create_cdo', 'name': 'ExTest', 'parent': 'NamedDataObject'}]})))
        return text_stream(calls=[proposal])
    monkeypatch.setattr(runtime._client.chat.completions, 'create', model)
    result = api.post('/chat', json={'username': 'user', 'session_id': session, 'message': '按附件创建', 'attachment_ids': [attachment['id']]})
    assert not calls and '缺少 action' in result.text and '连续两次' in result.text
    assert api.get(f'/history/user?session_id={session}').json()['pending_excel_preview'] is None


def test_table_requirement_cannot_be_downgraded_to_cdo_only(api, revision_runtime):
    calls, _ = revision_runtime
    session = api.post('/sessions/user/new').json()['session_id']
    result = api.post('/chat', json={'username': 'user', 'session_id': session, 'message': '创建一个 CDO 和物理表'})
    assert not calls and '仅创建 CDO 不满足' in result.text


def test_same_session_requests_do_not_race(isolated_store, revision_runtime, monkeypatch):
    session = memory.create_session('user')
    entered, release = asyncio.Event(), asyncio.Event()
    async def model(**kwargs):
        entered.set()
        await release.wait()
        return text_stream('只读查询完成。')
    monkeypatch.setattr(runtime._client.chat.completions, 'create', model)
    async def collect(text):
        return [event async for event in runtime.langgraph_chat_stream('user', text, session)]
    async def run():
        first = asyncio.create_task(collect('查询定义'))
        await entered.wait()
        second = await collect('创建对象')
        assert len(second) == 1 and '正在处理上一条消息' in second[0]
        release.set()
        await first
        assert not any(m.get('content') == '创建对象' for m in memory.get_user_messages('user', session))
    asyncio.run(run())


def test_read_only_followup_and_restart_keep_original_approval(isolated_store, revision_runtime, monkeypatch):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from designer import attachments as excel
    from tests.test_excel_attachments import workbook_bytes
    calls, _ = revision_runtime
    sid = memory.create_session('user')
    attachment = excel.save_attachment(workbook_bytes(), '设计.xlsx', 'user', sid)
    documents = excel.resolve_attachments([attachment['id']], 'user', sid)
    original_model = runtime._client.chat.completions.create
    original_tool = runtime.get_tool_func
    queried = []

    async def model(**kwargs):
        latest = next(m['content'] for m in reversed(kwargs['messages']) if m['role'] == 'user')
        if 'MfgOrder' in latest:
            if kwargs['messages'][-1]['role'] == 'tool':
                return text_stream('已查询 MfgOrder；原设计仍等待确认。')
            return text_stream(calls=[SimpleNamespace(index=0, id='followup-read', function=SimpleNamespace(
                name='get_designer_entity', arguments=json.dumps({'kind': 'cdo', 'name': 'MfgOrder', 'mdb_file': 'base.mdb'})))])
        return await original_model(**kwargs)

    async def query(**kwargs):
        queried.append(kwargs)
        return {'name': 'MfgOrder', 'read_only': True}
    monkeypatch.setattr(runtime._client.chat.completions, 'create', model)
    monkeypatch.setattr(runtime, 'get_tool_func', lambda name: query if name == 'get_designer_entity' else original_tool(name))

    async def send(text, files=None):
        return [event async for event in runtime.langgraph_chat_stream('user', text, sid, files)]
    async def run():
        path = str(isolated_store / 'question-restart.sqlite')
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            monkeypatch.setattr(runtime, '_graph', runtime._build_graph(saver))
            await send('按附件创建', documents)
            initial = runtime._first_interrupt_value(await runtime._graph.aget_state(runtime._graph_config('user', sid)))
            await send('查询 MfgOrder')
            assert len(queried) == 1 and not calls
            pending = runtime._first_interrupt_value(await runtime._graph.aget_state(runtime._graph_config('user', sid)))
            assert pending['excel_preview']['id'] == initial['excel_preview']['id']
        memory.init_memory()
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            monkeypatch.setattr(runtime, '_graph', runtime._build_graph(saver))
            result = await send('确认')
            assert not any('工作流恢复失败' in event for event in result)
            assert calls[0]['cdo_name'] == 'AssetTrace_6381'
            assert any(m.get('name') == 'get_designer_entity' for m in memory.get_user_messages('user', sid))
    asyncio.run(run())


def test_repeated_failed_design_is_not_reexecuted(api, revision_runtime, monkeypatch):
    attempts = []
    async def failed_tool(**kwargs):
        attempts.append(kwargs)
        raise ValueError('vendor failed; actual state must be checked')
    monkeypatch.setattr(runtime, 'get_tool_func', lambda name: failed_tool)
    async def model(**kwargs):
        proposal = SimpleNamespace(index=0, id=str(len(kwargs['messages'])), function=SimpleNamespace(
            name='generate_designer_cdo_package', arguments=json.dumps({'mdb_file': 'base.mdb', 'expected_sha256': 'a' * 64,
            'cdo_name': 'ExRepeat', 'parent_cdo': 'NamedDataObject', 'fields': [{'name': 'ExStatus', 'data_type': 'Integer'}]})))
        return text_stream(calls=[proposal])
    monkeypatch.setattr(runtime._client.chat.completions, 'create', model)
    sid = api.post('/sessions/user/new').json()['session_id']
    response = api.post('/chat', json={'username': 'user', 'session_id': sid, 'message': '创建一个CDO'})
    assert len(attempts) == 1 and '相同参数和来源版本' in response.text


def test_tool_schema_requires_action():
    from tools import mcp
    async def schema():
        tool = next(tool for tool in await mcp.list_tools() if tool.name == 'generate_designer_design_package')
        operation = tool.parameters['properties']['operations']['items']
        assert 'action' in operation['required']
        assert 'create_cdo' in operation['properties']['action']['enum']
    asyncio.run(schema())


def test_missing_source_is_rejected_before_preview():
    from designer.design_contract import validate_calls
    call = {'function': {'name': 'generate_designer_design_package', 'arguments': json.dumps({
        'operations': [{'action': 'create_cdo', 'name': 'ExExample', 'parent': 'NamedDataObject'}]})}}
    with pytest.raises(ValueError, match='参数不完整'):
        validate_calls([call])


def test_question_does_not_change_table_requirement_or_goal():
    task = chat_context.update_task({}, '只创建 CDO，不要建表', [])
    after = chat_context.update_task(task, '为什么需要物理表？', [])
    assert after == task and after['requires_table'] is False


def test_old_session_uses_current_rules_without_losing_user_requirements(isolated_store, revision_runtime, monkeypatch):
    from agent.prompts import SYSTEM_PROMPT
    sid = memory.create_session('user')
    messages = memory.get_user_messages('user', sid)
    messages[0]['content'] = '过期规则：不支持新建CDO'
    messages.append({'role': 'user', 'content': '需要保留原字段和工作区200'})
    observed = []
    async def model(**kwargs):
        observed.extend(kwargs['messages'])
        return text_stream('原要求已保留。')
    monkeypatch.setattr(runtime._client.chat.completions, 'create', model)
    async def run():
        return [event async for event in runtime.langgraph_chat_stream('user', '查询当前状态', sid)]
    asyncio.run(run())
    assert observed[0]['content'] == SYSTEM_PROMPT
    assert any(m.get('content') == '需要保留原字段和工作区200' for m in observed)


@pytest.mark.parametrize('unchanged', ['其他不变', '其余要求不变'])
def test_rename_otherwise_unchanged_cannot_drop_a_field(api, revision_runtime, monkeypatch, unchanged):
    calls, _ = revision_runtime
    sid = api.post('/sessions/user/new').json()['session_id']
    attachment = upload(api, sid).json()
    api.post('/chat', json={'username': 'user', 'session_id': sid, 'message': '按附件创建', 'attachment_ids': [attachment['id']]})
    async def model(**kwargs):
        proposal = SimpleNamespace(index=0, id=str(len(kwargs['messages'])), function=SimpleNamespace(
            name='generate_designer_cdo_package', arguments=json.dumps({'mdb_file': 'base.mdb', 'expected_sha256': 'a' * 64,
            'cdo_name': 'AssetTrace_6382', 'parent_cdo': 'NamedDataObject', 'fields': [{'name': 'DifferentField', 'data_type': 'String', 'max_length': 200}]})))
        payload = json.loads(proposal.function.arguments)
        payload['description'] = 'retry-' + str(len(kwargs['messages']))
        proposal.function.arguments = json.dumps(payload)
        return text_stream(calls=[proposal])
    monkeypatch.setattr(runtime._client.chat.completions, 'create', model)
    result = api.post('/chat', json={'username': 'user', 'session_id': sid, 'message': 'AssetTrace_6381换成6382，' + unchanged})
    assert not calls and '遗漏或改变了原字段属性' in result.text


def test_parent_selection_cannot_be_ignored_by_replanned_creation(api, revision_runtime):
    calls, _ = revision_runtime
    sid = api.post('/sessions/user/new').json()['session_id']
    attachment = upload(api, sid).json()
    api.post('/chat', json={'username': 'user', 'session_id': sid, 'message': '按附件创建', 'attachment_ids': [attachment['id']]})
    result = api.post('/chat', json={'username': 'user', 'session_id': sid,
        'message': '将 AssetTrace_6381 的父对象改为 Product，其余要求不变，请重新预检并生成预览。'})
    assert not calls and '当前计划没有应用此选择' in result.text
    assert api.get(f'/history/user?session_id={sid}').json()['pending_excel_preview'] is None
