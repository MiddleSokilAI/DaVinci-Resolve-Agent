"""Real managed MCP stdio discovery while Resolve and native bootstrap are absent."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_managed_stdio_catalogue_and_waiting_status(tmp_path: Path) -> None:
    base = tmp_path / "runtimes"
    base.mkdir()
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "schema": 1, "enabled": True,
        "runtime_base": str(base), "media_roots": [str(tmp_path)],
    }), encoding="utf-8")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server", "--backend", "free-marker",
              "--managed-profile", str(profile)],
    )

    async def scenario() -> None:
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as client,
        ):
            await client.initialize()
            tools = await client.list_tools()
            assert len(tools.tools) == 54
            assert "resolve_free_open_project" in {t.name for t in tools.tools}
            assert "resolve_free_autostart_status" in {t.name for t in tools.tools}
            capabilities = await client.call_tool("video_agent_capabilities", {
                "verify_connection": True
            })
            assert not capabilities.is_error
            assert capabilities.structured_content is not None
            assert capabilities.structured_content["connected"] is False
            assert not any(
                row["available"]
                for row in capabilities.structured_content["workflows"]
            )
            result = await client.call_tool("resolve_free_autostart_status", {})
            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["state"] == "watcher_status_unknown"
            assert result.structured_content["native_connection_verified"] is False
            native = await client.call_tool("resolve_free_connection_status", {})
            assert native.is_error
            assert not list(base.iterdir())
            assert not list((tmp_path / "catalogue-runtime").glob("*.drp"))
            assert not (tmp_path / "catalogue-runtime" / "inbox").exists()

    asyncio.run(scenario())
