"""Designer object-model tools backed by the locally installed vendor assembly."""
import asyncio
from pathlib import Path

import config
from designer import vendor
from designer.files import artifact_dir, source_path
from tools import mcp


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
    """编译隔离副本，对照 SQL 核对本次变更的对象、字段、CLF/调用/参数、事件、查询/文本/参数、映射、列、索引及标签元数据的具体值与删除结果，并检查物理列。可在已确认测试库临时表测试String长度边界，不写业务行；不代替CLF、Query或WCF运行行为验收。连续设计须使用合并后的最终设计清单。"""
    from designer.publication import verify_published_design
    return await asyncio.to_thread(verify_published_design,manifest_file,test_string_boundaries)


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
    return await asyncio.to_thread(publish_database,manifest_file,expected_manifest_sha256,backup_receipt,siteinfo_mdb)


@mcp.tool
async def backup_designer_test_database() -> dict:
    """为本机已确认的Designer测试数据库创建唯一COPY_ONLY备份并执行RESTORE VERIFYONLY；返回服务器备份位置与本地凭证，不修改业务数据。"""
    from designer.publication import backup_target
    return await asyncio.to_thread(backup_target)


@mcp.tool
async def prepare_designer_publish_plan(manifest_file: str) -> dict:
    """校验官方设计包、来源漂移和目标列冲突，生成具体发布前检查文件；不执行Update DB或服务部署。"""
    from designer.publication import preflight
    return await asyncio.to_thread(preflight, manifest_file)


@mcp.tool
async def list_designer_entities(mdb_file: str, kind: str, search: str = "", owner: str = "", offset: int = 0, limit: int = 20) -> dict:
    """经官方元数据模型读取有效定义，包括 CDO/field/field_type/CLF/function/event/query/table/column/index/map/label/workspace。

    kind 使用 get_designer_capabilities 的 entity_kinds。field 必须指定 owner CDO；最多100条，返回来源SHA256。
    """
    return await asyncio.to_thread(vendor.query, mdb_file, "list", kind, "", owner, search, offset, limit)


@mcp.tool
async def get_designer_entity(mdb_file: str, kind: str, name: str, owner: str = "") -> dict:
    """读取官方模型中的有效定义和属性；CDO含继承字段，CLF含有序函数，Query含SQL与参数。field必须指定owner。"""
    return await asyncio.to_thread(vendor.query, mdb_file, "get", kind, name, owner)


@mcp.tool
async def get_designer_entity_schema(mdb_file: str, kind: str, name: str, owner: str = "") -> dict:
    """读取安装版本的真实对象属性、枚举和可编辑标记，供patch设计使用；禁止猜测属性名或类型。"""
    return await asyncio.to_thread(vendor.query, mdb_file, "schema", kind, name, owner)


@mcp.tool
async def analyze_designer_where_used(mdb_file: str, kind: str, name: str, owner: str = "") -> dict:
    """调用厂商GetWhereUsed追踪定义引用者，最多100条；没有该方法的设计类型会明确返回错误。"""
    return await asyncio.to_thread(vendor.query, mdb_file, "impact", kind, name, owner)


@mcp.tool
async def generate_designer_design_package(mdb_file: str, expected_sha256: str, operations: list[dict], workspace: str = "") -> dict:
    """执行官方设计操作、重新加载并导出真实差异XML，成功后更新同一个固定工作MDB；不发布数据库。

    首次设计从指定来源建立项目工作文件，后续来源路径自动指向该工作文件，必须用最新读取的哈希。
    发布后的下一轮首次修改前自动备份上一版，最多保留10个版本；后续批次不重复备份。
    files['modified.mdb']与working_mdb返回固定工作路径；清单与核验快照单独留存。

    operations（顺序执行，最多50条）：
    create_cdo: name,parent,description,create_table,table_name,create_revision_base,create_maintenance。
    create_field_type: name,data_type,String需max_length；数值可precision/scale。
    add_field: name,owner,field_type,persistent(默认false),is_list(默认false),description。
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
    return await asyncio.to_thread(vendor.design, mdb_file, expected_sha256, workspace, operations)


@mcp.tool
async def generate_designer_cdo_package(
    mdb_file: str, expected_sha256: str, cdo_name: str, parent_cdo: str,
    fields: list[dict], workspace: str = "", description: str = "",
) -> dict:
    """创建继承CDO和字段并更新固定工作MDB，生成官方XML，不需要现有XML模板。例如ExProduct继承Product，ExDescription为String/max_length=200。

    fields每项{name,data_type,max_length,field_type,persistent,is_list,description}。
    String必须指定max_length；省略field_type时创建专用类型，避免改变共享类型。
    使用已有field_type时只引用它，不能同时更改长度。persistent默认true，厂商创建存储列；可明确指定false。
    """
    operations = [{"action": "create_cdo", "name": cdo_name, "parent": parent_cdo, "description": description}]
    if not isinstance(fields, list) or not 1 <= len(fields) <= 20:
        raise ValueError("fields 必须为1～20个字段定义")
    for field in fields:
        if not isinstance(field, dict) or not field.get("name"):
            raise ValueError("每个字段需要name")
        name = field["name"]
        field_type = field.get("field_type")
        if field_type and "max_length" in field:
            raise ValueError("使用现有field_type时不能同时指定max_length；请创建专用类型")
        if not field_type:
            if not field.get("data_type"):
                raise ValueError("每个字段需要data_type或现有field_type")
            field_type = name + (str(field["max_length"]) if field.get("data_type") == "String" and "max_length" in field else "Type")
            type_op = {"action": "create_field_type", "name": field_type, "data_type": field["data_type"]}
            for key in ("max_length", "precision", "scale"):
                if key in field:
                    type_op[key] = field[key]
            operations.append(type_op)
        operations.append({"action": "add_field", "name": name, "owner": cdo_name, "field_type": field_type,
                           "persistent": field.get("persistent", True), "is_list": field.get("is_list", False),
                           "description": field.get("description", "")})
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
