"""
MfgLine 实体 MCP 工具
=====================
Swagger: /api/MfgLines
每个 API 端点均对应一个 @mcp.tool。
"""

import json
from typing import Optional

from tools import mcp
from core.http_client import request


@mcp.tool
async def list_mfglines(
    filter_expr: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    select: Optional[str] = None,
    expand: Optional[str] = None,
    orderby: Optional[str] = None,
) -> str:
    """
    List MfgLines with optional OData querying.
    GET /api/MfgLines

    Optional OData Parameters:
      - filter_expr: example "Name eq 'Line-001'"
      - top: limit number of results
      - skip: skip number of results
      - select: fields to return
      - expand: navigation properties to expand
      - orderby: order sequence, e.g., "Name desc"

    Returns an array of MfgLineEntity objects. Large responses are automatically trimmed.
    """
    params = {}
    if filter_expr: params["$filter"] = filter_expr
    if top is not None: params["$top"] = top
    if skip is not None: params["$skip"] = skip
    if select: params["$select"] = select
    if expand: params["$expand"] = expand
    if orderby: params["$orderby"] = orderby

    return await request("GET", "/api/MfgLines", params=params or None)


@mcp.tool
async def get_mfgline(key: str) -> str:
    """
    Get a single MfgLine by its key (instanceID or name).
    GET /api/MfgLines/{key}
    Returns the full MfgLineEntity object.
    """
    return await request("GET", f"/api/MfgLines/{key}")


@mcp.tool
async def get_mfgline_by_odata_key(key: str) -> str:
    """
    Get a single MfgLine using OData key syntax.
    GET /api/MfgLines({key})
    Example key: 'Line-001'
    Returns the full MfgLineEntity object.
    """
    return await request("GET", f"/api/MfgLines({key})")


@mcp.tool
async def create_mfgline(
    name: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    setup_access_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Create a new MfgLine.
    POST /api/MfgLines

    Required fields: name.
    Optional: description, notes, setup_access_name, and any additional fields via body_json
    (a JSON string merged into the payload).
    """
    payload: dict = {
        "name": name,
    }
    if description is not None:
        payload["description"] = description
    if notes is not None:
        payload["notes"] = notes
    if setup_access_name is not None:
        payload["setupAccess"] = {"name": setup_access_name}

    if body_json:
        try:
            extra = json.loads(body_json)
            payload.update(extra)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("POST", "/api/MfgLines", body=payload)


@mcp.tool
async def update_mfgline(
    key: str,
    name: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    setup_access_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing MfgLine by key.
    PUT /api/MfgLines/{key}

    Required fields: name.
    Optional: description, notes, setup_access_name, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
    }
    if description is not None:
        payload["description"] = description
    if notes is not None:
        payload["notes"] = notes
    if setup_access_name is not None:
        payload["setupAccess"] = {"name": setup_access_name}

    if body_json:
        try:
            extra = json.loads(body_json)
            payload.update(extra)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("PUT", f"/api/MfgLines/{key}", body=payload)


@mcp.tool
async def update_mfgline_by_odata_key(
    key: str,
    name: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    setup_access_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing MfgLine using OData key syntax.
    PUT /api/MfgLines({key})

    Required fields: name.
    Optional: description, notes, setup_access_name, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
    }
    if description is not None:
        payload["description"] = description
    if notes is not None:
        payload["notes"] = notes
    if setup_access_name is not None:
        payload["setupAccess"] = {"name": setup_access_name}

    if body_json:
        try:
            extra = json.loads(body_json)
            payload.update(extra)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("PUT", f"/api/MfgLines({key})", body=payload)


@mcp.tool
async def patch_mfgline(body_json: str) -> str:
    """
    Partially update (patch) a MfgLine.
    PATCH /api/MfgLines

    Provide a JSON string with the fields to update (must include at least
    the identifying fields like name).
    Returns the updated MfgLine key string on success.
    """
    try:
        payload = json.loads(body_json)
    except json.JSONDecodeError as e:
        return f"❌ Invalid body_json: {e}"

    return await request("PATCH", "/api/MfgLines", body=payload)


@mcp.tool
async def delete_mfgline(key: str) -> str:
    """
    Delete a MfgLine by key.
    DELETE /api/MfgLines/{key}
    """
    return await request("DELETE", f"/api/MfgLines/{key}")


@mcp.tool
async def delete_mfgline_by_odata_key(key: str) -> str:
    """
    Delete a MfgLine using OData key syntax.
    DELETE /api/MfgLines({key})
    """
    return await request("DELETE", f"/api/MfgLines({key})")


@mcp.tool
async def get_mfglines_count() -> str:
    """
    Get the total count of MfgLines.
    GET /api/MfgLines/$count
    Returns the count.
    """
    return await request("GET", "/api/MfgLines/$count")


@mcp.tool
async def request_mfgline_selection_values(
    selection_values_expression: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Request selection (dropdown / LOV) values for a MfgLine entity.
    POST /api/MfgLines/RequestSelectionValues

    selection_values_expression: An OData-style expression to filter
        which selection values to return (optional query parameter).
    body_json: A JSON string representing a partial MfgLineEntity whose context
        drives the selection value resolution (optional request body).
    """
    params = {}
    if selection_values_expression:
        params["selectionValuesExpression"] = selection_values_expression

    payload = None
    if body_json:
        try:
            payload = json.loads(body_json)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("POST", "/api/MfgLines/RequestSelectionValues",
                         body=payload, params=params or None)
