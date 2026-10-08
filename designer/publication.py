"""Target inspection, validated publication plans, and recoverable test backups."""
from contextlib import contextmanager
import json
from datetime import datetime, timezone
import os
import subprocess
import shutil
from pathlib import Path

import config
from designer.files import artifact_dir, source_path, root_dir
from designer.metadata import load_xml
from designer import vendor


def odbc_value(value: str) -> str:
    if any(c in value for c in "\r\n\x00"):
        raise ValueError("数据库连接配置含非法字符")
    return "{" + value.replace("}", "}}") + "}"


def sql_identifier(value: str) -> str:
    if not value or any(c in value for c in "\r\n\x00"):
        raise ValueError("无效SQL标识符")
    return "["+value.replace("]","]]")+"]"


def target_schema(cursor) -> str:
    schemas=[r[0] for r in cursor.execute("SELECT TABLE_SCHEMA FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME IN ('CDODefinition','SiteState','Product') GROUP BY TABLE_SCHEMA HAVING COUNT(DISTINCT TABLE_NAME)=3").fetchall()]
    if len(schemas)!=1:
        raise ValueError("不能唯一确定Designer元数据与Product所属schema，请核对数据库目标")
    return schemas[0]


def redact(value: str) -> str:
    for secret in (config.DESIGNER_DB_PASSWORD, config.DESIGNER_UPDATE_DB_PASSWORD,
                   getattr(config,"DESIGNER_WINDOWS_PASSWORD","")):
        if secret: value=value.replace(secret,"[REDACTED]")
    return value


def metadata_fingerprint(cursor, schema: str) -> str:
    """Fingerprint the runtime design catalog, excluding business instances."""
    from hashlib import sha256
    result=sha256()
    available={r[0] for r in cursor.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=? AND TABLE_TYPE='BASE TABLE'",schema).fetchall()}
    tables=("CDODefinition","CDOFields","FieldDefinitions","CLFDefinitions","CLFFunctions","CLFParameters",
            "QueryDefs","QueryTexts","QueryParameters","DBTables","DBColumns","DBIndexDefinition","DBIndexEntries",
            "CDOMapDefinition","CDOFieldMapDefinition","CLFEventMaps","Labels","FunctionDefinitions","FunctionParameters","Workspace","FeatureDefinitions")
    for table in tables:
        if table not in available: continue
        cursor.execute(f"SELECT * FROM {sql_identifier(schema)}.{sql_identifier(table)}")
        result.update(table.encode())
        encoded=[]
        while True:
            rows=cursor.fetchmany(1000)
            if not rows: break
            for row in rows:
                encoded.append(json.dumps(list(row),ensure_ascii=False,default=str,separators=(",",":")).encode("utf-8"))
        for row in sorted(encoded): result.update(row); result.update(b"\n")
    columns=cursor.execute("SELECT TABLE_NAME,COLUMN_NAME,DATA_TYPE,CHARACTER_MAXIMUM_LENGTH,NUMERIC_PRECISION,NUMERIC_SCALE,IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? ORDER BY TABLE_NAME,ORDINAL_POSITION",schema).fetchall()
    for row in columns: result.update(json.dumps(list(row),default=str,separators=(",",":")).encode("utf-8"))
    return result.hexdigest()


def record_published_baseline(manifest_path: Path, report: dict, cursor, schema: str) -> dict:
    modified=source_path(str(manifest_path.parent/"modified.mdb"),".mdb")
    record={"status":"verified","target":report["target"],"mdb_file":str(modified),"sha256":vendor.digest(modified),
            "metadata_fingerprint":metadata_fingerprint(cursor,schema),"coverage":"installed_design_catalog_and_physical_schema",
            "publish_result_file":report.get("result_file",""),"recorded_utc":datetime.now(timezone.utc).isoformat()}
    path=root_dir()/"published_baseline.json"
    temporary=path.with_suffix(".tmp"); temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8"); temporary.replace(path)
    return record


def reconcile_restored_baseline(receipt: dict, fingerprint: str) -> dict:
    """Restore the MDB pointer only if it matches the backed-up design catalog."""
    baseline=receipt.get("published_baseline")
    target={"server":config.DESIGNER_DB_SERVER,"database":config.DESIGNER_DB_NAME}
    valid=False
    if isinstance(baseline,dict) and baseline.get("target")==target and baseline.get("metadata_fingerprint")==fingerprint:
        try:
            valid=vendor.digest(source_path(baseline["mdb_file"],".mdb"))==baseline.get("sha256")
        except (ValueError,KeyError,OSError):
            pass
    record={**baseline,"status":"verified"} if valid else {
        "status":"requires_baseline_reconciliation","target":target,"metadata_fingerprint":fingerprint,
        "reason":"恢复的数据库没有可核对的MDB基线；继续发布前必须重新核对设计目录"}
    path=root_dir()/"published_baseline.json"
    temporary=path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
    temporary.replace(path)
    return record


@contextmanager
def target_connection(database: str = "", update: bool = False):
    import pyodbc
    if not all((config.DESIGNER_DB_SERVER, config.DESIGNER_DB_NAME, config.DESIGNER_DB_USER, config.DESIGNER_DB_PASSWORD)):
        raise ValueError("请在本机.env中配置Designer数据库目标与凭据")
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    if not drivers:
        raise ValueError("缺少SQL Server ODBC驱动")
    driver = next((d for d in reversed(drivers) if "ODBC Driver" in d), drivers[0])
    values = {"DRIVER": driver, "SERVER": config.DESIGNER_DB_SERVER, "DATABASE": database or config.DESIGNER_DB_NAME,
              "UID": config.DESIGNER_UPDATE_DB_USER if update else config.DESIGNER_DB_USER,
              "PWD": config.DESIGNER_UPDATE_DB_PASSWORD if update else config.DESIGNER_DB_PASSWORD}
    connection = pyodbc.connect(";".join(k+"="+odbc_value(v) for k,v in values.items()), timeout=8, autocommit=True)
    try:
        yield connection
    finally:
        connection.close()


def inspect_target(table: str = "") -> dict:
    with target_connection() as conn:
        cur = conn.cursor()
        name = cur.execute("SELECT DB_NAME()").fetchone()[0]
        tables = cur.execute("SELECT TABLE_SCHEMA,TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA,TABLE_NAME").fetchall()
        columns = []
        if table:
            columns = [dict(zip(("schema", "table", "column", "data_type", "max_length", "nullable"), row)) for row in cur.execute(
                "SELECT TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,DATA_TYPE,CHARACTER_MAXIMUM_LENGTH,IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", table).fetchall()]
    return {"server": config.DESIGNER_DB_SERVER, "database": name, "read_only": True, "table_count": len(tables),
            "tables": [{"schema": r[0], "name": r[1]} for r in tables[:100]], "tables_truncated": len(tables)>100,
            "columns": columns, "table": table}


def preflight(manifest_file: str) -> dict:
    path = source_path(manifest_file, ".json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 2:
        raise ValueError("发布前检查需要官方MDB设计包 format_version=2")
    for name, checksum in manifest["artifacts"].items():
        file = source_path(str(path.parent/name),Path(name).suffix)
        if vendor.digest(file)!=checksum:
            raise ValueError(f"设计包发生变化：{name}")
    source=source_path(manifest["source_file"],".mdb")
    if vendor.digest(source)!=manifest["source_sha256"]:
        raise ValueError("设计来源已漂移，请重新生成设计包")
    root,_=load_xml(source_path(str(path.parent/"changes.xml"),".xml"))
    checks=[]
    for table in root.findall("Import/DBTableDefinitions/DBTableDefinition"):
        name=table.get("Name","")
        target=inspect_target(name)
        for column in table.findall("Columns/DBColumnDefinition"):
            col_name=column.get("Name","")
            matches=[r for r in target["columns"] if r["column"].casefold()==col_name.casefold()]
            checks.append({"table":name,"column":col_name,"action":column.get("Action"),
                           "existing_columns":matches,"proposed_precision":column.findtext("Attributes/Precision"),
                           "proposed_sql_type":column.findtext("Attributes/SQLType/Name"),
                           "conflict":column.get("Action")=="Create" and bool(matches)})
    target=inspect_target()
    blockers=[]
    if not config.DESIGNER_TEST_TARGET_CONFIRMED:
        blockers.append("尚未确认该目标允许发布测试。")
    if any(c["conflict"] for c in checks): blockers.append("拟新增列已存在于目标数据库。")
    plan={"status":"preflight_only", "ready_for_publish":not blockers,"database_modified":False,
          "services_verified":False,"verified_backup_required":True,
          "manifest_file":str(path),"manifest_sha256":vendor.digest(path),
          "target":{"server":target["server"],"database":target["database"]},"column_checks":checks,
          "blockers":blockers,"required_steps":["备份目标数据库并确认恢复路径。","核对服务器当前MDB与包基线。",
          "通过厂商Update DB应用元数据和存储差异。","按确认的测试目录生成服务并核对接口。","验证200字符接受、201字符拒绝及旧业务回归。"]}
    folder=artifact_dir()
    (folder/"publish_plan.json").write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
    plan["plan_file"]=str(folder/"publish_plan.json")
    return plan


def backup_target() -> dict:
    """Create a unique COPY_ONLY SQL backup and verify its server-side readability."""
    if not config.DESIGNER_TEST_TARGET_CONFIRMED:
        raise ValueError("尚未配置经用户确认的测试目标")
    folder=artifact_dir()
    result={"status":"backup_started","target":{"server":config.DESIGNER_DB_SERVER,"database":config.DESIGNER_DB_NAME},
            "created_utc":datetime.now(timezone.utc).isoformat(),"database_modified":False}
    receipt=folder/"backup_receipt.json"
    try:
        with target_connection() as conn:
            cur=conn.cursor()
            actual, directory, permission=cur.execute("SELECT DB_NAME(),CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000)),HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','BACKUP DATABASE')").fetchone()
            if actual!=config.DESIGNER_DB_NAME or not directory or not permission:
                raise ValueError("数据库身份、备份目录或备份权限未通过核对")
            baseline_path=root_dir()/"published_baseline.json"
            if baseline_path.is_file():
                baseline=json.loads(baseline_path.read_text(encoding="utf-8"))
                schema=target_schema(cur)
                if baseline.get("target")==result["target"] and baseline.get("metadata_fingerprint")==metadata_fingerprint(cur,schema):
                    try:
                        if vendor.digest(source_path(baseline["mdb_file"],".mdb"))==baseline.get("sha256"):
                            result["published_baseline"]=baseline
                    except (ValueError,KeyError,OSError):
                        pass
            from pathlib import PureWindowsPath
            backup=str(PureWindowsPath(directory)/f"DesignerMCP_{folder.name}.bak")
            result["server_backup_file"]=backup
            receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            db_identifier=sql_identifier(actual)
            cur.execute(f"BACKUP DATABASE {db_identifier} TO DISK=? WITH COPY_ONLY,CHECKSUM",backup)
            while cur.nextset(): pass
            cur.execute("RESTORE VERIFYONLY FROM DISK=? WITH CHECKSUM",backup)
            while cur.nextset(): pass
            result.update(status="backup_verified",restore_verifyonly=True,copy_only=True)
    except Exception as exc:
        result.update(status="backup_failed",error=redact(str(exc)))
        receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        raise ValueError(f"数据库备份未完成，记录：{receipt}；{result['error']}") from None
    receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return {**result,"receipt_file":str(receipt),"receipt_sha256":vendor.digest(receipt)}


def restore_test_backup(backup_receipt: str, expected_receipt_sha256: str) -> dict:
    """Restore the exact verified test backup after an unsuccessful publication."""
    if not config.DESIGNER_TEST_TARGET_CONFIRMED: raise ValueError("测试目标未确认")
    receipt_path=source_path(backup_receipt,".json")
    if vendor.digest(receipt_path)!=expected_receipt_sha256: raise ValueError("备份凭证SHA256不匹配")
    receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
    target={"server":config.DESIGNER_DB_SERVER,"database":config.DESIGNER_DB_NAME}
    if receipt.get("status")!="backup_verified" or receipt.get("target")!=target: raise ValueError("备份与测试目标不匹配")
    folder=artifact_dir(); result={"status":"restoring","target":target,"backup_receipt":str(receipt_path)}
    path=folder/"restore_result.json"
    db=sql_identifier(target["database"])
    try:
        with target_connection("master") as conn:
            cur=conn.cursor()
            cur.execute("RESTORE VERIFYONLY FROM DISK=? WITH CHECKSUM",receipt["server_backup_file"])
            while cur.nextset(): pass
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            cur.execute(f"ALTER DATABASE {db} SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
            try:
                cur.execute(f"RESTORE DATABASE {db} FROM DISK=? WITH REPLACE,RECOVERY",receipt["server_backup_file"])
                while cur.nextset(): pass
            finally:
                cur.execute(f"ALTER DATABASE {db} SET MULTI_USER")
            state=cur.execute("SELECT state_desc FROM sys.databases WHERE name=?",target["database"]).fetchone()[0]
            if state!="ONLINE": raise ValueError(f"恢复后数据库状态：{state}")
            result.update(status="restored",database_state=state,database_restored=True)
        with target_connection() as restored_conn:
            restored_cursor=restored_conn.cursor()
            fingerprint=metadata_fingerprint(restored_cursor,target_schema(restored_cursor))
            result["published_baseline"]=reconcile_restored_baseline(receipt,fingerprint)
    except Exception as exc:
        error=redact(str(exc))
        result.update(status="restore_failed",error=error)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        raise ValueError(f"恢复失败，记录：{path}；{error}") from None
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return {**result,"result_file":str(path)}


def verified_manifest(manifest_file: str) -> tuple[Path,dict]:
    path=source_path(manifest_file,".json")
    manifest=json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format_version")!=2 or manifest.get("status")!="saved_to_test_copy_and_exported":
        raise ValueError("需要成功保存并导出的官方设计包")
    for name in ("baseline.mdb","modified.mdb","changes.xml"):
        if vendor.digest(source_path(str(path.parent/name),Path(name).suffix))!=manifest["artifacts"][name]:
            raise ValueError(f"设计包发生变化：{name}")
    if vendor.digest(source_path(manifest["source_file"],".mdb"))!=manifest["source_sha256"]:
        raise ValueError("设计来源已漂移")
    return path,manifest


def verify_published_design(manifest_file: str, test_string_boundaries: bool = False) -> dict:
    """Check changed definitions and physical string columns, without business writes."""
    path,_=verified_manifest(manifest_file)
    root,_=load_xml(path.parent/"changes.xml")
    checks=[]
    with target_connection() as conn:
        cur=conn.cursor(); schema=target_schema(cur); prefix=sql_identifier(schema)+"."
        for cdo in root.findall("Import/CDODefinitions/CDODefinition"):
            name=cdo.get("Name",""); parent=cdo.findtext("Attributes/ParentCDO/Name")
            if cdo.get("Action")=="Delete":
                continue  # Deletion and other design domains have separate acceptance requirements.
            row=cur.execute(f"SELECT p.CDOName FROM {prefix}CDODefinition c LEFT JOIN {prefix}CDODefinition p ON c.ParentCDOID=p.CDODefID WHERE c.CDOName=?",name).fetchone()
            checks.append({"kind":"cdo","name":name,"expected_parent":parent,"actual_parent":row[0] if row else None,
                           "passed":bool(row) and (parent is None or parent==row[0])})
            for field in cdo.findall("CDOFieldDefinitions/CDOFieldDefinition"):
                if field.get("Action")=="Delete": continue
                field_name=field.get("Name",""); expected_type=field.findtext("Attributes/FieldDef")
                if not expected_type: expected_type=field.findtext("Attributes/FieldDef/Name")
                nonpersistent=field.findtext("Attributes/IsNonPersistent")
                row=cur.execute(f"SELECT d.FieldDefName,f.IsNonpersistent,d.PrecisionValue FROM {prefix}CDOFields f JOIN {prefix}CDODefinition c ON c.CDODefID=f.CDODefID JOIN {prefix}FieldDefinitions d ON d.FieldDefID=f.FieldDefID WHERE c.CDOName=? AND f.FieldName=?",name,field_name).fetchone()
                checks.append({"kind":"field","owner":name,"name":field_name,"expected_type":expected_type,
                               "actual_type":row[0] if row else None,"precision":row[2] if row else None,
                               "passed":bool(row) and (not expected_type or expected_type==row[0]) and
                                        (nonpersistent is None or bool(row[1])==(nonpersistent.casefold()=="true"))})
        for table in root.findall("Import/DBTableDefinitions/DBTableDefinition"):
            table_name=table.get("Name","")
            for column in table.findall("Columns/DBColumnDefinition"):
                if column.get("Action")=="Delete": continue
                column_name=column.get("Name","")
                precision=column.findtext("Attributes/Precision")
                string_type="String" in (column.findtext("Attributes/SQLType/Name") or "")
                row=cur.execute("SELECT DATA_TYPE,CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND COLUMN_NAME=?",schema,table_name,column_name).fetchone()
                passed=bool(row) and (not string_type or row[0] in ("varchar","nvarchar","char","nchar") and precision is not None and row[1]==int(precision))
                check={"kind":"column","table":table_name,"name":column_name,"actual_type":row[0] if row else None,
                       "actual_length":row[1] if row else None,"expected_precision":precision,"passed":passed}
                if passed and string_type and test_string_boundaries and 0<int(precision)<=4096:
                    if not config.DESIGNER_TEST_TARGET_CONFIRMED: raise ValueError("长度负例测试需要已确认测试目标")
                    length=int(precision); col=sql_identifier(column_name)
                    cur.execute("SET ANSI_WARNINGS ON")
                    cur.execute(f"SELECT TOP (0) {col} INTO #DesignerLengthProbe FROM {prefix}{sql_identifier(table_name)}")
                    try:
                        cur.execute(f"INSERT INTO #DesignerLengthProbe ({col}) VALUES (?)","x"*length)
                        accepted=cur.execute(f"SELECT LEN({col}) FROM #DesignerLengthProbe").fetchone()[0]==length
                        rejected=False
                        try: cur.execute(f"INSERT INTO #DesignerLengthProbe ({col}) VALUES (?)","x"*(length+1))
                        except Exception as exc:
                            if not any(code in str(exc) for code in ("8152","2628")): raise
                            rejected=True
                        check.update(boundary_accepted=accepted,overflow_rejected=rejected,passed=accepted and rejected)
                    finally: cur.execute("DROP TABLE #DesignerLengthProbe")
                checks.append(check)
    folder=artifact_dir(); result={"status":"verified" if checks and all(c["passed"] for c in checks) else "verification_failed",
        "target":{"server":config.DESIGNER_DB_SERVER,"database":config.DESIGNER_DB_NAME},"schema":schema,
        "manifest_file":str(path),"checks":checks,"business_rows_modified":False,"runtime_service_verified":False,
        "coverage":"changed_cdos_fields_and_columns; other design domains require separate acceptance"}
    file=folder/"verification_result.json"; file.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return {**result,"result_file":str(file)}


def publish_database(manifest_file: str, expected_manifest_sha256: str, backup_receipt: str, siteinfo_mdb: str,
                     expected_target_fingerprint: str = '') -> dict:
    """Execute installed Update DB against the configured, confirmed test target."""
    if not config.DESIGNER_TEST_TARGET_CONFIRMED:
        raise ValueError("目标未确认允许发布测试")
    path,manifest=verified_manifest(manifest_file)
    if vendor.digest(path)!=expected_manifest_sha256:
        raise ValueError("设计包清单SHA256不匹配")
    receipt_path=source_path(backup_receipt,".json")
    receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
    target={"server":config.DESIGNER_DB_SERVER,"database":config.DESIGNER_DB_NAME}
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(receipt["created_utc"])).total_seconds()
    if receipt.get("status")!="backup_verified" or receipt.get("target")!=target or not 0<=age<=3600:
        raise ValueError("需要该测试目标一小时内完成校验的备份凭证")
    siteinfo=source_path(siteinfo_mdb,".mdb")
    folder=artifact_dir()
    report={"status":"preparing","target":target,"manifest_file":str(path),"manifest_sha256":expected_manifest_sha256,
            "backup_receipt":str(receipt_path),"configuration_updated":False,"database_modified":False}
    report_file=folder/"publish_result.json"
    try:
        compiled_folder=folder/"compile"
        compiled_folder.mkdir()
        input_copy=compiled_folder/"input.mdb"
        shutil.copyfile(path.parent/"modified.mdb",input_copy)
        compiled=vendor.run(compiled_folder,{"mode":"compile","mdb":str(input_copy)})
        dll_dir=vendor.assembly().parent
        for name in ("Camstar.Data.dll","OECAdmin.dll","CIMS.DBUpdate.dll"):
            if not (dll_dir/name).is_file(): raise ValueError(f"缺少官方发布组件：{name}")
        env=os.environ.copy()
        env.update(OPCORE_DB_NAME=target["database"],OPCORE_DB_HOST=target["server"],
                   OPCORE_DB_USERNAME=config.DESIGNER_UPDATE_DB_USER,OPCORE_DB_PASSWORD=config.DESIGNER_UPDATE_DB_PASSWORD,OPCORE_DB_TYPE="SQLServer")
        with target_connection() as conn:
            cur=conn.cursor()
            actual,owner,admin=cur.execute("SELECT DB_NAME(),IS_MEMBER('db_owner'),IS_SRVROLEMEMBER('sysadmin')").fetchone()
            if actual!=target["database"] or not (owner or admin):
                raise ValueError("官方Update DB需要测试库管理员权限；实际数据库或权限不匹配")
            schema=target_schema(cur)
            with target_connection(update=True) as update_conn:
                default_schema,permission=update_conn.cursor().execute("SELECT SCHEMA_NAME(),HAS_PERMS_BY_NAME(?,'SCHEMA','CONTROL')",schema).fetchone()
                if default_schema!=schema or not permission:
                    raise ValueError("Update DB账号的默认schema或应用schema控制权限不匹配；未执行数据库修改")
            request={"server":target["server"],"database":target["database"],"schema":schema,
                     "compiled_mdb":compiled["compiled_mdb"],"siteinfo_mdb":str(siteinfo)}
            (folder/"request.json").write_text(json.dumps(request),encoding="utf-8")
            lock=cur.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource='CamstarDesignerMCP.Publish',@LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0; SELECT @r").fetchone()[0]
            if lock<0: raise ValueError("同一数据库已有Designer发布操作")
            if expected_target_fingerprint and metadata_fingerprint(cur,schema) != expected_target_fingerprint:
                raise ValueError('发布检查后目标设计已变化，请重新检查并备份')
            baseline_file=root_dir()/"published_baseline.json"
            if baseline_file.is_file():
                baseline=json.loads(baseline_file.read_text(encoding="utf-8"))
                if baseline.get("target")==target:
                    if baseline.get("status","verified")!="verified":
                        raise ValueError("恢复后的MDB基线尚未核对，拒绝使用旧设计包继续发布")
                    if manifest["source_sha256"]!=baseline["sha256"]:
                        raise ValueError("设计包未基于最近发布的MDB生成；请使用published_baseline.json中的mdb_file重新设计")
                    if metadata_fingerprint(cur,schema)!=baseline["metadata_fingerprint"]:
                        raise ValueError("目标设计或物理结构在上次发布后变化，请重新核对设计基线")
            cur.execute("RESTORE VERIFYONLY FROM DISK=? WITH CHECKSUM",receipt["server_backup_file"])
            while cur.nextset(): pass
            report.update(status="executing_update",database_modified="unknown",schema=schema,compiled_mdb=compiled["compiled_mdb"],compiled_sha256=vendor.digest(Path(compiled["compiled_mdb"])))
            report_file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            shell=Path(os.environ.get("SystemRoot","C:/Windows"))/"System32/WindowsPowerShell/v1.0/powershell.exe"
            args=[str(shell),"-NoProfile","-NonInteractive","-Mta","-File",str(Path(__file__).with_name("publication_bridge.ps1")),
                  "-AssemblyDirectory",str(dll_dir),"-RequestFile",str(folder/"request.json"),"-ResultFile",str(folder/"result.json")]
            with (folder/"update.log").open("wb") as log:
                process=subprocess.run(args,cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT,
                                       timeout=config.DESIGNER_PUBLICATION_TIMEOUT,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if process.returncode or not (folder/"result.json").is_file():
                raise ValueError(f"发布组件退出码{process.returncode}，请检查update.log")
            result=json.loads((folder/"result.json").read_text(encoding="utf-8-sig"))
            if not result.get("ok"): raise ValueError(result.get("error","官方发布失败"))
            report.update(result["result"],database_modified=True)
            # Verify every newly created CDO and its persisted field through SQL.
            checks=[]
            for op in manifest["operations"]:
                if op["action"]=="create_cdo":
                    row=cur.execute(f"SELECT CDOName FROM {sql_identifier(schema)}.CDODefinition WHERE CDOName=?",op["name"]).fetchone()
                    if not row: raise ValueError(f"发布后缺少CDO：{op['name']}")
                    checks.append({"cdo":op["name"],"present":True})
            report.update(status="database_published",metadata_checks=checks,services_deployed=False)
            report["published_baseline"]=record_published_baseline(path,{**report,"result_file":str(report_file)},cur,schema)
    except Exception as exc:
        error=redact(str(exc))
        report.update(status="publish_failed",error=error)
        report_file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        raise ValueError(f"发布未完成；记录：{report_file}；{error}") from None
    finally:
        for name in ("update.log","result.json"):
            file=folder/name
            if file.is_file():
                content=file.read_text(encoding="utf-8-sig",errors="replace")
                file.write_text(redact(content),encoding="utf-8")
    report_file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return {**report,"result_file":str(report_file)}
