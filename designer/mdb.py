"""Read-only Access adapter; schema mapping is discovered rather than assumed."""

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import base64


def access_drivers() -> list[str]:
    try:
        import pyodbc
    except ImportError:
        return []
    return [d for d in pyodbc.drivers() if "access" in d.casefold() and "mdb" in d.casefold()]


@contextmanager
def connect(path: Path):
    drivers = access_drivers()
    if not drivers:
        raise ValueError("未安装与 Python 位数匹配的 Microsoft Access ODBC 驱动")
    import pyodbc
    if any(c in str(path) for c in "{};\r\n"):
        raise ValueError("MDB 路径包含 ODBC 连接字符串保留字符")
    conn = pyodbc.connect(
        f"DRIVER={{{drivers[0]}}};DBQ={path};READONLY=1;",
        autocommit=True, timeout=10,
    )
    try:
        yield conn
    finally:
        conn.close()


def tables(conn) -> list[str]:
    return sorted({row.table_name for row in conn.cursor().tables(tableType="TABLE") if not row.table_name.startswith("MSys")})


def schema(path: Path) -> dict:
    with connect(path) as conn:
        result = []
        for table in tables(conn):
            quoted = "[" + table.replace("]", "]]") + "]"
            # ACE SQLColumns can return malformed UTF-16 in legacy remarks.
            # Query only the result schema, without fetching data or remarks.
            cursor = conn.cursor().execute(f"SELECT * FROM {quoted} WHERE 1=0")
            result.append({"name": table, "columns": [
                {"name": col[0], "type": col[1].__name__, "size": col[3], "nullable": col[6]}
                for col in cursor.description
            ]})
    return {"file": path.name, "read_only": True, "tables": result, "mapping_status": "raw_schema_only"}


def json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, str) and len(value) > 4000:
        return {"preview": value[:4000], "truncated": True}
    return value


def read_table(path: Path, table: str, limit: int) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit 必须为 1～100")
    with connect(path) as conn:
        known = tables(conn)
        if table not in known:
            raise ValueError("表名必须来自 inspect_designer_mdb 返回的表列表")
        quoted = "[" + table.replace("]", "]]") + "]"
        cursor = conn.cursor().execute(f"SELECT TOP {limit + 1} * FROM {quoted}")
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        result = [dict(zip(columns, (json_value(v) for v in row))) for row in rows[:limit]]
    return {"table": table, "rows": result, "has_more": len(rows) > limit, "read_only": True}
