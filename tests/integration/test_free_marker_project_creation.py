"""Project identity transitions over real MCP, marker packets and Lua 5.1.

Resolve is not launched. Native API doubles deliberately invalidate the old
project after creation, and their exports are real ZIP project snapshots.
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
from xml.etree import ElementTree as ET

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

from providers.resolve.lua_transport import read_project_snapshot
from providers.resolve.marker_transport import prepare_marker

OLD_ID = "source-project-id"
NEW_ID = "created-project-id"
PROJECT_NAME = "Український проєкт & голос"

NATIVE_PROJECT_MANAGER = """
local function project(name,identity)
    local alive=true
    local function valid() assert(alive,'old project userdata was accessed') end
    return {
        GetName=function() valid(); return name end,
        GetUniqueId=function() valid(); return identity end,
        IsRenderingInProgress=function() valid(); return false end,
        GetTimelineCount=function() valid(); return 0 end,
        GetCurrentTimeline=function() valid(); return nil end,
        Invalidate=function() alive=false end,
        -- Creating a project belongs to ProjectManager, not MediaPool.
        GetMediaPool=function() error('unexpected MediaPool dependency') end,
    }
end
local current=project('Source project','source-project-id')
local names={'Source project'}
local manager={}
function manager:GetCurrentProject() return current end
function manager:GetCurrentFolder() return 'Test folder' end
function manager:GetProjectListInCurrentFolder() return names end
function manager:SaveProject()
    record_call('save',current:GetUniqueId())
    return true
end
function manager:ExportProject(name,path)
    assert(name==current:GetName(),'export requested a noncurrent project')
    return native_export(path,current:GetUniqueId(),name)
end
function manager:CreateProject(name)
    record_call('create',current:GetUniqueId())
    record_creation(name)
    for _,existing in ipairs(names) do assert(existing~=name,'duplicate creation') end
    names[#names+1]=name
    current:Invalidate()
    current=project(name,'created-project-id')
    return current
end
resolve={GetProjectManager=function() return manager end}
bmd={fileexists=function(path) return native_exists(path) end,
     wait=function(seconds) native_wait(seconds) end}
dofile=nil;loadfile=nil;loadstring=nil;io=nil
"""


def unpack(result: CallToolResult) -> dict[str, Any]:
    """Require a structured successful response from the actual MCP process."""
    assert not result.is_error, result.content
    payload = result.structured_content
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize("late_ack", [False, True], ids=["completed", "late-ack"])
def test_marker_project_creation_survives_identity_change_and_client_restart(
    tmp_path: Path, late_ack: bool,
) -> None:
    root = tmp_path / "session"
    script = prepare_marker(root).read_text(encoding="utf-8")
    stopped = threading.Event()
    ack_started = threading.Event()
    release_ack = threading.Event()
    errors: list[BaseException] = []
    creations: list[str] = []
    calls: list[tuple[str, str]] = []

    def native_export(path: str, identity: str, name: str) -> bool:
        calls.append(("backup" if path.endswith(".before.drp") else "export", identity))
        if late_ack and ".ok_project_" in path:
            ack_started.set()
            assert release_ack.wait(30), "Late acknowledgement was never released"
        element = ET.Element("SM_Project", DbId=identity)
        ET.SubElement(element, "ProjectName").text = name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("project.xml", ET.tostring(element, encoding="utf-8"))
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
            lua.globals().record_call = lambda name, identity: calls.append(
                (name, identity)
            )
            lua.execute(NATIVE_PROJECT_MANAGER)
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
        arguments = {
            "name": PROJECT_NAME,
            "expected_project_id": OLD_ID,
            "idempotency_key": "create-project-stdio",
            "confirm": True,
            "timeout_seconds": 2 if late_ack else 5,
        }
        original_command: str | None = None
        created: dict[str, Any] | None = None
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert "resolve_lua_create_project" in tools
            annotations = tools["resolve_lua_create_project"].annotations
            assert annotations is not None
            assert annotations.read_only_hint is False
            assert annotations.idempotent_hint is True
            live = unpack(await session.call_tool("resolve_free_connection_status", {}))
            assert live["connected"] and live["project"]["id"] == OLD_ID
            before = list(calls)
            rejected = await session.call_tool(
                "resolve_lua_create_project", {**arguments, "confirm": False}
            )
            assert rejected.is_error
            assert calls == before
            result = await session.call_tool("resolve_lua_create_project", arguments)
            if late_ack:
                assert result.is_error
                assert ack_started.is_set(), "Timeout must follow native creation"
                receipts = list((root / "receipts").glob("*.json"))
                assert len(receipts) == 1
                pending = json.loads(receipts[0].read_text(encoding="utf-8"))
                assert pending["status"] == "pending"
                original_command = pending["command_id"]
                blocked = await session.call_tool(
                    "resolve_lua_create_project",
                    {
                        **arguments,
                        "name": "Another project",
                        "idempotency_key": "another-key",
                    },
                )
                assert blocked.is_error
            else:
                created = unpack(result)

        # A lost client must not cause another project creation. The native
        # loop remains alive, with its original command and new active project.
        assert creations == [PROJECT_NAME]
        release_ack.set()
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            recovered = unpack(
                await session.call_tool(
                    "resolve_lua_create_project", {**arguments, "timeout_seconds": 5}
                )
            )
            assert recovered["replayed"] is (not late_ack)
            assert recovered["status"] == "completed"
            assert recovered["previous_project_id"] == OLD_ID
            assert recovered["project"] == {"id": NEW_ID, "name": PROJECT_NAME}
            assert recovered["readback_verified_by"] == "ResolveLuaEditing"
            assert read_project_snapshot(Path(recovered["backup_path"]))["id"] == OLD_ID
            assert read_project_snapshot(Path(recovered["response_path"])) == {
                "id": NEW_ID, "name": PROJECT_NAME,
            }
            response = Path(recovered["response_path"])
            assert response.name.endswith(f".ok_project_{NEW_ID}.drp")
            if original_command:
                assert response.name.startswith(original_command + ".")
            if created:
                assert recovered["response_path"] == created["response_path"]
            replay = unpack(
                await session.call_tool(
                    "resolve_lua_create_project", {**arguments, "timeout_seconds": 5}
                )
            )
            assert replay["replayed"] is True
            changed = await session.call_tool(
                "resolve_lua_create_project", {**arguments, "name": "Changed name"}
            )
            assert changed.is_error
            live = unpack(await session.call_tool("resolve_free_connection_status", {}))
            assert live["connected"] and live["project"]["id"] == NEW_ID
            pending = unpack(
                await session.call_tool("resolve_free_pending_requests", {})
            )
            assert pending["pending_receipts"] == [] and pending["inbox"] is None
            stopped_result = unpack(await session.call_tool("resolve_lua_stop", {}))
            assert stopped_result["status"] == "stopping"

    try:
        asyncio.run(exercise())
    finally:
        release_ack.set()
        stopped.set()
        worker.join(5)
    assert not errors
    assert not worker.is_alive()
    assert creations == [PROJECT_NAME]
    assert calls.count(("create", OLD_ID)) == 1
    old_save = calls.index(("save", OLD_ID))
    old_backup = calls.index(("backup", OLD_ID))
    create_call = calls.index(("create", OLD_ID))
    new_save = calls.index(("save", NEW_ID))
    assert old_save < old_backup < create_call < new_save
    exit_export = root / "marker-exit-acknowledged_stop.drp"
    assert read_project_snapshot(exit_export)["id"] == NEW_ID
