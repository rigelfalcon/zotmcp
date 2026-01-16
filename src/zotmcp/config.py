"""
Configuration management for Zotero MCP Unified.
"""

import os
import json
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field


class ZoteroConfig(BaseModel):
    """Zotero connection configuration."""

    # Connection mode
    mode: Literal["local", "web", "sqlite"] = Field(
        default="local",
        description="Connection mode: local (Zotero app), web (API), sqlite (direct DB)"
    )

    # Local API settings (port 23119)
    local_host: str = Field(default="127.0.0.1", description="Zotero local API host (use IP for remote access)")
    local_port: int = Field(default=23119, description="Zotero local API port")

    # Web API settings
    api_key: Optional[str] = Field(default=None, description="Zotero Web API key")
    library_id: Optional[str] = Field(default=None, description="Zotero library ID")
    library_type: Literal["user", "group"] = Field(default="user")

    # SQLite settings
    sqlite_path: Optional[str] = Field(default=None, description="Path to zotero.sqlite")
    storage_path: Optional[str] = Field(default=None, description="Path to Zotero storage folder")
    linked_attachment_base: Optional[str] = Field(default=None, description="Base directory for linked attachments (attachments: prefix)")


class SemanticSearchConfig(BaseModel):
    """Semantic search configuration."""

    enabled: bool = Field(default=False, description="Enable semantic search")
    model_name: str = Field(
        default="all-MiniLM-L6-v2",
        description="Sentence transformer model name"
    )
    persist_directory: Optional[str] = Field(
        default=None,
        description="ChromaDB persist directory (None = in-memory)"
    )
    collection_name: str = Field(
        default="zotero_items",
        description="ChromaDB collection name"
    )
    batch_size: int = Field(
        default=50,
        description="Items per embedding batch"
    )


class ServerConfig(BaseModel):
    """Server configuration."""

    # Transport settings
    transport: Literal["stdio", "http", "sse"] = Field(
        default="stdio",
        description="Transport mode"
    )
    host: str = Field(default="0.0.0.0", description="HTTP server host")
    port: int = Field(default=8765, description="HTTP server port")
    cors_origins: list[str] = Field(default=["*"], description="CORS allowed origins")

    # Authentication
    api_token: Optional[str] = Field(default=None, description="API token for remote access")

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    log_file: Optional[str] = Field(default=None)


class Config(BaseModel):
    """Main configuration."""

    zotero: ZoteroConfig = Field(default_factory=ZoteroConfig)
    semantic: SemanticSearchConfig = Field(default_factory=SemanticSearchConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


def get_config_path() -> Path:
    """Get the configuration file path."""
    # Check environment variable first
    if env_path := os.environ.get("ZOTERO_MCP_CONFIG"):
        return Path(env_path)

    # Default to user config directory
    config_dir = Path.home() / ".config" / "zotero-mcp-unified"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.json"


def load_config(config_path: Optional[Path] = None) -> Config:
    """Load configuration from file and environment variables."""
    config_path = config_path or get_config_path()

    # Start with defaults
    config_data = {}

    # Load from file if exists
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)

    # Override with environment variables
    env_mappings = {
        "ZOTERO_LOCAL": ("zotero", "mode", lambda v: "local" if v.lower() in ["true", "1", "yes"] else None),
        "ZOTERO_API_KEY": ("zotero", "api_key", str),
        "ZOTERO_LIBRARY_ID": ("zotero", "library_id", str),
        "ZOTERO_LIBRARY_TYPE": ("zotero", "library_type", str),
        "ZOTERO_SQLITE_PATH": ("zotero", "sqlite_path", str),
        "ZOTERO_STORAGE_PATH": ("zotero", "storage_path", str),
        "ZOTERO_SEMANTIC_ENABLED": ("semantic", "enabled", lambda v: v.lower() in ["true", "1", "yes"]),
        "ZOTERO_SEMANTIC_MODEL": ("semantic", "model_name", str),
        "ZOTERO_SEMANTIC_PERSIST": ("semantic", "persist_directory", str),
        "ZOTERO_MCP_HOST": ("server", "host", str),
        "ZOTERO_MCP_PORT": ("server", "port", int),
        "ZOTERO_MCP_TOKEN": ("server", "api_token", str),
    }

    for env_var, (section, key, converter) in env_mappings.items():
        if value := os.environ.get(env_var):
            if section not in config_data:
                config_data[section] = {}
            converted = converter(value)
            if converted is not None:
                config_data[section][key] = converted

    return Config(**config_data)


def save_config(config: Config, config_path: Optional[Path] = None) -> None:
    """Save configuration to file."""
    config_path = config_path or get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, indent=2)
