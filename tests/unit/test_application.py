"""Application-service boundary tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent.application import AgentApplication


class StubResolveReader:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def current_project(self, timeout_seconds: float = 30) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
        return {"name": "Test Project"}

    def stop_bridge(self, timeout_seconds: float = 30) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
        return {"status": "stopping"}

    def timelines(self, timeout_seconds: float = 30) -> list[dict[str, Any]]:
        self.timeouts.append(timeout_seconds)
        return [{"index": 1, "name": "Main"}]

    def current_timeline(self, timeout_seconds: float = 30) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
        return {"name": "Main"}

    def timeline_items(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
        return {
            "timeline_id": timeline_id,
            "name": "Main",
            "items": [{"timeline_item_id": "item-1"}],
        }

    def media_pool_items(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
        return {
            "timeline": {"timeline_id": timeline_id, "name": "Main"},
            "methods": {"InsertFusionTitleIntoTimeline": True},
            "templates": [{"template_id": "accent-card-v1"}],
            "ready": True,
        }

    def color_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
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
            "items": [{"timeline_item_id": item_id} for item_id in timeline_item_ids],
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
        assert idempotency_key == "stable-key"
        self.timeouts.append(timeout_seconds)
        return {
            "timeline_id": timeline_id,
            "subtitle_environment": {"subtitle_item_count": 1},
        }

    def workspace_snapshot(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
        return {
            "project": {"name": "Test Project"},
            "timelines": [{"index": 1, "name": "Main"}],
        }

    def render_environment(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        assert confirm_insert is True
        self.timeouts.append(timeout_seconds)
        return {
            "timeline_id": timeline_id,
            "title_name": title_name,
            "requested_timecode": timecode,
            "item": {"timeline_item_id": "title-1"},
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
        self.timeouts.append(timeout_seconds)
        return {
            "timeline_id": timeline_id,
            "template_id": template_id,
            "requested_timecode": timecode,
            "item": {"timeline_item_id": "animation-1"},
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
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
        self.timeouts.append(timeout_seconds)
        return {
            "job_id": job_id,
            "rendering_in_progress": False,
            "status": {
                "JobStatus": "Ready",
                "CompletionPercentage": 0,
            },
            "job": {
                "TargetDir": "",
                "OutputFilename": "M11 Test.mp4",
            },
        }

    def start_render_job(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
        return {"job_id": job_id, "started": True}


class StubCompletedResolveReader(StubResolveReader):
    def __init__(self, output_directory: Path) -> None:
        super().__init__()
        self.output_directory = output_directory

    def render_job_status(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.timeouts.append(timeout_seconds)
        return {
            "job_id": job_id,
            "rendering_in_progress": False,
            "status": {
                "JobStatus": "Complete",
                "CompletionPercentage": 100,
            },
            "job": {
                "TargetDir": str(self.output_directory),
                "OutputFilename": "M11 Test.mp4",
            },
        }


class StubMediaPolicy:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def prepare_import(self, paths: list[str]) -> list[str]:
        self.paths = paths
        return [f"normalized:{path}" for path in paths]

    def validate_files(self, paths: list[str]) -> list[str]:
        self.paths = paths
        return [f"normalized:{path}" for path in paths]


class StubRoughCutPlanner:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

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
        self.arguments = {
            "screen_file": screen_file,
            "webcam_file": webcam_file,
            "screen_audio_file": screen_audio_file,
            "webcam_audio_file": webcam_audio_file,
            "speech_audio_file": speech_audio_file,
            "timeline_name": timeline_name,
            "max_sync_offset_ms": max_sync_offset_ms,
            "pause_threshold_dbfs": pause_threshold_dbfs,
            "min_pause_duration_ms": min_pause_duration_ms,
            "preserve_context_ms": preserve_context_ms,
        }
        return {
            "status": "pending_review",
            "review": {"apply_supported": False},
        }


class StubRoughCutReviewer:
    def __init__(self) -> None:
        self.plan_id = ""
        self.confirm_review = False

    def approve(
        self,
        plan_id: str,
        *,
        confirm_review: bool,
    ) -> dict[str, Any]:
        self.plan_id = plan_id
        self.confirm_review = confirm_review
        return {
            "plan_id": plan_id,
            "status": "approved",
            "apply_supported": False,
        }


class StubRoughCutInspector:
    def get_plan(self, plan_id: str) -> dict[str, Any]:
        return {
            "plan": {"plan_id": plan_id},
            "approval": None,
            "effective_status": "pending_review",
        }

    def list_plans(self, limit: int = 100) -> dict[str, Any]:
        return {
            "plans": [{"plan_id": "a" * 64}],
            "count": 1,
            "truncated": limit < 1,
        }


class StubSynchronizedPairAssembler:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

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
        self.arguments = {
            "timeline_name": timeline_name,
            "screen_asset_id": screen_asset_id,
            "webcam_asset_id": webcam_asset_id,
            "webcam_offset_ms": webcam_offset_ms,
            "confirm_sync": confirm_sync,
            "timeout_seconds": timeout_seconds,
        }
        return {"status": "applied", "timeline": {"name": timeline_name}}


class StubPictureInPictureComposer:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

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
        self.arguments = {
            "synchronized_pair_receipt_id": synchronized_pair_receipt_id,
            "size_percent": size_percent,
            "center_x_percent": center_x_percent,
            "center_y_percent": center_y_percent,
            "confirm_layout": confirm_layout,
            "timeout_seconds": timeout_seconds,
        }
        return {"status": "applied", "transform": {"zoom": 0.25}}


class StubSynchronizedScreenLinker:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    def link(
        self,
        *,
        synchronized_pair_receipt_id: str,
        confirm_link: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.arguments = {
            "synchronized_pair_receipt_id": synchronized_pair_receipt_id,
            "confirm_link": confirm_link,
            "timeout_seconds": timeout_seconds,
        }
        return {"status": "applied", "operation": {"status": "applied"}}


class StubPauseCompactionPreviewer:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    def preview(
        self,
        *,
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.arguments = {
            "plan_id": plan_id,
            "synchronized_pair_receipt_id": synchronized_pair_receipt_id,
            "target_timeline_name": target_timeline_name,
            "timeout_seconds": timeout_seconds,
        }
        return {"status": "preview", "apply_supported": False}


class StubPauseCompactionApplier:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    def apply(
        self,
        *,
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.arguments = {
            "plan_id": plan_id,
            "synchronized_pair_receipt_id": synchronized_pair_receipt_id,
            "target_timeline_name": target_timeline_name,
            "confirm_apply": confirm_apply,
            "timeout_seconds": timeout_seconds,
        }
        return {"status": "applied", "placement_count": 6}


class StubPauseCompactionFinalizer:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    def finalize(
        self,
        *,
        pause_compaction_receipt_id: str,
        picture_in_picture_receipt_id: str,
        synchronized_link_receipt_id: str,
        confirm_finalize: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.arguments = {
            "pause_compaction_receipt_id": pause_compaction_receipt_id,
            "picture_in_picture_receipt_id": picture_in_picture_receipt_id,
            "synchronized_link_receipt_id": synchronized_link_receipt_id,
            "confirm_finalize": confirm_finalize,
            "timeout_seconds": timeout_seconds,
        }
        return {"status": "applied", "link_groups": [["v", "a"]]}


class StubFinalizedRenderPreparer:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    def prepare(
        self,
        *,
        finalization_receipt_id: str,
        custom_name: str,
        profile: str,
        confirm_prepare: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.arguments = {
            "finalization_receipt_id": finalization_receipt_id,
            "custom_name": custom_name,
            "profile": profile,
            "confirm_prepare": confirm_prepare,
            "timeout_seconds": timeout_seconds,
        }
        return {"status": "applied", "operation": {"result": {"job_id": "j"}}}


class StubFinalizedRenderExecutor:
    def __init__(self) -> None:
        self.start_arguments: dict[str, Any] = {}
        self.status_arguments: dict[str, Any] = {}

    def start(
        self,
        *,
        preparation_receipt_id: str,
        confirm_render: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.start_arguments = {
            "preparation_receipt_id": preparation_receipt_id,
            "confirm_render": confirm_render,
            "timeout_seconds": timeout_seconds,
        }
        return {"status": "started", "receipt_id": "b" * 64}

    def status(
        self,
        execution_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.status_arguments = {
            "execution_receipt_id": execution_receipt_id,
            "timeout_seconds": timeout_seconds,
        }
        return {"output": {"validation": {"passed": True}}}


class StubFinalizedAudioExtractor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def prepare(
        self,
        *,
        finalization_receipt_id: str,
        custom_name: str,
        confirm_prepare: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "prepare",
                {
                    "finalization_receipt_id": finalization_receipt_id,
                    "custom_name": custom_name,
                    "confirm_prepare": confirm_prepare,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )
        return {"status": "prepared", "receipt_id": "c" * 64}

    def start(
        self,
        extraction_receipt_id: str,
        *,
        confirm_render: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "start",
                {
                    "extraction_receipt_id": extraction_receipt_id,
                    "confirm_render": confirm_render,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )
        return {"status": "started", "receipt_id": extraction_receipt_id}

    def status(
        self,
        extraction_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "status",
                {
                    "extraction_receipt_id": extraction_receipt_id,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )
        return {"output": {"validation": {"passed": True}}}


class StubAudioProcessor:
    def __init__(self) -> None:
        self.source_file = ""
        self.preset = ""

    def process(
        self,
        source_file: str,
        *,
        preset: str = "pcm-dialogue-level-v1",
    ) -> dict[str, Any]:
        self.source_file = source_file
        self.preset = preset
        return {
            "status": "completed",
            "source": {"preserved": True},
            "validation": {"target_met": True},
        }


class StubAudioReportInspector:
    def get_report(self, report_id: str) -> dict[str, Any]:
        return {"report_id": report_id, "status": "completed"}

    def list_reports(self, limit: int = 100) -> dict[str, Any]:
        return {
            "reports": [{"report_id": "b" * 64}],
            "count": 1,
            "truncated": limit < 1,
        }


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
        return {
            "recipe_id": recipe_id,
            "inputs": inputs,
            "status": "applied",
            "timeout_seconds": timeout_seconds,
        }


class StubBaselineEditWorkflow:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ready", "arguments": kwargs}

    def start(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "started",
            "receipt_id": "f" * 64,
            "arguments": kwargs,
        }

    def status(self, receipt_id: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "complete",
            "receipt_id": receipt_id,
            "arguments": kwargs,
        }


class StubBrollWorkflow:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "preview", "plan_id": "e" * 64, "arguments": kwargs}

    def apply(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "applied", "receipt_id": "f" * 64, "arguments": kwargs}

    def status(self, receipt_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "applied", "receipt_id": receipt_id, "arguments": kwargs}


class StubAnimationTemplateWorkflow:
    def list_templates(self) -> dict[str, Any]:
        return {"templates": [{"template_id": "accent-card-v1"}], "count": 1}

    def get_template(self, template_id: str) -> dict[str, Any]:
        return {"template_id": template_id, "template_version": "1.0"}

    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "preview", "plan_id": "d" * 64, "arguments": kwargs}

    def apply(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "applied", "receipt_id": "c" * 64, "arguments": kwargs}


class StubTakeSelectionWorkflow:
    def analyze(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "pending_review", "arguments": kwargs}

    def analyze_scripted(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "pending_review",
            "selection_version": "1.2",
            "arguments": kwargs,
        }

    def get(self, selection_id: str) -> dict[str, Any]:
        return {"selection_id": selection_id, "status": "pending_review"}

    def list(self, limit: int = 20) -> dict[str, Any]:
        return {"selections": [], "count": 0, "limit": limit}

    def review(self, **kwargs: Any) -> dict[str, Any]:
        return {"timeline_modified": False, "arguments": kwargs}

    def get_review(self, selection_id: str) -> dict[str, Any]:
        return {
            "selection_id": selection_id,
            "review_id": "b" * 64,
            "decision": "approve",
        }


class StubTakeSequenceWorkflow:
    def compose(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "approved_plan",
            "sequence_id": "c" * 64,
            "arguments": kwargs,
        }

    def get(self, sequence_id: str) -> dict[str, Any]:
        return {"sequence_id": sequence_id, "status": "approved_plan"}

    def list(self, limit: int = 20) -> dict[str, Any]:
        return {"sequences": [], "count": 0, "limit": limit}


class StubTakeSequenceBindingWorkflow:
    def bind(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "bound",
            "binding_id": "d" * 64,
            "arguments": kwargs,
        }

    def get(self, binding_id: str) -> dict[str, Any]:
        return {"binding_id": binding_id, "status": "bound"}

    def resolve_sources(self, binding_id: str) -> list[dict[str, Any]]:
        return [{"binding_id": binding_id}]


class StubTakeSequenceAssemblyWorkflow:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "preview",
            "plan_id": "e" * 64,
            "arguments": kwargs,
        }


class StubTakeSequenceTimelineMappingWorkflow:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "preview",
            "plan_id": "f" * 64,
            "arguments": kwargs,
        }


class StubTakeSequenceMediaImportWorkflow:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "preview",
            "plan_id": "0" * 64,
            "arguments": kwargs,
        }


class StubTakeSequenceMediaImportApplyWorkflow:
    def apply(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "applied",
            "receipt_id": "9" * 64,
            "arguments": kwargs,
        }

    def get(self, receipt_id: str) -> dict[str, Any]:
        return {"status": "applied", "receipt_id": receipt_id}


class StubTakeSequenceTimelineApplyWorkflow:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "preview", "plan_id": "8" * 64, "arguments": kwargs}

    def apply(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "applied", "receipt_id": "7" * 64, "arguments": kwargs}

    def get(self, receipt_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "applied", "receipt_id": receipt_id, "arguments": kwargs}


class StubTakeSequenceQcWorkflow:
    def inspect(
        self,
        timeline_apply_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "ready_for_review",
            "receipt_id": timeline_apply_receipt_id,
            "arguments": {"timeout_seconds": timeout_seconds},
        }

    def get(
        self,
        report_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "status": "ready_for_review",
            "report_id": report_id,
            "arguments": {"timeout_seconds": timeout_seconds},
        }

    def review(
        self,
        *,
        report_id: str,
        decision: str,
        note: str = "",
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "decision": decision,
            "timeline_modified": False,
            "arguments": {
                "report_id": report_id,
                "note": note,
                "timeout_seconds": timeout_seconds,
            },
        }

    def get_review(
        self,
        report_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        return {
            "decision": "approve",
            "report_id": report_id,
            "arguments": {"timeout_seconds": timeout_seconds},
        }


class StubTakeSequenceRenderWorkflow:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "preview", "plan_id": "5" * 64, "arguments": kwargs}

    def start(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "started", "receipt_id": "4" * 64, "arguments": kwargs}

    def status(self, receipt_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "complete", "receipt_id": receipt_id, "arguments": kwargs}


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


def test_application_exposes_take_analysis_and_review_boundary() -> None:
    application = AgentApplication(
        resolve=StubResolveReader(),
        take_selection_service=StubTakeSelectionWorkflow(),
        take_sequence_service=StubTakeSequenceWorkflow(),
        take_sequence_binding_service=StubTakeSequenceBindingWorkflow(),
        take_sequence_assembly_service=StubTakeSequenceAssemblyWorkflow(),
        take_sequence_timeline_mapping_service=(
            StubTakeSequenceTimelineMappingWorkflow()
        ),
        take_sequence_media_import_service=StubTakeSequenceMediaImportWorkflow(),
        take_sequence_media_import_apply_service=(
            StubTakeSequenceMediaImportApplyWorkflow()
        ),
        take_sequence_timeline_apply_service=(StubTakeSequenceTimelineApplyWorkflow()),
        take_sequence_qc_service=StubTakeSequenceQcWorkflow(),
        take_sequence_render_service=StubTakeSequenceRenderWorkflow(),
    )
    candidates: list[dict[str, str | float]] = [
        {"candidate_id": "take-a", "path": "first.mkv"},
        {"candidate_id": "take-b", "path": "second.mkv"},
    ]

    analyzed = application.analyze_take_candidates(
        selection_name="Intro",
        candidates=candidates,
    )
    scripted_candidates: list[dict[str, str | float]] = [
        {
            "candidate_id": "take-a",
            "path": "first.mkv",
            "start_seconds": 0.0,
            "end_seconds": 10.0,
        },
        {
            "candidate_id": "take-b",
            "path": "second.mkv",
            "start_seconds": 0.0,
            "end_seconds": 10.0,
        },
    ]
    scripted = application.analyze_scripted_take_candidates(
        selection_name="Intro script",
        candidates=scripted_candidates,
        reference_text="РћС‡С–РєСѓРІР°РЅРёР№ С‚РµРєСЃС‚",
    )
    detail = application.get_take_selection("a" * 64)
    listed = application.list_take_selections(5)
    reviewed = application.review_take_selection(
        selection_id="a" * 64,
        decision="approve",
        selected_candidate_id="take-b",
        note="Use take B.",
    )
    review = application.get_take_selection_review("a" * 64)
    sequence = application.compose_take_sequence(
        sequence_name="Approved intro",
        selection_ids=["a" * 64],
    )
    sequence_detail = application.get_take_sequence("c" * 64)
    sequences = application.list_take_sequences(5)
    binding = application.bind_take_sequence_sources(
        sequence_id="c" * 64,
        sources=[{"order": 1, "path": "first.mkv"}],
    )
    binding_detail = application.get_take_sequence_binding("d" * 64)
    assembly = application.preview_take_sequence_assembly(
        binding_id="d" * 64,
        assembly_name="Approved assembly",
    )
    mapping = application.preview_take_sequence_timeline_mapping(
        binding_id="d" * 64,
        assembly_name="Approved assembly",
        timeline_id="timeline-1",
        timeout_seconds=12.5,
    )
    import_preview = application.preview_take_sequence_media_import(
        binding_id="d" * 64,
        assembly_name="Approved assembly",
        timeline_id="timeline-1",
        timeout_seconds=13.5,
    )
    import_result = application.apply_take_sequence_media_import(
        binding_id="d" * 64,
        assembly_name="Approved assembly",
        timeline_id="timeline-1",
        expected_plan_id="0" * 64,
        confirm_import=True,
        timeout_seconds=14.5,
    )
    import_receipt = application.get_take_sequence_media_import("9" * 64)
    timeline_apply_preview = application.preview_take_sequence_timeline_apply(
        media_import_receipt_id="9" * 64,
        target_timeline_name="Sequence Draft",
        timeout_seconds=15.5,
    )
    timeline_apply = application.apply_take_sequence_timeline(
        media_import_receipt_id="9" * 64,
        target_timeline_name="Sequence Draft",
        expected_plan_id="8" * 64,
        confirm_apply=True,
        timeout_seconds=16.5,
    )
    timeline_apply_receipt = application.get_take_sequence_timeline_apply(
        "7" * 64,
        timeout_seconds=17.5,
    )
    qc = application.inspect_take_sequence_qc("7" * 64, timeout_seconds=18.5)
    qc_detail = application.get_take_sequence_qc("6" * 64, timeout_seconds=19.5)
    qc_review = application.review_take_sequence_qc(
        report_id="6" * 64,
        decision="approve",
        note="Reviewed.",
        timeout_seconds=20.5,
    )
    qc_review_detail = application.get_take_sequence_qc_review(
        "6" * 64,
        timeout_seconds=21.5,
    )
    render_preview = application.preview_take_sequence_render(
        qc_report_id="6" * 64,
        custom_name="Approved sequence",
        profile="youtube-2160p-h264-v1",
        timeout_seconds=22.5,
    )
    render_started = application.start_take_sequence_render(
        qc_report_id="6" * 64,
        custom_name="Approved sequence",
        profile="youtube-2160p-h264-v1",
        expected_plan_id="5" * 64,
        confirm_render=True,
        timeout_seconds=23.5,
    )
    render_status = application.get_take_sequence_render_status(
        "4" * 64,
        timeout_seconds=24.5,
    )

    assert analyzed["status"] == "pending_review"
    assert scripted["selection_version"] == "1.2"
    assert detail["selection_id"] == "a" * 64
    assert listed["limit"] == 5
    assert reviewed["timeline_modified"] is False
    assert review["decision"] == "approve"
    assert sequence["status"] == "approved_plan"
    assert sequence_detail["sequence_id"] == "c" * 64
    assert sequences["limit"] == 5
    assert binding["status"] == "bound"
    assert binding_detail["binding_id"] == "d" * 64
    assert assembly["status"] == "preview"
    assert assembly["arguments"]["binding_id"] == "d" * 64
    assert mapping["status"] == "preview"
    assert mapping["arguments"]["timeline_id"] == "timeline-1"
    assert mapping["arguments"]["timeout_seconds"] == 12.5
    assert import_preview["status"] == "preview"
    assert import_preview["arguments"]["timeline_id"] == "timeline-1"
    assert import_preview["arguments"]["timeout_seconds"] == 13.5
    assert import_result["status"] == "applied"
    assert import_result["arguments"]["confirm_import"] is True
    assert import_result["arguments"]["timeout_seconds"] == 14.5
    assert import_receipt["receipt_id"] == "9" * 64
    assert timeline_apply_preview["arguments"]["timeout_seconds"] == 15.5
    assert timeline_apply["arguments"]["confirm_apply"] is True
    assert timeline_apply["arguments"]["timeout_seconds"] == 16.5
    assert timeline_apply_receipt["arguments"]["timeout_seconds"] == 17.5
    assert qc["arguments"]["timeout_seconds"] == 18.5
    assert qc_detail["arguments"]["timeout_seconds"] == 19.5
    assert qc_review["decision"] == "approve"
    assert qc_review["arguments"]["timeout_seconds"] == 20.5
    assert qc_review_detail["arguments"]["timeout_seconds"] == 21.5
    assert render_preview["arguments"]["profile"] == "youtube-2160p-h264-v1"
    assert render_preview["arguments"]["timeout_seconds"] == 22.5
    assert render_started["arguments"]["confirm_render"] is True
    assert render_started["arguments"]["timeout_seconds"] == 23.5
    assert render_status["status"] == "complete"
    assert render_status["arguments"]["timeout_seconds"] == 24.5


def test_application_exposes_status_and_read_only_provider_methods() -> None:
    resolve = StubResolveReader()
    application = AgentApplication(resolve=resolve, state_loader=_fresh_state)

    status = application.status()

    assert status["healthy"] is True
    assert status["bridge"]["status"] == "ready"
    assert application.resolve_get_project(10) == {"name": "Test Project"}
    assert application.resolve_stop_bridge(12) == {"status": "stopping"}
    assert application.resolve_list_timelines(20) == [{"index": 1, "name": "Main"}]
    assert application.resolve_get_timeline(30) == {"name": "Main"}
    assert (
        application.resolve_list_timeline_items(
            "timeline-1",
            35,
        )["items"][0]["timeline_item_id"]
        == "item-1"
    )
    assert (
        application.resolve_list_media_pool_items(37)["items"][0]["asset_id"]
        == "asset-1"
    )
    editing_metadata = application.resolve_get_editing_metadata(
        "timeline-1", ["asset-1"], 37.5
    )
    assert editing_metadata["assets"][0]["frame_rate"] == 60.0
    assert editing_metadata["timeline"]["frame_rate"] == 60.0
    timeline_only_metadata = application.resolve_get_editing_metadata(
        "timeline-1", [], 37.5
    )
    assert timeline_only_metadata["assets"] == []
    assert timeline_only_metadata["timeline"]["frame_rate"] == 60.0
    subtitle_environment = application.resolve_get_subtitle_environment(
        "timeline-1", 37.75
    )
    assert subtitle_environment["subtitle_track_count"] == 0
    assert subtitle_environment["auto_caption"]["method_available"] is True
    assert application.resolve_get_workspace_snapshot(38)["project"]["name"] == (
        "Test Project"
    )
    assert application.resolve_get_render_options(40)["current"] == {
        "format": "mp4",
        "codec": "H264",
    }
    assert resolve.timeouts == [
        10,
        12,
        20,
        30,
        35,
        37,
        37.5,
        37.5,
        37.75,
        38,
        40,
    ]


def test_application_exposes_validated_write_methods() -> None:
    resolve = StubResolveReader()
    media_policy = StubMediaPolicy()
    application = AgentApplication(
        resolve=resolve,
        media_policy=media_policy,
    )

    imported = application.resolve_import_media(
        ["sample.wav"],
        timeout_seconds=10,
    )
    created = application.resolve_create_timeline(
        "M4 Timeline",
        timeout_seconds=20,
    )
    subtitles = application.resolve_create_subtitles_from_audio(
        "timeline-1",
        confirm_create=True,
        timeout_seconds=25,
        idempotency_key="stable-key",
    )
    assert subtitles["subtitle_environment"]["subtitle_item_count"] == 1
    tracks = application.resolve_ensure_timeline_tracks(
        "timeline-1",
        2,
        1,
        timeout_seconds=21,
    )
    duplicated = application.resolve_duplicate_timeline(
        "timeline-1",
        "Agent Draft",
        timeout_seconds=22,
    )
    selected = application.resolve_set_current_timeline(
        "timeline-1",
        timeout_seconds=25,
    )
    appended = application.resolve_append_clip(
        "timeline-1",
        "asset-1",
        timeout_seconds=30,
    )
    title = application.resolve_insert_title(
        "timeline-1",
        "Text",
        "01:00:05:00",
        confirm_insert=True,
        timeout_seconds=32,
    )
    inserted = application.resolve_insert_clip(
        "timeline-1",
        "asset-1",
        0,
        240,
        0,
        "video",
        1,
        timeout_seconds=35,
    )
    disabled = application.resolve_set_clip_enabled(
        "timeline-1",
        "item-1",
        False,
        timeout_seconds=37,
    )
    linked = application.resolve_set_clips_linked(
        "timeline-1",
        ["video-1", "audio-1"],
        True,
        timeout_seconds=37.5,
    )
    transformed = application.resolve_set_clip_transform(
        "timeline-1",
        "item-1",
        position_x=320.0,
        zoom=0.5,
        timeout_seconds=38,
    )
    deleted = application.resolve_delete_clip(
        "timeline-1",
        "item-1",
        confirm_delete=True,
        timeout_seconds=39,
    )
    marker = application.resolve_add_marker(
        "timeline-1",
        0,
        "Green",
        timeout_seconds=40,
    )
    render_job = application.resolve_prepare_render_job(
        "M7 Test",
        profile="youtube-2160p-h264-v1",
        timeout_seconds=50,
    )
    render_status = application.resolve_get_render_job_status(
        "job-1",
        timeout_seconds=60,
    )
    render_start = application.resolve_start_render_job(
        "job-1",
        timeout_seconds=70,
    )

    assert media_policy.paths == ["sample.wav"]
    assert imported["items"][0]["name"] == "normalized:sample.wav"
    assert created["timeline"]["name"] == "M4 Timeline"
    assert tracks["after"] == {
        "video_track_count": 2,
        "audio_track_count": 1,
    }
    assert duplicated["timeline"]["name"] == "Agent Draft"
    assert selected["timeline"]["timeline_id"] == "timeline-1"
    assert appended["asset_id"] == "asset-1"
    assert title["item"]["timeline_item_id"] == "title-1"
    assert inserted["item"]["source_end_frame"] == 240
    assert inserted["item"]["track_type"] == "video"
    assert disabled["timeline_item_id"] == "item-1"
    assert disabled["enabled"] is False
    assert linked["linked"] is True
    assert transformed["transform"]["position_x"] == 320.0
    assert transformed["transform"]["zoom"] == 0.5
    assert deleted["deleted"] is True
    assert deleted["ripple"] is False
    assert marker["color"] == "Green"
    assert render_job["started"] is False
    assert render_job["custom_name"] == "M7 Test"
    assert render_job["preset"] == "youtube-2160p-h264-v1"
    assert render_status["status"]["JobStatus"] == "Ready"
    assert render_start["started"] is True
    assert resolve.timeouts == [
        10,
        20,
        25,
        21,
        22,
        25,
        30,
        32,
        35,
        37,
        37.5,
        38,
        39,
        40,
        50,
        60,
        70,
    ]


def test_application_requires_explicit_delete_confirmation() -> None:
    application = AgentApplication(resolve=StubResolveReader())

    with pytest.raises(ValueError, match="confirm_delete must be true"):
        application.resolve_delete_clip(
            "timeline-1",
            "item-1",
            confirm_delete=False,
        )


def test_application_verifies_completed_render_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile"
    output_directory = tmp_path / "local" / "DaVinciResolveAgent" / "media" / "renders"
    output_directory.mkdir(parents=True)
    output_file = output_directory / "M11 Test.mp4"
    output_file.write_bytes(b"rendered")
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    resolve = StubCompletedResolveReader(output_directory)
    application = AgentApplication(resolve=resolve)

    result = application.resolve_verify_render_output(
        "job-1",
        timeout_seconds=25,
    )

    assert result["output"]["path"] == str(output_file.resolve())
    assert result["output"]["size_bytes"] == 8
    assert result["validation"] == {
        "completed": True,
        "managed_path": True,
        "non_empty": True,
        "passed": True,
    }


def test_application_creates_review_only_plan_without_resolve_call() -> None:
    resolve = StubResolveReader()
    media_policy = StubMediaPolicy()
    planner = StubRoughCutPlanner()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=resolve,
        media_policy=media_policy,
        rough_cut_planner=planner,
        workflow_audit=audit,
    )

    plan = application.create_rough_cut(
        screen_file="screen.mkv",
        webcam_file="webcam.mkv",
        screen_audio_file="screen.wav",
        webcam_audio_file="webcam.wav",
        speech_audio_file="speech.wav",
        timeline_name="M5 Draft",
    )

    assert plan["status"] == "pending_review"
    assert plan["review"]["apply_supported"] is False
    assert planner.arguments["screen_file"] == "normalized:screen.mkv"
    assert media_policy.paths == [
        "screen.mkv",
        "webcam.mkv",
        "screen.wav",
        "webcam.wav",
        "speech.wav",
    ]
    assert audit.operations == ["create_rough_cut"]
    assert resolve.timeouts == []


def test_application_approves_plan_without_resolve_call() -> None:
    resolve = StubResolveReader()
    reviewer = StubRoughCutReviewer()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=resolve,
        rough_cut_reviewer=reviewer,
        workflow_audit=audit,
    )

    approval = application.approve_rough_cut(
        "a" * 64,
        confirm_review=True,
    )

    assert approval["status"] == "approved"
    assert approval["apply_supported"] is False
    assert reviewer.plan_id == "a" * 64
    assert reviewer.confirm_review is True
    assert audit.operations == ["approve_rough_cut"]
    assert resolve.timeouts == []


def test_application_inspects_plans_without_resolve_call() -> None:
    resolve = StubResolveReader()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=resolve,
        rough_cut_inspector=StubRoughCutInspector(),
        workflow_audit=audit,
    )

    detail = application.get_rough_cut_plan("a" * 64)
    listing = application.list_rough_cut_plans(limit=10)

    assert detail["effective_status"] == "pending_review"
    assert listing["count"] == 1
    assert audit.operations == [
        "get_rough_cut_plan",
        "list_rough_cut_plans",
    ]
    assert resolve.timeouts == []


def test_application_runs_synchronized_pair_workflow() -> None:
    assembler = StubSynchronizedPairAssembler()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        synchronized_pair_assembler=assembler,
        workflow_audit=audit,
    )

    result = application.sync_screen_and_webcam(
        timeline_name="M38 Synced Pair",
        screen_asset_id="screen",
        webcam_asset_id="webcam",
        webcam_offset_ms=200,
        confirm_sync=True,
        timeout_seconds=45,
    )

    assert result["status"] == "applied"
    assert assembler.arguments == {
        "timeline_name": "M38 Synced Pair",
        "screen_asset_id": "screen",
        "webcam_asset_id": "webcam",
        "webcam_offset_ms": 200,
        "confirm_sync": True,
        "timeout_seconds": 45,
    }
    assert audit.operations == ["sync_screen_and_webcam"]


def test_application_runs_picture_in_picture_workflow() -> None:
    composer = StubPictureInPictureComposer()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        picture_in_picture_composer=composer,
        workflow_audit=audit,
    )

    result = application.compose_webcam_picture_in_picture(
        synchronized_pair_receipt_id="a" * 64,
        size_percent=25,
        center_x_percent=82,
        center_y_percent=82,
        confirm_layout=True,
        timeout_seconds=45,
    )

    assert result["status"] == "applied"
    assert composer.arguments == {
        "synchronized_pair_receipt_id": "a" * 64,
        "size_percent": 25,
        "center_x_percent": 82,
        "center_y_percent": 82,
        "confirm_layout": True,
        "timeout_seconds": 45,
    }
    assert audit.operations == ["compose_webcam_picture_in_picture"]


def test_application_runs_synchronized_screen_link_workflow() -> None:
    linker = StubSynchronizedScreenLinker()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        synchronized_screen_linker=linker,
        workflow_audit=audit,
    )

    result = application.link_synchronized_screen_pair(
        synchronized_pair_receipt_id="a" * 64,
        confirm_link=True,
        timeout_seconds=45,
    )

    assert result["status"] == "applied"
    assert linker.arguments == {
        "synchronized_pair_receipt_id": "a" * 64,
        "confirm_link": True,
        "timeout_seconds": 45,
    }
    assert audit.operations == ["link_synchronized_screen_pair"]


def test_application_previews_synchronized_pause_compaction() -> None:
    previewer = StubPauseCompactionPreviewer()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        pause_compaction_previewer=previewer,
        workflow_audit=audit,
    )

    result = application.preview_synchronized_pause_compaction(
        plan_id="a" * 64,
        synchronized_pair_receipt_id="b" * 64,
        target_timeline_name="M41 Preview",
        timeout_seconds=45,
    )

    assert result == {"status": "preview", "apply_supported": False}
    assert previewer.arguments == {
        "plan_id": "a" * 64,
        "synchronized_pair_receipt_id": "b" * 64,
        "target_timeline_name": "M41 Preview",
        "timeout_seconds": 45,
    }
    assert audit.operations == ["preview_synchronized_pause_compaction"]


def test_application_applies_synchronized_pause_compaction() -> None:
    applier = StubPauseCompactionApplier()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        pause_compaction_applier=applier,
        workflow_audit=audit,
    )

    result = application.apply_synchronized_pause_compaction(
        plan_id="a" * 64,
        synchronized_pair_receipt_id="b" * 64,
        target_timeline_name="M42 Apply",
        confirm_apply=True,
        timeout_seconds=45,
    )

    assert result == {"status": "applied", "placement_count": 6}
    assert applier.arguments == {
        "plan_id": "a" * 64,
        "synchronized_pair_receipt_id": "b" * 64,
        "target_timeline_name": "M42 Apply",
        "confirm_apply": True,
        "timeout_seconds": 45,
    }
    assert audit.operations == ["apply_synchronized_pause_compaction"]


def test_application_finalizes_synchronized_pause_compaction() -> None:
    finalizer = StubPauseCompactionFinalizer()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        pause_compaction_finalizer=finalizer,
        workflow_audit=audit,
    )

    result = application.finalize_synchronized_pause_compaction(
        pause_compaction_receipt_id="a" * 64,
        picture_in_picture_receipt_id="b" * 64,
        synchronized_link_receipt_id="c" * 64,
        confirm_finalize=True,
        timeout_seconds=45,
    )

    assert result["status"] == "applied"
    assert finalizer.arguments == {
        "pause_compaction_receipt_id": "a" * 64,
        "picture_in_picture_receipt_id": "b" * 64,
        "synchronized_link_receipt_id": "c" * 64,
        "confirm_finalize": True,
        "timeout_seconds": 45,
    }
    assert audit.operations == ["finalize_synchronized_pause_compaction"]


def test_application_prepares_finalized_timeline_render() -> None:
    preparer = StubFinalizedRenderPreparer()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        finalized_render_preparer=preparer,
        workflow_audit=audit,
    )

    result = application.prepare_finalized_timeline_render(
        finalization_receipt_id="a" * 64,
        custom_name="M44 Final",
        profile="youtube-1080p-h264-v1",
        confirm_prepare=True,
        timeout_seconds=45,
    )

    assert result["status"] == "applied"
    assert preparer.arguments == {
        "finalization_receipt_id": "a" * 64,
        "custom_name": "M44 Final",
        "profile": "youtube-1080p-h264-v1",
        "confirm_prepare": True,
        "timeout_seconds": 45,
    }
    assert audit.operations == ["prepare_finalized_timeline_render"]


def test_application_starts_and_inspects_finalized_render() -> None:
    executor = StubFinalizedRenderExecutor()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        finalized_render_executor=executor,
        workflow_audit=audit,
    )

    started = application.start_finalized_timeline_render(
        preparation_receipt_id="a" * 64,
        confirm_render=True,
        timeout_seconds=45,
    )
    status = application.get_finalized_timeline_render_status(
        "b" * 64,
        timeout_seconds=20,
    )

    assert started["status"] == "started"
    assert status["output"]["validation"]["passed"] is True
    assert executor.start_arguments == {
        "preparation_receipt_id": "a" * 64,
        "confirm_render": True,
        "timeout_seconds": 45,
    }
    assert executor.status_arguments == {
        "execution_receipt_id": "b" * 64,
        "timeout_seconds": 20,
    }
    assert audit.operations == [
        "start_finalized_timeline_render",
        "get_finalized_timeline_render_status",
    ]


def test_application_prepares_starts_and_inspects_finalized_audio() -> None:
    extractor = StubFinalizedAudioExtractor()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        finalized_audio_extractor=extractor,
        workflow_audit=audit,
    )

    prepared = application.prepare_finalized_timeline_audio(
        finalization_receipt_id="a" * 64,
        custom_name="M46 Dialogue Source",
        confirm_prepare=True,
        timeout_seconds=45,
    )
    started = application.start_finalized_timeline_audio(
        prepared["receipt_id"],
        confirm_render=True,
        timeout_seconds=30,
    )
    status = application.get_finalized_timeline_audio_status(
        prepared["receipt_id"], timeout_seconds=20
    )

    assert started["status"] == "started"
    assert status["output"]["validation"]["passed"] is True
    assert [name for name, _ in extractor.calls] == ["prepare", "start", "status"]
    assert audit.operations == [
        "prepare_finalized_timeline_audio",
        "start_finalized_timeline_audio",
        "get_finalized_timeline_audio_status",
    ]


def test_application_processes_audio_without_resolve_call() -> None:
    resolve = StubResolveReader()
    media_policy = StubMediaPolicy()
    processor = StubAudioProcessor()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=resolve,
        media_policy=media_policy,
        audio_processor=processor,
        workflow_audit=audit,
    )

    report = application.clean_dialogue_audio("dialogue.wav")

    assert report["status"] == "completed"
    assert report["source"]["preserved"] is True
    assert processor.source_file == "normalized:dialogue.wav"
    assert processor.preset == "pcm-dialogue-level-v1"
    assert audit.operations == ["clean_dialogue_audio"]
    assert resolve.timeouts == []


def test_application_inspects_audio_reports_without_resolve_call() -> None:
    resolve = StubResolveReader()
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=resolve,
        audio_report_inspector=StubAudioReportInspector(),
        workflow_audit=audit,
    )

    detail = application.get_audio_report("b" * 64)
    listing = application.list_audio_reports(limit=10)

    assert detail["status"] == "completed"
    assert listing["count"] == 1
    assert audit.operations == [
        "get_audio_report",
        "list_audio_reports",
    ]
    assert resolve.timeouts == []


def test_application_lists_previews_and_runs_editing_recipe() -> None:
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        editing_recipe_runner=StubEditingRecipeRunner(),
        workflow_audit=audit,
    )
    inputs = {"synchronized_pair_receipt_id": "a" * 64}

    listing = application.list_editing_recipes()
    detail = application.get_editing_recipe("tutorial-layout-v1")
    preview = application.preview_editing_recipe(
        "tutorial-layout-v1",
        inputs,
    )
    result = application.run_editing_recipe(
        "tutorial-layout-v1",
        inputs,
        confirm_execute=True,
        timeout_seconds=45,
    )

    assert listing["count"] == 1
    assert detail["recipe_version"] == "1.0"
    assert preview["status"] == "ready"
    assert result["status"] == "applied"
    assert result["timeout_seconds"] == 45
    assert audit.operations == [
        "list_editing_recipes",
        "get_editing_recipe",
        "preview_editing_recipe",
        "run_editing_recipe",
    ]


def test_application_previews_starts_and_inspects_baseline_edit() -> None:
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        baseline_edit_service=StubBaselineEditWorkflow(),
        workflow_audit=audit,
    )
    ids = {
        "finalization_receipt_id": "a" * 64,
        "audio_integration_receipt_id": "b" * 64,
        "subtitle_receipt_id": "c" * 64,
        "visual_treatment_receipt_id": "d" * 64,
    }

    preview = application.preview_baseline_edit(**ids, timeout_seconds=20)
    started = application.start_baseline_render(
        **ids,
        custom_name="Baseline",
        profile="youtube-2160p-h264-v1",
        confirm_render=True,
        timeout_seconds=25,
    )
    status = application.get_baseline_render_status(
        "f" * 64,
        timeout_seconds=15,
    )

    assert preview["status"] == "ready"
    assert preview["arguments"]["timeout_seconds"] == 20
    assert started["status"] == "started"
    assert started["arguments"]["profile"] == "youtube-2160p-h264-v1"
    assert status["status"] == "complete"
    assert audit.operations == [
        "preview_baseline_edit",
        "start_baseline_render",
        "get_baseline_render_status",
    ]


def test_application_previews_and_applies_reviewed_broll() -> None:
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        broll_service=StubBrollWorkflow(),
        workflow_audit=audit,
    )
    placements = [
        {
            "asset_id": "asset-1",
            "source_start_frame": 0,
            "source_end_frame": 47,
            "position_frames": 24,
            "track_index": 3,
            "purpose": "Show a detail",
        }
    ]

    preview = application.preview_broll_plan(
        baseline_edit_receipt_id="a" * 64,
        target_timeline_name="M51 B-roll",
        placements=placements,
        timeout_seconds=20,
    )
    applied = application.apply_broll_plan(
        baseline_edit_receipt_id="a" * 64,
        target_timeline_name="M51 B-roll",
        placements=placements,
        expected_plan_id="e" * 64,
        confirm_apply=True,
        timeout_seconds=25,
    )
    status = application.get_broll_status("f" * 64, timeout_seconds=15)

    assert preview["status"] == "preview"
    assert preview["arguments"]["timeout_seconds"] == 20
    assert applied["status"] == "applied"
    assert applied["arguments"]["confirm_apply"] is True
    assert status["status"] == "applied"
    assert audit.operations == [
        "preview_broll_plan",
        "apply_broll_plan",
        "get_broll_status",
    ]


def test_application_exposes_packaged_animation_template_workflow() -> None:
    audit = StubWorkflowAuditor()
    application = AgentApplication(
        resolve=StubResolveReader(),
        animation_template_service=StubAnimationTemplateWorkflow(),
        workflow_audit=audit,
    )
    listing = application.list_animation_templates()
    detail = application.get_animation_template("accent-card-v1")
    preview = application.preview_animation_template(
        broll_receipt_id="a" * 64,
        target_timeline_name="M52 Animation",
        template_id="accent-card-v1",
        timecode="01:01:15:00",
    )
    applied = application.apply_animation_template(
        broll_receipt_id="a" * 64,
        target_timeline_name="M52 Animation",
        template_id="accent-card-v1",
        timecode="01:01:15:00",
        expected_plan_id="d" * 64,
        confirm_apply=True,
    )

    assert listing["count"] == 1
    assert detail["template_id"] == "accent-card-v1"
    assert preview["status"] == "preview"
    assert applied["status"] == "applied"
    assert applied["arguments"]["confirm_apply"] is True
    assert audit.operations == [
        "list_animation_templates",
        "get_animation_template",
        "preview_animation_template",
        "apply_animation_template",
    ]


def test_application_exposes_animation_bridge_primitives() -> None:
    resolve = StubResolveReader()
    application = AgentApplication(resolve=resolve)

    environment = application.resolve_get_animation_template_environment(
        "timeline-1", timeout_seconds=15
    )
    inserted = application.resolve_insert_animation_template(
        "timeline-1",
        "accent-card-v1",
        "01:00:05:00",
        confirm_insert=True,
        timeout_seconds=20,
    )

    assert environment["ready"] is True
    assert inserted["template_id"] == "accent-card-v1"
    assert resolve.timeouts[-2:] == [15, 20]


@pytest.mark.parametrize("timeout_seconds", [0, -1, 301])
def test_application_rejects_unsafe_timeout(timeout_seconds: float) -> None:
    application = AgentApplication(resolve=StubResolveReader())

    with pytest.raises(ValueError, match="timeout_seconds"):
        application.resolve_get_project(timeout_seconds)
