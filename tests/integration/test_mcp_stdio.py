"""End-to-end MCP stdio handshake through the installed console script."""

from __future__ import annotations

import asyncio
import os
import sysconfig
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_installed_mcp_server_supports_stdio_handshake() -> None:
    executable_name = (
        "davinci-agent-mcp.exe" if os.name == "nt" else "davinci-agent-mcp"
    )
    executable = Path(sysconfig.get_path("scripts")) / executable_name
    assert executable.is_file()

    async def list_tool_names() -> set[str]:
        parameters = StdioServerParameters(command=str(executable), args=[
            "--backend", "python",
        ])
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
            return {tool.name for tool in result.tools}

    assert asyncio.run(list_tool_names()) == {
        "video_agent_capabilities",
        "video_agent_status",
        "resolve_stop_bridge",
        "resolve_get_project",
        "resolve_list_timelines",
        "resolve_get_timeline",
        "resolve_list_timeline_items",
        "resolve_list_media_pool_items",
        "resolve_get_editing_metadata",
        "resolve_get_subtitle_environment",
        "resolve_get_animation_template_environment",
        "resolve_get_color_environment",
        "resolve_create_subtitles_from_audio",
        "generate_subtitles",
        "resolve_get_workspace_snapshot",
        "resolve_get_render_options",
        "resolve_import_media",
        "resolve_create_timeline",
        "resolve_create_project",
        "resolve_close_project",
        "resolve_ensure_timeline_tracks",
        "resolve_duplicate_timeline",
        "resolve_set_current_timeline",
        "resolve_append_clip",
        "resolve_insert_clip",
        "resolve_set_clip_enabled",
        "resolve_set_clips_linked",
        "resolve_set_clip_transform",
        "resolve_insert_title",
        "resolve_insert_animation_template",
        "resolve_delete_clip",
        "resolve_add_marker",
        "resolve_prepare_render_job",
        "resolve_get_render_job_status",
        "resolve_start_render_job",
        "resolve_verify_render_output",
        "create_rough_cut",
        "approve_rough_cut",
        "get_rough_cut_plan",
        "list_rough_cut_plans",
        "preview_rough_cut_apply",
        "apply_rough_cut",
        "sync_screen_and_webcam",
        "list_editing_recipes",
        "get_editing_recipe",
        "preview_editing_recipe",
        "run_editing_recipe",
        "preview_visual_treatment",
        "apply_visual_treatment",
        "preview_baseline_edit",
        "start_baseline_render",
        "get_baseline_render_status",
        "preview_broll_plan",
        "apply_broll_plan",
        "get_broll_status",
        "list_animation_templates",
        "get_animation_template",
        "preview_animation_template",
        "apply_animation_template",
        "list_color_presets",
        "get_color_preset",
        "preview_color_treatment",
        "apply_color_treatment",
        "analyze_take_candidates",
        "analyze_scripted_take_candidates",
        "get_take_selection",
        "list_take_selections",
        "review_take_selection",
        "get_take_selection_review",
        "compose_take_sequence",
        "get_take_sequence",
        "list_take_sequences",
        "bind_take_sequence_sources",
        "get_take_sequence_binding",
        "preview_take_sequence_assembly",
        "preview_take_sequence_timeline_mapping",
        "preview_take_sequence_media_import",
        "apply_take_sequence_media_import",
        "get_take_sequence_media_import",
        "preview_take_sequence_timeline_apply",
        "apply_take_sequence_timeline",
        "get_take_sequence_timeline_apply",
        "inspect_take_sequence_qc",
        "get_take_sequence_qc",
        "review_take_sequence_qc",
        "get_take_sequence_qc_review",
        "preview_take_sequence_render",
        "start_take_sequence_render",
        "get_take_sequence_render_status",
        "compose_webcam_picture_in_picture",
        "link_synchronized_screen_pair",
        "preview_synchronized_pause_compaction",
        "apply_synchronized_pause_compaction",
        "finalize_synchronized_pause_compaction",
        "prepare_finalized_timeline_render",
        "start_finalized_timeline_render",
        "get_finalized_timeline_render_status",
        "prepare_finalized_timeline_audio",
        "start_finalized_timeline_audio",
        "get_finalized_timeline_audio_status",
        "apply_finalized_timeline_audio",
        "clean_dialogue_audio",
        "get_audio_report",
        "list_audio_reports",
    }
