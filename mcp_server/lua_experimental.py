"""Opt-in MCP prototype with a bounded, guarded Lua editing surface."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from agent import __version__
from providers.resolve.lua_cleanup import cleanup_responses
from providers.resolve.lua_transport import LuaSnapshotClient, prepare


def create_server(
    root: Path,
    *,
    name: str = "davinci-resolve-lua-experimental",
    instructions: str | None = None,
    excluded_tools: frozenset[str] = frozenset(),
) -> MCPServer:
    client = LuaSnapshotClient(root)
    server = MCPServer(
        name=name,
        version=__version__,
        instructions=instructions or (
            "Experimental Lua snapshot bridge. Requires a saved open "
            "project and a manually bootstrapped bridge (no session expiry). "
            "Requests run without mouse or keyboard input. Each request exports "
            "the project locally; not suitable for large projects. Writes require "
            "expected project ID, confirmation, and a stable idempotency key. "
            "After a timeout never retry with a new key: the edit may have happened."
        ),
    )
    read = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    @server.tool(annotations=read)
    async def resolve_lua_ping(timeout_seconds: float = 30) -> dict[str, Any]:
        """Verify a fresh background round-trip; requires an open project."""
        return await asyncio.to_thread(client.request, "ping", timeout_seconds)

    @server.tool(annotations=read)
    async def resolve_lua_get_project(timeout_seconds: float = 30) -> dict[str, Any]:
        """Read project identity from a fresh export; no project edits or save."""
        return await asyncio.to_thread(
            client.request, "get_current_project", timeout_seconds
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        )
    )
    async def resolve_lua_stop(timeout_seconds: float = 30) -> dict[str, Any]:
        """Stop the Lua loop after a final export acknowledges the request."""
        return await asyncio.to_thread(client.request, "stop", timeout_seconds)

    write = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    @server.tool(annotations=write)
    async def resolve_lua_quit(
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Save, back up, and request Resolve exit on explicit user instruction.

        Refuses during rendering or if project identity changed. An accepted
        response does not prove process exit. Never retry with a new key.
        Requires a newly prepared bridge containing quit support.
        """
        return await asyncio.to_thread(
            client.request,
            "quit_resolve",
            timeout_seconds,
            expected_project_id=expected_project_id,
            confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_import_media(
        paths: list[str],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Import 1–20 files from session media roots; reject already imported paths.

        Saves and exports a backup before import, then verifies file paths and
        saves. No files are copied, deleted or changed. Reuse the same key on retry.
        """
        return await asyncio.to_thread(
            client.request,
            "import_media",
            timeout_seconds,
            arguments={"paths": paths},
            expected_project_id=expected_project_id,
            confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_create_project(
        name: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Create and activate a project in the current project-library folder.

        Requires an open saved project and a freshly prepared bridge. Saves and
        backs up the previous project before creation, refuses existing names,
        then saves and verifies the new project. The result contains its new
        project ID and previous_project_id. On timeout or replay use the SAME
        original expected_project_id, name and key; never create again with a
        new key. This does not create a project from the Project Manager screen.
        """
        return await asyncio.to_thread(
            client.request,
            "create_project",
            timeout_seconds,
            arguments={"name": name},
            expected_project_id=expected_project_id,
            confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_create_timeline(
        name: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Create an empty named timeline after backup; reject existing exact names.

        Uses project settings. After creation is acknowledged, configure custom
        FPS separately with resolve_lua_set_empty_timeline_fps before adding media.
        """
        return await asyncio.to_thread(
            client.request,
            "create_timeline",
            timeout_seconds,
            arguments={"name": name},
            expected_project_id=expected_project_id,
            confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_native_audio(
        timeline_name: str,
        native_audio: dict[str, Any],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Run guarded native normalization, phrase batches, or audio probes.

        Use a checkpoint copy. Normalization requires exact audio item bounds
        and a mode returned by Resolve; presets must already exist uniquely.
        Voice-isolation state acceptance does not prove effective suppression.
        Cleanup operations accept only an exact muted, unprocessed failed batch.
        Native success and layout readback do not prove perceptual audio balance.
        A render/listening QA pass remains required, especially for mixed speech.
        """
        result = await asyncio.to_thread(
            client.request, "set_clip_properties", timeout_seconds,
            arguments={"timeline_name": timeline_name, "native_audio": native_audio},
            expected_project_id=expected_project_id, confirm=confirm,
            idempotency_key=idempotency_key,
        )
        return dict(result, audio_qa="pending")

    @server.tool(annotations=write)
    async def resolve_lua_open_audio_page(
        timeline_name: str,
        page: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Select an exact timeline and open Edit or Fairlight with native readback."""
        return await asyncio.to_thread(
            client.request, "set_clip_properties", timeout_seconds,
            arguments={"timeline_name": timeline_name, "open_page": page},
            expected_project_id=expected_project_id, confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_duplicate_timeline(
        source_timeline_name: str,
        name: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Copy an existing timeline, retaining clips and FPS without changing settings.

        Backs up first, refuses existing destination names, verifies separate
        timeline/item identities, copied AV bounds and unchanged source layout.
        The copy includes existing clips; this does not create an empty template.
        """
        return await asyncio.to_thread(
            client.request,
            "duplicate_timeline",
            timeout_seconds,
            arguments={"name": name, "source_timeline_name": source_timeline_name},
            expected_project_id=expected_project_id,
            confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_append_synced_pairs(
        timeline_name: str,
        sync_groups: list[dict[str, Any]],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Append 1-25 linked groups: screen V1, camera V2, screen master audio A1.

        Each group has screen_path, camera_path, screen_start_frame,
        camera_start_frame, frame_count. Alternatively a full pair has screen_path,
        camera_path, camera_delay_frames (0..600): whole sources, no edge trims.
        Sources and timeline must have equal FPS.
        Public ranges are inclusive (start + count - 1); native bounds are
        checked using live-tested Resolve conventions. Both pictures and master
        audio share duration/position for cut groups. Full pairs retain camera delay
        and each source duration. No speed changes, deletes or overwrite.
        Requires updated fixed editing/sync modules; validates native readback.
        """
        return await asyncio.to_thread(
            client.request,
            "append_clip",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "sync_groups": sync_groups},
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_append_clip(
        timeline_name: str,
        media_path: str,
        expected_project_id: str,
        idempotency_key: str,
        start_frame: int | None = None,
        end_frame: int | None = None,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Append imported media to one uniquely named timeline after backup.

        Optional zero-based source start/end frames are both inclusive. Omit both
        for the whole clip. Appends after AV items, ignoring subtitle tails.
        No insert/overwrite/delete. Verifies new item IDs and
        source identity, then saves. Reuse the same key to avoid duplicate clips.
        """
        return await asyncio.to_thread(
            client.request,
            "append_clip",
            timeout_seconds,
            arguments={
                "timeline_name": timeline_name,
                "media_path": media_path,
                "start_frame": start_frame,
                "end_frame": end_frame,
            },
            expected_project_id=expected_project_id,
            confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_ripple_cut_opening(
        timeline_name: str,
        ripple_cut: dict[str, Any],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Duplicate then cut inside the first linked V1/V2/A1 group.

        Requires explicit destination name, absolute exclusive cut bounds,
        expected timeline/group bounds and screen/camera source paths/in-points.
        Only plain first clips are recreated; downstream items retain identity,
        properties, Fusion compositions and links. Original timeline is preserved.
        Unsupported layouts fail closed. A failed write may leave a partial copy;
        inspect the receipt and backup, never retry using a new key.
        """
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "ripple_cut": ripple_cut},
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_ripple_cut_group(
        timeline_name: str,
        ripple_cut: dict[str, Any],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Cut inside one linked V1/V2/A1 group on a new timeline copy.

        Uses opening-cut fields plus group_index and expected_group_start.
        Retains untouched item identities and shifts only downstream groups.
        A camera circle composition is exported/imported with native APIs;
        other compositions are refused. Same backup and no-blind-retry rules.
        """
        if not {"group_index", "expected_group_start"} <= ripple_cut.keys():
            raise ValueError("Expected an explicit group index and group start.")
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "ripple_cut": ripple_cut},
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_ripple_cut_batch(
        timeline_name: str,
        ripple_batch: dict[str, Any],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 600,
    ) -> dict[str, Any]:
        """Cut reviewed intervals on a copy; group edges require explicit opt-in."""
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "ripple_batch": ripple_batch},
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_repair_ripple_visibility(
        timeline_name: str,
        ripple_repair: dict[str, Any],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Restore camera visibility on an existing, layout-verified cut copy."""
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "ripple_repair": ripple_repair},
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=read)
    async def resolve_lua_verify_ripple_batch(
        timeline_name: str,
        ripple_batch: dict[str, Any],
        expected_project_id: str,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Audit every retained batch fragment and graph without changing timelines."""
        return await asyncio.to_thread(
            client.request,
            "get_timeline_summary",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "ripple_batch": ripple_batch},
            expected_project_id=expected_project_id,
        )

    @server.tool(annotations=read)
    async def resolve_lua_verify_ripple_cut(
        timeline_name: str,
        ripple_cut: dict[str, Any],
        expected_project_id: str,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Audit an existing single-group cut against its source timeline."""
        return await asyncio.to_thread(
            client.request,
            "get_timeline_summary",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "ripple_cut": ripple_cut},
            expected_project_id=expected_project_id,
        )

    @server.tool(annotations=read)
    async def resolve_lua_preflight_ripple_span(
        timeline_name: str,
        ripple_span: dict[str, Any],
        expected_project_id: str,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Check span geometry and supported graphs without saving or editing."""
        return await asyncio.to_thread(
            client.request,
            "get_timeline_summary",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "ripple_span": ripple_span},
            expected_project_id=expected_project_id,
        )

    @server.tool(annotations=write)
    async def resolve_lua_camera_visibility(
        timeline_name: str,
        camera_visibility: dict[str, Any],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Duplicate a timeline and hide canonical V2 circles at explicit intervals.

        Leaves V1, audio, links, timings and camera framing untouched. Saves and
        backs up before duplicating. Existing animated camera blend is refused.
        """
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={
                "timeline_name": timeline_name,
                "camera_visibility": camera_visibility,
            },
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_ripple_cut_span(
        timeline_name: str,
        ripple_span: dict[str, Any],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Cut across 2–20 linked groups on a new copy, preserving source privacy.

        Requires explicit boundary-group source geometry. Only canonical static
        camera circles and canonical privacy masks are transferred on rebuilt
        boundaries. Other affected Fusion graphs fail closed. Never blindly retry.
        """
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "ripple_span": ripple_span},
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_privacy_blur_batch(
        timeline_name: str,
        privacy_batch: list[dict[str, Any]],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Apply privacy masks to 1-100 distinct V1 items on a timeline copy.

        Each entry has item_index, expected_media_path and privacy_blur with the
        same fields as the single-item tool. All targets are preflighted before
        one saved backup. Existing compositions are refused. A partial failure
        can leave some masks installed: reconcile before any new-key retry.
        """
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "privacy_batch": privacy_batch},
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_privacy_blur(
        timeline_name: str,
        item_index: int,
        expected_media_path: str,
        privacy_blur: dict[str, Any],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Add a time-bounded rectangle blur to one screen V1 clip.

        Use a verified timeline copy first. Requires exact clip bounds and an
        exclusive absolute start/end interval, normalized Fusion rectangle
        geometry and bounded strength. Refuses existing Fusion compositions.
        Optional additional_intervals preserves clear gaps between episodes.
        Keeps clip/source bounds, audio and camera tracks unchanged. Readback
        verifies graph/timing; native render inspection must establish privacy.
        """
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={
                "timeline_name": timeline_name,
                "item_index": item_index,
                "expected_media_path": expected_media_path,
                "privacy_blur": privacy_blur,
            },
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_preview_start(
        timeline_name: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 30,
        preview_frame: int | None = None,
    ) -> dict[str, Any]:
        """Select Edit at the start or an absolute timeline frame without playback."""
        arguments: dict[str, Any] = {
            "timeline_name": timeline_name, "preview_start": True
        }
        if preview_frame is not None:
            arguments["preview_frame"] = preview_frame
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments=arguments,
            expected_project_id=expected_project_id,
            confirm=confirm,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=write)
    async def resolve_lua_replace_video_take(
        timeline_name: str,
        track_index: int,
        item_index: int,
        expected_media_path: str,
        replacement_take_path: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Select a full-length derived video take on one existing item.

        Requires matching duration/FPS, no prior take selector or Fusion comp.
        Keeps the original take, timeline bounds and linked master audio.
        """
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={
                "timeline_name": timeline_name,
                "track_index": track_index,
                "item_index": item_index,
                "expected_media_path": expected_media_path,
                "replacement_take_path": replacement_take_path,
            },
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_set_empty_timeline_fps(
        timeline_name: str,
        frame_rate: int,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Recover settings of an existing EMPTY timeline; refuse any clips."""
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={
                "timeline_name": timeline_name,
                "empty_timeline_fps": frame_rate,
            },
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_circle_mask(
        timeline_name: str,
        track_index: int,
        item_index: int,
        expected_media_path: str,
        center_x: float,
        center_y: float,
        diameter: float,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Add a fixed transparent circular Fusion mask to one video item.

        Center is normalized, Fusion Y points upwards; diameter is a fraction
        of image width. Rejects existing compositions instead of replacing them.
        """
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={
                "timeline_name": timeline_name,
                "track_index": track_index,
                "item_index": item_index,
                "expected_media_path": expected_media_path,
                "circle_mask": {
                    "center_x": center_x,
                    "center_y": center_y,
                    "diameter": diameter,
                },
            },
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_set_clip_properties(
        timeline_name: str,
        track_type: str,
        track_index: int,
        item_index: int,
        expected_media_path: str,
        properties: dict[str, float],
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Set and verify clip gain or framing after backup.

        Track and item indices are one-based; source path must match. Audio allows
        AudioVolume (-60..12 dB). Video allows ZoomX/Y (.1..4), Pan/Tilt and Opacity.
        """
        return await asyncio.to_thread(
            client.request,
            "set_clip_properties",
            timeout_seconds,
            arguments={
                "timeline_name": timeline_name,
                "track_type": track_type,
                "track_index": track_index,
                "item_index": item_index,
                "expected_media_path": expected_media_path,
                "properties": properties,
            },
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_add_subtitles(
        timeline_name: str,
        subtitle_path: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Import bounded UTF-8 SRT captions into an empty AV/subtitle timeline.

        Backs up first, verifies cue count and first/last timing. SRT must be in
        session media roots. Add captions before AV clips, then append AV from the
        start. Render burns captions into the picture.
        """
        return await asyncio.to_thread(
            client.request,
            "add_subtitles",
            timeout_seconds,
            arguments={"timeline_name": timeline_name, "subtitle_path": subtitle_path},
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_prepare_render(
        timeline_name: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
        start_frame: int | None = None,
        end_frame: int | None = None,
    ) -> dict[str, Any]:
        """Queue a backed-up 1080p H.264 MP4 with audio and burnt captions.

        Uses installed YouTube - 1080p preset. Output is a new managed session
        directory; no existing video is overwritten. Returns owned render job ID.
        Optional absolute timeline bounds use an exclusive end. Supply both;
        the queued native range is verified before a later start is permitted.
        """
        arguments: dict[str, Any] = {"timeline_name": timeline_name}
        if start_frame is not None or end_frame is not None:
            arguments.update(start_frame=start_frame, end_frame=end_frame)
        return await asyncio.to_thread(
            client.request,
            "prepare_render",
            timeout_seconds,
            arguments=arguments,
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=write)
    async def resolve_lua_start_render(
        job_id: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Start only a job prepared by this running bridge; never blindly resubmit.

        Returns accepted before dispatch, not running or complete. The bridge
        exports acceptance before rendering blocks exports. Replay returns that
        receipt without another start. Poll owned status until complete or failed.
        """
        return await asyncio.to_thread(
            client.request,
            "start_render",
            timeout_seconds,
            arguments={"job_id": job_id},
            expected_project_id=expected_project_id,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )

    @server.tool(annotations=read)
    async def resolve_lua_get_item(
        timeline_name: str,
        track_type: str,
        track_index: int,
        item_index: int,
        expected_media_path: str,
        expected_project_id: str,
        timeout_seconds: float = 30,
        inspect_circle: bool = False,
        inspect_privacy: bool = False,
        inspect_camera_visibility: bool = False,
    ) -> dict[str, Any]:
        """Read native item/source bounds and link count with source identity check."""
        return await asyncio.to_thread(
            client.request,
            "get_timeline_summary",
            timeout_seconds,
            arguments={
                "timeline_name": timeline_name,
                "track_type": track_type,
                "track_index": track_index,
                "item_index": item_index,
                "expected_media_path": expected_media_path,
                **({"inspect_circle": True} if inspect_circle else {}),
                **({"inspect_privacy": True} if inspect_privacy else {}),
                **(
                    {"inspect_camera_visibility": True}
                    if inspect_camera_visibility
                    else {}
                ),
            },
            expected_project_id=expected_project_id,
        )

    @server.tool(annotations=read)
    async def resolve_lua_get_render_status(
        job_id: str,
        expected_project_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read owned job status and verify completed video output.

        After accepted dispatch, a missing reply returns awaiting_status, not
        running or failed. Rendering can block export; an unavailable bridge is
        also possible. Poll again without resubmitting start.
        """
        return await asyncio.to_thread(
            client.request,
            "get_render_status",
            timeout_seconds,
            arguments={"job_id": job_id},
            expected_project_id=expected_project_id,
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    async def resolve_lua_cleanup_responses(confirm: bool = False) -> dict[str, Any]:
        """Preview or delete response snapshots after an acknowledged session stop.

        Keeps every backup, render, receipt and script. Refuses uncertain writes.
        Defaults to preview; confirm=True deletes only the eligible response files.
        """
        return await asyncio.to_thread(cleanup_responses, root, confirm=confirm)

    @server.tool(annotations=read)
    async def resolve_lua_get_timeline_summary(
        timeline_name: str,
        expected_project_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read live item counts, subtitle bounds and timeline frame rate."""
        return await asyncio.to_thread(
            client.request,
            "get_timeline_summary",
            timeout_seconds,
            arguments={"timeline_name": timeline_name},
            expected_project_id=expected_project_id,
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    async def resolve_lua_reload_modules(timeout_seconds: float = 30) -> dict[str, Any]:
        """Reload the fixed trusted editing/finishing modules from this session.

        Accepts no code or path. An operator must first update those local files.
        Keeps owned jobs and receipts and refuses during rendering. Does not
        replace the running bridge loop or resolve uncertain write receipts.
        """
        return await asyncio.to_thread(
            client.request, "reload_modules", timeout_seconds
        )

    for tool_name in excluded_tools:
        server.remove_tool(tool_name)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--media-root", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.prepare:
        print(prepare(args.runtime, args.media_root))
    else:
        create_server(args.runtime).run()


if __name__ == "__main__":
    main()
