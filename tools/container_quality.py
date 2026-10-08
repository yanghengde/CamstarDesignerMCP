"""
Container quality MCP tools
===========================
Swagger: Shopfloor /api/ContainerDefect and /api/Rework
"""

import json
from typing import Optional

from core.http_client import request_shopfloor
from tools import mcp


_FIELD_KEYS = {
    "cdotypename": "cdoTypeName",
    "close": "close",
    "comment": "comment",
    "comments": "comments",
    "container": "container",
    "containerlevelinspected": "containerLevelInspected",
    "containersinspected": "containersInspected",
    "defectcount": "defectCount",
    "endreworkstep": "endReworkStep",
    "endreworkworkflow": "endReworkWorkflow",
    "instanceid": "instanceId",
    "level": "level",
    "moveallqty": "moveAllQty",
    "name": "name",
    "parent": "parent",
    "qty": "qty",
    "qty2": "qty2",
    "qty2inspected": "qty2Inspected",
    "qtyinspected": "qtyInspected",
    "reasoncode": "reasonCode",
    "reentrystep": "reEntryStep",
    "reentryworkflow": "reEntryWorkflow",
    "resource": "resource",
    "revision": "revision",
    "reworkreason": "reworkReason",
    "servicedetails": "serviceDetails",
    "tostep": "toStep",
    "toresource": "toResource",
    "toworkflow": "toWorkflow",
    "usereentryworkflowstack": "useReEntryWorkflowStack",
    "useror": "useROR",
}


def _normalize_json(value):
    """Normalize known Swagger fields recursively to their canonical casing."""
    if isinstance(value, dict):
        normalized = {}
        for key, child in value.items():
            canonical = _FIELD_KEYS.get(key.casefold(), key)
            normalized[canonical] = _normalize_json(child)
        return normalized
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    return value


def _merge_case_insensitive(target: dict, source: dict) -> None:
    """Recursively merge JSON fields without case-only duplicates."""
    normalized_source = _normalize_json(source)
    canonical_keys = {key.casefold(): key for key in target}
    for key, value in normalized_source.items():
        folded_key = key.casefold()
        target_key = canonical_keys.get(folded_key, key)
        current_value = target.get(target_key)
        if isinstance(current_value, dict) and isinstance(value, dict):
            _merge_case_insensitive(current_value, value)
        else:
            target[target_key] = value
        canonical_keys[folded_key] = target_key


def _merge_body_json(payload: dict, body_json: Optional[str]) -> Optional[str]:
    if not body_json:
        return None
    try:
        extra = json.loads(body_json)
    except json.JSONDecodeError as exc:
        return f"❌ Invalid body_json: {exc}"
    if not isinstance(extra, dict):
        return "❌ Invalid body_json: expected a JSON object."
    _merge_case_insensitive(payload, extra)
    return None


def _container_ref(name: str, level_name: Optional[str]) -> dict:
    reference: dict = {"name": name.strip()}
    if level_name and level_name.strip():
        reference["level"] = {"name": level_name.strip()}
    return reference


def _named_ref(name: str) -> dict:
    return {"name": name.strip()}


def _revisioned_ref(name: str, revision: str) -> dict:
    return {"name": name.strip(), "revision": revision.strip()}


def _validate_revision_pair(
    label: str,
    name: Optional[str],
    revision: Optional[str],
) -> Optional[str]:
    has_name = bool(name and name.strip())
    has_revision = bool(revision and revision.strip())
    if has_name != has_revision:
        return (
            f"❌ {label}_name and {label}_revision must be provided "
            "together."
        )
    return None


@mcp.tool
async def container_defect(
    container_name: str,
    reason_code_name: str,
    defect_count: int,
    container_level_name: Optional[str] = None,
    detail_comment: Optional[str] = None,
    comments: Optional[str] = None,
    resource_name: Optional[str] = None,
    qty_inspected: Optional[float] = None,
    qty2_inspected: Optional[float] = None,
    containers_inspected: Optional[int] = None,
    container_level_inspected_name: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Record a defect against a Container.
    POST Shopfloor /api/ContainerDefect

    The required service detail is built from reason_code_name and
    defect_count. Use body_json to replace serviceDetails with multiple defect
    details or to supply advanced fields from the Shopfloor Swagger schema.
    """
    if not container_name or not container_name.strip():
        return "❌ container_name must not be empty."
    if not reason_code_name or not reason_code_name.strip():
        return "❌ reason_code_name must not be empty."
    if defect_count <= 0:
        return "❌ defect_count must be greater than 0."
    if qty_inspected is not None and qty_inspected < 0:
        return "❌ qty_inspected must not be negative."
    if qty2_inspected is not None and qty2_inspected < 0:
        return "❌ qty2_inspected must not be negative."
    if containers_inspected is not None and containers_inspected < 0:
        return "❌ containers_inspected must not be negative."

    detail: dict = {
        "reasonCode": _named_ref(reason_code_name),
        "defectCount": defect_count,
    }
    if detail_comment is not None:
        detail["comment"] = detail_comment

    payload: dict = {
        "container": _container_ref(container_name, container_level_name),
        "serviceDetails": [detail],
    }
    if comments is not None:
        payload["comments"] = comments
    if resource_name:
        payload["resource"] = _named_ref(resource_name)
    if qty_inspected is not None:
        payload["qtyInspected"] = qty_inspected
    if qty2_inspected is not None:
        payload["qty2Inspected"] = qty2_inspected
    if containers_inspected is not None:
        payload["containersInspected"] = containers_inspected
    if container_level_inspected_name:
        payload["containerLevelInspected"] = _named_ref(
            container_level_inspected_name
        )

    merge_error = _merge_body_json(payload, body_json)
    if merge_error:
        return merge_error
    if not payload.get("container") or not payload.get("serviceDetails"):
        return "❌ ContainerDefect requires container and serviceDetails."
    return await request_shopfloor(
        "POST",
        "/api/ContainerDefect",
        body=payload,
    )


@mcp.tool
async def rework(
    container_name: str,
    rework_reason_name: str,
    container_level_name: Optional[str] = None,
    move_all_qty: bool = True,
    qty: Optional[float] = None,
    resource_name: Optional[str] = None,
    to_resource_name: Optional[str] = None,
    to_workflow_name: Optional[str] = None,
    to_workflow_revision: Optional[str] = None,
    to_step_name: Optional[str] = None,
    end_rework_workflow_name: Optional[str] = None,
    end_rework_workflow_revision: Optional[str] = None,
    end_rework_step_name: Optional[str] = None,
    reentry_workflow_name: Optional[str] = None,
    reentry_workflow_revision: Optional[str] = None,
    reentry_step_name: Optional[str] = None,
    use_reentry_workflow_stack: Optional[bool] = None,
    close: bool = False,
    comments: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Send a Container to rework using the original Camstar Rework service.
    POST Shopfloor /api/Rework

    container_name and rework_reason_name are required. By default the whole
    quantity is reworked. Set move_all_qty=false and provide qty for a partial
    quantity. Workflow references require both name and revision. Use
    body_json for advanced Rework fields such as workflow stacks.
    """
    if not container_name or not container_name.strip():
        return "❌ container_name must not be empty."
    if not rework_reason_name or not rework_reason_name.strip():
        return "❌ rework_reason_name must not be empty."
    if move_all_qty:
        if qty is not None:
            return "❌ qty must be omitted when move_all_qty is true."
    elif qty is None or qty <= 0:
        return "❌ qty must be greater than 0 when move_all_qty is false."

    workflow_pairs = (
        ("to_workflow", to_workflow_name, to_workflow_revision),
        (
            "end_rework_workflow",
            end_rework_workflow_name,
            end_rework_workflow_revision,
        ),
        (
            "reentry_workflow",
            reentry_workflow_name,
            reentry_workflow_revision,
        ),
    )
    for label, name, revision in workflow_pairs:
        pair_error = _validate_revision_pair(label, name, revision)
        if pair_error:
            return pair_error

    payload: dict = {
        "container": _container_ref(container_name, container_level_name),
        "reworkReason": _named_ref(rework_reason_name),
        "moveAllQty": move_all_qty,
        "close": close,
    }
    if qty is not None:
        payload["qty"] = qty
    if resource_name:
        payload["resource"] = _named_ref(resource_name)
    if to_resource_name:
        payload["toResource"] = _named_ref(to_resource_name)
    if to_workflow_name and to_workflow_revision:
        payload["toWorkflow"] = _revisioned_ref(
            to_workflow_name,
            to_workflow_revision,
        )
    if to_step_name:
        payload["toStep"] = _named_ref(to_step_name)
    if end_rework_workflow_name and end_rework_workflow_revision:
        payload["endReworkWorkflow"] = _revisioned_ref(
            end_rework_workflow_name,
            end_rework_workflow_revision,
        )
    if end_rework_step_name:
        payload["endReworkStep"] = _named_ref(end_rework_step_name)
    if reentry_workflow_name and reentry_workflow_revision:
        payload["reEntryWorkflow"] = _revisioned_ref(
            reentry_workflow_name,
            reentry_workflow_revision,
        )
    if reentry_step_name:
        payload["reEntryStep"] = _named_ref(reentry_step_name)
    if use_reentry_workflow_stack is not None:
        payload["useReEntryWorkflowStack"] = use_reentry_workflow_stack
    if comments is not None:
        payload["comments"] = comments

    merge_error = _merge_body_json(payload, body_json)
    if merge_error:
        return merge_error
    if not payload.get("container") or not payload.get("reworkReason"):
        return "❌ Rework requires container and reworkReason."
    return await request_shopfloor("POST", "/api/Rework", body=payload)
