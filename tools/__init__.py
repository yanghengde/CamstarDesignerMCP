"""
MCP 工具注册中心
==================
所有 MCP 工具模块在此聚合，导出统一的 mcp 实例。
未来新增模块只需在此文件 import 即可。
"""

import logging
import importlib
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

mcp = FastMCP(
    "CamstarDesigner",
    instructions=(
        "Siemens Opcenter Designer metadata tools: read-only MDB inspection, "
        "effective definitions via the installed vendor object model, CDO and field "
        "design on isolated MDB copies, property schemas, where-used analysis, "
        "official difference XML export and integrity validation. "
        "Official Update DB, verified backups and rollback target a confirmed test database. "
        "Report publication separately from XML import and runtime service deployment."
    ),
)

# -------------------------------------------------------
# 按模块导入工具 —— 工具通过 @mcp.tool 自动注册
# -------------------------------------------------------
_module_names = ["designer", "designer_design", "system_info"]
_modules = [importlib.import_module(f"tools.{name}") for name in _module_names]


def get_tool_func(name: str):
    """
    按函数名查找已注册的工具函数，供 Agent 直接调用。
    新增模块时，将对应 module 加入列表即可。
    """
    for module in _modules:
        func = getattr(module, name, None)
        if callable(func) and hasattr(func, "__fastmcp__") and getattr(func, "__module__", None) == module.__name__:
            return func
    return None
