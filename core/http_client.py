"""
通用 HTTP 客户端
==================
为所有 MCP 工具模块提供统一的 HTTP 请求能力。
"""

import logging
import httpx

from config import (
    CAMSTAR_BASE_URL,
    CAMSTAR_PASSWORD,
    CAMSTAR_QUERY_BASE_URL,
    CAMSTAR_SHOPFLOOR_BASE_URL,
    CAMSTAR_TIMEOUT,
    CAMSTAR_USERNAME,
    MAX_RESPONSE_LENGTH,
)
from core.auth import generate_camstar_auth_token
from core.response import smart_query_response, smart_response

logger = logging.getLogger("camstar-mcp")


_cached_token: str | None = None
_client: httpx.AsyncClient | None = None


def get_headers() -> dict:
    """Build common request headers with dynamic Bearer auth."""
    global _cached_token
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        if _cached_token is None:
            _cached_token = generate_camstar_auth_token(CAMSTAR_USERNAME, CAMSTAR_PASSWORD)
        if _cached_token:
            headers["Authorization"] = f"Bearer {_cached_token}"
    except Exception as e:
        logger.error(f"Failed to generate auth token: {e}")
    return headers


def get_client() -> httpx.AsyncClient:
    """Get or initialize the shared global AsyncClient."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=CAMSTAR_TIMEOUT, verify=False)
    return _client


async def close_client():
    """Close the shared AsyncClient connection pool."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        logger.info("Shared HTTP client connection pool closed.")


def build_url(path: str, base_url: str = CAMSTAR_BASE_URL) -> str:
    """Construct full URL from a relative API path."""
    base = base_url.rstrip("/")
    return f"{base}{path}"


async def request(method: str, path: str, body: dict | None = None,
                  params: dict | None = None,
                  base_url: str = CAMSTAR_BASE_URL,
                  response_formatter=smart_response) -> str:
    """
    Central HTTP request dispatcher.
    Returns the response text after smart truncation, or an error message.
    """
    url = build_url(path, base_url=base_url)
    headers = get_headers()

    logger.info("%s %s", method.upper(), url)

    try:
        client = get_client()
        resp = await client.request(
            method,
            url,
            headers=headers,
            json=body,
            params=params,
        )

        if resp.status_code >= 400:
            return (
                f"❌ HTTP {resp.status_code} Error\n"
                f"URL: {url}\n"
                f"Response: {resp.text[:2000]}"
            )

        # Some endpoints return empty 200
        if not resp.text.strip():
            return f"✅ {method.upper()} succeeded (HTTP {resp.status_code}, empty body)."

        try:
            data = resp.json()
        except Exception:
            return resp.text[:MAX_RESPONSE_LENGTH]

        return response_formatter(data)

    except httpx.TimeoutException:
        return f"❌ Request timed out after {CAMSTAR_TIMEOUT}s: {method.upper()} {url}"
    except Exception as exc:
        return f"❌ Request failed: {repr(exc)}"


async def request_shopfloor(method: str, path: str, body: dict | None = None,
                            params: dict | None = None) -> str:
    """Send a request to the Camstar Shopfloor API."""
    return await request(
        method,
        path,
        body=body,
        params=params,
        base_url=CAMSTAR_SHOPFLOOR_BASE_URL,
    )


async def request_query(method: str, path: str, body: dict | None = None,
                        params: dict | None = None) -> str:
    """Send a request to the Camstar Query API."""
    return await request(
        method,
        path,
        body=body,
        params=params,
        base_url=CAMSTAR_QUERY_BASE_URL,
        response_formatter=smart_query_response,
    )

