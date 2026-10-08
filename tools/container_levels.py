"""
ContainerLevels read-only MCP tools
===================================
Swagger: Modeling /api/ContainerLevels

These tools discover and validate Container Levels before ContainerStart.
"""

from typing import Optional

from core.http_client import request
from tools import mcp


@mcp.tool
async def list_container_levels(
    filter_expr: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    select: Optional[str] = None,
    expand: Optional[str] = None,
    orderby: Optional[str] = None,
) -> str:
    """
    List Container Levels.
    GET Modeling /api/ContainerLevels

    Useful fields include name, description, containerNumberingRule,
    isNameUnique, parentLevels, childLevels, and allowMove. Use this before
    container_start when the level_name or its default numbering rule is
    unknown.
    """
    params = {}
    if filter_expr:
        params["$filter"] = filter_expr
    if top is not None:
        params["$top"] = top
    if skip is not None:
        params["$skip"] = skip
    if select:
        params["$select"] = select
    if expand:
        params["$expand"] = expand
    if orderby:
        params["$orderby"] = orderby

    return await request(
        "GET",
        "/api/ContainerLevels",
        params=params or None,
    )


@mcp.tool
async def get_container_level(key: str) -> str:
    """
    Get one Container Level by name or instance ID.
    GET Modeling /api/ContainerLevels/{key}

    Use this to validate level_name and inspect containerNumberingRule before
    container_start.
    """
    return await request("GET", f"/api/ContainerLevels/{key}")


@mcp.tool
async def get_container_levels_count(
    filter_expr: Optional[str] = None,
) -> str:
    """Count Container Levels, optionally using an OData filter."""
    params = {"$filter": filter_expr} if filter_expr else None
    return await request(
        "GET",
        "/api/ContainerLevels/$count",
        params=params,
    )
