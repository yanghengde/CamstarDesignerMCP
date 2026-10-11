"""Conversation activity drives recency without losing older history."""
import json
import os
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent import memory
from web import routes


@pytest.fixture
def session_store(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, 'CHAT_USERNAME', 'user')
    monkeypatch.setattr(memory, 'SESSIONS_DIR', str(tmp_path/'sessions'))
    monkeypatch.setattr(memory, 'MEMORY_FILE', str(tmp_path/'absent.json'))
    monkeypatch.setattr(memory, 'user_memories', {})
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(memory, 'time', SimpleNamespace(time=lambda: clock.value))
    return tmp_path, clock


def test_resuming_old_conversation_moves_it_first_and_survives_restart(session_store):
    _, clock = session_store
    old = memory.create_session('user')
    clock.value = 200.0
    new = memory.create_session('user')
    assert [item['id'] for item in memory.get_sessions('user')] == [new, old]
    clock.value = 300.0
    memory.get_user_messages('user', old).append({'role': 'user', 'content': '继续旧对话'})
    memory.save_user_session('user', old)
    assert [item['id'] for item in memory.get_sessions('user')] == [old, new]
    assert memory.get_sessions('user')[0]['created_at'] == 100.0
    assert memory.get_sessions('user')[0]['updated_at'] == 300.0
    clock.value = 400.0
    memory.init_memory()
    assert [item['id'] for item in memory.get_sessions('user')] == [old, new]
    assert memory.get_sessions('user')[0]['updated_at'] == 300.0


def test_reading_renaming_and_bulk_save_do_not_change_activity_time(session_store):
    _, clock = session_store
    old = memory.create_session('user')
    clock.value = 200.0
    new = memory.create_session('user')
    clock.value = 300.0
    memory.set_active_session('user', old)
    memory.get_user_messages('user', old)
    memory.update_session_title('user', old, '查看历史')
    memory.save_memory()
    memory.init_memory()
    assert [item['id'] for item in memory.get_sessions('user')] == [new, old]
    assert memory.get_sessions('user')[1]['updated_at'] == 100.0


def test_legacy_file_times_migrate_once_before_startup_rewrites(session_store):
    root, _ = session_store
    folder = root/'sessions'/'user'; folder.mkdir(parents=True)
    for sid, modified in [('older', 100.0), ('recent', 200.0)]:
        file = folder/f'{sid}.json'
        file.write_text(json.dumps({'id': sid, 'title': sid, 'messages': []}), encoding='utf-8')
        os.utime(file, (modified, modified))
    for _ in range(2):
        memory.init_memory()
        sessions = memory.get_sessions('user')
        assert [item['id'] for item in sessions] == ['recent', 'older']
        assert [item['updated_at'] for item in sessions] == [200.0, 100.0]
    assert json.loads((folder/'recent.json').read_text(encoding='utf-8'))['updated_at'] == 200.0


def test_all_history_remains_accessible_and_api_returns_newest_first(session_store):
    _, clock = session_store
    ids = []
    for number in range(35):
        clock.value = float(number + 100)
        ids.append(memory.create_session('user'))
    app = FastAPI(); app.include_router(routes.router)
    client = TestClient(app)
    result = client.get('/sessions/user').json()['sessions']
    assert len(result) == 35
    assert [item['id'] for item in result] == list(reversed(ids))
    assert memory.get_user_messages('user', ids[0])
    assert memory.get_sessions('another-user') == []


def test_legacy_monolithic_memory_uses_original_file_time(session_store):
    root, _ = session_store
    file = root/'absent.json'
    file.write_text(json.dumps({'user': [{'role': 'user', 'content': '旧版对话'}]}), encoding='utf-8')
    os.utime(file, (150.0, 150.0))
    memory.init_memory()
    assert memory.get_sessions('user')[0]['updated_at'] == 150.0
