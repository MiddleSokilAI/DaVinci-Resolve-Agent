"""Typed Resolve adapter over the provider-neutral command client."""

from __future__ import annotations

from typing import Any, cast

from agent.client import (
    BridgeCommandError,
    BridgeProtocolError,
    CommandClient,
    FilesystemCommandClient,
)
from agent.contracts import ContractValidationError, validate_contract
from agent.rendering import DEFAULT_RENDER_PROFILE


class ResolveProviderClient:
    """Expose the explicitly allowlisted Resolve command surface."""

    def __init__(
        self,
        command_client: CommandClient | None = None,
    ) -> None:
        self._client = (
            FilesystemCommandClient() if command_client is None else command_client
        )

    def _request(self, action: str, timeout_seconds: float) -> Any:
        return self._client.request(
            provider="resolve",
            action=action,
            timeout_seconds=timeout_seconds,
        )

    def ping(self, timeout_seconds: float = 30) -> str:
        result = self._request("ping", timeout_seconds)
        if not isinstance(result, dict) or result.get("message") != "pong":
            raise BridgeProtocolError("Resolve ping response is invalid.")
        return "pong"

    def stop_bridge(self, timeout_seconds: float = 30) -> dict[str, Any]:
        """Request a clean stop of a manually launched persistent bridge."""
        result = self._object_result("stop_bridge", timeout_seconds)
        if result.get("status") != "stopping":
            raise BridgeProtocolError("Resolve stop_bridge response is invalid.")
        return result

    def bridge_info(self, timeout_seconds: float = 30) -> dict[str, Any]:
        return self._object_result("get_bridge_info", timeout_seconds)

    def capabilities(self, timeout_seconds: float = 30) -> dict[str, bool | str]:
        result = self._object_result("get_capabilities", timeout_seconds)
        try:
            validate_contract("capability", result)
        except ContractValidationError as error:
            raise BridgeProtocolError(str(error)) from error
        return cast(dict[str, bool | str], result)

    def current_project(self, timeout_seconds: float = 30) -> dict[str, Any] | None:
        return self._optional_object_result("get_current_project", timeout_seconds)

    def timelines(self, timeout_seconds: float = 30) -> list[dict[str, Any]]:
        result = self._request("list_timelines", timeout_seconds)
        if not isinstance(result, list) or not all(
            isinstance(item, dict) for item in result
        ):
            raise BridgeProtocolError("Resolve timelines response is invalid.")
        return cast(list[dict[str, Any]], result)

    def current_timeline(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any] | None:
        return self._optional_object_result("get_current_timeline", timeout_seconds)

    def timeline_items(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return bounded metadata for video/audio items in one timeline."""
        result = self._client.request(
            provider="resolve",
            action="list_timeline_items",
            arguments={"timeline_id": timeline_id},
            timeout_seconds=timeout_seconds,
        )
        value = self._object_value("list_timeline_items", result)
        if not isinstance(value.get("items"), list):
            raise BridgeProtocolError(
                "Resolve list_timeline_items response is invalid."
            )
        return value

    def media_pool_items(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return bounded identity metadata for current Media Pool items."""
        result = self._object_result("list_media_pool_items", timeout_seconds)
        if (
            not isinstance(result.get("items"), list)
            or not isinstance(result.get("folder_count"), int)
        ):
            raise BridgeProtocolError(
                "Resolve list_media_pool_items response is invalid."
            )
        return result

    def editing_metadata(
        self,
        timeline_id: str,
        asset_ids: list[str],
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return bounded source and target timeline placement metadata."""
        result = self._client.request(
            provider="resolve",
            action="get_editing_metadata",
            arguments={"timeline_id": timeline_id, "asset_ids": asset_ids},
            timeout_seconds=timeout_seconds,
        )
        value = self._object_value("get_editing_metadata", result)
        timeline = value.get("timeline")
        assets = value.get("assets")
        if not isinstance(timeline, dict) or not isinstance(assets, list):
            raise BridgeProtocolError(
                "Resolve get_editing_metadata response is invalid."
            )
        return value

    def subtitle_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return bounded subtitle items and native auto-caption availability."""
        result = self._client.request(
            provider="resolve",
            action="get_subtitle_environment",
            arguments={"timeline_id": timeline_id},
            timeout_seconds=timeout_seconds,
        )
        value = self._object_value("get_subtitle_environment", result)
        if (
            not isinstance(value.get("tracks"), list)
            or not isinstance(value.get("auto_caption"), dict)
            or not isinstance(value.get("subtitle_track_count"), int)
            or not isinstance(value.get("subtitle_item_count"), int)
        ):
            raise BridgeProtocolError(
                "Resolve get_subtitle_environment response is invalid."
            )
        return value

    def animation_template_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return documented Fusion-title methods and installed template state."""
        result = self._client.request(
            provider="resolve",
            action="get_animation_template_environment",
            arguments={"timeline_id": timeline_id},
            timeout_seconds=timeout_seconds,
        )
        value = self._object_value(
            "get_animation_template_environment", result
        )
        if (
            not isinstance(value.get("timeline"), dict)
            or not isinstance(value.get("methods"), dict)
            or not isinstance(value.get("templates"), list)
            or not isinstance(value.get("ready"), bool)
        ):
            raise BridgeProtocolError(
                "Resolve animation-template environment response is invalid."
            )
        return value

    def color_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return documented color-version and node-graph metadata."""
        result = self._client.request(
            provider="resolve",
            action="get_color_environment",
            arguments={"timeline_id": timeline_id},
            timeout_seconds=timeout_seconds,
        )
        value = self._object_value("get_color_environment", result)
        if (
            not isinstance(value.get("timeline"), dict)
            or not isinstance(value.get("methods"), dict)
            or not isinstance(value.get("items"), list)
            or not isinstance(value.get("ready"), bool)
            or not isinstance(value.get("apply_candidate"), bool)
        ):
            raise BridgeProtocolError(
                "Resolve color environment response is invalid."
            )
        return value

    def create_subtitles_from_audio(
        self,
        timeline_id: str,
        *,
        confirm_create: bool,
        timeout_seconds: float = 300,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create native auto-captions with one fixed backed-up policy."""
        result = self._client.request(
            provider="resolve",
            action="create_subtitles_from_audio",
            arguments={
                "timeline_id": timeline_id,
                "confirm_create": confirm_create,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        value = self._object_value("create_subtitles_from_audio", result)
        if (
            not isinstance(value.get("policy"), dict)
            or not isinstance(value.get("subtitle_environment"), dict)
            or not isinstance(value.get("backup_path"), str)
        ):
            raise BridgeProtocolError(
                "Resolve create_subtitles_from_audio response is invalid."
            )
        return value

    def workspace_snapshot(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return the fixed read-only workspace view from one bridge run."""
        result = self._object_result("get_workspace_snapshot", timeout_seconds)
        required_objects = ("bridge", "project", "media_pool", "render")
        if (
            not all(isinstance(result.get(name), dict) for name in required_objects)
            or not isinstance(result.get("timelines"), list)
            or (
                result.get("current_timeline") is not None
                and not isinstance(result.get("current_timeline"), dict)
            )
            or (
                result.get("timeline_items") is not None
                and not isinstance(result.get("timeline_items"), dict)
            )
        ):
            raise BridgeProtocolError(
                "Resolve get_workspace_snapshot response is invalid."
            )
        return result

    def render_environment(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return documented render formats, presets, current values, and jobs."""
        result = self._object_result("get_render_environment", timeout_seconds)
        if (
            not isinstance(result.get("formats"), list)
            or not isinstance(result.get("current"), dict)
            or not isinstance(result.get("presets"), list)
            or not isinstance(result.get("jobs"), list)
            or not isinstance(result.get("mp4_h264_resolutions"), list)
        ):
            raise BridgeProtocolError(
                "Resolve get_render_environment response is invalid."
            )
        return result

    def import_media(
        self,
        paths: list[str],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Import validated media paths with a mandatory project backup."""
        result = self._client.request(
            provider="resolve",
            action="import_media",
            arguments={"paths": paths},
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("import_media", result)

    def close_project(
        self,
        expected_project_id: str,
        *,
        confirm_close: bool,
        timeout_seconds: float = 30,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Close only an exact saved project, preserving original replay receipts."""
        result = self._client.request(
            provider="resolve", action="close_project",
            arguments={"expected_project_id": expected_project_id,
                       "confirm_close": confirm_close},
            timeout_seconds=timeout_seconds, idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("close_project", result)

    def create_project(
        self,
        name: str,
        *,
        confirm_create: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create and save a unique project through the guarded bridge action."""
        result = self._client.request(
            provider="resolve",
            action="create_project",
            arguments={"name": name, "confirm_create": confirm_create},
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("create_project", result)

    def create_timeline(
        self,
        name: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create an empty timeline with a mandatory project backup."""
        result = self._client.request(
            provider="resolve",
            action="create_timeline",
            arguments={"name": name},
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("create_timeline", result)

    def duplicate_timeline(
        self,
        timeline_id: str,
        name: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Duplicate one timeline with a mandatory project backup."""
        result = self._client.request(
            provider="resolve",
            action="duplicate_timeline",
            arguments={"timeline_id": timeline_id, "name": name},
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("duplicate_timeline", result)

    def ensure_timeline_tracks(
        self,
        timeline_id: str,
        video_track_count: int,
        audio_track_count: int,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Ensure bounded minimum video/audio track counts after a backup."""
        result = self._client.request(
            provider="resolve",
            action="ensure_timeline_tracks",
            arguments={
                "timeline_id": timeline_id,
                "video_track_count": video_track_count,
                "audio_track_count": audio_track_count,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("ensure_timeline_tracks", result)

    def set_current_timeline(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Select one existing timeline after a project backup."""
        result = self._client.request(
            provider="resolve",
            action="set_current_timeline",
            arguments={"timeline_id": timeline_id},
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("set_current_timeline", result)

    def append_clip(
        self,
        timeline_id: str,
        asset_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Append a media-pool asset with a mandatory project backup."""
        result = self._client.request(
            provider="resolve",
            action="append_clip",
            arguments={
                "timeline_id": timeline_id,
                "asset_id": asset_id,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("append_clip", result)

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
        """Insert one installed standard title at an exact timecode."""
        result = self._client.request(
            provider="resolve",
            action="insert_title",
            arguments={
                "timeline_id": timeline_id,
                "title_name": title_name,
                "timecode": timecode,
                "confirm_insert": confirm_insert,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("insert_title", result)

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
        """Insert one allowlisted packaged Fusion title after a backup."""
        arguments = {
            "timeline_id": timeline_id,
            "template_id": template_id,
            "timecode": timecode,
            "confirm_insert": confirm_insert,
        }

        def request() -> Any:
            return self._client.request(
                provider="resolve",
                action="insert_animation_template",
                arguments=arguments,
                timeout_seconds=timeout_seconds,
                idempotency_key=idempotency_key,
                create_backup=True,
            )

        try:
            result = request()
        except BridgeCommandError as error:
            if error.code != "TIMECODE_READBACK_FAILED" or not error.retryable:
                raise
            result = request()
        return self._object_value("insert_animation_template", result)

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
        """Append one receipt-bound SRT and report its placement anchor."""
        result = self._client.request(
            provider="resolve",
            action="append_subtitle_file",
            arguments={
                "timeline_id": timeline_id,
                "asset_id": asset_id,
                "subtitle_path": subtitle_path,
                "import_idempotency_key": import_idempotency_key,
                "confirm_apply": confirm_apply,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("append_subtitle_file", result)

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
        """Insert one bounded source range with a mandatory project backup."""
        result = self._client.request(
            provider="resolve",
            action="insert_clip",
            arguments={
                "timeline_id": timeline_id,
                "asset_id": asset_id,
                "source_start_frame": source_start_frame,
                "source_end_frame": source_end_frame,
                "position_frames": position_frames,
                "track_type": track_type,
                "track_index": track_index,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("insert_clip", result)

    def insert_clips(
        self,
        timeline_id: str,
        placements: list[dict[str, Any]],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert bounded source ranges in one backed-up provider operation."""
        result = self._client.request(
            provider="resolve",
            action="insert_clips",
            arguments={
                "timeline_id": timeline_id,
                "placements": placements,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("insert_clips", result)

    def set_clip_enabled(
        self,
        timeline_id: str,
        timeline_item_id: str,
        enabled: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set one timeline item enabled state after a project backup."""
        result = self._client.request(
            provider="resolve",
            action="set_clip_enabled",
            arguments={
                "timeline_id": timeline_id,
                "timeline_item_id": timeline_item_id,
                "enabled": enabled,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("set_clip_enabled", result)

    def set_clips_linked(
        self,
        timeline_id: str,
        timeline_item_ids: list[str],
        linked: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Link or unlink a bounded TimelineItem group after a backup."""
        result = self._client.request(
            provider="resolve",
            action="set_clips_linked",
            arguments={
                "timeline_id": timeline_id,
                "timeline_item_ids": timeline_item_ids,
                "linked": linked,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("set_clips_linked", result)

    def set_clip_link_groups(
        self,
        timeline_id: str,
        groups: list[list[str]],
        linked: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply bounded independent link groups in one backed-up operation."""
        result = self._client.request(
            provider="resolve",
            action="set_clip_link_groups",
            arguments={
                "timeline_id": timeline_id,
                "groups": groups,
                "linked": linked,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("set_clip_link_groups", result)

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
        """Apply one bounded provider-neutral video clip transform."""
        arguments: dict[str, Any] = {
            "timeline_id": timeline_id,
            "timeline_item_id": timeline_item_id,
        }
        for name, value in (
            ("position_x", position_x),
            ("position_y", position_y),
            ("zoom", zoom),
            ("rotation_degrees", rotation_degrees),
            ("opacity_percent", opacity_percent),
        ):
            if value is not None:
                arguments[name] = value
        result = self._client.request(
            provider="resolve",
            action="set_clip_transform",
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("set_clip_transform", result)

    def set_clip_transforms(
        self,
        timeline_id: str,
        items: list[dict[str, Any]],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply bounded transforms in one backed-up provider operation."""
        result = self._client.request(
            provider="resolve",
            action="set_clip_transforms",
            arguments={"timeline_id": timeline_id, "items": items},
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("set_clip_transforms", result)

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
        """Apply one allowlisted CDL preset to exact media-video items."""
        result = self._client.request(
            provider="resolve",
            action="apply_color_preset",
            arguments={
                "timeline_id": timeline_id,
                "timeline_item_ids": timeline_item_ids,
                "preset_id": preset_id,
                "confirm_apply": confirm_apply,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("apply_color_preset", result)

    def delete_clip(
        self,
        timeline_id: str,
        timeline_item_id: str,
        *,
        confirm_delete: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Delete exactly one timeline item without ripple after a backup."""
        result = self._client.request(
            provider="resolve",
            action="delete_clip",
            arguments={
                "timeline_id": timeline_id,
                "timeline_item_id": timeline_item_id,
                "confirm_delete": confirm_delete,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
            allow_destructive=True,
        )
        return self._object_value("delete_clip", result)

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
        """Add a timeline marker with a mandatory project backup."""
        result = self._client.request(
            provider="resolve",
            action="add_marker",
            arguments={
                "timeline_id": timeline_id,
                "frame": frame,
                "color": color,
                "name": name,
                "note": note,
                "duration": duration,
            },
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("add_marker", result)

    def prepare_render_job(
        self,
        custom_name: str,
        *,
        timeline_id: str | None = None,
        profile: str = DEFAULT_RENDER_PROFILE,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add one fixed safe render job with a mandatory project backup."""
        arguments = {"custom_name": custom_name, "profile": profile}
        if timeline_id is not None:
            arguments["timeline_id"] = timeline_id
        result = self._client.request(
            provider="resolve",
            action="prepare_render_job",
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("prepare_render_job", result)

    def render_job_status(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return documented status for one queued render job."""
        result = self._client.request(
            provider="resolve",
            action="get_render_job_status",
            arguments={"job_id": job_id},
            timeout_seconds=timeout_seconds,
        )
        return self._object_value("get_render_job_status", result)

    def start_render_job(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start one agent-prepared render job after a project backup."""
        result = self._client.request(
            provider="resolve",
            action="start_render_job",
            arguments={"job_id": job_id},
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            create_backup=True,
        )
        return self._object_value("start_render_job", result)

    def _object_result(
        self,
        action: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        result = self._request(action, timeout_seconds)
        if not isinstance(result, dict):
            raise BridgeProtocolError(f"Resolve {action} response is invalid.")
        return result

    def _optional_object_result(
        self,
        action: str,
        timeout_seconds: float,
    ) -> dict[str, Any] | None:
        result = self._request(action, timeout_seconds)
        if result is None:
            return None
        if not isinstance(result, dict):
            raise BridgeProtocolError(f"Resolve {action} response is invalid.")
        return result

    @staticmethod
    def _object_value(action: str, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise BridgeProtocolError(f"Resolve {action} response is invalid.")
        return result
