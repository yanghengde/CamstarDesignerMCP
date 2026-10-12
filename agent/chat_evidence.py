"""Session-bound access to original receipts omitted from the model context."""
from contextlib import contextmanager
from contextvars import ContextVar

scope = ContextVar('chat_evidence_scope', default=None)


@contextmanager
def evidence_scope(messages):
    token = scope.set(messages)
    try:
        yield
    finally:
        scope.reset(token)


def read(evidence_id, offset=0, limit=6000):
    messages = scope.get()
    if messages is None:
        raise ValueError('证据读取仅允许在当前聊天请求中使用')
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError('offset 必须是非负整数')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 12000:
        raise ValueError('limit 必须为 1～12000')
    for index, message in enumerate(messages):
        if (message.get('role') == 'tool' and message.get('tool_call_id') == evidence_id
                or evidence_id == f'message-{index}' and message.get('role') in {'tool', 'user'}
                or message.get('role') == 'user' and any(evidence_id == 'attachment-' + item['id']
                                                       for item in message.get('attachments', []))):
            content = str(message.get('content') or '')
            page = content[offset:offset + limit]
            return {'evidence_id': evidence_id, 'offset': offset, 'total_chars': len(content),
                    'content': page, 'next_offset': offset + len(page) if offset + len(page) < len(content) else None,
                    'scope': 'current_chat_original_receipt'}
    raise ValueError('当前对话中不存在此证据')
