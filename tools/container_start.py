"""
ContainerStart MCP tool
=======================
Swagger: Shopfloor /api/Start

Starts a new Camstar Container for a manufacturing order. For unit-level
tracking, the container name is normally the product serial number.
"""

import json
from typing import Optional

from core.http_client import request_shopfloor
from tools import mcp


_START_KEYS = {
    "comments": "comments",
    "currentstatusdetails": "currentStatusDetails",
    "details": "details",
    "factory": "factory",
}

_START_DETAILS_KEYS = {
    "autonumber": "autoNumber",
    "autonumberrule": "autoNumberRule",
    "containername": "containerName",
    "level": "level",
    "mfgorder": "mfgOrder",
    "owner": "owner",
    "product": "product",
    "qty": "qty",
    "startreason": "startReason",
    "uom": "uom",
}

_REFERENCE_KEYS = {
    "name": "name",
    "revision": "revision",
    "useror": "useROR",
    "instanceid": "instanceId",
    "cdotypename": "cdoTypeName",
}


def _merge_case_insensitive(
    target: dict,
    source: dict,
    known_keys: Optional[dict[str, str]] = None,
) -> None:
    """Recursively merge dictionaries without OData case-only duplicates."""
    canonical_keys = {key.casefold(): key for key in target}
    for key, value in source.items():
        folded_key = key.casefold()
        target_key = canonical_keys.get(
            folded_key,
            (known_keys or {}).get(folded_key, key),
        )
        current_value = target.get(target_key)
        if isinstance(current_value, dict) and isinstance(value, dict):
            child_keys = _REFERENCE_KEYS
            if target_key == "details":
                child_keys = _START_DETAILS_KEYS
            _merge_case_insensitive(current_value, value, child_keys)
        else:
            target[target_key] = value
        canonical_keys[folded_key] = target_key


@mcp.tool
async def container_start(
    mfg_order_name: str,
    product_name: str,
    product_revision: str,
    qty: float,
    level_name: str,
    owner_name: str,
    start_reason_name: str,
    container_name: Optional[str] = None,
    auto_number_rule_name: Optional[str] = None,
    uom_name: Optional[str] = None,
    workflow_name: Optional[str] = None,
    workflow_revision: Optional[str] = None,
    factory_name: Optional[str] = None,
    comments: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Start a Container (serial number) against a manufacturing order.
    POST Shopfloor /api/Start

    Required:
      - mfg_order_name: Manufacturing order name.
      - product_name and product_revision: Revisioned Product reference.
      - qty: Initial quantity in the Container. Use 1 for a unit serial number.
      - level_name: Camstar Container Level, for example "Unit".
      - owner_name: Owner resolved through Details.Owner selection values.
      - start_reason_name: Start reason resolved through Details.StartReason.

    Numbering (choose exactly one):
      - container_name: Explicit Container/serial number.
      - auto_number_rule_name: Confirms contextual automatic numbering was
        validated. The rule name is not written to Start because this server
        rejects direct writes to details.autoNumberRule.

    Before starting, use get_container_level to validate level_name. For
    automatic numbering, resolve Details.AutoNumberRule with
    request_container_start_selection_values in the complete Start context;
    global get_numbering_rule existence alone is insufficient.

    owner_name and start_reason_name are required by this server and must be
    resolved with read-only RequestSelectionValues validation.

    body_json must be a JSON object and is recursively merged into the Start
    payload using case-insensitive keys. Required fields are revalidated after
    the merge.
    """
    has_container_name = bool(container_name and container_name.strip())
    has_number_rule = bool(
        auto_number_rule_name and auto_number_rule_name.strip()
    )
    if has_container_name == has_number_rule:
        return (
            "❌ Provide exactly one of container_name or "
            "auto_number_rule_name."
        )
    if qty <= 0:
        return "❌ qty must be greater than 0."

    details: dict = {
        "mfgOrder": {"name": mfg_order_name},
        "product": {
            "name": product_name,
            "revision": product_revision,
        },
        "qty": qty,
        "level": {"name": level_name},
    }
    if has_container_name:
        details["containerName"] = container_name.strip()
        details["autoNumber"] = False
    else:
        details["autoNumber"] = True
    if uom_name is not None:
        details["uom"] = {"name": uom_name}
    if workflow_name is not None:
        if not workflow_revision:
            return (
                "❌ workflow_revision is required when workflow_name is "
                "provided."
            )
        payload_workflow = {
            "workflow": {
                "name": workflow_name,
                "revision": workflow_revision,
            }
        }

    payload: dict = {"details": details}
    if workflow_name is not None:
        payload["currentStatusDetails"] = payload_workflow
    if factory_name is not None:
        payload["factory"] = {"name": factory_name}
    if owner_name:
        details["owner"] = {"name": owner_name}
    if start_reason_name:
        details["startReason"] = {"name": start_reason_name}
    if comments is not None:
        payload["comments"] = comments

    if body_json:
        try:
            extra = json.loads(body_json)
        except json.JSONDecodeError as exc:
            return f"❌ Invalid body_json: {exc}"
        if not isinstance(extra, dict):
            return "❌ Invalid body_json: expected a JSON object."
        _merge_case_insensitive(payload, extra, _START_KEYS)

    validation_error = _validate_start_payload(payload)
    if validation_error:
        return f"❌ {validation_error}"

    return await request_shopfloor("POST", "/api/Start", body=payload)


def _reference_name(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("name") or "").strip()


def _validate_start_payload(payload: dict) -> Optional[str]:
    """Revalidate server-required Start fields after advanced JSON merging."""
    details = payload.get("details")
    if not isinstance(details, dict):
        return "details must be a JSON object."

    if not _reference_name(details.get("mfgOrder")):
        return "MfgOrder name is required."
    if not _reference_name(details.get("level")):
        return "Container Level name is required."

    product = details.get("product")
    if not _reference_name(product):
        return "Product name is required."
    if not isinstance(product, dict) or not str(
        product.get("revision") or ""
    ).strip():
        return "Product Revision is required."

    qty = details.get("qty")
    if not isinstance(qty, (int, float)) or isinstance(qty, bool) or qty <= 0:
        return "qty must be greater than 0."
    if not _reference_name(details.get("owner")):
        return (
            "Owner is required. Resolve Details.Owner with "
            "request_container_start_selection_values first."
        )
    if not _reference_name(details.get("startReason")):
        return (
            "StartReason is required. Resolve Details.StartReason with "
            "request_container_start_selection_values first."
        )

    container_name = str(details.get("containerName") or "").strip()
    auto_number = details.get("autoNumber") is True
    if bool(container_name) == auto_number:
        return (
            "Choose exactly one numbering mode: explicit containerName or "
            "contextual autoNumber=true."
        )
    if "autoNumberRule" in details:
        return (
            "Do not write details.autoNumberRule on this server; validate the "
            "contextual rule with RequestSelectionValues and use autoNumber=true."
        )
    return None


@mcp.tool
async def request_container_start_selection_values(
    selection_values_expression: str,
    mfg_order_name: Optional[str] = None,
    product_name: Optional[str] = None,
    product_revision: Optional[str] = None,
    qty: Optional[float] = None,
    level_name: Optional[str] = None,
    owner_name: Optional[str] = None,
    start_reason_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """Resolve context-sensitive Start reference values without creating data.

    POST Shopfloor /api/Start/RequestSelectionValues

    Typical expressions are Details.Owner, Details.StartReason, and
    Details.AutoNumberRule. Supply as much known Start context as possible.
    This is a read-only preflight tool and must be preferred over probing the
    live Start transaction.
    """
    if not selection_values_expression.strip():
        return "❌ selection_values_expression is required."

    details: dict = {}
    if mfg_order_name:
        details["mfgOrder"] = {"name": mfg_order_name}
    if product_name:
        product = {"name": product_name}
        if product_revision:
            product["revision"] = product_revision
        details["product"] = product
    if qty is not None:
        details["qty"] = qty
    if level_name:
        details["level"] = {"name": level_name}
    if owner_name:
        details["owner"] = {"name": owner_name}
    if start_reason_name:
        details["startReason"] = {"name": start_reason_name}

    payload: dict = {"details": details}
    if body_json:
        try:
            extra = json.loads(body_json)
        except json.JSONDecodeError as exc:
            return f"❌ Invalid body_json: {exc}"
        if not isinstance(extra, dict):
            return "❌ Invalid body_json: expected a JSON object."
        _merge_case_insensitive(payload, extra, _START_KEYS)

    return await request_shopfloor(
        "POST",
        "/api/Start/RequestSelectionValues",
        body=payload,
        params={
            "selectionValuesExpression": selection_values_expression.strip()
        },
    )
