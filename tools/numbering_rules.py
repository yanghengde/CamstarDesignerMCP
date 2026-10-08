"""
NumberingRules read-only MCP tools
==================================
Swagger: Modeling /api/NumberingRules

These tools discover and validate numbering rules before ContainerStart.
They intentionally do not modify global sequence state.
"""

from typing import Optional

from core.http_client import request
from tools import mcp


@mcp.tool
async def list_numbering_rules(
    filter_expr: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    select: Optional[str] = None,
    orderby: Optional[str] = None,
) -> str:
    """
    List Container numbering rules.
    GET Modeling /api/NumberingRules

    Useful fields include name, prefix, suffix, sequenceLength,
    lastAssignedSequence, maximumValue, isRollover, and numberingRuleType.
    Use this before container_start when the numbering rule name is unknown.
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
    if orderby:
        params["$orderby"] = orderby

    return await request(
        "GET",
        "/api/NumberingRules",
        params=params or None,
    )


@mcp.tool
async def get_numbering_rule(key: str) -> str:
    """
    Get one Container numbering rule by name or instance ID.
    GET Modeling /api/NumberingRules/{key}

    Use this to validate auto_number_rule_name before container_start.
    """
    return await request("GET", f"/api/NumberingRules/{key}")


@mcp.tool
async def get_numbering_rules_count(
    filter_expr: Optional[str] = None,
) -> str:
    """Count Container numbering rules, optionally using an OData filter."""
    params = {"$filter": filter_expr} if filter_expr else None
    return await request(
        "GET",
        "/api/NumberingRules/$count",
        params=params,
    )
