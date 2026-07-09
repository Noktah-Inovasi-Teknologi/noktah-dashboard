"""SSE HTTP transport entrypoint for the Client Knowledge Base MCP server.

Transport decision: specs/001-client-knowledge-base/research.md R1.
"""
import logging
import os

import uvicorn
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from server import mcp

logger = logging.getLogger("knowledge_base")


class ApiKeyMiddleware:
    """Raw ASGI middleware (not BaseHTTPMiddleware, which buffers responses and
    would break SSE streaming) validating X-API-KEY against KB_MCP_API_KEY."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return

        expected = os.getenv("KB_MCP_API_KEY")
        if expected:
            headers = Headers(scope=scope)
            if headers.get("x-api-key") != expected:
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app() -> Starlette:
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = 8080

    # Requests arrive with Host: knowledge-base:8080 inside the compose network.
    # FastMCP's DNS-rebinding protection only matches exact hosts or "base:*"
    # port-wildcard patterns (mcp.server.transport_security.TransportSecurityMiddleware)
    # — there is no true wildcard-all value, and it exists to defend browser
    # clients against cross-origin requests. Our client is AnythingLLM's backend
    # (server-to-server over an internal Docker network), not a browser page,
    # and the X-API-KEY header below is the real auth boundary, so disable it
    # rather than enumerate hosts.
    mcp.settings.transport_security.enable_dns_rebinding_protection = False

    sse_app = mcp.sse_app()
    app = Starlette(routes=[Route("/health", health), Mount("/", app=sse_app)])
    return ApiKeyMiddleware(app)


app = build_app()

if __name__ == "__main__":
    if not os.getenv("KB_MCP_API_KEY"):
        logger.warning(
            "KB_MCP_API_KEY is not set; the server will accept unauthenticated requests"
        )
    logger.info("Starting knowledge-base MCP server on 0.0.0.0:8080 (SSE transport)")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
