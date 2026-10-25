"""Empty chats stay temporary; the first message makes them history."""
import json
from pathlib import Path
import shutil
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent import memory, langgraph_runtime as runtime
from web import routes


@pytest.fixture
def drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, 'CHAT_USERNAME', 'user')
    monkeypatch.setattr(memory, 'SESSIONS_DIR', str(tmp_path / 'sessions'))
    monkeypatch.setattr(memory, 'MEMORY_FILE', str(tmp_path / 'legacy.json'))
    monkeypatch.setattr(memory, 'user_memories', {})
    monkeypatch.setattr(runtime, 'user_memories', memory.user_memories)
    monkeypatch.setattr(runtime, '_graph', None)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), tmp_path


@pytest.mark.parametrize('mode', ['designer', 'clf'])
def test_repeated_new_chats_are_usable_but_never_saved_or_listed(drafts, mode):
    client, _ = drafts
    for _ in range(3):
        response = client.post(f'/sessions/user/new?mode={mode}')
        assert response.status_code == 200
        sid = response.json()['session_id']
        assert client.get(f'/history/user?session_id={sid}').status_code == 200
        memory.update_session_title('user', sid, '临时草稿')
        memory.save_user_session('user', sid)
        memory.save_memory()
        assert client.get(f'/sessions/user?mode={mode}').json()['sessions'] == []
        assert not (Path(memory.get_user_dir('user')) / f'{sid}.json').exists()
    metadata = json.loads((Path(memory.get_user_dir('user')) / 'metadata.json').read_text(encoding='utf-8'))
    assert metadata['active_session'] is None
    memory.init_memory()
    assert memory.get_sessions('user') == []


def test_first_message_persists_and_empty_draft_does_not_reappear_after_restart(drafts):
    client, _ = drafts
    sid = client.post('/sessions/user/new').json()['session_id']
    messages = memory.get_user_messages('user', sid)
    messages.append({'role': 'user', 'content': '查询 Product 对象'})
    memory.save_user_session('user', sid)
    assert [s['id'] for s in client.get('/sessions/user').json()['sessions']] == [sid]
    assert json.loads((Path(memory.get_user_dir('user')) / f'{sid}.json').read_text(encoding='utf-8'))['messages'][-1]['role'] == 'user'
    draft = client.post('/sessions/user/new').json()['session_id']
    assert client.get(f'/history/user?session_id={draft}').json()['messages'][0]['role'] == 'system'
    assert [s['id'] for s in client.get('/sessions/user').json()['sessions']] == [sid]
    memory.init_memory()
    assert [s['id'] for s in client.get('/sessions/user').json()['sessions']] == [sid]
    assert client.get(f'/history/user?session_id={sid}').json()['messages'][-1]['content'] == '查询 Product 对象'
    assert client.get(f'/history/user?session_id={draft}').status_code == 404


def test_old_empty_files_and_monolithic_drafts_are_ignored(drafts):
    client, root = drafts
    folder = Path(memory.get_user_dir('user'))
    folder.mkdir(parents=True)
    for sid, messages in [('empty', []), ('system', [{'role': 'system', 'content': 'rules'}]),
                          ('blank', [{'role': 'user', 'content': ' \n '}]),
                          ('real', [{'role': 'user', 'content': '已有问题'}])]:
        (folder / f'{sid}.json').write_text(json.dumps({'id': sid, 'messages': messages}), encoding='utf-8')
    (root / 'legacy.json').write_text(json.dumps({'user': {'active_session': 'legacy', 'sessions': {
        'legacy': {'id': 'legacy', 'messages': []}}}}), encoding='utf-8')
    for _ in range(2):
        memory.init_memory()
        assert [s['id'] for s in client.get('/sessions/user').json()['sessions']] == ['real']
    assert (folder / 'empty.json').exists()


@pytest.mark.parametrize('mode', ['designer', 'clf'])
def test_chat_api_saves_draft_on_first_message(drafts, monkeypatch, mode):
    client, _ = drafts

    async def reply(username, message, session_id):
        memory.get_user_messages(username, session_id).append({'role': 'user', 'content': message})
        memory.save_user_session(username, session_id)
        yield 'data: ' + json.dumps({'type': 'message_saved', 'session_id': session_id}) + '\n\n'
        yield 'data: ' + json.dumps({'type': 'done', 'reply': '收到'}) + '\n\n'

    monkeypatch.setattr(routes, 'chat_stream', reply)
    sid = client.post(f'/sessions/user/new?mode={mode}').json()['session_id']
    body = {'username': 'user', 'session_id': sid, 'message': ' '}
    assert client.post('/chat', json=body).status_code == 400
    assert client.get('/sessions/user').json()['sessions'] == []
    response = client.post('/chat', json={**body, 'message': '查询对象'})
    assert response.status_code == 200
    assert 'message_saved' in response.text
    assert [s['id'] for s in client.get(f'/sessions/user?mode={mode}').json()['sessions']] == [sid]
    memory.init_memory()
    assert memory.get_user_messages('user', sid)[-1]['content'] == '查询对象'


def test_browser_keeps_draft_selected_when_recent_list_contains_only_saved_chats():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the browser function check')
    source = (Path(__file__).parents[1] / 'static/index.html').read_text(encoding='utf-8')
    refresh = source.split('        async function refreshSessionsList() {', 1)[1].split('        async function selectSession(', 1)[0]
    initialize = source.split('        async function initializeApp() {', 1)[1].split('        async function handleSend()', 1)[0]
    new_chat = source.split("        newChatTopBtn.addEventListener('click',", 1)[1].split("        for (const [id, view]", 1)[0]
    script = r"""
const assert=require('node:assert/strict');
let currentSessionId=null,currentUsername='user',sessions=[],historyLoading=false;
let attachmentUploading=false,reviewPreparing=false,attachmentDrafts=[],created=0;
const clfMode=true,location={search:'?session_id=handoff-draft'},window={};
const loaded=[],notices=[],document={getElementById:()=>({className:'',textContent:''})};
const sessionListUI={},input={value:'',style:{},focus(){}};
const updateControls=()=>{},renderSessions=()=>{},renderAttachmentDrafts=()=>{};
const detachChatView=()=>{},setView=()=>{},viewFromLocation=()=> 'design';
const showNotice=text=>notices.push(text),loadHistory=async()=>{loaded.push(currentSessionId);historyLoading=false;};
const fetchJson=async url=>{
    if(url==='/config')return {username:'user'};
    if(url.includes('/new?'))return {session_id:'draft-'+(++created)};
    if(url.startsWith('/sessions/'))return {sessions:[{id:'saved',title:'已有问题'}]};
    if(url.includes('session_id=handoff-draft'))return {messages:[]};
    throw Error('Unexpected request: '+url);
};
let click;const newChatTopBtn={addEventListener:(_,handler)=>click=handler};
""" + 'async function refreshSessionsList() {' + refresh + 'async function initializeApp() {' + initialize + "newChatTopBtn.addEventListener('click'," + new_chat + r"""
(async()=>{
    await initializeApp();
    assert.equal(currentSessionId,'handoff-draft');assert.equal(created,0);
    assert.deepEqual(loaded,['handoff-draft']);assert.deepEqual(notices,[]);
    await click();assert.equal(currentSessionId,'draft-1');
    await click();assert.equal(currentSessionId,'draft-2');
    assert.deepEqual(sessions.map(s=>s.id),['saved']);
    assert.deepEqual(loaded,['handoff-draft','draft-1','draft-2']);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run([node, '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
