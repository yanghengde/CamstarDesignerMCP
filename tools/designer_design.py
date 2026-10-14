"""Designer object-model tools backed by the locally installed vendor assembly."""
import asyncio
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

import config
from designer import vendor
from designer.files import artifact_dir, source_path
from tools import mcp


class DesignOperation(BaseModel):
    model_config = ConfigDict(extra='allow')
    action: str = Field(description='设计动作，必须使用 action 字段',
                        json_schema_extra={'enum': sorted(vendor.DESIGN_ACTIONS)})

    @field_validator('action')
    @classmethod
    def known_action(cls, value):
        if value not in vendor.DESIGN_ACTIONS:
            raise ValueError('未知 action，请读取 get_designer_capabilities')
        return value


@mcp.tool
async def prepare_designer_review(manifest_file: str, activate: bool = False) -> dict:
    """把最新设计更新到配置服务器的固定项目工作 MDB，附带 SiteInfo；后续批次使用相同路径。

    activate=true 将 Designer 配置指向固定工作文件并备份旧配置；用户明确要求在 Designer 中查看时可使用。
    已打开的 Designer 需先保存并关闭，再重新打开才能看到；不宣称已打开，不执行编译、发布或服务生成。
    """
    from designer.review import prepare
    return await asyncio.to_thread(prepare, manifest_file, activate)


@mcp.tool
async def inspect_designer_installation() -> dict:
    """通过已配置的Windows共享只读检查Designer界面、原生XML Import程序、组件版本及MDB路径；不启动界面或导入，不返回密码。"""
    from designer.installation import inspect_installation
    return await asyncio.to_thread(inspect_installation)


@mcp.tool
async def get_designer_capabilities() -> dict:
    """返回 Designer 各设计域和真实后端能力；设计副本保存与 XML 导入/数据库发布分开。"""
    configured = bool(config.DESIGNER_METADATA_ASSEMBLY)
    available = configured and Path(config.DESIGNER_METADATA_ASSEMBLY).is_file()
    return {"entity_kinds": vendor.KINDS, "vendor_backend_available": available,
            "design_operations": sorted(vendor.DESIGN_ACTIONS),
            "features": {"effective_metadata_read": available, "workspace_and_inheritance": available,
                         "create_cdo": available, "create_field_type": available, "add_field": available,
                         "creation_preflight_and_inherited_field_conflicts": available,
                         "persistent_field_mapping": available, "property_patch_with_expected_values": available,
                         "official_diff_export": available, "xml_import": False,
                         "native_xml_import_executable_configured": bool(config.DESIGNER_SERVER_IMPORT_EXE),
                         "database_publish": available and config.DESIGNER_TEST_TARGET_CONFIRMED and bool(config.DESIGNER_DB_SERVER),
                         "database_backup_and_restore": config.DESIGNER_TEST_TARGET_CONFIRMED and bool(config.DESIGNER_DB_SERVER),
                         "published_cdo_field_column_verification": bool(config.DESIGNER_DB_SERVER),
                         "published_metadata_update_delete_verification": available and bool(config.DESIGNER_DB_SERVER),
                         "designer_saved_file_receive": available, "mdb_backup_restore": available,
                         "change_customer_cdo_parent_same_usage": available, "change_storage_category": available,
                         "remove_inherited_field_override": available, "replace_unbind_events": available,
                         "replace_remove_clf_calls": available, "update_remove_field_maps": available,
                         "query_parameter_synchronization": available,
                         "complete_project_cycle_publication": available,
                         "verified_manual_publish_checkpoint": available and bool(config.DESIGNER_DB_SERVER),
                         "content_versioned_metadata_read_cache": available,
                         "metadata_compile": available,
                         "wcf_generation_adapter_implemented": True,
                         "wcf_server_configured": bool(config.DESIGNER_SERVER_SHARE and config.DESIGNER_WINDOWS_USER and config.DESIGNER_WCF_ADDRESS),
                         "runtime_service_generation": False},
            "scope": "stable_project_mdb_and_confirmed_test_database", "vendor_api_stability": "installed_assembly_version_requires_regression_tests"}


@mcp.tool
async def sync_designer_working_file(mdb_file: str, expected_sha256: str, accept_merged_sha256: str = '') -> dict:
    """读取 Designer 已保存的工作 MDB，验证后接收到同一个本地工作路径，生成新清单以便继续设计。两边同时变化时保留两个文件，由 Opcenter 合并；用户明确确认已在 Opcenter 合并后，才可传入拒绝回执中的合并文件 SHA256 为 accept_merged_sha256，接收指定版本。不合并、不发布数据库。"""
    from designer.review import synchronize
    return await asyncio.to_thread(synchronize, mdb_file, expected_sha256, accept_merged_sha256)


@mcp.tool
async def restore_designer_mdb_backup(mdb_file: str, backup_id: str, expected_sha256: str) -> dict:
    """恢复指定项目备份到同一个工作 MDB；先保留当前版本，再验证备份与当前哈希。用户明确要求恢复时使用。只恢复 MDB，不回退 SQL 数据库或 SiteInfo；返回新的设计清单。"""
    from designer.review import restore_backup
    return await asyncio.to_thread(restore_backup, mdb_file, backup_id, expected_sha256)


@mcp.tool
async def generate_designer_wcf_package(compiled_mdb: str, expected_sha256: str, verify_types: list[str], service_names: list[str] | None = None) -> dict:
    """在已配置的测试服务器隔离生成官方WCF程序集，核对非零计数与指定类型；不部署运行目录。需要Windows共享凭据与SQL Agent。省略service_names为全量，指定时仅生成部分服务，不能替代完整运行包。当前安装版本适配仍在验收，失败返回日志位置，不能据作业退出码宣称成功。"""
    from designer.services import generate_wcf
    return await asyncio.to_thread(generate_wcf,compiled_mdb,expected_sha256,verify_types,service_names)


@mcp.tool
async def verify_designer_published_design(manifest_file: str, test_string_boundaries: bool = False) -> dict:
    """编译隔离副本，对照 SQL 核对对象、字段、CLF/调用/参数、事件、查询/文本/参数、映射、列、索引及标签的具体值与删除结果，并检查物理列。工作 MDB 自动汇总本轮全部设计，传入最新设计清单即可。可在已确认测试库临时表测试String长度边界，不写业务行；不代替CLF、Query或WCF运行行为验收。"""
    from designer.publication import verify_published_design
    from designer.cycle import final_manifest
    def execute():
        return verify_published_design(final_manifest(manifest_file), test_string_boundaries)
    return await asyncio.to_thread(execute)


@mcp.tool
async def get_designer_catalog(mdb_file: str) -> dict:
    """一次读取官方模型各类有效定义数量及完整工作区配置，避免反复猜测工作区和内部表名。"""
    return await asyncio.to_thread(vendor.query, mdb_file, "catalog", "cdo")


@mcp.tool
async def inspect_designer_database(table: str = "") -> dict:
    """只读检查本机配置的Designer发布目标数据库与实际表列；不执行用户SQL，不返回凭据或业务实例。"""
    from designer.publication import inspect_target
    return await asyncio.to_thread(inspect_target, table)


@mcp.tool
async def restore_designer_test_database(backup_receipt: str, expected_receipt_sha256: str) -> dict:
    """恢复本机已确认测试库到指定已校验备份；会断开该测试库连接并覆盖备份后的变更。必须明确要求回退，并提供匹配的备份凭证及SHA256；只作用于本机配置目标。"""
    from designer.publication import restore_test_backup
    return await asyncio.to_thread(restore_test_backup,backup_receipt,expected_receipt_sha256)


@mcp.tool
async def publish_designer_test_database(manifest_file: str, expected_manifest_sha256: str, backup_receipt: str = '', siteinfo_mdb: str = '') -> dict:
    """将已核对的官方设计包编译并通过厂商Update DB发布到本机已确认测试库；必须提供清单SHA256和siteinfo_mdb路径。测试环境默认无需数据库备份，backup_receipt可省略；仅配置DESIGNER_REQUIRE_DATABASE_BACKUP=true时要求一小时内校验备份凭证。更新设计元数据及存储结构，不更新服务器/用户配置，不部署服务。失败可能有部分数据库更改，返回真实审计记录。"""
    from designer.publication import publish_database
    def execute():
        if vendor.digest(source_path(manifest_file, '.json')) != expected_manifest_sha256:
            raise ValueError('设计包清单SHA256不匹配')
        from designer.cycle import prepare_plan
        plan = prepare_plan(manifest_file)
        if not plan['ready_for_publish']:
            raise ValueError('发布预检未通过：' + '；'.join(plan['blockers']))
        return publish_database(plan['manifest_file'], plan['manifest_sha256'], backup_receipt, siteinfo_mdb,
                                expected_target_fingerprint=plan['target_fingerprint'])
    return await asyncio.to_thread(execute)


@mcp.tool
async def backup_designer_test_database() -> dict:
    """为本机已确认的Designer测试数据库创建唯一COPY_ONLY备份并执行RESTORE VERIFYONLY；返回服务器备份位置与本地凭证，不修改业务数据。"""
    from designer.publication import backup_target
    return await asyncio.to_thread(backup_target)


@mcp.tool
async def prepare_designer_publish_plan(manifest_file: str) -> dict:
    """使用当前项目完整设计周期的最终清单，校验来源漂移、已发布基线和目标列冲突，并记录目标指纹；不执行Update DB或服务部署。"""
    from designer.cycle import prepare_plan
    return await asyncio.to_thread(prepare_plan, manifest_file)


@mcp.tool
async def list_designer_entities(mdb_file: str, kind: str, search: str = "", owner: str = "", offset: int = 0, limit: int = 20) -> dict:
    """经官方元数据模型读取有效定义，包括 CDO/field/field_type/CLF/function/event/query/table/column/index/map/label/workspace。

    kind 使用 get_designer_capabilities 的 entity_kinds。field 必须指定 owner CDO；支持 offset/limit 分页，limit 为正整数，无固定条数上限，返回来源SHA256。用户要字段清单时优先 get_designer_fields，一次返回完整精简表格，不逐字段补查。
    """
    return await asyncio.to_thread(vendor.query, mdb_file, "list", kind, "", owner, search, offset, limit)


@mcp.tool
async def get_designer_inheritance(mdb_file: str, cdo_name: str) -> dict:
    """一次读取某 CDO 的全部派生/继承模型，返回名称、直接父对象与继承深度。depth=1 为直接子类。

    查询“Product 下有哪些继承模型/子类”时使用本工具，不扫描字段、不查 schema 或 where-used。
    基于官方有效元数据，含完整总数、来源 SHA256；不修改工作文件。
    """
    return await asyncio.to_thread(vendor.query, mdb_file, 'inheritance', 'cdo', cdo_name)


@mcp.tool
async def get_designer_fields(mdb_file: str, cdo_name: str) -> dict:
    """只读一次获取 CDO 的全部有效字段（含继承），用于完整列表展示。无需分页或逐字段查询。

    返回 columns/rows 表格：字段名、真实字段类型、数据类型、长度/精度、小数位、持久化、列表、来源对象、描述，含总数与来源 SHA256。
    浏览器聊天会直接显示完整表格；返回 displayed_as_table=true 时简短说明总数即可，不重复生成表格或要求用户输入继续。
    """
    return await asyncio.to_thread(vendor.query, mdb_file, 'fields', 'cdo', cdo_name)


@mcp.tool
async def get_designer_entity(mdb_file: str, kind: str, name: str, owner: str = "") -> dict:
    """读取官方模型中的有效定义和属性；CDO含全部有效字段（包括继承字段），CLF含有序函数，Query含SQL与参数。field必须指定owner。"""
    return await asyncio.to_thread(vendor.query, mdb_file, "get", kind, name, owner)


@mcp.tool
async def get_designer_entity_schema(mdb_file: str, kind: str, name: str, owner: str = "") -> dict:
    """读取安装版本的真实对象属性、枚举和可编辑标记，供patch设计使用；禁止猜测属性名或类型。"""
    return await asyncio.to_thread(vendor.query, mdb_file, "schema", kind, name, owner)


@mcp.tool
async def inspect_designer_creation_plan(mdb_file: str, expected_sha256: str,
                                          operations: list[DesignOperation], workspace: str = '') -> dict:
    """只读预检对象创建计划：使用厂商工厂在隔离内存中推演，不保存 MDB 或发布数据库。

    operations=[] 时读取常用类别、真实父对象及基础字段数量。传入完整有序操作时，返回完整基础字段、系统字段、派生对象/表、同批及继承重名冲突。
    ready=false 时先向用户说明冲突；不能静默跳过、改名或覆盖。类型/长度相符仍需明确复用并移除新增操作。
    """
    operations = [op.model_dump(exclude_unset=True) if isinstance(op, DesignOperation) else op for op in operations]
    return await asyncio.to_thread(vendor.preflight, mdb_file, expected_sha256, operations, workspace)


@mcp.tool
async def analyze_designer_where_used(mdb_file: str, kind: str, name: str, owner: str = "") -> dict:
    """调用厂商GetWhereUsed追踪定义引用者，最多100条；没有该方法的设计类型会明确返回错误。"""
    return await asyncio.to_thread(vendor.query, mdb_file, "impact", kind, name, owner)


@mcp.tool
async def generate_designer_design_package(mdb_file: str, expected_sha256: str, operations: list[DesignOperation], workspace: str = "") -> dict:
    """执行官方设计操作、重新加载并导出真实差异XML，成功后更新同一个固定工作MDB；不发布数据库。

    首次设计从指定来源建立项目工作文件，后续来源路径自动指向该工作文件，必须用最新读取的哈希。
    发布后的下一轮首次修改前自动备份上一版，最多保留10个版本；后续批次不重复备份。
    files['modified.mdb']与working_mdb返回固定工作路径；清单与核验快照单独留存。

    operations（非空列表，顺序执行，无固定条数上限，每项必须有 action，不使用 op 或操作名作包装键）：
    示例：[{"action":"create_cdo","name":"ExExample","parent":"NamedDataObject","create_table":true,"table_name":"ExExample","table_description":"示例主表"}]
    create_cdo: name,parent,description,create_table,table_name,table_description,create_revision_base,create_maintenance。建表时必须提供非空 table_description。
    create_field_type: name,data_type,String需max_length；数值可precision/scale。
    add_field: name,owner,field_type,persistent(默认false),is_list(默认false),description；expected_type可核对data_type/precision/scale，引用不符拒绝创建。创建参数未知键会拒绝，不能使用拼错的属性。
    copy_clf: name,template；create_query: name,query_type,db_type_id,text,description。
    create_map: owner源CDO,target目标CDO，由官方生成映射名称。
    create_clf: name,clf_type；add_clf_function: owner,function,sequence。
    create_label: name,text,category_id；add_column: name,owner表,sql_type_id,precision,scale。
    create_index: name,owner表,columns列名列表,is_unique。
    add_query_text: owner查询,db_type_id,text；add_field_map: map,source_cdo,source_field,target_cdo,target_field。
    change_field_type: owner对象,name字段,field_type,expected_field_type原类型。
    reorder_clf_functions: owner,function_ids,expected_function_ids，必须完整且不重复。
    set_clf_parameter: owner,call_id,parameter,value,expected_value，设置函数调用参数表达式。
    bind_event: owner客户CDO,event,clf,feature；可选field为客户字段；拒绝覆盖已有事件绑定。
    change_parent: name,parent,expected_parent；仅客户拥有且无下层覆盖的 CDO，同一 CDO 用途类别，拒绝继承环和字段冲突。
    change_storage_category: name,category,expected_category；使用已有存储分类改变元数据归属，不搬移文件或迁移工作区。
    remove_field_override: owner,name,expected_field_type；撤销客户字段覆盖并恢复继承，不能当作删除父级字段。
    replace_event_binding: owner,event,clf,feature,expected_clf,field?；unbind_event: owner,event,expected_clf,field?。替换后参数取新 CLF 的默认值。
    replace_clf_function: owner,call_id,function,expected_function；remove_clf_function: owner,call_id,expected_function。读取真实调用 ID；替换使用新函数默认参数。
    update_field_map: owner,name,source_cdo,source_field,target_cdo,target_field,expected_source_field,expected_target_field；remove_field_map: owner,name,expected_source_field,expected_target_field。owner 为映射名，name 可用 id:真实CDOFieldMapID 消除同名歧义。
    sync_query_parameters: owner；query_text 的 Text 属性 patch 也自动同步查询参数。
    patch: kind,name,owner,changes属性字典,expected原值字典。属性先查schema与entity。
    patch只支持schema标记可编辑的简单属性；引用必须使用专用操作，枚举须取schema给出的值。
    delete: kind,name,owner，仅允许删除当前客户工作区拥有且厂商判定未被引用的定义。
    workspace为空时仅自动选择唯一描述为Site的活跃客户工作区，否则需要明确指定。
    """
    payload = [op.model_dump(exclude_unset=True) if isinstance(op, DesignOperation) else op for op in operations]
    return await asyncio.to_thread(vendor.design, mdb_file, expected_sha256, workspace, payload)


@mcp.tool
async def generate_designer_cdo_package(
    mdb_file: str, expected_sha256: str, cdo_name: str, parent_cdo: str,
    fields: list[dict], workspace: str = "", description: str = "", create_table: bool = False,
    table_name: str = '', table_description: str = '', create_revision_base: bool = False, create_maintenance: bool = False,
) -> dict:
    """创建继承CDO和字段并更新固定工作MDB，生成官方XML，不需要现有XML模板。例如ExProduct继承Product，ExDescription为String/max_length=200。

    fields为非空列表，无固定字段数上限，每项{name,data_type,max_length,precision,scale,field_type,persistent,is_list,description}。
    String必须指定max_length；省略field_type时创建对象名前缀的专用类型，长名称带稳定摘要且不超过30字符，避免跨对象重名。
    使用已有field_type时只引用它，不能同时指定max_length；data_type/precision/scale若填写则作为预期属性逐项核对，不一致拒绝创建。
    persistent默认true，厂商创建存储列；可明确指定false。字段名称大小写重复、未知参数和非整数精度均在调用后端前拒绝。
    create_table/table_name/table_description、create_revision_base、create_maintenance 明确控制厂商派生结构，默认均不启用。
    create_table=true必须提供非空table_description。创建前执行官方只读预检，继承字段或派生名称冲突会拒绝保存。
    """
    from designer.design_contract import cdo_operations
    operations = cdo_operations(cdo_name, parent_cdo, fields, description, create_table=create_table,
        table_name=table_name, table_description=table_description, create_revision_base=create_revision_base,
        create_maintenance=create_maintenance)
    return await asyncio.to_thread(vendor.design, mdb_file, expected_sha256, workspace, operations)


@mcp.tool
async def export_designer_vendor_diff(base_mdb: str, modified_mdb: str) -> dict:
    """使用安装的Camstar.Metadata官方比较引擎在副本上导出差异XML；不依赖独立MetadataExport.exe。HTML由本项目渲染。"""
    import shutil
    base, modified = source_path(base_mdb, ".mdb"), source_path(modified_mdb, ".mdb")
    if base == modified:
        raise ValueError("基线与修改MDB不能相同")
    vendor.assembly()
    folder = artifact_dir()
    left, right = folder / "baseline.mdb", folder / "modified.mdb"
    def execute():
        before = [vendor.digest(base), vendor.digest(modified)]
        shutil.copyfile(base, left)
        shutil.copyfile(modified, right)
        if before != [vendor.digest(left), vendor.digest(right)] or before != [vendor.digest(base), vendor.digest(modified)]:
            raise ValueError("导出输入在复制期间变化")
        return vendor.export(folder, left, right)
    return await asyncio.to_thread(execute)


@mcp.tool
async def compile_designer_mdb(mdb_file: str, expected_sha256: str) -> dict:
    """通过官方MetadataCompile编译工作区与字段继承，生成独立compiled.mdb；不更新服务器或业务数据库。"""
    import shutil
    source=source_path(mdb_file,".mdb")
    if vendor.digest(source)!=expected_sha256:
        raise ValueError("来源MDB SHA256不匹配")
    vendor.assembly()
    folder=artifact_dir()
    def execute():
        copy=folder/"source.mdb"
        shutil.copyfile(source,copy)
        if vendor.digest(copy)!=expected_sha256 or vendor.digest(source)!=expected_sha256:
            raise ValueError("编译输入在复制期间变化")
        result=vendor.run(folder,{"mode":"compile","mdb":str(copy)})
        result["compiled_sha256"]=vendor.digest(folder/"compiled.mdb")
        result["source_unchanged"]=vendor.digest(source)==expected_sha256
        result["database_published"]=False
        return result
    return await asyncio.to_thread(execute)
