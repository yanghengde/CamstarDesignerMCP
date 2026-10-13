"""HTTP disconnects cannot cancel inference, saved design or later graph steps."""
import asyncio
import json
from pathlib import Path
import subprocess
import shutil
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.memory import InMemorySaver
import pytest

from agent import chat_jobs, memory, langgraph_runtime as runtime, llm_client
from web import routes
from tests.test_excel_attachments import isolated_store


@pytest.fixture
def background_runtime(isolated_store, monkeypatch):
    gate, release = asyncio.Event(), asyncio.Event()
    state = {'phase': 'inference', 'writes': 0, 'inferences': 0}

    async def title(_):
        return '后台设计测试'

    async def read(**kwargs):
        return {'read_only': True, 'source_sha256': 'a' * 64}

    async def save(**kwargs):
        if state['phase'] == 'tool':
            gate.set()
            await release.wait()
        state['writes'] += 1
        (isolated_store / 'saved.mdb').write_bytes(b'private test design')
        return {'status': 'saved_to_test_copy_and_exported', 'working_mdb': str(isolated_store / 'saved.mdb')}

    async def model(**kwargs):
        state['inferences'] += 1
        tool_messages = [message for message in kwargs['messages'] if message['role'] == 'tool']
        if not tool_messages:
            if state['phase'] == 'inference':
                gate.set()
                await release.wait()
            name, args, call_id = 'get_designer_environment', {}, 'read-background'
        elif tool_messages[-1]['name'] == 'get_designer_environment':
            name, args, call_id = 'generate_designer_cdo_package', {
                'mdb_file': 'test-only.mdb', 'expected_sha256': 'a' * 64, 'workspace': '200',
                'cdo_name': 'ExBackground', 'parent_cdo': 'NamedDataObject',
                'fields': [{'name': 'ExBackgroundText', 'data_type': 'String', 'max_length': 200}],
            }, 'write-background'
        else:
            name, args, call_id = None, {}, None
        delta = SimpleNamespace(content='设计已保存并核对。' if name is None else None,
                                tool_calls=[SimpleNamespace(index=0, id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))] if name else None)
        async def chunks():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])
        return chunks()

    monkeypatch.setattr(llm_client, 'generate_title', title)
    monkeypatch.setattr(runtime, '_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=model))))
    monkeypatch.setattr(runtime, '_graph', runtime._build_graph(InMemorySaver()))
    monkeypatch.setattr(runtime, 'get_tool_func', lambda name: save if name == 'generate_designer_cdo_package' else read)
    monkeypatch.setattr(runtime, 'record_perf', lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, 'record_tool_outcome', lambda **kwargs: None)
    monkeypatch.setattr(runtime, 'build_runtime_experience_context', lambda *args: '')
    return gate, release, state


async def disconnected_request(app, session, disconnect, sent):
    first = True
    async def receive():
        nonlocal first
        if first:
            first = False
            return {'type': 'http.request', 'body': json.dumps({'username': 'user', 'session_id': session, 'message': '创建测试CDO'}).encode(), 'more_body': False}
        await disconnect.wait()
        return {'type': 'http.disconnect'}
    async def send(message):
        sent.append(message)
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'}, 'http_version': '1.1',
             'method': 'POST', 'path': '/chat', 'raw_path': b'/chat', 'query_string': b'',
             'headers': [(b'content-type', b'application/json')], 'client': ('127.0.0.1', 1234), 'server': ('test', 80), 'scheme': 'http'}
    await app(scope, receive, send)


@pytest.mark.parametrize('phase', ['inference', 'tool'])
def test_leaving_page_continues_whole_graph_and_restores_history(background_runtime, isolated_store, phase):
    gate, release, state = background_runtime
    state['phase'] = phase
    sid = memory.create_session('user')
    app = FastAPI(); app.include_router(routes.router)
    async def run():
        disconnect = asyncio.Event()
        sent = []
        request = asyncio.create_task(disconnected_request(app, sid, disconnect, sent))
        try:
            await asyncio.wait_for(gate.wait(), 3)
            disconnect.set()
            await asyncio.wait_for(request, 3)
            assert next(message for message in sent if message['type'] == 'http.response.start')['status'] == 200
            history = await routes.history_endpoint('user', sid)
            assert history['background_running'] and history['background_chat']['status'] == 'running'
            with pytest.raises(ValueError, match='正在运行'):
                chat_jobs.start('user', sid, lambda: llm_client.chat_stream('user', '重复提交', sid))
            release.set()
            await asyncio.wait_for(chat_jobs.drain(), 3)
            history = await routes.history_endpoint('user', sid)
            assert not history['background_running'] and history['background_chat']['status'] == 'completed'
            assert history['messages'][-1]['content'] == '设计已保存并核对。'
            assert state['writes'] == 1 and state['inferences'] == 3
            assert sum(message.get('content') == '创建测试CDO' for message in history['messages']) == 1
        finally:
            disconnect.set(); release.set()
            await chat_jobs.drain()
            await asyncio.gather(request, return_exceptions=True)
    asyncio.run(run())
    assert (isolated_store / 'saved.mdb').is_file()
    memory.init_memory()
    assert memory.get_user_messages('user', sid)[-1]['content'] == '设计已保存并核对。'


def test_explicit_stop_and_stale_job_id(background_runtime):
    gate, release, state = background_runtime
    sid = memory.create_session('user')
    async def run():
        response = await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='创建测试CDO'))
        try:
            await asyncio.wait_for(gate.wait(), 3)
            job_id = response.headers['x-chat-job-id']
            with pytest.raises(HTTPException) as rejected:
                await routes.stop_chat_endpoint(routes.StopChatRequest(username='user', session_id=sid, job_id='stale'))
            assert rejected.value.status_code == 409 and chat_jobs.running('user', sid)
            result = await routes.stop_chat_endpoint(routes.StopChatRequest(username='user', session_id=sid, job_id=job_id))
            assert result['status'] == 'stopped' and not chat_jobs.running('user', sid)
            assert state['writes'] == 0
        finally:
            release.set()
            await chat_jobs.drain()
    asyncio.run(run())


def test_stop_during_save_waits_for_actual_result_before_history_notice(background_runtime):
    from designer import progress_store
    gate, release, state = background_runtime
    state['phase'] = 'tool'
    sid = memory.create_session('user')
    async def run():
        response = await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='创建测试CDO'))
        try:
            await asyncio.wait_for(gate.wait(), 3)
            await routes.stop_chat_endpoint(routes.StopChatRequest(username='user', session_id=sid, job_id=response.headers['x-chat-job-id']))
            history = await routes.history_endpoint('user', sid)
            assert history['background_running'] and history['background_chat']['status'] == 'stopped'
            assert not any(message.get('chat_job_id') for message in history['messages'])
            release.set()
            await asyncio.wait_for(progress_store.drain(), 3)
            history = await routes.history_endpoint('user', sid)
            assert not history['background_running'] and state['writes'] == 1 and state['inferences'] == 2
            outstanding = set()
            for message in history['messages']:
                if message.get('tool_calls'):
                    assert not outstanding
                    outstanding.update(call['id'] for call in message['tool_calls'])
                elif message['role'] == 'tool':
                    outstanding.remove(message['tool_call_id'])
                elif message['role'] == 'assistant':
                    assert not outstanding
            assert not outstanding and history['messages'][-1].get('chat_job_id')
        finally:
            release.set()
            await chat_jobs.drain(); await progress_store.drain()
    asyncio.run(run())


def test_shutdown_waits_for_detached_job(background_runtime):
    gate, release, state = background_runtime
    sid = memory.create_session('user')
    async def run():
        await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='创建测试CDO'))
        await asyncio.wait_for(gate.wait(), 3)
        shutdown = asyncio.create_task(chat_jobs.drain())
        await asyncio.sleep(0)
        assert not shutdown.done()
        release.set()
        await asyncio.wait_for(shutdown, 3)
        assert state['writes'] == 1
    asyncio.run(run())


def test_server_restart_does_not_claim_old_job_is_running(isolated_store):
    sid = memory.create_session('user')
    memory.user_memories['user']['sessions'][sid]['chat_job'] = {'id': 'old', 'status': 'running'}
    info = chat_jobs.status('user', sid)
    assert info['status'] == 'interrupted' and '服务已重启' in info['error']


def test_stop_before_producer_starts_does_not_leave_a_running_job(isolated_store, monkeypatch):
    monkeypatch.setattr(runtime, '_graph', None)
    sid = memory.create_session('user')
    started = []
    async def factory():
        started.append(True)
        yield 'data: {"type":"done","reply":"完成"}\n\n'
    async def run():
        job = chat_jobs.start('user', sid, factory)
        result = await chat_jobs.stop('user', sid, job.info['id'])
        assert result['status'] == 'stopped' and not started and not chat_jobs.running('user', sid)
        assert [event async for event in job.stream()]
    asyncio.run(run())


def test_switching_session_does_not_let_old_stream_reset_new_view():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the browser function check')
    source = (Path(__file__).parents[1] / 'static/index.html').read_text(encoding='utf-8')
    detach = source.split('        function detachChatView() {', 1)[1].split('        async function stopCurrentChat()', 1)[0]
    send = source.split('        async function handleSend() {', 1)[1].split("        newChatTopBtn.addEventListener", 1)[0]
    script = r"""
const assert=require('node:assert/strict');
let currentSessionId='old',currentUsername='user',busy=false,backgroundRunning=false,backgroundJobId=null,backgroundTimer;
let historyLoading=false,attachmentUploading=false,attachmentDrafts=[],abortController=null;
const input={value:'创建对象',style:{},focus(){}};
const typingIndicator={hidden:true,querySelector(){return {textContent:''}}};
const updateControls=()=>{},scrollToBottom=()=>{},appendMessage=()=>({parentElement:{remove(){}}});
let resolveResponse;const fetch=()=>new Promise(resolve=>resolveResponse=resolve);
""" + 'function detachChatView() {' + detach + 'async function handleSend() {' + send + r"""
(async()=>{
    const old=handleSend();const previousController=abortController;
    detachChatView();currentSessionId='new';busy=true;
    const newController=new AbortController();abortController=newController;
    resolveResponse({ok:true,body:{},headers:{get:name=>name==='X-Chat-Job-ID'?'old-job':'old'}});
    await old;
    assert.equal(previousController.signal.aborted,true);
    assert.equal(abortController,newController);assert.equal(busy,true);assert.equal(currentSessionId,'new');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run([node, '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_stop_button_does_not_discard_an_already_completed_response():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the browser function check')
    source = (Path(__file__).parents[1] / 'static/index.html').read_text(encoding='utf-8')
    stop = source.split('        async function stopCurrentChat() {', 1)[1].split('        function showWelcome()', 1)[0]
    script = r"""
const assert=require('node:assert/strict');
let abortController=new AbortController();abortController.jobId='job';
const currentUsername='user',currentSessionId='session',backgroundJobId='job',busy=true;
let status='completed';const fetchJson=async()=>({status});const showNotice=()=>{};
""" + 'async function stopCurrentChat() {' + stop + r"""
(async()=>{
    await stopCurrentChat();assert.equal(abortController.signal.aborted,false);
    status='stopped';await stopCurrentChat();assert.equal(abortController.signal.aborted,true);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run([node, '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_returning_page_recovers_results_and_distinguishes_waiting_for_approval():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the browser function check')
    source = (Path(__file__).parents[1] / 'static/index.html').read_text(encoding='utf-8')
    monitor = source.split('        function monitorBackground(data) {', 1)[1].split('        async function refreshBackground()', 1)[0]
    script = r"""
const assert=require('node:assert/strict');
let backgroundRunning=false,backgroundJobId=null,backgroundTimer,busy=false,historyLoading=false,currentSessionId='session';
const hint={hidden:true,textContent:''};const document={getElementById:()=>hint};
const typingIndicator={hidden:true,querySelector:()=>({textContent:''})};
const updateControls=()=>{},clearTimeout=()=>{},setTimeout=()=>0;
let reloads=0,notices=[];const loadHistory=()=>reloads++;const showNotice=text=>notices.push(text);
""" + 'function monitorBackground(data) {' + monitor + r"""
monitorBackground({background_running:true,background_chat:{id:'job',status:'running'}});
assert.equal(backgroundJobId,'job');assert.equal(hint.hidden,false);assert.equal(typingIndicator.hidden,false);
monitorBackground({background_running:false,background_chat:{id:'job',status:'waiting_approval'}});
assert.equal(backgroundJobId,null);assert.equal(reloads,1);assert.match(hint.textContent,/等待确认/);
assert.match(notices.at(-1),/等待确认/);assert.doesNotMatch(notices.at(-1),/已完成/);
monitorBackground({background_running:true,background_chat:{id:'job2',status:'running'}});
monitorBackground({background_running:false,background_chat:{id:'job2',status:'completed'}});
assert.equal(reloads,2);assert.match(notices.at(-1),/处理已完成/);
"""
    result = subprocess.run([node, '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
