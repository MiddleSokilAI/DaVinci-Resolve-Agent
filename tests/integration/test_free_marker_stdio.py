"""Real MCP stdio through native marker decoding and Lua-owned API doubles.

The test does not run Resolve. It exercises the actual generated Lua bundle,
filesystem markers, command guards, ZIP responses and persistent receipts.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import zipfile
from importlib import import_module
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

from providers.resolve.marker_transport import prepare_marker

NATIVE_DOUBLE = """
local timelines={}
local current=nil
local pool={}
function pool:CreateEmptyTimeline(name)
    record_creation(name)
    local timeline={GetName=function() return name end,
      GetUniqueId=function() return 'timeline-'..tostring(#timelines+1) end}
    local identity='timeline-'..tostring(#timelines+1)
    timeline.GetUniqueId=function() return identity end
    timelines[#timelines+1]=timeline
    current=timeline
    return timeline
end
local project={
  GetName=function() return 'Marker test' end,
  GetUniqueId=function() return 'marker-test-project' end,
  GetTimelineCount=function() return #timelines end,
  GetTimelineByIndex=function(_,i) return timelines[i] end,
  GetCurrentTimeline=function() return current end,
  SetCurrentTimeline=function(_,value) current=value;return true end,
  GetMediaPool=function() return pool end,
  IsRenderingInProgress=function() return false end,
}
local pm={GetCurrentProject=function() return project end,
  SaveProject=function() return true end,
  ExportProject=function(_,name,path) return native_export(path) end}
resolve={GetProjectManager=function() return pm end}
bmd={fileexists=function(path) return native_exists(path) end,
  wait=function(seconds) native_wait(seconds) end}
-- The targeted Free host has no disk loaders or preference-based data channel.
dofile=nil;loadfile=nil;loadstring=nil;io=nil
"""


def unpack(result: CallToolResult) -> dict[str, Any]:
    """Require structured successful tool output from the real MCP session."""
    assert not result.is_error, result.content
    payload = result.structured_content
    assert isinstance(payload, dict)
    return payload


def test_marker_stdio_survives_client_restart_and_replays_without_edit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session"
    script = prepare_marker(root).read_text(encoding="utf-8")
    stopped = threading.Event()
    errors: list[BaseException] = []
    creations: list[str] = []
    exports: list[str] = []

    def native_export(path: str) -> bool:
        exports.append(path)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "project.xml",
                '<SM_Project DbId="marker-test-project">'
                "<ProjectName>Marker test</ProjectName></SM_Project>",
            )
        return True

    def native_wait(seconds: float) -> None:
        if stopped.is_set():
            raise RuntimeError("test teardown")
        time.sleep(min(seconds, 0.01))

    def native_loop() -> None:
        try:
            lua = import_module("lupa.lua51").LuaRuntime()
            lua.globals().native_export = native_export
            lua.globals().native_exists = lambda path: Path(path).is_file()
            lua.globals().native_wait = native_wait
            lua.globals().record_creation = creations.append
            lua.execute(NATIVE_DOUBLE)
            lua.execute(script)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=native_loop, daemon=True)
    worker.start()

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m", "mcp_server.server", "--backend", "free-marker",
                "--runtime", str(root),
            ],
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert "resolve_free_connection_status" in names
            assert "resolve_lua_create_timeline" in names
            assert "resolve_lua_reload_modules" not in names
            assert not any("eval" in name or "run_lua" in name for name in names)
            live = unpack(await session.call_tool("resolve_free_connection_status", {}))
            assert live["connected"] is True, json.dumps(live)
            assert live["project"]["id"] == "marker-test-project"
            assert "record_arm" in live["unsupported_recording_controls"]
            arguments = {
                "name": "Нова доріжка тесту",
                "expected_project_id": "marker-test-project",
                "idempotency_key": "stdio-edit",
                "confirm": True,
            }
            rejected = await session.call_tool(
                "resolve_lua_create_timeline", {**arguments, "confirm": False}
            )
            assert rejected.is_error
            created = unpack(
                await session.call_tool("resolve_lua_create_timeline", arguments)
            )
            assert created["status"] == "completed"
            assert Path(created["backup_path"]).is_file()

        # The MCP process exits; the native Lua service and original replay
        # state stay alive. A new stdio process reconnects to the same session.
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            replay = unpack(
                await session.call_tool("resolve_lua_create_timeline", arguments)
            )
            assert replay["replayed"] is True
            live = unpack(await session.call_tool("resolve_free_connection_status", {}))
            assert live["connected"] is True, json.dumps(live)
            stopped_result = unpack(await session.call_tool("resolve_lua_stop", {}))
            assert stopped_result["status"] == "stopping"

    try:
        asyncio.run(exercise())
    finally:
        stopped.set()
        worker.join(5)
    assert not errors
    assert not worker.is_alive()
    assert creations == ["Нова доріжка тесту"]
    assert any(path.endswith(".before.drp") for path in exports)
    assert (root / "marker-exit-acknowledged_stop.drp").is_file()
