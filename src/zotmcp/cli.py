"""
CLI for Zotero MCP Unified.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from zotmcp.config import Config, get_config_path, load_config, save_config

console = Console()


def setup_logging(level: str = "INFO"):
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


@click.group()
@click.option("--config", "-c", type=click.Path(), help="Config file path")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def main(ctx, config: Optional[str], verbose: bool):
    """Zotero MCP Unified - A comprehensive Zotero MCP server."""
    ctx.ensure_object(dict)

    if config:
        ctx.obj["config_path"] = Path(config)
    else:
        ctx.obj["config_path"] = get_config_path()

    if verbose:
        setup_logging("DEBUG")
    else:
        setup_logging("INFO")


@main.command()
@click.option("--transport", "-t", type=click.Choice(["stdio", "http", "sse"]), default="stdio")
@click.option("--host", "-h", default="0.0.0.0", help="HTTP server host")
@click.option("--port", "-p", default=8765, type=int, help="HTTP server port")
@click.pass_context
def serve(ctx, transport: str, host: str, port: int):
    """Start the MCP server."""
    config = load_config(ctx.obj["config_path"])

    # Override with CLI options
    config.server.transport = transport
    config.server.host = host
    config.server.port = port

    if transport == "stdio":
        console.print("[green]Starting Zotero MCP server (stdio mode)...[/green]")
        from zotmcp.server import mcp
        mcp.run()

    elif transport in ["http", "sse"]:
        console.print(f"[green]Starting Zotero MCP server on {host}:{port}...[/green]")
        from zotmcp.transport import create_http_transport
        http = create_http_transport(config)
        http.run()


@main.command()
@click.option("--mode", "-m", type=click.Choice(["local", "web", "sqlite"]), help="Connection mode")
@click.option("--api-key", help="Zotero Web API key")
@click.option("--library-id", help="Zotero library ID")
@click.option("--sqlite-path", help="Path to zotero.sqlite")
@click.pass_context
def setup(ctx, mode: Optional[str], api_key: Optional[str], library_id: Optional[str], sqlite_path: Optional[str]):
    """Interactive setup wizard."""
    config_path = ctx.obj["config_path"]

    console.print("[bold blue]Zotero MCP Unified Setup[/bold blue]")
    console.print()

    # Load existing config or create new
    try:
        config = load_config(config_path)
    except Exception:
        config = Config()

    # Mode selection
    if not mode:
        console.print("Select connection mode:")
        console.print("  [1] local  - Connect to running Zotero app (recommended)")
        console.print("  [2] web    - Use Zotero Web API")
        console.print("  [3] sqlite - Direct database access (read-only)")
        choice = click.prompt("Choice", type=int, default=1)
        mode = ["local", "web", "sqlite"][choice - 1]

    config.zotero.mode = mode

    if mode == "web":
        if not api_key:
            console.print()
            console.print("Get your API key from: https://www.zotero.org/settings/keys")
            api_key = click.prompt("Zotero API Key")
        if not library_id:
            library_id = click.prompt("Library ID (your user ID)")

        config.zotero.api_key = api_key
        config.zotero.library_id = library_id

    elif mode == "sqlite":
        if not sqlite_path:
            # Try to find default path
            default_paths = [
                Path.home() / "Zotero" / "zotero.sqlite",
                Path.home() / ".zotero" / "zotero" / "zotero.sqlite",
            ]
            for p in default_paths:
                if p.exists():
                    sqlite_path = str(p)
                    break

            sqlite_path = click.prompt("Path to zotero.sqlite", default=sqlite_path or "")

        config.zotero.sqlite_path = sqlite_path

    # HTTP server settings
    console.print()
    if click.confirm("Enable HTTP server for remote access?", default=False):
        config.server.transport = "http"
        config.server.port = click.prompt("HTTP port", type=int, default=8765)

        if click.confirm("Set API token for authentication?", default=True):
            import secrets
            token = secrets.token_urlsafe(32)
            config.server.api_token = token
            console.print(f"[yellow]Generated API token: {token}[/yellow]")
            console.print("[yellow]Save this token - you'll need it for remote access![/yellow]")

    # Save config
    save_config(config, config_path)
    console.print()
    console.print(f"[green]Configuration saved to: {config_path}[/green]")

    # Show Claude Desktop config
    console.print()
    console.print("[bold]Claude Desktop Configuration:[/bold]")
    console.print("Add this to your claude_desktop_config.json:")
    console.print()

    claude_config = {
        "mcpServers": {
            "zotero": {
                "command": "zotero-mcp",
                "args": ["serve"],
                "env": {}
            }
        }
    }

    if mode == "local":
        claude_config["mcpServers"]["zotero"]["env"]["ZOTERO_LOCAL"] = "true"
    elif mode == "web":
        claude_config["mcpServers"]["zotero"]["env"]["ZOTERO_API_KEY"] = api_key
        claude_config["mcpServers"]["zotero"]["env"]["ZOTERO_LIBRARY_ID"] = library_id

    console.print_json(json.dumps(claude_config, indent=2))


@main.command()
@click.pass_context
def status(ctx):
    """Check Zotero connection status."""
    config = load_config(ctx.obj["config_path"])

    console.print("[bold]Zotero MCP Status[/bold]")
    console.print()

    table = Table()
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Config Path", str(ctx.obj["config_path"]))
    table.add_row("Mode", config.zotero.mode)

    if config.zotero.mode == "local":
        table.add_row("Port", str(config.zotero.local_port))
    elif config.zotero.mode == "web":
        table.add_row("Library", f"{config.zotero.library_type}/{config.zotero.library_id}")
    elif config.zotero.mode == "sqlite":
        table.add_row("Database", config.zotero.sqlite_path or "Not set")

    console.print(table)

    # Test connection
    console.print()
    console.print("Testing connection...")

    from zotmcp.clients import create_client

    async def test():
        client = create_client(config.zotero)
        return await client.is_available()

    try:
        available = asyncio.run(test())
        if available:
            console.print("[green]Connection successful![/green]")
        else:
            console.print("[red]Connection failed - Zotero not available[/red]")
    except Exception as e:
        console.print(f"[red]Connection error: {e}[/red]")


@main.command()
@click.pass_context
def config_show(ctx):
    """Show current configuration."""
    config = load_config(ctx.obj["config_path"])
    console.print_json(json.dumps(config.model_dump(), indent=2))


@main.command()
@click.argument("query")
@click.option("--limit", "-l", default=10, type=int, help="Number of results")
@click.pass_context
def search(ctx, query: str, limit: int):
    """Search Zotero library from command line."""
    config = load_config(ctx.obj["config_path"])

    from zotmcp.clients import create_client

    async def do_search():
        client = create_client(config.zotero)
        return await client.search_items(query=query, limit=limit)

    items = asyncio.run(do_search())

    if not items:
        console.print(f"No results for: {query}")
        return

    table = Table(title=f"Search Results: {query}")
    table.add_column("Key", style="cyan", width=10)
    table.add_column("Title", style="white")
    table.add_column("Authors", style="green")
    table.add_column("Date", style="yellow", width=10)

    for item in items:
        table.add_row(
            item.key,
            item.title[:50] + "..." if len(item.title) > 50 else item.title,
            item.format_creators()[:30],
            item.date or "",
        )

    console.print(table)


@main.command()
@click.pass_context
def collections(ctx):
    """List Zotero collections."""
    config = load_config(ctx.obj["config_path"])

    from zotmcp.clients import create_client

    async def get_collections():
        client = create_client(config.zotero)
        return await client.get_collections()

    colls = asyncio.run(get_collections())

    if not colls:
        console.print("No collections found")
        return

    table = Table(title="Zotero Collections")
    table.add_column("Key", style="cyan", width=10)
    table.add_column("Name", style="white")
    table.add_column("Parent", style="dim", width=10)

    for coll in colls:
        table.add_row(coll.key, coll.name, coll.parent_key or "-")

    console.print(table)


if __name__ == "__main__":
    main()
