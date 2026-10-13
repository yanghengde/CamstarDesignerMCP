"""Durable mid-run inputs, adopted only between complete tool operations."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from agent import memory, chat_intent

_owner = ContextVar('chat_requirement_owner', default=None)


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def job_scope(username, session_id, job_id):
    token = _owner.set((username, session_id, job_id))
    try:
        yield
    finally:
        _owner.reset(token)


def records(username, session_id):
    return memory.user_memories[username]['sessions'][session_id].setdefault('chat_requirements', [])


def enqueue(username, session_id, job_id, request_id, content):
    items = records(username, session_id)
    old = next((item for item in items if item['id'] == request_id), None)
    if old:
        if old['job_id'] != job_id or old['content'] != content:
            raise ValueError('这条补充消息的标识已被使用，请重新发送。')
        return dict(old)
    waiting = [item for item in items if item['job_id'] == job_id and item['status'] == 'queued']
    if len(waiting) >= 20 or sum(len(item['content']) for item in waiting) + len(content) > 20000:
        raise ValueError('等待处理的补充较多，请等已提交内容纳入处理后再发送。')
    item = {'id': request_id, 'job_id': job_id, 'content': content,
            'kind': chat_intent.running_intent(content), 'status': 'queued',
            'received_utc': now(), 'incorporated_utc': None}
    items.append(item)
    try:
        memory.save_user_session(username, session_id)
    except Exception:
        items.remove(item)
        raise
    return dict(item)


def pending(username, session_id, job_id=None):
    owner = _owner.get()
    if job_id is None:
        if not owner or owner[:2] != (username, session_id):
            return []
        job_id = owner[2]
    return [item for item in records(username, session_id)
            if item['job_id'] == job_id and item['status'] == 'queued']


def claim(username, session_id):
    items = pending(username, session_id)
    for item in items:
        item['status'] = 'processing'
    if items:
        memory.save_session(username, session_id)
    return [dict(item) for item in items]


def user_record(item):
    return {'role': 'user', 'content': item['content'], 'requirement_id': item['id']}


def reconcile(username, session_id, messages, job_id=None, terminal=None):
    seen = {message.get('requirement_id') for message in messages}
    changed = False
    for item in records(username, session_id):
        if job_id and item['job_id'] != job_id:
            continue
        if item['id'] in seen and item['status'] in {'queued', 'processing'}:
            item.update(status='incorporated', incorporated_utc=now())
            changed = True
        elif terminal and item['status'] in {'queued', 'processing'}:
            item.update(status='unhandled', reason='本次运行已结束，这条补充尚未纳入处理，请重新发送。')
            changed = True
    if changed:
        memory.save_session(username, session_id)
