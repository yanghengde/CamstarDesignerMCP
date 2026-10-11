"""Validated design packages handed to the configured Designer installation."""
import ast
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import threading

import config
from designer.files import root_dir, source_path
from designer.vendor import digest
from designer.publication import redact

_lock = threading.Lock()


def server_check(state, *, copy_to=None):
    remote = state.get('server_mdb', '')
    if not valid_server_file(remote, 'InSite.mdb'):
        raise ValueError('请先准备 Designer 工作文件')
    if config.DESIGNER_SERVER_SHARE.rstrip('\\').casefold() != ('\\\\' + config.DESIGNER_DB_SERVER + '\\C').casefold():
        raise ValueError('服务器共享配置不匹配')
    env = {k: v for k, v in os.environ.items() if k.casefold() != 'psmodulepath'}
    for name in ('DESIGNER_SERVER_SHARE', 'DESIGNER_WINDOWS_USER', 'DESIGNER_WINDOWS_PASSWORD'):
        env[name] = getattr(config, name)
        if not env[name]: raise ValueError('Designer 服务器共享或凭据未配置')
    from designer.files import artifact_dir
    folder = artifact_dir() if copy_to is None else copy_to.parent
    output = folder / 'designer_sync.json'
    shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    args = [str(shell), '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_name('review_sync.ps1')),
            '-ServerMdb', remote, '-ExpectedSha256', state.get('server_sha256') or '', '-ResultFile', str(output)]
    if copy_to: args += ['-LocalMdb', str(copy_to)]
    process = subprocess.run(args, env=env, capture_output=True, timeout=60,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if process.returncode:
        raise ValueError('Designer 文件读取失败：' + redact(process.stderr.decode('utf-8', errors='replace'))[:1200])
    return json.loads(output.read_text(encoding='utf-8-sig'))


def assert_server_unchanged(state):
    result = server_check(state)
    if not result.get('unchanged'):
        raise ValueError('Designer 已保存新的改动，请先同步 Designer 文件，再继续设计；服务器文件未覆盖')
    return result


def synchronize(mdb_file, expected_sha256, accept_merged_sha256=''):
    from designer import working, vendor
    from designer.files import artifact_dir
    source = working.resolve(mdb_file)
    project_id, _ = working.context(source)
    with working.project_lock(project_id):
        state = working.load(project_id)
        if not state or not state.get('server_mdb'):
            raise ValueError('请先准备 Designer 文件，再同步已保存的改动')
        if digest(source) != expected_sha256:
            raise ValueError('工作 MDB 已更新，请刷新后同步')
        folder = artifact_dir()
        candidate = folder / 'saved_designer.mdb'
        receipt = server_check(state, copy_to=candidate)
        copied = digest(candidate)
        if copied != receipt.get('copied_sha256'):
            raise ValueError('Designer 文件读取校验失败')
        if copied == expected_sha256:
            return {'status': 'file_synchronized', 'unchanged': True,
                    'working_mdb': str(source), 'files': {'manifest.json': state['latest_manifest'], 'modified.mdb': str(source)}}
        # Both files changed: keep both versions; Opcenter handles the merge.
        if state.get('server_sha256') != expected_sha256:
            if copied == state.get('server_sha256'):
                raise ValueError('Designer 文件尚未包含本地的新改动，请先准备最新 Designer 文件；本地工作 MDB 未覆盖')
            if accept_merged_sha256 != copied:
                raise ValueError(f'本地与 Designer 文件均有新改动，请在 Opcenter 合并，并明确确认接收合并版本后重试；两个文件均已保留。合并文件 SHA256：{copied}')
        result = vendor.adopt_candidate(str(source), expected_sha256, candidate, method='external_edit',
                                        state_update={'server_sha256': copied})
        path = Path(result['files']['manifest.json'])
        working.write_json(path.parent / 'designer_review.json', {**receipt, 'status': 'saved_designer_file_received',
                            'server_siteinfo': state.get('server_siteinfo'), 'activated': False})
        working.write_json(path.parent / 'designer_sync.json', {**receipt, 'unchanged': True, 'status': 'file_synchronized'})
        return {**result, 'status': 'file_synchronized', 'unchanged': True, 'received_changes': True}


def restore_backup(mdb_file, backup_id, expected_sha256):
    from designer import working, vendor
    from designer.files import artifact_dir
    from datetime import datetime, timezone
    from uuid import uuid4
    source = working.resolve(mdb_file)
    project_id, _ = working.context(source)
    with working.project_lock(project_id):
        state = working.load(project_id)
        if not state: raise ValueError('没有工作 MDB 项目')
        if digest(source) != expected_sha256: raise ValueError('工作 MDB 已变化，请刷新后恢复')
        if state.get('server_mdb'): assert_server_unchanged(state)
        selected = working.backup_file(project_id, backup_id, 'mdb')
        # Preserve the pre-restore version even when it has not been published.
        snapshot = artifact_dir() / 'before_restore.mdb'
        import shutil
        shutil.copyfile(source, snapshot)
        if digest(snapshot) != expected_sha256: raise ValueError('恢复前快照校验失败')
        checkpoint = {'id': uuid4().hex, 'sha256': expected_sha256, 'mdb_file': str(snapshot),
                      'published_utc': None, 'method': 'restore_checkpoint', 'objects': [],
                      'manifest_file': state['latest_manifest'], 'siteinfo_file': '', 'siteinfo_sha256': None}
        working.backup_release(project_id, checkpoint)
        try:
            result = vendor.adopt_candidate(str(source), expected_sha256, selected, method='restore_mdb')
        finally:
            working.prune_backups(project_id)
        result.update(restored_backup_id=backup_id, recovery_backup_id=checkpoint['id'], database_restored=False)
        if state.get('server_mdb'):
            try: result['review'] = prepare(result['files']['manifest.json'], False)
            except ValueError as exc: result['designer_sync_error'] = str(exc)
        return result


def valid_server_file(value, name):
    return bool(re.fullmatch(r'C:\\(?:Temp\\DesignerMCP\\Review_[0-9a-f]{32}|DesignerWorkspace\\[0-9a-f]{32})\\' + re.escape(name), value, re.IGNORECASE))


def sync_file(manifest_file: str) -> dict:
    """Read the saved review MDB; never infer which file the GUI has open."""
    item = summary(manifest_file)
    receipt = item.get('review') or {}
    remote = receipt.get('server_mdb', '')
    if not valid_server_file(remote, 'InSite.mdb'):
        raise ValueError('请先准备本次设计的 Designer 文件')
    if config.DESIGNER_SERVER_SHARE.rstrip('\\').casefold() != ('\\\\' + config.DESIGNER_DB_SERVER + '\\C').casefold():
        raise ValueError('服务器共享配置不匹配')
    env = {k: v for k, v in os.environ.items() if k.casefold() != 'psmodulepath'}
    for name in ('DESIGNER_SERVER_SHARE', 'DESIGNER_WINDOWS_USER', 'DESIGNER_WINDOWS_PASSWORD'):
        env[name] = getattr(config, name)
        if not env[name]:
            raise ValueError('Designer 服务器共享或凭据未配置')
    folder = Path(item['manifest_file']).parent
    output = folder / 'designer_sync.json'
    shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    process = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_name('review_sync.ps1')),
                              '-ServerMdb', remote, '-ExpectedSha256', item['sha256'], '-ResultFile', str(output)],
                             env=env, capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if process.returncode:
        raise ValueError('文件同步失败：' + redact(process.stderr.decode('utf-8', errors='replace'))[:1200])
    return json.loads(output.read_text(encoding='utf-8-sig'))


def package(manifest_file: str) -> tuple[Path, dict]:
    path = source_path(manifest_file, '.json')
    if path.name != 'manifest.json' or path.parent.parent != root_dir() / 'artifacts' or not re.fullmatch(r'[0-9a-f]{32}', path.parent.name):
        raise ValueError('请选择官方设计包的 manifest.json')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('status') != 'saved_to_test_copy_and_exported' or not manifest.get('workspace_applied_to_mdb'):
        raise ValueError('设计包尚未成功保存，不能交给 Designer')
    for name in ('modified.mdb', 'changes.xml'):
        file = source_path(str(path.parent / name), Path(name).suffix)
        if digest(file) != manifest.get('artifacts', {}).get(name):
            raise ValueError(f'{name} SHA256 不匹配，请重新生成设计包')
    return path, manifest


def summary(manifest_file: str) -> dict:
    from designer import working
    from designer.operations import summary as summarize
    path, manifest = package(manifest_file)
    changes = summarize(manifest['operations'], manifest.get('execution', {}).get('affected_cdos', []))
    objects = changes['objects']
    receipt = None
    receipt_file = path.parent / 'designer_review.json'
    if receipt_file.is_file():
        try:
            saved = json.loads(receipt_file.read_text(encoding='utf-8-sig'))
            if saved.get('copied_sha256') == manifest['artifacts']['modified.mdb']:
                receipt = {key: saved.get(key) for key in ('server_mdb', 'server_siteinfo', 'activated', 'status')}
        except (ValueError, OSError):
            pass
    work = working.summary(manifest['working_project_id']) if manifest.get('working_project_id') else None
    current = True
    if work:
        current = digest(Path(work['mdb_file'])) == manifest['artifacts']['modified.mdb']
    return {'id': path.parent.name, 'manifest_file': str(path),
            'mdb_file': work['mdb_file'] if work and current else str(path.parent / 'modified.mdb'),
            'working': work, 'is_current': current,
            'sha256': manifest['artifacts']['modified.mdb'], 'workspace': manifest['workspace'],
            'objects': [name for name in objects if name],
            'service_objects': changes['service_objects'], 'field_count': changes['field_count'],
            'change_count': changes['change_count'],
            'field_changes': sum(op.get('action') == 'add_field' for op in manifest['operations']),
            'status': 'ready_for_designer', 'database_published': False, 'review': receipt}


def session_packages(messages: list[dict]) -> list[dict]:
    """Only offer packages actually returned by this session's design tools."""
    found = {}
    for message in messages:
        if message.get('role') != 'tool' or message.get('name') not in ('generate_designer_design_package', 'generate_designer_cdo_package', 'sync_designer_working_file', 'restore_designer_mdb_backup', 'sync_designer_file', 'restore_designer_mdb'):
            continue
        try:
            content = message.get('content', '')
            result = content if isinstance(content, dict) else ast.literal_eval(content)
            item = summary(result['files']['manifest.json'])
            found[item['id']] = item
        except (ValueError, SyntaxError, TypeError, KeyError, OSError):
            continue
    return list(found.values())


def prepare(manifest_file: str, activate: bool = False) -> dict:
    from designer import working
    _, manifest = package(manifest_file)
    project_id = manifest.get('working_project_id')
    if project_id:
        with working.project_lock(project_id):
            return _prepare(manifest_file, activate)
    return _prepare(manifest_file, activate)


def _prepare(manifest_file: str, activate: bool = False) -> dict:
    """Update the fixed project MDB; optionally select it for next launch."""
    item = summary(manifest_file)
    from designer import working
    _, manifest = package(manifest_file)
    working.check_current(manifest)
    if not all((config.DESIGNER_SERVER_SHARE, config.DESIGNER_DB_SERVER, config.DESIGNER_WINDOWS_USER,
                config.DESIGNER_WINDOWS_PASSWORD, config.DESIGNER_UI_EXE)):
        raise ValueError('请先配置 Designer 服务器共享、Windows 凭据和界面程序路径')
    if config.DESIGNER_SERVER_SHARE.rstrip('\\').casefold() != ('\\\\' + config.DESIGNER_DB_SERVER + '\\C').casefold():
        raise ValueError('共享必须是已配置 Designer 服务器的 C 共享')
    exe = PureWindowsPath(config.DESIGNER_UI_EXE)
    if not exe.is_absolute() or exe.drive.lower() != 'c:' or '..' in exe.parts or exe.suffix.lower() != '.exe' or any(c in str(exe) for c in '\r\n\x00"'):
        raise ValueError('Designer 界面程序必须是 C 盘上的明确 exe 路径')
    with _lock:
        working.check_current(manifest)
        env = {k: v for k, v in os.environ.items() if k.casefold() != 'psmodulepath'}
        for name in ('DESIGNER_SERVER_SHARE', 'DESIGNER_WINDOWS_USER', 'DESIGNER_WINDOWS_PASSWORD', 'DESIGNER_UI_EXE'):
            env[name] = getattr(config, name)
        shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        folder = Path(item['manifest_file']).parent
        result_file = folder / 'designer_review.json'
        args = [str(shell), '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_name('review_transfer.ps1')),
                '-LocalMdb', item['mdb_file'], '-ExpectedSha256', item['sha256'], '-ResultFile', str(result_file),
                '-Activate', str(bool(activate)).lower()]
        if item.get('working'):
            args += ['-ProjectId', item['working']['id'], '-BackupsDirectory', str(working.project_dir(item['working']['id']) / 'backups')]
            state = working.load(item['working']['id'])
            args += ['-ExpectedServerSha256', state.get('server_sha256') or '']
        try:
            process = subprocess.run(args, env=env, capture_output=True, timeout=90,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if process.returncode:
                raise ValueError(redact(process.stderr.decode('utf-8', errors='replace'))[:2000])
            receipt = json.loads(result_file.read_text(encoding='utf-8-sig'))
            if item.get('working'):
                project_id = item['working']['id']
                siteinfo = folder / 'review_siteinfo.mdb'
                copied = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_name('progress_siteinfo.ps1')),
                                         '-ServerFile', receipt['server_siteinfo'], '-LocalFile', str(siteinfo)],
                                        env=env, capture_output=True, timeout=60,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if copied.returncode:
                    raise ValueError('SiteInfo 保存失败：' + redact(copied.stderr.decode('utf-8', errors='replace'))[:1000])
                state = working.load(project_id)
                working.write_json(working.project_dir(project_id) / 'state.json', {**state,
                                   'server_mdb': receipt['server_mdb'], 'server_siteinfo': receipt['server_siteinfo'],
                                   'server_sha256': receipt['copied_sha256'],
                                   'siteinfo_file': str(siteinfo)})
                item['working'] = working.summary(project_id)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise ValueError('Designer 交接失败：' + redact(str(exc))) from None
        instructions = ('保存并关闭当前 Designer，再重新打开。' if receipt.get('activated') else
                        '请保存当前设计，在 Designer 的打开文件入口选择 server_mdb；需要站点文件时选择 server_siteinfo。')
        return {**item, **receipt, 'instructions': instructions + '在客户工作区中搜索对象名称；审核、编译和发布继续在 Designer 内完成。',
                'designer_open_verified': False}
