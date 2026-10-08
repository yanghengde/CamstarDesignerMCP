"""
MCP 工具注册中心
==================
所有 MCP 工具模块在此聚合，导出统一的 mcp 实例。
未来新增模块只需在此文件 import 即可。
"""

import logging
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

mcp = FastMCP(
    "CamstarModeling",
    instructions=(
        "MCP Server for Siemens Opcenter (Camstar) Modeling, Shopfloor, and "
        "Query APIs. Provides tools to manage modeling entities, execute "
        "manufacturing Container transactions, and run read-only queries."
    ),
)

# -------------------------------------------------------
# 按模块导入工具 —— 工具通过 @mcp.tool 自动注册
# -------------------------------------------------------
from tools import specs                # Spec 实体
from tools import operations           # Operation 实体
from tools import workflows            # Workflow 实体
from tools import products             # Product 实体
from tools import mfgorders            # MfgOrder 实体
from tools import container_start       # Shopfloor Container Start 事务
from tools import container_moves       # Shopfloor Move/MoveIn/MoveOut 事务
from tools import container_quality     # Shopfloor ContainerDefect/Rework 事务
from tools import numbering_rules       # NumberingRule 查询
from tools import container_levels      # ContainerLevel 查询
from tools import queries               # Query API 查询
from tools import mfglines             # MfgLine 实体
from tools import producttypes         # ProductType 实体
from tools import excel_importer       # Excel 导入工具
from tools import system_info          # MCP 服务诊断（无外部副作用）


def get_tool_func(name: str):
    """
    按函数名查找已注册的工具函数，供 Agent 直接调用。
    新增模块时，将对应 module 加入列表即可。
    """
    for module in [specs, operations, workflows, products, mfgorders,
                   container_start, container_moves, container_quality,
                   numbering_rules,
                   container_levels, queries,
                   mfglines, producttypes, excel_importer, system_info]:
        func = getattr(module, name, None)
        if func is not None:
            return func
    return None
