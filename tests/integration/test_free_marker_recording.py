"""Real MCP stdio and Lua 5.1 recording preparation without Resolve or audio.

The native doubles expose no recording/playback API. Marker packets, native
track mutations, ZIP responses, receipts and restart replay are exercised.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import zipfile
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from providers.resolve.lua_transport import read_project_snapshot
from providers.resolve.marker_transport import prepare_marker

PROJECT_ID = "recording-test-project"
TRACK_NAME = "Український голос RAW"
# Marker requests stage many individual files. The integration test exercises
# the protocol, not a filesystem-speed limit on a busy Windows test host.
REQUEST_TIMEOUT_SECONDS = 30
ACK_RELEASE_TIMEOUT_SECONDS = 3 * REQUEST_TIMEOUT_SECONDS
NATIVE = """
local tracks={{name='Audio 1',subtype='stereo'}}
local page='fairlight'
local timecode='01:18:33:00'
local media='unset'
local timeline={}
function timeline:GetName() return 'Voice' end
function timeline:GetUniqueId() return 'voice-timeline' end
function timeline:GetTrackCount(kind) return kind=='audio' and #tracks or 0 end
function timeline:GetTrackName(kind,index)
    assert(kind=='audio');return tracks[index].name
end
function timeline:GetTrackSubType(kind,index)
    assert(kind=='audio');return tracks[index].subtype
end
function timeline:GetItemListInTrack() return {} end
function timeline:GetStartFrame() return 86400 end
function timeline:GetEndFrame() return 86400 end
function timeline:GetSetting(key) assert(key=='timelineFrameRate');return '24' end
function timeline:AddTrack(kind,subtype)
    assert(kind=='audio' and subtype=='mono')
    record_event('add',subtype)
    tracks[#tracks+1]={name='Audio '..tostring(#tracks+1),subtype=subtype}
    return true
end
function timeline:SetTrackName(kind,index,name)
    assert(kind=='audio');record_event('rename',name)
    tracks[index].name=name;return true
end
function timeline:SetCurrentTimecode(value)
    record_event('seek',value);timecode=value;return true
end
function timeline:GetCurrentTimecode() return timecode end
local project={}
function project:GetName() return 'Recording integration' end
function project:GetUniqueId() return 'recording-test-project' end
function project:GetTimelineCount() return 1 end
function project:GetTimelineByIndex(i) assert(i==1);return timeline end
function project:GetCurrentTimeline() return timeline end
function project:SetCurrentTimeline(value)
    assert(value==timeline);record_event('select','Voice');return true
end
function project:GetMediaPool() return {} end
function project:IsRenderingInProgress() return false end
function project:GetSettings()
    return {timelineSampleRate='48000',projectMediaLocation=media}
end
function project:SetSettings(values)
    assert(values.projectMediaLocation and not values.timelineSampleRate)
    record_event('media',values.projectMediaLocation)
    media=values.projectMediaLocation;return true
end
local manager={}
function manager:GetCurrentProject() return project end
function manager:SaveProject() record_event('save','');return true end
function manager:ExportProject(name,path)
    assert(name==project:GetName());return native_export(path)
end
resolve={GetProjectManager=function() return manager end,
    GetCurrentPage=function() return page end,
    OpenPage=function(_,value) record_event('page',value);page=value;return true end}
bmd={fileexists=function(path) return native_exists(path) end,
    wait=function(seconds) native_wait(seconds) end}
dofile=nil;loadfile=nil;loadstring=nil;io=nil
"""


def unpack(result: CallToolResult) -> dict[str, Any]:
    """Require actual structured success from the child MCP process."""
    assert not result.is_error, result.content
    value = result.structured_content
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("late_ack", [False, True], ids=["completed", "late-ack"])
def test_stdio_recording_preparation_and_restart_replay(
    tmp_path: Path,
    late_ack: bool,
) -> None:
    root, media = tmp_path / "session", tmp_path / "media"
    media.mkdir()
    script = prepare_marker(root, [media]).read_text(encoding="utf-8")
    stopped = threading.Event()
    ack_started = threading.Event()
    release_ack = threading.Event()
    failures: list[BaseException] = []
    events: list[tuple[str, str]] = []

    def export(path: str) -> bool:
        events.append(("backup" if path.endswith(".before.drp") else "export", path))
        if late_ack and ".ok_recording_ensure_mono_" in path:
            ack_started.set()
            assert release_ack.wait(ACK_RELEASE_TIMEOUT_SECONDS), (
                "Delayed recording ACK was never released"
            )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "project.xml",
                '<SM_Project DbId="recording-test-project">'
                "<ProjectName>Recording integration</ProjectName></SM_Project>",
            )
        return True

    def wait(seconds: float) -> None:
        if stopped.is_set():
            raise RuntimeError("test teardown")
        time.sleep(min(seconds, 0.01))

    def native_loop() -> None:
        try:
            lua = import_module("lupa.lua51").LuaRuntime()
            lua.globals().record_event = lambda action, value: events.append(
                (action, value)
            )
            lua.globals().native_export = export
            lua.globals().native_wait = wait
            lua.globals().native_exists = lambda path: Path(path).is_file()
            lua.execute(NATIVE)
            lua.execute(script)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=native_loop, daemon=True)
    worker.start()

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "mcp_server.server",
                "--backend",
                "free-marker",
                "--runtime",
                str(root),
            ],
        )
        ensure = {
            "timeline_name": "Voice",
            "track_name": TRACK_NAME,
            "expected_audio_track_count": 1,
            "expected_project_id": PROJECT_ID,
            "idempotency_key": "ensure-voice-track",
            "confirm": True,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        }
        created: dict[str, Any] | None = None
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert len(tools) == 50
            for name in (
                "resolve_lua_inspect_audio_track",
                "resolve_lua_ensure_mono_audio_track",
                "resolve_lua_seek_timeline",
                "resolve_lua_set_recording_media_root",
                "resolve_free_windows_recording_status",
                "resolve_free_select_audio_input",
                "resolve_free_get_audio_processing",
                "resolve_free_set_audio_eq",
                "resolve_free_set_audio_dynamics",
                "resolve_free_get_track_record_arm",
                "resolve_free_set_track_record_arm",
                "resolve_free_get_audio_input_patch",
                "resolve_free_patch_audio_input",
            ):
                assert name in tools
            properties = tools["resolve_lua_ensure_mono_audio_track"].input_schema[
                "properties"
            ]
            assert properties["confirm"]["default"] is False
            assert (
                "expected_audio_track_count"
                in tools["resolve_lua_ensure_mono_audio_track"].input_schema["required"]
            )
            live = unpack(
                await session.call_tool(
                    "resolve_free_connection_status",
                    {"timeout_seconds": REQUEST_TIMEOUT_SECONDS},
                )
            )
            assert live["connected"] and live["recording_native_api"], live
            assert live["recording_readiness"] == "not_assessed"
            original = list(events)
            for name, extras in (
                (
                    "resolve_free_set_audio_eq",
                    {"target_name": TRACK_NAME, "enabled": False},
                ),
                (
                    "resolve_free_set_audio_dynamics",
                    {"target_name": TRACK_NAME, "enabled": False},
                ),
                (
                    "resolve_free_set_track_record_arm",
                    {"track_name": TRACK_NAME, "armed": True},
                ),
                (
                    "resolve_free_patch_audio_input",
                    {
                        "track_name": TRACK_NAME,
                        "source_name": "USB Microphone",
                        "source_channel": 1,
                    },
                ),
            ):
                schema = tools[name].input_schema
                assert schema["properties"]["confirm"]["default"] is False
                assert "expected_project_id" in schema["required"]
                assert "idempotency_key" in schema["required"]
                tool_annotations = tools[name].annotations
                assert tool_annotations is not None
                assert tool_annotations.read_only_hint is False
                refused_ui = await session.call_tool(
                    name,
                    {
                        "timeline_name": "Voice",
                        "expected_track_index": 2,
                        "expected_project_id": PROJECT_ID,
                        "idempotency_key": name,
                        **extras,
                    },
                )
                assert refused_ui.is_error and events == original
            refused = await session.call_tool(
                "resolve_lua_ensure_mono_audio_track",
                {**ensure, "confirm": False},
            )
            assert refused.is_error and events == original
            inspected = unpack(
                await session.call_tool(
                    "resolve_lua_inspect_audio_track",
                    {
                        "timeline_name": "Voice",
                        "track_name": TRACK_NAME,
                        "expected_project_id": PROJECT_ID,
                        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
                    },
                )
            )
            assert inspected["recording"]["track"]["found"] is False
            pending_ensure = asyncio.create_task(
                session.call_tool("resolve_lua_ensure_mono_audio_track", ensure)
            )
            if late_ack:
                # Hold the native response only after track creation. A slow
                # marker publication must not masquerade as a lost ACK.
                reached_ack = await asyncio.to_thread(
                    ack_started.wait, REQUEST_TIMEOUT_SECONDS
                )
            outcome = await pending_ensure
            if late_ack:
                assert reached_ack, "Timeout must follow native track creation"
                assert outcome.is_error
                assert any(
                    isinstance(part, TextContent)
                    and "No fresh native marker response within the timeout."
                    in part.text
                    for part in outcome.content
                ), outcome.content
            else:
                created = unpack(outcome)

        release_ack.set()
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            replay = unpack(
                await session.call_tool(
                    "resolve_lua_ensure_mono_audio_track",
                    {**ensure, "timeout_seconds": REQUEST_TIMEOUT_SECONDS},
                )
            )
            assert replay["replayed"] is (not late_ack)
            assert replay["recording"]["track"]["subtype"] == "mono"
            assert replay["recording"]["track"]["index"] == 2
            assert replay["recording"]["track"]["name"] == TRACK_NAME
            assert replay["recording"]["sample_rate"] == 48000
            assert (
                read_project_snapshot(Path(replay["backup_path"]))["id"] == PROJECT_ID
            )
            assert replay["readback_verified_by"] == "ResolveLuaRecording"
            if late_ack:
                assert replay["reconciled_original_command"] is True
            else:
                assert created is not None
                assert replay["response_path"] == created["response_path"]
            repeated = unpack(
                await session.call_tool(
                    "resolve_lua_ensure_mono_audio_track",
                    {**ensure, "timeout_seconds": REQUEST_TIMEOUT_SECONDS},
                )
            )
            assert repeated["replayed"] is True
            assert repeated["response_path"] == replay["response_path"]
            sought = unpack(
                await session.call_tool(
                    "resolve_lua_seek_timeline",
                    {
                        "timeline_name": "Voice",
                        "timecode": "01:00:00:00",
                        "expected_project_id": PROJECT_ID,
                        "idempotency_key": "seek-voice-start",
                        "confirm": True,
                        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
                    },
                )
            )
            assert sought["recording"]["timecode"] == "01:00:00:00"
            configured = unpack(
                await session.call_tool(
                    "resolve_lua_set_recording_media_root",
                    {
                        "timeline_name": "Voice",
                        "media_root": str(media),
                        "expected_project_id": PROJECT_ID,
                        "idempotency_key": "voice-media-root",
                        "confirm": True,
                        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
                    },
                )
            )
            assert configured["recording"]["project_media_location"] == media.as_posix()
            summary = unpack(
                await session.call_tool(
                    "resolve_lua_get_timeline_summary",
                    {
                        "timeline_name": "Voice",
                        "expected_project_id": PROJECT_ID,
                        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
                    },
                )
            )
            assert summary["summary"]["audio_items"] == 0
            assert summary["summary"]["video_items"] == 0
            status = unpack(
                await session.call_tool(
                    "resolve_free_windows_recording_status",
                    {
                        "expected_project_id": PROJECT_ID,
                    },
                )
            )
            assert status["adapter"]["available"] is False
            assert status["recording_readiness"] == "not_assessed"
            stopped_result = unpack(await session.call_tool("resolve_lua_stop", {}))
            assert stopped_result["status"] == "stopping"

    try:
        asyncio.run(exercise())
    finally:
        release_ack.set()
        stopped.set()
        worker.join(5)
    assert not failures
    assert not worker.is_alive()
    assert events.count(("add", "mono")) == 1
    assert events.count(("rename", TRACK_NAME)) == 1
    assert events.count(("seek", "01:00:00:00")) == 1
    assert not any(action == "page" for action, _ in events)
    assert not any(
        "record" in action.lower() or "play" in action.lower() for action, _ in events
    )
    backup = next(i for i, event in enumerate(events) if event[0] == "backup")
    add = events.index(("add", "mono"))
    rename = events.index(("rename", TRACK_NAME))
    assert backup < add < rename
