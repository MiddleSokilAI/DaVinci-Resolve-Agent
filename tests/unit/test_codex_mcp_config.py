"""Contract tests for the project-scoped Codex MCP configuration."""

from __future__ import annotations

import asyncio
import os
import sys
import sysconfig
from pathlib import Path
from typing import Any, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


REPOSITORY_ROOT = Path(__file__).parents[2]
CODEX_DIRECTORY = REPOSITORY_ROOT / ".codex"
CONFIG_PATH = CODEX_DIRECTORY / "config.toml"


def _load_server_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as config_file:
        document: dict[str, Any] = tomllib.load(config_file)
    return cast(dict[str, Any], document["mcp_servers"]["davinci-resolve-agent"])


def test_codex_mcp_config_is_portable_and_safe_by_default() -> None:
    server = _load_server_config()
    command = server["command"]

    assert isinstance(command, str)
    assert not Path(command).is_absolute()
    assert ":" not in command
    assert "Users" not in command
    assert server["default_tools_approval_mode"] == "writes"
    assert server["required"] is False
    assert server["tool_timeout_sec"] > 300


def test_codex_mcp_entry_point_starts_the_installed_stdio_server() -> None:
    server = _load_server_config()
    # The portable project template uses a developer .venv, while CI may install
    # into the selected base interpreter. Test the same installed entry point.
    entry_point = Path(server["command"]).name
    if os.name != "nt":
        entry_point = Path(entry_point).stem
    executable = Path(sysconfig.get_path("scripts")) / entry_point
    assert executable.is_file()

    async def initialize_server() -> set[str]:
        parameters = StdioServerParameters(command=str(executable), args=[])
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            return {tool.name for tool in tools.tools}

    tool_names = asyncio.run(initialize_server())
    assert "video_agent_status" in tool_names
    assert "resolve_get_project" in tool_names
    assert "resolve_start_render_job" in tool_names
