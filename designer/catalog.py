"""Read raw CDO/field versions using the schema verified on an InSite snapshot.

Workspace overrides and inheritance masks are deliberately not merged here.
"""

from pathlib import Path

from designer.mdb import connect, json_value, tables

REQUIRED_COLUMNS = {
    "CDODefinition": {"CDODefID", "CDOName", "ParentCDOID", "WorkspaceCode"},
    "CDOFields": {"FieldID", "CDODefID", "FieldName", "FieldDefID", "CPPDataTypeID", "WorkspaceCode", "SequenceNumber"},
    "FieldDefinitions": {"FieldDefID", "FieldDefName", "CPPDataTypeID", "WorkspaceCode"},
    "CPPDataTypes": {"DataTypeID", "Name"},
    "Workspace": {"WorkspaceCode", "Sequence", "IsActive"},
}


def check_schema(conn):
    available = set(tables(conn))
    for table, columns in REQUIRED_COLUMNS.items():
        if table not in available:
            raise ValueError(f"此 MDB 不含已验证的 Designer 表 {table}；请先检查原始结构")
        cur = conn.cursor().execute(f"SELECT * FROM [{table}] WHERE 1=0")
        if not columns.issubset({col[0] for col in cur.description}):
            raise ValueError(f"此 MDB 的 {table} 结构与已验证版本不同")


def rows(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, (json_value(v) for v in row))) for row in cursor.fetchall()]


def bounds(offset: int, limit: int):
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
        raise ValueError("offset 必须为 0～10000")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit 必须为 1～100")


def list_cdos(path: Path, search: str, offset: int, limit: int) -> dict:
    bounds(offset, limit)
    with connect(path) as conn:
        check_schema(conn)
        records = rows(conn.cursor().execute(
            "SELECT CDODefID, CDOName, ParentCDOID, WorkspaceCode FROM [CDODefinition] ORDER BY CDOName, WorkspaceCode"
        ))
    records = [r for r in records if search.casefold() in (r["CDOName"] or "").casefold()]
    return {"total_versions": len(records), "cdos": records[offset:offset + limit], "offset": offset, "workspace_resolution": "raw_versions_not_merged", "read_only": True}


def get_cdo(path: Path, name: str, offset: int, limit: int) -> dict:
    bounds(offset, limit)
    with connect(path) as conn:
        check_schema(conn)
        definitions = rows(conn.cursor().execute("SELECT * FROM [CDODefinition] WHERE CDOName=?", name))
        if not definitions:
            raise ValueError(f"MDB 中找不到 CDO {name}")
        ids = sorted({row["CDODefID"] for row in definitions})
        placeholders = ",".join("?" for _ in ids)
        field_records = rows(conn.cursor().execute(
            f"SELECT * FROM [CDOFields] WHERE CDODefID IN ({placeholders}) ORDER BY WorkspaceCode, SequenceNumber, FieldName, FieldID", *ids
        ))
        paged = field_records[offset:offset + limit]
        field_defs = rows(conn.cursor().execute("SELECT FieldDefID, FieldDefName, CPPDataTypeID, WorkspaceCode FROM [FieldDefinitions]"))
        type_records = rows(conn.cursor().execute("SELECT DataTypeID, Name FROM [CPPDataTypes]"))
        types = {row["DataTypeID"]: row["Name"] for row in type_records}
        for field in paged:
            field["data_type_name"] = types.get(field["CPPDataTypeID"])
            field["field_type_versions"] = [d for d in field_defs if d["FieldDefID"] == field["FieldDefID"]]
        workspaces = rows(conn.cursor().execute("SELECT WorkspaceCode, Sequence, IsActive FROM [Workspace] ORDER BY Sequence"))
    return {
        "cdo_name": name, "definitions": definitions,
        "fields": paged, "total_field_versions": len(field_records), "offset": offset,
        "has_more": offset + limit < len(field_records), "workspaces": workspaces,
        "workspace_resolution": "raw_versions_not_merged", "read_only": True,
        "warning": "仅展示该 CDO 的原始定义和字段版本；尚未合并工作区覆盖、继承掩码或父对象字段。不能作为完整有效定义或自动生成 XML 的依据。",
    }
