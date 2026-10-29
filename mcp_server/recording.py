"""Typed recording preparation tools; no microphone capture or Record action."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from providers.resolve.lua_transport import LuaSnapshotClient


def register_native_recording_tools(
    server: MCPServer, client: LuaSnapshotClient,
) -> None:
    """Register bounded native operations on the existing guarded Lua client."""
    read = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True,
        open_world_hint=False,
    )
    write = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True,
        open_world_hint=False,
    )

    @server.tool(annotations=read)
    async def resolve_lua_inspect_audio_track(
        timeline_name: str,
        track_name: str,
        expected_project_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Find one exact named audio track and read subtype/count/sample rate.

        Returns missing explicitly; refuses duplicate names. Does not switch
        timelines/pages, start recording, or assert microphone readiness.
        """
        return await asyncio.to_thread(
            client.request, "get_timeline_summary", timeout_seconds,
            arguments={"timeline_name": timeline_name, "recording": {
                "operation": "inspect", "track_name": track_name,
            }},
            expected_project_id=expected_project_id,
        )

    @server.tool(annotations=write)
    async def resolve_lua_ensure_mono_audio_track(
        timeline_name: str,
        track_name: str,
        expected_audio_track_count: int,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Reuse one matching mono track or append and name a new mono track.

        Requires the exact current audio track count. Refuses duplicate names
        and stereo conflicts. Saves/backs up before changing tracks, verifies
        native readback, and never deletes tracks, processes clips or arms
        recording. Reconcile a timeout with identical arguments and key.
        """
        return await asyncio.to_thread(
            client.request, "set_clip_properties", timeout_seconds,
            arguments={"timeline_name": timeline_name, "recording": {
                "operation": "ensure_mono", "track_name": track_name,
                "expected_audio_track_count": expected_audio_track_count,
            }},
            expected_project_id=expected_project_id, confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_seek_timeline(
        timeline_name: str,
        timecode: str,
        expected_project_id: str,
        idempotency_key: str,
        page: str | None = None,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Seek to HH:MM:SS:FF, preserving the current page unless specified.

        Use page='fairlight' or 'edit' to select it explicitly. Preparation only:
        never starts/stops playback or recording. Caller must ensure transport
        is stopped; native SDK does not expose recording-state verification.
        """
        spec: dict[str, Any] = {"operation": "seek", "timecode": timecode}
        if page is not None:
            spec["page"] = page
        return await asyncio.to_thread(
            client.request, "set_clip_properties", timeout_seconds,
            arguments={"timeline_name": timeline_name, "recording": spec},
            expected_project_id=expected_project_id, confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_set_recording_media_root(
        timeline_name: str,
        media_root: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Set project media location to an existing allowed directory.

        Uses documented project settings and exact native readback. Changes
        the project media root, not only audio; Resolve may create subfolders.
        Never moves existing files or starts capture. Requires a fresh session.
        """
        return await asyncio.to_thread(
            client.request, "set_clip_properties", timeout_seconds,
            arguments={"timeline_name": timeline_name, "recording": {
                "operation": "configure_media_root", "media_root": media_root,
            }},
            expected_project_id=expected_project_id, confirm=confirm,
            idempotency_key=idempotency_key,
        )
