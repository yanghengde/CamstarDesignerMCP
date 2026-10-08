"""Project real tool receipts onto a personal ten-step Designer workflow."""
import ast
import asyncio
import json
from pathlib import Path
import time

from agent.memory import get_user_messages, get_sessions
from designer import review, progress_store as store
from designer.files import source_path
from designer.vendor import digest

TITLES = ['提交需求', '识别设计', '检查现有定义', '生成设计', '核对结果', 'Designer 审核', '编译设计', '发布设计', '生成与部署服务', '完成验收']
DONE = {'done', 'confirmed', 'skipped'}
STAGES = {'generate_designer_cdo_package': 4, 'generate_designer_design_package': 4,
          'check_designer_package': 5, 'prepare_designer_review': 6, 'compile_designer_mdb': 7,
          'publish_designer_test_database': 8, 'verify_designer_published_design': 8,
          'generate_designer_wcf_package': 9}
STAGES['sync_designer_file'] = 6
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
    for message in messages:
        for call in message.get('tool_calls', []):
            try:
                calls[call['id']] = json.loads(call['function']['arguments'])
            except (KeyError, ValueError):
                pass
        if message.get('role') == 'tool':
            content = message.get('content', '')
            receipts.append({'tool': message.get('name', ''), 'arguments': calls.get(message.get('tool_call_id'), {}),
                             'call_id': message.get('tool_call_id', ''), 'result': parse(content),
                             'status': 'failed' if isinstance(content, str) and content.startswith('Error') else 'done',
                             'error': content[:500] if isinstance(content, str) and content.startswith('Error') else ''})
    return receipts


def same_file(a, b):
    return bool(a and b and Path(a).resolve() == Path(b).resolve())


def tasks(username):
    result = []
    for session in reversed(get_sessions(username)):
        messages = get_user_messages(username, session['id'])
        if any(item.get('role') == 'user' for item in messages):
            result.append(session)
    return result


def snapshot(username, session_id):
    messages = get_user_messages(username, session_id)
    receipts = tool_receipts(messages)
    seen = {item['call_id'] for item in receipts if item['call_id']}
    events = store.operations(username, session_id)
    receipts += [event for event in events if not event['call_id'] or event['call_id'] not in seen]
    stages = [{'number': i+1, 'title': title, 'status': 'pending', 'detail': '', 'actions': []} for i, title in enumerate(TITLES)]
    def mark(number, status='done', detail=''):
        stages[number-1].update(status=status, detail=detail)
    users = [item for item in messages if item.get('role') == 'user']
    if users:
        mark(1)
    designs = [item for item in receipts if item['tool'] in ('generate_designer_cdo_package', 'generate_designer_design_package')]
    saved = [item for item in designs if (item.get('result') or {}).get('files', {}).get('manifest.json')] if designs else []
    package, manifests, error = None, [], ''
    if saved:
        path = saved[-1]['result']['files']['manifest.json']
        try:
            package = review.summary(path)
            owned = {str(Path(item['result']['files']['manifest.json']).resolve()): item for item in saved}
            cursor = path
            while cursor and len(manifests) < 100:
                manifest_path, manifest = review.package(cursor)
                if manifest.get('source_sha256') and digest(source_path(manifest['source_file'], '.mdb')) != manifest['source_sha256']:
                    raise ValueError('设计来源已变化，请重新核对')
                if any(str(manifest_path) == item[0] for item in manifests):
                    raise ValueError('设计来源存在循环')
                manifests.append((str(manifest_path), manifest))
                source = Path(manifest.get('source_file', ''))
                parent = str((source.parent / 'manifest.json').resolve())
                cursor = parent if source.name == 'modified.mdb' and parent in owned else ''
            operations = [op for _, manifest in reversed(manifests) for op in manifest.get('operations', [])]
            package['objects'] = list(dict.fromkeys(op.get('owner') or op.get('name') for op in operations if op.get('action') in ('create_cdo', 'add_field')))
            package['field_count'] = len({(op['owner'], op['name']) for op in operations if op.get('action') == 'add_field'})
            mark(2, detail=f"{len(package['objects'])} 个对象 · {package['field_count']} 个字段")
            mark(4)
        except (ValueError, KeyError, OSError) as exc:
            error = str(exc)
            mark(4, 'blocked', '设计文件已变化')
    reads = [item for item in receipts if item['tool'].startswith(('get_designer_', 'list_designer_', 'inspect_designer_', 'analyze_designer_')) and item['status'] == 'done' and item.get('result')]
    if reads:
        mark(3)
    if designs and designs[-1]['status'] in ('failed', 'interrupted'):
        mark(4, 'failed', designs[-1].get('error', '设计未完成'))
    if designs:
        mark(2, detail=stages[1]['detail'])
    acknowledgements = store.confirmations(username, session_id, package['id']) if package else {}
    relevant = []
    if package:
        for item in receipts:
            args = item.get('arguments', {})
            if item.get('package_id') == package['id'] or same_file(args.get('manifest_file'), package['manifest_file']) or same_file(args.get('mdb_file'), package['mdb_file']):
                relevant.append(item)
        for item in relevant:
            result = item.get('result') or {}
            tool = item['tool']
            if tool == 'check_designer_package' and result.get('intact'):
                mark(5, detail='文件与来源已核验')
            elif tool == 'compile_designer_mdb' and result.get('compiled_mdb'):
                mark(7, detail='官方编译完成')
            elif tool == 'publish_designer_test_database' and result.get('status') == 'database_published':
                mark(8, detail='程序发布完成')
            elif tool == 'verify_designer_published_design':
                if result.get('status') == 'verified':
                    mark(8, detail='对象与字段已核验')
                elif result.get('status') == 'verification_failed':
                    mark(8, 'waiting', '发布结果尚未核验通过')
                elif result.get('status') == 'partial_verification':
                    mark(8, 'waiting', '部分变更需 Designer 确认')
            if item['status'] in ('failed', 'interrupted') and tool in STAGES:
                mark(STAGES[tool], 'failed', item.get('error', '操作未完成'))
        for stage, status in acknowledgements.items():
            if stages[stage-1]['status'] != 'done':
                mark(stage, status, '用户确认' if status == 'confirmed' else '无需服务')
        if package.get('review') and stages[5]['status'] == 'pending':
            mark(6, 'waiting', '文件已准备，待 Designer 审核')
        syncs = [item for item in relevant if item.get('action') == 'sync' and item.get('result') and item['result'].get('server_mdb') == (package.get('review') or {}).get('server_mdb')]
        if syncs and syncs[-1]['result'].get('unchanged') is False:
            for number in range(5, 11):
                mark(number, 'blocked', 'Designer 文件已变化，需重新核对')
        if stages[3]['status'] in ('failed', 'blocked'):
            for number in range(5, 11):
                mark(number, 'blocked', '等待设计结果核对')
        stage_actions = {
            5: [('check', '核对结果')], 6: [('prepare', '准备 Designer 文件'), ('confirm_review', '确认已审核')],
            7: [('compile', '编译'), ('confirm_compile', '已在 Designer 编译')],
            8: [('verify', '核验发布'), ('confirm_publish', '已在 Designer 发布')],
            9: [('skip_services', '无需服务'), ('confirm_services', '确认服务已完成')],
            10: [('confirm_complete', '确认验收完成')]}
        for number, actions in stage_actions.items():
            enabled = all(stage['status'] in DONE for stage in stages[:number-1])
            if number == 5 and stages[3]['status'] == 'done':
                enabled = True
            stages[number-1]['actions'] = [{'id': action, 'label': label, 'enabled': enabled} for action, label in actions]
    active = next((item for item in reversed(events) if item['status'] == 'running'), None)
    if active:
        if active['tool'] == '__llm_inference':
            if stages[1]['status'] == 'pending':
                mark(2, 'running', '正在识别需求')
        else:
            stage = STAGES.get(active['tool'], 3)
            mark(stage, 'running', '正在处理')
        for item in stages:
            for action in item['actions']:
                action['enabled'] = False
    current = next((item['number'] for item in stages if item['status'] not in DONE), 10)
    if active and active['tool'] != '__llm_inference':
        current = STAGES.get(active['tool'], 3)
    recent_job = next((item for item in reversed(events) if item.get('action')), None)
    return {'session_id': session_id, 'stages': stages, 'current': current,
            'completed': sum(item['status'] in DONE for item in stages), 'package': package,
            'requirements': [item.get('display_content', item.get('content', ''))[:10000] for item in users],
            'active': public_job(active), 'last_job': public_job(recent_job), 'error': error,
            'design_rows': [{'owner': op['owner'], 'name': op['name'], 'field_type': op.get('field_type', '')}
                            for _, manifest in reversed(manifests) for op in manifest.get('operations', []) if op.get('action') == 'add_field'],
            'updated': time.time(), '_manifests': [path for path, _ in manifests]}


def public_job(item):
    if not item:
        return None
    return {key: item.get(key) for key in ('id', 'action', 'status', 'started', 'updated', 'finished', 'error', 'result')}


async def execute(identity, username, session_id, package, manifests, action):
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
            result = await asyncio.to_thread(sync_file, package['manifest_file'])
        else:
            if package.get('review'):
                synced = await asyncio.to_thread(sync_file, package['manifest_file'])
                if not synced['unchanged']:
                    raise ValueError('Designer 文件已变化，请先同步并重新核对')
            if action == 'compile':
                result = await compile_designer_mdb(package['mdb_file'], package['sha256'])
            elif action == 'verify':
                checked = [await asyncio.to_thread(verify_published_design, manifest, False) for manifest in manifests]
                supported = all(op.get('action') in {'create_cdo', 'add_field', 'create_field_type'}
                                for manifest in manifests for op in json.loads(Path(manifest).read_text(encoding='utf-8')).get('operations', []))
                status = ('verified' if supported else 'partial_verification') if checked and all(item['status'] == 'verified' for item in checked) else 'verification_failed'
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


def start_action(username, session_id, package_id, action):
    state = snapshot(username, session_id)
    package = state['package']
    if not package or package['id'] != package_id:
        raise ValueError('设计结果已更新，请刷新页面')
    if state['active']:
        return state['active']
    if action == 'sync':
        if not package.get('review'):
            raise ValueError('请先准备 Designer 文件')
    else:
        allowed = next((item for stage in state['stages'] for item in stage['actions'] if item['id'] == action), None)
        if not allowed or not allowed['enabled']:
            raise ValueError('请先完成前面的步骤')
    confirmations = {'confirm_review': 6, 'confirm_compile': 7, 'confirm_publish': 8, 'confirm_services': 9,
                     'skip_services': 9, 'confirm_complete': 10}
    if action in confirmations:
        store.confirm(username, session_id, package_id, confirmations[action], 'skipped' if action == 'skip_services' else 'confirmed')
        return {'status': 'confirmed'}
    tool = {'check': 'check_designer_package', 'prepare': 'prepare_designer_review', 'sync': 'sync_designer_file',
            'compile': 'compile_designer_mdb', 'verify': 'verify_designer_published_design'}[action]
    identity, created = store.begin(username, session_id, tool, {'manifest_file': package['manifest_file']}, package_id=package_id, action=action)
    if created:
        task = asyncio.create_task(execute(identity, username, session_id, package, state['_manifests'], action))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return {'id': identity, 'status': 'running'}
