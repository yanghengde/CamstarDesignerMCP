"""Enforce the local management boundary even when ASGI is launched directly."""
import ipaddress
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


def loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LocalAccessMiddleware:
    def __init__(self, app, mcp_path=None):
        self.app = app
        self.mcp_path = mcp_path

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            if self.mcp_path and (scope.get('path') == self.mcp_path or scope.get('path', '').startswith(self.mcp_path + '/')):
                # The separate MCP transport enforces its own token and host rules.
                await self.app(scope, receive, send)
                return
            headers = dict(scope.get('headers', []))
            try:
                host = urlsplit('//' + headers.get(b'host', b'').decode('latin1')).hostname
            except ValueError:
                host = None
            origin = headers.get(b'origin', b'').decode('latin1')
            peer = (scope.get('client') or ('', 0))[0]
            # Host and Origin checks prevent an unrelated browser page from
            # reaching an unauthenticated loopback management API.
            allowed = loopback(peer) and loopback(host)
            if origin:
                try:
                    parsed = urlsplit(origin)
                    allowed = allowed and parsed.scheme in {'http', 'https'} and parsed.netloc == headers.get(b'host', b'').decode('latin1')
                except ValueError:
                    allowed = False
            if not allowed:
                await JSONResponse({'error': '管理界面仅允许本机同源访问'}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)
