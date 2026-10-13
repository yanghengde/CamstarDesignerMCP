"""Own chat execution on the server; SSE connections only observe the job."""
import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import time
from uuid import uuid4

from agent import memory, chat_requirements

_jobs = {}
MAX_EVENT_CHARS = 2_000_000


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sse(payload):
    return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'


@dataclass
class Job:
    username: str
    session_id: str
    info: dict
    task: asyncio.Task | None = None
    done: bool = False
    events: deque = field(default_factory=deque)
    sequence: int = 0
    event_chars: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    last_saved: float = 0
    accepting: bool = True

    async def publish(self, event):
        async with self.condition:
            self.sequence += 1
            self.events.append((self.sequence, event))
            self.event_chars += len(event)
            while len(self.events) > 1 and self.event_chars > MAX_EVENT_CHARS:
                self.event_chars -= len(self.events.popleft()[1])
            self.condition.notify_all()

    async def stream(self):
        # Cancelling/closing this iterator must never cancel self.task.
        cursor = 0
        while True:
            async with self.condition:
                await self.condition.wait_for(lambda: self.done or self.sequence > cursor)
                if self.events and cursor < self.events[0][0] - 1:
                    cursor = self.events[0][0] - 1
                    missing = True
                else:
                    missing = False
                batch = [(sequence, event) for sequence, event in self.events if sequence > cursor]
                finished = self.done
            if missing:
                yield _sse({'type': 'history_required', 'session_id': self.session_id})
            for sequence, event in batch:
                cursor = sequence
                yield event
            if finished:
                return


def _save(job, force=False):
    if not force and time.monotonic() - job.last_saved < 1:
        return
    job.info['updated_utc'] = _now()
    memory.save_session(job.username, job.session_id)
    job.last_saved = time.monotonic()


def status(username, session_id):
    session = memory.user_memories.get(username, {}).get('sessions', {}).get(session_id, {})
    info = session.get('chat_job')
    if not info:
        return None
    job = _jobs.get((username, session_id))
    if info['status'] == 'running' and (job is None or job.done):
        info.update(status='interrupted', finished_utc=_now(),
                    error='服务已重启，上次后台对话未继续运行；已保存的设计结果仍可查看。')
        memory.save_session(username, session_id)
        chat_requirements.reconcile(username, session_id, session.get('messages', []), info['id'], terminal='interrupted')
    return dict(info)


def running(username, session_id):
    job = _jobs.get((username, session_id))
    return bool(job and not job.done)


async def submit_requirement(username, session_id, job_id, request_id, content):
    # A lost HTTP acknowledgement can be retried even after the job finishes.
    existing = next((item for item in chat_requirements.records(username, session_id)
                     if item['id'] == request_id), None)
    if existing:
        if existing['job_id'] != job_id or existing['content'] != content:
            raise ValueError('这条补充消息的标识已被使用，请重新发送。')
        return dict(existing)
    job = _jobs.get((username, session_id))
    if not job or job.done or not job.accepting or job.info['id'] != job_id:
        raise ValueError('这轮处理已结束或已变化，请刷新状态后发送；补充内容尚未提交。')
    item = chat_requirements.enqueue(username, session_id, job_id, request_id, content)
    await job.publish(_sse({'type': 'chat_requirement', 'requirement': item}))
    return item


def recover_notice(username, session_id, messages, info):
    if not info or info.get('status') not in {'failed', 'stopped'}:
        return
    reply = info.get('error') or info.get('notice')
    if not reply or any(message.get('chat_job_id') == info['id'] for message in messages):
        return
    pending = set()
    for message in messages:
        pending.update(call.get('id') for call in message.get('tool_calls', []))
        if message.get('role') == 'tool':
            pending.discard(message.get('tool_call_id'))
    # A stopped save may still be running. Wait for its actual tool result;
    # never insert an assistant message between a call and its tool response.
    if not pending:
        messages.append({'role': 'assistant', 'content': reply, 'chat_job_id': info['id']})
        memory.save_user_session(username, session_id)


def start(username, session_id, factory):
    from designer import progress_store
    if running(username, session_id) or progress_store.background_running(username, session_id):
        raise ValueError('这个对话已有后台操作正在运行，请等待完成后再发送。')
    status(username, session_id)
    info = {'id': uuid4().hex, 'status': 'running', 'started_utc': _now(),
            'updated_utc': _now(), 'finished_utc': None, 'error': '', 'last_step': ''}
    job = Job(username, session_id, info)
    memory.user_memories[username]['sessions'][session_id]['chat_job'] = info
    _save(job, True)
    _jobs[(username, session_id)] = job
    job.task = asyncio.create_task(_run(job, factory), name='chat:' + info['id'])
    job.task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
    return job


async def _finish(job, state):
    job.accepting = False
    job.info.update(status=state, finished_utc=_now())
    try:
        chat_requirements.reconcile(job.username, job.session_id,
                                    memory.get_user_messages(job.username, job.session_id),
                                    job.info['id'], terminal=state)
        _save(job, True)
    finally:
        await job.publish(_sse({'type': 'chat_job', 'id': job.info['id'], 'session_id': job.session_id, 'status': state}))
        async with job.condition:
            job.done = True
            job.condition.notify_all()
        if _jobs.get((job.username, job.session_id)) is job:
            _jobs.pop((job.username, job.session_id), None)


async def _run(job, factory):
    state, stream_error = 'completed', ''
    try:
        await job.publish(_sse({'type': 'chat_job', 'id': job.info['id'], 'session_id': job.session_id, 'status': 'running'}))
        with chat_requirements.job_scope(job.username, job.session_id, job.info['id']):
            while True:
                async for event in factory():
                    await job.publish(event)
                    try:
                        payload = json.loads(event.removeprefix('data: ').strip())
                    except (ValueError, TypeError):
                        continue
                    if payload.get('type') == 'step':
                        job.info['last_step'] = payload.get('func', '')
                    elif payload.get('type') == 'error':
                        stream_error = payload.get('message', '后台对话失败')
                    _save(job, payload.get('type') in {'message_saved', 'step', 'done', 'error'})
                if stream_error or not chat_requirements.pending(job.username, job.session_id):
                    # No await between the final queue check and closing intake.
                    job.accepting = False
                    break
                from agent import langgraph_runtime as runtime
                factory = lambda: runtime.continue_queued_chat_stream(job.username, job.session_id)
        if stream_error:
            state = 'failed'
            job.info['error'] = stream_error
        else:
            from agent import langgraph_runtime as runtime
            if runtime._graph is not None:
                snapshot = await runtime._graph.aget_state(runtime._graph_config(job.username, job.session_id))
                if runtime._has_interrupt(snapshot):
                    state = 'waiting_approval'
    except asyncio.CancelledError:
        state = 'stopped'
        from agent import langgraph_runtime as runtime
        if runtime._graph is not None:
            await runtime._sync_legacy_messages(job.username, job.session_id, runtime._graph_config(job.username, job.session_id))
    except Exception as exc:
        state = 'failed'
        job.info['error'] = f'后台对话失败：{type(exc).__name__}: {exc}'
        await job.publish(_sse({'type': 'error', 'message': job.info['error']}))
    finally:
        try:
            if state in {'failed', 'stopped'}:
                messages = memory.get_user_messages(job.username, job.session_id)
                job.info['status'] = state
                job.info['notice'] = job.info['error'] if state == 'failed' else '本次回答已停止；已经开始的工具操作会继续完成，可在设计进度查看结果。'
                recover_notice(job.username, job.session_id, messages, job.info)
        finally:
            await _finish(job, state)


async def stop(username, session_id, job_id):
    job = _jobs.get((username, session_id))
    if job is None:
        info = status(username, session_id)
        if info and info['id'] == job_id:
            return info
        raise ValueError('当前后台对话不存在')
    if job.info['id'] != job_id:
        raise ValueError('后台对话已变化，请刷新后再停止')
    job.accepting = False
    job.task.cancel()
    await asyncio.gather(job.task, return_exceptions=True)
    # A task cancelled before its first instruction never enters _run/finally.
    if not job.done:
        job.info['notice'] = '本次回答已停止。'
        job.info['status'] = 'stopped'
        recover_notice(username, session_id, memory.get_user_messages(username, session_id), job.info)
        await _finish(job, 'stopped')
    return dict(job.info)


async def drain():
    while _jobs:
        await asyncio.gather(*(job.task for job in list(_jobs.values())), return_exceptions=True)
