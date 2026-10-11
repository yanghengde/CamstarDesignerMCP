"""Durable, session-owned progress receipts and background-operation leases."""
import asyncio
from contextlib import contextmanager, asynccontextmanager
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import config

LEASE_SECONDS = 30
_tool_tasks = {}
_tool_requests = {}


def background_running(username, session_id):
    return any(key[:2] == (username, session_id) and not task.done()
               for key, task in _tool_tasks.items())


async def drain():
    """Let vendor saves finish during a graceful service shutdown."""
    if _tool_tasks:
        await asyncio.gather(*list(_tool_tasks.values()), return_exceptions=True)


def recover_chat_results(username, session_id, messages):
    """Restore completed tool outcomes lost when the streaming caller left."""
    seen = {item.get('tool_call_id') for item in messages if item.get('role') == 'tool'}
    calls = {call.get('id') for item in messages for call in item.get('tool_calls', [])}
    changed = False
    for event in operations(username, session_id):
        call_id = event['call_id']
        if not call_id or call_id in seen or event['status'] not in {'done', 'failed'}:
            continue
        result = event.get('result') or ('Error: ' + event['error'] if event['error'] else None)
        if result is None:
            continue
        if call_id not in calls:
            messages.append({'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': call_id, 'type': 'function', 'function': {'name': event['tool'],
                'arguments': json.dumps(event['arguments'], ensure_ascii=False)}}]})
        messages.append({'role': 'tool', 'tool_call_id': call_id, 'name': event['tool'], 'content': str(result)})
        seen.add(call_id)
        changed = True
    if changed:
        from agent.memory import save_session
        save_session(username, session_id)


async def run_tool(username, session_id, tool, arguments, call_id, function):
    """Keep the execution and its receipt alive when a response disconnects."""
    key = (username, session_id, call_id or uuid4().hex)
    task = _tool_tasks.get(key)
    if task is not None and _tool_requests[key] != (tool, arguments):
        raise ValueError('同一个工具调用标识不能对应不同操作')
    if task is None:
        if call_id:
            previous = previous_call(username, session_id, call_id)
            if previous:
                if previous['tool'] != tool or previous['arguments'] != arguments:
                    raise ValueError('工具调用标识已用于另一项操作')
                if previous['status'] == 'done' and previous.get('result'):
                    return previous['result']
                if previous['status'] in {'failed', 'interrupted'}:
                    raise ValueError(previous['error'] or '上次操作未完成，请核对后使用新的调用重试')
                if previous['status'] == 'running':
                    raise ValueError('此调用的后台结果尚未确定，请核对设计进度')
        async def execute():
            async with tracking(username, session_id, tool, arguments, call_id) as identity:
                try:
                    result = await function(**arguments)
                except Exception as exc:
                    finish(identity, error=str(exc), status='failed')
                    raise
                finish(identity, result)
                return result
        task = asyncio.create_task(execute())
        _tool_tasks[key] = task
        _tool_requests[key] = (tool, dict(arguments))
        def completed(saved):
            _tool_tasks.pop(key, None)
            _tool_requests.pop(key, None)
            # A disconnected caller no longer observes failures. The durable
            # receipt already holds the error; consume it to avoid task warnings.
            if not saved.cancelled():
                saved.exception()
        task.add_done_callback(completed)
    return await asyncio.shield(task)


@contextmanager
def connection():
    path = Path(config.DESIGNER_PROGRESS_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY, username TEXT, session_id TEXT, package_id TEXT,
            tool TEXT, action TEXT, call_id TEXT, status TEXT, started REAL,
            updated REAL, finished REAL, arguments TEXT, result TEXT, error TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS confirmations (
            username TEXT, session_id TEXT, package_id TEXT, stage INTEGER,
            status TEXT, updated REAL, PRIMARY KEY(username, session_id, package_id, stage))''')
        conn.execute('CREATE INDEX IF NOT EXISTS operation_calls ON operations(username,session_id,call_id,started)')
        if 'workflow_version' not in {row[1] for row in conn.execute('PRAGMA table_info(confirmations)')}:
            conn.execute('ALTER TABLE confirmations ADD COLUMN workflow_version INTEGER DEFAULT 1')
        if conn.execute('SELECT 1 FROM confirmations WHERE workflow_version=1 LIMIT 1').fetchone():
            conn.execute('BEGIN IMMEDIATE')
            legacy = conn.execute('SELECT * FROM confirmations WHERE workflow_version=1').fetchall()
            conn.execute('DELETE FROM confirmations WHERE workflow_version=1')
            for row in legacy:
                stages = [9, 10] if row['stage'] == 9 else [11] if row['stage'] == 10 else [row['stage']]
                for stage in stages:
                    conn.execute('INSERT OR IGNORE INTO confirmations VALUES (?,?,?,?,?,?,?)',
                                 (row['username'], row['session_id'], row['package_id'], stage, row['status'], row['updated'], 2))
            conn.commit()
        with conn:
            yield conn
    finally:
        conn.close()


def operations(username, session_id):
    with connection() as conn:
        conn.execute("UPDATE operations SET status='interrupted', error='操作中断，请核对结果后重试' WHERE username=? AND session_id=? AND status='running' AND updated<?",
                     (username, session_id, time.time() - LEASE_SECONDS))
        rows = conn.execute('SELECT * FROM operations WHERE username=? AND session_id=? ORDER BY started', (username, session_id)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item['arguments'] = json.loads(item['arguments'])
        item['result'] = json.loads(item['result']) if item['result'] else None
        result.append(item)
    return result


def previous_call(username, session_id, call_id):
    with connection() as conn:
        row = conn.execute('SELECT * FROM operations WHERE username=? AND session_id=? AND call_id=? ORDER BY started DESC LIMIT 1',
                           (username, session_id, call_id)).fetchone()
    if row is None:
        return None
    event = dict(row)
    event['arguments'] = json.loads(event['arguments'])
    event['result'] = json.loads(event['result']) if event['result'] else None
    return event


def begin(username, session_id, tool, arguments, *, package_id='', action='', call_id=''):
    now = time.time()
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if action:
            existing = conn.execute("SELECT id FROM operations WHERE username=? AND session_id=? AND status='running' AND updated>=?", (username, session_id, now-LEASE_SECONDS)).fetchone()
            if existing:
                return existing['id'], False
        identity = uuid4().hex
        conn.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (identity, username, session_id, package_id, tool, action, call_id, 'running', now, now, None,
                      json.dumps(arguments, ensure_ascii=False), None, ''))
        return identity, True


def finish(identity, result=None, error='', status='done'):
    from designer.publication import redact
    # Keep receipts small and do not copy environment/configuration into the UI.
    allowed = {'status', 'files', 'manifest_file', 'compiled_mdb', 'compiled_sha256', 'source_unchanged',
               'result_file', 'intact', 'checks', 'activated', 'server_mdb', 'server_siteinfo',
               'copied_sha256', 'unchanged', 'database_published', 'deployed', 'partial_package',
               'source_sha256', 'read_only', 'workspace_resolution', 'total', 'target', 'ready_for_publish',
               'manifest_sha256', 'plan_file', 'plan_sha256', 'target_fingerprint', 'blockers', 'changes',
               'design_sha256', 'receipt_file', 'receipt_sha256', 'created_utc', 'restore_verifyonly',
               'server_backup_file', 'data_contract_count', 'service_count', 'type_checks',
               'files_sha256', 'client_assembly', 'service_assembly', 'service_scope', 'fields_verified',
               'method', 'database_modified'}
    saved = {key: value for key, value in result.items() if key in allowed} if isinstance(result, dict) else None
    if isinstance(result, str) and result.startswith('Error'):
        error, status = result, 'failed'
    now = time.time()
    with connection() as conn:
        conn.execute('UPDATE operations SET status=?, result=?, error=?, updated=?, finished=? WHERE id=?',
                     (status, json.dumps(saved, ensure_ascii=False) if saved is not None else None,
                      redact(error)[:1200], now, now, identity))


async def heartbeat(identity):
    while True:
        await asyncio.sleep(3)
        with connection() as conn:
            conn.execute("UPDATE operations SET updated=? WHERE id=? AND status='running'", (time.time(), identity))


@asynccontextmanager
async def tracking(username, session_id, tool, arguments, call_id):
    identity, _ = begin(username, session_id, tool, arguments, call_id=call_id)
    pulse = asyncio.create_task(heartbeat(identity))
    try:
        yield identity
    except asyncio.CancelledError:
        finish(identity, error='请求已停止，请核对设计结果', status='interrupted')
        raise
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)


def confirm(username, session_id, package_id, stage, status):
    with connection() as conn:
        conn.execute('INSERT OR REPLACE INTO confirmations VALUES (?,?,?,?,?,?,?)',
                     (username, session_id, package_id, stage, status, time.time(), 2))


def clear_from(username, session_id, package_id, stage):
    with connection() as conn:
        conn.execute('DELETE FROM confirmations WHERE username=? AND session_id=? AND package_id=? AND stage>=?',
                     (username, session_id, package_id, stage))


def confirmations(username, session_id, package_id):
    with connection() as conn:
        rows = conn.execute('SELECT stage,status,workflow_version FROM confirmations WHERE username=? AND session_id=? AND package_id=?',
                            (username, session_id, package_id)).fetchall()
    result = {}
    for row in sorted(rows, key=lambda row: row['workflow_version']):
        stages = [row['stage']]
        if row['workflow_version'] == 1:
            stages = [9, 10] if row['stage'] == 9 else [11] if row['stage'] == 10 else stages
        result.update({stage: row['status'] for stage in stages})
    return result
