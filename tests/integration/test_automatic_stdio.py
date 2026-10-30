"""Default automatic endpoint discovery without a running editor or watcher."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_default_stdio_exposes_both_catalogues_without_manual_start(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "schema": 1,
                "enabled": False,
                "runtime_base": str(root),
                "media_roots": [],
            }
        )
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "mcp_server.server",
            "--managed-profile",
            str(profile),
        ],
    )

    async def scenario() -> None:
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as client,
        ):
            await client.initialize()
            tools = await client.list_tools()
            assert len(tools.tools) == 156
            report = await client.call_tool(
                "video_agent_capabilities",
                {
                    "verify_connection": True,
                },
            )
            assert not report.is_error
            assert report.structured_content is not None
            assert report.structured_content["backend"] == "auto"
            assert report.structured_content["connected"] is False

    asyncio.run(scenario())
