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


def test_cancelled_title_does_not_cancel_answer(title_store, monkeypatch):
    async def title(_):
        raise asyncio.CancelledError()
    monkeypatch.setattr(llm_client, 'generate_title', title)
    sid = memory.create_session('user')
    initial = first_message_title('查询Product对象字段')
    memory.get_user_messages('user', sid).append({'role': 'user', 'content': '查询Product对象字段'})
    memory.update_session_title('user', sid, initial)

    async def scenario():
        async def answer():
            await asyncio.sleep(0)
            yield 'answer'
        assert [event async for event in runtime._stream_with_title(answer(), 'user', sid, '查询Product对象字段', initial)] == ['answer']
    asyncio.run(scenario())
    persisted = json.loads((title_store / 'user' / f'{sid}.json').read_text(encoding='utf-8'))
    assert persisted['title'] == initial


def test_slow_title_never_blocks_first_answer_or_stream_completion(title_store, monkeypatch):
    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def title(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        monkeypatch.setattr(llm_client, 'generate_title', title)
        sid = memory.create_session('user')
        async def answer():
            await started.wait()
            yield 'first answer chunk'
        async def consume():
            return [event async for event in runtime._stream_with_title(answer(), 'user', sid, '查询对象', '新对话 1')]
        assert await asyncio.wait_for(consume(), 1) == ['first answer chunk']
        assert cancelled.is_set()
    asyncio.run(scenario())


def test_stopping_answer_closes_title_and_graph_tasks(title_store, monkeypatch):
    async def scenario():
        started, title_closed, graph_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def title(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                title_closed.set()
        async def answer():
            try:
                await asyncio.Event().wait()
                yield 'unreachable'
            finally:
                graph_closed.set()
        monkeypatch.setattr(llm_client, 'generate_title', title)
        async def consume():
            async for _ in runtime._stream_with_title(answer(), 'user', 'sid', '查询对象', '新对话'):
                pass
        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert title_closed.is_set() and graph_closed.is_set()
    asyncio.run(scenario())
