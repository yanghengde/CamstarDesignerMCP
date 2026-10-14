"""Isolated .NET Framework bridge to the installed vendor metadata object model."""
from hashlib import sha256
import html
import json
import os
from pathlib import Path
import shutil
import subprocess

import config
from designer.catalog import bounds, rows
from designer.files import artifact_dir, source_path
from designer.mdb import connect
from designer.metadata import identifier, load_xml, validate

KINDS = (
    "cdo", "field", "field_type", "clf", "function", "event", "query", "table",
    "column", "index", "map", "label", "label_category", "workspace", "data_type",
    "query_type", "clf_type", "sql_type", "db_type", "storage_category",
    "clf_function", "clf_parameter", "query_text", "query_parameter", "index_entry", "function_parameter",
    "event_binding", "feature", "field_map", "cdo_usage",
)
from designer.operations import CREATE_ACTIONS, DESIGN_ACTIONS


def digest(path: Path) -> str:
    result=sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024),b""):
            result.update(block)
    return result.hexdigest()


def assembly() -> Path:
    path = Path(config.DESIGNER_METADATA_ASSEMBLY)
    if not config.DESIGNER_METADATA_ASSEMBLY or not path.is_absolute() or path.suffix.lower() != ".dll" or not path.is_file():
        raise ValueError("请配置 DESIGNER_METADATA_ASSEMBLY 为本机安装的 Camstar.Metadata.dll 绝对路径")
    return path


def run(folder: Path, request: dict) -> dict:
    dll = assembly()
    request_file, result_file = folder / "request.json", folder / "result.json"
    request_file.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    shell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not shell.is_file():
        raise ValueError("官方元数据桥需要 Windows PowerShell 5.1 和匹配位数的 ACE OLEDB 驱动")
    args = [str(shell), "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("vendor_bridge.ps1")),
            "-AssemblyPath", str(dll), "-RequestFile", str(request_file), "-ResultFile", str(result_file)]
    with (folder / "bridge.log").open("wb") as log:
        try:
            process = subprocess.run(args, cwd=folder, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=config.DESIGNER_BRIDGE_TIMEOUT, check=False,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"元数据桥超时；日志：{folder / 'bridge.log'}") from exc
    if process.returncode or not result_file.is_file():
        raise ValueError(f"元数据桥失败，退出码 {process.returncode}；日志：{folder / 'bridge.log'}")
    result = json.loads(result_file.read_text(encoding="utf-8-sig"))
    if not result.get("ok"):
        raise ValueError(f"官方元数据组件拒绝操作：{result.get('error', '')[:3000]}")
    return result["result"]


def query(mdb_file: str, mode: str, kind: str, name: str = "", owner: str = "",
          search: str = "", offset: int = 0, limit: int = 20) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind 必须为 {', '.join(KINDS)}")
    bounds(offset, limit)
    if mode not in {'catalog', 'list', 'get', 'schema', 'impact', 'fields', 'inheritance'}:
        raise ValueError('查询只支持只读元数据模式')
    if mode == 'fields' and kind != 'cdo':
        raise ValueError('完整字段目录必须指定 CDO')
    from designer import working
    from designer import read_cache
    source = working.resolve(mdb_file)
    before = digest(source)
    dll = assembly()  # Fail before creating a copy if the backend is not configured.
    request = {'mode': mode, 'kind': kind, 'name': name, 'owner': owner,
               'search': search, 'offset': offset, 'limit': limit}
    # Load the field collection once; later pages/searches use the same version.
    list_page = mode == 'list'
    shared_search = kind in {'field', 'data_type', 'sql_type', 'db_type', 'storage_category',
                            'cdo_usage', 'query_type', 'clf_type', 'workspace'}
    if list_page:
        if shared_search:
            request.update(search='', offset=0, limit=2147483647)
        else:
            # Bounded read-ahead shares neighbouring pages without serializing
            # the entire SDK graph for a broad CDO/label search.
            start = offset // 300 * 300
            request.update(offset=start, limit=((offset + limit - start + 299) // 300) * 300)

    def page(result):
        if not list_page or 'records' not in result:
            return result
        records = result['records']
        if shared_search:
            records = [row for row in records if search.casefold() in row['name'].casefold()]
        total = len(records) if shared_search else result['total']
        start = offset - request['offset']
        return {**result, 'total': total, 'offset': offset, 'limit': limit,
                'has_more': offset + limit < total, 'records': records[start:start + limit]}

    key = read_cache.identity(source, before, dll, request)
    result = read_cache.load(key)
    if result is None and mode == 'fields':
        detail_request = {**request, 'mode': 'get'}
        detail = read_cache.load(read_cache.identity(source, before, dll, detail_request))
        if detail and isinstance(detail.get('field_catalog'), dict):
            result = {**detail['field_catalog'], **{key: detail[key] for key in
                      ('source_sha256', 'source_file', 'read_only', 'workspace_resolution')}}
    if result is not None:
        if digest(source) != before:
            raise ValueError('查询期间来源 MDB 发生变化，请重试')
        return page(result)
    folder = artifact_dir()
    copy = folder / "read.mdb"
    try:
        shutil.copyfile(source, copy)
        if digest(copy) != before or digest(source) != before:
            raise ValueError("复制过程中来源 MDB 发生变化，请重试")
        result = run(folder, {**request, 'mdb': str(copy)})
        if digest(source) != before:
            raise ValueError('查询期间来源 MDB 发生变化，请重试')
        result = {"source_sha256": before, "source_file": str(source), "read_only": True,
                  "workspace_resolution": "vendor_metadata_model", **result}
        read_cache.save(key, result)
        return page(result)
    finally:
        # The bridge owns an isolated copy. Release it after the process exits;
        # design snapshots and fixed work files are never touched here.
        try:
            copy.unlink(missing_ok=True)
        except OSError:
            pass


def select_workspace(source: Path, workspace: str) -> str:
    with connect(source) as conn:
        workspaces = rows(conn.cursor().execute("SELECT WorkspaceCode, WorkspaceDescription, IsActive FROM Workspace"))
    active = [r for r in workspaces if r["IsActive"] and r["WorkspaceCode"] != "csi"]
    if workspace:
        if not any(r["WorkspaceCode"] == workspace for r in active):
            raise ValueError("指定工作区不是活跃的扩展工作区")
        return workspace
    site = [r for r in active if (r["WorkspaceDescription"] or "").casefold() == "site"]
    if len(site) != 1:
        raise ValueError("无法唯一确定 Site 客户工作区，请从 workspace 目录指定 workspace")
    return site[0]["WorkspaceCode"]


def _validate_type_shape(values: dict, *, required=False) -> None:
    if required or 'data_type' in values:
        value = values.get('data_type')
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError('data_type 必须是实际读取的非空数据类型名称')
    for key in ('precision', 'scale'):
        if key in values and (isinstance(values[key], bool) or not isinstance(values[key], int)
                              or not 0 <= values[key] <= 2147483647):
            raise ValueError(f'{key} 必须为非负整数，不能使用布尔值、小数或字符串')
    if 'precision' in values and 'scale' in values and values['scale'] > values['precision']:
        raise ValueError('scale 不能大于 precision')


def validate_operations(operations: list[dict]) -> None:
    if not isinstance(operations, list) or not operations:
        raise ValueError("operations 必须为非空设计操作列表")
    for op in operations:
        if not isinstance(op, dict) or op.get("action") not in DESIGN_ACTIONS:
            raise ValueError("未知设计操作；请查询 get_designer_capabilities")
        action = op["action"]
        creation_keys = {
            'create_cdo': {'name', 'parent', 'description', 'create_table', 'table_name', 'table_description',
                           'create_revision_base', 'create_maintenance'},
            'create_field_type': {'name', 'data_type', 'max_length', 'precision', 'scale', 'description'},
            'add_field': {'name', 'owner', 'field_type', 'persistent', 'is_list', 'description', 'expected_type'},
        }
        if action in creation_keys:
            unknown = set(op) - creation_keys[action] - {'action'}
            if unknown:
                raise ValueError(f"{action} 包含未知参数：{', '.join(sorted(unknown))}；请检查拼写")
            if 'description' in op and not isinstance(op['description'], str):
                raise ValueError('description 必须是字符串')
        if action in CREATE_ACTIONS - {"create_map", "add_clf_function", "add_query_text", "add_field_map", "bind_event"}:
            identifier(op.get("name", ""))
            if len(op["name"]) > 30:
                raise ValueError("新定义名超过手册规定的 30 字符")
        if action == 'create_cdo':
            if not isinstance(op.get('parent'), str) or not op['parent'].strip():
                raise ValueError('新 CDO 必须指定父对象')
            for key in ('table_name', 'table_description'):
                if key in op and not isinstance(op[key], str):
                    raise ValueError(f'{key} 必须是字符串')
            if 'table_name' in op and op['table_name']:
                identifier(op['table_name'])
                if len(op['table_name']) > 30:
                    raise ValueError('新表名不能超过 30 字符')
            if op.get('create_table') is True:
                if not isinstance(op.get('table_description'), str) or not op['table_description'].strip():
                    raise ValueError('创建表必须提供非空 table_description，避免 Designer 保存时拒绝空表描述')
            elif op.get('table_name') or op.get('table_description'):
                raise ValueError('指定 table_name/table_description 时必须明确 create_table=true')
        if action == 'add_field':
            if any(not isinstance(op.get(key), str) or not op[key].strip() for key in ('owner', 'field_type')):
                raise ValueError('新增字段必须指定 owner 和 field_type')
            if 'expected_type' in op:
                shape = op['expected_type']
                if not isinstance(shape, dict) or set(shape) - {'data_type', 'precision', 'scale'}:
                    raise ValueError('expected_type 只支持 data_type/precision/scale 属性核对')
                _validate_type_shape(shape)
        if action == 'create_field_type':
            _validate_type_shape(op, required=True)
            if op['data_type'] == 'String':
                value = op.get('max_length')
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000000:
                    raise ValueError('String max_length 必须为 1～1000000 的整数')
                if 'precision' in op or 'scale' in op:
                    raise ValueError('String类型仅用max_length指定长度，不能同时指定precision/scale')
            elif 'max_length' in op:
                raise ValueError('max_length 仅用于 String；数值类型请使用 precision/scale')
        for key in ("persistent", "is_list", "create_table", "create_revision_base", "create_maintenance"):
            if key in op and not isinstance(op[key], bool):
                raise ValueError(f"{key} 必须是布尔值")
        required = {
            "copy_clf": ("template",), "create_clf": ("clf_type",),
            "create_query": ("query_type", "db_type_id", "text"), "create_map": ("owner", "target"),
            "add_clf_function": ("owner", "function", "sequence"), "add_column": ("owner", "sql_type_id"),
            "add_query_text": ("owner", "db_type_id", "text"), "delete": ("kind", "name"),
            "change_field_type": ("owner", "name", "field_type", "expected_field_type"),
            "create_index": ("owner", "columns"), "add_field_map": ("map", "source_cdo", "source_field", "target_cdo", "target_field"),
            "reorder_clf_functions": ("owner", "function_ids", "expected_function_ids"),
            "set_clf_parameter": ("owner", "call_id", "parameter", "value", "expected_value"),
            "bind_event": ("owner", "event", "clf", "feature"),
            "change_parent": ("name", "parent", "expected_parent"),
            "change_storage_category": ("name", "category", "expected_category"),
            "replace_event_binding": ("owner", "event", "clf", "feature", "expected_clf"),
            "unbind_event": ("owner", "event", "expected_clf"),
            "replace_clf_function": ("owner", "call_id", "function", "expected_function"),
            "remove_clf_function": ("owner", "call_id", "expected_function"),
            "update_field_map": ("owner", "name", "source_cdo", "source_field", "target_cdo", "target_field", "expected_source_field", "expected_target_field"),
            "remove_field_map": ("owner", "name", "expected_source_field", "expected_target_field"),
            "remove_field_override": ("owner", "name", "expected_field_type"),
            "sync_query_parameters": ("owner",),
        }
        allow_empty={"value","expected_value"} if action=="set_clf_parameter" else set()
        if any(key not in op or op[key] is None or (key not in allow_empty and op[key]=="") for key in required.get(action, ())):
            raise ValueError(f"{action} 缺少必要参数 {required[action]}")
        if action=="create_index" and (not isinstance(op["columns"],list) or not 1<=len(op["columns"])<=16):
            raise ValueError("索引必须指定1～16个已有列")
        if action == "patch":
            if op.get("kind") not in KINDS or not op.get("name") or not isinstance(op.get("changes"), dict) or not op["changes"]:
                raise ValueError("patch 需要 kind/name/changes")
            if not isinstance(op.get("expected"), dict) or not set(op["changes"]).issubset(op["expected"]):
                raise ValueError("每个修改属性都必须提供 expected 原值")
            protected = {'cdousagemask', 'cdousagemaskid', 'parentcdo', 'parentcdoname', 'parentcdoid',
                         'storagecategoryname', 'storagecategoryid'}
            if op['kind'] == 'cdo' and any(key.casefold() in protected for key in op['changes']):
                raise ValueError('对象用途、父对象和存储类别不能通过 patch 修改；使用官方创建或 change_parent/change_storage_category 专用流程')
            for key in ("Name", "FieldName", "FieldDefName"):
                if key in op["changes"]:
                    identifier(op["changes"][key])
                    if len(op['changes'][key]) > 30:
                        raise ValueError('定义名不能超过 30 字符')
        if action in {'replace_clf_function', 'remove_clf_function', 'set_clf_parameter'}:
            if isinstance(op['call_id'], bool) or not isinstance(op['call_id'], int) or op['call_id'] < 1:
                raise ValueError('call_id 必须为实际读取的正整数')


def preflight(mdb_file: str, expected_sha256: str, operations: list[dict], workspace: str = '') -> dict:
    """Run vendor factories on an isolated graph; never save or commit a design."""
    if operations:
        validate_operations(operations)
    elif operations != []:
        raise ValueError('operations 必须为设计操作列表')
    from designer import working, read_cache
    source = working.resolve(mdb_file)
    before = digest(source)
    if before != expected_sha256:
        raise ValueError('来源 MDB SHA256 不匹配，请重新读取设计定义')
    dll = assembly()
    dll_hash = digest(dll)
    bridge = Path(__file__).with_name('VendorBridge.cs')
    bridge_hash = digest(bridge)
    workspace = select_workspace(source, workspace)
    request = {'mode': 'preflight', 'workspace': workspace, 'operations': operations}
    key = read_cache.identity(source, before, dll, request)
    result = read_cache.load(key)
    if result is not None:
        if digest(source) != before or digest(dll) != dll_hash or digest(bridge) != bridge_hash:
            raise ValueError('预检期间来源 MDB 已变化')
        return result
    folder = artifact_dir()
    private = folder / 'read.mdb'
    try:
        shutil.copyfile(source, private)
        if digest(private) != before or digest(source) != before or digest(dll) != dll_hash or digest(bridge) != bridge_hash:
            raise ValueError('预检复制期间来源 MDB 已变化')
        result = run(folder, {**request, 'mdb': str(private)})
        if digest(private) != before or digest(source) != before or digest(dll) != dll_hash or digest(bridge) != bridge_hash:
            raise ValueError('只读预检修改了 MDB 或来源已变化，拒绝使用结果')
        if (result.get('complete') is not True or not isinstance(result.get('conflicts'), list)
                or not isinstance(result.get('ready'), bool) or not isinstance(result.get('objects'), list)):
            raise ValueError('厂商创建预检结果不完整')
        if any(profile.get('complete') is not True or profile.get('total_fields') != len(profile.get('fields', []))
               for profile in result['objects']):
            raise ValueError('厂商基础字段列表不完整，不能用于冲突预检')
        result = {**result, 'source_file': str(source), 'source_sha256': before,
                  'vendor_assembly_sha256': dll_hash, 'bridge_sha256': bridge_hash, 'read_only': True}
        read_cache.save(key, result)
        return result
    finally:
        private.unlink(missing_ok=True)


def preflight_error(result: dict) -> str:
    lines = []
    for conflict in result['conflicts'][:10]:
        label = '.'.join(value for value in (conflict.get('owner'), conflict.get('name')) if value)
        existing = conflict.get('existing')
        detail = ''
        if existing:
            detail = (f"已有字段来自 {existing['origin']}，{existing['field_type']} ({existing['data_type']})，"
                      f"长度/精度 {existing['precision']}，小数位 {existing['scale']}，"
                      f"持久化 {'是' if existing['persistent'] else '否'}，列表 {'是' if existing['is_list'] else '否'}")
            if existing['system_field']:
                detail += '，属于系统用途字段'
            requested = conflict['requested']
            detail += (f"；请求 {requested['field_type']} ({requested['data_type']})，长度/精度 {requested['precision']}，"
                       f"小数位 {requested['scale']}，持久化 {'是' if requested['persistent'] else '否'}，"
                       f"列表 {'是' if requested['is_list'] else '否'}。")
        if conflict.get('existing_type'):
            shape = conflict['existing_type']
            detail = (f"已有字段类型 {shape['field_type']} ({shape['data_type']})，长度/精度 {shape['precision']}，"
                      f"小数位 {shape['scale']}，工作区 {shape['workspace']}。")
            requested = conflict.get('requested_type') or {}
            if requested:
                detail += f"请求 {requested['data_type']}，长度/精度 {requested.get('precision')}，小数位 {requested.get('scale')}。"
        lines.append(f"{label}：{detail}{conflict['message']}")
    return '创建预检未通过，尚未保存 Designer：' + '\n'.join(lines)


def export(folder: Path, baseline: Path, modified: Path) -> dict:
    export_folder = folder / "export"
    export_folder.mkdir()
    run(export_folder, {"mode": "export", "base": str(baseline), "mdb": str(modified)})
    xml = export_folder / "changes.xml"
    root, checksum = load_xml(xml)
    validation = validate(root)
    # HTML renderer belongs to this project, XML comparison belongs to the vendor.
    report = export_folder / "report.html"
    report.write_text("<!doctype html><meta charset='utf-8'><title>Designer metadata diff</title>"
                      "<h1>官方元数据差异 XML</h1><pre>" + html.escape(xml.read_text(encoding="utf-8-sig")) + "</pre>", encoding="utf-8")
    return {"xml_file": str(xml), "report_file": str(report), "source_sha256": checksum, "validation": validation,
            "comparison_engine": "Camstar.Metadata.Comparison.CompareAndExport", "html_renderer": "project"}


def design(mdb_file: str, expected_sha256: str, workspace: str, operations: list[dict]) -> dict:
    from designer import working
    source = working.resolve(mdb_file)
    project_id, _ = working.context(source)
    with working.project_lock(project_id):
        return _design(mdb_file, expected_sha256, workspace, operations)


def _design(mdb_file: str, expected_sha256: str, workspace: str, operations: list[dict]) -> dict:
    validate_operations(operations)
    from designer import working
    source = working.resolve(mdb_file)
    project_id, work_state = working.context(source)
    before = digest(source)
    if before != expected_sha256:
        raise ValueError("来源 MDB SHA256 不匹配，请重新读取设计定义")
    if any(op['action'] in {'create_cdo', 'create_field_type', 'add_field'} for op in operations):
        inspected = preflight(mdb_file, expected_sha256, operations, workspace)
        if not inspected['ready']:
            raise ValueError(preflight_error(inspected))
    if work_state.get('server_mdb'):
        from designer import review
        review.assert_server_unchanged(work_state)
    assembly()
    workspace = select_workspace(source, workspace)
    folder = artifact_dir()
    baseline, modified = folder / "baseline.mdb", folder / "modified.mdb"
    shutil.copyfile(source, baseline)
    if digest(baseline) != before or digest(source) != before:
        raise ValueError("来源 MDB 在复制期间发生变化")
    shutil.copyfile(baseline, modified)
    stage="apply"
    try:
        applied = run(folder, {"mode": "apply", "mdb": str(modified), "workspace": workspace, "operations": operations})
        stage="export"
        exported = export(folder, baseline, modified)
    except Exception as exc:
        failure={"format_version":2,"status":"failed","failed_stage":stage,"ready_for_publish":False,
                 "source_file":str(source),"source_sha256":before,"workspace":workspace,"operations":operations,
                 "error":str(exc),"warning":"失败副本可能包含部分已保存的设计，不能作为成功结果或发布输入。"}
        (folder/"failure.json").write_text(json.dumps(failure,ensure_ascii=False,indent=2),encoding="utf-8")
        raise ValueError(f"设计{stage}阶段失败；记录：{folder/'failure.json'}；{exc}") from exc
    xml_target = folder / "changes.xml"
    shutil.copyfile(exported["xml_file"], xml_target)
    manifest = {
        "format_version": 2, "status": "saved_to_test_copy_and_exported", "ready_for_publish": False,
        "official_xml_import_verified": False, "database_published": False,
        "source_file": str(baseline), "source_sha256": before, "source_suffix": ".mdb",
        "working_project_id": project_id, "input_file": str(source),
        "parent_manifest_file": work_state['latest_manifest'] if not work_state.get('backup_pending') else '',
        "workspace": workspace, "workspace_applied_to_mdb": True, "operations": operations,
        "artifacts": {"baseline.mdb": digest(baseline), "modified.mdb": digest(modified), "changes.xml": digest(xml_target)},
        "vendor_assembly_sha256": digest(assembly()), "validation": exported["validation"],
        "execution": applied,
    }
    manifest_file = folder / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if work_state.get('server_mdb'):
        review.assert_server_unchanged(work_state)
    working_mdb = working.commit(project_id, work_state, manifest_file, before)
    (folder / "report.md").write_text(
        "# Designer 设计结果\n\n已通过官方元数据对象模型更新固定工作 MDB，并导出官方差异 XML。\n\n"
        f"工作 MDB：{working_mdb}\n\n"
        f"工作区：{workspace}\n\n源 SHA256：{before}\n\n"
        "未通过 Designer XML Import 往返验证，未更新业务数据库，未生成运行服务。\n\n"
        "```json\n" + json.dumps(operations, ensure_ascii=False, indent=2) + "\n```\n", encoding="utf-8")
    result = {"status": manifest["status"], "ready_for_publish": False, "workspace": workspace,
            "execution": applied, "export": exported,
            "working_project_id": project_id, "working_mdb": working_mdb,
            "files": {n: str(folder / n) for n in ("baseline.mdb", "modified.mdb", "changes.xml", "manifest.json", "report.md")}}
    result['files']['modified.mdb'] = working_mdb
    # Once handed to Designer, subsequent batches refresh the same server path.
    if work_state.get('server_mdb'):
        from designer import review
        try:
            result['review'] = review.prepare(str(manifest_file), False)
        except ValueError as exc:
            result['designer_sync_error'] = str(exc)
    return result


def adopt_candidate(mdb_file, expected_sha256, candidate, *, method, state_update=None):
    """Accept one verified saved MDB, preserving snapshots and the fixed path."""
    from designer import working
    from designer.metadata import effective
    source = working.resolve(mdb_file)
    project_id, state = working.context(source)
    if not state.get('latest_manifest'):
        raise ValueError('请先建立工作 MDB，再同步或恢复')
    if digest(source) != expected_sha256:
        raise ValueError('工作 MDB 已变化，请刷新后重试')
    previous = json.loads(Path(state['latest_manifest']).read_text(encoding='utf-8'))
    folder = artifact_dir()
    baseline, modified = folder / 'baseline.mdb', folder / 'modified.mdb'
    shutil.copyfile(source, baseline)
    candidate_hash = digest(candidate)
    shutil.copyfile(candidate, modified)
    if digest(baseline) != expected_sha256 or digest(modified) != candidate_hash or digest(candidate) != candidate_hash:
        raise ValueError('文件在同步期间发生变化，请保存后重试')
    run(folder, {'mode': 'catalog', 'mdb': str(modified), 'workspace': previous['workspace']})
    exported = export(folder, baseline, modified)
    shutil.copyfile(exported['xml_file'], folder / 'changes.xml')
    root, _ = load_xml(folder / 'changes.xml')
    operations = []
    tags = {'CDODefinition': 'cdo', 'CDOFieldDefinition': 'field', 'FieldDefinition': 'field_type',
            'CLFDefinition': 'clf', 'QueryDefinition': 'query', 'DBColumnDefinition': 'column',
            'DBIndexDefinition': 'index', 'CDOMapDefinition': 'map', 'LabelDefinition': 'label'}
    def visit(node, owner=''):
        target_kind = tags.get(node.tag)
        name = node.get('Name')
        if target_kind and name:
            changed = effective(node)
            op = {'action': 'delete' if node.get('Action') == 'Delete' else 'external_edit',
                  'kind': target_kind, 'name': name, 'owner': owner,
                  'origin': method, 'changes': {item.tag: item.text or '' for item in changed.findall('Attributes/*') if len(item) == 0}}
            operations.append(op)
            if target_kind == 'cdo': owner = name
        for child in node:
            if child.tag != 'Attributes': visit(child, owner)
    visit(root.find('Import'))
    if not operations: operations = [{'action': method, 'name': '当前工作 MDB'}]
    manifest = {**previous, 'format_version': 2, 'status': 'saved_to_test_copy_and_exported',
                'source_file': str(baseline), 'source_sha256': expected_sha256,
                'input_file': str(source), 'parent_manifest_file': state['latest_manifest'] if not state.get('backup_pending') else '',
                'operations': operations, 'origin': method, 'database_published': False, 'ready_for_publish': False,
                'artifacts': {name: digest(folder / name) for name in ('baseline.mdb', 'modified.mdb', 'changes.xml')},
                'validation': exported['validation'], 'execution': {'status': 'verified_saved_mdb', 'method': method}}
    manifest.pop('owned_source_manifests', None)
    path = folder / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if state.get('server_mdb'):
        from designer import review
        review.assert_server_unchanged({**state, **(state_update or {})})
    fixed = working.commit(project_id, {**state, **(state_update or {})}, path, expected_sha256)
    return {'status': manifest['status'], 'working_project_id': project_id, 'working_mdb': fixed,
            'files': {'manifest.json': str(path), 'modified.mdb': fixed, 'changes.xml': str(folder / 'changes.xml')},
            'source': method, 'database_published': False}
