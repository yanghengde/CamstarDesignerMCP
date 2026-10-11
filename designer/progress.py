"""Project real tool receipts onto a personal Designer workflow."""
import ast
import asyncio
import json
from pathlib import Path
import time

from agent.memory import get_user_messages, get_sessions
from designer import review, publication, progress_store as store
from designer.files import source_path
from designer.vendor import digest
from designer import progress_actions

TITLES = ['提交需求', '识别设计', '检查现有定义', '生成设计', '核对结果', '检查设计', 'Compile', 'Update Database', '生成 WCF', '部署 WCF', '验证结果']
DONE = {'done', 'confirmed', 'skipped'}
DESIGN_TOOLS = {'generate_designer_cdo_package', 'generate_designer_design_package', 'sync_designer_working_file',
                'restore_designer_mdb_backup', 'sync_designer_file', 'restore_designer_mdb'}
STAGES = {'generate_designer_cdo_package': 4, 'generate_designer_design_package': 4,
          'check_designer_package': 5, 'prepare_designer_review': 6, 'compile_designer_mdb': 7,
          'publish_designer_test_database': 8, 'verify_designer_published_design': 11,
          'prepare_designer_publish_plan': 8, 'backup_designer_test_database': 8,
          'generate_designer_wcf_package': 9}
STAGES.update({name: 4 for name in DESIGN_TOOLS})
STAGES['confirm_designer_manual_publish'] = 8
_tasks = set()


def parse(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = ast.literal_eval(value)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError, SyntaxError):
        return None


def tool_receipts(messages):
    calls, receipts = {}, []
    turn_start = None
    for index, message in enumerate(messages):
        if message.get('role') == 'user':
            turn_start = index
        for call in message.get('tool_calls', []):
            try:
                calls[call['id']] = (json.loads(call['function']['arguments']), turn_start)
            except (KeyError, ValueError):
                pass
        if message.get('role') == 'tool':
            content = message.get('content', '')
            arguments, call_turn = calls.get(message.get('tool_call_id'), ({}, turn_start))
            receipts.append({'tool': message.get('name', ''), 'arguments': arguments,
                             'turn_start': call_turn,
                             'call_id': message.get('tool_call_id', ''), 'result': parse(content),
                             'status': 'failed' if isinstance(content, str) and content.startswith('Error') else 'done',
                             'error': content[:500] if isinstance(content, str) and content.startswith('Error') else ''})
    return receipts


def workflow_receipts(messages, events):
    receipts = tool_receipts(messages)
    seen = {item['call_id'] for item in receipts if item['call_id']}
    event_times = {event['call_id']: event['started'] for event in events if event['call_id']}
    for item in receipts:
        item['started'] = event_times.get(item['call_id'])
    call_turns, turn_start = {}, None
    for index, message in enumerate(messages):
        if message.get('role') == 'user':
            turn_start = index
        for call in message.get('tool_calls', []):
            call_turns[call.get('id')] = turn_start
    latest_user = next((index for index in range(len(messages)-1, -1, -1)
                        if messages[index].get('role') == 'user'), None)
    for event in events:
        if event['call_id'] and event['call_id'] in seen:
            continue
        fallback = latest_user if event['status'] == 'running' and event['tool'] in DESIGN_TOOLS else None
        item = {**event, 'turn_start': call_turns.get(event['call_id'], fallback)}
        position = next((index for index, receipt in enumerate(receipts)
                         if receipt.get('started') is not None and receipt['started'] > event['started']), len(receipts))
        receipts.insert(position, item)
    return receipts


def is_design_attempt(item):
    # Policy-cancelled tool calls have no execution receipt or design result.
    if item['tool'] in {'sync_designer_working_file', 'restore_designer_mdb_backup', 'sync_designer_file', 'restore_designer_mdb'}:
        return bool((item.get('result') or {}).get('files', {}).get('manifest.json'))
    return item['tool'] in DESIGN_TOOLS and ((isinstance(item.get('result'), dict) and bool(item['result']))
                                           or item['status'] in ('running', 'failed', 'interrupted'))


def same_file(a, b):
    return bool(a and b and Path(a).resolve() == Path(b).resolve())


def design_task_summary(session, attempt):
    args = attempt.get('arguments') or {}
    operations = args.get('operations', [])
    updated = attempt.get('started') or 0
    manifest_file = (attempt.get('result') or {}).get('files', {}).get('manifest.json')
    if manifest_file:
        try:
            file = source_path(manifest_file, '.json')
            operations = json.loads(file.read_text(encoding='utf-8')).get('operations', operations)
            updated = updated or file.stat().st_mtime
        except (ValueError, OSError):
            pass
    if not isinstance(operations, list):
        operations = []
    names = list(dict.fromkeys(op.get('owner') or op.get('name') for op in operations
                              if isinstance(op, dict) and isinstance(op.get('owner') or op.get('name'), str)))
    if isinstance(args.get('cdo_name'), str) and args['cdo_name']:
        names = [args['cdo_name']]
    title = '、'.join(names[:3]) + ' 设计' if names else '元数据设计'
    return {**session, 'title': title, 'design_updated_at': updated}


def tasks(username):
    result = []
    for session in get_sessions(username):
        messages = get_user_messages(username, session['id'])
        receipts = workflow_receipts(messages, store.operations(username, session['id']))
        designs = [item for item in receipts if is_design_attempt(item)]
        if designs:
            result.append(design_task_summary(session, designs[-1]))
    return sorted(result, key=lambda item: item['design_updated_at'], reverse=True)


def snapshot(username, session_id):
    messages = get_user_messages(username, session_id)
    events = store.operations(username, session_id)
    receipts = workflow_receipts(messages, events)
    stages = [{'number': i+1, 'title': title, 'status': 'pending', 'detail': '', 'actions': []} for i, title in enumerate(TITLES)]
    def mark(number, status='done', detail=''):
        stages[number-1].update(status=status, detail=detail)
    designs = [item for item in receipts if is_design_attempt(item)]
    design_turns = {designs[-1]['turn_start']} - {None} if designs else set()
    if designs:
        mark(1)
        mark(2, detail='本次设计参数已提交')
    all_saved = [item for item in designs if (item.get('result') or {}).get('files', {}).get('manifest.json')]
    saved = all_saved if designs and designs[-1] in all_saved else []
    package, manifests, error = None, [], ''
    change_summary = {'rows': []}
    publication_info = None
    if saved:
        path = saved[-1]['result']['files']['manifest.json']
        try:
            package = review.summary(path)
            from designer import working
            if package.get('working'):
                path = package['working']['latest_manifest']
                package = review.summary(path)
                working.check_current(json.loads(Path(path).read_text(encoding='utf-8')))
            owned = {str(Path(item['result']['files']['manifest.json']).resolve()): item for item in all_saved}
            if package.get('working'):
                project_state = working.load(package['working']['id'])
                for cycle_path in project_state.get('cycle_manifests', [path]):
                    owned.setdefault(str(Path(cycle_path).resolve()), {'turn_start': None})
            cursor = path
            while cursor and len(manifests) < 100:
                manifest_path, manifest = review.package(cursor)
                if manifest.get('source_sha256') and digest(source_path(manifest['source_file'], '.mdb')) != manifest['source_sha256']:
                    raise ValueError('设计来源已变化，请重新核对')
                if any(str(manifest_path) == item[0] for item in manifests):
                    raise ValueError('设计来源存在循环')
                manifests.append((str(manifest_path), manifest))
                if owned[str(manifest_path)].get('turn_start') is not None:
                    design_turns.add(owned[str(manifest_path)]['turn_start'])
                source = Path(manifest.get('source_file', ''))
                parent = str((source.parent / 'manifest.json').resolve())
                explicit_parent = manifest.get('parent_manifest_file')
                if explicit_parent:
                    parent = str(Path(explicit_parent).resolve())
                cursor = parent if (explicit_parent or source.name == 'modified.mdb') and parent in owned else ''
            operations = [op for _, manifest in reversed(manifests) for op in manifest.get('operations', [])]
            from designer.operations import summary as summarize
            affected = [name for _, manifest in manifests for name in manifest.get('execution', {}).get('affected_cdos', [])]
            change_summary = summarize(operations, affected)
            package.update({key: change_summary[key] for key in ('objects', 'service_objects', 'field_count', 'change_count')})
            mark(2, detail=f"{len(package['objects'])} 个对象 · {package['field_count']} 个字段")
            mark(4)
        except (ValueError, KeyError, OSError) as exc:
            error = str(exc)
            mark(4, 'blocked', '设计文件已变化')
    users = [item for index, item in enumerate(messages) if index in design_turns and item.get('role') == 'user']
    reads = [item for item in receipts if item.get('turn_start') in design_turns and item['tool'].startswith(('get_designer_', 'list_designer_', 'inspect_designer_', 'analyze_designer_')) and item['status'] == 'done' and item.get('result')]
    if reads:
        mark(3)
    elif designs:
        mark(3, 'skipped', '由生成设计一并核对')
    if designs and designs[-1]['status'] in ('failed', 'interrupted'):
        mark(4, 'failed', designs[-1].get('error', '设计未完成'))
    if designs:
        mark(2, detail=stages[1]['detail'])
    acknowledgements = store.confirmations(username, session_id, package['id']) if package else {}
    relevant = []
    if package:
        for item in receipts:
            args = item.get('arguments', {})
            if (args.get('mdb_file') and same_file(args['mdb_file'], package['mdb_file'])
                    and args.get('expected_sha256') and args['expected_sha256'] != package['sha256']):
                continue
            if item.get('package_id') == package['id'] or same_file(args.get('manifest_file'), package['manifest_file']) or same_file(args.get('mdb_file'), package['mdb_file']):
                relevant.append(item)
        compiled_paths = {(item.get('result') or {}).get('compiled_mdb') for item in relevant}
        relevant += [item for item in receipts if item not in relevant and item['tool'] == 'generate_designer_wcf_package'
                     and item.get('arguments', {}).get('compiled_mdb') in compiled_paths - {None}]
        publication_info = progress_actions.publication_state(relevant, package)
        for item in relevant:
            result = item.get('result') or {}
            tool = item['tool']
            if tool == 'check_designer_package' and result.get('intact'):
                mark(5, detail='文件与来源已核验')
            elif tool == 'compile_designer_mdb' and result.get('compiled_mdb'):
                mark(7, detail='官方编译完成')
            elif tool in {'publish_designer_test_database', 'confirm_designer_manual_publish'} and result.get('status') == 'database_published':
                mark(8, detail='手动发布已核验' if tool == 'confirm_designer_manual_publish' else 'Update Database 完成')
            elif tool == 'generate_designer_wcf_package' and result.get('status') == 'services_generated':
                if not result.get('partial_package') and result.get('fields_verified'):
                    mark(9, detail=f"{result.get('service_count', 0)} 个服务 · {result.get('data_contract_count', 0)} 个数据契约")
                else:
                    mark(9, 'waiting', '生成产物需完整核对')
            elif tool == 'verify_designer_published_design':
                if result.get('status') == 'verified':
                    mark(8, detail='对象与字段已核验')
                    mark(11, 'waiting', '数据库已核验，待业务验收')
                elif result.get('status') == 'verification_failed':
                    mark(11, 'waiting', '数据库核验未通过')
                elif result.get('status') == 'partial_verification':
                    mark(11, 'waiting', '部分变更需 Designer 确认')
            if item['status'] in ('failed', 'interrupted') and tool in STAGES:
                mark(STAGES[tool], 'failed', item.get('error', '操作未完成'))
        for stage, status in acknowledgements.items():
            if stages[stage-1]['status'] != 'done':
                mark(stage, status, '用户确认' if status == 'confirmed' else '无需服务')
        if package.get('review') and stages[5]['status'] == 'pending':
            mark(6, 'waiting', '文件已准备，待 Designer 审核')
        syncs = [item for item in relevant if item.get('action') == 'sync' and item.get('result') and item['result'].get('server_mdb') == (package.get('review') or {}).get('server_mdb')]
        if syncs and syncs[-1]['result'].get('unchanged') is False:
            for number in range(5, len(TITLES)+1):
                mark(number, 'blocked', 'Designer 文件已变化，需重新核对')
        if stages[3]['status'] in ('failed', 'blocked'):
            for number in range(5, len(TITLES)+1):
                mark(number, 'blocked', '等待设计结果核对')
        stage_actions = {
            5: [('check', '核对结果')], 6: [('prepare', '准备 Designer 文件'), ('confirm_review', '手动检查确认')],
            7: [('compile', '执行 Compile'), ('confirm_compile', '手动编译确认')],
            8: [('preflight', '检查发布')] +
               ([('backup', '备份数据库')] if publication_info['backup_required'] else []) +
               [('publish', '执行 Update Database'), ('confirm_publish', '核验手动更新' if package.get('working') else '手动更新确认')],
            9: [('wcf', '生成 WCF'), ('confirm_wcf', '手动生成确认'), ('skip_services', '无需 WCF')],
            10: [('confirm_services', '手动部署确认')],
            11: [('verify', '核验数据库'), ('confirm_complete', '确认验收完成')]}
        for number, actions in stage_actions.items():
            enabled = all(stage['status'] in DONE for stage in stages[:number-1])
            if number == 5 and stages[3]['status'] == 'done':
                enabled = True
            stages[number-1]['actions'] = [{'id': action, 'label': label, 'enabled': enabled} for action, label in actions]
            for item in stages[number-1]['actions']:
                if item['id'] == 'publish':
                    item['enabled'] &= publication_info['ready'] and stages[7]['status'] not in DONE
                if item['id'] in ('preflight', 'backup') and stages[7]['status'] in DONE:
                    item['enabled'] = False
                if item['id'] == 'backup':
                    item['enabled'] &= bool(publication_info['plan'] and publication_info['plan'].get('ready_for_publish'))
                if item['id'] == 'wcf':
                    item['enabled'] &= bool(package.get('service_objects', package['objects']))
        stages[9]['detail'] = stages[9]['detail'] or '在运行环境部署 WCF，完成后确认'
    active = next((item for item in reversed(events) if item['status'] == 'running'), None)
    workflow_active = active if active and active['tool'] in STAGES and (active['tool'] in DESIGN_TOOLS or any(item.get('id') == active['id'] for item in relevant)
                      or (package and active.get('package_id') == package['id'] and active['tool'] in STAGES)) else None
    if active:
        if workflow_active:
            stage = STAGES[active['tool']]
            mark(stage, 'running', '正在处理')
        for item in stages:
            for action in item['actions']:
                action['enabled'] = False
    current = next((item['number'] for item in stages if item['status'] not in DONE), len(TITLES))
    if workflow_active:
        current = STAGES[workflow_active['tool']]
    recent_job = next((item for item in reversed(events) if item.get('action') and package
                       and (item.get('package_id') == package['id'] or any(receipt.get('id') == item['id'] for receipt in relevant))), None)
    wcf = next((item['result'] for item in reversed(relevant) if item['tool'] == 'generate_designer_wcf_package'
                and item.get('result') and item['result'].get('fields_verified') and item['result'].get('status') == 'services_generated'), None)
    return {'session_id': session_id, 'stages': stages, 'current': current,
            'completed': sum(item['status'] in DONE for item in stages), 'package': package,
            'requirements': [item.get('display_content', item.get('content', ''))[:10000] for item in users],
            'active': public_job(active), 'last_job': public_job(recent_job), 'error': error,
            'has_design': bool(designs), 'workflow_active': public_job(workflow_active),
            'publication': publication_info,
            'wcf': wcf,
            'design_rows': change_summary['rows'] if package else [],
            'field_expectations': change_summary.get('field_expectations', []) if package else [],
            'updated': time.time(), '_manifests': [path for path, _ in manifests]}


def public_job(item):
    if not item:
        return None
    return {key: item.get(key) for key in ('id', 'action', 'status', 'started', 'updated', 'finished', 'error', 'result')}


async def execute(identity, username, session_id, package, manifests, action, options=None):
    pulse = asyncio.create_task(store.heartbeat(identity))
    try:
        from tools.designer import check_designer_package
        from tools.designer_design import compile_designer_mdb
        from designer.publication import verify_published_design
        from designer.review import sync_file
        check = await check_designer_package(package['manifest_file'])
        if not check['intact']:
            raise ValueError('设计文件或来源已变化，请重新核对')
        if action == 'check':
            result = check
        elif action == 'prepare':
            result = await asyncio.to_thread(review.prepare, package['manifest_file'], True)
        elif action == 'sync':
            result = await asyncio.to_thread(review.synchronize, package['mdb_file'], package['sha256'])
        elif action == 'restore_mdb':
            result = await asyncio.to_thread(review.restore_backup, package['mdb_file'], (options or {}).get('backup_id', ''), package['sha256'])
        else:
            if package.get('review'):
                synced = await asyncio.to_thread(sync_file, package['manifest_file'])
                if not synced['unchanged']:
                    raise ValueError('Designer 文件已变化，请先同步并重新核对')
            if action == 'compile':
                result = await compile_designer_mdb(package['mdb_file'], package['sha256'])
            elif action == 'confirm_publish':
                result = await asyncio.to_thread(publication.record_manual_baseline, package['manifest_file'])
            elif action in ('preflight', 'backup', 'publish', 'wcf'):
                fresh = await asyncio.to_thread(snapshot, username, session_id)
                if not fresh['package'] or fresh['package']['id'] != package['id'] or fresh['error']:
                    raise ValueError('设计已更新，请刷新页面后重试')
                if action == 'preflight':
                    result = await asyncio.to_thread(progress_actions.prepare_plan, package, manifests)
                elif action == 'backup':
                    info = fresh['publication']
                    plan = info['plan']
                    if not plan or not plan.get('ready_for_publish') or plan.get('target') != progress_actions.target() or not progress_actions.recent(plan.get('created_utc')):
                        raise ValueError('请先重新检查发布')
                    progress_actions.load_receipt(plan['plan_file'], plan['plan_sha256'])
                    from designer.publication import backup_target
                    result = await asyncio.to_thread(backup_target)
                elif action == 'publish':
                    result = await asyncio.to_thread(progress_actions.publish, fresh, options or {})
                else:
                    names = progress_actions.wcf_types(package, (options or {}).get('verify_types'))
                    # Manual Designer compilation provides no local artifact. Build
                    # the exact reviewed copy before calling the full WCF generator.
                    compiled = await compile_designer_mdb(package['mdb_file'], package['sha256'])
                    if compiled.get('source_unchanged') is False or digest(Path(package['mdb_file'])) != package['sha256']:
                        raise ValueError('WCF 编译期间设计发生变化，请重新核对')
                    final_fields = await asyncio.to_thread(progress_actions.final_wcf_fields,
                        compiled['compiled_mdb'], compiled['compiled_sha256'], fresh['field_expectations'], names)
                    from tools.designer_design import generate_designer_wcf_package
                    result = await generate_designer_wcf_package(compiled['compiled_mdb'], compiled['compiled_sha256'], names)
                    progress_actions.verify_wcf_fields(result, {**fresh, 'field_expectations': final_fields}, names)
                    result['fields_verified'] = True
            elif action == 'verify':
                from designer.cycle import final_manifest
                final = (await asyncio.to_thread(final_manifest, package['manifest_file']) if package.get('working')
                         else await asyncio.to_thread(progress_actions.combined_manifest, package, manifests))
                checked = [await asyncio.to_thread(verify_published_design, final, False)]
                status = 'verified' if checked and all(item['status'] == 'verified' for item in checked) else ('partial_verification' if checked and all(item['status'] in {'verified', 'partial_verification'} for item in checked) else 'verification_failed')
                result = {'status': status,
                          'checks': [check for item in checked for check in item.get('checks', [])]}
            else:
                raise ValueError('不支持此操作')
        store.finish(identity, result)
    except asyncio.CancelledError:
        store.finish(identity, error='操作已中断，请核对结果后重试', status='interrupted')
        raise
    except Exception as exc:
        store.finish(identity, error=str(exc), status='failed')
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)


def start_action(username, session_id, package_id, action, options=None):
    state = snapshot(username, session_id)
    package = state['package']
    if not package or package['id'] != package_id:
        raise ValueError('设计结果已更新，请刷新页面')
    if state['active']:
        return state['active']
    if action == 'sync':
        if not package.get('review'):
            raise ValueError('请先准备 Designer 文件')
    elif action == 'restore_mdb':
        from designer import working
        if not package.get('working'): raise ValueError('没有可恢复的工作 MDB')
        working.backup_file(package['working']['id'], (options or {}).get('backup_id', ''), 'mdb')
    else:
        allowed = next((item for stage in state['stages'] for item in stage['actions'] if item['id'] == action), None)
        if not allowed or not allowed['enabled']:
            raise ValueError('请先完成前面的步骤')
    confirmations = {'confirm_review': 6, 'confirm_compile': 7, 'confirm_publish': 8, 'confirm_wcf': 9, 'confirm_services': 10,
                     'skip_services': 9, 'confirm_complete': 11}
    if action in confirmations and not (action == 'confirm_publish' and package.get('working')):
        if action == 'confirm_publish':
            from designer import working
            working.mark_published(package['manifest_file'], method='manual')
        if action in ('confirm_wcf', 'skip_services', 'confirm_services'):
            store.clear_from(username, session_id, package_id, confirmations[action]+1)
        store.confirm(username, session_id, package_id, confirmations[action], 'skipped' if action == 'skip_services' else 'confirmed')
        if action == 'skip_services':
            store.confirm(username, session_id, package_id, 10, 'skipped')
        return {'status': 'confirmed'}
    if action == 'publish':
        progress_actions.validate_publish(state, options or {})
    if action == 'wcf':
        progress_actions.wcf_types(package, (options or {}).get('verify_types'))
    tool = {'check': 'check_designer_package', 'prepare': 'prepare_designer_review', 'sync': 'sync_designer_file', 'restore_mdb': 'restore_designer_mdb',
            'confirm_publish': 'confirm_designer_manual_publish',
            'compile': 'compile_designer_mdb', 'verify': 'verify_designer_published_design',
            'preflight': 'prepare_designer_publish_plan', 'backup': 'backup_designer_test_database',
            'publish': 'publish_designer_test_database', 'wcf': 'generate_designer_wcf_package'}[action]
    identity, created = store.begin(username, session_id, tool, {'manifest_file': package['manifest_file']}, package_id=package_id, action=action)
    if created:
        if action == 'wcf':
            store.clear_from(username, session_id, package_id, 9)
        task = asyncio.create_task(execute(identity, username, session_id, package, state['_manifests'], action, options))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return {'id': identity, 'status': 'running'}
