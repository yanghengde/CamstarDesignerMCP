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


def sync_file(manifest_file: str) -> dict:
    """Read the saved review MDB; never infer which file the GUI has open."""
    item = summary(manifest_file)
    receipt = item.get('review') or {}
    remote = receipt.get('server_mdb', '')
    if not re.fullmatch(r'C:\\Temp\\DesignerMCP\\Review_[0-9a-f]{32}\\InSite\.mdb', remote, re.IGNORECASE):
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
    path, manifest = package(manifest_file)
    objects = list(dict.fromkeys(op.get('owner') or op.get('name') for op in manifest['operations']
                               if op.get('action') in ('create_cdo', 'add_field') or op.get('kind') == 'cdo'))
    receipt = None
    receipt_file = path.parent / 'designer_review.json'
    if receipt_file.is_file():
        try:
            saved = json.loads(receipt_file.read_text(encoding='utf-8-sig'))
            if saved.get('copied_sha256') == manifest['artifacts']['modified.mdb']:
                receipt = {key: saved.get(key) for key in ('server_mdb', 'server_siteinfo', 'activated', 'status')}
        except (ValueError, OSError):
            pass
    return {'id': path.parent.name, 'manifest_file': str(path), 'mdb_file': str(path.parent / 'modified.mdb'),
            'sha256': manifest['artifacts']['modified.mdb'], 'workspace': manifest['workspace'],
            'objects': [name for name in objects if name],
            'field_changes': sum(op.get('action') == 'add_field' for op in manifest['operations']),
            'status': 'ready_for_designer', 'database_published': False, 'review': receipt}


def session_packages(messages: list[dict]) -> list[dict]:
    """Only offer packages actually returned by this session's design tools."""
    found = {}
    for message in messages:
        if message.get('role') != 'tool' or message.get('name') not in ('generate_designer_design_package', 'generate_designer_cdo_package'):
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
    """Copy into a unique remote review directory; optionally select for next launch."""
    item = summary(manifest_file)
    if not all((config.DESIGNER_SERVER_SHARE, config.DESIGNER_DB_SERVER, config.DESIGNER_WINDOWS_USER,
                config.DESIGNER_WINDOWS_PASSWORD, config.DESIGNER_UI_EXE)):
        raise ValueError('请先配置 Designer 服务器共享、Windows 凭据和界面程序路径')
    if config.DESIGNER_SERVER_SHARE.rstrip('\\').casefold() != ('\\\\' + config.DESIGNER_DB_SERVER + '\\C').casefold():
        raise ValueError('共享必须是已配置 Designer 服务器的 C 共享')
    exe = PureWindowsPath(config.DESIGNER_UI_EXE)
    if not exe.is_absolute() or exe.drive.lower() != 'c:' or '..' in exe.parts or exe.suffix.lower() != '.exe' or any(c in str(exe) for c in '\r\n\x00"'):
        raise ValueError('Designer 界面程序必须是 C 盘上的明确 exe 路径')
    with _lock:
        env = {k: v for k, v in os.environ.items() if k.casefold() != 'psmodulepath'}
        for name in ('DESIGNER_SERVER_SHARE', 'DESIGNER_WINDOWS_USER', 'DESIGNER_WINDOWS_PASSWORD', 'DESIGNER_UI_EXE'):
            env[name] = getattr(config, name)
        shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        folder = Path(item['manifest_file']).parent
        result_file = folder / 'designer_review.json'
        args = [str(shell), '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_name('review_transfer.ps1')),
                '-LocalMdb', item['mdb_file'], '-ExpectedSha256', item['sha256'], '-ResultFile', str(result_file),
                '-Activate', str(bool(activate)).lower()]
        try:
            process = subprocess.run(args, env=env, capture_output=True, timeout=90,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if process.returncode:
                raise ValueError(redact(process.stderr.decode('utf-8', errors='replace'))[:2000])
            receipt = json.loads(result_file.read_text(encoding='utf-8-sig'))
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise ValueError('Designer 交接失败：' + redact(str(exc))) from None
        instructions = ('保存并关闭当前 Designer，再重新打开。' if receipt.get('activated') else
                        '请保存当前设计，在 Designer 的打开文件入口选择 server_mdb；需要站点文件时选择 server_siteinfo。')
        return {**item, **receipt, 'instructions': instructions + '在客户工作区中搜索对象名称；审核、编译和发布继续在 Designer 内完成。',
                'designer_open_verified': False}
