"""Bounded metadata read cache keyed by MDB and vendor implementation hashes."""
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from designer.files import root_dir

MAX_ENTRIES = 128
MAX_BYTES = 16 * 1024 * 1024


def directory():
    root = root_dir()
    folder = (root / 'cache' / 'metadata').resolve()
    if not folder.is_relative_to(root):
        raise ValueError('查询缓存不能位于 DESIGNER_ROOT 之外')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def identity(source, checksum, assembly, request):
    from designer.vendor import digest
    bridge = Path(__file__).with_name('VendorBridge.cs')
    launcher = bridge.with_name('vendor_bridge.ps1')
    value = [str(source), checksum, digest(assembly), digest(bridge), digest(launcher), request]
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def load(key):
    try:
        folder = directory()
        file = folder / (key + '.json')
        if file.resolve().parent != folder:
            return None
        if file.stat().st_size > MAX_BYTES:
            return None
        saved = json.loads(file.read_text(encoding='utf-8'))
        payload = json.dumps(saved['result'], ensure_ascii=False, sort_keys=True)
        if saved['key'] == key and sha256(payload.encode('utf-8')).hexdigest() == saved['checksum']:
            return saved['result']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def save(key, result):
    folder = directory()
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if len(payload.encode('utf-8')) > MAX_BYTES:
        return
    temporary = folder / (uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps({'key': key, 'checksum': sha256(payload.encode('utf-8')).hexdigest(),
                                         'result': result}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(folder / (key + '.json'))
        entries = sorted((file for file in folder.glob('*.json') if not file.is_symlink()),
                         key=lambda file: file.stat().st_mtime, reverse=True)
        size = 0
        for index, file in enumerate(entries):
            size += file.stat().st_size
            if index >= MAX_ENTRIES or size > MAX_BYTES:
                file.unlink(missing_ok=True)
    except OSError:
        # Caching must not turn a successful vendor read into a failed operation.
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
