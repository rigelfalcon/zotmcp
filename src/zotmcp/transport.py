"""
HTTP/SSE Transport for remote access to Zotero MCP.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from aiohttp import web

from zotmcp.config import Config, load_config
from zotmcp.server import mcp

logger = logging.getLogger(__name__)


class HTTPTransport:
    """HTTP/SSE transport for MCP server."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or load_config()
        self.app = web.Application()
        self._setup_routes()
        self._sse_clients: dict[str, web.StreamResponse] = {}

    def _setup_routes(self):
        """Setup HTTP routes."""
        self.app.router.add_get("/health", self._health_check)
        self.app.router.add_get("/tools", self._list_tools)
        self.app.router.add_post("/tools/{tool_name}", self._call_tool)
        self.app.router.add_get("/sse", self._sse_handler)
        self.app.router.add_post("/sse", self._sse_handler)  # OpenCode uses POST
        self.app.router.add_post("/messages", self._messages_handler)  # MCP SSE protocol
        self.app.router.add_post("/mcp", self._messages_handler)  # MCP Streamable HTTP (Claude Code 2.1+)
        self.app.router.add_get("/mcp", self._sse_handler)  # Streamable HTTP SSE fallback
        # OAuth/OIDC discovery endpoints - return 404 to prevent 405 from catch-all
        # Claude Code 2.1+ probes all of these before SSE connection
        self.app.router.add_get("/.well-known/{path:.*}", self._not_found)
        self.app.router.add_post("/register", self._not_found)
        # Catch-all for any unregistered GET/POST paths (returns 404 instead of 405)
        self.app.router.add_get("/{path:.*}", self._not_found)
        self.app.router.add_post("/{path:.*}", self._not_found)
        self.app.router.add_options("/{path:.*}", self._cors_preflight)

        # Add CORS middleware
        self.app.middlewares.append(self._cors_middleware)

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        """CORS middleware for cross-origin requests."""
        response = await handler(request)
        origin = ", ".join(self.config.server.cors_origins)
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response

    async def _not_found(self, request: web.Request) -> web.Response:
        """Return 404 for unimplemented endpoints.

        Claude Code bug #46640: OAuth probe triggers auth flow on any non-200.
        We return plain 404 with no body to minimize false positives.
        """
        return web.Response(status=404, body=b"", content_type="text/plain")

    async def _cors_preflight(self, request: web.Request) -> web.Response:
        """Handle CORS preflight requests."""
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": ", ".join(self.config.server.cors_origins),
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )

    def _check_auth(self, request: web.Request) -> bool:
        """Check authentication if token is configured."""
        if not self.config.server.api_token:
            return True

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return token == self.config.server.api_token

    def _get_timeout(self, request: web.Request) -> Optional[float]:
        """Get timeout from request header."""
        timeout_header = request.headers.get("X-Timeout")
        if timeout_header:
            try:
                return float(timeout_header)
            except ValueError:
                pass
        return None
        return False

    async def _health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        from zotmcp.clients import create_client

        client = create_client(self.config.zotero)
        available = await client.is_available()

        return web.json_response(
            {
                "status": "healthy" if available else "degraded",
                "zotero_available": available,
                "mode": self.config.zotero.mode,
            }
        )

    async def _list_tools(self, request: web.Request) -> web.Response:
        """List available tools."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        # Get tools from MCP server (FastMCP 2.x API)
        tools = []
        tool_manager = getattr(mcp, "_tool_manager", None)
        if tool_manager:
            for name, tool in tool_manager._tools.items():
                tools.append(
                    {
                        "name": name,
                        "description": getattr(tool, "description", ""),
                    }
                )

        return web.json_response({"tools": tools})

    async def _call_tool(self, request: web.Request) -> web.Response:
        """Call a specific tool."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        tool_name = request.match_info["tool_name"]

        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}

        # Find the tool (FastMCP 2.x API)
        tool_manager = getattr(mcp, "_tool_manager", None)
        tool = tool_manager._tools.get(tool_name) if tool_manager else None
        if not tool:
            return web.json_response(
                {"error": f"Tool not found: {tool_name}"},
                status=404,
            )

        try:
            # Create a mock context for the tool
            class MockContext:
                def info(self, msg):
                    logger.info(msg)

                def warn(self, msg):
                    logger.warning(msg)

                def error(self, msg):
                    logger.error(msg)

            # Call the tool function
            result = await tool.fn(**body, ctx=MockContext())

            return web.json_response(
                {
                    "tool": tool_name,
                    "result": result,
                }
            )

        except Exception as e:
            logger.exception(f"Error calling tool {tool_name}")
            return web.json_response(
                {"error": str(e)},
                status=500,
            )

    async def _messages_handler(self, request: web.Request) -> web.Response:
        """Handle MCP protocol messages (POST /messages)."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Handle MCP JSON-RPC messages
        method = body.get("method", "")
        params = body.get("params", {})
        msg_id = body.get("id")


        # MCP notifications (no id) - accept silently per spec
        if method.startswith("notifications/"):
            return web.Response(status=202)
        try:
            if method == "initialize":
                # MCP initialize handshake
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": "zotero-mcp-unified",
                        "version": "0.1.0",
                    },
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                }
            elif method == "tools/list":
                # List available tools
                tools = []
                tool_manager = getattr(mcp, "_tool_manager", None)
                if tool_manager:
                    for name, tool in tool_manager._tools.items():
                        tools.append(
                            {
                                "name": name,
                                "description": getattr(tool, "description", ""),
                                "inputSchema": getattr(
                                    tool, "parameters", {"type": "object", "properties": {}}
                                ),
                            }
                        )
                result = {"tools": tools}
            elif method == "tools/call":
                # Call a tool
                tool_name = params.get("name")
                arguments = params.get("arguments", {})

                tool_manager = getattr(mcp, "_tool_manager", None)
                tool = tool_manager._tools.get(tool_name) if tool_manager else None
                if not tool:
                    tnf_response = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
                    }
                    sid = request.rel_url.query.get("sessionId")
                    if sid and sid in self._sse_clients:
                        await self._send_sse_event(self._sse_clients[sid], "message", tnf_response)
                        return web.Response(status=202)
                    return web.json_response(tnf_response)

                class MockContext:
                    def info(self, msg):
                        logger.info(msg)

                    def warn(self, msg):
                        logger.warning(msg)

                    def error(self, msg):
                        logger.error(msg)

                tool_result = await tool.fn(**arguments, ctx=MockContext())
                result = {
                    "content": [{"type": "text", "text": str(tool_result)}],
                    "isError": False,
                }
            elif method == "ping":
                result = {}
            else:
                mnf_response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
                sid = request.rel_url.query.get("sessionId")
                if sid and sid in self._sse_clients:
                    await self._send_sse_event(self._sse_clients[sid], "message", mnf_response)
                    return web.Response(status=202)
                return web.json_response(mnf_response)

            # Build JSON-RPC response
            rpc_response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": result,
            }

            # If request has sessionId, push via SSE stream (legacy SSE protocol)
            sid = request.rel_url.query.get("sessionId")
            if sid and sid in self._sse_clients:
                await self._send_sse_event(
                    self._sse_clients[sid],
                    "message",
                    rpc_response,
                )
                return web.Response(status=202)

            # Otherwise return as HTTP response (Streamable HTTP)
            return web.json_response(rpc_response)

        except Exception as e:
            logger.exception(f"Error handling MCP message: {method}")
            err_response = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(e)}}
            sid = request.rel_url.query.get("sessionId")
            if sid and sid in self._sse_clients:
                await self._send_sse_event(self._sse_clients[sid], "message", err_response)
                return web.Response(status=202)
            return web.json_response(err_response)

    async def _sse_handler(self, request: web.Request) -> web.StreamResponse:
        """Server-Sent Events handler for MCP protocol."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        # Generate session ID for this connection
        session_id = str(uuid.uuid4())

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": ", ".join(self.config.server.cors_origins),
            },
        )
        await response.prepare(request)

        self._sse_clients[session_id] = response

        try:
            # Send MCP SSE protocol endpoint event (required by OpenCode)
            await self._send_sse_event(
                response,
                "endpoint",
                f"/messages?sessionId={session_id}",
            )

            # Keep connection alive
            while True:
                await asyncio.sleep(30)
                await self._send_sse_event(
                    response, "ping", {"timestamp": asyncio.get_event_loop().time()}
                )

        except asyncio.CancelledError:
            pass
        finally:
            self._sse_clients.pop(session_id, None)

        return response

    async def _send_sse_event(
        self,
        response: web.StreamResponse,
        event: str,
        data: Any,
    ):
        """Send an SSE event."""
        # For strings, use raw value to avoid extra quotes from json.dumps
        if isinstance(data, str):
            data_str = data
        else:
            data_str = json.dumps(data)

        message = f"event: {event}\ndata: {data_str}\n\n"
        await response.write(message.encode("utf-8"))

    async def broadcast_event(self, event: str, data: Any):
        """Broadcast an event to all SSE clients."""
        for client in self._sse_clients.values():
            try:
                await self._send_sse_event(client, event, data)
            except Exception:
                pass

    def run(self):
        """Run the HTTP server."""
        host = self.config.server.host
        port = self.config.server.port

        logger.info(f"Starting HTTP server on {host}:{port}")
        web.run_app(self.app, host=host, port=port)

    async def start(self):
        """Start the server asynchronously."""
        runner = web.AppRunner(self.app)
        await runner.setup()

        site = web.TCPSite(
            runner,
            self.config.server.host,
            self.config.server.port,
        )
        await site.start()

        logger.info(f"HTTP server started on {self.config.server.host}:{self.config.server.port}")

        return runner


def create_http_transport(config: Optional[Config] = None) -> HTTPTransport:
    """Create HTTP transport instance."""
    return HTTPTransport(config)
