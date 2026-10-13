"""Mid-run input cannot race saves, execute old plans or bypass confirmation."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from fastapi import HTTPException
from langgraph.checkpoint.memory import InMemorySaver
import pytest

from agent import chat_jobs, chat_requirements, chat_intent, memory, langgraph_runtime as runtime, llm_client
from designer import progress_store
from tests.test_excel_attachments import isolated_store
from web import routes


def assert_pairs(messages, allow_pending=False):
    pending = set()
    for message in messages:
        if message.get('tool_calls'):
            assert not pending
            pending.update(call['id'] for call in message['tool_calls'])
        elif message['role'] == 'tool':
            assert message['tool_call_id'] in pending
            pending.remove(message['tool_call_id'])
        else:
            assert not pending
    if not allow_pending:
        assert not pending


@pytest.fixture
def live_runtime(isolated_store, monkeypatch):
    gate, release = asyncio.Event(), asyncio.Event()
    state = {'phase': 'inference', 'started': [], 'saved': [], 'inferences': 0, 'model_inputs': []}

    def call(name, identity):
        args = {'mdb_file': 'private-test.mdb', 'expected_sha256': 'a' * 64, 'workspace': '200',
                'cdo_name': name, 'parent_cdo': 'NamedDataObject',
                'fields': [{'name': 'Description', 'data_type': 'String', 'max_length': 200}]}
        return SimpleNamespace(index=identity, id=f'{name}-{identity}',
                               function=SimpleNamespace(name='generate_designer_cdo_package', arguments=json.dumps(args)))

    async def save(**kwargs):
        name = kwargs['cdo_name']
        state['started'].append(name)
        if state['phase'] == 'tool' and len(state['started']) == 1:
            gate.set()
            await release.wait()
        state['saved'].append(name)
        return {'status': 'saved_to_test_copy_and_exported', 'source_sha256': 'b' * 64}

    async def model(**kwargs):
        state['inferences'] += 1
        state['model_inputs'].append(kwargs)
        assert_pairs(kwargs['messages'])
        if state['phase'] == 'inference' and state['inferences'] == 1:
            gate.set()
            await release.wait()
        latest = next(message['content'] for message in reversed(kwargs['messages']) if message['role'] == 'user')
        responses = [message for message in kwargs['messages'] if message['role'] == 'tool']
        if '为什么' in latest:
            assert all(chat_intent.read_only_tool(tool['function']['name']) for tool in kwargs['tools'])
            calls, text = None, '字段按业务需要选择，已完成的设计保持有效。'
        elif 'ExNew' in latest:
            calls, text = [call('ExNew', 0)], None
        elif responses:
            calls, text = None, '设计处理完成。'
        else:
            calls, text = [call('ExOld', 0), call('ExOldNext', 1)], None
        async def chunks():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=calls))])
        return chunks()

    async def title(_):
        return '运行中补充测试'

    monkeypatch.setattr(llm_client, 'generate_title', title)
    monkeypatch.setattr(runtime, '_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=model))))
    monkeypatch.setattr(runtime, '_graph', runtime._build_graph(InMemorySaver()))
    monkeypatch.setattr(runtime, 'get_tool_func', lambda name: save)
    monkeypatch.setattr(runtime, 'record_perf', lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, 'record_tool_outcome', lambda **kwargs: None)
    monkeypatch.setattr(runtime, 'build_runtime_experience_context', lambda *args: '')
    return gate, release, state


async def append(sid, job_id, text='ExOld改为ExNew', request_id='requirement-one'):
    return await routes.chat_requirement_endpoint(routes.ChatRequirementRequest(
        username='user', session_id=sid, job_id=job_id, request_id=request_id, message=text))


@pytest.mark.parametrize('phase', ['inference', 'tool'])
def test_revision_cancels_only_unstarted_calls_and_requires_a_new_preview(live_runtime, phase):
    gate, release, state = live_runtime
    state['phase'] = phase
    sid = memory.create_session('user')
    async def run():
        response = await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='创建两个测试对象'))
        try:
            await asyncio.wait_for(gate.wait(), 3)
            result = await append(sid, response.headers['x-chat-job-id'])
            assert result['status'] == 'queued'
            history = await routes.history_endpoint('user', sid)
            assert history['chat_requirements'][0]['content'].startswith('ExOld改为ExNew')
            assert history['background_running']
            release.set()
            await asyncio.wait_for(chat_jobs.drain(), 3)
            history = await routes.history_endpoint('user', sid)
            assert history['background_chat']['status'] == 'waiting_approval'
            assert history['chat_requirements'][0]['status'] == 'incorporated'
            assert (await append(sid, response.headers['x-chat-job-id']))['status'] == 'incorporated'
            assert history['pending_excel_preview']['objects'][0]['name'] == 'ExNew'
            assert state['saved'] == (['ExOld'] if phase == 'tool' else [])
            assert 'ExOldNext' not in state['started'] and 'ExNew' not in state['started']
            assert_pairs(history['messages'], allow_pending=True)
            assert sum(message.get('requirement_id') == result['id'] for message in history['messages']) == 1
            await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='确认'))
            await asyncio.wait_for(chat_jobs.drain(), 3)
            assert state['saved'][-1] == 'ExNew'
            assert_pairs((await routes.history_endpoint('user', sid))['messages'])
        finally:
            release.set()
            await chat_jobs.drain(); await progress_store.drain()
    asyncio.run(run())
    memory.init_memory()
    assert chat_requirements.records('user', sid)[0]['status'] == 'incorporated'


def test_questions_wait_for_current_batch_and_use_read_only_tools(live_runtime):
    gate, release, state = live_runtime
    state['phase'] = 'tool'
    sid = memory.create_session('user')
    async def run():
        response = await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='创建两个测试对象'))
        try:
            await asyncio.wait_for(gate.wait(), 3)
            await append(sid, response.headers['x-chat-job-id'], '为什么使用这个字段类型？')
            release.set()
            await asyncio.wait_for(chat_jobs.drain(), 3)
            history = await routes.history_endpoint('user', sid)
            assert state['saved'] == ['ExOld', 'ExOldNext']
            assert history['background_chat']['status'] == 'completed'
            assert '业务需要' in history['messages'][-1]['content']
            assert_pairs(history['messages'])
        finally:
            release.set(); await chat_jobs.drain()
    asyncio.run(run())


def test_idempotency_stale_jobs_and_session_ownership(live_runtime):
    gate, release, state = live_runtime
    sid, other = memory.create_session('user'), memory.create_session('user')
    async def run():
        response = await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='创建两个测试对象'))
        try:
            await asyncio.wait_for(gate.wait(), 3)
            job = response.headers['x-chat-job-id']
            first = await append(sid, job)
            assert (await append(sid, job))['id'] == first['id']
            assert len(chat_requirements.records('user', sid)) == 1
            for target, job_id, text in [(sid, 'stale', 'ExOld改为ExNew'), (other, job, 'ExOld改为ExNew'), (sid, job, 'ExOld改为ExDifferent')]:
                with pytest.raises(HTTPException) as rejected:
                    await append(target, job_id, text)
                assert rejected.value.status_code == 409
            with pytest.raises(HTTPException) as rejected:
                await routes.chat_requirement_endpoint(routes.ChatRequirementRequest(
                    username='another-user', session_id=sid, job_id=job, request_id='wrong-owner', message='修改字段'))
            assert rejected.value.status_code == 404
        finally:
            release.set(); await chat_jobs.drain()
    asyncio.run(run())


def test_stop_keeps_unhandled_input_without_cancelling_the_active_save(live_runtime):
    gate, release, state = live_runtime
    state['phase'] = 'tool'
    sid = memory.create_session('user')
    async def run():
        response = await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='创建两个测试对象'))
        try:
            await asyncio.wait_for(gate.wait(), 3)
            job = response.headers['x-chat-job-id']
            await append(sid, job)
            await chat_jobs.stop('user', sid, job)
            assert chat_requirements.records('user', sid)[0]['status'] == 'unhandled'
            release.set()
            await asyncio.wait_for(progress_store.drain(), 3)
            assert state['saved'] == ['ExOld']
        finally:
            release.set(); await chat_jobs.drain(); await progress_store.drain()
    asyncio.run(run())


def test_confirmed_excel_batch_preserves_partial_execution_and_supersedes_old_preview(live_runtime, monkeypatch):
    gate, release, state = live_runtime
    state['phase'] = 'tool'
    source = [{'id': 'test-attachment', 'name': 'test.xlsx', 'sha256': 'a' * 64}]
    monkeypatch.setattr(runtime.excel_preview, 'sources', lambda _: source)
    monkeypatch.setattr(runtime.excel_preview, 'validate_sources', lambda *args: None)
    sid = memory.create_session('user')
    async def run():
        await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='按已有附件创建两个测试对象'))
        await chat_jobs.drain()
        original = (await routes.history_endpoint('user', sid))['pending_excel_preview']
        response = await routes.chat_endpoint(routes.ChatRequest(username='user', session_id=sid, message='确认'))
        try:
            await asyncio.wait_for(gate.wait(), 3)
            await append(sid, response.headers['x-chat-job-id'])
            release.set()
            await asyncio.wait_for(chat_jobs.drain(), 3)
            events = [json.loads(event.removeprefix('data: ')) async for event in response.body_iterator]
            statuses = [event for event in events if event.get('type') == 'excel_preview_status' and event['id'] == original['id']]
            assert statuses[-1]['status'] == 'superseded' and statuses[-1]['partial_execution']
            history = await routes.history_endpoint('user', sid)
            old = next(message['excel_preview'] for message in history['messages'] if message.get('excel_preview', {}).get('id') == original['id'])
            assert old['partial_execution'] and old['status'] == 'superseded'
            assert history['pending_excel_preview']['objects'][0]['name'] == 'ExNew'
            assert state['saved'] == ['ExOld']
            assert_pairs(history['messages'], allow_pending=True)
        finally:
            release.set(); await chat_jobs.drain()
    asyncio.run(run())


@pytest.mark.parametrize('pause', [False, True])
def test_input_accepted_at_graph_end_is_not_lost(live_runtime, monkeypatch, pause):
    gate, release, state = live_runtime
    state['phase'] = 'none'
    sid = memory.create_session('user')
    if pause:
        monkeypatch.setattr(runtime, 'evaluate_mutations', lambda *args: SimpleNamespace(
            requires_approval=True, totals=SimpleNamespace(as_dict=lambda: {}), categories=['create']))
        monkeypatch.setattr(runtime, 'approval_prompt', lambda _: '请确认')
    async def factory():
        async for event in llm_client.chat_stream('user', '创建两个测试对象', sid):
            yield event
        gate.set()
        await release.wait()
    async def run():
        job = chat_jobs.start('user', sid, factory)
        try:
            await asyncio.wait_for(gate.wait(), 3)
            await append(sid, job.info['id'])
            release.set()
            await asyncio.wait_for(chat_jobs.drain(), 3)
            history = await routes.history_endpoint('user', sid)
            assert history['pending_excel_preview']['objects'][0]['name'] == 'ExNew'
            assert history['chat_requirements'][0]['status'] == 'incorporated'
            assert_pairs(history['messages'], allow_pending=True)
            assert state['saved'] == ([] if pause else ['ExOld', 'ExOldNext'])
        finally:
            release.set(); await chat_jobs.drain()
    asyncio.run(run())


def test_restart_preserves_unhandled_requirements_for_review(isolated_store):
    sid = memory.create_session('user')
    memory.user_memories['user']['sessions'][sid]['chat_job'] = {'id': 'old', 'status': 'running'}
    chat_requirements.enqueue('user', sid, 'old', 'persisted', '再加一个字段 Note')
    memory.init_memory()
    assert chat_jobs.status('user', sid)['status'] == 'interrupted'
    item = chat_requirements.records('user', sid)[0]
    assert item['status'] == 'unhandled' and item['content'] == '再加一个字段 Note'


@pytest.mark.parametrize(('text', 'kind'), [
    ('再加一个字段 Note，String 长度 200', 'revision'), ('去掉自增字段', 'revision'),
    ('新增对象 ExNew', 'revision'), ('增加字段可以吗？', 'question'),
    ('为什么删除这个字段？', 'question'), ('确认', 'question'),
    ('OwnerEmail 长度 200，持久化', 'revision'), ('还需要一个备注', 'revision'),
    ('字段改为 200 可以吗？', 'question'),
])
def test_requirement_intent(text, kind):
    assert chat_intent.running_intent(text) == kind


def run_browser_script(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the browser function check')
    result = subprocess.run([node, '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_running_composer_supports_sending_and_stopping_at_the_same_time():
    source = (Path(__file__).parents[1] / 'static/index.html').read_text(encoding='utf-8')
    controls = source.split('        function updateControls() {', 1)[1].split('        function monitorBackground', 1)[0]
    run_browser_script(r"""
const assert=require('node:assert/strict');
const element=()=>({disabled:false,hidden:false,setAttribute(){}});
const input=element(),sendBtn=element(),stopBtn=element(),attachBtn=element(),fileInput=element(),newChatTopBtn=element(),messagesDiv=element();
const document={getElementById:element,querySelectorAll:()=>[]};
let busy=true,backgroundRunning=false,backgroundJobId=null,abortController=null,historyLoading=false,attachmentUploading=false,reviewPreparing=false,requirementSubmitting=false,pendingExcelPreview=null,attachmentDrafts=[];
""" + 'function updateControls() {' + controls + r"""
updateControls();assert.equal(input.disabled,true);assert.equal(sendBtn.disabled,true);
abortController={jobId:'accepted'};updateControls();
assert.equal(input.disabled,false);assert.equal(sendBtn.disabled,false);assert.equal(sendBtn.hidden,false);assert.equal(stopBtn.hidden,false);assert.equal(attachBtn.disabled,true);
busy=false;abortController=null;backgroundRunning=true;backgroundJobId='return-job';updateControls();
assert.equal(input.disabled,false);assert.equal(sendBtn.disabled,false);assert.equal(stopBtn.hidden,false);
requirementSubmitting=true;updateControls();assert.equal(sendBtn.disabled,true);
requirementSubmitting=false;backgroundJobId=null;updateControls();assert.equal(sendBtn.disabled,true);
""")


def test_supplement_retries_keep_the_same_id_and_do_not_disrupt_stream_or_new_chat():
    source = (Path(__file__).parents[1] / 'static/index.html').read_text(encoding='utf-8')
    submit = source.split('        async function submitRequirement(text) {', 1)[1].split('        function attachmentCard', 1)[0]
    send = source.split('        async function handleSend() {', 1)[1].split('        newChatTopBtn.addEventListener', 1)[0]
    run_browser_script(r"""
const assert=require('node:assert/strict');
let currentSessionId='original',currentUsername='user',abortController={jobId:'job'},backgroundJobId='job',busy=true,backgroundRunning=true;
let requirementSubmitting=false,requirementRequest=null,attachmentDrafts=[],historyLoading=false,attachmentUploading=false,reviewPreparing=false;
const input={value:'再加一个字段 Note',style:{}};
const updateControls=()=>{},showNotice=()=>{},refreshBackground=()=>{};
let records=[],requests=[],mode='fail',resolveRequest;
const showRequirement=item=>records.push(item);
const fetchJson=async(url,options)=>{
    assert.equal(url,'/chat/requirements');const body=JSON.parse(options.body);requests.push(body);
    if(mode==='fail')throw Error('acknowledgement lost');
    if(mode==='delay')await new Promise(resolve=>resolveRequest=resolve);
    return {id:body.request_id,content:body.message,status:'queued'};
};
""" + 'async function submitRequirement(text) {' + submit + 'async function handleSend() {' + send + r"""
(async()=>{
    const originalController=abortController;
    await handleSend();assert.equal(input.value,'再加一个字段 Note');assert.equal(records.length,0);
    mode='ok';await handleSend();assert.equal(requests[0].request_id,requests[1].request_id);
    assert.equal(records.length,1);assert.equal(input.value,'');assert.equal(abortController,originalController);assert.equal(busy,true);
    input.value='调整长度';mode='delay';const pending=handleSend();
    currentSessionId='new';input.value='new chat draft';resolveRequest();await pending;
    assert.equal(input.value,'new chat draft');assert.equal(records.length,1);assert.equal(busy,true);
})().catch(error=>{console.error(error);process.exitCode=1});
""")
