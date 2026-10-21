"""One bounded, serialized read-only SDK process. Writes never enter this worker."""
import atexit
import json
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import threading
import time

_lock = threading.Lock()
_process = None
_identity = None
_last_used = 0
_resident = None
_resident_hash = None


def _checksum(path):
    result = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _stop():
    global _process, _identity, _resident, _resident_hash
    if _process is not None:
        if _process.poll() is None:
            _process.kill()
        _process.wait(timeout=10)
        for stream in (_process.stdin, _process.stdout):
            if stream:
                stream.close()
    _process = None
    _identity = None
    if _resident is not None:
        try:
            _resident.unlink(missing_ok=True)
        except OSError:
            pass
    _resident = _resident_hash = None


def close():
    with _lock:
        _stop()


atexit.register(close)


def execute(args, folder, identity, timeout):
    global _process, _identity, _last_used, _resident, _resident_hash
    with _lock:
        # Only one version stays resident. Changes to MDB/SDK/bridge discard the
        # process, including all vendor static state, before the next load.
        if (_process is not None and
                (_identity != identity or _process.poll() is not None or time.monotonic() - _last_used > 300)):
            _stop()
        reused = _process is not None
        try:
            if _process is None:
                request_file = folder / 'request.json'
                request = json.loads(request_file.read_text(encoding='utf-8-sig'))
                if request.get('mdb'):
                    source = Path(request['mdb']).resolve()
                    if source != (folder / 'read.mdb').resolve():
                        raise ValueError('只读进程只接受隔离的读取副本')
                    # The SDK reopens its original MDB lazily (e.g. GetWorkspaces).
                    # Own one file for the same lifetime as the resident graph.
                    _resident = folder / 'resident.mdb'
                    _resident_hash = _checksum(source)
                    shutil.copyfile(source, _resident)
                    if _checksum(_resident) != _resident_hash:
                        raise ValueError('只读常驻副本复制校验失败')
                    request['mdb'] = str(_resident)
                    request_file.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
                _process = subprocess.Popen(args, cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                _identity = identity
            if _resident is not None and _checksum(_resident) != _resident_hash:
                raise ValueError('只读常驻副本已变化')
            completion = folder / 'result.json.done'
            completion.unlink(missing_ok=True)
            _process.stdin.write(json.dumps({'input': str(folder / 'request.json'),
                'output': str(folder / 'result.json'), 'identity': identity}) + '\n')
            _process.stdin.flush()
            deadline = time.monotonic() + timeout
            while not completion.is_file():
                if _process.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('只读元数据进程退出或超时')
                time.sleep(0.05)
            response = completion.read_text(encoding='utf-8-sig')
            if response != 'ok':
                raise ValueError('只读元数据进程失败：' + response[:1000])
            if _resident is not None and _checksum(_resident) != _resident_hash:
                raise ValueError('只读查询修改了常驻副本')
            _last_used = time.monotonic()
            return reused
        except (OSError, ValueError) as exc:
            _stop()
            raise ValueError('只读查询失败或超时，请重试') from exc
