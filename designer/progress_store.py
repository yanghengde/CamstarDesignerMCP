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
               'files_sha256', 'client_assembly', 'service_assembly', 'service_scope', 'fields_verified'}
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
