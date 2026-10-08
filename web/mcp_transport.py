"""Security helpers for the external MCP Streamable HTTP transport."""

from __future__ import annotations

import hmac
import json
from typing import Any, Awaitable, Callable


class BearerTokenMiddleware:
    """Small ASGI middleware that protects every request to the mounted MCP app.

    A pure ASGI implementation is used so streaming responses are not buffered.
    """

    def __init__(self, app: Any, token: str):
        self.app = app
        self._expected = f"Bearer {token}".encode("utf-8")

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"authorization", b"")
        if not hmac.compare_digest(supplied, self._expected):
            body = json.dumps(
                {"error": "MCP endpoint requires a valid Bearer token."},
                ensure_ascii=False,
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
