"""
MfgOrder 实体 MCP 工具
=====================
Swagger: /api/MfgOrders
每个 API 端点均对应一个 @mcp.tool。
"""

import json
from typing import Optional

from tools import mcp
from core.http_client import request


_REVISIONED_REF_KEYS = {
    "instanceid": "instanceId",
    "cdotypename": "cdoTypeName",
    "name": "name",
    "revision": "revision",
    "useror": "useROR",
}


def _merge_case_insensitive(payload: dict, extra: dict) -> None:
    """Merge top-level fields without creating OData case-only duplicates."""
    canonical_keys = {key.casefold(): key for key in payload}
    for key, value in extra.items():
        target_key = canonical_keys.get(key.casefold(), key)
        payload[target_key] = value
        canonical_keys[key.casefold()] = target_key


def _normalize_revisioned_ref(value: object) -> object:
    """Normalize a revisioned reference to the casing declared by Swagger."""
    if not isinstance(value, dict):
        return value

    normalized: dict = {}
    for key, item in value.items():
        target_key = _REVISIONED_REF_KEYS.get(key.casefold(), key)
        normalized[target_key] = item
    return normalized


def _merge_body_json(payload: dict, body_json: Optional[str]) -> Optional[str]:
    """Parse and merge an optional JSON object, returning an error if invalid."""
    if not body_json:
        return None

    try:
        extra = json.loads(body_json)
    except json.JSONDecodeError as exc:
        return f"❌ Invalid body_json: {exc}"

    if not isinstance(extra, dict):
        return "❌ Invalid body_json: expected a JSON object."

    _merge_case_insensitive(payload, extra)
    if "product" in payload:
        payload["product"] = _normalize_revisioned_ref(payload["product"])
    return None


@mcp.tool
async def list_mfgorders(
    filter_expr: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    select: Optional[str] = None,
    expand: Optional[str] = None,
    orderby: Optional[str] = None,
) -> str:
    """
    List MfgOrders with optional OData querying.
    GET /api/MfgOrders

    Optional OData Parameters:
      - filter_expr: example "Name eq 'MO-00123'"
      - top: limit number of results
      - skip: skip number of results
      - select: fields to return
      - expand: navigation properties to expand
      - orderby: order sequence, e.g., "Name desc"

    Returns an array of MfgOrderEntity objects. Large responses are automatically trimmed.
    """
    params = {}
    if filter_expr: params["$filter"] = filter_expr
    if top is not None: params["$top"] = top
    if skip is not None: params["$skip"] = skip
    if select: params["$select"] = select
    if expand: params["$expand"] = expand
    if orderby: params["$orderby"] = orderby

    return await request("GET", "/api/MfgOrders", params=params or None)


@mcp.tool
async def get_mfgorder(key: str) -> str:
    """
    Get a single MfgOrder by its key (instanceID or name).
    GET /api/MfgOrders/{key}
    Returns the full MfgOrderEntity object.
    """
    return await request("GET", f"/api/MfgOrders/{key}")


@mcp.tool
async def get_mfgorder_by_odata_key(key: str) -> str:
    """
    Get a single MfgOrder using OData key syntax.
    GET /api/MfgOrders({key})
    Example key: 'MO-00123'
    Returns the full MfgOrderEntity object.
    """
    return await request("GET", f"/api/MfgOrders({key})")


@mcp.tool
async def create_mfgorder(
    name: str,
    product_name: str,
    product_revision: str,
    qty: float,
    description: Optional[str] = None,
    order_status_name: Optional[str] = None,
    order_type_name: Optional[str] = None,
    priority_name: Optional[str] = None,
    planned_start_date: Optional[str] = None,
    planned_completion_date: Optional[str] = None,
    release_date: Optional[str] = None,
    uom_name: Optional[str] = None,
    mfg_line_name: Optional[str] = None,
    reporting_factory_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Create a new MfgOrder.
    POST /api/MfgOrders

    Required fields: name, product_name, product_revision, qty.
    Optional: description, orderStatus, orderType, priority, dates,
    UOM, mfgLine, reportingFactory, and any additional fields via body_json
    (a JSON string merged into the payload).
    """
    payload: dict = {
        "name": name,
        "product": {"name": product_name, "revision": product_revision},
        "qty": qty,
    }
    if description is not None:
        payload["description"] = description
    if order_status_name is not None:
        payload["orderStatus"] = {"name": order_status_name}
    if order_type_name is not None:
        payload["orderType"] = {"name": order_type_name}
    if priority_name is not None:
        payload["priority"] = {"name": priority_name}
    if planned_start_date is not None:
        payload["plannedStartDate"] = planned_start_date
    if planned_completion_date is not None:
        payload["plannedCompletionDate"] = planned_completion_date
    if release_date is not None:
        payload["releaseDate"] = release_date
    if uom_name is not None:
        payload["uom"] = {"name": uom_name}
    if mfg_line_name is not None:
        payload["mfgLine"] = {"name": mfg_line_name}
    if reporting_factory_name is not None:
        payload["reportingFactory"] = {"name": reporting_factory_name}

    merge_error = _merge_body_json(payload, body_json)
    if merge_error:
        return merge_error

    return await request("POST", "/api/MfgOrders", body=payload)


@mcp.tool
async def update_mfgorder(
    key: str,
    name: str,
    product_name: str,
    product_revision: str,
    qty: float,
    description: Optional[str] = None,
    order_status_name: Optional[str] = None,
    order_type_name: Optional[str] = None,
    priority_name: Optional[str] = None,
    planned_start_date: Optional[str] = None,
    planned_completion_date: Optional[str] = None,
    release_date: Optional[str] = None,
    uom_name: Optional[str] = None,
    mfg_line_name: Optional[str] = None,
    reporting_factory_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing MfgOrder by key.
    PUT /api/MfgOrders/{key}

    Required fields: name, product_name, product_revision, qty.
    Optional: description, orderStatus, orderType, priority, dates,
    UOM, mfgLine, reportingFactory, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
        "product": {"name": product_name, "revision": product_revision},
        "qty": qty,
    }
    if description is not None:
        payload["description"] = description
    if order_status_name is not None:
        payload["orderStatus"] = {"name": order_status_name}
    if order_type_name is not None:
        payload["orderType"] = {"name": order_type_name}
    if priority_name is not None:
        payload["priority"] = {"name": priority_name}
    if planned_start_date is not None:
        payload["plannedStartDate"] = planned_start_date
    if planned_completion_date is not None:
        payload["plannedCompletionDate"] = planned_completion_date
    if release_date is not None:
        payload["releaseDate"] = release_date
    if uom_name is not None:
        payload["uom"] = {"name": uom_name}
    if mfg_line_name is not None:
        payload["mfgLine"] = {"name": mfg_line_name}
    if reporting_factory_name is not None:
        payload["reportingFactory"] = {"name": reporting_factory_name}

    merge_error = _merge_body_json(payload, body_json)
    if merge_error:
        return merge_error

    return await request("PUT", f"/api/MfgOrders/{key}", body=payload)


@mcp.tool
async def update_mfgorder_by_odata_key(
    key: str,
    name: str,
    product_name: str,
    product_revision: str,
    qty: float,
    description: Optional[str] = None,
    order_status_name: Optional[str] = None,
    order_type_name: Optional[str] = None,
    priority_name: Optional[str] = None,
    planned_start_date: Optional[str] = None,
    planned_completion_date: Optional[str] = None,
    release_date: Optional[str] = None,
    uom_name: Optional[str] = None,
    mfg_line_name: Optional[str] = None,
    reporting_factory_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing MfgOrder using OData key syntax.
    PUT /api/MfgOrders({key})

    Required fields: name, product_name, product_revision, qty.
    Optional: description, orderStatus, orderType, priority, dates,
    UOM, mfgLine, reportingFactory, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
        "product": {"name": product_name, "revision": product_revision},
        "qty": qty,
    }
    if description is not None:
        payload["description"] = description
    if order_status_name is not None:
        payload["orderStatus"] = {"name": order_status_name}
    if order_type_name is not None:
        payload["orderType"] = {"name": order_type_name}
    if priority_name is not None:
        payload["priority"] = {"name": priority_name}
    if planned_start_date is not None:
        payload["plannedStartDate"] = planned_start_date
    if planned_completion_date is not None:
        payload["plannedCompletionDate"] = planned_completion_date
    if release_date is not None:
        payload["releaseDate"] = release_date
    if uom_name is not None:
        payload["uom"] = {"name": uom_name}
    if mfg_line_name is not None:
        payload["mfgLine"] = {"name": mfg_line_name}
    if reporting_factory_name is not None:
        payload["reportingFactory"] = {"name": reporting_factory_name}

    merge_error = _merge_body_json(payload, body_json)
    if merge_error:
        return merge_error

    return await request("PUT", f"/api/MfgOrders({key})", body=payload)


@mcp.tool
async def patch_mfgorder(body_json: str) -> str:
    """
    Partially update (patch) a MfgOrder.
    PATCH /api/MfgOrders

    Provide a JSON string with the fields to update (must include at least
    the identifying fields like name).
    Returns the updated MfgOrder key string on success.
    """
    try:
        payload = json.loads(body_json)
    except json.JSONDecodeError as e:
        return f"❌ Invalid body_json: {e}"

    return await request("PATCH", "/api/MfgOrders", body=payload)


@mcp.tool
async def delete_mfgorder(key: str) -> str:
    """
    Delete a MfgOrder by key.
    DELETE /api/MfgOrders/{key}
    """
    return await request("DELETE", f"/api/MfgOrders/{key}")


@mcp.tool
async def delete_mfgorder_by_odata_key(key: str) -> str:
    """
    Delete a MfgOrder using OData key syntax.
    DELETE /api/MfgOrders({key})
    """
    return await request("DELETE", f"/api/MfgOrders({key})")


@mcp.tool
async def get_mfgorders_count() -> str:
    """
    Get the total count of MfgOrders.
    GET /api/MfgOrders/$count
    Returns the count (or a list, depending on the server implementation).
    """
    return await request("GET", "/api/MfgOrders/$count")


@mcp.tool
async def request_mfgorder_selection_values(
    selection_values_expression: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Request selection (dropdown / LOV) values for a MfgOrder entity.
    POST /api/MfgOrders/RequestSelectionValues

    selection_values_expression: An OData-style expression to filter
        which selection values to return (optional query parameter).
    body_json: A JSON string representing a partial MfgOrderEntity whose context
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

    return await request("POST", "/api/MfgOrders/RequestSelectionValues",
                         body=payload, params=params or None)
