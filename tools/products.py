"""
Product 实体 MCP 工具
====================
Swagger: /api/Products
每个 API 端点均对应一个 @mcp.tool。
"""

import json
from typing import Optional

from tools import mcp
from core.http_client import request


@mcp.tool
async def list_products(
    filter_expr: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    select: Optional[str] = None,
    expand: Optional[str] = None,
    orderby: Optional[str] = None,
) -> str:
    """
    List Products with optional OData querying.
    GET /api/Products
    
    Optional OData Parameters:
      - filter_expr: example "Name eq 'ABC'"
      - top: limit number of results
      - skip: skip number of results
      - select: fields to return
      - expand: navigation properties to expand
      - orderby: order sequence, e.g., "Name desc"
      
    Returns an array of ProductEntity objects. Large responses are automatically trimmed.
    """
    params = {}
    if filter_expr: params["$filter"] = filter_expr
    if top is not None: params["$top"] = top
    if skip is not None: params["$skip"] = skip
    if select: params["$select"] = select
    if expand: params["$expand"] = expand
    if orderby: params["$orderby"] = orderby
    
    return await request("GET", "/api/Products", params=params or None)


@mcp.tool
async def get_product(key: str) -> str:
    """
    Get a single Product by its key (instanceID or name:revision).
    GET /api/Products/{key}
    Returns the full ProductEntity object.
    """
    return await request("GET", f"/api/Products/{key}")


@mcp.tool
async def get_product_by_odata_key(key: str) -> str:
    """
    Get a single Product using OData key syntax.
    GET /api/Products({key})
    Example key: 'MyProduct:001'
    Returns the full ProductEntity object.
    """
    return await request("GET", f"/api/Products({key})")


@mcp.tool
async def create_product(
    name: str,
    revision: str,
    product_type_name: str,
    description: Optional[str] = None,
    status: Optional[int] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Create a new Product.
    POST /api/Products

    Required fields: name, revision, productType (by name).
    Optional: description, status (1=Active, 2=Inactive), and any
    additional fields via body_json (a JSON string merged into the payload).
    """
    payload: dict = {
        "name": name,
        "revision": revision,
        "productType": {"name": product_type_name},
    }
    if description is not None:
        payload["description"] = description
    if status is not None:
        payload["status"] = status

    # Merge extra fields from caller
    if body_json:
        try:
            extra = json.loads(body_json)
            payload.update(extra)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("POST", "/api/Products", body=payload)


@mcp.tool
async def update_product(
    key: str,
    name: str,
    revision: str,
    product_type_name: str,
    description: Optional[str] = None,
    status: Optional[int] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing Product by key.
    PUT /api/Products/{key}

    Required fields: name, revision, productType (by name).
    Optional: description, status, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
        "revision": revision,
        "productType": {"name": product_type_name},
    }
    if description is not None:
        payload["description"] = description
    if status is not None:
        payload["status"] = status

    if body_json:
        try:
            extra = json.loads(body_json)
            payload.update(extra)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("PUT", f"/api/Products/{key}", body=payload)


@mcp.tool
async def update_product_by_odata_key(
    key: str,
    name: str,
    revision: str,
    product_type_name: str,
    description: Optional[str] = None,
    status: Optional[int] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing Product using OData key syntax.
    PUT /api/Products({key})

    Required fields: name, revision, productType (by name).
    Optional: description, status, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
        "revision": revision,
        "productType": {"name": product_type_name},
    }
    if description is not None:
        payload["description"] = description
    if status is not None:
        payload["status"] = status

    if body_json:
        try:
            extra = json.loads(body_json)
            payload.update(extra)
        except json.JSONDecodeError as e:
            return f"❌ Invalid body_json: {e}"

    return await request("PUT", f"/api/Products({key})", body=payload)


@mcp.tool
async def patch_product(body_json: str) -> str:
    """
    Partially update (patch) a Product.
    PATCH /api/Products

    Provide a JSON string with the fields to update (must include at least
    the identifying fields like name + revision).
    Returns the updated Product key string on success.
    """
    try:
        payload = json.loads(body_json)
    except json.JSONDecodeError as e:
        return f"❌ Invalid body_json: {e}"

    return await request("PATCH", "/api/Products", body=payload)


@mcp.tool
async def delete_product(key: str) -> str:
    """
    Delete a Product by key.
    DELETE /api/Products/{key}
    """
    return await request("DELETE", f"/api/Products/{key}")


@mcp.tool
async def delete_product_by_odata_key(key: str) -> str:
    """
    Delete a Product using OData key syntax.
    DELETE /api/Products({key})
    """
    return await request("DELETE", f"/api/Products({key})")


@mcp.tool
async def get_products_count() -> str:
    """
    Get the total count of Products.
    GET /api/Products/$count
    Returns the count (or a list, depending on the server implementation).
    """
    return await request("GET", "/api/Products/$count")


@mcp.tool
async def request_product_selection_values(
    selection_values_expression: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Request selection (dropdown / LOV) values for a Product entity.
    POST /api/Products/RequestSelectionValues

    selection_values_expression: An OData-style expression to filter
        which selection values to return (optional query parameter).
    body_json: A JSON string representing a partial ProductEntity whose context
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

    return await request("POST", "/api/Products/RequestSelectionValues",
                         body=payload, params=params or None)
