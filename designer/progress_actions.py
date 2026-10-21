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
        if config.DESIGNER_REQUIRE_DATABASE_BACKUP:
            if not backup:
                raise ValueError('请先备份数据库')
            _, saved = load_receipt(backup['receipt_file'], backup['receipt_sha256'])
            if saved.get('status') != 'backup_verified' or saved.get('target') != target() or not recent(saved.get('created_utc')):
                raise ValueError('备份已过期或目标不匹配，请重新备份')
        if not (package.get('review') or {}).get('server_siteinfo'):
            raise ValueError('请先准备 Designer 文件及 SiteInfo')
    except (KeyError, ValueError, OSError) as exc:
        reasons.append(str(exc))
    return {'target': target(), 'plan': plan, 'backup': backup,
            'backup_required': config.DESIGNER_REQUIRE_DATABASE_BACKUP, 'ready': not reasons,
            'reason': reasons[0] if reasons else ''}


def combined_manifest(package, manifests):
    """Export the final owned MDB against its earliest owned baseline for a batch."""
    if package.get('working'):
        from designer import working
        work = working.load(package['working']['id'])
        if work['latest_manifest'] != package['manifest_file']:
            raise ValueError('工作 MDB 已更新，请刷新后检查发布')
        # A new chat still edits the same file. Compare its whole current cycle,
        # including changes already saved to that file in earlier chats.
        manifests = work.get('cycle_manifests') or manifests
    pairs = [publication.verified_manifest(path) for path in manifests]
    if package.get('working'):
        pairs.reverse()  # Stored in save order; the plan consumes newest first.
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
    return str(path)


def prepare_plan(package, manifests, final_file=None):
    if final_file:
        path = Path(final_file)
    elif package.get('working'):
        from designer.cycle import final_manifest
        path = Path(final_manifest(package['manifest_file']))
    else:
        path = Path(combined_manifest(package, manifests))
    result = publication.preflight(str(path))
    # The exact catalog inspected here must still match under the publication lock.
    with publication.target_connection() as conn:
        cur = conn.cursor()
        schema = publication.target_schema(cur)
        fingerprint = publication.metadata_fingerprint(cur, schema)
        baseline_file = root_dir() / 'published_baseline.json'
        baseline = json.loads(baseline_file.read_text(encoding='utf-8')) if baseline_file.is_file() else None
        baseline_current = publication.baseline_matches(cur, schema, baseline) if baseline else True
    baseline_file = root_dir() / 'published_baseline.json'
    if baseline_file.is_file():
        baseline = json.loads(baseline_file.read_text(encoding='utf-8'))
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if baseline.get('target') == result['target'] and (
                baseline.get('status', 'verified') != 'verified' or baseline.get('sha256') != manifest['source_sha256']
                or not baseline_current):
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
            (config.DESIGNER_REQUIRE_DATABASE_BACKUP and
             options.get('expected_backup_sha256') != info['backup']['receipt_sha256'])):
        raise ValueError('发布确认内容已更新，请重新查看后确认')
    if not config.DESIGNER_TEST_TARGET_CONFIRMED:
        raise ValueError('目标尚未配置为已确认测试库')
    return info


def publish(state, options):
    info = validate_publish(state, options)
    plan = info['plan']
    siteinfo = copy_siteinfo(state['package'])
    return publication.publish_database(plan['manifest_file'], plan['manifest_sha256'],
                                        info['backup']['receipt_file'] if config.DESIGNER_REQUIRE_DATABASE_BACKUP else '', str(siteinfo),
                                        expected_target_fingerprint=plan['target_fingerprint'])


def copy_siteinfo(package):
    """Use the same authenticated, fixed server-copy mechanism as review sync."""
    import os
    import re
    import subprocess
    from designer.publication import redact
    remote = (package.get('review') or {}).get('server_siteinfo', '')
    if not review.valid_server_file(remote, 'SiteInfo.mdb'):
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
    from designer import wcf_contract
    eligible = package.get('service_objects', package.get('objects', []))
    names = eligible if requested is None else requested
    if not isinstance(names, list) or not names:
        raise ValueError('请选择至少一个不重复的验收对象')
    names = wcf_contract.names(names)
    for name in names:
        if name not in eligible:
            raise ValueError('WCF 验收对象必须来自当前设计')
    # A selection can add emphasis, but cannot narrow whole-design acceptance.
    return list(dict.fromkeys([*names, *eligible]))


def final_wcf_fields(mdb_file, checksum, fields, names):
    """Read actual compiled fields, including inheritance, before DLL checks."""
    source = source_path(mdb_file, '.mdb')
    if vendor.digest(source) != checksum:
        raise ValueError('WCF 验收来源已变化')
    from designer import wcf_contract
    fields = wcf_contract.fields(fields)
    if {row['owner'] for row in fields} - set(names):
        raise ValueError('WCF 验收范围遗漏当前设计字段')
    actual = {}
    for owner in dict.fromkeys(row['owner'] for row in fields if row['owner'] in names):
        actual[owner] = set()
        offset = 0
        while True:
            page = vendor.query(mdb_file, 'list', 'field', owner=owner, offset=offset, limit=100)
            records, total = page['records'], page['total']
            if total > 10000 or (not records and offset < total):
                raise ValueError('WCF 最终字段目录未完整读取')
            actual[owner].update(row['name'] for row in records)
            offset += len(records)
            if offset >= total:
                break
    if vendor.digest(source) != checksum:
        raise ValueError('WCF 验收来源已变化')
    result = []
    for row in fields:
        if row['owner'] not in names:
            continue
        present = row['name'] in actual[row['owner']]
        if row['present'] and not present:
            raise ValueError(f"最终 MDB 缺少预期字段：{row['owner']}.{row['name']}")
        result.append({**row, 'present': present})
    return result


def verify_wcf_fields(result, state, names):
    from designer import wcf_contract
    from designer.operations import final_fields
    names = wcf_contract.names(names)
    fields = state.get('field_expectations')
    if fields is None:
        fields = final_fields([{**row, 'kind': row.get('kind', 'field')} for row in state['design_rows']])
    if set((state.get('package') or {}).get('service_objects', [])) - set(names):
        raise ValueError('WCF 验收范围遗漏当前设计对象')
    return wcf_contract.verify_result(result, fields, names)


def complete_wcf_receipt(result, package, fields):
    if not result.get('fields_verified') or result.get('verification_version') != 2:
        return False
    try:
        from designer import wcf_contract
        checked = wcf_contract.fields(result.get('verified_fields', []))
        verified = {(row['owner'], row['name']): row for row in checked}
        if set(verified) != {(row['owner'], row['name']) for row in fields}:
            return False
        if any(row['present'] and not verified[row['owner'], row['name']]['present'] for row in fields):
            return False
        if any(row['present'] and not {'data_type', 'is_list'}.issubset(row) for row in verified.values()):
            return False
        # A deleted override can expose an inherited field again. Use the final
        # compiled expectation, retaining every raw change's field identity.
        verify_wcf_fields(result, {'package': package, 'field_expectations': list(verified.values())}, result.get('verified_types', []))
        return True
    except (ValueError, KeyError, TypeError):
        return False


def compiled_field_contracts(mdb_file, checksum, fields):
    """Validate actual SDK maintenance links and bind schema to this compiled MDB."""
    source = source_path(mdb_file, '.mdb')
    if vendor.digest(source) != checksum:
        raise ValueError('编译 MDB 已变化')
    from designer import wcf_contract
    fields = wcf_contract.fields(fields)
    result = vendor.run(artifact_dir(), {'mode': 'field_contracts', 'mdb': str(source), 'fields': fields}) if fields else {'complete': True, 'fields': []}
    if result.get('complete') is not True or not isinstance(result.get('fields'), list) or vendor.digest(source) != checksum:
        raise ValueError('编译字段契约检查未完成')
    actual = wcf_contract.fields(result['fields'], enriched=True)
    expected = {(row['owner'], row['name']): row for row in fields}
    if set(expected) != {(row['owner'], row['name']) for row in actual}:
        raise ValueError('编译字段契约返回的对象或字段与请求不一致')
    if any(expected[row['owner'], row['name']]['present'] and not row['present'] for row in actual):
        raise ValueError('编译字段契约缺少预期字段')
    return actual


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
