"""Backend-specific workflow discovery; registration is never proof of liveness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.animation_workflow import REQUIRED_CAPABILITIES as ANIMATION_CAPABILITIES
from agent.broll import REQUIRED_CAPABILITIES as BROLL_CAPABILITIES
from agent.color_workflow import REQUIRED_CAPABILITIES as COLOR_CAPABILITIES
from agent.contracts import validate_contract
from agent.pause_compaction import APPLY_CAPABILITIES as PAUSE_CAPABILITIES
from agent.picture_in_picture import REQUIRED_CAPABILITIES as PIP_CAPABILITIES
from agent.synchronized_pair import REQUIRED_CAPABILITIES as SYNC_CAPABILITIES
from agent.take_sequence_render import REQUIRED_CAPABILITIES as RENDER_CAPABILITIES
from agent.take_sequence_timeline_apply import (
    REQUIRED_CAPABILITIES as TAKE_APPLY_CAPABILITIES,
)


@dataclass(frozen=True)
class Workflow:
    """Describe the exact entry points and native requirements of one workflow."""

    name: str
    python_tools: tuple[str, ...]
    marker_tools: tuple[str, ...] = ()
    python_capabilities: tuple[str, ...] = ()
    marker_alternatives: tuple[str, ...] = ()
    local: bool = False
    writes: bool = True
    media: bool = False


WORKFLOWS = (
    Workflow("project_inspection", ("resolve_get_project",),
             ("resolve_lua_get_project",), writes=False),
    Workflow("project_creation", ("resolve_create_project",),
             ("resolve_lua_create_project",), ("project.create",)),
    Workflow("timeline_inspection", ("resolve_get_timeline",),
             ("resolve_lua_get_timeline_summary",),
             ("timeline.read",), writes=False),
    Workflow("media_import", ("resolve_import_media",),
             ("resolve_lua_import_media",), ("media.import",), media=True),
    Workflow("timeline_assembly", ("resolve_create_timeline", "resolve_append_clip"),
             ("resolve_lua_create_timeline", "resolve_lua_append_clip"),
             ("timeline.create", "clip.insert"), media=True),
    Workflow("duplicate_timeline", ("resolve_duplicate_timeline",),
             ("resolve_lua_duplicate_timeline",), ("timeline.duplicate",)),
    Workflow("clip_transform", ("resolve_set_clip_transform",),
             ("resolve_lua_set_clip_properties",), ("clip.transform",)),
    Workflow("rough_cut_planning", ("create_rough_cut",), local=True),
    Workflow("rough_cut_apply", ("preview_rough_cut_apply", "apply_rough_cut"),
             python_capabilities=("timeline.duplicate", "clip.read"),
             marker_alternatives=("resolve_lua_ripple_cut_batch",)),
    Workflow("dialogue_audio_processing", ("clean_dialogue_audio",), local=True),
    Workflow("take_analysis", ("analyze_take_candidates",), local=True),
    Workflow("scripted_take_analysis", ("analyze_scripted_take_candidates",),
             local=True),
    Workflow("editing_recipes", ("preview_editing_recipe", "run_editing_recipe"),
             python_capabilities=SYNC_CAPABILITIES),
    Workflow("visual_treatment", ("preview_visual_treatment",
             "apply_visual_treatment"),
             python_capabilities=("clip.read", "clip.transform", "title.insert")),
    Workflow("baseline_edit", ("preview_baseline_edit", "start_baseline_render",
             "get_baseline_render_status"),
             python_capabilities=(*SYNC_CAPABILITIES, *RENDER_CAPABILITIES)),
    Workflow("screen_webcam_sync", ("sync_screen_and_webcam",),
             python_capabilities=SYNC_CAPABILITIES,
             marker_alternatives=("resolve_lua_append_synced_pairs",)),
    Workflow("webcam_picture_in_picture", ("compose_webcam_picture_in_picture",),
             python_capabilities=PIP_CAPABILITIES,
             marker_alternatives=("resolve_lua_set_clip_properties",
                                  "resolve_lua_circle_mask")),
    Workflow("pause_compaction", ("apply_synchronized_pause_compaction",),
             python_capabilities=PAUSE_CAPABILITIES,
             marker_alternatives=("resolve_lua_ripple_cut_batch",
                                  "resolve_lua_ripple_cut_span")),
    Workflow("synchronized_screen_link", ("link_synchronized_screen_pair",),
             python_capabilities=("clip.link",)),
    Workflow("pause_compaction_finalization",
             ("finalize_synchronized_pause_compaction",),
             python_capabilities=("clip.link", "clip.read", "clip.transform")),
    Workflow("finalized_audio_delivery", ("prepare_finalized_timeline_audio",
             "start_finalized_timeline_audio", "get_finalized_timeline_audio_status",
             "apply_finalized_timeline_audio"),
             python_capabilities=(*RENDER_CAPABILITIES, "clip.range_insert",
                                  "clip.enable", "timeline.track.create", "clip.read")),
    Workflow("finalized_render", ("prepare_finalized_timeline_render",
             "start_finalized_timeline_render", "get_finalized_timeline_render_status"),
             python_capabilities=RENDER_CAPABILITIES),
    Workflow("automatic_subtitles", ("generate_subtitles",),
             python_capabilities=("subtitle.read", "subtitle.import", "media.import"),
             marker_alternatives=("resolve_lua_add_subtitles",)),
    Workflow("resolve_auto_captions", ("resolve_create_subtitles_from_audio",),
             python_capabilities=("subtitle.auto_caption",)),
    Workflow("broll_workflow", ("apply_broll_plan",),
             python_capabilities=BROLL_CAPABILITIES,
             marker_alternatives=("resolve_lua_append_clip",)),
    Workflow("animation_templates", ("apply_animation_template",),
             python_capabilities=ANIMATION_CAPABILITIES),
    Workflow("color_treatment", ("apply_color_treatment",),
             python_capabilities=(*COLOR_CAPABILITIES, "color.inspect",
                                  "color.cdl.apply")),
    Workflow("approved_take_delivery", ("apply_take_sequence_timeline",
             "inspect_take_sequence_qc", "start_take_sequence_render"),
             python_capabilities=(*TAKE_APPLY_CAPABILITIES, *RENDER_CAPABILITIES)),
    Workflow("render_delivery", ("resolve_prepare_render_job",
             "resolve_start_render_job", "resolve_verify_render_output"),
             ("resolve_lua_prepare_render", "resolve_lua_start_render",
              "resolve_lua_get_render_status"),
             ("render.configure", "render.start", "render.discovery")),
    Workflow("privacy_blur", (), ("resolve_lua_privacy_blur_batch",)),
    Workflow("native_audio_processing", (), ("resolve_lua_native_audio",)),
    Workflow("native_synced_pair_assembly", (), ("resolve_lua_append_synced_pairs",),
             media=True),
    Workflow("native_ripple_editing", (), ("resolve_lua_ripple_cut_batch",)),
    Workflow("srt_import", (), ("resolve_lua_add_subtitles",), media=True),
    Workflow("circular_camera_mask", (), ("resolve_lua_circle_mask",)),
    Workflow("recording_track_setup", (),
             ("resolve_lua_inspect_audio_track", "resolve_lua_ensure_mono_audio_track",
              "resolve_lua_set_recording_media_root")),
    Workflow("fairlight_processing", (),
             ("resolve_free_get_audio_processing", "resolve_free_set_audio_eq",
              "resolve_free_set_audio_dynamics")),
    Workflow("physical_audio_input", (), ("resolve_free_select_audio_input",)),
    Workflow("recording_start", ()),
    Workflow("effect_inserts", ()),
)


def workflow_capabilities(
    *,
    backend: str,
    tools: Sequence[str],
    connected: bool | None,
    evidence: str,
    session: str | None = None,
    project: Mapping[str, Any] | None = None,
    protocol: int = 4,
    session_capabilities: Sequence[str] = (),
    native_capabilities: Mapping[str, Any] | None = None,
    media_roots_configured: bool | None = None,
    mutation_blocker: str | None = None,
    ui: Mapping[str, Any] | None = None,
    fairlight: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Report only this endpoint's workflows, without porting incompatible APIs.

    Available means callable subject to each tool's input and live preflight
    guards. Partial workflows expose alternatives, never a supported apply path.
    Cached supervisor/heartbeat evidence cannot establish a native connection.
    """
    marker = backend == "free_marker"
    registered = set(tools)
    native = native_capabilities or {}
    rows: list[dict[str, Any]] = []
    for workflow in WORKFLOWS:
        required = workflow.marker_tools if marker else workflow.python_tools
        missing = sorted(set(required) - registered)
        alternatives = sorted(set(workflow.marker_alternatives) & registered)
        supported = bool(required) and not missing
        reason = "registered_entry_points"
        status = "available"
        if not supported:
            status = "partial" if alternatives else "unsupported"
            reason = "workflow_adapter_missing" if alternatives else "not_exposed"
        elif not workflow.local:
            if marker and (
                protocol != 4
                or (workflow.name == "recording_track_setup"
                    and "recording_api_v1" not in session_capabilities)
                or (workflow.name == "project_creation"
                    and "create_project" not in session_capabilities)
            ):
                status, reason = "unsupported", "session_upgrade_required"
                supported = False
            if status == "available":
                if connected is False:
                    status, reason = "blocked", "native_disconnected"
                elif workflow.writes and mutation_blocker:
                    status, reason = "blocked", mutation_blocker
                elif connected is not True:
                    status, reason = "unverified", "fresh_native_check_required"
                elif not marker and any(
                    native.get(key) is False
                    for key in workflow.python_capabilities
                ):
                    status, reason = "unsupported", "native_capability_unavailable"
                elif not marker and any(
                    native.get(key) is not True
                    for key in workflow.python_capabilities
                ):
                    status, reason = "unverified", "native_capability_not_confirmed"
                elif workflow.media and media_roots_configured is not True:
                    status, reason = "blocked", "media_roots_not_verified"
            adapter = (
                (fairlight or {}) if workflow.name == "fairlight_processing"
                else (ui or {}) if workflow.name == "physical_audio_input" else None
            )
            if adapter is not None:
                if adapter.get("available") is not True:
                    status, reason = "blocked", "windows_ui_unavailable_or_disabled"
                elif adapter.get("live_verification") == "pending":
                    status, reason = "unverified", "live_acceptance_pending"
        rows.append({
            "workflow": workflow.name,
            "supported": supported,
            "available": status == "available",
            "status": status,
            "reason": reason,
            "tools": list(required) if supported else [],
            "missing_tools": missing,
            "required_native_capabilities": (
                [] if marker else list(workflow.python_capabilities)
            ),
            "alternative_tools": alternatives,
            "requires_confirmation": workflow.writes and not workflow.local,
            "scope": "local" if workflow.local else "resolve",
        })
    result = {
        "schema_version": "1.0",
        "backend": backend,
        "session": session,
        "connected": connected,
        "evidence": evidence,
        "project": dict(project) if project is not None else None,
        "mutation_blocker": mutation_blocker,
        "registered_tools": sorted(registered),
        "workflows": rows,
        "limitations": [
            "Availability is a point-in-time preflight, not proof of an edit.",
            "Partial alternatives do not execute workflows or accept their receipts.",
            "Technical take ranking does not establish editorial or visual quality.",
            "Dialogue gain/limiter processing is not denoise or de-reverb.",
            "Every write retains confirmation, identity and backup guards.",
        ],
    }
    validate_contract("workflow-capabilities", result)
    return result
