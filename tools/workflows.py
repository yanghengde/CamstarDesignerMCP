"""
Workflow Entity MCP Tools
=========================
Swagger: /api/Workflows
Each API endpoint corresponds to a @mcp.tool.
"""

import json
from typing import Optional

from tools import mcp
from core.http_client import request


@mcp.tool
async def list_workflows(
    filter_expr: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    select: Optional[str] = None,
    expand: Optional[str] = None,
    orderby: Optional[str] = None,
) -> str:
    """
    List Workflows with optional OData querying.
    GET /api/Workflows
    
    Optional OData Parameters:
      - filter_expr: example "Name eq 'ABC'"
      - top: limit number of results
      - skip: skip number of results
      - select: fields to return
      - expand: navigation properties to expand
      - orderby: order sequence, e.g., "Name desc"
      
    Returns an array of WorkflowEntity objects with key fields.
    Large responses are automatically trimmed.
    """
    params = {}
    if filter_expr: params["$filter"] = filter_expr
    if top is not None: params["$top"] = top
    if skip is not None: params["$skip"] = skip
    if select: params["$select"] = select
    if expand: params["$expand"] = expand
    if orderby: params["$orderby"] = orderby
    
    return await request("GET", "/api/Workflows", params=params or None)


@mcp.tool
async def get_workflow(key: str) -> str:
    """
    Get a single Workflow by its key (instanceID or name:revision).
    GET /api/Workflows/{key}
    Returns the full WorkflowEntity object.
    """
    return await request("GET", f"/api/Workflows/{key}")


@mcp.tool
async def get_workflow_by_odata_key(key: str) -> str:
    """
    Get a single Workflow using OData key syntax.
    GET /api/Workflows({key})
    Example key: 'MyWorkflow:1'
    Returns the full WorkflowEntity object.
    """
    return await request("GET", f"/api/Workflows({key})")


@mcp.tool
async def create_workflow(
    name: str,
    revision: str,
    description: Optional[str] = None,
    status: Optional[int] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Create a new Workflow.
    POST /api/Workflows

    Required fields:
      - name: Workflow name
      - revision: Workflow revision (e.g., '1', 'A')
    Optional: description, status (1=Active, 2=Inactive), and any additional fields via body_json.
    """
    payload: dict = {
        "name": name,
        "revision": revision,
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

    return await request("POST", "/api/Workflows", body=payload)


@mcp.tool
async def update_workflow(
    key: str,
    name: str,
    revision: str,
    description: Optional[str] = None,
    status: Optional[int] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing Workflow by key.
    PUT /api/Workflows/{key}

    Required fields: name, revision.
    Optional: description, status, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
        "revision": revision,
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

    return await request("PUT", f"/api/Workflows/{key}", body=payload)


@mcp.tool
async def update_workflow_by_odata_key(
    key: str,
    name: str,
    revision: str,
    description: Optional[str] = None,
    status: Optional[int] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Fully update (replace) an existing Workflow using OData key syntax.
    PUT /api/Workflows({key})

    Required fields: name, revision.
    Optional: description, status, or any extra fields via body_json.
    """
    payload: dict = {
        "name": name,
        "revision": revision,
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

    return await request("PUT", f"/api/Workflows({key})", body=payload)


@mcp.tool
async def patch_workflow(
    body_json: str,
) -> str:
    """
    Patch an existing Workflow (partial update). Key should be in the URL or payload if Camstar uses it like Specs do.
    PATCH /api/Workflows
    Wait, Camstar patch typically takes no key in URL, but requires OData/InstanceId in body, or similar.
    Provide the partial JSON payload in body_json.
    """
    try:
        payload = json.loads(body_json)
    except json.JSONDecodeError as e:
        return f"❌ Invalid JSON in body_json: {e}"

    return await request("PATCH", "/api/Workflows", body=payload)


@mcp.tool
async def delete_workflow(key: str) -> str:
    """
    Delete a Workflow by key.
    DELETE /api/Workflows/{key}
    """
    return await request("DELETE", f"/api/Workflows/{key}")


@mcp.tool
async def delete_workflow_by_odata_key(key: str) -> str:
    """
    Delete a Workflow using OData key syntax.
    DELETE /api/Workflows({key})
    """
    return await request("DELETE", f"/api/Workflows({key})")


@mcp.tool
async def get_workflows_count() -> str:
    """
    Get the total count of Workflows.
    GET /api/Workflows/$count
    """
    return await request("GET", "/api/Workflows/$count")


@mcp.tool
async def request_workflow_selection_values(
    selection_values_expression: Optional[str] = None,
    body_json: Optional[str] = None,
) -> str:
    """
    Request selection (dropdown / LOV) values for a Workflow entity.
    POST /api/Workflows/RequestSelectionValues

    selection_values_expression: An OData-style expression to filter
        which selection values to return (optional query parameter).
    body_json: A JSON string representing a partial WorkflowEntity whose
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

    return await request("POST", "/api/Workflows/RequestSelectionValues",
                         body=payload, params=params or None)


@mcp.tool
async def add_spec_step_to_workflow(
    workflow_id: str,
    step_name: str,
    spec_name: str,
    spec_revision: Optional[str] = None,
    sequence: Optional[int] = None
) -> str:
    """
    Add a step associated with a Spec to an existing Workflow.
    PUT /api/Workflows/{workflow_id}

    Required fields:
      - workflow_id: Instance ID or Name:Revision of the target workflow. For newly created/draft workflows, InstanceID MUST be used.
      - step_name: Name for the new step in the workflow
      - spec_name: Name of the Spec to associate with this step

    Optional:
      - spec_revision: Specific revision of the Spec. Defaults to Revision of Record if not provided.
      - sequence: Sequence number for the step.
    """
    spec_ref = {"name": spec_name}
    if spec_revision:
        spec_ref["revision"] = spec_revision
    else:
        spec_ref["useROR"] = True

    payload = {
        "steps-Expanded": [
            {
                "listItemAction": "add",
                "value": {
                    "@odata.type": "#modeling.SpecStepChanges",
                    "name": step_name,
                    "spec": spec_ref
                }
            }
        ]
    }
    
    if sequence is not None:
        payload["steps-Expanded"][0]["value"]["sequence"] = sequence

    return await request("PUT", f"/api/Workflows/{workflow_id}", body=payload)


@mcp.tool
async def connect_workflow_steps(
    workflow_id: str,
    workflow_name: str,
    workflow_revision: str,
    from_step: str,
    to_step: str,
    path_name: Optional[str] = None
) -> str:
    """
    Connect two steps within a Workflow by creating a MovePath.
    PUT /api/Workflows/{workflow_id}

    Required fields:
      - workflow_id: Instance ID (or Name:Revision) of the target workflow. For Drafts, InstanceID is required.
      - workflow_name: Name of the workflow (needed for parent reference)
      - workflow_revision: Revision of the workflow (needed for parent reference)
      - from_step: The name of the step where the connection starts
      - to_step: The name of the step where the connection goes

    Optional:
      - path_name: Custom name for the path. Defaults to '{from_step}_to_{to_step}'.
    """
    if not path_name:
        path_name = f"{from_step}_to_{to_step}"

    payload = {
        "steps-Expanded": [
            {
                "listItemAction": "change",
                "key": {"name": from_step},
                "value": {
                    "@odata.type": "#modeling.SpecStepChanges",
                    "name": from_step,
                    "paths-Expanded": [{
                        "listItemAction": "add",
                        "value": {
                            "@odata.type": "#modeling.MovePathChanges",
                            "name": path_name,
                            "toStep": {
                                "name": to_step,
                                "parent": {"name": workflow_name, "revision": workflow_revision}
                            }
                        }
                    }]
                }
            }
        ]
    }
    
    return await request("PUT", f"/api/Workflows/{workflow_id}", body=payload)


@mcp.tool
async def delete_workflow_steps(
    workflow_id: str,
    step_names_json: str,
) -> str:
    """
    Delete one or more steps from a Workflow by name.
    PUT /api/Workflows/{workflow_id}

    IMPORTANT: When a step is referenced by paths from other steps,
    individual deletion will fail. This tool resolves this by batching
    ALL deletions into a single API call, which Camstar processes atomically.

    Required fields:
      - workflow_id: Instance ID of the target workflow.
      - step_names_json: A JSON array of step names to delete.
        Example: '["HD-001-01", "HD-005"]'
    """

    try:
        step_names = json.loads(step_names_json)
        if not isinstance(step_names, list):
            return "❌ step_names_json must be a JSON array of strings"
    except json.JSONDecodeError as e:
        return f"❌ Invalid step_names_json: {e}"

    payload = {
        "steps-Expanded": [
            {"listItemAction": "delete", "key": {"name": name}}
            for name in step_names
        ]
    }

    return await request("PUT", f"/api/Workflows/{workflow_id}", body=payload)


@mcp.tool
async def rebuild_workflow_route(
    workflow_id: str,
    workflow_name: str,
    workflow_revision: str,
    route_json: str,
) -> str:
    """
    Rebuild an entire workflow route from scratch: clear ALL existing steps,
    then add new steps in order and connect them sequentially.
    PUT /api/Workflows/{workflow_id}

    This is the most reliable way to restructure a workflow route.
    It performs 3 phases atomically per-step:
      Phase 1: Delete all existing steps (batch delete).
      Phase 2: Add each new step (associated with its Spec via useROR).
      Phase 3: Connect steps sequentially with MovePaths.

    Required fields:
      - workflow_id: Instance ID of the target workflow.
      - workflow_name: Name of the workflow (e.g. 'WF-HD').
      - workflow_revision: Revision of the workflow (e.g. '1').
      - route_json: A JSON array of step definitions in order.
        Each element can be:
          - A string (step name = spec name): "HD-001"
          - An object with details: {"step_name": "Step1", "spec_name": "HD-001", "spec_revision": "A"}
        Example: '["HD-001", "HD-002", {"step_name": "QC-Check", "spec_name": "QC-001"}]'

    Returns a summary of all operations performed.
    """

    try:
        route = json.loads(route_json)
        if not isinstance(route, list) or len(route) == 0:
            return "❌ route_json must be a non-empty JSON array"
    except json.JSONDecodeError as e:
        return f"❌ Invalid route_json: {e}"

    # Normalize route entries
    steps = []
    for item in route:
        if isinstance(item, str):
            steps.append({"step_name": item, "spec_name": item, "spec_revision": None})
        elif isinstance(item, dict):
            step_name = item.get("step_name") or item.get("name")
            spec_name = item.get("spec_name") or step_name
            spec_rev = item.get("spec_revision")
            if not step_name:
                return f"❌ Each route entry must have a 'step_name' or 'name'. Got: {item}"
            steps.append({"step_name": step_name, "spec_name": spec_name, "spec_revision": spec_rev})
        else:
            return f"❌ Invalid route entry (must be string or object): {item}"

    results = []

    # --- Phase 1: Get current steps and delete them all ---
    try:
        current_raw = await request("GET", f"/api/Workflows/{workflow_id}")
        current = json.loads(current_raw)
        existing_steps = [ref.get("Name") for ref in current.get("ES_ResolvedSteps", [])]
    except Exception:
        existing_steps = []

    if existing_steps:
        delete_payload = {
            "steps-Expanded": [
                {"listItemAction": "delete", "key": {"name": name}}
                for name in existing_steps
            ]
        }
        del_result = await request("PUT", f"/api/Workflows/{workflow_id}", body=delete_payload)
        results.append(f"Phase 1 - Deleted {len(existing_steps)} existing steps: {existing_steps}")
    else:
        results.append("Phase 1 - No existing steps to delete")

    # --- Phase 2: Add each step ---
    for s in steps:
        spec_ref = {"name": s["spec_name"]}
        if s["spec_revision"]:
            spec_ref["revision"] = s["spec_revision"]
        else:
            spec_ref["useROR"] = True

        add_payload = {
            "steps-Expanded": [{
                "listItemAction": "add",
                "value": {
                    "@odata.type": "#modeling.SpecStepChanges",
                    "name": s["step_name"],
                    "spec": spec_ref
                }
            }]
        }
        add_result = await request("PUT", f"/api/Workflows/{workflow_id}", body=add_payload)
        ok = "✅" if "已于" in add_result or "updated" in add_result.lower() else "⚠️"
        results.append(f"Phase 2 - Add step '{s['step_name']}' (Spec: {s['spec_name']}): {ok}")

    # --- Phase 3: Connect steps sequentially ---
    for i in range(len(steps) - 1):
        from_step = steps[i]["step_name"]
        to_step = steps[i + 1]["step_name"]
        path_name = f"{from_step}_to_{to_step}"

        connect_payload = {
            "steps-Expanded": [{
                "listItemAction": "change",
                "key": {"name": from_step},
                "value": {
                    "@odata.type": "#modeling.SpecStepChanges",
                    "name": from_step,
                    "paths-Expanded": [{
                        "listItemAction": "add",
                        "value": {
                            "@odata.type": "#modeling.MovePathChanges",
                            "name": path_name,
                            "toStep": {
                                "name": to_step,
                                "parent": {"name": workflow_name, "revision": workflow_revision}
                            }
                        }
                    }]
                }
            }]
        }
        conn_result = await request("PUT", f"/api/Workflows/{workflow_id}", body=connect_payload)
        ok = "✅" if "已于" in conn_result or "updated" in conn_result.lower() else "⚠️"
        results.append(f"Phase 3 - Connect '{from_step}' → '{to_step}': {ok}")

    # --- Verify ---
    try:
        verify_raw = await request("GET", f"/api/Workflows/{workflow_id}")
        verify = json.loads(verify_raw)
        final_steps = [ref.get("Name") for ref in verify.get("ES_ResolvedSteps", [])]
        first_step = verify.get("FirstStep", {}).get("Name", "N/A")
        results.append(f"\n✅ Verification: {len(final_steps)} steps, FirstStep={first_step}")
        results.append(f"   Route: {' → '.join(final_steps)}")
    except Exception as e:
        results.append(f"⚠️ Verification failed: {e}")

    return "\n".join(results)


@mcp.tool
async def update_workflow_step(
    workflow_id: str,
    step_name: str,
    new_spec_name: Optional[str] = None,
    new_spec_revision: Optional[str] = None,
    description: Optional[str] = None,
    is_last_step: Optional[bool] = None,
    sequence: Optional[int] = None,
) -> str:
    """
    Update properties of an existing step within a Workflow.
    PUT /api/Workflows/{workflow_id}

    Required fields:
      - workflow_id: Instance ID of the target workflow.
      - step_name: Name of the step to modify.

    Optional:
      - new_spec_name: Change which Spec this step references.
      - new_spec_revision: Specific Spec revision (defaults to ROR if new_spec_name is given).
      - description: Update the step description.
      - is_last_step: Mark this as the last step (true/false).
      - sequence: Update the step sequence number.
    """
    step_value: dict = {
        "@odata.type": "#modeling.SpecStepChanges",
        "name": step_name,
    }

    if new_spec_name:
        spec_ref = {"name": new_spec_name}
        if new_spec_revision:
            spec_ref["revision"] = new_spec_revision
        else:
            spec_ref["useROR"] = True
        step_value["spec"] = spec_ref

    if description is not None:
        step_value["description"] = description
    if is_last_step is not None:
        step_value["isLastStep"] = is_last_step
    if sequence is not None:
        step_value["sequence"] = sequence

    payload = {
        "steps-Expanded": [{
            "listItemAction": "change",
            "key": {"name": step_name},
            "value": step_value
        }]
    }

    return await request("PUT", f"/api/Workflows/{workflow_id}", body=payload)

