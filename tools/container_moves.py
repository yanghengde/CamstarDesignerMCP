"""
Container movement MCP tools
============================
Swagger: Shopfloor /api/MoveStd, /api/MoveIn, /api/MoveOut
"""

import json
from typing import Optional

from core.http_client import request_shopfloor
from tools import mcp


_MOVE_KEYS = {
    "close": "close",
    "comments": "comments",
    "container": "container",
    "moveallqty": "moveAllQty",
    "path": "path",
    "qty": "qty",
    "resource": "resource",
    "thruputallqty": "thruputAllQty",
    "toresource": "toResource",
}

_REFERENCE_KEYS = {
    "cdotypename": "cdoTypeName",
    "instanceid": "instanceId",
    "level": "level",
    "name": "name",
}


def _merge_case_insensitive(
    target: dict,
    source: dict,
    known_keys: Optional[dict[str, str]] = None,
) -> None:
    """Recursively merge JSON fields without OData case-only duplicates."""
    canonical_keys = {key.casefold(): key for key in target}
    for key, value in source.items():
        folded_key = key.casefold()
        target_key = canonical_keys.get(
            folded_key,
            (known_keys or {}).get(folded_key, key),
        )
        current_value = target.get(target_key)
        if isinstance(current_value, dict) and isinstance(value, dict):
            _merge_case_insensitive(
                current_value,
                value,
                _REFERENCE_KEYS,
            )
        elif isinstance(value, dict) and target_key in {
            "container",
            "level",
            "path",
            "resource",
            "toResource",
        }:
            normalized_value: dict = {}
            _merge_case_insensitive(
                normalized_value,
                value,
                _REFERENCE_KEYS,
            )
            target[target_key] = normalized_value
        else:
            target[target_key] = value
        canonical_keys[folded_key] = target_key


def _container_ref(name: str, level_name: Optional[str]) -> dict:
    reference: dict = {"name": name.strip()}
    if level_name and level_name.strip():
        reference["level"] = {"name": level_name.strip()}
    return reference


def _apply_quantity(
    payload: dict,
    move_all_qty: bool,
    qty: Optional[float],
) -> Optional[str]:
    payload["moveAllQty"] = move_all_qty
    if move_all_qty:
        if qty is not None:
            return "❌ qty must be omitted when move_all_qty is true."
        return None
    if qty is None or qty <= 0:
        return "❌ qty must be greater than 0 when move_all_qty is false."
    payload["qty"] = qty
    return None


def _merge_body_json(payload: dict, body_json: Optional[str]) -> Optional[str]:
    if not body_json:
        return None
    try:
        extra = json.loads(body_json)
    except json.JSONDecodeError as exc:
        return f"❌ Invalid body_json: {exc}"
    if not isinstance(extra, dict):
        return "❌ Invalid body_json: expected a JSON object."
    _merge_case_insensitive(payload, extra, _MOVE_KEYS)
    return None


def _add_common_fields(
    payload: dict,
    resource_name: Optional[str],
    comments: Optional[str],
) -> None:
    if resource_name:
        payload["resource"] = {"name": resource_name}
    if comments is not None:
        payload["comments"] = comments


@mcp.tool
async def container_move(
    container_name: str,
    container_level_name: Optional[str] = None,
    move_all_qty: bool = True,
    qty: Optional[float] = None,
    path_name: Optional[str] = None,
    resource_name: Optional[str] = None,
    to_resource_name: Optional[str] = None,
    close: bool = False,
    comments: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Move a Container through its standard workflow path.
    POST Shopfloor /api/MoveStd

    container_name is required. container_level_name should be supplied when
    Container names are not globally unique. By default the entire quantity
    follows the default path. For a partial move set move_all_qty=false and
    provide qty. Use body_json for advanced MoveStd fields.
    """
    if not container_name.strip():
        return "❌ container_name must not be empty."

    payload: dict = {
        "container": _container_ref(container_name, container_level_name),
        "close": close,
    }
    quantity_error = _apply_quantity(payload, move_all_qty, qty)
    if quantity_error:
        return quantity_error
    if path_name:
        payload["path"] = {"name": path_name}
    _add_common_fields(payload, resource_name, comments)
    if to_resource_name:
        payload["toResource"] = {"name": to_resource_name}

    merge_error = _merge_body_json(payload, body_json)
    if merge_error:
        return merge_error
    return await request_shopfloor("POST", "/api/MoveStd", body=payload)


@mcp.tool
async def container_move_in(
    container_name: str,
    container_level_name: Optional[str] = None,
    resource_name: Optional[str] = None,
    comments: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Move a Container into processing at its current workflow step.
    POST Shopfloor /api/MoveIn

    container_name is required. Supply resource_name when the operation or
    dispatch configuration requires a resource. Use body_json for data
    collection, electronic signatures, or other advanced MoveIn fields.
    """
    if not container_name.strip():
        return "❌ container_name must not be empty."

    payload: dict = {
        "container": _container_ref(container_name, container_level_name),
    }
    _add_common_fields(payload, resource_name, comments)
    merge_error = _merge_body_json(payload, body_json)
    if merge_error:
        return merge_error
    return await request_shopfloor("POST", "/api/MoveIn", body=payload)


@mcp.tool
async def container_move_out(
    container_name: str,
    container_level_name: Optional[str] = None,
    move_all_qty: bool = True,
    qty: Optional[float] = None,
    thruput_all_qty: bool = True,
    path_name: Optional[str] = None,
    resource_name: Optional[str] = None,
    to_resource_name: Optional[str] = None,
    close: bool = False,
    comments: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Move a Container out of processing and onward through its workflow.
    POST Shopfloor /api/MoveOut

    By default all Container quantity and throughput quantity are processed.
    For a partial move set move_all_qty=false and provide qty. Use body_json
    for advanced MoveOut fields such as throughput details or data collection.
    """
    if not container_name.strip():
        return "❌ container_name must not be empty."

    payload: dict = {
        "container": _container_ref(container_name, container_level_name),
        "thruputAllQty": thruput_all_qty,
        "close": close,
    }
    quantity_error = _apply_quantity(payload, move_all_qty, qty)
    if quantity_error:
        return quantity_error
    if path_name:
        payload["path"] = {"name": path_name}
    _add_common_fields(payload, resource_name, comments)
    if to_resource_name:
        payload["toResource"] = {"name": to_resource_name}

    merge_error = _merge_body_json(payload, body_json)
    if merge_error:
        return merge_error
    return await request_shopfloor("POST", "/api/MoveOut", body=payload)
