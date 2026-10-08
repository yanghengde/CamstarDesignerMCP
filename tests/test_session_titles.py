"""First-message naming, persisted length limits, and model failure recovery."""
import asyncio
import json
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver
import pytest

from agent import memory, llm_client, langgraph_runtime as runtime
from agent.titles import clean_title, first_message_title


@pytest.mark.parametrize('reply', ['“标题：创建Product扩展对象和字段并配置数据库存储映射”', None, '', '新对话'])
def test_model_title_is_bounded_or_uses_first_message(reply, monkeypatch):
    async def create(**kwargs):
        assert '20个字符' in kwargs['messages'][0]['content']
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])
    monkeypatch.setattr(llm_client, 'oai_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    result = asyncio.run(llm_client.generate_title('请帮我查询Product对象及其字段'))
    assert 1 <= len(result) <= 20
    assert result != '新对话'
    assert not result.startswith('标题')


def test_title_model_failure_preserves_useful_name(monkeypatch):
    async def create(**kwargs):
        raise RuntimeError('model unavailable')
    monkeypatch.setattr(llm_client, 'oai_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    assert asyncio.run(llm_client.generate_title('请帮我查询 Product 对象')) == '查询Product对象'
    assert len(clean_title('中文' * 30)) == 20


@pytest.fixture
def title_store(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'SESSIONS_DIR', str(tmp_path))
    monkeypatch.setattr(memory, 'user_memories', {})
    monkeypatch.setattr(runtime, 'user_memories', memory.user_memories)
    return tmp_path


def test_startup_names_old_placeholder_from_first_visible_message_only(title_store, monkeypatch):
    sessions = {
        'old': {'id': 'old', 'title': '新对话 1', 'messages': [
            {'role': 'system', 'content': 'rules'},
            {'role': 'user', 'display_content': '查询Product对象', 'content': '查询Product对象\nFULL ATTACHMENT DATA'},
            {'role': 'user', 'content': '新增别的对象'}]},
        'empty': {'id': 'empty', 'title': '新对话 2', 'messages': []},
        'named': {'id': 'named', 'title': '保留原名称', 'messages': [{'role': 'user', 'content': '更改需求'}]},
    }
    monkeypatch.setattr(memory, 'load_memory', lambda: {'user': {'active_session': 'empty', 'sessions': sessions}})
    memory.init_memory()
    assert sessions['old']['title'] == '查询Product对象'
    assert sessions['empty']['title'] == '新对话 2'
    assert sessions['named']['title'] == '保留原名称'
    assert json.loads((title_store / 'user' / 'old.json').read_text(encoding='utf-8'))['title'] == '查询Product对象'


def test_first_message_title_streams_and_persists_without_followup_rename(title_store, monkeypatch):
    title_calls = []
    async def title(message):
        title_calls.append(message)
        return 'Product对象查询'
    async def create(**kwargs):
        async def chunks():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='已读取设计需求', tool_calls=None))])
        return chunks()
    monkeypatch.setattr(llm_client, 'generate_title', title)
    monkeypatch.setattr(runtime, '_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(runtime, '_graph', runtime._build_graph(InMemorySaver()))
    monkeypatch.setattr(runtime, 'record_perf', lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, 'build_runtime_experience_context', lambda *args: '')
    sid = memory.create_session('user')
    async def scenario():
        events = [json.loads(event[6:]) async for event in runtime.langgraph_chat_stream('user', '请查询Product对象', sid)]
        assert any(event.get('title') == 'Product对象查询' for event in events if event['type'] == 'title_update')
        followup = [json.loads(event[6:]) async for event in runtime.langgraph_chat_stream('user', '再新增其他对象', sid)]
        assert not any(event['type'] == 'title_update' for event in followup)
    asyncio.run(scenario())
    assert title_calls == ['请查询Product对象']
    persisted = json.loads((title_store / 'user' / f'{sid}.json').read_text(encoding='utf-8'))
    assert persisted['title'] == 'Product对象查询'


def test_cancel_during_title_generation_keeps_first_message_name(title_store, monkeypatch):
    async def title(_):
        raise asyncio.CancelledError()
    async def snapshot(_):
        return SimpleNamespace(tasks=[])
    monkeypatch.setattr(llm_client, 'generate_title', title)
    monkeypatch.setattr(runtime, '_graph', SimpleNamespace(aget_state=snapshot))
    sid = memory.create_session('user')
    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            async for _ in runtime.langgraph_chat_stream('user', '查询Product对象字段', sid):
                pass
    asyncio.run(scenario())
    persisted = json.loads((title_store / 'user' / f'{sid}.json').read_text(encoding='utf-8'))
    assert persisted['title'] == first_message_title('查询Product对象字段')
