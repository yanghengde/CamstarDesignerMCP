"""Stable project MDBs and ten release backups; merging remains Opcenter's job."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import time
import threading
from contextlib import contextmanager
from uuid import uuid4

from designer.files import root_dir, source_path

BACKUP_LIMIT = 10
_locks = {}
_locks_guard = threading.Lock()


@contextmanager
def project_lock(project_id):
    project_dir(project_id)
    with _locks_guard:
        lock = _locks.setdefault(project_id, threading.RLock())
    with lock:
        yield


def project_dir(project_id):
    if not re.fullmatch(r'[0-9a-f]{32}', project_id):
        raise ValueError('无效的设计项目')
    folder = (root_dir() / 'workspaces' / project_id).resolve()
    if not folder.is_relative_to(root_dir()):
        raise ValueError('工作目录必须位于 DESIGNER_ROOT 内')
    return folder


def write_json(file, value):
    temporary = file.with_name(file.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        for attempt in range(5):
            try:
                temporary.replace(file)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (2 ** attempt))
    finally:
        temporary.unlink(missing_ok=True)


def backups_dir(project_id):
    project = project_dir(project_id)
    folder = (project / 'backups').resolve()
    if not folder.is_relative_to(project):
        raise ValueError('备份目录必须位于项目工作目录内')
    return folder


def load(project_id):
    from designer.vendor import digest
    folder = project_dir(project_id)
    journal = folder / 'pending.json'
    if journal.is_file():
        pending = json.loads(journal.read_text(encoding='utf-8'))
        current = folder / 'InSite.mdb'
        if current.is_file() and digest(current) == pending['sha256']:
            write_json(folder / 'state.json', pending)
        journal.unlink()
    file = folder / 'state.json'
    return json.loads(file.read_text(encoding='utf-8')) if file.is_file() else None


def identity(source):
    """Resolve a legacy artifact lineage to one stable project, without copying it."""
    seen = set()
    while source not in seen:
        seen.add(source)
        if source.parent.parent == root_dir() / 'workspaces':
            project_id = source.parent.name
            project_dir(project_id)
            return project_id, source
        manifest_file = source.parent / 'manifest.json'
        if source.name == 'modified.mdb' and source.parent.parent == root_dir() / 'artifacts' and manifest_file.is_file():
            manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
            if manifest.get('working_project_id'):
                return manifest['working_project_id'], source
            if manifest.get('source_file'):
                source = source_path(manifest['source_file'], '.mdb')
                continue
        return sha256(str(source).casefold().encode('utf-8')).hexdigest()[:32], source
    raise ValueError('设计来源存在循环')


def resolve(mdb_file):
    """Old seed/artifact paths resolve to the current work file once managed."""
    source = source_path(mdb_file, '.mdb')
    if 'backups' in source.relative_to(root_dir()).parts:
        raise ValueError('备份用于查阅或恢复；继续设计请使用当前工作 MDB')
    project_id, _ = identity(source)
    state = load(project_id)
    return source_path(str(project_dir(project_id) / 'InSite.mdb'), '.mdb') if state else source


def context(source):
    project_id, origin = identity(source)
    state = load(project_id)
    return project_id, state or {'id': project_id, 'origin_file': str(origin), 'latest_manifest': '', 'published': None}


def backup_release(project_id, published):
    from designer.vendor import digest
    folder = project_dir(project_id)
    backups = backups_dir(project_id)
    backups.mkdir(exist_ok=True)
    destination = backups / published['id']
    if (destination / 'backup.json').is_file():
        record = json.loads((destination / 'backup.json').read_text(encoding='utf-8'))
        if digest(destination / 'InSite.mdb') != record['sha256']:
            raise ValueError('已发布版本备份发生变化，请核对备份文件')
        return
    staging = backups / ('.' + uuid4().hex + '.tmp')
    staging.mkdir()
    try:
        source = source_path(published['mdb_file'], '.mdb')
        shutil.copyfile(source, staging / 'InSite.mdb')
        if digest(staging / 'InSite.mdb') != published['sha256']:
            raise ValueError('已发布 MDB 备份校验失败，未修改工作文件')
        siteinfo = published.get('siteinfo_file')
        site_hash = None
        if siteinfo:
            shutil.copyfile(source_path(siteinfo, '.mdb'), staging / 'SiteInfo.mdb')
            site_hash = digest(staging / 'SiteInfo.mdb')
            if site_hash != published.get('siteinfo_sha256'):
                raise ValueError('已发布 SiteInfo 备份校验失败，未修改工作文件')
        record = {**published, 'created_utc': datetime.now(timezone.utc).isoformat(), 'siteinfo_sha256': site_hash}
        write_json(staging / 'backup.json', record)
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging)
        raise


def backups(project_id):
    folder = backups_dir(project_id)
    result = []
    if folder.is_dir():
        for item in folder.iterdir():
            if re.fullmatch(r'[0-9a-f]{32}', item.name) and not item.is_symlink() and (item / 'backup.json').is_file():
                record = json.loads((item / 'backup.json').read_text(encoding='utf-8'))
                result.append({key: record.get(key) for key in ('id', 'created_utc', 'published_utc', 'sha256', 'siteinfo_sha256', 'objects', 'method')})
    return sorted(result, key=lambda item: item['created_utc'], reverse=True)


def commit(project_id, state, manifest_file, before):
    """Replace the fixed file only after vendor save/export and release backup pass."""
    from designer.vendor import digest
    folder = project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / 'InSite.mdb'
    if destination.is_file() and digest(destination) != before:
        raise ValueError('工作 MDB 已变化，请重新读取后设计')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    after = manifest['artifacts']['modified.mdb']
    published = state.get('published')
    if published and state.get('backup_pending') and before != after:
        backup_release(project_id, published)
    cycle = [] if state.get('backup_pending') else state.get('cycle_manifests', [])
    next_state = {**state, 'id': project_id, 'latest_manifest': str(manifest_file), 'sha256': after,
                  'cycle_manifests': cycle + [str(manifest_file)],
                  'updated_utc': datetime.now(timezone.utc).isoformat(),
                  'backup_pending': bool(state.get('backup_pending') and before == after)}
    temporary = folder / ('InSite.' + uuid4().hex + '.tmp')
    try:
        shutil.copyfile(manifest_file.parent / 'modified.mdb', temporary)
        if digest(temporary) != after:
            raise ValueError('工作 MDB 保存校验失败')
        write_json(folder / 'pending.json', next_state)
        temporary.replace(destination)
        write_json(folder / 'state.json', next_state)
        (folder / 'pending.json').unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
    prune_backups(project_id)
    return str(destination)


def prune_backups(project_id):
    for old in backups(project_id)[BACKUP_LIMIT:]:
        destination = (backups_dir(project_id) / old['id']).resolve()
        if not destination.is_relative_to(backups_dir(project_id)):
            raise ValueError('备份目录超出工作项目范围')
        shutil.rmtree(destination)


def check_current(manifest):
    from designer.vendor import digest
    if not manifest.get('working_project_id'):
        return
    current = project_dir(manifest['working_project_id']) / 'InSite.mdb'
    if not current.is_file() or digest(current) != manifest['artifacts']['modified.mdb']:
        raise ValueError('工作 MDB 已更新，请使用最新设计重新核对')


def mark_published(manifest_file, *, method='automatic', siteinfo_file=''):
    from designer.vendor import digest
    path = source_path(str(manifest_file), '.json')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    project_id = manifest.get('working_project_id')
    if not project_id:
        return
    check_current(manifest)
    state = load(project_id)
    checksum = manifest['artifacts']['modified.mdb']
    if (state.get('published') or {}).get('sha256') == checksum and state.get('backup_pending'):
        return
    siteinfo_file = siteinfo_file or state.get('siteinfo_file', '')
    site = source_path(siteinfo_file, '.mdb') if siteinfo_file else None
    release_id = uuid4().hex
    if site:
        saved_site = path.parent / ('published_siteinfo_' + release_id + '.mdb')
        shutil.copyfile(site, saved_site)
        if digest(saved_site) != digest(site):
            raise ValueError('发布 SiteInfo 在保存期间发生变化，请重新确认')
        site = saved_site
    published = {'id': release_id, 'sha256': checksum, 'mdb_file': str(path.parent / 'modified.mdb'),
                 'manifest_file': str(path), 'published_utc': datetime.now(timezone.utc).isoformat(),
                 'method': method, 'objects': list(dict.fromkeys(op.get('owner') or op.get('name') for op in manifest['operations'])),
                 'siteinfo_file': str(site) if site else '', 'siteinfo_sha256': digest(site) if site else None}
    write_json(project_dir(project_id) / 'state.json', {**state, 'published': published, 'backup_pending': True})


def summary(project_id):
    state = load(project_id)
    if not state:
        return None
    return {'id': project_id, 'mdb_file': str(project_dir(project_id) / 'InSite.mdb'),
            'server_mdb': state.get('server_mdb'), 'server_siteinfo': state.get('server_siteinfo'),
            'server_current': state.get('server_sha256') == state.get('sha256'),
            'latest_manifest': state['latest_manifest'], 'backup_pending': state.get('backup_pending', False),
            'published_utc': (state.get('published') or {}).get('published_utc'),
            'backup_limit': BACKUP_LIMIT, 'backups': backups(project_id)}


def available():
    base = root_dir() / 'workspaces'
    return [summary(item.name) for item in base.iterdir() if re.fullmatch(r'[0-9a-f]{32}', item.name) and load(item.name)] if base.is_dir() else []


def backup_file(project_id, backup_id, kind):
    from designer.vendor import digest
    if not re.fullmatch(r'[0-9a-f]{32}', backup_id) or kind not in ('mdb', 'siteinfo'):
        raise ValueError('无效的备份文件')
    folder = (backups_dir(project_id) / backup_id).resolve()
    if not folder.is_relative_to(backups_dir(project_id)):
        raise ValueError('备份文件必须位于项目备份目录内')
    record_file = source_path(str(folder / 'backup.json'), '.json')
    record = json.loads(record_file.read_text(encoding='utf-8'))
    file = source_path(str(folder / ('InSite.mdb' if kind == 'mdb' else 'SiteInfo.mdb')), '.mdb')
    if digest(file) != record['sha256' if kind == 'mdb' else 'siteinfo_sha256']:
        raise ValueError('备份文件 SHA256 不匹配')
    return file
