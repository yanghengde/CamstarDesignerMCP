"""Read-only delivery evidence, without inferring deployment or rerunning tests."""
from pathlib import Path
import json

from designer import clf_workspace as core, publication, testing, testing_store as store, vendor
from designer.files import source_path


def report(owner, plan_id):
    plan = core.item(owner, 'plan', plan_id)
    receipts = [r for r in core.items(owner, 'delivery') if r['plan_id'] == plan_id]
    saved = next((r for r in receipts if r['action'] == 'save' and r['status'] == 'saved'), None)
    restored = any(r['action'] == 'restore' and r['status'] == 'completed' for r in receipts)
    project = testing.project(plan['project_id'])
    current = vendor.digest(Path(project['mdb_file']))
    checksum = (saved or {}).get('result', {}).get('saved_sha256', '')
    version_current = bool(checksum and checksum == current and not restored)
    checks = []
    integrity = False
    if saved:
        try:
            publication.verified_manifest(saved['result']['files']['manifest.json'])
            integrity = True
        except (ValueError, OSError, KeyError) as exc:
            checks.append('保存设计包未通过当前完整性核验：' + str(exc))
    compiled = next((r for r in receipts if r['action'] == 'compile' and r['status'] == 'completed'
                     and r['expected_sha256'] == checksum), None) if checksum else None
    compile_valid = False
    if compiled:
        try:
            value = compiled['result']
            compile_valid = (value['source_unchanged'] is True and value['input_sha256'] == checksum and
                vendor.digest(source_path(value['compiled_mdb'], '.mdb')) == value['compiled_sha256'])
            if not compile_valid:
                checks.append('编译回执的输入、产物或来源保持证据不匹配')
        except (ValueError, OSError, KeyError) as exc:
            checks.append('编译产物无法核验：' + str(exc))
    if restored:
        checks.append('该方案的工作文件保存已恢复；历史回执保留，不能视为当前待交付版本')
    elif saved and not version_current:
        checks.append('当前工作源已偏离该方案保存版本，请核对后续改动')
    runs = []
    # Bound after owner/project/source filtering, so unrelated history cannot
    # crowd out this version's evidence. No business input is exported here.
    with store.connection() as conn:
        where = "owner=? AND json_extract(document,'$.spec.project_id')=? AND json_extract(document,'$.spec.kind')='clf' AND json_extract(document,'$.spec.source_sha256')=?"
        args = (owner, plan['project_id'], checksum)
        count = conn.execute('SELECT COUNT(*) FROM test_runs WHERE ' + where, args).fetchone()[0]
        records = conn.execute('SELECT document FROM test_runs WHERE ' + where + ' ORDER BY updated DESC LIMIT 100', args).fetchall()
    for row in records:
        record = json.loads(row[0])
        spec = record['spec']
        inspection = core.inspect_run(owner, record['id'])
        runs.append({'run_id': record['id'], 'clf_name': spec.get('clf_name'),
                     'status': record['status'], 'assertion_result': inspection['assertion_result'],
                     'business_outcome': inspection['business_outcome'],
                     'target_name_evidence': bool(inspection['target_evidence']),
                     'environment_id': spec.get('environment_id'), 'environment_version': spec.get('environment_version'),
                     'client_sha256': spec.get('client_sha256'), 'deployment_source': 'unknown'})
    if not saved:
        checks.append('尚无官方保存及回读回执')
    if not compile_valid:
        checks.append('尚无通过完整性核验的对应版本编译产物')
    if not runs:
        checks.append('该保存源版本尚无本项目 CLF 测试回执')
    checks.extend(['尚无该方案的实际数据库发布回执', 'WCF 是否需要重建、交付以及部署来源尚未核对',
                   '已配置断言与 CLF 名称证据不等于现场业务验收；仍需确认覆盖范围和部署版本'])
    if vendor.digest(Path(project['mdb_file'])) != current:
        raise ValueError('生成交付报告期间工作源变化，请重新读取')
    stages = [
        {'id': 'preflight', 'name': '官方方案预检', 'status': 'passed' if plan['result']['metadata_preflight_passed'] else 'conflict'},
        {'id': 'save', 'name': '工作 MDB 保存与回读', 'status': 'restored' if restored else 'verified' if saved and integrity and version_current else 'needs_review' if saved else 'missing'},
        {'id': 'compile', 'name': '对应版本编译', 'status': 'verified' if compile_valid and version_current and integrity else 'historical' if compiled else 'missing'},
        {'id': 'tests', 'name': '同源测试回执', 'status': 'observed' if runs else 'missing'},
        {'id': 'database', 'name': '数据库发布', 'status': 'not_recorded'},
        {'id': 'wcf', 'name': 'WCF 生成与交付', 'status': 'applicability_unknown'},
        {'id': 'deployment', 'name': '部署来源与现场验收', 'status': 'unknown'},
    ]
    return {'schema_version': 1, 'created_utc': store.now(), 'plan_id': plan_id, 'project_id': plan['project_id'],
            'ready_for_release': False, 'overall': 'needs_review' if saved and (not integrity or not version_current or compiled and not compile_valid) else 'pending_acceptance',
            'versions': {'preflight_source': plan['result']['source_sha256'], 'saved_source': checksum,
                         'current_source': current, 'compiled_input': (compiled or {}).get('expected_sha256', ''),
                         'compiled_artifact': (compiled or {}).get('result', {}).get('compiled_sha256', '')},
            'save_package_verified': integrity, 'current_matches_saved': version_current,
            'stages': stages, 'blockers': checks, 'test_evidence': runs,
            'test_scope': {'source': 'owned_project_local_clf_receipts', 'source_sha256': checksum,
                           'total': count, 'included': len(runs), 'truncated': count > len(runs),
                           'scope': 'same_working_source_only_not_confirmed_deployment_or_all_changed_rules'},
            'receipts': [{'id': r['id'], 'action': r['action'], 'status': r['status'],
                          'updated_utc': r.get('updated_utc'), 'error': r.get('error')} for r in receipts],
            'notice': '报告只整理当前用户、本项目、本方案的已有证据，不执行测试或发布；阶段完成与整体交付分别判断。'}
