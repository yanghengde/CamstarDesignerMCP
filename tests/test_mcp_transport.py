"""Integration coverage for the external MCP Streamable HTTP endpoint."""

from fastapi.testclient import TestClient
from mcp.types import LATEST_PROTOCOL_VERSION

import web.app as app_module


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _initialize_request() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            # Verify backwards compatibility as well as the current SDK.
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    }


def test_enabled_transport_requires_api_key(monkeypatch):
    monkeypatch.setattr(app_module, "ENABLE_MCP_HTTP", True)
    monkeypatch.setattr(app_module, "MCP_API_KEY", "")

    try:
        app_module.create_app()
    except RuntimeError as exc:
        assert "MCP_API_KEY" in str(exc)
    else:
        raise AssertionError("MCP HTTP must not start without authentication")


def test_streamable_http_auth_initialize_list_and_call(monkeypatch):
    async def no_register() -> None:
        return None

    monkeypatch.setattr(app_module, "ENABLE_MCP_HTTP", True)
    monkeypatch.setattr(app_module, "MCP_API_KEY", "integration-secret")
    monkeypatch.setattr(app_module, "MCP_ALLOWED_HOSTS", ["testserver:*"])
    monkeypatch.setattr(app_module, "MCP_ALLOWED_ORIGINS", [])
    monkeypatch.setattr(app_module, "AGENT_ENGINE", "legacy")
    monkeypatch.setattr(app_module, "register_tools", no_register)
    monkeypatch.setattr(app_module, "init_memory", lambda: None)

    app = app_module.create_app()
    with TestClient(app) as client:
        unauthorized = client.post(
            "/mcp/", json=_initialize_request(), headers=_headers()
        )
        assert unauthorized.status_code == 401

        initialized = client.post(
            "/mcp/",
            json=_initialize_request(),
            headers=_headers("integration-secret"),
        )
        assert initialized.status_code == 200
        init_result = initialized.json()["result"]
        assert init_result["protocolVersion"] == "2025-11-25"
        assert init_result["serverInfo"]["name"] == "CamstarModeling"

        listed = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=_headers("integration-secret"),
        )
        assert listed.status_code == 200
        tool_names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        assert "container_start" in tool_names
        assert "get_mcp_server_status" in tool_names

        called = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_mcp_server_status", "arguments": {}},
            },
            headers=_headers("integration-secret"),
        )
        assert called.status_code == 200
        call_result = called.json()["result"]
        assert call_result["isError"] is False
        assert call_result["structuredContent"]["server"] == "CamstarModeling"
        assert (
            call_result["structuredContent"]["protocol_version"]
            == LATEST_PROTOCOL_VERSION
        )
