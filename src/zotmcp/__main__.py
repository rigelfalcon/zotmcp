"""
ZotMCP Server Entry Point

Usage:
    # Local only (default)
    python -m zotmcp

    # Remote access (listen on all interfaces)
    python -m zotmcp --host 0.0.0.0 --port 8765

    # With SSE transport for HTTP clients
    python -m zotmcp --host 0.0.0.0 --port 8765 --transport sse
"""

import argparse
import logging
import sys

from zotmcp.server import create_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="ZotMCP Server - Zotero MCP Interface")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (use 0.0.0.0 for remote access)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to listen on"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type: stdio (default) or sse (HTTP/SSE for remote)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    mcp = create_server()

    if args.transport == "sse":
        logger.info(f"Starting ZotMCP server on http://{args.host}:{args.port}")
        logger.info("Other computers can connect via: http://<your-ip>:%d/sse", args.port)
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        logger.info("Starting ZotMCP server with stdio transport")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
