"""
Camstar Designer MCP — 统一配置中心
====================================
所有环境变量在此集中读取，其他模块通过 from config import xxx 使用。
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# LLM 大模型配置（兼容 OpenAI 协议的任意模型）
# ---------------------------------------------------------------------------
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# ---------------------------------------------------------------------------
# Agent 配置
# ---------------------------------------------------------------------------
MAX_TOOL_LOOPS = int(os.getenv("MAX_TOOL_LOOPS", "15"))
# A graph turn traverses multiple nodes (agent, policy, and one node per tool),
# so this must be higher than MAX_TOOL_LOOPS. The agent loop guard remains the
# primary protection against runaway reasoning.
LANGGRAPH_RECURSION_LIMIT = max(
    25,
    int(os.getenv("LANGGRAPH_RECURSION_LIMIT", str(MAX_TOOL_LOOPS * 6 + 10))),
)
MEMORY_FILE = os.path.join("data", "memory.json")
SESSIONS_DIR = os.path.join("data", "sessions")
EXCEL_ATTACHMENT_ROOT = os.path.join("data", "excel_attachments")
DESIGNER_PROGRESS_DB = os.getenv("DESIGNER_PROGRESS_DB", os.path.join("data", "designer_progress.sqlite"))

LANGGRAPH_CHECKPOINT_DB = os.getenv(
    "LANGGRAPH_CHECKPOINT_DB",
    os.path.join("data", "langgraph_checkpoints.sqlite"),
)
EXPERIENCE_DB = os.getenv(
    "EXPERIENCE_DB",
    os.path.join("data", "agent_experience.sqlite"),
)

# ---------------------------------------------------------------------------
# MCP Streamable HTTP transport
# ---------------------------------------------------------------------------
# The browser's /chat SSE route is separate from the MCP protocol endpoint.
# External access to local Designer metadata is opt-in.
ENABLE_MCP_HTTP = os.getenv("ENABLE_MCP_HTTP", "False").lower() in (
    "true",
    "1",
    "yes",
)
MCP_HTTP_PATH = os.getenv("MCP_HTTP_PATH", "/mcp").strip() or "/mcp"
if not MCP_HTTP_PATH.startswith("/"):
    MCP_HTTP_PATH = f"/{MCP_HTTP_PATH}"
MCP_HTTP_PATH = MCP_HTTP_PATH.rstrip("/") or "/mcp"
MCP_API_KEY = os.getenv("MCP_API_KEY", "").strip()


def _csv_env(name: str, default: str) -> list[str]:
    """Read a comma-separated environment variable into a clean list."""
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


# FastMCP host/origin protection prevents DNS-rebinding attacks. Add the actual
# server address explicitly when exposing the endpoint on a LAN.
MCP_ALLOWED_HOSTS = _csv_env(
    "MCP_ALLOWED_HOSTS",
    "localhost:*,127.0.0.1:*,[::1]:*",
)
MCP_ALLOWED_ORIGINS = _csv_env("MCP_ALLOWED_ORIGINS", "")

# 安全卡点阈值：当修改行为超过多少条时要求强制确认
SAFE_CREATE_THRESHOLD = int(os.getenv("SAFE_CREATE_THRESHOLD", "20"))
SAFE_UPDATE_THRESHOLD = int(os.getenv("SAFE_UPDATE_THRESHOLD", "3"))
SAFE_DELETE_THRESHOLD = int(os.getenv("SAFE_DELETE_THRESHOLD", "0")) # Default 0 means >= 1 triggers it

# ---------------------------------------------------------------------------
# 日志与性能监控开关
# ---------------------------------------------------------------------------
ENABLE_PERFORMANCE_LOG = os.getenv("ENABLE_PERFORMANCE_LOG", "True").lower() in ("true", "1", "yes")

# Designer local metadata and browser session identity.
DESIGNER_ROOT = os.getenv("DESIGNER_ROOT", os.path.join("data", "designer"))
DESIGNER_METADATA_EXPORT_EXE = os.getenv("DESIGNER_METADATA_EXPORT_EXE", "")
DESIGNER_METADATA_ASSEMBLY = os.getenv("DESIGNER_METADATA_ASSEMBLY", "")
DESIGNER_BRIDGE_TIMEOUT = int(os.getenv("DESIGNER_BRIDGE_TIMEOUT", "180"))
DESIGNER_DB_SERVER = os.getenv("DESIGNER_DB_SERVER", "")
DESIGNER_DB_NAME = os.getenv("DESIGNER_DB_NAME", "")
DESIGNER_DB_USER = os.getenv("DESIGNER_DB_USER", "")
DESIGNER_DB_PASSWORD = os.getenv("DESIGNER_DB_PASSWORD", "")
DESIGNER_UPDATE_DB_USER = os.getenv("DESIGNER_UPDATE_DB_USER", DESIGNER_DB_USER)
DESIGNER_UPDATE_DB_PASSWORD = os.getenv("DESIGNER_UPDATE_DB_PASSWORD", DESIGNER_DB_PASSWORD)
DESIGNER_TEST_TARGET_CONFIRMED = os.getenv("DESIGNER_TEST_TARGET_CONFIRMED", "false").lower() == "true"
# Confirmed test targets can publish without a database backup by default.
DESIGNER_REQUIRE_DATABASE_BACKUP = os.getenv("DESIGNER_REQUIRE_DATABASE_BACKUP", "false").lower() == "true"
DESIGNER_WCF_DIRECTORY = os.getenv("DESIGNER_WCF_DIRECTORY", "")
DESIGNER_SERVER_SHARE = os.getenv("DESIGNER_SERVER_SHARE", "")
DESIGNER_UI_EXE = os.getenv("DESIGNER_UI_EXE", "")
DESIGNER_SERVER_IMPORT_EXE = os.getenv("DESIGNER_SERVER_IMPORT_EXE", "")
DESIGNER_WINDOWS_USER = os.getenv("DESIGNER_WINDOWS_USER", "")
DESIGNER_WINDOWS_PASSWORD = os.getenv("DESIGNER_WINDOWS_PASSWORD", "")
DESIGNER_SERVER_SITEINFO = os.getenv("DESIGNER_SERVER_SITEINFO", "")
DESIGNER_SERVER_WCF_GENERATOR = os.getenv("DESIGNER_SERVER_WCF_GENERATOR", "")
DESIGNER_WCF_ADDRESS = os.getenv("DESIGNER_WCF_ADDRESS", "")
DESIGNER_SERVICE_TIMEOUT = int(os.getenv("DESIGNER_SERVICE_TIMEOUT", "1800"))
if not 10<=DESIGNER_SERVICE_TIMEOUT<=3600:
    raise ValueError("DESIGNER_SERVICE_TIMEOUT must be 10..3600 seconds")
DESIGNER_PUBLICATION_TIMEOUT = int(os.getenv("DESIGNER_PUBLICATION_TIMEOUT", "900"))
DESIGNER_EXPORT_TIMEOUT = int(os.getenv("DESIGNER_EXPORT_TIMEOUT", "120"))
SERVER_PORT = int(os.getenv("SERVER_PORT", "8032"))
CHAT_USERNAME = os.getenv("CHAT_USERNAME", "designer")
