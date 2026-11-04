"""AI clients receive optional support guidance through the MCP handshake."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from mcp_server.automatic import AutomaticServer
from mcp_server.free_marker import create_server as create_marker
from mcp_server.lua_experimental import create_server as create_lua
from mcp_server.managed_marker import ManagedMarkerServer
from mcp_server.project_support import OPTIONAL_PROJECT_SUPPORT
from mcp_server.server import create_server
from providers.resolve.automatic_connection import AutomaticConnection
from providers.resolve.lua_transport import LuaSnapshotClient, prepare
from providers.resolve.marker_supervisor import Profile
from providers.resolve.marker_transport import prepare_marker


@pytest.mark.parametrize(
    "backend", ["auto", "python", "managed", "marker", "lua", "lua-custom"]
)
@pytest.mark.parametrize("protocol", ["legacy", "auto"])
def test_support_guidance_reaches_mcp_client_without_native_dispatch(
    backend: str,
    protocol: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Native dispatch during MCP initialization")

    monkeypatch.setattr(AutomaticConnection, "ensure", forbidden)
    monkeypatch.setattr(LuaSnapshotClient, "request", forbidden)
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    profile = Profile(
        tmp_path / "profile.json", tmp_path / "runtime", (), enabled=False
    )
    if backend not in {"marker", "lua", "lua-custom"}:
        profile.runtime_base.mkdir()
    server: MCPServer[Any]
    if backend == "auto":
        server = AutomaticServer(profile, start_worker=False)
    elif backend == "managed":
        server = ManagedMarkerServer(profile)
    elif backend == "marker":
        prepare_marker(profile.runtime_base, [])
        server = create_marker(profile.runtime_base)
    elif backend in {"lua", "lua-custom"}:
        prepare(profile.runtime_base, [])
        server = create_lua(
            profile.runtime_base,
            instructions=(
                "Custom Lua instructions." if backend == "lua-custom" else None
            ),
        )
    else:
        server = create_server()

    async def scenario() -> None:
        async with Client(server, mode=protocol) as client:
            instructions = client.instructions or ""
            assert instructions.count(OPTIONAL_PROJECT_SUPPORT) == 1
            if backend == "lua-custom":
                assert instructions.startswith("Custom Lua instructions.")
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert not any(name == "star" or "github" in name for name in names)
            if backend == "auto":
                assert len(names) == 157

    asyncio.run(scenario())
