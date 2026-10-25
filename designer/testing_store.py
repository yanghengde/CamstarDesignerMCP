"""Local, owner-scoped environments, cases and durable test receipts."""
import base64
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

import config


class _DataBlob(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    path = Path(config.DESIGNER_TESTING_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=15) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=15000')
        conn.executescript('''
          CREATE TABLE IF NOT EXISTS test_items (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
            document TEXT NOT NULL, updated TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS test_items_owner ON test_items(owner,kind,updated);
          CREATE TABLE IF NOT EXISTS test_runs (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, document TEXT NOT NULL, updated TEXT NOT NULL,
            UNIQUE(owner,request_id));
        ''')
        yield conn


def protect(value, *, decrypt=False):
    """DPAPI binds saved business passwords to this Windows user/machine."""
    if not value:
        return ''
    if os.name != 'nt':
        raise ValueError('保存业务密码需要 Windows DPAPI')

    raw = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buffer = ctypes.create_string_buffer(raw)
    source = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt = ctypes.windll.crypt32
    signature = [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_DataBlob)]
    crypt.CryptUnprotectData.argtypes = signature
    crypt.CryptUnprotectData.restype = ctypes.c_int
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, *signature[2:]]
    crypt.CryptProtectData.restype = ctypes.c_int
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), 'Camstar test environment', None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise ValueError('无法使用当前 Windows 账号保护或读取业务密码')
    try:
        result = ctypes.string_at(output.data, output.length)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)


def items(owner, kind):
    with connection() as conn:
        return [json.loads(row[0]) for row in conn.execute(
            'SELECT document FROM test_items WHERE owner=? AND kind=? ORDER BY updated DESC', (owner, kind))]


def item(owner, kind, identity):
    with connection() as conn:
        row = conn.execute('SELECT document FROM test_items WHERE owner=? AND kind=? AND id=?', (owner, kind, identity)).fetchone()
    if not row:
        raise ValueError('记录不存在或不属于当前用户')
    return json.loads(row[0])


def save(owner, kind, document, identity=None):
    document = {**document, 'id': identity or uuid4().hex, 'updated_utc': now()}
    with connection() as conn:
        conn.execute('INSERT INTO test_items VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document,updated=excluded.updated WHERE test_items.owner=excluded.owner AND test_items.kind=excluded.kind',
                     (document['id'], owner, kind, json.dumps(document, ensure_ascii=False), document['updated_utc']))
    return document


def delete(owner, kind, identity):
    with connection() as conn:
        conn.execute('DELETE FROM test_items WHERE owner=? AND kind=? AND id=?', (owner, kind, identity))


def public_environment(document):
    return {**{key: value for key, value in document.items() if key != 'protected_password'},
            'has_password': bool(document.get('protected_password'))}


def begin(owner, request_id, fingerprint, spec):
    document = {'id': uuid4().hex, 'request_id': request_id, 'status': 'running', 'execution_phase': 'queued', 'created_utc': now(), 'spec': spec, 'result': None}
    with connection() as conn:
        conn.execute('INSERT OR IGNORE INTO test_runs VALUES (?,?,?,?,?,?)',
                     (document['id'], owner, request_id, fingerprint, json.dumps(document, ensure_ascii=False), now()))
        row = conn.execute('SELECT fingerprint,document FROM test_runs WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
    if row['fingerprint'] != fingerprint:
        raise ValueError('同一个请求标识已用于不同测试，请重新发起')
    existing = json.loads(row['document'])
    return existing, existing['id'] == document['id']


def finish(owner, identity, result):
    document = run(owner, identity)
    document.update(status=result['status'], result=result, finished_utc=now())
    document['execution_phase'] = result.get('execution_phase', 'completed')
    with connection() as conn:
        conn.execute('UPDATE test_runs SET document=?,updated=? WHERE id=? AND owner=?',
                     (json.dumps(document, ensure_ascii=False), now(), identity, owner))
    return document


def phase(owner, identity, value):
    """Only the executor reports phases; UI elapsed time never implies invocation."""
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT document FROM test_runs WHERE owner=? AND id=?', (owner, identity)).fetchone()
        if not row:
            raise ValueError('测试记录不存在或不属于当前用户')
        document = json.loads(row[0])
        if document['status'] != 'running':
            return
        document['execution_phase'] = value
        document['phase_updated_utc'] = now()
        conn.execute('UPDATE test_runs SET document=?,updated=? WHERE owner=? AND id=?',
                     (json.dumps(document, ensure_ascii=False), now(), owner, identity))


def run(owner, identity):
    with connection() as conn:
        row = conn.execute('SELECT document FROM test_runs WHERE owner=? AND id=?', (owner, identity)).fetchone()
    if not row:
        raise ValueError('测试记录不存在或不属于当前用户')
    return json.loads(row[0])


def history(owner, limit=50):
    with connection() as conn:
        return [json.loads(row[0]) for row in conn.execute('SELECT document FROM test_runs WHERE owner=? ORDER BY updated DESC LIMIT ?', (owner, limit))]


def recover():
    with connection() as conn:
        for row in conn.execute('SELECT id,document FROM test_runs').fetchall():
            document = json.loads(row['document'])
            if document['status'] == 'running':
                document.update(status='interrupted', finished_utc=now(), result={
                    'status': 'interrupted', 'outcome': 'unknown',
                    'error': '服务在测试期间退出，实际业务结果未知；写入测试请先核对业务数据，避免重复提交。'})
                conn.execute('UPDATE test_runs SET document=?,updated=? WHERE id=?', (json.dumps(document, ensure_ascii=False), now(), row['id']))
