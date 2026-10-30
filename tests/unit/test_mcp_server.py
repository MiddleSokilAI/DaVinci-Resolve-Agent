"""MCP tool-surface tests using the official in-memory client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from mcp import Client

from agent.application import AgentApplication
from mcp_server.server import create_server


class StubResolveReader:
    def current_project(self, timeout_seconds: float = 30) -> dict[str, Any]:
        return {"name": "Test Project"}

    def stop_bridge(self, timeout_seconds: float = 30) -> dict[str, Any]:
        return {"status": "stopping"}

    def timelines(self, timeout_seconds: float = 30) -> list[dict[str, Any]]:
        return [{"index": 1, "name": "Main"}]

    def current_timeline(self, timeout_seconds: float = 30) -> dict[str, Any]:
        return {"name": "Main"}

    def timeline_items(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "timeline_id": timeline_id,
            "name": "Main",
            "items": [{"timeline_item_id": "item-1"}],
        }

    def media_pool_items(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "items": [{"asset_id": "asset-1", "name": "screen.mkv"}],
            "folder_count": 1,
        }

    def editing_metadata(
        self,
        timeline_id: str,
        asset_ids: list[str],
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "timeline": {
                "timeline_id": timeline_id,
                "video_track_count": 1,
                "audio_track_count": 1,
                "frame_rate": 60.0,
            },
            "assets": [
                {
                    "asset_id": asset_id,
                    "duration_frames": 240,
                    "frame_rate": 60.0,
                }
                for asset_id in asset_ids
            ],
        }

    def subtitle_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "timeline_id": timeline_id,
            "subtitle_track_count": 0,
            "subtitle_item_count": 0,
            "tracks": [],
            "auto_caption": {"method_available": True},
        }

    def animation_template_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "timeline": {"timeline_id": timeline_id, "name": "Main"},
            "methods": {"insert_fusion_title": True},
            "templates": [
                {
                    "template_id": "accent-card-v1",
                    "installed": True,
                    "hash_matches": True,
                }
            ],
            "ready": True,
        }

    def color_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "timeline": {"timeline_id": timeline_id, "name": "Main"},
            "methods": {"SetCDL": True, "AddVersion": True},
            "items": [],
            "ready": True,
            "apply_candidate": True,
        }

    def apply_color_preset(
        self,
        timeline_id: str,
        timeline_item_ids: list[str],
        preset_id: str,
        *,
        confirm_apply: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeline_id": timeline_id,
            "preset_id": preset_id,
            "items": [
                {"timeline_item_id": item_id}
                for item_id in timeline_item_ids
            ],
        }

    def create_subtitles_from_audio(
        self,
        timeline_id: str,
        *,
        confirm_create: bool,
        timeout_seconds: float = 300,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert confirm_create is True
        return {
            "timeline_id": timeline_id,
            "policy": {"language": "auto"},
            "subtitle_environment": {"subtitle_item_count": 1},
        }

    def insert_animation_template(
        self,
        timeline_id: str,
        template_id: str,
        timecode: str,
        *,
        confirm_insert: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert confirm_insert is True
        return {
            "timeline_id": timeline_id,
            "template_id": template_id,
            "requested_timecode": timecode,
            "item": {"timeline_item_id": "animation-1", "fusion_comp_count": 1},
        }

    def workspace_snapshot(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "project": {"name": "Test Project"},
            "timelines": [{"index": 1, "name": "Main"}],
        }

    def render_environment(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "formats": [],
            "current": {"format": "mp4", "codec": "H264"},
            "presets": [],
            "jobs": [],
        }

    def import_media(
        self,
        paths: list[str],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {"items": [{"asset_id": "asset-1", "name": paths[0]}]}

    def close_project(
        self, expected_project_id: str, *, confirm_close: bool,
        idempotency_key: str, timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        assert confirm_close is True
        return {"project": {"project_id": expected_project_id}, "closed": True}

    def create_project(
        self,
        name: str,
        *,
        confirm_create: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert confirm_create is True
        return {"project": {"project_id": "project-new", "name": name}}

    def create_timeline(
        self,
        name: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {"timeline": {"timeline_id": "timeline-1", "name": name}}

    def ensure_timeline_tracks(
        self,
        timeline_id: str,
        video_track_count: int,
        audio_track_count: int,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeline": {"timeline_id": timeline_id},
            "after": {
                "video_track_count": video_track_count,
                "audio_track_count": audio_track_count,
            },
        }

    def duplicate_timeline(
        self,
        timeline_id: str,
        name: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "source_timeline": {"timeline_id": timeline_id},
            "timeline": {"timeline_id": "timeline-2", "name": name},
        }

    def set_current_timeline(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeline": {
                "timeline_id": timeline_id,
                "name": "Selected",
            }
        }

    def append_clip(
        self,
        timeline_id: str,
        asset_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {"timeline_id": timeline_id, "asset_id": asset_id}

    def insert_title(
        self,
        timeline_id: str,
        title_name: str,
        timecode: str,
        *,
        confirm_insert: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeline_id": timeline_id,
            "title_name": title_name,
            "requested_timecode": timecode,
            "confirm_insert": confirm_insert,
        }

    def append_subtitle_file(
        self,
        timeline_id: str,
        asset_id: str,
        subtitle_path: str,
        import_idempotency_key: str,
        *,
        confirm_apply: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeline_id": timeline_id,
            "asset_id": asset_id,
            "subtitle_path": subtitle_path,
            "import_idempotency_key": import_idempotency_key,
            "confirm_apply": confirm_apply,
        }

    def insert_clip(
        self,
        timeline_id: str,
        asset_id: str,
        source_start_frame: int,
        source_end_frame: int,
        position_frames: int,
        track_type: str,
        track_index: int,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeline_id": timeline_id,
            "asset_id": asset_id,
            "item": {
                "source_start_frame": source_start_frame,
                "source_end_frame": source_end_frame,
                "timeline_start_frame": position_frames,
                "track_type": track_type,
                "track_index": track_index,
            },
        }

    def insert_clips(
        self,
        timeline_id: str,
        placements: list[dict[str, Any]],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {"timeline_id": timeline_id, "items": placements}

    def set_clip_enabled(
        self,
        timeline_id: str,
        timeline_item_id: str,
        enabled: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeline_id": timeline_id,
            "timeline_item_id": timeline_item_id,
            "previous_enabled": not enabled,
            "enabled": enabled,
        }

    def set_clips_linked(
        self,
        timeline_id: str,
        timeline_item_ids: list[str],
        linked: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeline_id": timeline_id,
            "timeline_item_ids": timeline_item_ids,
            "linked": linked,
        }

    def set_clip_link_groups(
        self,
        timeline_id: str,
        groups: list[list[str]],
        linked: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {"timeline_id": timeline_id, "groups": groups, "linked": linked}

    def set_clip_transform(
        self,
        timeline_id: str,
        timeline_item_id: str,
        *,
        position_x: float | None = None,
        position_y: float | None = None,
        zoom: float | None = None,
        rotation_degrees: float | None = None,
        opacity_percent: float | None = None,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timeline_id": timeline_id,
            "timeline_item_id": timeline_item_id,
            "transform": {
                "position_x": position_x,
                "position_y": position_y,
                "zoom": zoom,
                "rotation_degrees": rotation_degrees,
                "opacity_percent": opacity_percent,
            },
        }

    def set_clip_transforms(
        self,
        timeline_id: str,
        items: list[dict[str, Any]],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {"timeline_id": timeline_id, "items": items}

    def delete_clip(
        self,
        timeline_id: str,
        timeline_item_id: str,
        *,
        confirm_delete: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert confirm_delete is True
        return {
            "timeline_id": timeline_id,
            "timeline_item_id": timeline_item_id,
            "deleted": True,
            "ripple": False,
        }

    def add_marker(
        self,
        timeline_id: str,
        frame: int,
        color: str,
        name: str,
        note: str,
        duration: int,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {"timeline_id": timeline_id, "frame": frame, "color": color}

    def prepare_render_job(
        self,
        custom_name: str,
        *,
        timeline_id: str | None = None,
        profile: str = "youtube-1080p-h264-v1",
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "job_id": "job-1",
            "custom_name": custom_name,
            "preset": profile,
            "started": False,
        }

    def render_job_status(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {"job_id": job_id, "status": {"JobStatus": "Ready"}}

    def start_render_job(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {"job_id": job_id, "started": True}


class StubMediaPolicy:
    def prepare_import(self, paths: list[str]) -> list[str]:
        return paths

    def validate_files(self, paths: list[str]) -> list[str]:
        return paths


class StubRoughCutPlanner:
    def create_plan(
        self,
        *,
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
        return {
            "status": "pending_review",
            "timeline": {"name": timeline_name},
            "review": {"required": True, "apply_supported": False},
        }


class StubRoughCutReviewer:
    def approve(
        self,
        plan_id: str,
        *,
        confirm_review: bool,
    ) -> dict[str, Any]:
        return {
            "plan_id": plan_id,
            "status": "approved",
            "confirm_review": confirm_review,
            "apply_supported": False,
        }


class StubRoughCutInspector:
    def get_plan(self, plan_id: str) -> dict[str, Any]:
        return {
            "plan": {"plan_id": plan_id},
            "approval": None,
            "effective_status": "pending_review",
            "apply_supported": False,
        }

    def list_plans(self, limit: int = 100) -> dict[str, Any]:
        return {
            "plans": [{"plan_id": "a" * 64}],
            "count": 1,
            "truncated": False,
        }


class StubSynchronizedPairAssembler:
    def assemble(
        self,
        *,
        timeline_name: str,
        screen_asset_id: str,
        webcam_asset_id: str,
        webcam_offset_ms: int,
        confirm_sync: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "applied",
            "timeline": {"timeline_id": "timeline-38", "name": timeline_name},
            "inputs": {
                "screen_asset_id": screen_asset_id,
                "webcam_asset_id": webcam_asset_id,
                "webcam_offset_ms": webcam_offset_ms,
                "confirm_sync": confirm_sync,
            },
        }


class StubPictureInPictureComposer:
    def compose(
        self,
        *,
        synchronized_pair_receipt_id: str,
        size_percent: float,
        center_x_percent: float,
        center_y_percent: float,
        confirm_layout: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "applied",
            "inputs": {
                "synchronized_pair_receipt_id": synchronized_pair_receipt_id,
                "size_percent": size_percent,
                "center_x_percent": center_x_percent,
                "center_y_percent": center_y_percent,
                "confirm_layout": confirm_layout,
            },
            "transform": {"zoom": size_percent / 100},
        }


class StubSynchronizedScreenLinker:
    def link(
        self,
        *,
        synchronized_pair_receipt_id: str,
        confirm_link: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "applied",
            "inputs": {
                "synchronized_pair_receipt_id": synchronized_pair_receipt_id,
                "confirm_link": confirm_link,
            },
        }


class StubPauseCompactionPreviewer:
    def preview(
        self,
        *,
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "preview",
            "apply_supported": False,
            "plan_id": plan_id,
            "synchronized_pair_receipt_id": synchronized_pair_receipt_id,
            "target_timeline_name": target_timeline_name,
        }


class StubPauseCompactionApplier:
    def apply(
        self,
        *,
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "applied",
            "plan_id": plan_id,
            "synchronized_pair_receipt_id": synchronized_pair_receipt_id,
            "target_timeline_name": target_timeline_name,
            "confirm_apply": confirm_apply,
        }


class StubPauseCompactionFinalizer:
    def finalize(
        self,
        *,
        pause_compaction_receipt_id: str,
        picture_in_picture_receipt_id: str,
        synchronized_link_receipt_id: str,
        confirm_finalize: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "applied",
            "receipt_id": "d" * 64,
            "confirm_finalize": confirm_finalize,
        }


class StubFinalizedRenderPreparer:
    def prepare(
        self,
        *,
        finalization_receipt_id: str,
        custom_name: str,
        profile: str,
        confirm_prepare: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "applied",
            "receipt_id": "e" * 64,
            "operation": {"result": {"job_id": "job-final"}},
        }


class StubFinalizedRenderExecutor:
    def start(
        self,
        *,
        preparation_receipt_id: str,
        confirm_render: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {"status": "started", "receipt_id": "f" * 64}

    def status(
        self,
        execution_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "execution_receipt_id": execution_receipt_id,
            "output": {"validation": {"passed": True}},
        }


class StubFinalizedAudioExtractor:
    def prepare(
        self,
        *,
        finalization_receipt_id: str,
        custom_name: str,
        confirm_prepare: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {"status": "prepared", "receipt_id": "1" * 64}

    def start(
        self,
        extraction_receipt_id: str,
        *,
        confirm_render: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {"status": "started", "receipt_id": extraction_receipt_id}

    def status(
        self,
        extraction_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "receipt_id": extraction_receipt_id,
            "output": {"validation": {"passed": True}},
        }


class StubFinalizedAudioIntegrator:
    def apply(
        self,
        *,
        extraction_receipt_id: str,
        audio_report_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "applied",
            "inputs": {
                "extraction_receipt_id": extraction_receipt_id,
                "audio_report_id": audio_report_id,
            },
        }


class StubAudioProcessor:
    def process(
        self,
        source_file: str,
        *,
        preset: str = "pcm-dialogue-level-v1",
    ) -> dict[str, Any]:
        return {
            "status": "completed",
            "source": {"path": source_file, "preserved": True},
            "preset": {"name": preset},
        }


class StubAudioReportInspector:
    def get_report(self, report_id: str) -> dict[str, Any]:
        return {"report_id": report_id, "status": "completed"}

    def list_reports(self, limit: int = 100) -> dict[str, Any]:
        return {
            "reports": [{"report_id": "b" * 64}],
            "count": 1,
            "truncated": False,
        }


class StubSubtitleGenerator:
    def generate(
        self,
        source_file: str,
        timeline_id: str,
        *,
        confirm_apply: bool,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        assert source_file == "dialogue.wav"
        assert confirm_apply is True
        return {"status": "applied", "timeline_id": timeline_id}


class StubEditingRecipeRunner:
    def list_recipes(self) -> dict[str, Any]:
        return {"recipes": [{"recipe_id": "tutorial-layout-v1"}], "count": 1}

    def get_recipe(self, recipe_id: str) -> dict[str, Any]:
        return {"recipe_id": recipe_id, "recipe_version": "1.0"}

    def preview(
        self,
        recipe_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        return {"recipe_id": recipe_id, "inputs": inputs, "status": "ready"}

    def run(
        self,
        recipe_id: str,
        inputs: dict[str, Any],
        *,
        confirm_execute: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        assert confirm_execute is True
        return {"recipe_id": recipe_id, "inputs": inputs, "status": "applied"}


class StubBaselineEditWorkflow:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ready", "arguments": kwargs}

    def start(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "started", "receipt_id": "f" * 64}

    def status(self, receipt_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "complete", "receipt_id": receipt_id}


class StubBrollWorkflow:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "preview", "plan_id": "e" * 64, "arguments": kwargs}

    def apply(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "applied", "receipt_id": "f" * 64, "arguments": kwargs}

    def status(self, receipt_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "applied", "receipt_id": receipt_id, "live": True}


class StubAnimationTemplateWorkflow:
    def list_templates(self) -> dict[str, Any]:
        return {"count": 1, "templates": [{"template_id": "accent-card-v1"}]}

    def get_template(self, template_id: str) -> dict[str, Any]:
        return {"template_id": template_id, "template_version": "1.0"}

    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "preview", "plan_id": "d" * 64, "arguments": kwargs}

    def apply(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "applied", "receipt_id": "c" * 64, "arguments": kwargs}


class StubWorkflowAuditor:
    def __init__(self) -> None:
        self.operations: list[str] = []

    def run(
        self,
        operation: str,
        callback: Callable[[], Any],
    ) -> Any:
        self.operations.append(operation)
        return callback()


def _fresh_state() -> dict[str, Any]:
    return {
        "bridge_version": "0.1.0",
        "protocol_version": "1.0",
        "status": "ready",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "capabilities": {"bridge.ping": True},
    }


def _broll_placement() -> dict[str, Any]:
    return {
        "asset_id": "asset-1",
        "source_start_frame": 0,
        "source_end_frame": 47,
        "position_frames": 24,
        "track_index": 3,
        "purpose": "Show a detail",
    }


def test_mcp_exposes_fixed_m5_tool_surface() -> None:
    workflow_audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        state_loader=_fresh_state,
        media_policy=StubMediaPolicy(),
        rough_cut_planner=StubRoughCutPlanner(),
        rough_cut_reviewer=StubRoughCutReviewer(),
        rough_cut_inspector=StubRoughCutInspector(),
        synchronized_pair_assembler=StubSynchronizedPairAssembler(),
        picture_in_picture_composer=StubPictureInPictureComposer(),
        synchronized_screen_linker=StubSynchronizedScreenLinker(),
        pause_compaction_previewer=StubPauseCompactionPreviewer(),
        pause_compaction_applier=StubPauseCompactionApplier(),
        pause_compaction_finalizer=StubPauseCompactionFinalizer(),
        finalized_render_preparer=StubFinalizedRenderPreparer(),
        finalized_render_executor=StubFinalizedRenderExecutor(),
        finalized_audio_extractor=StubFinalizedAudioExtractor(),
        finalized_audio_integrator=StubFinalizedAudioIntegrator(),
        audio_processor=StubAudioProcessor(),
        audio_report_inspector=StubAudioReportInspector(),
        subtitle_generator=StubSubtitleGenerator(),
        editing_recipe_runner=StubEditingRecipeRunner(),
        baseline_edit_service=StubBaselineEditWorkflow(),
        broll_service=StubBrollWorkflow(),
        animation_template_service=StubAnimationTemplateWorkflow(),
        workflow_audit=workflow_audit,
    )
    server = create_server(application)

    async def exercise_server() -> tuple[dict[str, Any], set[str]]:
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            annotations = {
                tool.name: tool.annotations for tool in tools.tools
            }
            assert all(annotation is not None for annotation in annotations.values())
            assert {
                name
                for name, annotation in annotations.items()
                if annotation is not None and annotation.destructive_hint is True
            } == {"resolve_delete_clip"}
            status_annotations = annotations["video_agent_status"]
            import_annotations = annotations["resolve_import_media"]
            sequence_import_annotations = annotations[
                "apply_take_sequence_media_import"
            ]
            sequence_import_get_annotations = annotations[
                "get_take_sequence_media_import"
            ]
            sequence_apply_preview_annotations = annotations[
                "preview_take_sequence_timeline_apply"
            ]
            sequence_apply_annotations = annotations[
                "apply_take_sequence_timeline"
            ]
            sequence_apply_get_annotations = annotations[
                "get_take_sequence_timeline_apply"
            ]
            rough_cut_annotations = annotations["create_rough_cut"]
            approval_annotations = annotations["approve_rough_cut"]
            get_plan_annotations = annotations["get_rough_cut_plan"]
            list_plans_annotations = annotations["list_rough_cut_plans"]
            audio_annotations = annotations["clean_dialogue_audio"]
            sync_annotations = annotations["sync_screen_and_webcam"]
            pip_annotations = annotations[
                "compose_webcam_picture_in_picture"
            ]
            link_annotations = annotations[
                "link_synchronized_screen_pair"
            ]
            recipe_list_annotations = annotations["list_editing_recipes"]
            recipe_preview_annotations = annotations["preview_editing_recipe"]
            recipe_run_annotations = annotations["run_editing_recipe"]
            baseline_preview_annotations = annotations["preview_baseline_edit"]
            baseline_start_annotations = annotations["start_baseline_render"]
            baseline_status_annotations = annotations[
                "get_baseline_render_status"
            ]
            broll_preview_annotations = annotations["preview_broll_plan"]
            broll_apply_annotations = annotations["apply_broll_plan"]
            broll_status_annotations = annotations["get_broll_status"]
            animation_environment_annotations = annotations[
                "resolve_get_animation_template_environment"
            ]
            animation_insert_annotations = annotations[
                "resolve_insert_animation_template"
            ]
            animation_list_annotations = annotations["list_animation_templates"]
            animation_get_annotations = annotations["get_animation_template"]
            animation_preview_annotations = annotations[
                "preview_animation_template"
            ]
            animation_apply_annotations = annotations["apply_animation_template"]
            compaction_annotations = annotations[
                "preview_synchronized_pause_compaction"
            ]
            compaction_apply_annotations = annotations[
                "apply_synchronized_pause_compaction"
            ]
            finalized_render_annotations = annotations[
                "prepare_finalized_timeline_render"
            ]
            finalized_start_annotations = annotations[
                "start_finalized_timeline_render"
            ]
            finalized_status_annotations = annotations[
                "get_finalized_timeline_render_status"
            ]
            audio_prepare_annotations = annotations[
                "prepare_finalized_timeline_audio"
            ]
            audio_start_annotations = annotations[
                "start_finalized_timeline_audio"
            ]
            audio_status_annotations = annotations[
                "get_finalized_timeline_audio_status"
            ]
            audio_apply_annotations = annotations[
                "apply_finalized_timeline_audio"
            ]
            get_audio_annotations = annotations["get_audio_report"]
            list_audio_annotations = annotations["list_audio_reports"]
            render_annotations = annotations["resolve_get_render_options"]
            subtitle_annotations = annotations[
                "resolve_get_subtitle_environment"
            ]
            create_subtitle_annotations = annotations[
                "resolve_create_subtitles_from_audio"
            ]
            prepare_annotations = annotations["resolve_prepare_render_job"]
            job_status_annotations = annotations[
                "resolve_get_render_job_status"
            ]
            start_annotations = annotations["resolve_start_render_job"]
            verify_annotations = annotations["resolve_verify_render_output"]
            enabled_annotations = annotations["resolve_set_clip_enabled"]
            select_annotations = annotations["resolve_set_current_timeline"]
            transform_annotations = annotations["resolve_set_clip_transform"]
            delete_annotations = annotations["resolve_delete_clip"]
            assert status_annotations is not None
            assert import_annotations is not None
            assert sequence_import_annotations is not None
            assert sequence_import_get_annotations is not None
            assert sequence_apply_preview_annotations is not None
            assert sequence_apply_annotations is not None
            assert sequence_apply_get_annotations is not None
            assert rough_cut_annotations is not None
            assert approval_annotations is not None
            assert get_plan_annotations is not None
            assert list_plans_annotations is not None
            assert audio_annotations is not None
            assert sync_annotations is not None
            assert pip_annotations is not None
            assert link_annotations is not None
            assert recipe_list_annotations is not None
            assert recipe_preview_annotations is not None
            assert recipe_run_annotations is not None
            assert baseline_preview_annotations is not None
            assert baseline_start_annotations is not None
            assert baseline_status_annotations is not None
            assert broll_preview_annotations is not None
            assert broll_apply_annotations is not None
            assert broll_status_annotations is not None
            assert animation_environment_annotations is not None
            assert animation_insert_annotations is not None
            assert animation_list_annotations is not None
            assert animation_get_annotations is not None
            assert animation_preview_annotations is not None
            assert animation_apply_annotations is not None
            assert compaction_annotations is not None
            assert compaction_apply_annotations is not None
            assert finalized_render_annotations is not None
            assert finalized_start_annotations is not None
            assert finalized_status_annotations is not None
            assert audio_prepare_annotations is not None
            assert audio_start_annotations is not None
            assert audio_status_annotations is not None
            assert audio_apply_annotations is not None
            assert get_audio_annotations is not None
            assert list_audio_annotations is not None
            assert render_annotations is not None
            assert subtitle_annotations is not None
            assert create_subtitle_annotations is not None
            assert prepare_annotations is not None
            assert job_status_annotations is not None
            assert start_annotations is not None
            assert verify_annotations is not None
            assert enabled_annotations is not None
            assert select_annotations is not None
            assert transform_annotations is not None
            assert delete_annotations is not None
            assert status_annotations.read_only_hint is True
            assert import_annotations.read_only_hint is False
            assert sequence_import_annotations.read_only_hint is False
            assert sequence_import_get_annotations.read_only_hint is True
            assert sequence_apply_preview_annotations.read_only_hint is True
            assert sequence_apply_annotations.read_only_hint is False
            assert sequence_apply_get_annotations.read_only_hint is True
            assert rough_cut_annotations.read_only_hint is False
            assert approval_annotations.read_only_hint is False
            assert get_plan_annotations.read_only_hint is True
            assert list_plans_annotations.read_only_hint is True
            assert audio_annotations.read_only_hint is False
            assert sync_annotations.read_only_hint is False
            assert pip_annotations.read_only_hint is False
            assert link_annotations.read_only_hint is False
            assert recipe_list_annotations.read_only_hint is True
            assert recipe_preview_annotations.read_only_hint is True
            assert recipe_run_annotations.read_only_hint is False
            assert baseline_preview_annotations.read_only_hint is True
            assert baseline_start_annotations.read_only_hint is False
            assert baseline_status_annotations.read_only_hint is True
            assert broll_preview_annotations.read_only_hint is True
            assert broll_apply_annotations.read_only_hint is False
            assert broll_status_annotations.read_only_hint is True
            assert animation_environment_annotations.read_only_hint is True
            assert animation_insert_annotations.read_only_hint is False
            assert animation_list_annotations.read_only_hint is True
            assert animation_get_annotations.read_only_hint is True
            assert animation_preview_annotations.read_only_hint is True
            assert animation_apply_annotations.read_only_hint is False
            assert compaction_annotations.read_only_hint is True
            assert compaction_apply_annotations.read_only_hint is False
            assert finalized_render_annotations.read_only_hint is False
            assert finalized_start_annotations.read_only_hint is False
            assert finalized_status_annotations.read_only_hint is True
            assert audio_prepare_annotations.read_only_hint is False
            assert audio_start_annotations.read_only_hint is False
            assert audio_status_annotations.read_only_hint is True
            assert audio_apply_annotations.read_only_hint is False
            assert get_audio_annotations.read_only_hint is True
            assert list_audio_annotations.read_only_hint is True
            assert render_annotations.read_only_hint is True
            assert subtitle_annotations.read_only_hint is True
            assert create_subtitle_annotations.read_only_hint is False
            assert prepare_annotations.read_only_hint is False
            assert job_status_annotations.read_only_hint is True
            assert start_annotations.read_only_hint is False
            assert verify_annotations.read_only_hint is True
            assert enabled_annotations.read_only_hint is False
            assert select_annotations.read_only_hint is False
            assert transform_annotations.read_only_hint is False
            assert delete_annotations.read_only_hint is False
            assert delete_annotations.destructive_hint is True

            results = {
                "project": await client.call_tool("resolve_get_project", {}),
                "stopped": await client.call_tool("resolve_stop_bridge", {}),
                "timelines": await client.call_tool(
                    "resolve_list_timelines",
                    {},
                ),
                "timeline": await client.call_tool(
                    "resolve_get_timeline",
                    {},
                ),
                "items": await client.call_tool(
                    "resolve_list_timeline_items",
                    {"timeline_id": "timeline-1"},
                ),
                "media_pool_items": await client.call_tool(
                    "resolve_list_media_pool_items",
                    {},
                ),
                "editing_metadata": await client.call_tool(
                    "resolve_get_editing_metadata",
                    {"timeline_id": "timeline-1", "asset_ids": ["asset-1"]},
                ),
                "subtitles": await client.call_tool(
                    "resolve_get_subtitle_environment",
                    {"timeline_id": "timeline-1"},
                ),
                "animation_environment": await client.call_tool(
                    "resolve_get_animation_template_environment",
                    {"timeline_id": "timeline-1"},
                ),
                "created_subtitles": await client.call_tool(
                    "resolve_create_subtitles_from_audio",
                    {"timeline_id": "timeline-1", "confirm_create": True},
                ),
                "generated_subtitles": await client.call_tool(
                    "generate_subtitles",
                    {
                        "source_file": "dialogue.wav",
                        "timeline_id": "timeline-1",
                        "confirm_apply": True,
                    },
                ),
                "snapshot": await client.call_tool(
                    "resolve_get_workspace_snapshot",
                    {},
                ),
                "render": await client.call_tool(
                    "resolve_get_render_options",
                    {},
                ),
                "status": await client.call_tool("video_agent_status", {}),
                "imported": await client.call_tool(
                    "resolve_import_media",
                    {"paths": ["sample.wav"]},
                ),
                "created": await client.call_tool(
                    "resolve_create_timeline",
                    {"name": "M4 Timeline"},
                ),
                "tracks": await client.call_tool(
                    "resolve_ensure_timeline_tracks",
                    {
                        "timeline_id": "timeline-1",
                        "video_track_count": 2,
                        "audio_track_count": 1,
                    },
                ),
                "duplicated": await client.call_tool(
                    "resolve_duplicate_timeline",
                    {
                        "timeline_id": "timeline-1",
                        "name": "Agent Draft",
                    },
                ),
                "selected": await client.call_tool(
                    "resolve_set_current_timeline",
                    {"timeline_id": "timeline-1"},
                ),
                "appended": await client.call_tool(
                    "resolve_append_clip",
                    {"timeline_id": "timeline-1", "asset_id": "asset-1"},
                ),
                "inserted": await client.call_tool(
                    "resolve_insert_clip",
                    {
                        "timeline_id": "timeline-1",
                        "asset_id": "asset-1",
                        "source_start_frame": 0,
                        "source_end_frame": 240,
                        "position_frames": 0,
                        "track_type": "video",
                        "track_index": 1,
                    },
                ),
                "animation_inserted": await client.call_tool(
                    "resolve_insert_animation_template",
                    {
                        "timeline_id": "timeline-1",
                        "template_id": "accent-card-v1",
                        "timecode": "01:00:10:00",
                        "confirm_insert": True,
                    },
                ),
                "disabled": await client.call_tool(
                    "resolve_set_clip_enabled",
                    {
                        "timeline_id": "timeline-1",
                        "timeline_item_id": "item-1",
                        "enabled": False,
                    },
                ),
                "linked": await client.call_tool(
                    "resolve_set_clips_linked",
                    {
                        "timeline_id": "timeline-1",
                        "timeline_item_ids": ["video-1", "audio-1"],
                        "linked": True,
                    },
                ),
                "transformed": await client.call_tool(
                    "resolve_set_clip_transform",
                    {
                        "timeline_id": "timeline-1",
                        "timeline_item_id": "item-1",
                        "position_x": 320.0,
                        "zoom": 0.5,
                    },
                ),
                "deleted": await client.call_tool(
                    "resolve_delete_clip",
                    {
                        "timeline_id": "timeline-1",
                        "timeline_item_id": "item-1",
                        "confirm_delete": True,
                    },
                ),
                "marker": await client.call_tool(
                    "resolve_add_marker",
                    {
                        "timeline_id": "timeline-1",
                        "frame": 0,
                        "color": "Green",
                    },
                ),
                "prepared": await client.call_tool(
                    "resolve_prepare_render_job",
                    {
                        "custom_name": "M9 Test",
                        "profile": "youtube-2160p-h264-v1",
                    },
                ),
                "render_status": await client.call_tool(
                    "resolve_get_render_job_status",
                    {"job_id": "job-1"},
                ),
                "render_start": await client.call_tool(
                    "resolve_start_render_job",
                    {"job_id": "job-1"},
                ),
                "rough_cut": await client.call_tool(
                    "create_rough_cut",
                    {
                        "screen_file": "screen.mkv",
                        "webcam_file": "webcam.mkv",
                        "screen_audio_file": "screen.wav",
                        "webcam_audio_file": "webcam.wav",
                        "speech_audio_file": "speech.wav",
                        "timeline_name": "M5 Draft",
                    },
                ),
                "approval": await client.call_tool(
                    "approve_rough_cut",
                    {
                        "plan_id": "a" * 64,
                        "confirm_review": True,
                    },
                ),
                "rough_cut_detail": await client.call_tool(
                    "get_rough_cut_plan",
                    {"plan_id": "a" * 64},
                ),
                "rough_cut_plans": await client.call_tool(
                    "list_rough_cut_plans",
                    {"limit": 10},
                ),
                "synced": await client.call_tool(
                    "sync_screen_and_webcam",
                    {
                        "timeline_name": "M38 Synced Pair",
                        "screen_asset_id": "screen",
                        "webcam_asset_id": "webcam",
                        "webcam_offset_ms": 200,
                        "confirm_sync": True,
                    },
                ),
                "composed": await client.call_tool(
                    "compose_webcam_picture_in_picture",
                    {
                        "synchronized_pair_receipt_id": "a" * 64,
                        "size_percent": 25,
                        "center_x_percent": 82,
                        "center_y_percent": 82,
                        "confirm_layout": True,
                    },
                ),
                "screen_linked": await client.call_tool(
                    "link_synchronized_screen_pair",
                    {
                        "synchronized_pair_receipt_id": "a" * 64,
                        "confirm_link": True,
                    },
                ),
                "recipe_list": await client.call_tool(
                    "list_editing_recipes",
                    {},
                ),
                "recipe_detail": await client.call_tool(
                    "get_editing_recipe",
                    {"recipe_id": "tutorial-layout-v1"},
                ),
                "recipe_preview": await client.call_tool(
                    "preview_editing_recipe",
                    {
                        "recipe_id": "tutorial-layout-v1",
                        "inputs": {"synchronized_pair_receipt_id": "a" * 64},
                    },
                ),
                "recipe_run": await client.call_tool(
                    "run_editing_recipe",
                    {
                        "recipe_id": "tutorial-layout-v1",
                        "inputs": {"synchronized_pair_receipt_id": "a" * 64},
                        "confirm_execute": True,
                    },
                ),
                "baseline_preview": await client.call_tool(
                    "preview_baseline_edit",
                    {
                        "finalization_receipt_id": "a" * 64,
                        "audio_integration_receipt_id": "b" * 64,
                    },
                ),
                "baseline_started": await client.call_tool(
                    "start_baseline_render",
                    {
                        "finalization_receipt_id": "a" * 64,
                        "audio_integration_receipt_id": "b" * 64,
                        "custom_name": "M50 Baseline",
                        "confirm_render": True,
                    },
                ),
                "baseline_status": await client.call_tool(
                    "get_baseline_render_status",
                    {"receipt_id": "f" * 64},
                ),
                "broll_preview": await client.call_tool(
                    "preview_broll_plan",
                    {
                        "baseline_edit_receipt_id": "a" * 64,
                        "target_timeline_name": "M51 B-roll",
                        "placements": [_broll_placement()],
                    },
                ),
                "broll_applied": await client.call_tool(
                    "apply_broll_plan",
                    {
                        "baseline_edit_receipt_id": "a" * 64,
                        "target_timeline_name": "M51 B-roll",
                        "placements": [_broll_placement()],
                        "expected_plan_id": "e" * 64,
                        "confirm_apply": True,
                    },
                ),
                "broll_status": await client.call_tool(
                    "get_broll_status",
                    {"receipt_id": "f" * 64},
                ),
                "animation_templates": await client.call_tool(
                    "list_animation_templates",
                    {},
                ),
                "animation_template": await client.call_tool(
                    "get_animation_template",
                    {"template_id": "accent-card-v1"},
                ),
                "animation_preview": await client.call_tool(
                    "preview_animation_template",
                    {
                        "broll_receipt_id": "f" * 64,
                        "target_timeline_name": "M52 Animation",
                        "template_id": "accent-card-v1",
                        "timecode": "01:00:10:00",
                    },
                ),
                "animation_applied": await client.call_tool(
                    "apply_animation_template",
                    {
                        "broll_receipt_id": "f" * 64,
                        "target_timeline_name": "M52 Animation",
                        "template_id": "accent-card-v1",
                        "timecode": "01:00:10:00",
                        "expected_plan_id": "d" * 64,
                        "confirm_apply": True,
                    },
                ),
                "compaction": await client.call_tool(
                    "preview_synchronized_pause_compaction",
                    {
                        "plan_id": "a" * 64,
                        "synchronized_pair_receipt_id": "b" * 64,
                        "target_timeline_name": "M41 Preview",
                    },
                ),
                "compaction_apply": await client.call_tool(
                    "apply_synchronized_pause_compaction",
                    {
                        "plan_id": "a" * 64,
                        "synchronized_pair_receipt_id": "b" * 64,
                        "target_timeline_name": "M42 Apply",
                        "confirm_apply": True,
                    },
                ),
                "compaction_finalized": await client.call_tool(
                    "finalize_synchronized_pause_compaction",
                    {
                        "pause_compaction_receipt_id": "a" * 64,
                        "picture_in_picture_receipt_id": "b" * 64,
                        "synchronized_link_receipt_id": "c" * 64,
                        "confirm_finalize": True,
                    },
                ),
                "finalized_render": await client.call_tool(
                    "prepare_finalized_timeline_render",
                    {
                        "finalization_receipt_id": "d" * 64,
                        "custom_name": "M44 Final",
                        "profile": "youtube-1080p-h264-v1",
                        "confirm_prepare": True,
                    },
                ),
                "finalized_started": await client.call_tool(
                    "start_finalized_timeline_render",
                    {
                        "preparation_receipt_id": "e" * 64,
                        "confirm_render": True,
                    },
                ),
                "finalized_status": await client.call_tool(
                    "get_finalized_timeline_render_status",
                    {"execution_receipt_id": "f" * 64},
                ),
                "audio_prepared": await client.call_tool(
                    "prepare_finalized_timeline_audio",
                    {
                        "finalization_receipt_id": "d" * 64,
                        "custom_name": "M46 Dialogue Source",
                        "confirm_prepare": True,
                    },
                ),
                "audio_started": await client.call_tool(
                    "start_finalized_timeline_audio",
                    {
                        "extraction_receipt_id": "1" * 64,
                        "confirm_render": True,
                    },
                ),
                "audio_status": await client.call_tool(
                    "get_finalized_timeline_audio_status",
                    {"extraction_receipt_id": "1" * 64},
                ),
                "audio_applied": await client.call_tool(
                    "apply_finalized_timeline_audio",
                    {
                        "extraction_receipt_id": "1" * 64,
                        "audio_report_id": "2" * 64,
                        "confirm_apply": True,
                    },
                ),
                "audio": await client.call_tool(
                    "clean_dialogue_audio",
                    {"source_file": "dialogue.wav"},
                ),
                "audio_detail": await client.call_tool(
                    "get_audio_report",
                    {"report_id": "b" * 64},
                ),
                "audio_reports": await client.call_tool(
                    "list_audio_reports",
                    {"limit": 10},
                ),
            }
        return results, names

    results, names = asyncio.run(exercise_server())

    assert names == {
        "video_agent_capabilities",
        "video_agent_status",
        "resolve_get_project",
        "resolve_stop_bridge",
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
        "resolve_create_project",
        "resolve_close_project",
        "resolve_create_timeline",
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
    assert results["project"].structured_content == {
        "project": {"name": "Test Project"}
    }
    assert results["stopped"].structured_content == {"status": "stopping"}
    assert results["timelines"].structured_content == {
        "timelines": [{"index": 1, "name": "Main"}]
    }
    assert results["timeline"].structured_content == {
        "timeline": {"name": "Main"}
    }
    assert results["items"].structured_content["items"][0][
        "timeline_item_id"
    ] == "item-1"
    assert results["media_pool_items"].structured_content["items"][0][
        "asset_id"
    ] == "asset-1"
    assert results["editing_metadata"].structured_content["assets"][0][
        "duration_frames"
    ] == 240
    assert results["editing_metadata"].structured_content["timeline"][
        "frame_rate"
    ] == 60.0
    assert results["subtitles"].structured_content["subtitle_track_count"] == 0
    assert results["animation_environment"].structured_content["ready"] is True
    assert results["created_subtitles"].structured_content[
        "subtitle_environment"
    ]["subtitle_item_count"] == 1
    assert results["generated_subtitles"].structured_content["status"] == (
        "applied"
    )
    assert results["subtitles"].structured_content["auto_caption"][
        "method_available"
    ] is True
    assert results["snapshot"].structured_content["project"]["name"] == (
        "Test Project"
    )
    assert results["render"].structured_content["current"]["format"] == "mp4"
    assert results["status"].structured_content["healthy"] is True
    assert results["imported"].structured_content["items"][0]["asset_id"] == (
        "asset-1"
    )
    assert results["created"].structured_content["timeline"]["timeline_id"] == (
        "timeline-1"
    )
    assert results["tracks"].structured_content["after"] == {
        "video_track_count": 2,
        "audio_track_count": 1,
    }
    assert results["duplicated"].structured_content["timeline"]["timeline_id"] == (
        "timeline-2"
    )
    assert results["selected"].structured_content["timeline"]["timeline_id"] == (
        "timeline-1"
    )
    assert results["appended"].structured_content["asset_id"] == "asset-1"
    assert results["inserted"].structured_content["item"][
        "source_end_frame"
    ] == 240
    assert results["animation_inserted"].structured_content[
        "template_id"
    ] == "accent-card-v1"
    assert results["disabled"].structured_content["enabled"] is False
    assert results["linked"].structured_content["linked"] is True
    assert results["transformed"].structured_content["transform"]["zoom"] == 0.5
    assert results["deleted"].structured_content["deleted"] is True
    assert results["marker"].structured_content["color"] == "Green"
    assert results["prepared"].structured_content["started"] is False
    assert results["prepared"].structured_content["preset"] == (
        "youtube-2160p-h264-v1"
    )
    assert results["render_status"].structured_content["status"][
        "JobStatus"
    ] == "Ready"
    assert results["render_start"].structured_content["started"] is True
    assert results["rough_cut"].structured_content["status"] == "pending_review"
    assert results["approval"].structured_content["status"] == "approved"
    assert results["approval"].structured_content["apply_supported"] is False
    assert results["rough_cut_detail"].structured_content[
        "effective_status"
    ] == "pending_review"
    assert results["rough_cut_plans"].structured_content["count"] == 1
    assert results["synced"].structured_content["status"] == "applied"
    assert results["composed"].structured_content["transform"]["zoom"] == 0.25
    assert results["screen_linked"].structured_content["status"] == "applied"
    assert results["recipe_list"].structured_content["count"] == 1
    assert results["recipe_detail"].structured_content["recipe_version"] == "1.0"
    assert results["recipe_preview"].structured_content["status"] == "ready"
    assert results["recipe_run"].structured_content["status"] == "applied"
    assert results["baseline_preview"].structured_content["status"] == "ready"
    assert results["baseline_started"].structured_content["status"] == "started"
    assert results["baseline_status"].structured_content["status"] == "complete"
    assert results["broll_preview"].structured_content["status"] == "preview"
    assert results["broll_applied"].structured_content["status"] == "applied"
    assert results["broll_status"].structured_content["live"] is True
    assert results["animation_templates"].structured_content["count"] == 1
    assert results["animation_template"].structured_content[
        "template_version"
    ] == "1.0"
    assert results["animation_preview"].structured_content["status"] == "preview"
    assert results["animation_applied"].structured_content["status"] == "applied"
    assert results["compaction"].structured_content["status"] == "preview"
    assert results["compaction_apply"].structured_content["status"] == "applied"
    assert results["compaction_finalized"].structured_content["status"] == (
        "applied"
    )
    assert results["finalized_render"].structured_content["status"] == "applied"
    assert results["finalized_started"].structured_content["status"] == "started"
    assert results["finalized_status"].structured_content["output"][
        "validation"
    ]["passed"] is True
    assert results["audio_prepared"].structured_content["status"] == "prepared"
    assert results["audio_started"].structured_content["status"] == "started"
    assert results["audio_status"].structured_content["output"]["validation"][
        "passed"
    ] is True
    assert results["audio_applied"].structured_content["status"] == "applied"
    assert results["audio"].structured_content["status"] == "completed"
    assert results["audio_detail"].structured_content["status"] == "completed"
    assert results["audio_reports"].structured_content["count"] == 1
    assert workflow_audit.operations == [
        "create_rough_cut",
        "approve_rough_cut",
        "get_rough_cut_plan",
        "list_rough_cut_plans",
        "sync_screen_and_webcam",
        "compose_webcam_picture_in_picture",
        "link_synchronized_screen_pair",
        "list_editing_recipes",
        "get_editing_recipe",
        "preview_editing_recipe",
        "run_editing_recipe",
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
    ]
