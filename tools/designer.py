"""Designer tools shared by FastMCP and the natural-language agent."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import config
from designer.files import artifact_dir, root_dir, source_path
from designer import mdb
from designer import catalog
from designer.metadata import (
    build_field_draft, cdo_nodes, definition_index, fields, find_named,
    load_xml, serialize, validate,
)
from tools import mcp


def _page_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit 必须为 1～100")


@mcp.tool
async def get_designer_environment() -> dict:
    """检查 Designer 文件目录、Access 驱动和配置的 MetadataExport；不扫描生产环境。"""
    root = root_dir()
    files = []
    if root.is_dir():
        for path in root.rglob("*"):
            if path.suffix.lower() in {".xml", ".mdb"} and path.is_file() and path.resolve().is_relative_to(root):
                files.append(str(path.relative_to(root)))
                if len(files) == 100:
                    break
    executable = Path(config.DESIGNER_METADATA_EXPORT_EXE) if config.DESIGNER_METADATA_EXPORT_EXE else None
    return {
        "root": str(root), "root_exists": root.is_dir(), "files": files,
        "inventory_limit": 100, "access_drivers": await asyncio.to_thread(mdb.access_drivers),
        "metadata_export_configured": bool(executable),
        "metadata_export_available": bool(executable and executable.is_file() and executable.suffix.lower() == ".exe"),
        "automatic_import_available": False, "database_publish_available": False,
    }


@mcp.tool
async def inspect_designer_mdb(mdb_file: str) -> dict:
    """只读列出 DESIGNER_ROOT 内 MDB 的真实表结构，不假设不同版本的内部表名。"""
    return await asyncio.to_thread(mdb.schema, source_path(mdb_file, ".mdb"))


@mcp.tool
async def read_designer_mdb_table(mdb_file: str, table: str, limit: int = 20) -> dict:
    """只读读取已发现的 MDB 表，最多 100 行；不接受自由 SQL，不修改 MDB。"""
    return await asyncio.to_thread(mdb.read_table, source_path(mdb_file, ".mdb"), table, limit)


@mcp.tool
async def list_designer_mdb_cdos(mdb_file: str, search: str = "", offset: int = 0, limit: int = 20) -> dict:
    """从已验证结构的 InSite MDB 检索 CDO 原始工作区版本；不合并覆盖或继承。"""
    return await asyncio.to_thread(catalog.list_cdos, source_path(mdb_file, ".mdb"), search, offset, limit)


@mcp.tool
async def get_designer_mdb_cdo(mdb_file: str, cdo_name: str, offset: int = 0, limit: int = 20) -> dict:
    """从 InSite MDB 读取 CDO、字段、类型和工作区原始版本；分页，不合并继承或存储映射。"""
    return await asyncio.to_thread(catalog.get_cdo, source_path(mdb_file, ".mdb"), cdo_name, offset, limit)


@mcp.tool
async def list_designer_cdos(xml_file: str, search: str = "", offset: int = 0, limit: int = 20) -> dict:
    """检索导出 XML 中的 CDO，返回来源 SHA256；XML 可能只包含差异。"""
    _page_limit(limit)
    if offset < 0:
        raise ValueError("offset 不能为负数")
    root, digest = load_xml(source_path(xml_file, ".xml"))
    validate(root)
    cdos = [c for c in cdo_nodes(root) if search.casefold() in c.get("Name", "").casefold()]
    return {
        "source_sha256": digest, "total": len(cdos), "offset": offset,
        "cdos": [{"name": c.get("Name"), "field_count": len(fields(c))} for c in cdos[offset:offset + limit]],
        "scope": "provided_xml_only",
    }


@mcp.tool
async def get_designer_cdo(xml_file: str, cdo_name: str) -> dict:
    """读取指定 CDO 的原始 XML（含类型和映射），用于选择现有字段模板。"""
    root, digest = load_xml(source_path(xml_file, ".xml"))
    validate(root)
    cdo = find_named(cdo_nodes(root), cdo_name)
    xml = ET.tostring(cdo, encoding="unicode")
    if len(xml) > 50000:
        raise ValueError("CDO 定义超过 50000 字符，请使用更小的对象导出文件")
    return {"source_sha256": digest, "cdo_name": cdo.get("Name"), "xml": xml, "scope": "provided_xml_only"}


@mcp.tool
async def validate_designer_xml(xml_file: str) -> dict:
    """检查 XML 文档结构、重复定义和 ExpectedValue 格式；不等同于 Designer 导入校验。"""
    root, digest = load_xml(source_path(xml_file, ".xml"))
    return {"source_sha256": digest, **validate(root)}


@mcp.tool
async def compare_designer_xml(base_xml: str, modified_xml: str, limit: int = 100) -> dict:
    """比较两个 XML 中出现的定义与有效属性；忽略排版、属性顺序及 Action，不推断 MDB 删除。"""
    _page_limit(limit)
    left, left_hash = load_xml(source_path(base_xml, ".xml"))
    right, right_hash = load_xml(source_path(modified_xml, ".xml"))
    validate(left)
    validate(right)
    a, b = definition_index(left), definition_index(right)
    added = sorted(b.keys() - a.keys())
    removed = sorted(a.keys() - b.keys())
    changed = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    return {
        "base_sha256": left_hash, "modified_sha256": right_hash,
        "counts": {"added": len(added), "missing": len(removed), "changed": len(changed)},
        "added": added[:limit], "missing": removed[:limit], "changed": changed[:limit],
        "truncated": any(len(items) > limit for items in (added, removed, changed)),
        "scope": "provided_xml_only",
        "warning": "XML 中缺失的定义不代表目标 MDB 已删除该定义；仅比较文档所包含的属性。",
    }


@mcp.tool
async def generate_designer_field_package(
    xml_file: str, expected_sha256: str, target_cdo: str, template_cdo: str,
    template_field: str, field_name: str, workspace: str,
    description: str | None = None,
) -> dict:
    """从已有 XML 字段模板生成新增非持久化简单字段的草案包。必须先查询来源 SHA256。

    workspace 仅记录拟导入的客户工作区，实际工作区需在测试 Designer 中选择。
    不生成存储映射，不导入 MDB，不发布数据库。真实导入前需核对目标字段及模板类型。
    """
    if not workspace.strip():
        raise ValueError("必须指定拟导入的客户工作区，不能猜测")
    path = source_path(xml_file, ".xml")
    root, digest = load_xml(path)
    if digest != expected_sha256:
        raise ValueError("来源 XML 已变化，或 SHA256 不匹配；请重新读取定义后生成")
    draft, plan = build_field_draft(root, target_cdo, template_cdo, template_field, field_name, description)
    output = serialize(draft)
    # Retain the parsed source, so the audit snapshot exactly matches the plan.
    baseline = serialize(root)
    folder = artifact_dir()
    (folder / "changes.xml").write_bytes(output)
    (folder / "baseline.xml").write_bytes(baseline)
    manifest = {
        "format_version": 1, "status": "draft_requires_test_import", "ready_for_publish": False,
        "source_file": str(path), "source_sha256": digest,
        "baseline_sha256": sha256(baseline).hexdigest(),
        "changes_sha256": sha256(output).hexdigest(),
        "workspace": workspace, "workspace_applied_to_xml": False, "plan": plan,
        "validation": validate(draft),
        "required_checks": [
            "在测试 MDB 中确认对象、父对象及类型引用存在，且没有同名字段。",
            "确认所有模板属性符合新字段用途，尤其默认值、权限和类型。",
            "选择指定客户工作区，通过 File > Import 导入测试 MDB。",
            "导出导入后的差异，核对新增字段及所有意外变更。",
            "单独验证数据库更新和服务生成；本工具不执行发布。",
        ],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report = (
        f"# Designer 字段变更草案\n\n目标：{plan['target_cdo']}.{field_name}\n\n"
        f"工作区：{workspace}（需要在 Designer 中选择）\n\n"
        f"模板：{template_cdo}.{template_field}\n\n来源 SHA256：`{digest}`\n\n"
        "状态：仅通过文档结构校验，尚未导入测试 MDB；未生成存储映射。\n\n"
        "Action=Create 只是导入意图，不保证同名字段不会被更新。Header 的 Stop 只对"
        "已指定 ExpectedValue 的属性冲突生效，本包不能检查目标字段不存在。\n\n"
        + "\n".join(f"- {item}" for item in manifest["required_checks"])
        + "\n\n```xml\n" + plan["attributes_xml"] + "\n```\n"
    )
    (folder / "report.md").write_text(report, encoding="utf-8")
    return {
        "status": manifest["status"], "ready_for_publish": False, "plan": plan,
        "files": {name: str(folder / name) for name in ("changes.xml", "baseline.xml", "manifest.json", "report.md")},
        "required_checks": manifest["required_checks"],
    }


@mcp.tool
async def check_designer_package(manifest_file: str) -> dict:
    """检查生成包的文件哈希及来源漂移；不会把草案标为已导入或可发布。"""
    path = source_path(manifest_file, ".json")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("manifest 文件过大")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    checks = {}
    for name, key in (("changes.xml", "changes_sha256"), ("baseline.xml", "baseline_sha256")):
        file = source_path(str(path.parent / name), ".xml")
        root, digest = load_xml(file)
        validate(root)
        checks[name] = digest == manifest[key]
    source = source_path(manifest["source_file"], ".xml")
    _, digest = load_xml(source)
    checks["source_unchanged"] = digest == manifest["source_sha256"]
    return {"intact": all(checks.values()), "checks": checks, "ready_for_publish": False, "scope": "file_integrity_only"}


@mcp.tool
async def export_designer_metadata_diff(base_mdb: str, modified_mdb: str) -> dict:
    """运行官方 MetadataExport -a，在输入副本上导出全部差异 XML/HTML；不导入、不发布。

    需要管理员在 DESIGNER_METADATA_EXPORT_EXE 配置实际 exe 路径。结果写入新 artifact 目录。
    """
    base = source_path(base_mdb, ".mdb")
    modified = source_path(modified_mdb, ".mdb")
    if base == modified:
        raise ValueError("基线与修改后的 MDB 不能是同一个文件")
    if not config.DESIGNER_METADATA_EXPORT_EXE:
        raise ValueError("尚未配置 DESIGNER_METADATA_EXPORT_EXE，不能执行官方导出")
    exe = Path(config.DESIGNER_METADATA_EXPORT_EXE)
    if not exe.is_absolute() or not exe.is_file() or exe.suffix.lower() != ".exe":
        raise ValueError("DESIGNER_METADATA_EXPORT_EXE 必须是已安装官方工具的绝对 exe 路径")
    folder = artifact_dir()
    await asyncio.to_thread(shutil.copyfile, base, folder / "base.mdb")
    await asyncio.to_thread(shutil.copyfile, modified, folder / "modified.mdb")
    xml, report = folder / "changes.xml", folder / "report.html"
    args = [str(exe), "-a", "-b", str(folder / "base.mdb"), "-m", str(folder / "modified.mdb"), "-o", str(xml), "-r", str(report)]

    def run():
        # No shell, no argument interpolation, no visible console window.
        with (folder / "export.log").open("wb") as log:
            return subprocess.run(
                args, cwd=str(exe.parent), stdout=log, stderr=subprocess.STDOUT,
                timeout=config.DESIGNER_EXPORT_TIMEOUT, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).returncode

    try:
        code = await asyncio.to_thread(run)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"官方导出超时，日志目录：{folder}") from exc
    if code != 0 or not xml.is_file() or not report.is_file():
        raise ValueError(f"官方导出未成功（退出码 {code}），请检查 {folder / 'export.log'}")
    root, digest = load_xml(xml)
    return {"status": "exported", "xml_file": str(xml), "report_file": str(report), "source_sha256": digest, "validation": validate(root), "input_mdbs_modified": False}
