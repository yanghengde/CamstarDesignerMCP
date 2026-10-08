"""
Camstar Query API MCP tools
===========================
Reference: OCEXCR REST API 2510+, Chapter 4 (Query APIs)
"""

import json
import re
from typing import Optional
from urllib.parse import quote

from config import CAMSTAR_QUERY_BASE_URL
from core.http_client import (
    build_url,
    get_client,
    get_headers,
    request_query,
)
from tools import mcp


_SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SWAGGER_CONFIG_PATTERN = re.compile(
    r"var\s+configObject\s*=\s*JSON\.parse\('(?P<config>.*?)'\);",
    re.DOTALL,
)
_FORBIDDEN_ADHOC_KEYWORDS = {
    "ALTER",
    "CALL",
    "COMMIT",
    "CREATE",
    "DELETE",
    "DROP",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "INSERT",
    "INTO",
    "MERGE",
    "REVOKE",
    "ROLLBACK",
    "TRUNCATE",
    "UPDATE",
}


def _validate_service_name(name: str) -> Optional[str]:
    if not name or not name.strip():
        return "❌ Query service/CDO name must not be empty."
    if not _SERVICE_NAME_PATTERN.fullmatch(name.strip()):
        return (
            "❌ Query service/CDO name may contain only letters, digits, "
            "and underscores, and must not start with a digit."
        )
    return None


def _parse_body_json(body_json: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    if not body_json:
        return None, None
    try:
        body = json.loads(body_json)
    except json.JSONDecodeError as exc:
        return None, f"❌ Invalid body_json: {exc}"
    if not isinstance(body, dict):
        return None, "❌ Invalid body_json: expected a JSON object."
    return body, None


def _parse_parameters_json(
    parameters_json: Optional[str],
) -> tuple[Optional[list[dict[str, str]]], Optional[str]]:
    if not parameters_json:
        return None, None
    try:
        raw_parameters = json.loads(parameters_json)
    except json.JSONDecodeError as exc:
        return None, f"❌ Invalid parameters_json: {exc}"

    if isinstance(raw_parameters, dict):
        entries = [
            {"name": name, "value": value}
            for name, value in raw_parameters.items()
        ]
    elif isinstance(raw_parameters, list):
        entries = raw_parameters
    else:
        return None, (
            "❌ Invalid parameters_json: expected an object mapping names "
            "to values, or a list of {name, value} objects."
        )

    parameters: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return None, (
                "❌ Invalid parameters_json: every list item must be an "
                "object with name and value."
            )
        name = entry.get("name")
        value = entry.get("value")
        if not isinstance(name, str) or not name.strip():
            return None, "❌ Every query parameter requires a non-empty name."
        if isinstance(value, (dict, list)):
            return None, (
                f"❌ Query parameter {name!r} must have a scalar value."
            )
        normalized_name = name.strip()
        if normalized_name.casefold() in seen_names:
            return None, f"❌ Duplicate query parameter: {normalized_name}."
        seen_names.add(normalized_name.casefold())
        parameters.append(
            {
                "name": normalized_name,
                "value": "" if value is None else str(value),
            }
        )
    return parameters, None


def _validate_read_only_query(query_text: str) -> Optional[str]:
    if not query_text or not query_text.strip():
        return "❌ query_text must not be empty."

    statement = query_text.strip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
    if ";" in statement:
        return "❌ Ad hoc Query allows exactly one SELECT statement."
    if "--" in statement or "/*" in statement or "*/" in statement:
        return "❌ SQL comments are not allowed in ad hoc Query text."
    if not re.match(r"^SELECT\b", statement, flags=re.IGNORECASE):
        return "❌ Ad hoc Query is restricted to a SELECT statement."

    words = set(re.findall(r"\b[A-Za-z]+\b", statement.upper()))
    forbidden = sorted(words & _FORBIDDEN_ADHOC_KEYWORDS)
    if forbidden:
        return (
            "❌ Ad hoc Query contains a forbidden write or execution "
            f"keyword: {', '.join(forbidden)}."
        )
    return None


async def _fetch_query_text(path: str) -> str:
    """Fetch an untrimmed Query service document for discovery tools."""
    url = build_url(path, base_url=CAMSTAR_QUERY_BASE_URL)
    response = await get_client().get(url, headers=get_headers())
    if response.status_code >= 400:
        raise RuntimeError(
            f"HTTP {response.status_code} from {url}: {response.text[:500]}"
        )
    return response.text


async def _fetch_query_json(path: str) -> dict:
    text = await _fetch_query_text(path)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("Query Swagger response was not a JSON object.")
    return data


def _request_schema_name(operation: dict) -> Optional[str]:
    schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return reference.rsplit("/", 1)[-1]
    return None


@mcp.tool
async def list_query_services(name_filter: Optional[str] = None) -> str:
    """
    List Query CDOs and special Query endpoints exposed by this server.
    GET Query /swagger/index.html

    This is discovery only and does not execute a query. Use name_filter for
    case-insensitive matching, for example "Container" or "Audit".
    """
    try:
        html = await _fetch_query_text("/swagger/index.html")
        match = _SWAGGER_CONFIG_PATTERN.search(html)
        if not match:
            return "❌ Could not find the Query Swagger service list."
        config = json.loads(match.group("config"))
        services = [
            item.get("name")
            for item in config.get("urls", [])
            if isinstance(item, dict) and item.get("name")
        ]
        if name_filter:
            folded_filter = name_filter.casefold()
            services = [
                name for name in services
                if folded_filter in name.casefold()
            ]
        services = sorted(set(services), key=str.casefold)
        return json.dumps(
            {"count": len(services), "services": services},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return f"❌ Failed to discover Query services: {exc}"


@mcp.tool
async def get_query_service_schema(
    service_name: str,
    field_filter: Optional[str] = None,
) -> str:
    """
    Summarize paths, methods, parameters, and request fields for one Query CDO.
    GET Query /swagger/{service_name}/swagger.json

    Call this before execute_query_inquiry when the required payload is not
    already known. field_filter narrows request field names case-insensitively.
    """
    validation_error = _validate_service_name(service_name)
    if validation_error:
        return validation_error
    service = service_name.strip()
    try:
        swagger = await _fetch_query_json(
            f"/swagger/{quote(service, safe='')}/swagger.json"
        )
        schema_names: set[str] = set()
        path_summary: list[dict] = []
        for path, path_item in swagger.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete"):
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                schema_name = _request_schema_name(operation)
                if schema_name:
                    schema_names.add(schema_name)
                path_summary.append(
                    {
                        "method": method.upper(),
                        "path": path,
                        "requestSchema": schema_name,
                        "parameters": [
                            {
                                "name": parameter.get("name"),
                                "in": parameter.get("in"),
                                "required": bool(parameter.get("required")),
                            }
                            for parameter in operation.get("parameters", [])
                            if isinstance(parameter, dict)
                        ],
                    }
                )

        schema_summary: dict[str, dict] = {}
        all_schemas = swagger.get("components", {}).get("schemas", {})
        for schema_name in sorted(schema_names):
            schema = all_schemas.get(schema_name, {})
            property_names = list(schema.get("properties", {}).keys())
            if field_filter:
                folded_filter = field_filter.casefold()
                property_names = [
                    name for name in property_names
                    if folded_filter in name.casefold()
                ]
            schema_summary[schema_name] = {
                "required": schema.get("required", []),
                "fieldCount": len(schema.get("properties", {})),
                "fields": property_names,
            }

        return json.dumps(
            {
                "service": service,
                "paths": path_summary,
                "requestSchemas": schema_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return f"❌ Failed to load Query Swagger for {service}: {exc}"


@mcp.tool
async def execute_query_inquiry(
    cdo_name: str,
    body_json: Optional[str] = None,
    select: Optional[str] = None,
    expand: Optional[str] = None,
    include_execute_node: bool = False,
) -> str:
    """
    Execute a standard Inquiry CDO.
    POST Query /api/{cdo_name}

    Use get_query_service_schema first to discover the expected body fields.
    select and expand map to $select and $expand. include_execute_node adds
    execute=true when the Inquiry requires the __execute node.
    """
    validation_error = _validate_service_name(cdo_name)
    if validation_error:
        return validation_error
    if cdo_name.casefold() in {"adhoc", "advancedquery"}:
        return (
            "❌ Use execute_adhoc_query, execute_advanced_query, or "
            "execute_user_query for this Query service."
        )
    body, body_error = _parse_body_json(body_json)
    if body_error:
        return body_error

    params: dict[str, str] = {}
    if select:
        params["$select"] = select
    if expand:
        params["$expand"] = expand
    if include_execute_node:
        params["execute"] = "true"

    return await request_query(
        "POST",
        f"/api/{cdo_name.strip()}",
        body=body,
        params=params or None,
    )


@mcp.tool
async def execute_query_inquiry_event(
    cdo_name: str,
    event_name: str,
    body_json: Optional[str] = None,
    select: Optional[str] = None,
    expand: Optional[str] = None,
) -> str:
    """
    Execute a named custom event on an Inquiry-derived CDO.
    POST Query /api/{cdo_name}?eventname={event_name}

    Custom event implementations can contain server-side logic beyond a plain
    read. Inspect the CDO/event definition before use and do not use this tool
    merely to discover event names.
    """
    validation_error = _validate_service_name(cdo_name)
    if validation_error:
        return validation_error
    event_error = _validate_service_name(event_name)
    if event_error:
        return event_error.replace("service/CDO", "event")
    body, body_error = _parse_body_json(body_json)
    if body_error:
        return body_error

    params: dict[str, str] = {"eventname": event_name.strip()}
    if select:
        params["$select"] = select
    if expand:
        params["$expand"] = expand
    return await request_query(
        "POST",
        f"/api/{cdo_name.strip()}",
        body=body,
        params=params,
    )


@mcp.tool
async def request_query_selection_values(
    cdo_name: str,
    selection_values_expression: str,
    body_json: Optional[str] = None,
) -> str:
    """
    Request valid values for a field in an Inquiry CDO without executing it.
    POST Query /api/{cdo_name}/RequestSelectionValues

    The current server exposes RequestSelectionValues in Swagger. Provide the
    partial Inquiry body when selection values depend on other fields.
    """
    validation_error = _validate_service_name(cdo_name)
    if validation_error:
        return validation_error
    if not selection_values_expression.strip():
        return "❌ selection_values_expression must not be empty."
    body, body_error = _parse_body_json(body_json)
    if body_error:
        return body_error
    return await request_query(
        "POST",
        f"/api/{cdo_name.strip()}/RequestSelectionValues",
        body=body,
        params={
            "selectionValuesExpression": selection_values_expression.strip()
        },
    )


async def _execute_named_query(
    query_name: str,
    query_type: str,
    parameters_json: Optional[str],
) -> str:
    if not query_name or not query_name.strip():
        return "❌ query_name must not be empty."
    parameters, parameters_error = _parse_parameters_json(parameters_json)
    if parameters_error:
        return parameters_error
    payload: dict = {"queryType": query_type}
    if parameters is not None:
        payload["parameters"] = parameters
    key = quote(query_name.strip(), safe="")
    return await request_query(
        "POST",
        f"/api/AdvancedQuery/{key}",
        body=payload,
    )


@mcp.tool
async def execute_advanced_query(
    query_name: str,
    parameters_json: Optional[str] = None,
) -> str:
    """
    Execute a system Advanced Query configured in Designer.
    POST Query /api/AdvancedQuery/{query_name} with queryType="system"

    parameters_json may be an object such as {"Role": "instance-id"} or a
    list of {"name": "Role", "value": "instance-id"} objects.
    """
    return await _execute_named_query(
        query_name,
        "system",
        parameters_json,
    )


@mcp.tool
async def execute_user_query(
    query_name: str,
    parameters_json: Optional[str] = None,
) -> str:
    """
    Execute a User Query created through the Modeling UserQueries entity.
    POST Query /api/AdvancedQuery/{query_name} with queryType="user"

    queryType is intentionally lowercase as required by the REST API guide.
    """
    return await _execute_named_query(
        query_name,
        "user",
        parameters_json,
    )


@mcp.tool
async def execute_adhoc_query(
    query_text: str,
    parameters_json: Optional[str] = None,
) -> str:
    """
    Execute one read-only ad hoc SELECT through the Query API.
    POST Query /api/AdHoc

    For safety this MCP accepts exactly one SELECT statement and rejects SQL
    comments, SELECT INTO, and write/execution keywords. parameters_json uses
    the same formats as execute_advanced_query.
    """
    query_error = _validate_read_only_query(query_text)
    if query_error:
        return query_error
    parameters, parameters_error = _parse_parameters_json(parameters_json)
    if parameters_error:
        return parameters_error

    payload: dict = {"queryText": query_text.strip()}
    if parameters is not None:
        payload["parameters"] = parameters
    return await request_query("POST", "/api/AdHoc", body=payload)
