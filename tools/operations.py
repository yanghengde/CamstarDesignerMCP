"""
Operation 实体 MCP 工具
========================
Swagger: /api/Operations
每个 API 端点均对应一个 @mcp.tool。
"""

import json
from typing import Optional
from tools import mcp
from core.http_client import request


@mcp.tool
async def list_operations(
    filter_expr: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    select: Optional[str] = None,
    expand: Optional[str] = None,
    orderby: Optional[str] = None,
) -> str:
    """
    List Operations with optional OData querying.
    GET /api/Operations
    
    Optional OData Parameters:
      - filter_expr: example "Name eq 'ABC'"
      - top: limit number of results
      - skip: skip number of results
      - select: fields to return
      - expand: navigation properties to expand
      - orderby: order sequence, e.g., "Name desc"
      
    Returns an array of OperationEntity objects with key fields.
    Large responses are automatically trimmed.
    """
    params = {}
    if filter_expr: params["$filter"] = filter_expr
    if top is not None: params["$top"] = top
    if skip is not None: params["$skip"] = skip
    if select: params["$select"] = select
    if expand: params["$expand"] = expand
    if orderby: params["$orderby"] = orderby
    
    return await request("GET", "/api/Operations", params=params or None)


@mcp.tool
async def get_operation(key: str) -> str:
    """
    Get a single Operation by its key (instanceID or name).
    GET /api/Operations/{key}
    Returns the full OperationEntity object.
    """
    return await request("GET", f"/api/Operations/{key}")


@mcp.tool
async def get_operation_by_odata_key(key: str) -> str:
    """
    Get a single Operation using OData key syntax.
    GET /api/Operations({key})
    Example key: 'Quarantine'
    Returns the full OperationEntity object.
    """
    return await request("GET", f"/api/Operations({key})")


@mcp.tool
async def create_operation(
    name: str,
    thruput_reporting_level: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Create a new Operation.
    POST /api/Operations

    Required fields:
      - name: Operation name
      - thruput_reporting_level: Throughput reporting level name. Valid values: 'BOX', 'CARRIER', 'COMPONENT', 'PANEL', 'PCB'.
    Optional: description, notes, and any additional fields via body_json.
    """
    payload: dict = {
        "name": name,
        "thruputReportingLevel": {"name": thruput_reporting_level},
    }
    if description is not None:
        payload["description"] = description
    if notes is not None:
        payload["notes"] = notes

    if body_json:
        try:
            extra = json.loads(body_json)
            payload.update(extra)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("POST", "/api/Operations", body=payload)


@mcp.tool
async def update_operation(
    key: str,
    name: str,
    thruput_reporting_level: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing Operation by key.
    PUT /api/Operations/{key}

    Required fields: name, thruput_reporting_level (e.g., 'BOX', 'CARRIER', 'COMPONENT', 'PANEL', 'PCB').
    Optional: description, notes, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
        "thruputReportingLevel": {"name": thruput_reporting_level},
    }
    if description is not None:
        payload["description"] = description
    if notes is not None:
        payload["notes"] = notes

    if body_json:
        try:
            extra = json.loads(body_json)
            payload.update(extra)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("PUT", f"/api/Operations/{key}", body=payload)


@mcp.tool
async def update_operation_by_odata_key(
    key: str,
    name: str,
    thruput_reporting_level: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing Operation using OData key syntax.
    PUT /api/Operations({key})

    Required fields: name, thruput_reporting_level (e.g., 'BOX', 'CARRIER', 'COMPONENT', 'PANEL', 'PCB').
    Optional: description, notes, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
        "thruputReportingLevel": {"name": thruput_reporting_level},
    }
    if description is not None:
        payload["description"] = description
    if notes is not None:
        payload["notes"] = notes

    if body_json:
        try:
            extra = json.loads(body_json)
            payload.update(extra)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("PUT", f"/api/Operations({key})", body=payload)


@mcp.tool
async def delete_operation(key: str) -> str:
    """
    Delete an Operation by key.
    DELETE /api/Operations/{key}
    """
    return await request("DELETE", f"/api/Operations/{key}")


@mcp.tool
async def delete_operation_by_odata_key(key: str) -> str:
    """
    Delete an Operation using OData key syntax.
    DELETE /api/Operations({key})
    """
    return await request("DELETE", f"/api/Operations({key})")


@mcp.tool
async def get_operations_count() -> str:
    """
    Get the total count of Operations.
    GET /api/Operations/$count
    """
    return await request("GET", "/api/Operations/$count")


@mcp.tool
async def request_operation_selection_values(
    selection_values_expression: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Request selection (dropdown / LOV) values for an Operation entity.
    POST /api/Operations/RequestSelectionValues

    selection_values_expression: An OData-style expression to filter
        which selection values to return (optional query parameter).
    body_json: A JSON string representing a partial OperationEntity whose
        context drives the selection value resolution (optional request body).
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

    return await request("POST", "/api/Operations/RequestSelectionValues",
                         body=payload, params=params or None)
