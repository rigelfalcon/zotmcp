"""Integration tests for HTTP transport."""

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from unittest.mock import patch, MagicMock, AsyncMock

from zotmcp.transport import HTTPTransport
from zotmcp.config import Config, ZoteroConfig, SemanticSearchConfig, ServerConfig


class TestHTTPTransport(AioHTTPTestCase):
    """Test HTTP transport functionality."""

    async def get_application(self):
        """Create test application."""
        config = Config(
            zotero=ZoteroConfig(mode="local", local_port=23119),
            semantic=SemanticSearchConfig(enabled=False),
            server=ServerConfig(
                host="127.0.0.1",
                port=8765,
                cors_origins=["*"],
                api_token="test-token-123",
            ),
        )
        self.transport = HTTPTransport(config)
        return self.transport.app

    @unittest_run_loop
    async def test_health_check(self):
        """Test health check endpoint."""
        with patch("zotmcp.clients.create_client") as mock_create:
            mock_client = AsyncMock()
            mock_client.is_available = AsyncMock(return_value=True)
            mock_create.return_value = mock_client

            resp = await self.client.request("GET", "/health")
            assert resp.status == 200

            data = await resp.json()
            assert data["status"] == "healthy"
            assert data["zotero_available"] is True
            assert data["mode"] == "local"

    @unittest_run_loop
    async def test_health_check_degraded(self):
        """Test health check when Zotero unavailable."""
        with patch("zotmcp.clients.create_client") as mock_create:
            mock_client = AsyncMock()
            mock_client.is_available = AsyncMock(return_value=False)
            mock_create.return_value = mock_client

            resp = await self.client.request("GET", "/health")
            assert resp.status == 200

            data = await resp.json()
            assert data["status"] == "degraded"
            assert data["zotero_available"] is False

    @unittest_run_loop
    async def test_list_tools_unauthorized(self):
        """Test tools listing without auth token."""
        resp = await self.client.request("GET", "/tools")
        assert resp.status == 401

        data = await resp.json()
        assert data["error"] == "Unauthorized"

    @unittest_run_loop
    async def test_list_tools_invalid_token(self):
        """Test tools listing with invalid token."""
        resp = await self.client.request(
            "GET",
            "/tools",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status == 401

    @unittest_run_loop
    async def test_list_tools_valid_token(self):
        """Test tools listing with valid token."""
        resp = await self.client.request(
            "GET",
            "/tools",
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert resp.status == 200

        data = await resp.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)

    @unittest_run_loop
    async def test_cors_headers(self):
        """Test CORS headers are present."""
        resp = await self.client.request("GET", "/health")

        assert "Access-Control-Allow-Origin" in resp.headers
        assert "Access-Control-Allow-Methods" in resp.headers

    @unittest_run_loop
    async def test_cors_preflight(self):
        """Test CORS preflight request."""
        resp = await self.client.request("OPTIONS", "/tools")
        assert resp.status == 204

        assert resp.headers["Access-Control-Allow-Origin"] == "*"
        assert "GET" in resp.headers["Access-Control-Allow-Methods"]
        assert "POST" in resp.headers["Access-Control-Allow-Methods"]
        assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]

    @unittest_run_loop
    async def test_call_tool_unauthorized(self):
        """Test tool call without auth."""
        resp = await self.client.request(
            "POST",
            "/tools/zotero_status",
            json={},
        )
        assert resp.status == 401

    @unittest_run_loop
    async def test_call_tool_not_found(self):
        """Test calling non-existent tool."""
        resp = await self.client.request(
            "POST",
            "/tools/nonexistent_tool",
            headers={"Authorization": "Bearer test-token-123"},
            json={},
        )
        assert resp.status == 404

        data = await resp.json()
        assert "not found" in data["error"].lower()


@pytest.mark.asyncio
async def test_transport_no_auth_required():
    """Test transport without auth token configured."""
    config = Config(
        zotero=ZoteroConfig(mode="local"),
        semantic=SemanticSearchConfig(enabled=False),
        server=ServerConfig(
            host="127.0.0.1",
            port=8766,
            cors_origins=["*"],
            api_token=None,  # No token required
        ),
    )
    transport = HTTPTransport(config)

    # _check_auth should return True when no token is configured
    mock_request = MagicMock()
    mock_request.headers = {}
    assert transport._check_auth(mock_request) is True


@pytest.mark.asyncio
async def test_transport_timeout_header():
    """Test timeout extraction from headers."""
    config = Config(
        zotero=ZoteroConfig(mode="local"),
        semantic=SemanticSearchConfig(enabled=False),
        server=ServerConfig(host="127.0.0.1", port=8767),
    )
    transport = HTTPTransport(config)

    # Test valid timeout header
    mock_request = MagicMock()
    mock_request.headers = {"X-Timeout": "60.5"}
    assert transport._get_timeout(mock_request) == 60.5

    # Test missing timeout header
    mock_request.headers = {}
    assert transport._get_timeout(mock_request) is None

    # Test invalid timeout header
    mock_request.headers = {"X-Timeout": "invalid"}
    assert transport._get_timeout(mock_request) is None


@pytest.mark.asyncio
async def test_sse_connection_unauthorized():
    """Test SSE connection without auth."""
    from aiohttp.test_utils import TestClient, TestServer

    config = Config(
        zotero=ZoteroConfig(mode="local"),
        semantic=SemanticSearchConfig(enabled=False),
        server=ServerConfig(
            host="127.0.0.1",
            port=8768,
            api_token="secret-token",
        ),
    )
    transport = HTTPTransport(config)

    async with TestClient(TestServer(transport.app)) as client:
        resp = await client.get("/sse")
        assert resp.status == 401
