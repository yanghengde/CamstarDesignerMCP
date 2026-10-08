"""
ProductType 实体 MCP 工具
=========================
Swagger: /api/ProductTypes
每个 API 端点均对应一个 @mcp.tool。
"""

import json
from typing import Optional

from tools import mcp
from core.http_client import request


@mcp.tool
async def list_producttypes(
    filter_expr: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    select: Optional[str] = None,
    expand: Optional[str] = None,
    orderby: Optional[str] = None,
) -> str:
    """
    List ProductTypes with optional OData querying.
    GET /api/ProductTypes

    Optional OData Parameters:
      - filter_expr: example "Name eq 'FinishedGood'"
      - top: limit number of results
      - skip: skip number of results
      - select: fields to return
      - expand: navigation properties to expand
      - orderby: order sequence, e.g., "Name desc"

    Returns an array of ProductTypeEntity objects. Large responses are automatically trimmed.
    """
    params = {}
    if filter_expr: params["$filter"] = filter_expr
    if top is not None: params["$top"] = top
    if skip is not None: params["$skip"] = skip
    if select: params["$select"] = select
    if expand: params["$expand"] = expand
    if orderby: params["$orderby"] = orderby

    return await request("GET", "/api/ProductTypes", params=params or None)


@mcp.tool
async def get_producttype(key: str) -> str:
    """
    Get a single ProductType by its key (instanceID or name).
    GET /api/ProductTypes/{key}
    Returns the full ProductTypeEntity object.
    """
    return await request("GET", f"/api/ProductTypes/{key}")


@mcp.tool
async def get_producttype_by_odata_key(key: str) -> str:
    """
    Get a single ProductType using OData key syntax.
    GET /api/ProductTypes({key})
    Example key: 'FinishedGood'
    Returns the full ProductTypeEntity object.
    """
    return await request("GET", f"/api/ProductTypes({key})")


@mcp.tool
async def create_producttype(
    name: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    setup_access_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Create a new ProductType.
    POST /api/ProductTypes

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

    return await request("POST", "/api/ProductTypes", body=payload)


@mcp.tool
async def update_producttype(
    key: str,
    name: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    setup_access_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing ProductType by key.
    PUT /api/ProductTypes/{key}

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

    return await request("PUT", f"/api/ProductTypes/{key}", body=payload)


@mcp.tool
async def update_producttype_by_odata_key(
    key: str,
    name: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    setup_access_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing ProductType using OData key syntax.
    PUT /api/ProductTypes({key})

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

    return await request("PUT", f"/api/ProductTypes({key})", body=payload)


@mcp.tool
async def patch_producttype(body_json: str) -> str:
    """
    Partially update (patch) a ProductType.
    PATCH /api/ProductTypes

    Provide a JSON string with the fields to update (must include at least
    the identifying fields like name).
    Returns the updated ProductType key string on success.
    """
    try:
        payload = json.loads(body_json)
    except json.JSONDecodeError as e:
        return f"❌ Invalid body_json: {e}"

    return await request("PATCH", "/api/ProductTypes", body=payload)


@mcp.tool
async def delete_producttype(key: str) -> str:
    """
    Delete a ProductType by key.
    DELETE /api/ProductTypes/{key}
    """
    return await request("DELETE", f"/api/ProductTypes/{key}")


@mcp.tool
async def delete_producttype_by_odata_key(key: str) -> str:
    """
    Delete a ProductType using OData key syntax.
    DELETE /api/ProductTypes({key})
    """
    return await request("DELETE", f"/api/ProductTypes({key})")


@mcp.tool
async def get_producttypes_count() -> str:
    """
    Get the total count of ProductTypes.
    GET /api/ProductTypes/$count
    Returns the count.
    """
    return await request("GET", "/api/ProductTypes/$count")


@mcp.tool
async def request_producttype_selection_values(
    selection_values_expression: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Request selection (dropdown / LOV) values for a ProductType entity.
    POST /api/ProductTypes/RequestSelectionValues

    selection_values_expression: An OData-style expression to filter
        which selection values to return (optional query parameter).
    body_json: A JSON string representing a partial ProductTypeEntity whose context
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

    return await request("POST", "/api/ProductTypes/RequestSelectionValues",
                         body=payload, params=params or None)
