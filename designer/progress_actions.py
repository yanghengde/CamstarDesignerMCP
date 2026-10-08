"""Bind UI execution to owned design packages, reviewed plans and verified receipts."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import config
from designer import publication, review, vendor
from designer.files import artifact_dir, root_dir, source_path


def target():
    return {'server': config.DESIGNER_DB_SERVER, 'database': config.DESIGNER_DB_NAME}


def recent(created):
    try:
        return 0 <= (datetime.now(timezone.utc) - datetime.fromisoformat(created)).total_seconds() <= 3600
    except (ValueError, TypeError):
        return False


def load_receipt(path, checksum):
    file = source_path(path, '.json')
    if not checksum or vendor.digest(file) != checksum:
        raise ValueError('执行凭证已变化，请重新检查')
    return file, json.loads(file.read_text(encoding='utf-8'))


def publication_state(receipts, package):
    plans = [item.get('result') for item in receipts if item['tool'] == 'prepare_designer_publish_plan']
    backups = [item.get('result') for item in receipts if item['tool'] == 'backup_designer_test_database']
    plan, backup = plans[-1] if plans else None, backups[-1] if backups else None
    reasons = []
    try:
        if not plan:
            raise ValueError('请先检查发布')
        _, checked = load_receipt(plan['plan_file'], plan['plan_sha256'])
        if not checked.get('ready_for_publish') or checked.get('target') != target():
            raise ValueError('发布目标或检查结果未通过')
        if checked.get('design_sha256') != package['sha256'] or not recent(checked.get('created_utc')):
            raise ValueError('发布检查已过期，请重新检查')
        file, _ = publication.verified_manifest(checked['manifest_file'])
        if vendor.digest(file) != checked['manifest_sha256']:
            raise ValueError('发布设计已变化，请重新检查')
        if not backup:
            raise ValueError('请先备份数据库')
        _, saved = load_receipt(backup['receipt_file'], backup['receipt_sha256'])
        if saved.get('status') != 'backup_verified' or saved.get('target') != target() or not recent(saved.get('created_utc')):
            raise ValueError('备份已过期或目标不匹配，请重新备份')
        if not (package.get('review') or {}).get('server_siteinfo'):
            raise ValueError('请先准备 Designer 文件及 SiteInfo')
    except (KeyError, ValueError, OSError) as exc:
        reasons.append(str(exc))
    return {'target': target(), 'plan': plan, 'backup': backup, 'ready': not reasons,
            'reason': reasons[0] if reasons else ''}


def prepare_plan(package, manifests):
    """Export the final owned MDB against its earliest owned baseline for a batch."""
    pairs = [publication.verified_manifest(path) for path in manifests]
    if len({item['workspace'] for _, item in pairs}) != 1:
        raise ValueError('连续设计的工作区不一致，请在设计对话中核对')
    path = Path(package['manifest_file'])
    if len(pairs) > 1:
        folder = artifact_dir()
        earliest_path, earliest = pairs[-1]
        shutil.copyfile(earliest_path.parent / 'baseline.mdb', folder / 'baseline.mdb')
        shutil.copyfile(path.parent / 'modified.mdb', folder / 'modified.mdb')
        if vendor.digest(folder / 'baseline.mdb') != earliest['source_sha256'] or vendor.digest(folder / 'modified.mdb') != package['sha256']:
            raise ValueError('连续设计在复制期间变化，请重新核对')
        exported = vendor.export(folder, folder / 'baseline.mdb', folder / 'modified.mdb')
        shutil.copyfile(exported['xml_file'], folder / 'changes.xml')
        manifest = {**pairs[0][1], 'source_file': earliest['source_file'], 'source_sha256': earliest['source_sha256'],
                    'operations': [op for _, item in reversed(pairs) for op in item['operations']],
                    'artifacts': {name: vendor.digest(folder / name) for name in ('baseline.mdb', 'modified.mdb', 'changes.xml')},
                    'validation': exported['validation'], 'owned_source_manifests': manifests}
        path = folder / 'manifest.json'
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    result = publication.preflight(str(path))
    # The exact catalog inspected here must still match under the publication lock.
    with publication.target_connection() as conn:
        cur = conn.cursor()
        fingerprint = publication.metadata_fingerprint(cur, publication.target_schema(cur))
    baseline_file = root_dir() / 'published_baseline.json'
    if baseline_file.is_file():
        baseline = json.loads(baseline_file.read_text(encoding='utf-8'))
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if baseline.get('target') == result['target'] and (
                baseline.get('status', 'verified') != 'verified' or baseline.get('sha256') != manifest['source_sha256']
                or baseline.get('metadata_fingerprint') != fingerprint):
            result['blockers'].append('当前设计与已发布基线不一致，请先在设计对话中重新核对')
    result.update(ready_for_publish=not result['blockers'], target_fingerprint=fingerprint,
                  design_sha256=package['sha256'], created_utc=datetime.now(timezone.utc).isoformat(),
                  changes=json.loads(path.read_text(encoding='utf-8'))['operations'])
    plan_file = Path(result['plan_file'])
    plan_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return {**result, 'plan_sha256': vendor.digest(plan_file)}


def validate_publish(state, options):
    info = state['publication']
    if not info['ready']:
        raise ValueError(info['reason'])
    if (options.get('expected_plan_sha256') != info['plan']['plan_sha256'] or
            options.get('expected_backup_sha256') != info['backup']['receipt_sha256']):
        raise ValueError('发布确认内容已更新，请重新查看后确认')
    if not config.DESIGNER_TEST_TARGET_CONFIRMED:
        raise ValueError('目标尚未配置为已确认测试库')
    return info


def publish(state, options):
    info = validate_publish(state, options)
    plan = info['plan']
    siteinfo = copy_siteinfo(state['package'])
    return publication.publish_database(plan['manifest_file'], plan['manifest_sha256'],
                                        info['backup']['receipt_file'], str(siteinfo),
                                        expected_target_fingerprint=plan['target_fingerprint'])


def copy_siteinfo(package):
    """Use the same authenticated, fixed server-copy mechanism as review sync."""
    import os
    import re
    import subprocess
    from designer.publication import redact
    remote = (package.get('review') or {}).get('server_siteinfo', '')
    if not re.fullmatch(r'C:\\Temp\\DesignerMCP\\Review_[0-9a-f]{32}\\SiteInfo\.mdb', remote, re.IGNORECASE):
        raise ValueError('请先准备 Designer 文件及 SiteInfo')
    if config.DESIGNER_SERVER_SHARE.rstrip('\\').casefold() != ('\\\\' + config.DESIGNER_DB_SERVER + '\\C').casefold():
        raise ValueError('服务器共享配置不匹配')
    env = {key: value for key, value in os.environ.items() if key.casefold() != 'psmodulepath'}
    for name in ('DESIGNER_SERVER_SHARE', 'DESIGNER_WINDOWS_USER', 'DESIGNER_WINDOWS_PASSWORD'):
        env[name] = getattr(config, name)
        if not env[name]:
            raise ValueError('Designer 服务器共享或凭据未配置')
    file = Path(package['manifest_file']).parent / 'review_siteinfo.mdb'
    shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_name('progress_siteinfo.ps1')),
                             '-ServerFile', remote, '-LocalFile', str(file)], env=env, capture_output=True, timeout=60,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('SiteInfo 读取失败：' + redact(result.stderr.decode('utf-8', errors='replace'))[:1200])
    return source_path(str(file), '.mdb')


def wcf_types(package, requested):
    from designer.metadata import identifier
    names = package['objects'] if requested is None else requested
    if not 1 <= len(names) <= 20 or len(names) != len(set(names)):
        raise ValueError('请选择 1～20 个不重复的验收对象')
    for name in names:
        identifier(name)
        if name not in package['objects']:
            raise ValueError('WCF 验收对象必须来自当前设计')
    return names


def verify_wcf_fields(result, state, names):
    if result.get('status') != 'services_generated' or result.get('partial_package') or not result.get('source_unchanged'):
        raise ValueError('WCF 全量生成未通过核对')
    checks = {item['name'].split('.')[-1].removesuffix('Changes'): item.get('properties', []) for item in result.get('type_checks', [])}
    for row in state['design_rows']:
        if row['owner'] in names and row['name'] not in checks.get(row['owner'], []):
            raise ValueError(f"WCF 未包含当前字段：{row['owner']}.{row['name']}")
    if any(name not in checks for name in names):
        raise ValueError('WCF 缺少验收对象')
    if not result.get('data_contract_count') or not result.get('service_count') or not result.get('files_sha256'):
        raise ValueError('WCF 产物或生成计数不完整')
    files = {name.replace('\\', '/') for name in result['files_sha256']}
    if not {'client/Camstar.WCFClient.dll', 'server/bin/Camstar.WCFService.dll'}.issubset(files):
        raise ValueError('WCF 客户端或服务端程序集缺失')
    return result


def wcf_archive(result):
    import zipfile
    report = source_path(result['result_file'], '.json')
    folder = report.parent
    if folder.parent != root_dir() / 'artifacts':
        raise ValueError('WCF 产物目录不匹配')
    files = []
    for name, checksum in result['files_sha256'].items():
        file = (folder / name).resolve()
        if not file.is_relative_to(folder.resolve()) or file.parts[len(folder.parts)] not in ('client', 'server'):
            raise ValueError('WCF 产物路径不匹配')
        # Generated config files can contain the server connection string.
        # Keep runtime configuration on the server and export only assemblies.
        if file.suffix.casefold() != '.dll':
            continue
        if not file.is_file() or vendor.digest(file) != checksum:
            raise ValueError('WCF 产物已变化，请重新生成')
        files.append((file, name.replace('\\', '/')))
    if not files:
        raise ValueError('WCF 程序集不存在')
    # Each response owns its archive, including while another response is
    # streaming on Windows. Only verified DLLs enter the download.
    from uuid import uuid4
    identity = uuid4().hex
    output = folder / ('wcf_package_' + identity + '.zip')
    temporary = folder / (identity + '.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for file, name in files:
                archive.write(file, name)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
