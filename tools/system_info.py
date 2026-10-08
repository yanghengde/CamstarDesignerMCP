"""Local, side-effect-free MCP server diagnostics."""

from importlib.metadata import version

from mcp.types import LATEST_PROTOCOL_VERSION

from config import ENABLE_MCP_HTTP, MCP_HTTP_PATH
from tools import mcp


@mcp.tool
async def get_mcp_server_status() -> dict[str, object]:
    """Return MCP protocol/runtime information without contacting Camstar."""
    return {
        "server": "CamstarDesigner",
        "protocol_version": LATEST_PROTOCOL_VERSION,
        "fastmcp_version": version("fastmcp"),
        "mcp_sdk_version": version("mcp"),
        "streamable_http_enabled": ENABLE_MCP_HTTP,
        "streamable_http_path": MCP_HTTP_PATH,
    }
