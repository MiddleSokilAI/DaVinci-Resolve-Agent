"""Local stdio MCP server exposing the fixed application API."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from agent import __version__
from agent.application import AgentApplication
from agent.capabilities import workflow_capabilities
from agent.media import MediaPolicy
from agent.rendering import DEFAULT_RENDER_PROFILE
from agent.workflow_audit import WorkflowAuditLog

READ_ONLY_TOOL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
WRITE_TOOL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
DESTRUCTIVE_WRITE_TOOL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)


def create_server(application: AgentApplication | None = None) -> MCPServer:
    """Create an MCP server bound to an application service."""
    service = (
        AgentApplication(workflow_audit=WorkflowAuditLog())
        if application is None
        else application
    )
    server = MCPServer(
        name="davinci-resolve-agent",
        version=__version__,
    instructions=(
            "Local access to DaVinci Resolve Agent. "
            "Call video_agent_capabilities before choosing a workflow; cached "
            "heartbeat and registered tools do not prove native availability. "
            "The Resolve tools require the one-shot ResolveBridge script to be "
            "launched inside DaVinci Resolve while the request is waiting. "
            "Rough-cut and audio workflows run locally without changing Resolve."
        ),
    )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def video_agent_capabilities(
        verify_connection: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Map workflows to this Python backend; optionally verify native liveness.

        The default reads cached state only. It never treats another backend's
        live connection as support for Python workflow receipts or write APIs.
        """
        connected: bool | None = None
        project = None
        evidence = "cached_python_state"
        if verify_connection:
            try:
                project = await asyncio.to_thread(
                    service.resolve_get_project, timeout_seconds
                )
                connected = bool(project)
                evidence = "fresh_native_request"
            except (OSError, RuntimeError, ValueError):
                connected = False
                evidence = "native_request_failed"
        try:
            state = (await asyncio.to_thread(service.status))["bridge"]
        except (OSError, RuntimeError, ValueError):
            state = {}
        try:
            await asyncio.to_thread(MediaPolicy.from_local_config)
            media_configured = True
        except (OSError, RuntimeError, ValueError):
            media_configured = False
        return workflow_capabilities(
            backend="python",
            tools=[tool.name for tool in await server.list_tools()],
            connected=connected,
            evidence=evidence,
            project=project,
            native_capabilities=state.get("capabilities", {}),
            media_roots_configured=media_configured,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def video_agent_status(
        max_age_seconds: int = 120,
    ) -> dict[str, Any]:
        """Return cached bridge status without sending a Resolve command."""
        return await asyncio.to_thread(service.status, max_age_seconds)

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_get_project(
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return the current Resolve project."""
        project = await asyncio.to_thread(
            service.resolve_get_project,
            timeout_seconds,
        )
        return {"project": project}

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_stop_bridge(
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Stop the manually launched persistent bridge cleanly."""
        return await asyncio.to_thread(service.resolve_stop_bridge, timeout_seconds)

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_list_timelines(
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """List timelines in the current Resolve project."""
        timelines = await asyncio.to_thread(
            service.resolve_list_timelines,
            timeout_seconds,
        )
        return {"timelines": timelines}

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_get_timeline(
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return the current Resolve timeline."""
        timeline = await asyncio.to_thread(
            service.resolve_get_timeline,
            timeout_seconds,
        )
        return {"timeline": timeline}

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_list_timeline_items(
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """List bounded metadata for video/audio items in one timeline."""
        return await asyncio.to_thread(
            service.resolve_list_timeline_items,
            timeline_id,
            timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_list_media_pool_items(
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """List bounded identities for items in the current Media Pool."""
        return await asyncio.to_thread(
            service.resolve_list_media_pool_items,
            timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_get_editing_metadata(
        timeline_id: str,
        asset_ids: list[str],
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read source frame count/FPS and target video/audio track counts."""
        return await asyncio.to_thread(
            service.resolve_get_editing_metadata,
            timeline_id,
            asset_ids,
            timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_get_subtitle_environment(
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read subtitle items and native auto-caption API availability."""
        return await asyncio.to_thread(
            service.resolve_get_subtitle_environment,
            timeline_id,
            timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_get_animation_template_environment(
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Inspect packaged template integrity and documented Fusion methods."""
        return await asyncio.to_thread(
            service.resolve_get_animation_template_environment,
            timeline_id,
            timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_get_color_environment(
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Inspect documented color versions and node graphs."""
        return await asyncio.to_thread(
            service.resolve_get_color_environment,
            timeline_id,
            timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_create_subtitles_from_audio(
        timeline_id: str,
        confirm_create: bool = False,
        timeout_seconds: float = 300,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create fixed AUTO subtitles after backup and verify readback."""
        return await asyncio.to_thread(
            service.resolve_create_subtitles_from_audio,
            timeline_id,
            confirm_create=confirm_create,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_get_workspace_snapshot(
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Collect project, timeline, Media Pool, and render discovery once."""
        return await asyncio.to_thread(
            service.resolve_get_workspace_snapshot,
            timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_get_render_options(
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Discover documented render formats, codecs, presets, and jobs."""
        return await asyncio.to_thread(
            service.resolve_get_render_options,
            timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_import_media(
        paths: list[str],
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Import allowlisted local media after exporting a project backup."""
        return await asyncio.to_thread(
            service.resolve_import_media,
            paths,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_close_project(
        expected_project_id: str,
        idempotency_key: str,
        confirm_close: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Save, back up and close exactly the current project; keep Resolve running.

        Requires explicit user intent. Reuse the same key after a timeout.
        Replay returns historical evidence without opening or closing any project.
        Automatic reopening stays suspended until an explicit project open.
        """
        return await asyncio.to_thread(
            service.resolve_close_project, expected_project_id,
            confirm_close=confirm_close, idempotency_key=idempotency_key,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_create_project(
        name: str,
        confirm_create: bool = False,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create and select a unique project; back up the current one if open."""
        return await asyncio.to_thread(
            service.resolve_create_project,
            name,
            confirm_create=confirm_create,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_create_timeline(
        name: str,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create an empty timeline after exporting a project backup."""
        return await asyncio.to_thread(
            service.resolve_create_timeline,
            name,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_ensure_timeline_tracks(
        timeline_id: str,
        video_track_count: int,
        audio_track_count: int,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Ensure 1-8 video/audio tracks after exporting a backup."""
        return await asyncio.to_thread(
            service.resolve_ensure_timeline_tracks,
            timeline_id,
            video_track_count,
            audio_track_count,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_duplicate_timeline(
        timeline_id: str,
        name: str,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Duplicate one timeline after exporting a project backup."""
        return await asyncio.to_thread(
            service.resolve_duplicate_timeline,
            timeline_id,
            name,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_set_current_timeline(
        timeline_id: str,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Select one existing timeline after exporting a project backup."""
        return await asyncio.to_thread(
            service.resolve_set_current_timeline,
            timeline_id,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_append_clip(
        timeline_id: str,
        asset_id: str,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Append a media asset after exporting a project backup."""
        return await asyncio.to_thread(
            service.resolve_append_clip,
            timeline_id,
            asset_id,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_insert_clip(
        timeline_id: str,
        asset_id: str,
        source_start_frame: int,
        source_end_frame: int,
        position_frames: int,
        track_type: str,
        track_index: int,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert one bounded source range after exporting a project backup."""
        return await asyncio.to_thread(
            service.resolve_insert_clip,
            timeline_id,
            asset_id,
            source_start_frame,
            source_end_frame,
            position_frames,
            track_type,
            track_index,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_set_clip_enabled(
        timeline_id: str,
        timeline_item_id: str,
        enabled: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set one timeline item enabled state after exporting a backup."""
        return await asyncio.to_thread(
            service.resolve_set_clip_enabled,
            timeline_id,
            timeline_item_id,
            enabled,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_set_clips_linked(
        timeline_id: str,
        timeline_item_ids: list[str],
        linked: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Link or unlink 2 to 16 timeline items after exporting a backup."""
        return await asyncio.to_thread(
            service.resolve_set_clips_linked,
            timeline_id,
            timeline_item_ids,
            linked,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_set_clip_transform(
        timeline_id: str,
        timeline_item_id: str,
        position_x: float | None = None,
        position_y: float | None = None,
        zoom: float | None = None,
        rotation_degrees: float | None = None,
        opacity_percent: float | None = None,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply one fixed bounded video clip transform after a backup."""
        return await asyncio.to_thread(
            service.resolve_set_clip_transform,
            timeline_id,
            timeline_item_id,
            position_x=position_x,
            position_y=position_y,
            zoom=zoom,
            rotation_degrees=rotation_degrees,
            opacity_percent=opacity_percent,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_insert_title(
        timeline_id: str,
        title_name: str,
        timecode: str,
        confirm_insert: bool = False,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert an installed standard title at a confirmed exact timecode."""
        return await asyncio.to_thread(
            service.resolve_insert_title,
            timeline_id,
            title_name,
            timecode,
            confirm_insert=confirm_insert,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_insert_animation_template(
        timeline_id: str,
        template_id: str,
        timecode: str,
        confirm_insert: bool = False,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert one packaged Fusion title at a confirmed exact timecode."""
        return await asyncio.to_thread(
            service.resolve_insert_animation_template,
            timeline_id,
            template_id,
            timecode,
            confirm_insert=confirm_insert,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=DESTRUCTIVE_WRITE_TOOL)
    async def resolve_delete_clip(
        timeline_id: str,
        timeline_item_id: str,
        confirm_delete: bool = False,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Delete exactly one timeline item without ripple after confirmation."""
        return await asyncio.to_thread(
            service.resolve_delete_clip,
            timeline_id,
            timeline_item_id,
            confirm_delete=confirm_delete,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_add_marker(
        timeline_id: str,
        frame: int,
        color: str,
        name: str = "",
        note: str = "",
        duration: int = 1,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add a timeline marker after exporting a project backup."""
        return await asyncio.to_thread(
            service.resolve_add_marker,
            timeline_id,
            frame,
            color,
            name,
            note,
            duration,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_prepare_render_job(
        custom_name: str,
        timeline_id: str | None = None,
        profile: str = DEFAULT_RENDER_PROFILE,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add a fixed MP4/H264 job after backup without starting render."""
        return await asyncio.to_thread(
            service.resolve_prepare_render_job,
            custom_name,
            timeline_id=timeline_id,
            profile=profile,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_get_render_job_status(
        job_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return current Resolve status for one render job."""
        return await asyncio.to_thread(
            service.resolve_get_render_job_status,
            job_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def resolve_start_render_job(
        job_id: str,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start one agent-prepared render job after a project backup."""
        return await asyncio.to_thread(
            service.resolve_start_render_job,
            job_id,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def resolve_verify_render_output(
        job_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Verify one completed render as a managed non-empty MP4 file."""
        return await asyncio.to_thread(
            service.resolve_verify_render_output,
            job_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def generate_subtitles(
        source_file: str,
        timeline_id: str,
        confirm_apply: bool = False,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        """Transcribe locally, write SRT and apply it to one exact timeline."""
        return await asyncio.to_thread(
            service.generate_subtitles,
            source_file,
            timeline_id,
            confirm_apply=confirm_apply,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def create_rough_cut(
        screen_file: str,
        webcam_file: str,
        screen_audio_file: str,
        webcam_audio_file: str,
        speech_audio_file: str,
        timeline_name: str,
        max_sync_offset_ms: int = 30_000,
        pause_threshold_dbfs: float = -40.0,
        min_pause_duration_ms: int = 700,
        preserve_context_ms: int = 120,
    ) -> dict[str, Any]:
        """Create and persist a pending-review plan without changing Resolve."""
        return await asyncio.to_thread(
            service.create_rough_cut,
            screen_file=screen_file,
            webcam_file=webcam_file,
            screen_audio_file=screen_audio_file,
            webcam_audio_file=webcam_audio_file,
            speech_audio_file=speech_audio_file,
            timeline_name=timeline_name,
            max_sync_offset_ms=max_sync_offset_ms,
            pause_threshold_dbfs=pause_threshold_dbfs,
            min_pause_duration_ms=min_pause_duration_ms,
            preserve_context_ms=preserve_context_ms,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def approve_rough_cut(
        plan_id: str,
        confirm_review: bool,
    ) -> dict[str, Any]:
        """Approve one stored draft without applying any Resolve edits."""
        return await asyncio.to_thread(
            service.approve_rough_cut,
            plan_id,
            confirm_review=confirm_review,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_rough_cut_plan(plan_id: str) -> dict[str, Any]:
        """Return one stored draft and its matching approval state."""
        return await asyncio.to_thread(
            service.get_rough_cut_plan,
            plan_id,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def list_rough_cut_plans(limit: int = 100) -> dict[str, Any]:
        """List bounded rough-cut summaries without source media paths."""
        return await asyncio.to_thread(
            service.list_rough_cut_plans,
            limit,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_rough_cut_apply(
        plan_id: str,
        source_timeline_id: str,
        target_timeline_name: str,
    ) -> dict[str, Any]:
        """Show M34 operations and capability blocks without changing Resolve."""
        return await asyncio.to_thread(
            service.preview_rough_cut_apply,
            plan_id,
            source_timeline_id,
            target_timeline_name,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def apply_rough_cut(
        plan_id: str,
        source_timeline_id: str,
        target_timeline_name: str,
        confirm_apply: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply an approved plan only when every required API is verified."""
        return await asyncio.to_thread(
            service.apply_rough_cut,
            plan_id,
            source_timeline_id,
            target_timeline_name,
            confirm_apply=confirm_apply,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def sync_screen_and_webcam(
        timeline_name: str,
        screen_asset_id: str,
        webcam_asset_id: str,
        webcam_offset_ms: int,
        confirm_sync: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Create a timeline and place synchronized screen V1/A1 + webcam V2."""
        return await asyncio.to_thread(
            service.sync_screen_and_webcam,
            timeline_name=timeline_name,
            screen_asset_id=screen_asset_id,
            webcam_asset_id=webcam_asset_id,
            webcam_offset_ms=webcam_offset_ms,
            confirm_sync=confirm_sync,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def list_editing_recipes() -> dict[str, Any]:
        """List packaged allowlisted editing recipes."""
        return await asyncio.to_thread(service.list_editing_recipes)

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_editing_recipe(recipe_id: str) -> dict[str, Any]:
        """Return one validated packaged editing recipe."""
        return await asyncio.to_thread(service.get_editing_recipe, recipe_id)

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_editing_recipe(
        recipe_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Preview exact steps and capability gates without Resolve writes."""
        return await asyncio.to_thread(
            service.preview_editing_recipe,
            recipe_id,
            inputs,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def run_editing_recipe(
        recipe_id: str,
        inputs: dict[str, Any],
        confirm_execute: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Execute one packaged recipe with confirmation and durable replay."""
        return await asyncio.to_thread(
            service.run_editing_recipe,
            recipe_id,
            inputs,
            confirm_execute=confirm_execute,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_visual_treatment(
        timeline_id: str,
        transforms: list[dict[str, Any]],
        titles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Preview exact standard-title and static reframing operations."""
        return await asyncio.to_thread(
            service.preview_visual_treatment,
            timeline_id,
            transforms,
            titles,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def apply_visual_treatment(
        timeline_id: str,
        transforms: list[dict[str, Any]],
        titles: list[dict[str, Any]],
        confirm_apply: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply a confirmed visual treatment with durable replay."""
        return await asyncio.to_thread(
            service.apply_visual_treatment,
            timeline_id,
            transforms,
            titles,
            confirm_apply=confirm_apply,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_baseline_edit(
        finalization_receipt_id: str,
        audio_integration_receipt_id: str,
        subtitle_receipt_id: str | None = None,
        visual_treatment_receipt_id: str | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Verify one baseline edit from canonical receipts and live readback."""
        return await asyncio.to_thread(
            service.preview_baseline_edit,
            finalization_receipt_id=finalization_receipt_id,
            audio_integration_receipt_id=audio_integration_receipt_id,
            subtitle_receipt_id=subtitle_receipt_id,
            visual_treatment_receipt_id=visual_treatment_receipt_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def start_baseline_render(
        finalization_receipt_id: str,
        audio_integration_receipt_id: str,
        custom_name: str,
        profile: str = DEFAULT_RENDER_PROFILE,
        confirm_render: bool = False,
        subtitle_receipt_id: str | None = None,
        visual_treatment_receipt_id: str | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Start a uniquely named render after successful baseline edit QA."""
        return await asyncio.to_thread(
            service.start_baseline_render,
            finalization_receipt_id=finalization_receipt_id,
            audio_integration_receipt_id=audio_integration_receipt_id,
            subtitle_receipt_id=subtitle_receipt_id,
            visual_treatment_receipt_id=visual_treatment_receipt_id,
            custom_name=custom_name,
            profile=profile,
            confirm_render=confirm_render,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_baseline_render_status(
        receipt_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Re-run live QA and inspect one exact baseline render output."""
        return await asyncio.to_thread(
            service.get_baseline_render_status,
            receipt_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_broll_plan(
        baseline_edit_receipt_id: str,
        target_timeline_name: str,
        placements: list[dict[str, Any]],
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Validate one explicit video-only B-roll placement plan."""
        return await asyncio.to_thread(
            service.preview_broll_plan,
            baseline_edit_receipt_id=baseline_edit_receipt_id,
            target_timeline_name=target_timeline_name,
            placements=placements,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def apply_broll_plan(
        baseline_edit_receipt_id: str,
        target_timeline_name: str,
        placements: list[dict[str, Any]],
        expected_plan_id: str,
        confirm_apply: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply an exact reviewed B-roll plan to a duplicate timeline."""
        return await asyncio.to_thread(
            service.apply_broll_plan,
            baseline_edit_receipt_id=baseline_edit_receipt_id,
            target_timeline_name=target_timeline_name,
            placements=placements,
            expected_plan_id=expected_plan_id,
            confirm_apply=confirm_apply,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_broll_status(
        receipt_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read one M51 receipt and verify its live target timeline."""
        return await asyncio.to_thread(
            service.get_broll_status,
            receipt_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def list_animation_templates() -> dict[str, Any]:
        """List immutable animation templates packaged with the agent."""
        return await asyncio.to_thread(service.list_animation_templates)

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_animation_template(template_id: str) -> dict[str, Any]:
        """Return one immutable packaged animation template manifest."""
        return await asyncio.to_thread(
            service.get_animation_template, template_id
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_animation_template(
        broll_receipt_id: str,
        target_timeline_name: str,
        template_id: str,
        timecode: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Preview one packaged animation on an M51 timeline duplicate."""
        return await asyncio.to_thread(
            service.preview_animation_template,
            broll_receipt_id=broll_receipt_id,
            target_timeline_name=target_timeline_name,
            template_id=template_id,
            timecode=timecode,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def apply_animation_template(
        broll_receipt_id: str,
        target_timeline_name: str,
        template_id: str,
        timecode: str,
        expected_plan_id: str,
        confirm_apply: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply one exact reviewed animation plan to a duplicate timeline."""
        return await asyncio.to_thread(
            service.apply_animation_template,
            broll_receipt_id=broll_receipt_id,
            target_timeline_name=target_timeline_name,
            template_id=template_id,
            timecode=timecode,
            expected_plan_id=expected_plan_id,
            confirm_apply=confirm_apply,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def list_color_presets() -> dict[str, Any]:
        """List immutable packaged M53 CDL presets."""
        return await asyncio.to_thread(service.list_color_presets)

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_color_preset(preset_id: str) -> dict[str, Any]:
        """Return one immutable packaged M53 CDL preset."""
        return await asyncio.to_thread(service.get_color_preset, preset_id)

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_color_treatment(
        animation_receipt_id: str,
        target_timeline_name: str,
        preset_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Preview one packaged CDL preset on M52 media video items."""
        return await asyncio.to_thread(
            service.preview_color_treatment,
            animation_receipt_id=animation_receipt_id,
            target_timeline_name=target_timeline_name,
            preset_id=preset_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def apply_color_treatment(
        animation_receipt_id: str,
        target_timeline_name: str,
        preset_id: str,
        expected_plan_id: str,
        confirm_apply: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply one exact reviewed CDL plan to a duplicate timeline."""
        return await asyncio.to_thread(
            service.apply_color_treatment,
            animation_receipt_id=animation_receipt_id,
            target_timeline_name=target_timeline_name,
            preset_id=preset_id,
            expected_plan_id=expected_plan_id,
            confirm_apply=confirm_apply,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def analyze_take_candidates(
        selection_name: str,
        candidates: list[dict[str, str | float]],
    ) -> dict[str, Any]:
        """Analyze 2–8 full files or uniformly bounded source segments."""
        return await asyncio.to_thread(
            service.analyze_take_candidates,
            selection_name=selection_name,
            candidates=candidates,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def analyze_scripted_take_candidates(
        selection_name: str,
        candidates: list[dict[str, str | float]],
        reference_text: str,
    ) -> dict[str, Any]:
        """Rank bounded dialogue takes against one expected script."""
        return await asyncio.to_thread(
            service.analyze_scripted_take_candidates,
            selection_name=selection_name,
            candidates=candidates,
            reference_text=reference_text,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_take_selection(selection_id: str) -> dict[str, Any]:
        """Return one stored M54 technical take-selection report."""
        return await asyncio.to_thread(service.get_take_selection, selection_id)

    @server.tool(annotations=READ_ONLY_TOOL)
    async def list_take_selections(limit: int = 20) -> dict[str, Any]:
        """List bounded M54 take-selection summaries."""
        return await asyncio.to_thread(service.list_take_selections, limit)

    @server.tool(annotations=WRITE_TOOL)
    async def review_take_selection(
        selection_id: str,
        decision: str,
        selected_candidate_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Approve or reject one selection without modifying Resolve."""
        return await asyncio.to_thread(
            service.review_take_selection,
            selection_id=selection_id,
            decision=decision,
            selected_candidate_id=selected_candidate_id,
            note=note,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_take_selection_review(selection_id: str) -> dict[str, Any]:
        """Return one immutable M54 take-selection review."""
        return await asyncio.to_thread(
            service.get_take_selection_review,
            selection_id,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def compose_take_sequence(
        sequence_name: str,
        selection_ids: list[str],
    ) -> dict[str, Any]:
        """Compose an ordered handoff from approved take selections."""
        return await asyncio.to_thread(
            service.compose_take_sequence,
            sequence_name=sequence_name,
            selection_ids=selection_ids,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_take_sequence(sequence_id: str) -> dict[str, Any]:
        """Return one persisted M54.4 approved take sequence."""
        return await asyncio.to_thread(service.get_take_sequence, sequence_id)

    @server.tool(annotations=READ_ONLY_TOOL)
    async def list_take_sequences(limit: int = 20) -> dict[str, Any]:
        """List bounded M54.4 take-sequence summaries."""
        return await asyncio.to_thread(service.list_take_sequences, limit)

    @server.tool(annotations=WRITE_TOOL)
    async def bind_take_sequence_sources(
        sequence_id: str,
        sources: list[dict[str, str | int]],
    ) -> dict[str, Any]:
        """Bind approved sequence entries to exact allowlisted local files."""
        return await asyncio.to_thread(
            service.bind_take_sequence_sources,
            sequence_id=sequence_id,
            sources=sources,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_take_sequence_binding(binding_id: str) -> dict[str, Any]:
        """Return one path-redacted M55.1 source binding."""
        return await asyncio.to_thread(
            service.get_take_sequence_binding,
            binding_id,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_take_sequence_assembly(
        binding_id: str,
        assembly_name: str,
    ) -> dict[str, Any]:
        """Calculate a path-redacted sequential M55.2 assembly preview."""
        return await asyncio.to_thread(
            service.preview_take_sequence_assembly,
            binding_id=binding_id,
            assembly_name=assembly_name,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_take_sequence_timeline_mapping(
        binding_id: str,
        assembly_name: str,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Map an M55.2 assembly to verified live target timeline frames."""
        return await asyncio.to_thread(
            service.preview_take_sequence_timeline_mapping,
            binding_id=binding_id,
            assembly_name=assembly_name,
            timeline_id=timeline_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_take_sequence_media_import(
        binding_id: str,
        assembly_name: str,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Plan conservative M55.4 Media Pool imports without writing."""
        return await asyncio.to_thread(
            service.preview_take_sequence_media_import,
            binding_id=binding_id,
            assembly_name=assembly_name,
            timeline_id=timeline_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def apply_take_sequence_media_import(
        binding_id: str,
        assembly_name: str,
        timeline_id: str,
        expected_plan_id: str,
        confirm_import: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Import one exact reviewed M55.4 batch after explicit confirmation."""
        return await asyncio.to_thread(
            service.apply_take_sequence_media_import,
            binding_id=binding_id,
            assembly_name=assembly_name,
            timeline_id=timeline_id,
            expected_plan_id=expected_plan_id,
            confirm_import=confirm_import,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_take_sequence_media_import(
        receipt_id: str,
    ) -> dict[str, Any]:
        """Return one path-redacted M55.5 confirmed import receipt."""
        return await asyncio.to_thread(
            service.get_take_sequence_media_import,
            receipt_id,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_take_sequence_timeline_apply(
        media_import_receipt_id: str,
        target_timeline_name: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Preview exact V1/A1 insertion into a protected duplicate timeline."""
        return await asyncio.to_thread(
            service.preview_take_sequence_timeline_apply,
            media_import_receipt_id=media_import_receipt_id,
            target_timeline_name=target_timeline_name,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def apply_take_sequence_timeline(
        media_import_receipt_id: str,
        target_timeline_name: str,
        expected_plan_id: str,
        confirm_apply: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply one reviewed sequence only to a duplicate timeline."""
        return await asyncio.to_thread(
            service.apply_take_sequence_timeline,
            media_import_receipt_id=media_import_receipt_id,
            target_timeline_name=target_timeline_name,
            expected_plan_id=expected_plan_id,
            confirm_apply=confirm_apply,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_take_sequence_timeline_apply(
        receipt_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return one M55.6 receipt with fresh source/target verification."""
        return await asyncio.to_thread(
            service.get_take_sequence_timeline_apply,
            receipt_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def inspect_take_sequence_qc(
        timeline_apply_receipt_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Verify exact M55.6 structure and create an M55.7 QC report."""
        return await asyncio.to_thread(
            service.inspect_take_sequence_qc,
            timeline_apply_receipt_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_take_sequence_qc(
        report_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return an M55.7 report after fresh live timeline verification."""
        return await asyncio.to_thread(
            service.get_take_sequence_qc,
            report_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def review_take_sequence_qc(
        report_id: str,
        decision: str,
        note: str = "",
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Record an immutable human approve/reject decision without editing."""
        return await asyncio.to_thread(
            service.review_take_sequence_qc,
            report_id=report_id,
            decision=decision,
            note=note,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_take_sequence_qc_review(
        report_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return one immutable M55.7 human QC decision."""
        return await asyncio.to_thread(
            service.get_take_sequence_qc_review,
            report_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_take_sequence_render(
        qc_report_id: str,
        custom_name: str,
        profile: str = DEFAULT_RENDER_PROFILE,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Preview a fixed managed render for one approved M55.7 report."""
        return await asyncio.to_thread(
            service.preview_take_sequence_render,
            qc_report_id=qc_report_id,
            custom_name=custom_name,
            profile=profile,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def start_take_sequence_render(
        qc_report_id: str,
        custom_name: str,
        expected_plan_id: str,
        confirm_render: bool = False,
        profile: str = DEFAULT_RENDER_PROFILE,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Prepare and start one exact approved M55.8 managed render."""
        return await asyncio.to_thread(
            service.start_take_sequence_render,
            qc_report_id=qc_report_id,
            custom_name=custom_name,
            expected_plan_id=expected_plan_id,
            confirm_render=confirm_render,
            profile=profile,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_take_sequence_render_status(
        receipt_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Verify one M55.8 render job and its managed MP4 output."""
        return await asyncio.to_thread(
            service.get_take_sequence_render_status,
            receipt_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def compose_webcam_picture_in_picture(
        synchronized_pair_receipt_id: str,
        size_percent: float = 25.0,
        center_x_percent: float = 82.0,
        center_y_percent: float = 82.0,
        confirm_layout: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Lay out the synchronized webcam using normalized frame coordinates."""
        return await asyncio.to_thread(
            service.compose_webcam_picture_in_picture,
            synchronized_pair_receipt_id=synchronized_pair_receipt_id,
            size_percent=size_percent,
            center_x_percent=center_x_percent,
            center_y_percent=center_y_percent,
            confirm_layout=confirm_layout,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def link_synchronized_screen_pair(
        synchronized_pair_receipt_id: str,
        confirm_link: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Link the canonical screen video/audio items from an M38 receipt."""
        return await asyncio.to_thread(
            service.link_synchronized_screen_pair,
            synchronized_pair_receipt_id=synchronized_pair_receipt_id,
            confirm_link=confirm_link,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def preview_synchronized_pause_compaction(
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Preview a kept-range synchronized rebuild without editing Resolve."""
        return await asyncio.to_thread(
            service.preview_synchronized_pause_compaction,
            plan_id=plan_id,
            synchronized_pair_receipt_id=synchronized_pair_receipt_id,
            target_timeline_name=target_timeline_name,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def apply_synchronized_pause_compaction(
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Create a new synchronized timeline from approved kept ranges."""
        return await asyncio.to_thread(
            service.apply_synchronized_pause_compaction,
            plan_id=plan_id,
            synchronized_pair_receipt_id=synchronized_pair_receipt_id,
            target_timeline_name=target_timeline_name,
            confirm_apply=confirm_apply,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def finalize_synchronized_pause_compaction(
        pause_compaction_receipt_id: str,
        picture_in_picture_receipt_id: str,
        synchronized_link_receipt_id: str,
        confirm_finalize: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Propagate approved screen links and webcam layout to M42 segments."""
        return await asyncio.to_thread(
            service.finalize_synchronized_pause_compaction,
            pause_compaction_receipt_id=pause_compaction_receipt_id,
            picture_in_picture_receipt_id=picture_in_picture_receipt_id,
            synchronized_link_receipt_id=synchronized_link_receipt_id,
            confirm_finalize=confirm_finalize,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def prepare_finalized_timeline_render(
        finalization_receipt_id: str,
        custom_name: str,
        profile: str = DEFAULT_RENDER_PROFILE,
        confirm_prepare: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Prepare a fixed render job for one applied M43 timeline."""
        return await asyncio.to_thread(
            service.prepare_finalized_timeline_render,
            finalization_receipt_id=finalization_receipt_id,
            custom_name=custom_name,
            profile=profile,
            confirm_prepare=confirm_prepare,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def start_finalized_timeline_render(
        preparation_receipt_id: str,
        confirm_render: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Start exactly one applied M44 render job."""
        return await asyncio.to_thread(
            service.start_finalized_timeline_render,
            preparation_receipt_id=preparation_receipt_id,
            confirm_render=confirm_render,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_finalized_timeline_render_status(
        execution_receipt_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Inspect one M45 render and verify its managed MP4 output."""
        return await asyncio.to_thread(
            service.get_finalized_timeline_render_status,
            execution_receipt_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def prepare_finalized_timeline_audio(
        finalization_receipt_id: str,
        custom_name: str,
        confirm_prepare: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Prepare one fixed full-timeline Audio Only WAV job."""
        return await asyncio.to_thread(
            service.prepare_finalized_timeline_audio,
            finalization_receipt_id=finalization_receipt_id,
            custom_name=custom_name,
            confirm_prepare=confirm_prepare,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def start_finalized_timeline_audio(
        extraction_receipt_id: str,
        confirm_render: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Start one exact prepared M46 audio render job."""
        return await asyncio.to_thread(
            service.start_finalized_timeline_audio,
            extraction_receipt_id,
            confirm_render=confirm_render,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_finalized_timeline_audio_status(
        extraction_receipt_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Inspect one M46 render and verify its managed PCM WAV."""
        return await asyncio.to_thread(
            service.get_finalized_timeline_audio_status,
            extraction_receipt_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def clean_dialogue_audio(
        source_file: str,
        preset: str = "pcm-dialogue-level-v1",
    ) -> dict[str, Any]:
        """Create a derived PCM WAV and canonical before/after report."""
        return await asyncio.to_thread(
            service.clean_dialogue_audio,
            source_file,
            preset=preset,
        )

    @server.tool(annotations=WRITE_TOOL)
    async def apply_finalized_timeline_audio(
        extraction_receipt_id: str,
        audio_report_id: str,
        confirm_apply: bool = False,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Insert validated processed audio on A2 and disable source A1 items."""
        return await asyncio.to_thread(
            service.apply_finalized_timeline_audio,
            extraction_receipt_id=extraction_receipt_id,
            audio_report_id=audio_report_id,
            confirm_apply=confirm_apply,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def get_audio_report(report_id: str) -> dict[str, Any]:
        """Return one stored and validated audio before/after report."""
        return await asyncio.to_thread(
            service.get_audio_report,
            report_id,
        )

    @server.tool(annotations=READ_ONLY_TOOL)
    async def list_audio_reports(limit: int = 100) -> dict[str, Any]:
        """List bounded audio summaries without local filesystem paths."""
        return await asyncio.to_thread(
            service.list_audio_reports,
            limit,
        )

    return server


def main() -> None:
    """Run the local MCP server over stdio."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", choices=("auto", "python", "free-marker"), default="auto"
    )
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--managed-profile", type=Path)
    parser.add_argument("--enable-windows-recording-ui", action="store_true")
    args = parser.parse_args()
    if args.backend == "auto":
        if args.runtime is not None:
            parser.error("--runtime requires an explicit free-marker backend")
        from mcp_server.automatic import run as run_automatic

        run_automatic(
            args.managed_profile,
            enable_windows_recording_ui=args.enable_windows_recording_ui,
        )
        return
    if args.backend == "free-marker":
        if args.managed_profile is not None:
            if args.runtime is not None:
                parser.error("--managed-profile and --runtime are mutually exclusive")
            from mcp_server.managed_marker import run as run_managed

            run_managed(
                args.managed_profile,
                enable_windows_recording_ui=args.enable_windows_recording_ui,
            )
            return
        if args.runtime is None:
            parser.error(
                "--backend free-marker requires --runtime or --managed-profile"
            )
        from mcp_server.free_marker import run

        run(args.runtime, enable_windows_recording_ui=args.enable_windows_recording_ui)
        return
    if args.runtime is not None:
        parser.error("--runtime is only supported with --backend free-marker")
    if args.managed_profile is not None:
        parser.error("--managed-profile requires --backend free-marker")
    if args.enable_windows_recording_ui:
        parser.error("--enable-windows-recording-ui requires --backend free-marker")
    create_server().run()


if __name__ == "__main__":
    main()
