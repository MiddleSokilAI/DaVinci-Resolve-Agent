"""One-shot allowlisted bridge executed inside DaVinci Resolve."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import shutil
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

BRIDGE_VERSION = "0.1.0"
PROTOCOL_VERSION = "1.0"
APPLICATION_DIRECTORY_NAME = "DaVinciResolveAgent"
STORAGE_LAYOUT_VERSION = "1.0"
STORAGE_MANIFEST_NAME = "storage.json"
MAX_STORAGE_MANIFEST_BYTES = 16384
MINIMUM_OUTPUT_FREE_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_RENDER_PROFILE = "youtube-1080p-h264-v1"
POLL_INTERVAL_SECONDS = 0.5
RENDER_PROFILES = {
    "youtube-1080p-h264-v1": {
        "kind": "video",
        "resolve_preset": "YouTube - 1080p",
        "format": "MP4",
        "codec": "H264",
        "extension": ".mp4",
        "width": 1920,
        "height": 1080,
    },
    "youtube-2160p-h264-v1": {
        "kind": "video",
        "resolve_preset": "YouTube - 2160p",
        "format": "MP4",
        "codec": "H264",
        "extension": ".mp4",
        "width": 3840,
        "height": 2160,
    },
    "audio-only-pcm-wav-v1": {
        "kind": "audio",
        "resolve_preset": "Audio Only",
        "format": "Wave",
        "extension": ".wav",
        "audio_bit_depth": 16,
        "audio_sample_rate": 48000,
    },
}
ANIMATION_TEMPLATES = {
    "accent-card-v1": {
        "resolve_name": "DaVinci Agent Accent Card",
        "relative_path": "Edit/Titles/DaVinci Agent Accent Card.setting",
        "sha256": "15deaf708213ba1ffc8d509069bd761467a69c71ee510148b9ce78441b3f1bf1",
    }
}
COLOR_PRESETS = {
    "tutorial-clean-v1": {
        "version_name": "DaVinci Agent Tutorial Clean v1",
        "version_type": 0,
        "cdl": {
            "NodeIndex": "1",
            "Slope": "1.03 1.03 1.03",
            "Offset": "0.0 0.0 0.0",
            "Power": "1.0 1.0 1.0",
            "Saturation": "1.05",
        },
    }
}
ALLOWED_ACTIONS = {
    "close_project",
    "create_project",
    "ping",
    "stop_bridge",
    "get_bridge_info",
    "get_capabilities",
    "get_current_project",
    "list_timelines",
    "get_current_timeline",
    "list_timeline_items",
    "list_media_pool_items",
    "get_editing_metadata",
    "get_subtitle_environment",
    "get_animation_template_environment",
    "get_color_environment",
    "apply_color_preset",
    "get_workspace_snapshot",
    "get_render_environment",
    "get_render_job_status",
    "import_media",
    "create_timeline",
    "ensure_timeline_tracks",
    "duplicate_timeline",
    "set_current_timeline",
    "append_clip",
    "insert_title",
    "insert_animation_template",
    "append_subtitle_file",
    "insert_clip",
    "insert_clips",
    "set_clip_enabled",
    "set_clips_linked",
    "set_clip_link_groups",
    "set_clip_transform",
    "set_clip_transforms",
    "delete_clip",
    "add_marker",
    "create_subtitles_from_audio",
    "prepare_render_job",
    "start_render_job",
}
WRITE_ACTIONS = {
    "close_project",
    "create_project",
    "import_media",
    "create_timeline",
    "ensure_timeline_tracks",
    "duplicate_timeline",
    "set_current_timeline",
    "append_clip",
    "insert_title",
    "insert_animation_template",
    "append_subtitle_file",
    "insert_clip",
    "insert_clips",
    "set_clip_enabled",
    "set_clips_linked",
    "set_clip_link_groups",
    "set_clip_transform",
    "set_clip_transforms",
    "delete_clip",
    "add_marker",
    "create_subtitles_from_audio",
    "prepare_render_job",
    "start_render_job",
    "apply_color_preset",
}
DESTRUCTIVE_ACTIONS = {"delete_clip"}
CAPABILITY_BY_ACTION = {
    "close_project": "project.close",
    "create_project": "project.create",
    "list_timeline_items": "clip.read",
    "list_media_pool_items": "media.read",
    "get_editing_metadata": "media.metadata.read",
    "get_subtitle_environment": "subtitle.read",
    "get_color_environment": "color.inspect",
    "apply_color_preset": "color.cdl.apply",
    "create_subtitles_from_audio": "subtitle.auto_caption",
    "import_media": "media.import",
    "create_timeline": "timeline.create",
    "ensure_timeline_tracks": "timeline.track.create",
    "duplicate_timeline": "timeline.duplicate",
    "set_current_timeline": "timeline.select",
    "append_clip": "clip.insert",
    "insert_title": "title.insert",
    "insert_animation_template": "animation.template.insert",
    "append_subtitle_file": "subtitle.import",
    "insert_clip": "clip.range_insert",
    "insert_clips": "clip.range_insert",
    "set_clip_enabled": "clip.enable",
    "set_clips_linked": "clip.link",
    "set_clip_link_groups": "clip.link",
    "set_clip_transform": "clip.transform",
    "set_clip_transforms": "clip.transform",
    "delete_clip": "clip.delete",
    "add_marker": "marker.create",
    "prepare_render_job": "render.configure",
    "start_render_job": "render.start",
}


class BridgeOperationError(RuntimeError):
    """A safe error returned for an allowlisted Resolve operation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.details = {} if details is None else details
        super().__init__(message)


def utc_now() -> str:
    """Return a UTC timestamp suitable for protocol files."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def runtime_root(environment: Mapping[str, str] | None = None) -> Path:
    """Resolve the shared runtime root from the installed storage manifest."""
    return _storage_data_root(environment) / "runtime"


def _storage_data_root(
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Read the tiny provider-neutral storage bootstrap available in Resolve."""
    source = os.environ if environment is None else environment
    app_data = source.get("APPDATA")
    if app_data:
        manifest = (
            Path(app_data)
            / APPLICATION_DIRECTORY_NAME
            / STORAGE_MANIFEST_NAME
        )
        if manifest.is_file():
            try:
                if manifest.stat().st_size > MAX_STORAGE_MANIFEST_BYTES:
                    raise ValueError("Storage manifest is unexpectedly large.")
                with manifest.open("r", encoding="utf-8") as stream:
                    payload = json.load(stream)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"Storage manifest is unreadable: {error}"
                ) from error
            if (
                not isinstance(payload, dict)
                or set(payload) != {"storage_version", "data_root"}
                or payload.get("storage_version") != STORAGE_LAYOUT_VERSION
                or not isinstance(payload.get("data_root"), str)
            ):
                raise RuntimeError(
                    "Storage manifest does not match version 1.0."
                )
            root = Path(payload["data_root"])
            if not root.is_absolute() or root == Path(root.anchor):
                raise RuntimeError(
                    "Storage data_root must be an absolute non-volume-root path."
                )
            return root.resolve()
    local_app_data = source.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not set inside Resolve.")
    return Path(local_app_data) / APPLICATION_DIRECTORY_NAME


def _managed_media_root() -> Path:
    """Return the configured root for generated media."""
    return _storage_data_root() / "media"


def _ensure_output_capacity(output: Path) -> None:
    """Keep a fixed safety reserve on the configured output volume."""
    free = shutil.disk_usage(output).free
    if free < MINIMUM_OUTPUT_FREE_BYTES:
        raise BridgeOperationError(
            "OUTPUT_STORAGE_LOW",
            "The configured output volume has less than 10 GiB free.",
            retryable=False,
            details={"output_directory": str(output), "free_bytes": free},
        )


def runtime_directories(root: Path) -> dict[str, Path]:
    """Return the fixed filesystem transport layout."""
    return {
        name: root / name
        for name in (
            "commands",
            "processing",
            "responses",
            "failed",
            "state",
            "logs",
            "backups",
        )
    }


def ensure_runtime_directories(root: Path) -> dict[str, Path]:
    """Create the fixed filesystem transport layout."""
    directories = runtime_directories(root)
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    return directories


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    attempts: int = 25,
    retry_delay_seconds: float = 0.02,
) -> None:
    """Write JSON atomically with bounded Windows replace retries."""
    if attempts < 1:
        raise ValueError("attempts must be greater than zero.")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must not be negative.")
    temporary_path = path.with_name(
        f"{path.name}.{uuid4().hex}.tmp"
    )
    with temporary_path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    try:
        for attempt in range(attempts):
            try:
                temporary_path.replace(path)
                return
            except PermissionError:
                if attempt + 1 == attempts:
                    raise
                time.sleep(retry_delay_seconds)
    finally:
        if temporary_path.is_file():
            temporary_path.unlink()


def append_log(log_path: Path, level: str, event: str, **fields: Any) -> None:
    """Append one structured bridge log record."""
    record = {
        "timestamp": utc_now(),
        "level": level,
        "component": "bridge.resolve",
        "event": event,
        **fields,
    }
    with log_path.open("a", encoding="utf-8", newline="\n") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        log_file.write("\n")


def publish_bridge_state(
    state_path: Path,
    state: dict[str, Any],
    log_path: Path,
) -> bool:
    """Publish heartbeat state without ending the loop on a transient lock."""
    try:
        atomic_write_json(state_path, state)
    except PermissionError:
        append_log(
            log_path,
            "WARNING",
            "state_publish_deferred",
            error_code="WINDOWS_SHARING_VIOLATION",
            retryable=True,
        )
        return False
    return True


def _is_resolve_application(candidate: Any) -> bool:
    return candidate is not None and callable(
        getattr(candidate, "GetProjectManager", None)
    )


def get_resolve_application(context: Mapping[str, Any] | None = None) -> Any:
    """Return Resolve from the internal host context or documented module."""
    internal_context = globals() if context is None else context

    for name in ("resolve", "app"):
        candidate = internal_context.get(name)
        if _is_resolve_application(candidate):
            return candidate

    for name in ("app", "fusion", "fu"):
        host = internal_context.get(name)
        get_resolve = getattr(host, "GetResolve", None)
        if callable(get_resolve):
            candidate = get_resolve()
            if _is_resolve_application(candidate):
                return candidate

    try:
        scripting_module = importlib.import_module("DaVinciResolveScript")
    except ModuleNotFoundError as error:
        available = [
            name
            for name in ("resolve", "app", "fusion", "fu")
            if internal_context.get(name) is not None
        ]
        context_names = ", ".join(available) if available else "none"
        raise RuntimeError(
            "Resolve context was unavailable. "
            f"Detected internal context objects: {context_names}."
        ) from error

    candidate = scripting_module.scriptapp("Resolve")
    if not _is_resolve_application(candidate):
        message = "DaVinciResolveScript.scriptapp('Resolve') returned no object."
        raise RuntimeError(message)
    return candidate


def collect_bridge_state(
    resolve: Any,
    *,
    heartbeat: str | None = None,
) -> dict[str, Any]:
    """Collect only documented read-only Resolve information."""
    product_name = resolve.GetProductName()
    resolve_version = resolve.GetVersionString()
    version_fields = resolve.GetVersion()
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        raise RuntimeError("Resolve.GetProjectManager() returned no object.")

    project = project_manager.GetCurrentProject()
    project_name: str | None = None
    current_timeline_id: str | None = None
    current_timeline_name: str | None = None
    timelines: list[dict[str, Any]] = []
    timeline_read_capability: bool | str = "unknown"

    if project is not None:
        project_name = project.GetName()
        timeline_count = int(project.GetTimelineCount())
        for index in range(1, timeline_count + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline is not None:
                timelines.append(
                    {
                        "timeline_id": str(timeline.GetUniqueId()),
                        "index": index,
                        "name": timeline.GetName(),
                    }
                )
        current_timeline = project.GetCurrentTimeline()
        if current_timeline is not None:
            current_timeline_id = str(current_timeline.GetUniqueId())
            current_timeline_name = current_timeline.GetName()
        timeline_read_capability = True

    capabilities: dict[str, bool | str] = {
        "bridge.ping": True,
        "project.read": True,
        "timeline.read": timeline_read_capability,
        "timeline.create": "unknown",
        "timeline.track.create": "unknown",
        "timeline.duplicate": "unknown",
        "timeline.select": "unknown",
        "media.import": "unknown",
        "media.read": "unknown",
        "media.metadata.read": "unknown",
        "subtitle.read": "unknown",
        "subtitle.auto_caption": "unknown",
        "subtitle.import": "unknown",
        "clip.insert": "unknown",
        "clip.read": "unknown",
        "clip.range_insert": "unknown",
        "clip.enable": "unknown",
        "clip.link": "unknown",
        "clip.transform": "unknown",
        "title.insert": "unknown",
        "animation.template.insert": "unknown",
        "color.inspect": "unknown",
        "color.cdl.apply": "unknown",
        "clip.delete": "unknown",
        "marker.create": "unknown",
        "render.configure": "unknown",
        "render.discovery": "unknown",
        "render.start": "unknown",
    }
    return {
        "protocol_version": PROTOCOL_VERSION,
        "bridge_version": BRIDGE_VERSION,
        "status": "ready",
        "last_heartbeat": heartbeat or utc_now(),
        "provider": "resolve",
        "product_name": product_name,
        "resolve_version": resolve_version,
        "resolve_version_fields": version_fields,
        "edition": "unknown",
        "project_open": project is not None,
        "project_name": project_name,
        "current_timeline_id": current_timeline_id,
        "current_timeline_name": current_timeline_name,
        "timelines": timelines,
        "capabilities": capabilities,
        "lifecycle": {
            "mode": "persistent",
            "poll_interval_seconds": POLL_INTERVAL_SECONDS,
            "stop_supported": True,
        },
        "error": None,
    }


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 string.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be a valid ISO-8601 string.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone.")
    return parsed


def validate_command(command: Any) -> dict[str, Any]:
    """Validate the fixed command envelope and action-specific arguments."""
    if not isinstance(command, dict):
        raise ValueError("Command must be a JSON object.")

    expected_fields = {
        "protocol_version",
        "command_id",
        "idempotency_key",
        "created_at",
        "expires_at",
        "provider",
        "action",
        "arguments",
        "safety",
    }
    if set(command) != expected_fields:
        raise ValueError("Command fields do not match the protocol contract.")

    required_string_fields = (
        "command_id",
        "idempotency_key",
        "created_at",
        "expires_at",
        "provider",
        "action",
    )
    for field in required_string_fields:
        if not isinstance(command.get(field), str) or not command[field]:
            raise ValueError(f"{field} must be a non-empty string.")
    if command.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Unsupported protocol_version.")
    if command["provider"] != "resolve":
        raise ValueError("ResolveBridge only accepts provider 'resolve'.")
    if command["action"] not in ALLOWED_ACTIONS:
        raise ValueError("Unsupported or unsafe bridge action.")
    if not isinstance(command.get("arguments"), dict):
        raise ValueError("arguments must be a JSON object.")
    if not isinstance(command.get("safety"), dict):
        raise ValueError("safety must be a JSON object.")
    if set(command["safety"]) != {"allow_destructive", "create_backup"}:
        raise ValueError("safety fields do not match the protocol contract.")
    expected_destructive = command["action"] in DESTRUCTIVE_ACTIONS
    if command["safety"].get("allow_destructive") is not expected_destructive:
        raise ValueError(
            "safety.allow_destructive does not match the action policy."
        )
    if not isinstance(command["safety"].get("create_backup"), bool):
        raise ValueError("safety.create_backup must be a boolean.")
    if command["action"] in WRITE_ACTIONS:
        if command["safety"]["create_backup"] is not True:
            raise ValueError("Write commands must request a project backup.")
        _validate_write_arguments(command["action"], command["arguments"])
    elif command["action"] == "get_render_job_status":
        _validate_render_job_arguments(
            command["action"],
            command["arguments"],
        )
    elif command["action"] == "list_timeline_items":
        _validate_list_timeline_items_arguments(command["arguments"])
    elif command["action"] == "get_editing_metadata":
        _validate_editing_metadata_arguments(command["arguments"])
    elif command["action"] in {
        "get_subtitle_environment",
        "get_animation_template_environment",
        "get_color_environment",
    }:
        _validate_list_timeline_items_arguments(command["arguments"])
    elif command["arguments"] != {}:
        raise ValueError("This read-only action does not accept arguments.")

    _parse_timestamp(command["created_at"], "created_at")
    expires_at = _parse_timestamp(command["expires_at"], "expires_at")
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("Command has expired.")
    return command


def _validate_list_timeline_items_arguments(
    arguments: dict[str, Any],
) -> str:
    if set(arguments) != {"timeline_id"}:
        raise ValueError("list_timeline_items requires only timeline_id.")
    timeline_id = arguments["timeline_id"]
    if (
        not isinstance(timeline_id, str)
        or not timeline_id
        or len(timeline_id) > 128
    ):
        raise ValueError("timeline_id must contain 1 to 128 characters.")
    return timeline_id


def _validate_editing_metadata_arguments(
    arguments: dict[str, Any],
) -> tuple[str, list[str]]:
    """Validate bounded, read-only media metadata discovery arguments."""
    if set(arguments) != {"timeline_id", "asset_ids"}:
        raise ValueError(
            "get_editing_metadata requires timeline_id and asset_ids."
        )
    timeline_id = _validate_list_timeline_items_arguments(
        {"timeline_id": arguments["timeline_id"]}
    )
    asset_ids = arguments["asset_ids"]
    if (
        not isinstance(asset_ids, list)
        or not 0 <= len(asset_ids) <= 100
        or len(set(asset_ids)) != len(asset_ids)
        or not all(
            isinstance(asset_id, str)
            and 1 <= len(asset_id) <= 128
            for asset_id in asset_ids
        )
    ):
        raise ValueError(
            "asset_ids must contain 0 to 100 unique identifiers of up to "
            "128 characters."
        )
    return timeline_id, asset_ids


def _validate_write_arguments(action: str, arguments: dict[str, Any]) -> None:
    """Validate bridge-side write arguments without trusting the agent."""
    if action == "apply_color_preset":
        expected = {
            "timeline_id",
            "timeline_item_ids",
            "preset_id",
            "confirm_apply",
        }
        if set(arguments) != expected:
            raise ValueError("apply_color_preset fields do not match the contract.")
        timeline_id = arguments["timeline_id"]
        item_ids = arguments["timeline_item_ids"]
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if (
            not isinstance(item_ids, list)
            or not 1 <= len(item_ids) <= 100
            or len(set(item_ids)) != len(item_ids)
            or any(
                not isinstance(item_id, str)
                or not item_id
                or len(item_id) > 128
                for item_id in item_ids
            )
        ):
            raise ValueError(
                "timeline_item_ids must contain 1 to 100 unique IDs."
            )
        if arguments["preset_id"] not in COLOR_PRESETS:
            raise ValueError("preset_id is not allowlisted.")
        if arguments["confirm_apply"] is not True:
            raise ValueError("apply_color_preset requires confirm_apply=true.")
        return
    if action == "import_media":
        if set(arguments) != {"paths"}:
            raise ValueError("import_media requires only paths.")
        paths = arguments["paths"]
        if not isinstance(paths, list) or not 1 <= len(paths) <= 100:
            raise ValueError("paths must contain between 1 and 100 items.")
        if not all(isinstance(path, str) and path for path in paths):
            raise ValueError("Every media path must be a non-empty string.")
        return
    if action == "close_project":
        if set(arguments) != {"expected_project_id", "confirm_close"}:
            raise ValueError(
                "close_project requires an exact project ID and confirmation."
            )
        identity = arguments["expected_project_id"]
        if (
            not isinstance(identity, str) or not 1 <= len(identity) <= 128
            or identity != identity.strip()
            or any(ord(character) < 32 for character in identity)
            or arguments["confirm_close"] is not True
        ):
            raise ValueError(
                "close_project requires a clean ID and confirm_close=true."
            )
        return
    if action == "create_project":
        if set(arguments) != {"name", "confirm_create"}:
            raise ValueError("create_project requires name and confirm_create.")
        name = arguments["name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name) > 128
            or any(ord(character) < 32 for character in name)
            or arguments["confirm_create"] is not True
        ):
            raise ValueError(
                "A bounded project name and confirm_create=true are required."
            )
        return
    if action == "create_timeline":
        if set(arguments) != {"name"}:
            raise ValueError("create_timeline requires only name.")
        name = arguments["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("Timeline name must contain 1 to 128 characters.")
        return
    if action == "ensure_timeline_tracks":
        expected = {"timeline_id", "video_track_count", "audio_track_count"}
        if set(arguments) != expected:
            raise ValueError(
                "ensure_timeline_tracks requires timeline_id and target counts."
            )
        timeline_id = arguments["timeline_id"]
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError(
                "timeline_id must contain 1 to 128 characters."
            )
        for field in ("video_track_count", "audio_track_count"):
            value = arguments[field]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= 8
            ):
                raise ValueError(f"{field} must be between 1 and 8.")
        return
    if action == "duplicate_timeline":
        if set(arguments) != {"timeline_id", "name"}:
            raise ValueError(
                "duplicate_timeline requires timeline_id and name."
            )
        timeline_id = arguments["timeline_id"]
        name = arguments["name"]
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError(
                "timeline_id must contain 1 to 128 characters."
            )
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("Timeline name must contain 1 to 128 characters.")
        return
    if action == "set_current_timeline":
        if set(arguments) != {"timeline_id"}:
            raise ValueError(
                "set_current_timeline requires only timeline_id."
            )
        timeline_id = arguments["timeline_id"]
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError(
                "timeline_id must contain 1 to 128 characters."
            )
        return
    if action == "append_clip":
        if set(arguments) != {"timeline_id", "asset_id"}:
            raise ValueError("append_clip requires timeline_id and asset_id.")
        for field in ("timeline_id", "asset_id"):
            if not isinstance(arguments[field], str) or not arguments[field]:
                raise ValueError(f"{field} must be a non-empty string.")
        return
    if action == "insert_title":
        if set(arguments) != {
            "timeline_id",
            "title_name",
            "timecode",
            "confirm_insert",
        }:
            raise ValueError("insert_title fields do not match the contract.")
        for field in ("timeline_id", "title_name"):
            value = arguments[field]
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise ValueError(f"{field} must contain 1 to 128 characters.")
        timecode = arguments["timecode"]
        if (
            not isinstance(timecode, str)
            or re.fullmatch(r"\d{2,3}:\d{2}:\d{2}:\d{2}", timecode) is None
        ):
            raise ValueError("timecode must use HH:MM:SS:FF format.")
        if arguments["confirm_insert"] is not True:
            raise ValueError("confirm_insert must be true.")
        return
    if action == "insert_animation_template":
        if set(arguments) != {
            "timeline_id",
            "template_id",
            "timecode",
            "confirm_insert",
        }:
            raise ValueError(
                "insert_animation_template fields do not match the contract."
            )
        timeline_id = arguments["timeline_id"]
        if (
            not isinstance(timeline_id, str)
            or not 1 <= len(timeline_id) <= 128
        ):
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if arguments["template_id"] not in ANIMATION_TEMPLATES:
            raise ValueError("template_id is not an allowlisted animation template.")
        timecode = arguments["timecode"]
        if (
            not isinstance(timecode, str)
            or re.fullmatch(r"\d{2,3}:\d{2}:\d{2}:\d{2}", timecode) is None
        ):
            raise ValueError("timecode must use HH:MM:SS:FF format.")
        if arguments["confirm_insert"] is not True:
            raise ValueError("confirm_insert must be true.")
        return
    if action == "append_subtitle_file":
        expected = {
            "timeline_id",
            "asset_id",
            "subtitle_path",
            "import_idempotency_key",
            "confirm_apply",
        }
        if set(arguments) != expected:
            raise ValueError("append_subtitle_file fields do not match the contract.")
        for field in ("timeline_id", "asset_id", "import_idempotency_key"):
            value = arguments[field]
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise ValueError(f"{field} must contain 1 to 128 characters.")
        subtitle_path = arguments["subtitle_path"]
        if (
            not isinstance(subtitle_path, str)
            or not subtitle_path
            or Path(subtitle_path).suffix.casefold() != ".srt"
        ):
            raise ValueError("subtitle_path must identify one SRT file.")
        if arguments["confirm_apply"] is not True:
            raise ValueError("confirm_apply must be true.")
        return
    if action == "insert_clip":
        expected = {
            "timeline_id",
            "asset_id",
            "source_start_frame",
            "source_end_frame",
            "position_frames",
            "track_type",
            "track_index",
        }
        if set(arguments) != expected:
            raise ValueError("insert_clip fields do not match the contract.")
        for field in ("timeline_id", "asset_id"):
            if not isinstance(arguments[field], str) or not arguments[field]:
                raise ValueError(f"{field} must be a non-empty string.")
        source_start = arguments["source_start_frame"]
        source_end = arguments["source_end_frame"]
        position = arguments["position_frames"]
        track_index = arguments["track_index"]
        if (
            not isinstance(source_start, int)
            or isinstance(source_start, bool)
            or not isinstance(source_end, int)
            or isinstance(source_end, bool)
            or not 0 <= source_start < source_end <= 2_147_483_647
        ):
            raise ValueError(
                "source frames must be ordered within the supported range."
            )
        if (
            not isinstance(position, int)
            or isinstance(position, bool)
            or not 0 <= position <= 2_147_483_647
        ):
            raise ValueError("position_frames must be a non-negative integer.")
        if arguments["track_type"] not in {"video", "audio"}:
            raise ValueError("track_type must be video or audio.")
        if (
            not isinstance(track_index, int)
            or isinstance(track_index, bool)
            or not 1 <= track_index <= 128
        ):
            raise ValueError("track_index must be between 1 and 128.")
        return
    if action == "insert_clips":
        if set(arguments) != {"timeline_id", "placements"}:
            raise ValueError("insert_clips requires timeline_id and placements.")
        timeline_id = arguments["timeline_id"]
        placements = arguments["placements"]
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if not isinstance(placements, list) or not 1 <= len(placements) <= 3003:
            raise ValueError("placements must contain between 1 and 3003 items.")
        for placement in placements:
            if not isinstance(placement, dict):
                raise ValueError("Every placement must be an object.")
            _validate_write_arguments(
                "insert_clip",
                {"timeline_id": timeline_id, **placement},
            )
        return
    if action == "set_clip_enabled":
        if set(arguments) != {"timeline_id", "timeline_item_id", "enabled"}:
            raise ValueError(
                "set_clip_enabled fields do not match the contract."
            )
        for field in ("timeline_id", "timeline_item_id"):
            value = arguments[field]
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 128
            ):
                raise ValueError(
                    f"{field} must contain 1 to 128 characters."
                )
        if not isinstance(arguments["enabled"], bool):
            raise ValueError("enabled must be a boolean.")
        return
    if action == "set_clips_linked":
        if set(arguments) != {
            "timeline_id",
            "timeline_item_ids",
            "linked",
        }:
            raise ValueError(
                "set_clips_linked fields do not match the contract."
            )
        timeline_id = arguments["timeline_id"]
        item_ids = arguments["timeline_item_ids"]
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError(
                "timeline_id must contain 1 to 128 characters."
            )
        if (
            not isinstance(item_ids, list)
            or not 2 <= len(item_ids) <= 16
            or any(
                not isinstance(item_id, str)
                or not item_id
                or len(item_id) > 128
                for item_id in item_ids
            )
            or len(set(item_ids)) != len(item_ids)
        ):
            raise ValueError(
                "timeline_item_ids must contain 2 to 16 unique bounded IDs."
            )
        if not isinstance(arguments["linked"], bool):
            raise ValueError("linked must be a boolean.")
        return
    if action == "set_clip_link_groups":
        if set(arguments) != {"timeline_id", "groups", "linked"}:
            raise ValueError(
                "set_clip_link_groups fields do not match the contract."
            )
        timeline_id = arguments["timeline_id"]
        groups = arguments["groups"]
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if not isinstance(groups, list) or not 1 <= len(groups) <= 1001:
            raise ValueError("groups must contain between 1 and 1001 pairs.")
        flattened: list[str] = []
        for group in groups:
            if not isinstance(group, list) or len(group) != 2:
                raise ValueError("Every link group must contain exactly 2 IDs.")
            _validate_write_arguments(
                "set_clips_linked",
                {
                    "timeline_id": timeline_id,
                    "timeline_item_ids": group,
                    "linked": arguments["linked"],
                },
            )
            flattened.extend(group)
        if len(flattened) != len(set(flattened)):
            raise ValueError("Link group IDs must be unique across the batch.")
        return
    if action == "set_clip_transform":
        required = {"timeline_id", "timeline_item_id"}
        transform_fields = {
            "position_x",
            "position_y",
            "zoom",
            "rotation_degrees",
            "opacity_percent",
        }
        if (
            not required.issubset(arguments)
            or not set(arguments).issubset(required | transform_fields)
            or not set(arguments).intersection(transform_fields)
        ):
            raise ValueError(
                "set_clip_transform requires IDs and at least one "
                "allowlisted transform field."
            )
        for field in required:
            value = arguments[field]
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 128
            ):
                raise ValueError(
                    f"{field} must contain 1 to 128 characters."
                )
        numeric_ranges = {
            "position_x": (-32_768.0, 32_768.0),
            "position_y": (-32_768.0, 32_768.0),
            "zoom": (0.0, 100.0),
            "rotation_degrees": (-360.0, 360.0),
            "opacity_percent": (0.0, 100.0),
        }
        for field, (minimum, maximum) in numeric_ranges.items():
            if field not in arguments:
                continue
            value = arguments[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not minimum <= value <= maximum
            ):
                raise ValueError(
                    f"{field} must be a finite number from "
                    f"{minimum} to {maximum}."
                )
        return
    if action == "set_clip_transforms":
        if set(arguments) != {"timeline_id", "items"}:
            raise ValueError(
                "set_clip_transforms requires timeline_id and items."
            )
        timeline_id = arguments["timeline_id"]
        items = arguments["items"]
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if not isinstance(items, list) or not 1 <= len(items) <= 1001:
            raise ValueError("items must contain between 1 and 1001 transforms.")
        transform_item_ids: list[str] = []
        for item in items:
            if not isinstance(item, dict) or "timeline_id" in item:
                raise ValueError("Every transform item must be an object.")
            _validate_write_arguments(
                "set_clip_transform",
                {"timeline_id": timeline_id, **item},
            )
            transform_item_ids.append(item["timeline_item_id"])
        if len(transform_item_ids) != len(set(transform_item_ids)):
            raise ValueError("Transform item IDs must be unique across the batch.")
        return
    if action == "delete_clip":
        if set(arguments) != {
            "timeline_id",
            "timeline_item_id",
            "confirm_delete",
        }:
            raise ValueError("delete_clip fields do not match the contract.")
        for field in ("timeline_id", "timeline_item_id"):
            value = arguments[field]
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 128
            ):
                raise ValueError(
                    f"{field} must contain 1 to 128 characters."
                )
        if arguments["confirm_delete"] is not True:
            raise ValueError("confirm_delete must be true.")
        return
    if action == "add_marker":
        expected = {
            "timeline_id",
            "frame",
            "color",
            "name",
            "note",
            "duration",
        }
        if set(arguments) != expected:
            raise ValueError("add_marker fields do not match the contract.")
        if not isinstance(arguments["timeline_id"], str) or not arguments[
            "timeline_id"
        ]:
            raise ValueError("timeline_id must be a non-empty string.")
        if (
            not isinstance(arguments["frame"], int)
            or arguments["frame"] < 0
        ):
            raise ValueError("frame must be a non-negative integer.")
        if not isinstance(arguments["color"], str) or not arguments["color"]:
            raise ValueError("color must be a non-empty string.")
        if not isinstance(arguments["name"], str):
            raise ValueError("name must be a string.")
        if not isinstance(arguments["note"], str):
            raise ValueError("note must be a string.")
        if (
            not isinstance(arguments["duration"], int)
            or arguments["duration"] < 1
        ):
            raise ValueError("duration must be a positive integer.")
        return
    if action == "create_subtitles_from_audio":
        if set(arguments) != {"timeline_id", "confirm_create"}:
            raise ValueError(
                "create_subtitles_from_audio requires timeline_id and "
                "confirm_create."
            )
        timeline_id = arguments["timeline_id"]
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if arguments["confirm_create"] is not True:
            raise ValueError("confirm_create must be true.")
        return
    if action == "prepare_render_job":
        if set(arguments) not in (
            {"custom_name"},
            {"custom_name", "profile"},
            {"custom_name", "timeline_id"},
            {"custom_name", "timeline_id", "profile"},
        ):
            raise ValueError(
                "prepare_render_job requires custom_name and optional "
                "timeline_id/profile."
            )
        _validate_render_name(arguments["custom_name"])
        timeline_id = arguments.get("timeline_id")
        if timeline_id is not None and (
            not isinstance(timeline_id, str)
            or not timeline_id
            or len(timeline_id) > 128
        ):
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        _validate_render_profile(
            arguments.get("profile", DEFAULT_RENDER_PROFILE)
        )
        return
    if action == "start_render_job":
        _validate_render_job_arguments(action, arguments)
        return
    raise ValueError("Unsupported write action.")


def _validate_render_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("custom_name must be a string.")
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("custom_name must contain 1 to 128 characters.")
    if normalized in {".", ".."}:
        raise ValueError("custom_name must be a filename stem.")
    forbidden = set('<>:"/\\|?*')
    if any(character in forbidden or ord(character) < 32 for character in normalized):
        raise ValueError("custom_name contains an invalid filename character.")
    if normalized.endswith((".", " ")):
        raise ValueError("custom_name must not end with a dot or space.")
    return normalized


def _validate_render_job_arguments(
    action: str,
    arguments: dict[str, Any],
) -> str:
    if set(arguments) != {"job_id"}:
        raise ValueError(f"{action} requires only job_id.")
    job_id = arguments["job_id"]
    if (
        not isinstance(job_id, str)
        or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", job_id) is None
    ):
        raise ValueError(
            "job_id must contain 1 to 128 safe identifier characters."
        )
    return job_id


def _validate_render_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or value not in RENDER_PROFILES:
        raise ValueError(
            "profile must be one of: "
            + ", ".join(sorted(RENDER_PROFILES))
            + "."
        )
    return RENDER_PROFILES[value]


def _render_output_directory() -> Path:
    output = (_managed_media_root() / "renders").resolve()
    output.mkdir(parents=True, exist_ok=True)
    _ensure_output_capacity(output)
    return output


def _audio_source_output_directory() -> Path:
    output = (_managed_media_root() / "audio-sources").resolve()
    output.mkdir(parents=True, exist_ok=True)
    _ensure_output_capacity(output)
    return output


def command_result(
    action: str,
    state: dict[str, Any],
    resolve: Any | None = None,
    arguments: dict[str, Any] | None = None,
) -> Any:
    """Return a read-only result from the collected snapshot."""
    if action == "ping":
        return {"message": "pong"}
    if action == "stop_bridge":
        return {"status": "stopping"}
    if action == "get_bridge_info":
        return {
            key: state[key]
            for key in (
                "bridge_version",
                "protocol_version",
                "product_name",
                "resolve_version",
                "edition",
                "last_heartbeat",
            )
        }
    if action == "get_capabilities":
        return state["capabilities"]
    if action == "get_current_project":
        name = state["project_name"]
        return None if name is None else {"name": name}
    if action == "list_timelines":
        return state["timelines"]
    if action == "get_current_timeline":
        timeline_id = state["current_timeline_id"]
        name = state["current_timeline_name"]
        return (
            None
            if name is None or timeline_id is None
            else {"timeline_id": timeline_id, "name": name}
        )
    if action == "list_timeline_items":
        if resolve is None:
            raise BridgeOperationError(
                "RESOLVE_CONTEXT_REQUIRED",
                "A live Resolve context is required for timeline item discovery.",
                retryable=True,
            )
        timeline_id = _validate_list_timeline_items_arguments(
            arguments or {}
        )
        return _list_timeline_items(resolve, timeline_id)
    if action == "list_media_pool_items":
        if resolve is None:
            raise BridgeOperationError(
                "RESOLVE_CONTEXT_REQUIRED",
                "A live Resolve context is required for Media Pool discovery.",
                retryable=True,
            )
        return _list_media_pool_items(resolve)
    if action == "get_editing_metadata":
        if resolve is None:
            raise BridgeOperationError(
                "RESOLVE_CONTEXT_REQUIRED",
                "A live Resolve context is required for media metadata discovery.",
                retryable=True,
            )
        timeline_id, asset_ids = _validate_editing_metadata_arguments(
            arguments or {}
        )
        return _editing_metadata(resolve, timeline_id, asset_ids)
    if action == "get_subtitle_environment":
        if resolve is None:
            raise BridgeOperationError(
                "RESOLVE_CONTEXT_REQUIRED",
                "A live Resolve context is required for subtitle discovery.",
                retryable=True,
            )
        timeline_id = _validate_list_timeline_items_arguments(
            arguments or {}
        )
        return _subtitle_environment(resolve, timeline_id)
    if action == "get_animation_template_environment":
        if resolve is None:
            raise BridgeOperationError(
                "RESOLVE_CONTEXT_REQUIRED",
                "A live Resolve context is required for animation-template "
                "discovery.",
                retryable=True,
            )
        timeline_id = _validate_list_timeline_items_arguments(arguments or {})
        return _animation_template_environment(resolve, timeline_id)
    if action == "get_color_environment":
        if resolve is None:
            raise BridgeOperationError(
                "RESOLVE_CONTEXT_REQUIRED",
                "A live Resolve context is required for color discovery.",
                retryable=True,
            )
        timeline_id = _validate_list_timeline_items_arguments(arguments or {})
        return _color_environment(resolve, timeline_id)
    if action == "get_workspace_snapshot":
        if resolve is None:
            raise BridgeOperationError(
                "RESOLVE_CONTEXT_REQUIRED",
                "A live Resolve context is required for workspace discovery.",
                retryable=True,
            )
        return _workspace_snapshot(resolve, state)
    if action == "get_render_environment":
        if resolve is None:
            raise BridgeOperationError(
                "RESOLVE_CONTEXT_REQUIRED",
                "A live Resolve context is required for render discovery.",
                retryable=True,
            )
        return _render_environment(resolve)
    if action == "get_render_job_status":
        if resolve is None:
            raise BridgeOperationError(
                "RESOLVE_CONTEXT_REQUIRED",
                "A live Resolve context is required for render status.",
                retryable=True,
            )
        job_id = _validate_render_job_arguments(action, arguments or {})
        return _render_job_status(resolve, job_id)
    raise ValueError("Unsupported or unsafe bridge action.")


def _animation_template_path(template: dict[str, str]) -> Path:
    """Return one fixed Resolve user-template path without exposing it."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise BridgeOperationError(
            "APPDATA_UNAVAILABLE",
            "APPDATA is not set inside Resolve.",
        )
    path = (
        Path(appdata)
        / "Blackmagic Design"
        / "DaVinci Resolve"
        / "Support"
        / "Fusion"
        / "Templates"
    )
    for part in template["relative_path"].split("/"):
        path /= part
    return path


def _animation_template_environment(
    resolve: Any,
    timeline_id: str,
) -> dict[str, Any]:
    """Inspect documented Fusion-title methods and packaged file integrity."""
    _, project, _ = _require_project(resolve)
    timeline = _find_timeline(project, timeline_id)
    method_names = (
        "InsertFusionTitleIntoTimeline",
        "GetCurrentTimecode",
        "SetCurrentTimecode",
        "GetEndFrame",
        "GetSetting",
        "GetTrackCount",
        "GetItemListInTrack",
    )
    methods = {
        name: callable(getattr(timeline, name, None)) for name in method_names
    }
    methods["SetCurrentTimeline"] = callable(
        getattr(project, "SetCurrentTimeline", None)
    )
    templates: list[dict[str, Any]] = []
    for template_id, template in ANIMATION_TEMPLATES.items():
        path = _animation_template_path(template)
        installed = path.is_file()
        actual_hash: str | None = None
        if installed:
            try:
                actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                raise BridgeOperationError(
                    "ANIMATION_TEMPLATE_UNREADABLE",
                    "The installed animation template could not be read.",
                    details={"template_id": template_id},
                ) from error
        templates.append(
            {
                "template_id": template_id,
                "resolve_name": template["resolve_name"],
                "installed": installed,
                "sha256_matches": actual_hash == template["sha256"],
            }
        )
    ready = all(methods.values()) and all(
        item["installed"] and item["sha256_matches"] for item in templates
    )
    return {
        "timeline": {
            "timeline_id": str(timeline.GetUniqueId()),
            "name": str(timeline.GetName()),
        },
        "methods": methods,
        "templates": templates,
        "ready": ready,
    }


def _color_environment(resolve: Any, timeline_id: str) -> dict[str, Any]:
    """Inspect documented color versions and node graphs without writes."""
    _, project, _ = _require_project(resolve)
    timeline = _find_timeline(project, timeline_id)
    snapshot = _list_timeline_items(resolve, timeline_id)
    color_items: list[dict[str, Any]] = []
    read_method_names = (
        "GetCurrentVersion",
        "GetVersionNameList",
        "GetNodeGraph",
    )
    write_method_names = (
        "SetCDL",
        "AddVersion",
        "LoadVersionByName",
    )
    methods: dict[str, bool] = {
        name: True for name in (*read_method_names, *write_method_names)
    }
    methods.update(
        {
            "Graph.GetNumNodes": True,
            "Graph.GetNodeLabel": True,
            "Graph.GetLUT": True,
        }
    )
    for metadata in snapshot["items"]:
        if (
            metadata["track_type"] != "video"
            or metadata["source_type"] != "media"
        ):
            continue
        item = _find_timeline_item(timeline, metadata["timeline_item_id"])
        for name in (*read_method_names, *write_method_names):
            methods[name] = methods[name] and callable(getattr(item, name, None))
        missing_read = [
            name
            for name in read_method_names
            if not callable(getattr(item, name, None))
        ]
        if missing_read:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "A media video item cannot report its color environment.",
                details={
                    "timeline_item_id": metadata["timeline_item_id"],
                    "missing_methods": missing_read,
                },
            )
        current_version = item.GetCurrentVersion()
        local_versions = item.GetVersionNameList(0)
        graph = item.GetNodeGraph()
        if (
            not isinstance(current_version, dict)
            or not isinstance(current_version.get("versionName"), str)
            or not current_version["versionName"]
            or current_version.get("versionType") not in (0, 1)
            or not isinstance(local_versions, list)
            or any(not isinstance(name, str) or not name for name in local_versions)
            or graph is None
        ):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Resolve returned invalid color version or graph metadata.",
                details={"timeline_item_id": metadata["timeline_item_id"]},
            )
        graph_methods = {
            "Graph.GetNumNodes": getattr(graph, "GetNumNodes", None),
            "Graph.GetNodeLabel": getattr(graph, "GetNodeLabel", None),
            "Graph.GetLUT": getattr(graph, "GetLUT", None),
        }
        for name, method in graph_methods.items():
            methods[name] = methods[name] and callable(method)
        missing_graph = [
            name for name, method in graph_methods.items() if not callable(method)
        ]
        if missing_graph:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "A media video item graph cannot report bounded node metadata.",
                details={
                    "timeline_item_id": metadata["timeline_item_id"],
                    "missing_methods": missing_graph,
                },
            )
        node_count = graph.GetNumNodes()
        if (
            not isinstance(node_count, int)
            or isinstance(node_count, bool)
            or node_count < 1
        ):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Graph.GetNumNodes() returned an invalid value.",
                details={"timeline_item_id": metadata["timeline_item_id"]},
            )
        nodes: list[dict[str, Any]] = []
        for node_index in range(1, node_count + 1):
            label = graph.GetNodeLabel(node_index)
            lut = graph.GetLUT(node_index)
            if label is None:
                label = ""
            if lut is None:
                lut = ""
            if not isinstance(label, str) or not isinstance(lut, str):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Resolve returned invalid color node metadata.",
                    details={
                        "timeline_item_id": metadata["timeline_item_id"],
                        "node_index": node_index,
                    },
                )
            nodes.append({"index": node_index, "label": label, "lut": lut})
        color_items.append(
            {
                **metadata,
                "current_version": {
                    "versionName": current_version["versionName"],
                    "versionType": int(current_version["versionType"]),
                },
                "local_versions": local_versions,
                "node_count": node_count,
                "nodes": nodes,
            }
        )
    if not color_items:
        raise BridgeOperationError(
            "COLOR_MEDIA_REQUIRED",
            "The timeline has no media video items for color discovery.",
        )
    read_ready = all(methods[name] for name in (*read_method_names, *graph_methods))
    apply_candidate = read_ready and all(
        methods[name] for name in write_method_names
    )
    return {
        "timeline": {
            "timeline_id": str(timeline.GetUniqueId()),
            "name": str(timeline.GetName()),
        },
        "methods": methods,
        "items": color_items,
        "ready": read_ready,
        "apply_candidate": apply_candidate,
    }


def _prepare_color_preset_items(
    resolve: Any,
    timeline: Any,
    arguments: dict[str, Any],
) -> list[tuple[dict[str, Any], Any]]:
    """Validate exact media targets and documented color methods before backup."""
    snapshot = _list_timeline_items(resolve, arguments["timeline_id"])
    media_items = {
        item["timeline_item_id"]: item
        for item in snapshot["items"]
        if item["track_type"] == "video" and item["source_type"] == "media"
    }
    requested_ids = set(arguments["timeline_item_ids"])
    if requested_ids != set(media_items):
        raise BridgeOperationError(
            "COLOR_TARGET_MISMATCH",
            "Color apply must target every and only media video item.",
            details={
                "expected_item_ids": sorted(media_items),
                "requested_item_ids": sorted(requested_ids),
            },
        )
    get_locked = getattr(timeline, "GetIsTrackLocked", None)
    if not callable(get_locked):
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The timeline cannot report video track lock state.",
            details={"missing_methods": ["GetIsTrackLocked"]},
        )
    prepared: list[tuple[dict[str, Any], Any]] = []
    required = (
        "GetCurrentVersion",
        "GetVersionNameList",
        "GetNodeGraph",
        "AddVersion",
        "LoadVersionByName",
        "SetCDL",
    )
    for item_id in arguments["timeline_item_ids"]:
        metadata = media_items[item_id]
        item = _find_timeline_item(timeline, item_id)
        missing = [
            name for name in required if not callable(getattr(item, name, None))
        ]
        if missing:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "A media video item cannot apply the fixed CDL preset.",
                details={"timeline_item_id": item_id, "missing_methods": missing},
            )
        if get_locked("video", metadata["track_index"]) is True:
            raise BridgeOperationError(
                "TIMELINE_TRACK_LOCKED",
                "A color target is on a locked video track.",
                details={
                    "timeline_item_id": item_id,
                    "track_index": metadata["track_index"],
                },
            )
        graph = item.GetNodeGraph()
        get_num_nodes = getattr(graph, "GetNumNodes", None)
        if not callable(get_num_nodes):
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "A media video item cannot report its color nodes.",
                details={"timeline_item_id": item_id},
            )
        node_count = get_num_nodes()
        if (
            not isinstance(node_count, int)
            or isinstance(node_count, bool)
            or node_count < 1
        ):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "The color graph has no valid node 1.",
                details={"timeline_item_id": item_id},
            )
        prepared.append((metadata, item))
    return prepared


def _apply_color_preset_items(
    arguments: dict[str, Any],
    prepared: list[tuple[dict[str, Any], Any]],
) -> list[dict[str, Any]]:
    """Apply one code-owned CDL map on a new local version per media item."""
    preset = COLOR_PRESETS[arguments["preset_id"]]
    version_name = preset["version_name"]
    version_type = preset["version_type"]
    cdl = cast(dict[str, str], preset["cdl"])
    results: list[dict[str, Any]] = []
    for metadata, item in prepared:
        versions = item.GetVersionNameList(version_type)
        if not isinstance(versions, list):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Resolve returned an invalid local color-version list.",
                details={"timeline_item_id": metadata["timeline_item_id"]},
            )
        version_created = version_name not in versions
        if version_created and item.AddVersion(version_name, version_type) is not True:
            raise BridgeOperationError(
                "COLOR_VERSION_CREATE_FAILED",
                "Resolve did not create the managed local color version.",
                details={"timeline_item_id": metadata["timeline_item_id"]},
            )
        if item.LoadVersionByName(version_name, version_type) is not True:
            raise BridgeOperationError(
                "COLOR_VERSION_LOAD_FAILED",
                "Resolve did not activate the managed local color version.",
                details={"timeline_item_id": metadata["timeline_item_id"]},
            )
        if item.SetCDL(dict(cdl)) is not True:
            raise BridgeOperationError(
                "COLOR_CDL_APPLY_FAILED",
                "Resolve did not apply the fixed CDL preset.",
                details={"timeline_item_id": metadata["timeline_item_id"]},
            )
        current = item.GetCurrentVersion()
        graph = item.GetNodeGraph()
        node_count = graph.GetNumNodes() if graph is not None else None
        if (
            not isinstance(current, dict)
            or current.get("versionName") != version_name
            or current.get("versionType") != version_type
            or not isinstance(node_count, int)
            or node_count < 1
        ):
            raise BridgeOperationError(
                "COLOR_CDL_READBACK_FAILED",
                "Resolve did not report the managed version and node 1.",
                details={"timeline_item_id": metadata["timeline_item_id"]},
            )
        results.append(
            {
                "timeline_item_id": metadata["timeline_item_id"],
                "name": metadata["name"],
                "track_index": metadata["track_index"],
                "version_name": version_name,
                "version_type": version_type,
                "version_created": version_created,
                "node_index": 1,
                "cdl": dict(cdl),
            }
        )
    return results


def _workspace_snapshot(
    resolve: Any,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Collect the fixed read-only workspace view in one bridge command."""
    project_name = state["project_name"]
    if project_name is None:
        raise BridgeOperationError(
            "PROJECT_NOT_OPEN",
            "Open a Resolve project before collecting a workspace snapshot.",
            retryable=True,
        )
    timeline_id = state["current_timeline_id"]
    timeline_name = state["current_timeline_name"]
    current_timeline = (
        None
        if timeline_id is None or timeline_name is None
        else {"timeline_id": timeline_id, "name": timeline_name}
    )
    timeline_items = (
        None
        if timeline_id is None
        else _list_timeline_items(resolve, timeline_id)
    )
    return {
        "bridge": {
            key: state[key]
            for key in (
                "bridge_version",
                "protocol_version",
                "product_name",
                "resolve_version",
                "edition",
                "last_heartbeat",
            )
        },
        "project": {"name": project_name},
        "timelines": state["timelines"],
        "current_timeline": current_timeline,
        "timeline_items": timeline_items,
        "media_pool": _list_media_pool_items(resolve),
        "render": _render_environment(resolve),
    }


def _list_media_pool_items(resolve: Any) -> dict[str, Any]:
    _, _, media_pool = _require_project(resolve)
    get_root_folder = getattr(media_pool, "GetRootFolder", None)
    if not callable(get_root_folder):
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The media pool cannot report its root folder.",
            details={"missing_methods": ["GetRootFolder"]},
        )
    root = get_root_folder()
    if root is None:
        raise BridgeOperationError(
            "MEDIA_POOL_ROOT_UNAVAILABLE",
            "Resolve MediaPool returned no root folder.",
            retryable=True,
        )

    media_pool_items: list[dict[str, Any]] = []
    visited_folder_ids: set[str] = set()
    pending: list[tuple[Any, tuple[str, ...]]] = [(root, ())]
    while pending:
        if len(visited_folder_ids) >= 1_000:
            raise BridgeOperationError(
                "MEDIA_POOL_LIMIT_EXCEEDED",
                "Media Pool folder discovery exceeded 1000 folders.",
            )
        folder, parent_path = pending.pop(0)
        required_folder_methods = (
            "GetUniqueId",
            "GetName",
            "GetClipList",
            "GetSubFolderList",
        )
        missing = [
            name
            for name in required_folder_methods
            if not callable(getattr(folder, name, None))
        ]
        if missing:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "A Media Pool folder cannot report bounded metadata.",
                details={"missing_methods": missing},
            )
        folder_id = str(folder.GetUniqueId())
        if not folder_id or len(folder_id) > 128:
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Media Pool folder ID is invalid.",
            )
        if folder_id in visited_folder_ids:
            continue
        visited_folder_ids.add(folder_id)
        folder_name = str(folder.GetName())
        folder_path = (*parent_path, folder_name)
        clips = folder.GetClipList()
        subfolders = folder.GetSubFolderList()
        if not isinstance(clips, list) or not isinstance(subfolders, list):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Media Pool folder lists are invalid.",
                details={"folder_id": folder_id},
            )
        for clip in clips:
            if len(media_pool_items) >= 10_000:
                raise BridgeOperationError(
                    "MEDIA_POOL_LIMIT_EXCEEDED",
                    "Media Pool discovery exceeded 10000 items.",
                )
            get_media_id = getattr(clip, "GetMediaId", None)
            get_name = getattr(clip, "GetName", None)
            if not callable(get_media_id) or not callable(get_name):
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "A media asset cannot report identity metadata.",
                    details={"missing_methods": ["GetMediaId", "GetName"]},
                )
            asset_id = str(get_media_id())
            if not asset_id or len(asset_id) > 128:
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "MediaPoolItem.GetMediaId() returned an invalid value.",
                )
            media_pool_items.append(
                {
                    "asset_id": asset_id,
                    "name": str(get_name()),
                    "folder_id": folder_id,
                    "folder_path": list(folder_path),
                }
            )
        child_entries = [
            (child, str(child.GetName()))
            for child in subfolders
            if callable(getattr(child, "GetName", None))
        ]
        if len(child_entries) != len(subfolders):
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "A Media Pool subfolder cannot report its name.",
                details={"missing_methods": ["GetName"]},
            )
        for child, _ in sorted(child_entries, key=lambda entry: entry[1]):
            pending.append((child, folder_path))

    return {
        "items": sorted(
            media_pool_items,
            key=lambda item: (
                item["folder_path"],
                item["name"],
                item["asset_id"],
            ),
        ),
        "folder_count": len(visited_folder_ids),
    }


def _editing_metadata(
    resolve: Any,
    timeline_id: str,
    asset_ids: list[str],
) -> dict[str, Any]:
    """Return only placement-relevant documented Resolve metadata.

    `MediaPoolItem.GetClipProperty` is intentionally narrowed to Frames and
    FPS. Raw property snapshots may expose machine-specific paths and are not
    part of the MCP contract.
    """
    _, project, media_pool = _require_project(resolve)
    timeline = _find_timeline(project, timeline_id)
    get_track_count = getattr(timeline, "GetTrackCount", None)
    get_setting = getattr(timeline, "GetSetting", None)
    missing_timeline_methods = [
        name
        for name, method in (
            ("GetTrackCount", get_track_count),
            ("GetSetting", get_setting),
        )
        if not callable(method)
    ]
    if missing_timeline_methods:
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The timeline cannot report placement metadata.",
            details={"missing_methods": missing_timeline_methods},
        )
    get_track_count_call = cast(Callable[[str], Any], get_track_count)
    get_setting_call = cast(Callable[[str], Any], get_setting)
    tracks: dict[str, int] = {}
    for track_type in ("video", "audio"):
        count = get_track_count_call(track_type)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Timeline.GetTrackCount() returned an invalid value.",
                details={"track_type": track_type},
            )
        tracks[track_type] = count
    try:
        timeline_frame_rate = _positive_frame_rate_setting(
            get_setting_call("timelineFrameRate")
        )
        timeline_width = _positive_integer_setting(
            get_setting_call("timelineResolutionWidth")
        )
        timeline_height = _positive_integer_setting(
            get_setting_call("timelineResolutionHeight")
        )
    except ValueError:
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "Timeline.GetSetting() returned invalid bounded editing metadata.",
        ) from None

    timeline_metadata = {
        "timeline_id": timeline_id,
        "name": str(timeline.GetName()),
        "video_track_count": tracks["video"],
        "audio_track_count": tracks["audio"],
        "frame_rate": timeline_frame_rate,
        "resolution_width": timeline_width,
        "resolution_height": timeline_height,
    }
    requested = set(asset_ids)
    if not requested:
        return {"timeline": timeline_metadata, "assets": []}
    found: dict[str, Any] = {}
    get_root_folder = getattr(media_pool, "GetRootFolder", None)
    if not callable(get_root_folder):
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The media pool cannot report its root folder.",
            details={"missing_methods": ["GetRootFolder"]},
        )
    root = get_root_folder()
    if root is None:
        raise BridgeOperationError(
            "MEDIA_POOL_ROOT_UNAVAILABLE",
            "Resolve MediaPool returned no root folder.",
            retryable=True,
        )
    pending = [root]
    visited: set[str] = set()
    while pending and len(found) < len(requested):
        if len(visited) >= 1_000:
            raise BridgeOperationError(
                "MEDIA_POOL_LIMIT_EXCEEDED",
                "Media Pool metadata discovery exceeded 1000 folders.",
            )
        folder = pending.pop(0)
        required = ("GetUniqueId", "GetClipList", "GetSubFolderList")
        missing = [
            name
            for name in required
            if not callable(getattr(folder, name, None))
        ]
        if missing:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "A Media Pool folder cannot enumerate requested assets.",
                details={"missing_methods": missing},
            )
        folder_id = str(folder.GetUniqueId())
        if folder_id in visited:
            continue
        visited.add(folder_id)
        clips = folder.GetClipList()
        children = folder.GetSubFolderList()
        if not isinstance(clips, list) or not isinstance(children, list):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Media Pool folder lists are invalid.",
            )
        for clip in clips:
            get_media_id = getattr(clip, "GetMediaId", None)
            if not callable(get_media_id):
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "A Media Pool item cannot report its canonical ID.",
                    details={"missing_methods": ["GetMediaId"]},
                )
            asset_id = str(get_media_id())
            if asset_id in requested:
                found[asset_id] = clip
        pending.extend(children)

    missing_assets = sorted(requested - set(found))
    if missing_assets:
        raise BridgeOperationError(
            "MEDIA_ASSET_NOT_FOUND",
            "One or more requested Media Pool assets are unavailable.",
            details={"asset_ids": missing_assets},
        )

    assets: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        clip = found[asset_id]
        get_name = getattr(clip, "GetName", None)
        get_property = getattr(clip, "GetClipProperty", None)
        if not callable(get_name) or not callable(get_property):
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "A Media Pool item cannot report placement metadata.",
                details={"missing_methods": ["GetName", "GetClipProperty"]},
            )
        frames_value = get_property("Frames")
        frame_rate_value = get_property("FPS")
        try:
            duration_frames = _positive_integer_property(frames_value)
            frame_rate = _positive_number_property(frame_rate_value)
        except ValueError:
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "MediaPoolItem.GetClipProperty() returned invalid Frames or FPS.",
                details={
                    "asset_id": asset_id,
                    "frames_type": type(frames_value).__name__,
                    "fps_type": type(frame_rate_value).__name__,
                },
            ) from None
        assets.append(
            {
                "asset_id": asset_id,
                "name": str(get_name()),
                "duration_frames": duration_frames,
                "frame_rate": frame_rate,
            }
        )
    return {
        "timeline": timeline_metadata,
        "assets": assets,
    }


def _positive_number_property(value: Any) -> float:
    """Normalize a finite positive numeric Resolve clip property."""
    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric clip property.")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError as error:
            raise ValueError("Clip property is not numeric.") from error
    else:
        raise ValueError("Clip property is not numeric.")
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Clip property must be finite and positive.")
    return number


def _positive_frame_rate_setting(value: Any) -> float:
    """Normalize a documented Resolve timeline frame-rate setting."""
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.upper().endswith(" DF"):
            normalized = normalized[:-3].strip()
        value = normalized
    return _positive_number_property(value)


def _positive_integer_setting(value: Any) -> int:
    """Normalize a documented positive whole-number timeline setting."""
    return _positive_integer_property(value)


def _positive_integer_property(value: Any) -> int:
    """Normalize a finite positive whole-frame Resolve clip property."""
    number = _positive_number_property(value)
    if not number.is_integer():
        raise ValueError("Frame count must be a whole number.")
    return int(number)


def _list_timeline_items(
    resolve: Any,
    timeline_id: str,
) -> dict[str, Any]:
    _, project, _ = _require_project(resolve)
    timeline = _find_timeline(project, timeline_id)
    required_timeline_methods = ("GetTrackCount", "GetItemListInTrack")
    missing = [
        name
        for name in required_timeline_methods
        if not callable(getattr(timeline, name, None))
    ]
    if missing:
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The timeline cannot enumerate video and audio items.",
            details={"missing_methods": missing},
        )

    discovered: list[dict[str, Any]] = []
    item_methods = (
        "GetUniqueId",
        "GetName",
        "GetDuration",
        "GetStart",
        "GetEnd",
        "GetSourceStartFrame",
        "GetSourceEndFrame",
        "GetMediaPoolItem",
        "GetTrackTypeAndIndex",
    )
    for track_type in ("video", "audio"):
        track_count = timeline.GetTrackCount(track_type)
        if (
            not isinstance(track_count, int)
            or isinstance(track_count, bool)
            or track_count < 0
        ):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Timeline.GetTrackCount() returned an invalid value.",
                details={"track_type": track_type},
            )
        for track_index in range(1, track_count + 1):
            items = timeline.GetItemListInTrack(track_type, track_index)
            if not isinstance(items, list):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Timeline.GetItemListInTrack() returned an invalid value.",
                    details={
                        "track_type": track_type,
                        "track_index": track_index,
                    },
                )
            for item in items:
                missing_item_methods = [
                    name
                    for name in item_methods
                    if not callable(getattr(item, name, None))
                ]
                if missing_item_methods:
                    raise BridgeOperationError(
                        "UNSUPPORTED_CAPABILITY",
                        "A timeline item cannot report bounded metadata.",
                        details={"missing_methods": missing_item_methods},
                    )
                actual_track = item.GetTrackTypeAndIndex()
                if actual_track not in (
                    [track_type, track_index],
                    (track_type, track_index),
                ):
                    raise BridgeOperationError(
                        "INVALID_RESOLVE_RESPONSE",
                        "TimelineItem track readback does not match enumeration.",
                    )
                timeline_values = {
                    "duration_frames": item.GetDuration(False),
                    "timeline_start_frame": item.GetStart(False),
                    "timeline_end_frame": item.GetEnd(False),
                }
                if any(
                    not isinstance(value, int) or isinstance(value, bool)
                    for value in timeline_values.values()
                ):
                    raise BridgeOperationError(
                        "INVALID_RESOLVE_RESPONSE",
                        "TimelineItem timeline-frame readback is invalid.",
                    )
                media_pool_item = item.GetMediaPoolItem()
                source_start = item.GetSourceStartFrame()
                source_end = item.GetSourceEndFrame()
                if media_pool_item is None:
                    source_type = "generated"
                    source_values: dict[str, int | None] = {
                        "source_start_frame": None,
                        "source_end_frame": None,
                    }
                elif all(
                    isinstance(value, int) and not isinstance(value, bool)
                    for value in (source_start, source_end)
                ):
                    source_type = "media"
                    source_values = {
                        "source_start_frame": int(source_start),
                        "source_end_frame": int(source_end),
                    }
                else:
                    raise BridgeOperationError(
                        "INVALID_RESOLVE_RESPONSE",
                        "Media TimelineItem source-frame readback is invalid.",
                    )
                discovered.append(
                    {
                        "timeline_item_id": str(item.GetUniqueId()),
                        "name": str(item.GetName()),
                        "track_type": track_type,
                        "track_index": track_index,
                        "source_type": source_type,
                        **timeline_values,
                        **source_values,
                    }
                )
    return {
        "timeline_id": timeline_id,
        "name": str(timeline.GetName()),
        "items": discovered,
    }


def _video_item_snapshots(timeline: Any) -> dict[str, dict[str, int]]:
    """Return strict identity and timeline bounds for every video item."""
    track_count = timeline.GetTrackCount("video")
    if not isinstance(track_count, int) or isinstance(track_count, bool):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "Timeline.GetTrackCount() returned an invalid video count.",
        )
    snapshots: dict[str, dict[str, int]] = {}
    for track_index in range(1, track_count + 1):
        items = timeline.GetItemListInTrack("video", track_index)
        if not isinstance(items, list):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Timeline.GetItemListInTrack() returned an invalid value.",
            )
        for item in items:
            required = ("GetUniqueId", "GetStart", "GetEnd", "GetDuration")
            missing = [
                name
                for name in required
                if not callable(getattr(item, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "A video item cannot report title-safety bounds.",
                    details={"missing_methods": missing},
                )
            values = {
                "timeline_start_frame": item.GetStart(False),
                "timeline_end_frame": item.GetEnd(False),
                "duration_frames": item.GetDuration(False),
                "track_index": track_index,
            }
            if any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in values.values()
            ):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "A video item returned invalid title-safety bounds.",
                )
            item_id = str(item.GetUniqueId())
            if not item_id or item_id in snapshots:
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Video item identity is empty or duplicated.",
                )
            snapshots[item_id] = values
    return snapshots


def _non_drop_timecode_frame(timecode: str, frame_rate: float) -> int:
    """Convert bounded non-drop timecode to an absolute timeline frame."""
    rounded_rate = round(frame_rate)
    if not math.isclose(frame_rate, rounded_rate, rel_tol=0.0, abs_tol=1e-9):
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "Safe standard-title placement currently requires integer FPS.",
            details={"timeline_frame_rate": frame_rate},
        )
    hours, minutes, seconds, frames = (int(part) for part in timecode.split(":"))
    if minutes >= 60 or seconds >= 60 or frames >= rounded_rate:
        raise BridgeOperationError(
            "TIMECODE_INVALID",
            "The requested title timecode is invalid for the timeline FPS.",
            details={"timecode": timecode, "timeline_frame_rate": frame_rate},
        )
    return ((hours * 60 + minutes) * 60 + seconds) * rounded_rate + frames


def _subtitle_environment(
    resolve: Any,
    timeline_id: str,
) -> dict[str, Any]:
    """Return bounded subtitle items and documented auto-caption surface."""
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        raise BridgeOperationError(
            "PROJECT_MANAGER_UNAVAILABLE",
            "Resolve.GetProjectManager() returned no object.",
            retryable=True,
        )
    project = project_manager.GetCurrentProject()
    if project is None:
        raise BridgeOperationError(
            "PROJECT_NOT_OPEN",
            "Open a Resolve project before subtitle discovery.",
            retryable=True,
        )
    timeline = _find_timeline(project, timeline_id)
    required_timeline_methods = (
        "GetTrackCount",
        "GetTrackName",
        "GetItemListInTrack",
    )
    missing = [
        name
        for name in required_timeline_methods
        if not callable(getattr(timeline, name, None))
    ]
    if missing:
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The timeline cannot enumerate subtitle tracks.",
            details={"missing_methods": missing},
        )

    track_count = timeline.GetTrackCount("subtitle")
    if (
        not isinstance(track_count, int)
        or isinstance(track_count, bool)
        or not 0 <= track_count <= 128
    ):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "Timeline.GetTrackCount('subtitle') returned an invalid value.",
        )

    tracks: list[dict[str, Any]] = []
    item_count = 0
    item_methods = (
        "GetUniqueId",
        "GetName",
        "GetDuration",
        "GetStart",
        "GetEnd",
        "GetTrackTypeAndIndex",
    )
    for track_index in range(1, track_count + 1):
        items = timeline.GetItemListInTrack("subtitle", track_index)
        if not isinstance(items, list):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Timeline.GetItemListInTrack() returned invalid subtitles.",
                details={"track_index": track_index},
            )
        discovered_items: list[dict[str, Any]] = []
        for item in items:
            item_count += 1
            if item_count > 10_000:
                raise BridgeOperationError(
                    "DISCOVERY_LIMIT_EXCEEDED",
                    "Subtitle discovery exceeded 10000 timeline items.",
                )
            missing_item_methods = [
                name
                for name in item_methods
                if not callable(getattr(item, name, None))
            ]
            if missing_item_methods:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "A subtitle item cannot report bounded metadata.",
                    details={"missing_methods": missing_item_methods},
                )
            actual_track = item.GetTrackTypeAndIndex()
            if actual_track not in (
                ["subtitle", track_index],
                ("subtitle", track_index),
            ):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Subtitle item track readback does not match enumeration.",
                )
            text = str(item.GetName())
            if not text or len(text) > 4_000:
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Subtitle text must contain 1 to 4000 characters.",
                )
            numeric_values = {
                "duration_frames": item.GetDuration(False),
                "timeline_start_frame": item.GetStart(False),
                "timeline_end_frame": item.GetEnd(False),
            }
            if any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in numeric_values.values()
            ):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Subtitle item frame readback is invalid.",
                )
            timeline_item_id = str(item.GetUniqueId())
            if not timeline_item_id or len(timeline_item_id) > 128:
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Subtitle item canonical ID must contain 1 to 128 characters.",
                )
            discovered_items.append(
                {
                    "timeline_item_id": timeline_item_id,
                    "text": text,
                    **numeric_values,
                }
            )
        tracks.append(
            {
                "track_index": track_index,
                "name": str(timeline.GetTrackName("subtitle", track_index)),
                "items": discovered_items,
            }
        )

    required_constants = (
        "SUBTITLE_LANGUAGE",
        "SUBTITLE_CAPTION_PRESET",
        "SUBTITLE_CHARS_PER_LINE",
        "SUBTITLE_LINE_BREAK",
        "SUBTITLE_GAP",
        "AUTO_CAPTION_AUTO",
        "AUTO_CAPTION_SUBTITLE_DEFAULT",
        "AUTO_CAPTION_LINE_SINGLE",
    )
    missing_constants = [
        name for name in required_constants if getattr(resolve, name, None) is None
    ]
    return {
        "timeline_id": timeline_id,
        "name": str(timeline.GetName()),
        "subtitle_track_count": track_count,
        "subtitle_item_count": item_count,
        "tracks": tracks,
        "auto_caption": {
            "method_available": callable(
                getattr(timeline, "CreateSubtitlesFromAudio", None)
            ),
            "required_constants_available": not missing_constants,
            "missing_constants": missing_constants,
            "fixed_policy": {
                "language": "auto",
                "caption_preset": "default",
                "characters_per_line": 42,
                "line_break": "single",
                "gap_frames": 0,
            },
            "verified": False,
        },
    }


def _render_environment(resolve: Any) -> dict[str, Any]:
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        raise BridgeOperationError(
            "PROJECT_MANAGER_UNAVAILABLE",
            "Resolve.GetProjectManager() returned no object.",
            retryable=True,
        )
    project = project_manager.GetCurrentProject()
    if project is None:
        raise BridgeOperationError(
            "PROJECT_NOT_OPEN",
            "Open a Resolve project before discovering render options.",
            retryable=True,
        )

    required_methods = (
        "GetRenderFormats",
        "GetRenderCodecs",
        "GetRenderResolutions",
        "GetCurrentRenderFormatAndCodec",
        "GetRenderPresetList",
        "GetRenderJobList",
    )
    missing = [
        name
        for name in required_methods
        if not callable(getattr(project, name, None))
    ]
    if missing:
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The current Resolve project does not expose render discovery.",
            details={"missing_methods": missing},
        )

    raw_formats = project.GetRenderFormats()
    if not isinstance(raw_formats, Mapping):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "Resolve.GetRenderFormats() returned an invalid value.",
        )
    formats: list[dict[str, Any]] = []
    for format_name, extension in sorted(
        raw_formats.items(),
        key=lambda item: str(item[0]),
    ):
        raw_codecs = project.GetRenderCodecs(str(format_name))
        if not isinstance(raw_codecs, Mapping):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Resolve.GetRenderCodecs() returned an invalid value.",
                details={"format": str(format_name)},
            )
        formats.append(
            {
                "format": str(format_name),
                "extension": str(extension),
                "codecs": [
                    {
                        "description": str(description),
                        "codec": str(codec),
                    }
                    for description, codec in sorted(
                        raw_codecs.items(),
                        key=lambda item: str(item[0]),
                    )
                ],
            }
        )

    current = project.GetCurrentRenderFormatAndCodec()
    if not isinstance(current, Mapping):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "Resolve.GetCurrentRenderFormatAndCodec() returned an invalid value.",
        )
    presets = project.GetRenderPresetList()
    jobs = project.GetRenderJobList()
    if not isinstance(presets, list) or not isinstance(jobs, list):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "Resolve returned an invalid render preset or job list.",
        )
    raw_resolutions = project.GetRenderResolutions("MP4", "H264")
    if not isinstance(raw_resolutions, list) or not all(
        isinstance(item, Mapping)
        and isinstance(item.get("Width"), int)
        and isinstance(item.get("Height"), int)
        for item in raw_resolutions
    ):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "Resolve.GetRenderResolutions() returned an invalid value.",
            details={"format": "MP4", "codec": "H264"},
        )
    mp4_h264_resolutions = sorted(
        [
            {
                "width": int(item["Width"]),
                "height": int(item["Height"]),
            }
            for item in raw_resolutions
        ],
        key=lambda item: (item["width"], item["height"]),
    )
    timeline = project.GetCurrentTimeline()
    timeline_summary: dict[str, Any] | None = None
    if timeline is not None:
        if not callable(getattr(timeline, "GetTrackCount", None)) or not callable(
            getattr(timeline, "GetItemListInTrack", None)
        ):
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "The current timeline does not expose documented track inspection.",
                details={
                    "missing_methods": [
                        "GetTrackCount",
                        "GetItemListInTrack",
                    ]
                },
            )
        video_track_count = int(timeline.GetTrackCount("video"))
        audio_track_count = int(timeline.GetTrackCount("audio"))
        timeline_summary = {
            "name": str(timeline.GetName()),
            "video_track_count": video_track_count,
            "audio_track_count": audio_track_count,
            "video_item_count": sum(
                len(timeline.GetItemListInTrack("video", index) or [])
                for index in range(1, video_track_count + 1)
            ),
            "audio_item_count": sum(
                len(timeline.GetItemListInTrack("audio", index) or [])
                for index in range(1, audio_track_count + 1)
            ),
        }
    return {
        "formats": formats,
        "current": {
            "format": _optional_string(current.get("format")),
            "codec": _optional_string(current.get("codec")),
        },
        "presets": _json_safe(presets),
        "jobs": _json_safe(jobs),
        "mp4_h264_resolutions": mp4_h264_resolutions,
        "timeline": timeline_summary,
    }


def _render_job_status(resolve: Any, job_id: str) -> dict[str, Any]:
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        raise BridgeOperationError(
            "PROJECT_MANAGER_UNAVAILABLE",
            "Resolve.GetProjectManager() returned no object.",
            retryable=True,
        )
    project = project_manager.GetCurrentProject()
    if project is None:
        raise BridgeOperationError(
            "PROJECT_NOT_OPEN",
            "Open a Resolve project before reading render status.",
            retryable=True,
        )
    required_methods = (
        "GetRenderJobList",
        "GetRenderJobStatus",
        "IsRenderingInProgress",
    )
    missing = [
        name
        for name in required_methods
        if not callable(getattr(project, name, None))
    ]
    if missing:
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The current Resolve project cannot report render status.",
            details={"missing_methods": missing},
        )
    job = _find_render_job(project, job_id)
    status = project.GetRenderJobStatus(job_id)
    if not isinstance(status, Mapping):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "Resolve.GetRenderJobStatus() returned an invalid value.",
            details={"job_id": job_id},
        )
    return {
        "job_id": job_id,
        "rendering_in_progress": bool(project.IsRenderingInProgress()),
        "status": _json_safe(status),
        "job": _json_safe(job),
    }


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _require_project(resolve: Any) -> tuple[Any, Any, Any]:
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        raise BridgeOperationError(
            "PROJECT_MANAGER_UNAVAILABLE",
            "Resolve.GetProjectManager() returned no object.",
            retryable=True,
        )
    project = project_manager.GetCurrentProject()
    if project is None:
        raise BridgeOperationError(
            "PROJECT_NOT_OPEN",
            "Open a Resolve project before running this command.",
            retryable=True,
        )
    media_pool = project.GetMediaPool()
    if media_pool is None:
        raise BridgeOperationError(
            "MEDIA_POOL_UNAVAILABLE",
            "The current project returned no MediaPool object.",
            retryable=True,
        )
    return project_manager, project, media_pool


def _safe_backup_stem(project_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", project_name).strip("-._")
    return normalized[:80] or "resolve-project"


def _create_project_backup(
    project_manager: Any,
    project: Any,
    backups_directory: Path,
    command_id: str,
) -> str:
    if project_manager.SaveProject() is not True:
        raise BridgeOperationError(
            "PROJECT_SAVE_FAILED",
            "Resolve could not save the current project before modification.",
            retryable=True,
        )
    project_name = str(project.GetName())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backups_directory / (
        f"{_safe_backup_stem(project_name)}-{timestamp}-"
        f"{_safe_backup_stem(command_id)}.drp"
    )
    exported = project_manager.ExportProject(
        project_name,
        str(backup_path),
        False,
    )
    if exported is not True or not backup_path.is_file():
        raise BridgeOperationError(
            "PROJECT_BACKUP_FAILED",
            "Resolve could not export a project backup; no edit was attempted.",
            retryable=True,
            details={"backup_path": str(backup_path)},
        )
    return str(backup_path)


def _load_media_roots(state_directory: Path) -> tuple[Path, ...]:
    policy_path = state_directory / "media-policy.json"
    try:
        with policy_path.open("r", encoding="utf-8") as policy_file:
            policy = json.load(policy_file)
    except (OSError, json.JSONDecodeError) as error:
        raise BridgeOperationError(
            "MEDIA_POLICY_UNAVAILABLE",
            "Resolved media policy is missing or unreadable.",
            retryable=True,
        ) from error
    if (
        not isinstance(policy, dict)
        or policy.get("policy_version") != "1.0"
        or not isinstance(policy.get("allowed_roots"), list)
        or not policy["allowed_roots"]
    ):
        raise BridgeOperationError(
            "MEDIA_POLICY_INVALID",
            "Resolved media policy does not match version 1.0.",
        )
    roots: list[Path] = []
    for raw_root in policy["allowed_roots"]:
        if not isinstance(raw_root, str):
            raise BridgeOperationError(
                "MEDIA_POLICY_INVALID",
                "Every resolved media root must be a string.",
            )
        root = Path(raw_root)
        if not root.is_absolute():
            raise BridgeOperationError(
                "MEDIA_POLICY_INVALID",
                "Every resolved media root must be absolute.",
            )
        roots.append(root.resolve())
    return tuple(roots)


def _validate_bridge_media_paths(
    raw_paths: list[str],
    state_directory: Path,
) -> list[str]:
    roots = _load_media_roots(state_directory)
    validated: list[str] = []
    for raw_path in raw_paths:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            raise BridgeOperationError(
                "MEDIA_PATH_NOT_ABSOLUTE",
                f"Media path must be absolute: {raw_path}",
            )
        resolved = candidate.resolve()
        if not resolved.is_file():
            raise BridgeOperationError(
                "MEDIA_FILE_NOT_FOUND",
                f"Media file does not exist: {resolved}",
            )
        if not any(resolved.is_relative_to(root) for root in roots):
            raise BridgeOperationError(
                "MEDIA_PATH_NOT_ALLOWED",
                f"Media path is outside configured allowed roots: {resolved}",
            )
        validated.append(str(resolved))
    return validated


def _find_timeline(project: Any, timeline_id: str) -> Any:
    for index in range(1, int(project.GetTimelineCount()) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is not None and str(timeline.GetUniqueId()) == timeline_id:
            return timeline
    raise BridgeOperationError(
        "TIMELINE_NOT_FOUND",
        f"Resolve timeline was not found: {timeline_id}",
    )


def _find_timeline_item(timeline: Any, timeline_item_id: str) -> Any:
    required_methods = ("GetTrackCount", "GetItemListInTrack")
    missing = [
        name
        for name in required_methods
        if not callable(getattr(timeline, name, None))
    ]
    if missing:
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The timeline cannot enumerate video and audio items.",
            details={"missing_methods": missing},
        )
    for track_type in ("video", "audio"):
        track_count = timeline.GetTrackCount(track_type)
        if not isinstance(track_count, int) or track_count < 0:
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Timeline.GetTrackCount() returned an invalid value.",
                details={"track_type": track_type},
            )
        for track_index in range(1, track_count + 1):
            items = timeline.GetItemListInTrack(track_type, track_index)
            if not isinstance(items, list):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Timeline.GetItemListInTrack() returned an invalid value.",
                    details={
                        "track_type": track_type,
                        "track_index": track_index,
                    },
                )
            for item in items:
                get_unique_id = getattr(item, "GetUniqueId", None)
                if callable(get_unique_id) and str(
                    get_unique_id()
                ) == timeline_item_id:
                    return item
    raise BridgeOperationError(
        "TIMELINE_ITEM_NOT_FOUND",
        f"Resolve timeline item was not found: {timeline_item_id}",
    )


def _linked_timeline_item_ids(item: Any) -> list[str]:
    """Return validated canonical IDs from documented link-state readback."""
    linked_items = item.GetLinkedItems()
    if not isinstance(linked_items, (list, tuple)):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "TimelineItem.GetLinkedItems() returned an invalid value.",
        )
    linked_ids: list[str] = []
    for linked_item in linked_items:
        get_unique_id = getattr(linked_item, "GetUniqueId", None)
        if not callable(get_unique_id):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "A linked TimelineItem cannot report its unique ID.",
            )
        linked_id = str(get_unique_id())
        if not linked_id:
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "A linked TimelineItem returned an empty unique ID.",
            )
        linked_ids.append(linked_id)
    return sorted(set(linked_ids))


def _prepare_link_items(
    timeline: Any,
    item_ids: list[str],
) -> tuple[Any, list[Any], list[tuple[str, int]], dict[str, list[str]]]:
    """Resolve and validate one bounded group before changing link state."""
    set_linked = getattr(timeline, "SetClipsLinked", None)
    get_locked = getattr(timeline, "GetIsTrackLocked", None)
    missing_timeline_methods = [
        name
        for name, method in (
            ("SetClipsLinked", set_linked),
            ("GetIsTrackLocked", get_locked),
        )
        if not callable(method)
    ]
    if missing_timeline_methods:
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The timeline cannot safely change clip links.",
            details={"missing_methods": missing_timeline_methods},
        )
    items = [_find_timeline_item(timeline, item_id) for item_id in item_ids]
    item_tracks: list[tuple[str, int]] = []
    previous_links: dict[str, list[str]] = {}
    for item in items:
        missing_item_methods = [
            name
            for name in (
                "GetUniqueId",
                "GetName",
                "GetTrackTypeAndIndex",
                "GetLinkedItems",
            )
            if not callable(getattr(item, name, None))
        ]
        if missing_item_methods:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "A timeline item cannot report its link state.",
                details={"missing_methods": missing_item_methods},
            )
        actual_track = item.GetTrackTypeAndIndex()
        if (
            not isinstance(actual_track, (list, tuple))
            or len(actual_track) != 2
            or actual_track[0] not in {"video", "audio"}
            or not isinstance(actual_track[1], int)
            or isinstance(actual_track[1], bool)
        ):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "TimelineItem track readback is invalid.",
            )
        track = (str(actual_track[0]), int(actual_track[1]))
        get_locked_call = cast(Callable[..., Any], get_locked)
        if get_locked_call(track[0], track[1]) is True:
            raise BridgeOperationError(
                "TIMELINE_TRACK_LOCKED",
                "A requested timeline item track is locked.",
                details={
                    "timeline_item_id": str(item.GetUniqueId()),
                    "track_type": track[0],
                    "track_index": track[1],
                },
            )
        item_tracks.append(track)
        previous_links[str(item.GetUniqueId())] = _linked_timeline_item_ids(item)
    return set_linked, items, item_tracks, previous_links


def _apply_link_items(
    set_linked: Any,
    items: list[Any],
    item_tracks: list[tuple[str, int]],
    previous_links: dict[str, list[str]],
    linked: bool,
) -> list[dict[str, Any]]:
    """Apply and read back one prevalidated link group."""
    set_linked_call = cast(Callable[..., Any], set_linked)
    if set_linked_call(items, linked) is not True:
        raise BridgeOperationError(
            "CLIP_LINK_FAILED",
            "Resolve did not change the requested clip link state.",
            retryable=True,
        )
    requested_ids = {str(item.GetUniqueId()) for item in items}
    readback_items: list[dict[str, Any]] = []
    for item, track in zip(items, item_tracks, strict=True):
        item_id = str(item.GetUniqueId())
        linked_ids = _linked_timeline_item_ids(item)
        selected_peers = requested_ids - {item_id}
        linked_selected_peers = selected_peers.intersection(linked_ids)
        if (linked and linked_selected_peers != selected_peers) or (
            not linked and linked_selected_peers
        ):
            raise BridgeOperationError(
                "CLIP_LINK_READBACK_FAILED",
                "Resolve did not report the requested clip link state.",
                details={
                    "timeline_item_id": item_id,
                    "requested_linked": linked,
                    "linked_item_ids": linked_ids,
                },
            )
        readback_items.append(
            {
                "timeline_item_id": item_id,
                "name": str(item.GetName()),
                "track_type": track[0],
                "track_index": track[1],
                "previous_linked_item_ids": previous_links[item_id],
                "linked_item_ids": linked_ids,
            }
        )
    return readback_items


def _prepare_transform_item(
    timeline: Any,
    timeline_item_id: str,
    arguments: dict[str, Any],
) -> tuple[Any, int, dict[str, bool | float], dict[str, Any]]:
    """Resolve and validate one bounded transform before its project backup."""
    item = _find_timeline_item(timeline, timeline_item_id)
    required_item_methods = (
        "GetUniqueId",
        "GetName",
        "GetTrackTypeAndIndex",
        "GetProperty",
        "SetProperty",
    )
    missing = [
        name
        for name in required_item_methods
        if not callable(getattr(item, name, None))
    ]
    if missing:
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The timeline item cannot change transform properties.",
            details={"missing_methods": missing},
        )
    actual_track = item.GetTrackTypeAndIndex()
    if (
        not isinstance(actual_track, (list, tuple))
        or len(actual_track) != 2
        or actual_track[0] != "video"
        or not isinstance(actual_track[1], int)
        or isinstance(actual_track[1], bool)
    ):
        raise BridgeOperationError(
            "VIDEO_TIMELINE_ITEM_REQUIRED",
            "Clip transform requires one video timeline item.",
        )
    get_locked = getattr(timeline, "GetIsTrackLocked", None)
    get_setting = getattr(timeline, "GetSetting", None)
    missing_timeline_methods = [
        name
        for name, method in (
            ("GetIsTrackLocked", get_locked),
            ("GetSetting", get_setting),
        )
        if not callable(method)
    ]
    if missing_timeline_methods:
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY",
            "The timeline cannot validate clip transforms.",
            details={"missing_methods": missing_timeline_methods},
        )
    get_locked_call = cast(Callable[..., Any], get_locked)
    get_setting_call = cast(Callable[..., Any], get_setting)
    if get_locked_call("video", actual_track[1]) is True:
        raise BridgeOperationError(
            "TIMELINE_TRACK_LOCKED",
            "The timeline item track is locked.",
            details={"track_type": "video", "track_index": actual_track[1]},
        )
    try:
        timeline_width = int(get_setting_call("timelineResolutionWidth"))
        timeline_height = int(get_setting_call("timelineResolutionHeight"))
    except (TypeError, ValueError) as error:
        raise BridgeOperationError(
            "TIMELINE_RESOLUTION_INVALID",
            "Resolve returned invalid timeline dimensions.",
        ) from error
    if timeline_width < 1 or timeline_height < 1:
        raise BridgeOperationError(
            "TIMELINE_RESOLUTION_INVALID",
            "Resolve returned invalid timeline dimensions.",
        )
    if (
        "position_x" in arguments
        and abs(arguments["position_x"]) > 4.0 * timeline_width
    ):
        raise BridgeOperationError(
            "CLIP_POSITION_OUT_OF_RANGE",
            "position_x exceeds four times the timeline width.",
            details={"timeline_width": timeline_width},
        )
    if (
        "position_y" in arguments
        and abs(arguments["position_y"]) > 4.0 * timeline_height
    ):
        raise BridgeOperationError(
            "CLIP_POSITION_OUT_OF_RANGE",
            "position_y exceeds four times the timeline height.",
            details={"timeline_height": timeline_height},
        )
    resolve_properties: dict[str, bool | float] = {}
    if "position_x" in arguments:
        resolve_properties["Pan"] = float(arguments["position_x"])
    if "position_y" in arguments:
        resolve_properties["Tilt"] = float(arguments["position_y"])
    if "zoom" in arguments:
        resolve_properties.update(
            {
                "ZoomGang": True,
                "ZoomX": float(arguments["zoom"]),
                "ZoomY": float(arguments["zoom"]),
            }
        )
    if "rotation_degrees" in arguments:
        resolve_properties["RotationAngle"] = float(
            arguments["rotation_degrees"]
        )
    if "opacity_percent" in arguments:
        resolve_properties["Opacity"] = float(arguments["opacity_percent"])
    previous_properties = {
        key: item.GetProperty(key) for key in resolve_properties
    }
    return item, int(actual_track[1]), resolve_properties, previous_properties


def _apply_transform_item(
    item: Any,
    resolve_properties: dict[str, bool | float],
) -> dict[str, Any]:
    """Apply and read back one prevalidated allowlisted transform."""
    if item.SetProperty(resolve_properties) is not True:
        raise BridgeOperationError(
            "CLIP_TRANSFORM_FAILED",
            "Resolve did not apply the fixed clip transform.",
            retryable=True,
        )
    actual_properties = {
        key: item.GetProperty(key) for key in resolve_properties
    }
    mismatched: list[str] = []
    for key, expected in resolve_properties.items():
        actual = actual_properties[key]
        if isinstance(expected, bool):
            if actual is not expected:
                mismatched.append(key)
        elif (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or not math.isclose(
                float(actual), expected, rel_tol=1e-9, abs_tol=1e-6
            )
        ):
            mismatched.append(key)
    if mismatched:
        raise BridgeOperationError(
            "CLIP_TRANSFORM_READBACK_FAILED",
            "Resolve did not report the requested transform.",
            details={"mismatched_properties": mismatched},
        )
    return cast(dict[str, Any], _json_safe(actual_properties))


def _timeline_item_exists(timeline: Any, timeline_item_id: str) -> bool:
    try:
        _find_timeline_item(timeline, timeline_item_id)
    except BridgeOperationError as error:
        if error.code == "TIMELINE_ITEM_NOT_FOUND":
            return False
        raise
    return True


def _find_media_item(folder: Any, asset_id: str) -> Any:
    for item in folder.GetClipList() or []:
        if str(item.GetMediaId()) == asset_id:
            return item
    for child in folder.GetSubFolderList() or []:
        found = _find_media_item_or_none(child, asset_id)
        if found is not None:
            return found
    raise BridgeOperationError(
        "MEDIA_ASSET_NOT_FOUND",
        f"Resolve media asset was not found: {asset_id}",
    )


def _find_media_item_or_none(folder: Any, asset_id: str) -> Any:
    try:
        return _find_media_item(folder, asset_id)
    except BridgeOperationError as error:
        if error.code == "MEDIA_ASSET_NOT_FOUND":
            return None
        raise


def _request_fingerprint(command: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "provider": command["provider"],
            "action": command["action"],
            "arguments": command["arguments"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _receipt_path(state_directory: Path, idempotency_key: str) -> Path:
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return state_directory / "receipts" / f"{key_hash}.json"


def _read_receipt(
    command: dict[str, Any],
    state_directory: Path,
) -> dict[str, Any] | None:
    path = _receipt_path(state_directory, command["idempotency_key"])
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as receipt_file:
            receipt = json.load(receipt_file)
    except (OSError, json.JSONDecodeError) as error:
        raise BridgeOperationError(
            "IDEMPOTENCY_RECEIPT_INVALID",
            "The stored idempotency receipt is unreadable.",
        ) from error
    if (
        not isinstance(receipt, dict)
        or receipt.get("fingerprint") != _request_fingerprint(command)
        or not isinstance(receipt.get("result"), dict)
    ):
        raise BridgeOperationError(
            "IDEMPOTENCY_CONFLICT",
            "The idempotency key was already used for a different command.",
        )
    return cast(dict[str, Any], receipt["result"])


def _write_receipt(
    command: dict[str, Any],
    state_directory: Path,
    result: dict[str, Any],
) -> None:
    path = _receipt_path(state_directory, command["idempotency_key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        {
            "idempotency_key": command["idempotency_key"],
            "action": command["action"],
            "fingerprint": _request_fingerprint(command),
            "created_at": utc_now(),
            "result": result,
        },
    )


def _verify_subtitle_import_receipt(
    state_directory: Path,
    idempotency_key: str,
    subtitle_path: str,
    asset_id: str,
) -> None:
    path = _receipt_path(state_directory, idempotency_key)
    if not path.is_file():
        raise BridgeOperationError(
            "SUBTITLE_IMPORT_RECEIPT_REQUIRED",
            "A verified import receipt is required before subtitle append.",
        )
    try:
        with path.open("r", encoding="utf-8") as source:
            receipt = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise BridgeOperationError(
            "SUBTITLE_IMPORT_RECEIPT_INVALID",
            "The subtitle import receipt is unreadable.",
        ) from error
    expected_fingerprint = _request_fingerprint(
        {
            "provider": "resolve",
            "action": "import_media",
            "arguments": {"paths": [subtitle_path]},
        }
    )
    result = receipt.get("result") if isinstance(receipt, dict) else None
    items = result.get("items") if isinstance(result, dict) else None
    imported_ids = {
        item.get("asset_id")
        for item in items
        if isinstance(item, dict)
    } if isinstance(items, list) else set()
    if (
        not isinstance(receipt, dict)
        or receipt.get("action") != "import_media"
        or receipt.get("fingerprint") != expected_fingerprint
        or asset_id not in imported_ids
    ):
        raise BridgeOperationError(
            "SUBTITLE_IMPORT_RECEIPT_INVALID",
            "The import receipt does not bind this SRT and asset ID.",
        )


def _subtitle_environment_item_ids(environment: dict[str, Any]) -> set[str]:
    return {
        item["timeline_item_id"]
        for track in environment["tracks"]
        for item in track["items"]
    }


def _find_render_job(project: Any, job_id: str) -> dict[str, Any]:
    jobs = project.GetRenderJobList()
    if not isinstance(jobs, list):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE",
            "Resolve.GetRenderJobList() returned an invalid value.",
        )
    for item in jobs:
        if isinstance(item, Mapping) and str(item.get("JobId", "")) == job_id:
            return {str(key): value for key, value in item.items()}
    raise BridgeOperationError(
        "RENDER_JOB_NOT_FOUND",
        f"Resolve render job was not found: {job_id}",
    )


def _find_prepared_render_result(
    state_directory: Path,
    job_id: str,
) -> dict[str, Any]:
    receipts_directory = state_directory / "receipts"
    for path in receipts_directory.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as receipt_file:
                receipt = json.load(receipt_file)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(receipt, dict) or receipt.get("action") != (
            "prepare_render_job"
        ):
            continue
        result = receipt.get("result")
        if isinstance(result, dict) and result.get("job_id") == job_id:
            return cast(dict[str, Any], result)
    raise BridgeOperationError(
        "RENDER_JOB_NOT_AGENT_PREPARED",
        "The render job was not prepared by DaVinci Resolve Agent.",
        details={"job_id": job_id},
    )


def _verify_agent_prepared_render_job(
    project: Any,
    state_directory: Path,
    job_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared = _find_prepared_render_result(state_directory, job_id)
    job = _find_render_job(project, job_id)
    profile_name = prepared.get("preset")
    profile = (
        RENDER_PROFILES.get(profile_name)
        if isinstance(profile_name, str)
        else None
    )
    if profile is None:
        raise BridgeOperationError(
            "RENDER_JOB_POLICY_MISMATCH",
            "The prepared render job has an unsupported profile.",
            details={"job_id": job_id},
        )
    kind = profile.get("kind")
    expected_directory = (
        _audio_source_output_directory()
        if kind == "audio"
        else _render_output_directory()
    )
    extension = profile.get("extension", "") if isinstance(profile, dict) else ""
    expected_name = f"{prepared.get('custom_name', '')}{extension}"
    prepared_directory = Path(str(prepared.get("target_directory", ""))).resolve()
    live_directory = Path(str(job.get("TargetDir", ""))).resolve()
    common_valid = (
        prepared.get("resolve_preset") == profile["resolve_preset"]
        and prepared.get("started") is False
        and prepared_directory == expected_directory
        and live_directory == expected_directory
        and job.get("PresetName") == profile["resolve_preset"]
        and job.get("OutputFilename") == expected_name
    )
    if kind == "audio":
        valid = bool(
            common_valid
            and str(prepared.get("format", "")).casefold() in {"wav", "wave"}
            and prepared.get("export_video") is False
            and prepared.get("export_audio") is True
            and prepared.get("audio_bit_depth") == profile["audio_bit_depth"]
            and prepared.get("audio_sample_rate")
            == profile["audio_sample_rate"]
            and job.get("IsExportVideo") is False
            and job.get("IsExportAudio") is True
            and job.get("AudioBitDepth") == profile["audio_bit_depth"]
            and job.get("AudioSampleRate") == profile["audio_sample_rate"]
        )
    else:
        valid = bool(
            common_valid
            and prepared.get("format") == profile["format"]
            and prepared.get("codec") == profile["codec"]
            and job.get("VideoFormat") == "MP4"
            and job.get("VideoCodec") in {"H.264", "H264"}
            and job.get("FormatWidth") == profile["width"]
            and job.get("FormatHeight") == profile["height"]
        )
    if not valid:
        raise BridgeOperationError(
            "RENDER_JOB_POLICY_MISMATCH",
            "The render job no longer matches the fixed safe render policy.",
            details={"job_id": job_id},
        )
    return prepared, job


def _render_start_path(state_directory: Path, job_id: str) -> Path:
    job_hash = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    return state_directory / "render-starts" / f"{job_hash}.json"


def _reserve_render_start(
    state_directory: Path,
    command: dict[str, Any],
    job_id: str,
) -> Path:
    path = _render_start_path(state_directory, job_id)
    if path.is_file():
        raise BridgeOperationError(
            "RENDER_JOB_ALREADY_STARTED",
            "This agent-prepared render job already has a start record.",
            details={"job_id": job_id},
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        {
            "job_id": job_id,
            "command_id": command["command_id"],
            "idempotency_key": command["idempotency_key"],
            "status": "starting",
            "created_at": utc_now(),
        },
    )
    return path


def _verified_capabilities_path(state_directory: Path) -> Path:
    return state_directory / "verified-capabilities.json"


def _record_verified_capability(
    state_directory: Path,
    capability: str,
) -> None:
    path = _verified_capabilities_path(state_directory)
    verified: dict[str, Any] = {}
    if path.is_file():
        try:
            with path.open("r", encoding="utf-8") as capability_file:
                loaded = json.load(capability_file)
            if isinstance(loaded, dict):
                verified = loaded
        except (OSError, json.JSONDecodeError):
            verified = {}
    verified[capability] = True
    atomic_write_json(path, verified)


def _apply_verified_capabilities(
    state: dict[str, Any],
    state_directory: Path,
) -> None:
    path = _verified_capabilities_path(state_directory)
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8") as capability_file:
            verified = json.load(capability_file)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(verified, dict):
        return
    capabilities = state["capabilities"]
    for capability, value in verified.items():
        supported = set(CAPABILITY_BY_ACTION.values()) | {"render.discovery"}
        if capability in supported and value is True:
            capabilities[capability] = True


def _execute_close_project(
    resolve: Any,
    command: dict[str, Any],
    directories: dict[str, Path],
) -> tuple[dict[str, Any], list[str]]:
    """Save and back up one exact project, then verify native closure.

    CloseProject itself does not save. Never discard changes or fall back to
    closing another project. Receipts describe the original execution only.
    """
    manager = resolve.GetProjectManager()
    required = ("GetCurrentProject", "SaveProject", "ExportProject", "CloseProject")
    if manager is None or any(
        not callable(getattr(manager, method, None)) for method in required
    ):
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY", "Native project closure is unavailable."
        )
    expected = command["arguments"]["expected_project_id"]

    def current_target() -> Any:
        current = manager.GetCurrentProject()
        if current is None or current.GetUniqueId() != expected:
            raise BridgeOperationError(
                "PROJECT_CHANGED", "The requested project is no longer current."
            )
        if current.IsRenderingInProgress() is not False:
            raise BridgeOperationError(
                "RENDER_ALREADY_IN_PROGRESS",
                "Stop rendering before closing the project."
            )
        return current

    project = current_target()
    name = project.GetName()
    backup_path = _create_project_backup(
        manager, project, directories["backups"], command["command_id"]
    )
    try:
        project = current_target()
        if manager.SaveProject() is not True:
            raise BridgeOperationError("PROJECT_SAVE_FAILED", "Project was not closed.")
        project = current_target()
        if manager.CloseProject(project) is not True:
            raise BridgeOperationError(
                "PROJECT_CLOSE_FAILED",
                "Native closure was not acknowledged; do not retry."
            )
        if manager.GetCurrentProject() is not None:
            raise BridgeOperationError(
                "PROJECT_CLOSE_READBACK_FAILED",
                "Project closure could not be verified."
            )
        result = {
            "project": {"project_id": expected, "name": name},
            "closed": True,
            "saved": True,
            "backup_path": backup_path,
            "evidence": "native_readback_at_execution",
        }
        _write_receipt(command, directories["state"], result)
        return result, []
    except BridgeOperationError as error:
        error.details.setdefault("backup_path", backup_path)
        raise
    except Exception as error:
        raise BridgeOperationError(
            "PROJECT_CLOSE_UNCERTAIN",
            "Inspect original evidence before retrying closure.",
            details={"backup_path": backup_path},
        ) from error


def _execute_create_project(
    resolve: Any,
    command: dict[str, Any],
    directories: dict[str, Path],
) -> tuple[dict[str, Any], list[str]]:
    """Create a unique project, backing up the current project when present.

    Creation operates in the current library folder. It never loads a name
    collision or deletes a project after a partial failure. Successful replay
    returns the receipt without switching the user's current project.
    """
    manager = resolve.GetProjectManager()
    required = ("GetCurrentProject", "GetProjectListInCurrentFolder",
                "CreateProject", "SaveProject")
    if manager is None or any(
        not callable(getattr(manager, method, None)) for method in required
    ):
        raise BridgeOperationError(
            "UNSUPPORTED_CAPABILITY", "Project creation methods are unavailable."
        )
    name = command["arguments"]["name"]
    names = manager.GetProjectListInCurrentFolder()
    if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
        raise BridgeOperationError(
            "INVALID_RESOLVE_RESPONSE", "Project folder listing is invalid."
        )
    if any(item.casefold() == name.casefold() for item in names):
        raise BridgeOperationError(
            "PROJECT_NAME_CONFLICT", "A project with this name already exists."
        )
    previous = manager.GetCurrentProject()
    previous_project_id = None
    backup_path = None
    if previous is not None:
        previous_project_id = str(previous.GetUniqueId())
        if previous.IsRenderingInProgress() is True:
            raise BridgeOperationError(
                "RENDER_ALREADY_IN_PROGRESS",
                "Wait for rendering before creating a project.",
            )
        backup_path = _create_project_backup(
            manager, previous, directories["backups"], command["command_id"]
        )
    try:
        created = manager.CreateProject(name)
        if created is None:
            raise BridgeOperationError(
                "PROJECT_CREATE_FAILED", "Resolve did not create the requested project."
            )
        current = manager.GetCurrentProject()
        if (
            current is None
            or created.GetName() != name
            or current.GetUniqueId() != created.GetUniqueId()
            or not created.GetUniqueId()
            or previous_project_id == str(created.GetUniqueId())
        ):
            raise BridgeOperationError(
                "PROJECT_CREATE_READBACK_FAILED",
                "New project identity could not be verified.",
            )
        if manager.SaveProject() is not True:
            raise BridgeOperationError(
                "PROJECT_SAVE_FAILED", "The created project could not be saved."
            )
        result = {
            "project": {"project_id": str(created.GetUniqueId()), "name": name},
            "backup_path": backup_path,
            "previous_project_id": previous_project_id,
            "created": True,
        }
        _write_receipt(command, directories["state"], result)
        return result, []
    except BridgeOperationError as error:
        error.details.setdefault("backup_path", backup_path)
        raise
    except Exception as error:
        raise BridgeOperationError(
            "PROJECT_CREATE_FAILED",
            "Project creation failed; inspect Resolve before retrying.",
            details={"backup_path": backup_path},
        ) from error


def _execute_write_command(
    resolve: Any,
    command: dict[str, Any],
    directories: dict[str, Path],
) -> tuple[dict[str, Any], list[str]]:
    receipt = _read_receipt(command, directories["state"])
    if receipt is not None:
        return receipt, ["Returned the stored idempotent result."]

    if command["action"] == "close_project":
        return _execute_close_project(resolve, command, directories)

    if command["action"] == "create_project":
        return _execute_create_project(resolve, command, directories)

    project_manager, project, media_pool = _require_project(resolve)
    action = command["action"]
    arguments = command["arguments"]

    if action == "import_media":
        arguments = {
            "paths": _validate_bridge_media_paths(
                arguments["paths"],
                directories["state"],
            )
        }
    elif action == "duplicate_timeline":
        timeline = _find_timeline(project, arguments["timeline_id"])
        duplicate_timeline = getattr(timeline, "DuplicateTimeline", None)
        get_current_timeline = getattr(project, "GetCurrentTimeline", None)
        set_current_timeline = getattr(project, "SetCurrentTimeline", None)
        missing_methods = [
            name
            for name, method in (
                ("DuplicateTimeline", duplicate_timeline),
                ("GetCurrentTimeline", get_current_timeline),
                ("SetCurrentTimeline", set_current_timeline),
            )
            if not callable(method)
        ]
        if missing_methods:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "The timeline cannot be safely duplicated.",
                details={"missing_methods": missing_methods},
            )
        get_current_timeline_call = cast(Callable[[], Any], get_current_timeline)
        previous_timeline = get_current_timeline_call()
        if previous_timeline is None:
            raise BridgeOperationError(
                "CURRENT_TIMELINE_REQUIRED",
                "Select a current timeline before duplicating a timeline.",
                retryable=True,
            )
        timeline_count = project.GetTimelineCount()
        if not isinstance(timeline_count, int):
            raise BridgeOperationError(
                "INVALID_RESOLVE_RESPONSE",
                "Project.GetTimelineCount() returned an invalid value.",
            )
        for index in range(1, timeline_count + 1):
            candidate = project.GetTimelineByIndex(index)
            if (
                candidate is not None
                and str(candidate.GetName()).casefold()
                == arguments["name"].casefold()
            ):
                raise BridgeOperationError(
                    "TIMELINE_NAME_CONFLICT",
                    "A timeline with the requested name already exists.",
                    details={"name": arguments["name"]},
                )
        source_timeline = {
            "timeline_id": str(timeline.GetUniqueId()),
            "name": str(timeline.GetName()),
        }
    elif action == "set_current_timeline":
        timeline = _find_timeline(project, arguments["timeline_id"])
        required_project_methods = (
            "GetCurrentTimeline",
            "SetCurrentTimeline",
        )
        missing = [
            name
            for name in required_project_methods
            if not callable(getattr(project, name, None))
        ]
        if missing:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "The current project cannot select a timeline.",
                details={"missing_methods": missing},
            )
        previous_timeline = project.GetCurrentTimeline()
    elif action in {
        "ensure_timeline_tracks",
        "append_clip",
        "insert_title",
        "insert_animation_template",
        "append_subtitle_file",
        "insert_clip",
        "insert_clips",
        "set_clip_enabled",
        "set_clips_linked",
        "set_clip_link_groups",
        "set_clip_transform",
        "set_clip_transforms",
        "delete_clip",
        "add_marker",
        "create_subtitles_from_audio",
        "apply_color_preset",
    }:
        timeline = _find_timeline(project, arguments["timeline_id"])
        if action == "apply_color_preset":
            if not callable(getattr(project, "SetCurrentTimeline", None)):
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The project cannot select the color target timeline.",
                    details={"missing_methods": ["SetCurrentTimeline"]},
                )
            color_items = _prepare_color_preset_items(
                resolve, timeline, arguments
            )
        elif action == "create_subtitles_from_audio":
            required_methods = (
                "CreateSubtitlesFromAudio",
                "GetItemListInTrack",
                "GetTrackCount",
                "GetTrackName",
            )
            missing = [
                name
                for name in required_methods
                if not callable(getattr(timeline, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot create and verify native subtitles.",
                    details={"missing_methods": missing},
                )
            if not callable(getattr(project, "SetCurrentTimeline", None)):
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The current project cannot select the subtitle timeline.",
                    details={"missing_methods": ["SetCurrentTimeline"]},
                )
            required_constants = (
                "SUBTITLE_LANGUAGE",
                "SUBTITLE_CAPTION_PRESET",
                "SUBTITLE_CHARS_PER_LINE",
                "SUBTITLE_LINE_BREAK",
                "SUBTITLE_GAP",
                "AUTO_CAPTION_AUTO",
                "AUTO_CAPTION_SUBTITLE_DEFAULT",
                "AUTO_CAPTION_LINE_SINGLE",
            )
            missing_constants = [
                name
                for name in required_constants
                if getattr(resolve, name, None) is None
            ]
            if missing_constants:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "Resolve does not expose the required auto-caption constants.",
                    details={"missing_constants": missing_constants},
                )
            audio_track_count = timeline.GetTrackCount("audio")
            if (
                not isinstance(audio_track_count, int)
                or isinstance(audio_track_count, bool)
                or audio_track_count < 1
            ):
                raise BridgeOperationError(
                    "TIMELINE_AUDIO_REQUIRED",
                    "The timeline must contain at least one audio track.",
                )
            previous_subtitles = _subtitle_environment(
                resolve,
                arguments["timeline_id"],
            )
        if action == "insert_title":
            insert_title = getattr(timeline, "InsertTitleIntoTimeline", None)
            get_timecode = getattr(timeline, "GetCurrentTimecode", None)
            set_timecode = getattr(timeline, "SetCurrentTimecode", None)
            missing = [
                name
                for name, method in (
                    ("InsertTitleIntoTimeline", insert_title),
                    ("GetCurrentTimecode", get_timecode),
                    ("SetCurrentTimecode", set_timecode),
                    ("GetEndFrame", getattr(timeline, "GetEndFrame", None)),
                    ("GetSetting", getattr(timeline, "GetSetting", None)),
                    ("GetTrackCount", getattr(timeline, "GetTrackCount", None)),
                    (
                        "GetItemListInTrack",
                        getattr(timeline, "GetItemListInTrack", None),
                    ),
                    (
                        "SetCurrentTimeline",
                        getattr(project, "SetCurrentTimeline", None),
                    ),
                )
                if not callable(method)
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot insert a standard title at an exact timecode.",
                    details={"missing_methods": missing},
                )
            assert callable(get_timecode)
            previous_timecode = get_timecode()
            if not isinstance(previous_timecode, str) or not previous_timecode:
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Timeline.GetCurrentTimecode() returned an invalid value.",
                )
            timeline_end_frame = timeline.GetEndFrame()
            if (
                not isinstance(timeline_end_frame, int)
                or isinstance(timeline_end_frame, bool)
            ):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Timeline.GetEndFrame() returned an invalid value.",
                )
            timeline_frame_rate = _positive_frame_rate_setting(
                timeline.GetSetting("timelineFrameRate")
            )
            requested_title_frame = _non_drop_timecode_frame(
                arguments["timecode"], timeline_frame_rate
            )
            if requested_title_frame < timeline_end_frame:
                raise BridgeOperationError(
                    "TITLE_APPEND_ONLY",
                    "Standard titles may be inserted only at or after "
                    "the timeline end.",
                    details={
                        "requested_frame": requested_title_frame,
                        "timeline_end_frame": timeline_end_frame,
                    },
                )
            previous_video_items = _video_item_snapshots(timeline)
        if action == "insert_animation_template":
            template = ANIMATION_TEMPLATES[arguments["template_id"]]
            environment = _animation_template_environment(
                resolve, arguments["timeline_id"]
            )
            if environment["ready"] is not True:
                raise BridgeOperationError(
                    "ANIMATION_TEMPLATE_UNAVAILABLE",
                    "The packaged animation template or required Resolve "
                    "methods are unavailable.",
                    details={
                        "template_id": arguments["template_id"],
                        "methods": environment["methods"],
                        "templates": environment["templates"],
                    },
                )
            insert_animation_template = getattr(
                timeline, "InsertFusionTitleIntoTimeline", None
            )
            get_timecode = getattr(timeline, "GetCurrentTimecode", None)
            if not callable(insert_animation_template) or not callable(
                get_timecode
            ):
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot insert the packaged Fusion title.",
                )
            previous_timecode = get_timecode()
            if not isinstance(previous_timecode, str) or not previous_timecode:
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Timeline.GetCurrentTimecode() returned an invalid value.",
                )
            timeline_end_frame = timeline.GetEndFrame()
            if (
                not isinstance(timeline_end_frame, int)
                or isinstance(timeline_end_frame, bool)
            ):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Timeline.GetEndFrame() returned an invalid value.",
                )
            timeline_frame_rate = _positive_frame_rate_setting(
                timeline.GetSetting("timelineFrameRate")
            )
            requested_animation_frame = _non_drop_timecode_frame(
                arguments["timecode"], timeline_frame_rate
            )
            if requested_animation_frame != timeline_end_frame:
                raise BridgeOperationError(
                    "ANIMATION_TEMPLATE_APPEND_ONLY",
                    "Animation templates may be inserted only at the exact "
                    "timeline end.",
                    details={
                        "requested_frame": requested_animation_frame,
                        "timeline_end_frame": timeline_end_frame,
                    },
                )
            previous_video_items = _video_item_snapshots(timeline)
        if action == "ensure_timeline_tracks":
            required_track_methods = ("GetTrackCount", "AddTrack")
            missing = [
                name
                for name in required_track_methods
                if not callable(getattr(timeline, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot safely add tracks.",
                    details={"missing_methods": missing},
                )
            for track_type in ("video", "audio"):
                count = timeline.GetTrackCount(track_type)
                if (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 0
                ):
                    raise BridgeOperationError(
                        "INVALID_RESOLVE_RESPONSE",
                        "Timeline.GetTrackCount() returned an invalid value.",
                        details={"track_type": track_type},
                    )
        if action in {"append_clip", "append_subtitle_file", "insert_clip"}:
            root_folder = media_pool.GetRootFolder()
            if root_folder is None:
                raise BridgeOperationError(
                    "MEDIA_POOL_ROOT_UNAVAILABLE",
                    "Resolve MediaPool returned no root folder.",
                    retryable=True,
                )
            media_item = _find_media_item(root_folder, arguments["asset_id"])
        if action == "append_subtitle_file":
            normalized_subtitle_path = _validate_bridge_media_paths(
                [arguments["subtitle_path"]],
                directories["state"],
            )[0]
            _verify_subtitle_import_receipt(
                directories["state"],
                arguments["import_idempotency_key"],
                normalized_subtitle_path,
                arguments["asset_id"],
            )
            subtitle_required_methods = (
                "GetEndFrame",
                "GetSetting",
                "GetStartFrame",
            )
            missing = [
                name
                for name in subtitle_required_methods
                if not callable(getattr(timeline, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot report safe subtitle append placement.",
                    details={"missing_methods": missing},
                )
            previous_subtitles = _subtitle_environment(
                resolve,
                arguments["timeline_id"],
            )
            timeline_start_frame = timeline.GetStartFrame()
            append_frame = timeline.GetEndFrame()
            timeline_frame_rate = _positive_frame_rate_setting(
                timeline.GetSetting("timelineFrameRate")
            )
            if (
                not isinstance(timeline_start_frame, int)
                or isinstance(timeline_start_frame, bool)
                or not isinstance(append_frame, int)
                or isinstance(append_frame, bool)
            ):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Resolve returned invalid timeline frame metadata.",
                )
        if action == "insert_clips":
            root_folder = media_pool.GetRootFolder()
            if root_folder is None:
                raise BridgeOperationError(
                    "MEDIA_POOL_ROOT_UNAVAILABLE",
                    "Resolve MediaPool returned no root folder.",
                    retryable=True,
                )
            batch_media_items = {
                asset_id: _find_media_item(root_folder, asset_id)
                for asset_id in {
                    placement["asset_id"]
                    for placement in arguments["placements"]
                }
            }
            required_timeline_methods = (
                "GetStartFrame",
                "GetTrackCount",
                "GetIsTrackLocked",
            )
            missing = [
                name
                for name in required_timeline_methods
                if not callable(getattr(timeline, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot validate batched ranged clip insertion.",
                    details={"missing_methods": missing},
                )
            for track_type, track_index in {
                (placement["track_type"], placement["track_index"])
                for placement in arguments["placements"]
            }:
                track_count = timeline.GetTrackCount(track_type)
                if not isinstance(track_count, int) or track_index > track_count:
                    raise BridgeOperationError(
                        "TIMELINE_TRACK_NOT_FOUND",
                        "A requested timeline track does not exist.",
                        details={
                            "track_type": track_type,
                            "track_index": track_index,
                        },
                    )
                if timeline.GetIsTrackLocked(track_type, track_index) is True:
                    raise BridgeOperationError(
                        "TIMELINE_TRACK_LOCKED",
                        "A requested timeline track is locked.",
                        details={
                            "track_type": track_type,
                            "track_index": track_index,
                        },
                    )
            timeline_start = timeline.GetStartFrame()
            if not isinstance(timeline_start, int):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Timeline.GetStartFrame() returned an invalid value.",
                )
            if any(
                timeline_start + placement["position_frames"] > 2_147_483_647
                for placement in arguments["placements"]
            ):
                raise BridgeOperationError(
                    "FRAME_RANGE_INVALID",
                    "A requested timeline position exceeds the supported range.",
                )
        if action == "insert_clip":
            required_timeline_methods = (
                "GetStartFrame",
                "GetTrackCount",
                "GetIsTrackLocked",
            )
            missing = [
                name
                for name in required_timeline_methods
                if not callable(getattr(timeline, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot validate ranged clip insertion.",
                    details={"missing_methods": missing},
                )
            track_type = arguments["track_type"]
            track_index = arguments["track_index"]
            track_count = timeline.GetTrackCount(track_type)
            if not isinstance(track_count, int) or track_index > track_count:
                raise BridgeOperationError(
                    "TIMELINE_TRACK_NOT_FOUND",
                    "The requested timeline track does not exist.",
                    details={
                        "track_type": track_type,
                        "track_index": track_index,
                    },
                )
            if timeline.GetIsTrackLocked(track_type, track_index) is True:
                raise BridgeOperationError(
                    "TIMELINE_TRACK_LOCKED",
                    "The requested timeline track is locked.",
                    details={
                        "track_type": track_type,
                        "track_index": track_index,
                    },
                )
            timeline_start = timeline.GetStartFrame()
            if not isinstance(timeline_start, int):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "Timeline.GetStartFrame() returned an invalid value.",
                )
            record_frame = timeline_start + arguments["position_frames"]
            if record_frame > 2_147_483_647:
                raise BridgeOperationError(
                    "FRAME_RANGE_INVALID",
                    "The requested timeline position exceeds the supported range.",
                )
        elif action == "set_clip_enabled":
            item = _find_timeline_item(
                timeline,
                arguments["timeline_item_id"],
            )
            enable_item_methods = (
                "GetUniqueId",
                "GetName",
                "GetTrackTypeAndIndex",
                "GetClipEnabled",
                "SetClipEnabled",
            )
            missing = [
                name
                for name in enable_item_methods
                if not callable(getattr(item, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline item cannot change enabled state.",
                    details={"missing_methods": missing},
                )
            actual_track = item.GetTrackTypeAndIndex()
            if (
                not isinstance(actual_track, (list, tuple))
                or len(actual_track) != 2
                or actual_track[0] not in {"video", "audio"}
                or not isinstance(actual_track[1], int)
            ):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "TimelineItem track readback is invalid.",
                )
            get_locked = getattr(timeline, "GetIsTrackLocked", None)
            if not callable(get_locked):
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot report track lock state.",
                    details={"missing_methods": ["GetIsTrackLocked"]},
                )
            if get_locked(actual_track[0], actual_track[1]) is True:
                raise BridgeOperationError(
                    "TIMELINE_TRACK_LOCKED",
                    "The timeline item track is locked.",
                    details={
                        "track_type": actual_track[0],
                        "track_index": actual_track[1],
                    },
                )
            previous_enabled = item.GetClipEnabled()
            if not isinstance(previous_enabled, bool):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "TimelineItem.GetClipEnabled() returned an invalid value.",
                )
        elif action == "set_clips_linked":
            set_linked = getattr(timeline, "SetClipsLinked", None)
            get_locked = getattr(timeline, "GetIsTrackLocked", None)
            missing_timeline_methods = [
                name
                for name, method in (
                    ("SetClipsLinked", set_linked),
                    ("GetIsTrackLocked", get_locked),
                )
                if not callable(method)
            ]
            if missing_timeline_methods:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot safely change clip links.",
                    details={"missing_methods": missing_timeline_methods},
                )
            items = [
                _find_timeline_item(timeline, item_id)
                for item_id in arguments["timeline_item_ids"]
            ]
            item_tracks: list[tuple[str, int]] = []
            previous_links: dict[str, list[str]] = {}
            for item in items:
                missing_item_methods = [
                    name
                    for name in (
                        "GetUniqueId",
                        "GetName",
                        "GetTrackTypeAndIndex",
                        "GetLinkedItems",
                    )
                    if not callable(getattr(item, name, None))
                ]
                if missing_item_methods:
                    raise BridgeOperationError(
                        "UNSUPPORTED_CAPABILITY",
                        "A timeline item cannot report its link state.",
                        details={"missing_methods": missing_item_methods},
                    )
                actual_track = item.GetTrackTypeAndIndex()
                if (
                    not isinstance(actual_track, (list, tuple))
                    or len(actual_track) != 2
                    or actual_track[0] not in {"video", "audio"}
                    or not isinstance(actual_track[1], int)
                    or isinstance(actual_track[1], bool)
                ):
                    raise BridgeOperationError(
                        "INVALID_RESOLVE_RESPONSE",
                        "TimelineItem track readback is invalid.",
                    )
                track = (str(actual_track[0]), int(actual_track[1]))
                get_locked_call = cast(Callable[..., Any], get_locked)
                if get_locked_call(track[0], track[1]) is True:
                    raise BridgeOperationError(
                        "TIMELINE_TRACK_LOCKED",
                        "A requested timeline item track is locked.",
                        details={
                            "timeline_item_id": str(item.GetUniqueId()),
                            "track_type": track[0],
                            "track_index": track[1],
                        },
                    )
                item_tracks.append(track)
                previous_links[str(item.GetUniqueId())] = (
                    _linked_timeline_item_ids(item)
                )
        elif action == "set_clip_link_groups":
            link_batches = [
                _prepare_link_items(timeline, group)
                for group in arguments["groups"]
            ]
        elif action == "set_clip_transform":
            item = _find_timeline_item(
                timeline,
                arguments["timeline_item_id"],
            )
            transform_item_methods = (
                "GetUniqueId",
                "GetName",
                "GetTrackTypeAndIndex",
                "GetProperty",
                "SetProperty",
            )
            missing = [
                name
                for name in transform_item_methods
                if not callable(getattr(item, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline item cannot change transform properties.",
                    details={"missing_methods": missing},
                )
            actual_track = item.GetTrackTypeAndIndex()
            if (
                not isinstance(actual_track, (list, tuple))
                or len(actual_track) != 2
                or actual_track[0] != "video"
                or not isinstance(actual_track[1], int)
                or isinstance(actual_track[1], bool)
            ):
                raise BridgeOperationError(
                    "VIDEO_TIMELINE_ITEM_REQUIRED",
                    "Clip transform requires one video timeline item.",
                )
            get_locked = getattr(timeline, "GetIsTrackLocked", None)
            get_setting = getattr(timeline, "GetSetting", None)
            missing_timeline_methods = [
                name
                for name, method in (
                    ("GetIsTrackLocked", get_locked),
                    ("GetSetting", get_setting),
                )
                if not callable(method)
            ]
            if missing_timeline_methods:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot validate clip transforms.",
                    details={"missing_methods": missing_timeline_methods},
                )
            get_locked_call = cast(Callable[..., Any], get_locked)
            get_setting_call = cast(Callable[..., Any], get_setting)
            if get_locked_call("video", actual_track[1]) is True:
                raise BridgeOperationError(
                    "TIMELINE_TRACK_LOCKED",
                    "The timeline item track is locked.",
                    details={
                        "track_type": "video",
                        "track_index": actual_track[1],
                    },
                )
            try:
                timeline_width = int(
                    get_setting_call("timelineResolutionWidth")
                )
                timeline_height = int(
                    get_setting_call("timelineResolutionHeight")
                )
            except (TypeError, ValueError) as error:
                raise BridgeOperationError(
                    "TIMELINE_RESOLUTION_INVALID",
                    "Resolve returned invalid timeline dimensions.",
                ) from error
            if timeline_width < 1 or timeline_height < 1:
                raise BridgeOperationError(
                    "TIMELINE_RESOLUTION_INVALID",
                    "Resolve returned invalid timeline dimensions.",
                )
            if (
                "position_x" in arguments
                and abs(arguments["position_x"]) > 4.0 * timeline_width
            ):
                raise BridgeOperationError(
                    "CLIP_POSITION_OUT_OF_RANGE",
                    "position_x exceeds four times the timeline width.",
                    details={"timeline_width": timeline_width},
                )
            if (
                "position_y" in arguments
                and abs(arguments["position_y"]) > 4.0 * timeline_height
            ):
                raise BridgeOperationError(
                    "CLIP_POSITION_OUT_OF_RANGE",
                    "position_y exceeds four times the timeline height.",
                    details={"timeline_height": timeline_height},
                )
            resolve_properties: dict[str, bool | float] = {}
            if "position_x" in arguments:
                resolve_properties["Pan"] = float(arguments["position_x"])
            if "position_y" in arguments:
                resolve_properties["Tilt"] = float(arguments["position_y"])
            if "zoom" in arguments:
                resolve_properties.update(
                    {
                        "ZoomGang": True,
                        "ZoomX": float(arguments["zoom"]),
                        "ZoomY": float(arguments["zoom"]),
                    }
                )
            if "rotation_degrees" in arguments:
                resolve_properties["RotationAngle"] = float(
                    arguments["rotation_degrees"]
                )
            if "opacity_percent" in arguments:
                resolve_properties["Opacity"] = float(
                    arguments["opacity_percent"]
                )
            previous_properties = {
                key: item.GetProperty(key)
                for key in resolve_properties
            }
        elif action == "set_clip_transforms":
            transform_batches = [
                (
                    update,
                    _prepare_transform_item(
                        timeline,
                        update["timeline_item_id"],
                        update,
                    ),
                )
                for update in arguments["items"]
            ]
        elif action == "delete_clip":
            item = _find_timeline_item(
                timeline,
                arguments["timeline_item_id"],
            )
            delete_item_methods = (
                "GetUniqueId",
                "GetName",
                "GetStart",
                "GetEnd",
                "GetTrackTypeAndIndex",
            )
            missing = [
                name
                for name in delete_item_methods
                if not callable(getattr(item, name, None))
            ]
            delete_clips = getattr(timeline, "DeleteClips", None)
            get_locked = getattr(timeline, "GetIsTrackLocked", None)
            if not callable(delete_clips):
                missing.append("DeleteClips")
            if not callable(get_locked):
                missing.append("GetIsTrackLocked")
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The timeline cannot safely delete one item.",
                    details={"missing_methods": missing},
                )
            actual_track = item.GetTrackTypeAndIndex()
            if (
                not isinstance(actual_track, (list, tuple))
                or len(actual_track) != 2
                or actual_track[0] not in {"video", "audio"}
                or not isinstance(actual_track[1], int)
                or isinstance(actual_track[1], bool)
            ):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "TimelineItem track readback is invalid.",
                )
            get_locked_call = cast(Callable[..., Any], get_locked)
            if get_locked_call(actual_track[0], actual_track[1]) is True:
                raise BridgeOperationError(
                    "TIMELINE_TRACK_LOCKED",
                    "The timeline item track is locked.",
                    details={
                        "track_type": actual_track[0],
                        "track_index": actual_track[1],
                    },
                )
            deleted_item = {
                "timeline_item_id": str(item.GetUniqueId()),
                "name": str(item.GetName()),
                "timeline_start_frame": int(item.GetStart(False)),
                "timeline_end_frame": int(item.GetEnd(False)),
                "track_type": str(actual_track[0]),
                "track_index": int(actual_track[1]),
            }
    elif action == "prepare_render_job" and "timeline_id" in arguments:
        render_timeline = _find_timeline(project, arguments["timeline_id"])
        required_project_methods = (
            "GetCurrentTimeline",
            "SetCurrentTimeline",
        )
        missing = [
            name
            for name in required_project_methods
            if not callable(getattr(project, name, None))
        ]
        if missing:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "The current project cannot select the render timeline.",
                details={"missing_methods": missing},
            )
    elif action == "start_render_job":
        job_id = _validate_render_job_arguments(action, arguments)
        required_methods = (
            "GetRenderJobList",
            "GetRenderJobStatus",
            "IsRenderingInProgress",
            "StartRendering",
        )
        missing = [
            name
            for name in required_methods
            if not callable(getattr(project, name, None))
        ]
        if missing:
            raise BridgeOperationError(
                "UNSUPPORTED_CAPABILITY",
                "The current Resolve project cannot start render jobs.",
                details={"missing_methods": missing},
            )
        _verify_agent_prepared_render_job(
            project,
            directories["state"],
            job_id,
        )
        if _render_start_path(directories["state"], job_id).is_file():
            raise BridgeOperationError(
                "RENDER_JOB_ALREADY_STARTED",
                "This agent-prepared render job already has a start record.",
                details={"job_id": job_id},
            )
        if project.IsRenderingInProgress() is True:
            raise BridgeOperationError(
                "RENDER_ALREADY_IN_PROGRESS",
                "Resolve is already rendering a job.",
                retryable=True,
            )

    backup_path = _create_project_backup(
        project_manager,
        project,
        directories["backups"],
        command["command_id"],
    )

    try:
        result: dict[str, Any]
        if action == "import_media":
            imported = media_pool.ImportMedia(arguments["paths"])
            if not imported:
                raise BridgeOperationError(
                    "MEDIA_IMPORT_FAILED",
                    "Resolve did not import any media items.",
                    retryable=True,
                )
            result = {
                "items": [
                    {
                        "asset_id": str(item.GetMediaId()),
                        "name": str(item.GetName()),
                    }
                    for item in imported
                ],
                "backup_path": backup_path,
            }
        elif action == "create_timeline":
            timeline = media_pool.CreateEmptyTimeline(arguments["name"])
            if timeline is None:
                raise BridgeOperationError(
                    "TIMELINE_CREATE_FAILED",
                    "Resolve did not create the requested timeline.",
                    retryable=True,
                )
            result = {
                "timeline": {
                    "timeline_id": str(timeline.GetUniqueId()),
                    "name": str(timeline.GetName()),
                },
                "backup_path": backup_path,
            }
        elif action == "ensure_timeline_tracks":
            before = {
                track_type: int(timeline.GetTrackCount(track_type))
                for track_type in ("video", "audio")
            }
            added = {"video": 0, "audio": 0}
            for track_type, field in (
                ("video", "video_track_count"),
                ("audio", "audio_track_count"),
            ):
                target = arguments[field]
                current = before[track_type]
                while current < target:
                    if track_type == "audio":
                        created = timeline.AddTrack("audio", "stereo")
                    else:
                        created = timeline.AddTrack("video")
                    if created is not True:
                        raise BridgeOperationError(
                            "TIMELINE_TRACK_CREATE_FAILED",
                            "Resolve did not add the requested timeline track.",
                            retryable=True,
                            details={"track_type": track_type},
                        )
                    readback = timeline.GetTrackCount(track_type)
                    if (
                        not isinstance(readback, int)
                        or isinstance(readback, bool)
                        or readback != current + 1
                    ):
                        raise BridgeOperationError(
                            "TIMELINE_TRACK_READBACK_FAILED",
                            "Resolve returned an unexpected track count.",
                            details={"track_type": track_type},
                        )
                    current = readback
                    added[track_type] += 1
            after = {
                track_type: int(timeline.GetTrackCount(track_type))
                for track_type in ("video", "audio")
            }
            result = {
                "timeline": {
                    "timeline_id": str(timeline.GetUniqueId()),
                    "name": str(timeline.GetName()),
                },
                "before": {
                    "video_track_count": before["video"],
                    "audio_track_count": before["audio"],
                },
                "after": {
                    "video_track_count": after["video"],
                    "audio_track_count": after["audio"],
                },
                "added": {
                    "video_track_count": added["video"],
                    "audio_track_count": added["audio"],
                },
                "backup_path": backup_path,
            }
        elif action == "duplicate_timeline":
            duplicate_timeline_call = cast(
                Callable[..., Any],
                duplicate_timeline,
            )
            duplicated = duplicate_timeline_call(arguments["name"])
            if duplicated is None:
                raise BridgeOperationError(
                    "TIMELINE_DUPLICATE_FAILED",
                    "Resolve did not duplicate the requested timeline.",
                    retryable=True,
                )
            duplicated_id = str(duplicated.GetUniqueId())
            duplicated_name = str(duplicated.GetName())
            if (
                not duplicated_id
                or duplicated_id == arguments["timeline_id"]
                or duplicated_name != arguments["name"]
            ):
                raise BridgeOperationError(
                    "TIMELINE_DUPLICATE_READBACK_FAILED",
                    "Resolve returned invalid duplicated timeline metadata.",
                    details={
                        "timeline_id": duplicated_id,
                        "name": duplicated_name,
                    },
                )
            discovered = _find_timeline(project, duplicated_id)
            if str(discovered.GetName()) != arguments["name"]:
                raise BridgeOperationError(
                    "TIMELINE_DUPLICATE_READBACK_FAILED",
                    "The duplicated timeline is absent from the project list.",
                    details={"timeline_id": duplicated_id},
                )
            current_timeline = project.GetCurrentTimeline()
            if (
                current_timeline is None
                or str(current_timeline.GetUniqueId())
                != str(previous_timeline.GetUniqueId())
            ):
                set_current_timeline_call = cast(
                    Callable[[Any], Any],
                    set_current_timeline,
                )
                if set_current_timeline_call(previous_timeline) is not True:
                    raise BridgeOperationError(
                        "TIMELINE_RESTORE_FAILED",
                        "Resolve could not restore the previous current timeline.",
                        retryable=True,
                        details={"duplicated_timeline_id": duplicated_id},
                    )
                current_timeline = project.GetCurrentTimeline()
            if (
                current_timeline is None
                or str(current_timeline.GetUniqueId())
                != str(previous_timeline.GetUniqueId())
            ):
                raise BridgeOperationError(
                    "TIMELINE_RESTORE_READBACK_FAILED",
                    "Resolve did not report the previous current timeline.",
                    details={"duplicated_timeline_id": duplicated_id},
                )
            result = {
                "source_timeline": source_timeline,
                "timeline": {
                    "timeline_id": duplicated_id,
                    "name": duplicated_name,
                },
                "current_timeline": {
                    "timeline_id": str(current_timeline.GetUniqueId()),
                    "name": str(current_timeline.GetName()),
                },
                "backup_path": backup_path,
            }
        elif action == "set_current_timeline":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            current_timeline = project.GetCurrentTimeline()
            if (
                current_timeline is None
                or str(current_timeline.GetUniqueId())
                != arguments["timeline_id"]
            ):
                raise BridgeOperationError(
                    "TIMELINE_SELECT_READBACK_FAILED",
                    "Resolve did not report the requested current timeline.",
                    details={"timeline_id": arguments["timeline_id"]},
                )
            result = {
                "timeline": {
                    "timeline_id": str(current_timeline.GetUniqueId()),
                    "name": str(current_timeline.GetName()),
                },
                "previous_timeline": (
                    None
                    if previous_timeline is None
                    else {
                        "timeline_id": str(previous_timeline.GetUniqueId()),
                        "name": str(previous_timeline.GetName()),
                    }
                ),
                "backup_path": backup_path,
            }
        elif action == "append_clip":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            appended = media_pool.AppendToTimeline([media_item])
            if not appended:
                raise BridgeOperationError(
                    "CLIP_APPEND_FAILED",
                    "Resolve did not append the requested media item.",
                    retryable=True,
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "asset_id": arguments["asset_id"],
                "items": [
                    {
                        "timeline_item_id": str(item.GetUniqueId()),
                        "name": str(item.GetName()),
                    }
                    for item in appended
                ],
                "backup_path": backup_path,
            }
        elif action == "insert_title":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested title timeline.",
                    retryable=True,
                )
            if timeline.SetCurrentTimecode(arguments["timecode"]) is not True:
                raise BridgeOperationError(
                    "TIMECODE_SELECT_FAILED",
                    "Resolve could not select the requested title timecode.",
                    details={"timecode": arguments["timecode"]},
                )
            try:
                title_item = timeline.InsertTitleIntoTimeline(
                    arguments["title_name"]
                )
            finally:
                timeline.SetCurrentTimecode(previous_timecode)
            if title_item is None:
                raise BridgeOperationError(
                    "TITLE_INSERT_FAILED",
                    "Resolve did not insert the requested installed standard title.",
                    retryable=True,
                    details={"title_name": arguments["title_name"]},
                )
            required_title_item_methods = (
                "GetUniqueId",
                "GetName",
                "GetStart",
                "GetEnd",
                "GetDuration",
                "GetTrackTypeAndIndex",
            )
            missing = [
                name
                for name in required_title_item_methods
                if not callable(getattr(title_item, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "TITLE_READBACK_FAILED",
                    "The inserted title cannot report canonical metadata.",
                    details={"missing_methods": missing},
                )
            actual_video_items = _video_item_snapshots(timeline)
            returned_title_id = str(title_item.GetUniqueId())
            new_item_ids = sorted(
                set(actual_video_items).difference(previous_video_items)
            )
            changed_existing_ids = sorted(
                item_id
                for item_id, snapshot in previous_video_items.items()
                if actual_video_items.get(item_id) != snapshot
            )
            inserted_snapshot = actual_video_items.get(returned_title_id)
            if (
                new_item_ids != [returned_title_id]
                or changed_existing_ids
                or inserted_snapshot is None
                or inserted_snapshot["timeline_start_frame"]
                != requested_title_frame
            ):
                raise BridgeOperationError(
                    "TITLE_INSERT_READBACK_FAILED",
                    "Resolve changed unexpected video items during title insertion.",
                    details={
                        "returned_title_id": returned_title_id,
                        "new_item_ids": new_item_ids,
                        "changed_existing_ids": changed_existing_ids,
                        "requested_frame": requested_title_frame,
                        "inserted_snapshot": inserted_snapshot,
                    },
                )
            track = title_item.GetTrackTypeAndIndex()
            if (
                not isinstance(track, (list, tuple))
                or len(track) != 2
                or track[0] != "video"
                or not isinstance(track[1], int)
                or isinstance(track[1], bool)
            ):
                raise BridgeOperationError(
                    "TITLE_READBACK_FAILED",
                    "The inserted title did not report a video track.",
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "title_name": arguments["title_name"],
                "requested_timecode": arguments["timecode"],
                "requested_frame": requested_title_frame,
                "previous_timecode": previous_timecode,
                "item": {
                    "timeline_item_id": returned_title_id,
                    "name": str(title_item.GetName()),
                    "track_type": "video",
                    "track_index": int(track[1]),
                    "timeline_start_frame": int(title_item.GetStart(False)),
                    "timeline_end_frame": int(title_item.GetEnd(False)),
                    "duration_frames": int(title_item.GetDuration(False)),
                },
                "backup_path": backup_path,
            }
        elif action == "insert_animation_template":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested animation timeline.",
                    retryable=True,
                )
            if timeline.SetCurrentTimecode(arguments["timecode"]) is not True:
                raise BridgeOperationError(
                    "TIMECODE_SELECT_FAILED",
                    "Resolve could not select the requested animation timecode.",
                    details={"timecode": arguments["timecode"]},
                )
            selected_timecode = timeline.GetCurrentTimecode()
            if selected_timecode != arguments["timecode"]:
                timeline.SetCurrentTimecode(previous_timecode)
                raise BridgeOperationError(
                    "TIMECODE_READBACK_FAILED",
                    "Resolve did not retain the requested animation timecode.",
                    retryable=True,
                    details={
                        "requested_timecode": arguments["timecode"],
                        "selected_timecode": selected_timecode,
                    },
                )
            insert_animation_call = cast(
                Callable[..., Any], insert_animation_template
            )
            try:
                animation_item = insert_animation_call(template["resolve_name"])
            finally:
                timeline.SetCurrentTimecode(previous_timecode)
            if animation_item is None:
                raise BridgeOperationError(
                    "ANIMATION_TEMPLATE_INSERT_FAILED",
                    "Resolve did not insert the packaged Fusion title.",
                    retryable=True,
                    details={"template_id": arguments["template_id"]},
                )
            required_animation_item_methods = (
                "GetUniqueId",
                "GetName",
                "GetStart",
                "GetEnd",
                "GetDuration",
                "GetTrackTypeAndIndex",
                "GetFusionCompCount",
            )
            missing = [
                name
                for name in required_animation_item_methods
                if not callable(getattr(animation_item, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "ANIMATION_TEMPLATE_READBACK_FAILED",
                    "The inserted animation cannot report canonical metadata.",
                    details={"missing_methods": missing},
                )
            fusion_comp_count = animation_item.GetFusionCompCount()
            if (
                not isinstance(fusion_comp_count, int)
                or isinstance(fusion_comp_count, bool)
                or fusion_comp_count < 1
            ):
                raise BridgeOperationError(
                    "ANIMATION_TEMPLATE_READBACK_FAILED",
                    "The inserted title did not report a Fusion composition.",
                    details={"fusion_comp_count": fusion_comp_count},
                )
            actual_video_items = _video_item_snapshots(timeline)
            returned_animation_id = str(animation_item.GetUniqueId())
            new_item_ids = sorted(
                set(actual_video_items).difference(previous_video_items)
            )
            changed_existing_ids = sorted(
                item_id
                for item_id, snapshot in previous_video_items.items()
                if actual_video_items.get(item_id) != snapshot
            )
            inserted_snapshot = actual_video_items.get(returned_animation_id)
            if (
                new_item_ids != [returned_animation_id]
                or changed_existing_ids
                or inserted_snapshot is None
                or inserted_snapshot["timeline_start_frame"]
                != requested_animation_frame
            ):
                raise BridgeOperationError(
                    "ANIMATION_TEMPLATE_READBACK_FAILED",
                    "Resolve changed unexpected video items during animation "
                    "insertion.",
                    details={
                        "returned_item_id": returned_animation_id,
                        "new_item_ids": new_item_ids,
                        "changed_existing_ids": changed_existing_ids,
                        "requested_frame": requested_animation_frame,
                        "inserted_snapshot": inserted_snapshot,
                    },
                )
            track = animation_item.GetTrackTypeAndIndex()
            if (
                not isinstance(track, (list, tuple))
                or len(track) != 2
                or track[0] != "video"
                or not isinstance(track[1], int)
                or isinstance(track[1], bool)
            ):
                raise BridgeOperationError(
                    "ANIMATION_TEMPLATE_READBACK_FAILED",
                    "The inserted animation did not report a video track.",
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "template_id": arguments["template_id"],
                "resolve_name": template["resolve_name"],
                "requested_timecode": arguments["timecode"],
                "requested_frame": requested_animation_frame,
                "previous_timecode": previous_timecode,
                "fusion_comp_count": fusion_comp_count,
                "item": {
                    "timeline_item_id": returned_animation_id,
                    "name": str(animation_item.GetName()),
                    "track_type": "video",
                    "track_index": int(track[1]),
                    "timeline_start_frame": int(animation_item.GetStart(False)),
                    "timeline_end_frame": int(animation_item.GetEnd(False)),
                    "duration_frames": int(animation_item.GetDuration(False)),
                },
                "backup_path": backup_path,
            }
        elif action == "append_subtitle_file":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested subtitle timeline.",
                    retryable=True,
                )
            appended = media_pool.AppendToTimeline([media_item])
            if not isinstance(appended, list) or not appended:
                raise BridgeOperationError(
                    "SUBTITLE_APPEND_FAILED",
                    "Resolve did not append the imported SRT.",
                    retryable=True,
                )
            readback = _subtitle_environment(resolve, arguments["timeline_id"])
            previous_ids = _subtitle_environment_item_ids(previous_subtitles)
            new_items = [
                item
                for track in readback["tracks"]
                for item in track["items"]
                if item["timeline_item_id"] not in previous_ids
            ]
            if not new_items:
                raise BridgeOperationError(
                    "SUBTITLE_READBACK_FAILED",
                    "Resolve did not report new imported subtitle items.",
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "asset_id": arguments["asset_id"],
                "subtitle_path": normalized_subtitle_path,
                "timeline_start_frame": timeline_start_frame,
                "append_frame": append_frame,
                "timeline_frame_rate": timeline_frame_rate,
                "items": new_items,
                "backup_path": backup_path,
            }
        elif action == "insert_clip":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            clip_info = {
                "mediaPoolItem": media_item,
                "startFrame": arguments["source_start_frame"],
                "endFrame": arguments["source_end_frame"],
                "mediaType": 1 if arguments["track_type"] == "video" else 2,
                "trackIndex": arguments["track_index"],
                "recordFrame": record_frame,
            }
            inserted = media_pool.AppendToTimeline([clip_info])
            if not inserted or len(inserted) != 1:
                raise BridgeOperationError(
                    "CLIP_INSERT_FAILED",
                    "Resolve did not insert the requested source range.",
                    retryable=True,
                )
            item = inserted[0]
            required_single_item_methods = (
                "GetUniqueId",
                "GetName",
                "GetStart",
                "GetEnd",
                "GetSourceStartFrame",
                "GetSourceEndFrame",
                "GetTrackTypeAndIndex",
            )
            missing = [
                name
                for name in required_single_item_methods
                if not callable(getattr(item, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "The inserted TimelineItem lacks documented readback methods.",
                    details={"missing_methods": missing},
                )
            actual_track = item.GetTrackTypeAndIndex()
            if (
                not isinstance(actual_track, (list, tuple))
                or len(actual_track) != 2
            ):
                raise BridgeOperationError(
                    "INVALID_RESOLVE_RESPONSE",
                    "TimelineItem track readback is invalid.",
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "asset_id": arguments["asset_id"],
                "item": {
                    "timeline_item_id": str(item.GetUniqueId()),
                    "name": str(item.GetName()),
                    "timeline_start_frame": int(item.GetStart(False)),
                    "timeline_end_frame": int(item.GetEnd(False)),
                    "source_start_frame": int(item.GetSourceStartFrame()),
                    "source_end_frame": int(item.GetSourceEndFrame()),
                    "track_type": str(actual_track[0]),
                    "track_index": int(actual_track[1]),
                },
                "backup_path": backup_path,
            }
        elif action == "insert_clips":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            clip_infos = [
                {
                    "mediaPoolItem": batch_media_items[placement["asset_id"]],
                    "startFrame": placement["source_start_frame"],
                    "endFrame": placement["source_end_frame"],
                    "mediaType": 1 if placement["track_type"] == "video" else 2,
                    "trackIndex": placement["track_index"],
                    "recordFrame": timeline_start + placement["position_frames"],
                }
                for placement in arguments["placements"]
            ]
            inserted = media_pool.AppendToTimeline(clip_infos)
            if not isinstance(inserted, list) or len(inserted) != len(clip_infos):
                raise BridgeOperationError(
                    "CLIP_BATCH_INSERT_FAILED",
                    "Resolve did not insert every requested source range.",
                    retryable=True,
                    details={
                        "requested_count": len(clip_infos),
                        "inserted_count": (
                            len(inserted) if isinstance(inserted, list) else 0
                        ),
                    },
                )
            required_batch_item_methods = (
                "GetUniqueId",
                "GetName",
                "GetStart",
                "GetEnd",
                "GetSourceStartFrame",
                "GetSourceEndFrame",
                "GetTrackTypeAndIndex",
            )
            result_items = []
            for placement_index, (placement, item) in enumerate(
                zip(arguments["placements"], inserted, strict=True)
            ):
                missing = [
                    name
                    for name in required_batch_item_methods
                    if not callable(getattr(item, name, None))
                ]
                if missing:
                    raise BridgeOperationError(
                        "INVALID_RESOLVE_RESPONSE",
                        "An inserted TimelineItem lacks documented readback methods.",
                        details={
                            "placement_index": placement_index,
                            "missing_methods": missing,
                        },
                    )
                actual_track = item.GetTrackTypeAndIndex()
                if (
                    not isinstance(actual_track, (list, tuple))
                    or len(actual_track) != 2
                ):
                    raise BridgeOperationError(
                        "INVALID_RESOLVE_RESPONSE",
                        "TimelineItem track readback is invalid.",
                        details={"placement_index": placement_index},
                    )
                result_items.append(
                    {
                        "placement_index": placement_index,
                        "asset_id": placement["asset_id"],
                        "timeline_item_id": str(item.GetUniqueId()),
                        "name": str(item.GetName()),
                        "timeline_start_frame": int(item.GetStart(False)),
                        "timeline_end_frame": int(item.GetEnd(False)),
                        "source_start_frame": int(item.GetSourceStartFrame()),
                        "source_end_frame": int(item.GetSourceEndFrame()),
                        "track_type": str(actual_track[0]),
                        "track_index": int(actual_track[1]),
                    }
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "items": result_items,
                "backup_path": backup_path,
            }
        elif action == "set_clip_enabled":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            if item.SetClipEnabled(arguments["enabled"]) is not True:
                raise BridgeOperationError(
                    "CLIP_ENABLE_FAILED",
                    "Resolve did not change the timeline item enabled state.",
                    retryable=True,
                )
            actual_enabled = item.GetClipEnabled()
            if (
                not isinstance(actual_enabled, bool)
                or actual_enabled is not arguments["enabled"]
            ):
                raise BridgeOperationError(
                    "CLIP_ENABLE_READBACK_FAILED",
                    "Resolve did not report the requested enabled state.",
                    details={
                        "requested_enabled": arguments["enabled"],
                        "actual_enabled": actual_enabled,
                    },
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "timeline_item_id": arguments["timeline_item_id"],
                "name": str(item.GetName()),
                "track_type": str(actual_track[0]),
                "track_index": int(actual_track[1]),
                "previous_enabled": previous_enabled,
                "enabled": actual_enabled,
                "backup_path": backup_path,
            }
        elif action == "set_clips_linked":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            set_linked_call = cast(Callable[..., Any], set_linked)
            if set_linked_call(items, arguments["linked"]) is not True:
                raise BridgeOperationError(
                    "CLIP_LINK_FAILED",
                    "Resolve did not change the requested clip link state.",
                    retryable=True,
                )
            requested_ids = set(arguments["timeline_item_ids"])
            readback_items: list[dict[str, Any]] = []
            for item, track in zip(items, item_tracks, strict=True):
                item_id = str(item.GetUniqueId())
                linked_ids = _linked_timeline_item_ids(item)
                selected_peers = requested_ids - {item_id}
                linked_selected_peers = selected_peers.intersection(linked_ids)
                if (
                    arguments["linked"]
                    and linked_selected_peers != selected_peers
                ) or (
                    not arguments["linked"]
                    and linked_selected_peers
                ):
                    raise BridgeOperationError(
                        "CLIP_LINK_READBACK_FAILED",
                        "Resolve did not report the requested clip link state.",
                        details={
                            "timeline_item_id": item_id,
                            "requested_linked": arguments["linked"],
                            "linked_item_ids": linked_ids,
                        },
                    )
                readback_items.append(
                    {
                        "timeline_item_id": item_id,
                        "name": str(item.GetName()),
                        "track_type": track[0],
                        "track_index": track[1],
                        "previous_linked_item_ids": previous_links[item_id],
                        "linked_item_ids": linked_ids,
                    }
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "linked": arguments["linked"],
                "items": readback_items,
                "backup_path": backup_path,
            }
        elif action == "set_clip_link_groups":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            group_results = []
            for group_index, batch in enumerate(link_batches):
                set_linked, items, item_tracks, previous_links = batch
                group_results.append(
                    {
                        "group_index": group_index,
                        "items": _apply_link_items(
                            set_linked,
                            items,
                            item_tracks,
                            previous_links,
                            arguments["linked"],
                        ),
                    }
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "linked": arguments["linked"],
                "groups": group_results,
                "backup_path": backup_path,
            }
        elif action == "apply_color_preset":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the color target timeline.",
                    retryable=True,
                )
            applied_items = _apply_color_preset_items(arguments, color_items)
            result = {
                "timeline_id": arguments["timeline_id"],
                "preset_id": arguments["preset_id"],
                "version_name": COLOR_PRESETS[arguments["preset_id"]][
                    "version_name"
                ],
                "items": applied_items,
                "backup_path": backup_path,
            }
        elif action == "set_clip_transform":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            if item.SetProperty(resolve_properties) is not True:
                raise BridgeOperationError(
                    "CLIP_TRANSFORM_FAILED",
                    "Resolve did not apply the fixed clip transform.",
                    retryable=True,
                )
            actual_properties = {
                key: item.GetProperty(key)
                for key in resolve_properties
            }
            mismatched: list[str] = []
            for key, expected in resolve_properties.items():
                actual = actual_properties[key]
                if isinstance(expected, bool):
                    if actual is not expected:
                        mismatched.append(key)
                elif (
                    isinstance(actual, bool)
                    or not isinstance(actual, (int, float))
                    or not math.isclose(
                        float(actual),
                        expected,
                        rel_tol=1e-9,
                        abs_tol=1e-6,
                    )
                ):
                    mismatched.append(key)
            if mismatched:
                raise BridgeOperationError(
                    "CLIP_TRANSFORM_READBACK_FAILED",
                    "Resolve did not report the requested transform.",
                    details={"mismatched_properties": mismatched},
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "timeline_item_id": arguments["timeline_item_id"],
                "name": str(item.GetName()),
                "track_type": "video",
                "track_index": int(actual_track[1]),
                "previous_properties": _json_safe(previous_properties),
                "properties": _json_safe(actual_properties),
                "backup_path": backup_path,
            }
        elif action == "set_clip_transforms":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            transformed_items = []
            for item_index, (update, prepared) in enumerate(transform_batches):
                item, track_index, resolve_properties, previous_properties = (
                    prepared
                )
                transformed_items.append(
                    {
                        "item_index": item_index,
                        "timeline_item_id": update["timeline_item_id"],
                        "name": str(item.GetName()),
                        "track_type": "video",
                        "track_index": track_index,
                        "previous_properties": _json_safe(previous_properties),
                        "properties": _apply_transform_item(
                            item, resolve_properties
                        ),
                    }
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "items": transformed_items,
                "backup_path": backup_path,
            }
        elif action == "delete_clip":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested timeline.",
                    retryable=True,
                )
            delete_clips_call = cast(Callable[..., Any], delete_clips)
            if delete_clips_call([item], False) is not True:
                raise BridgeOperationError(
                    "CLIP_DELETE_FAILED",
                    "Resolve did not delete the requested timeline item.",
                    retryable=True,
                )
            if _timeline_item_exists(
                timeline,
                arguments["timeline_item_id"],
            ):
                raise BridgeOperationError(
                    "CLIP_DELETE_READBACK_FAILED",
                    "Resolve still reports the deleted timeline item.",
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "timeline_item_id": arguments["timeline_item_id"],
                "deleted": True,
                "ripple": False,
                "item": deleted_item,
                "backup_path": backup_path,
            }
        elif action == "add_marker":
            custom_data = f"davinci-agent:{command['idempotency_key']}"
            added = timeline.AddMarker(
                arguments["frame"],
                arguments["color"],
                arguments["name"],
                arguments["note"],
                arguments["duration"],
                custom_data,
            )
            if added is not True:
                raise BridgeOperationError(
                    "MARKER_CREATE_FAILED",
                    "Resolve did not create the requested timeline marker.",
                    retryable=True,
                )
            result = {
                "timeline_id": arguments["timeline_id"],
                "frame": arguments["frame"],
                "color": arguments["color"],
                "name": arguments["name"],
                "custom_data": custom_data,
                "backup_path": backup_path,
            }
        elif action == "create_subtitles_from_audio":
            if project.SetCurrentTimeline(timeline) is not True:
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested subtitle timeline.",
                    retryable=True,
                )
            settings = {
                resolve.SUBTITLE_LANGUAGE: resolve.AUTO_CAPTION_AUTO,
                resolve.SUBTITLE_CAPTION_PRESET: (
                    resolve.AUTO_CAPTION_SUBTITLE_DEFAULT
                ),
                resolve.SUBTITLE_CHARS_PER_LINE: 42,
                resolve.SUBTITLE_LINE_BREAK: resolve.AUTO_CAPTION_LINE_SINGLE,
                resolve.SUBTITLE_GAP: 0,
            }
            if timeline.CreateSubtitlesFromAudio(settings) is not True:
                raise BridgeOperationError(
                    "AUTO_CAPTION_UNAVAILABLE",
                    "Resolve did not accept native subtitle generation. "
                    "This operation may be unavailable in Resolve Free.",
                    details={"timeline_id": arguments["timeline_id"]},
                )
            readback = _subtitle_environment(
                resolve,
                arguments["timeline_id"],
            )
            if (
                readback["subtitle_item_count"]
                <= previous_subtitles["subtitle_item_count"]
            ):
                raise BridgeOperationError(
                    "SUBTITLE_READBACK_FAILED",
                    "Resolve accepted auto-caption but created no subtitle items.",
                    details={
                        "previous_item_count": previous_subtitles[
                            "subtitle_item_count"
                        ],
                        "actual_item_count": readback["subtitle_item_count"],
                    },
                )
            readback["auto_caption"]["verified"] = True
            result = {
                "timeline_id": arguments["timeline_id"],
                "policy": readback["auto_caption"]["fixed_policy"],
                "previous_subtitle_item_count": previous_subtitles[
                    "subtitle_item_count"
                ],
                "subtitle_environment": readback,
                "backup_path": backup_path,
            }
        elif action == "prepare_render_job":
            custom_name = _validate_render_name(arguments["custom_name"])
            profile_name = arguments.get(
                "profile",
                DEFAULT_RENDER_PROFILE,
            )
            profile = _validate_render_profile(profile_name)
            preset_name = profile["resolve_preset"]
            if not callable(getattr(resolve, "OpenPage", None)) or not callable(
                getattr(resolve, "GetCurrentPage", None)
            ):
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "Resolve does not expose documented page navigation.",
                    details={
                        "missing_methods": ["GetCurrentPage", "OpenPage"],
                    },
                )
            prepare_required_methods = (
                "GetCurrentTimeline",
                "GetRenderPresetList",
                "GetRenderResolutions",
                "GetCurrentRenderFormatAndCodec",
                "LoadRenderPreset",
                "SetCurrentRenderFormatAndCodec",
                "SetCurrentRenderMode",
                "SetRenderSettings",
                "AddRenderJob",
                "DeleteRenderJob",
            )
            missing = [
                name
                for name in prepare_required_methods
                if not callable(getattr(project, name, None))
            ]
            if missing:
                raise BridgeOperationError(
                    "UNSUPPORTED_CAPABILITY",
                    "The current Resolve project cannot prepare render jobs.",
                    details={"missing_methods": missing},
                )
            if "timeline_id" in arguments and (
                project.SetCurrentTimeline(render_timeline) is not True
            ):
                raise BridgeOperationError(
                    "TIMELINE_SELECT_FAILED",
                    "Resolve could not select the requested render timeline.",
                    retryable=True,
                )
            current_timeline = project.GetCurrentTimeline()
            if current_timeline is None:
                raise BridgeOperationError(
                    "TIMELINE_NOT_OPEN",
                    "Open a timeline before preparing a render job.",
                    retryable=True,
                )
            previous_page = resolve.GetCurrentPage()
            if resolve.OpenPage("deliver") is not True:
                raise BridgeOperationError(
                    "DELIVER_PAGE_OPEN_FAILED",
                    "Resolve could not open the Deliver page.",
                    retryable=True,
                )
            try:
                presets = project.GetRenderPresetList()
                if not isinstance(presets, list) or preset_name not in presets:
                    raise BridgeOperationError(
                        "RENDER_PRESET_UNAVAILABLE",
                        f"Resolve render preset is unavailable: {preset_name}",
                    )
                if project.LoadRenderPreset(preset_name) is not True:
                    raise BridgeOperationError(
                        "RENDER_PRESET_LOAD_FAILED",
                        f"Resolve could not load render preset: {preset_name}",
                        retryable=True,
                    )
                if profile["kind"] == "video":
                    resolutions = project.GetRenderResolutions("MP4", "H264")
                    expected_resolution = {
                        "Width": profile["width"],
                        "Height": profile["height"],
                    }
                    if (
                        not isinstance(resolutions, list)
                        or expected_resolution not in resolutions
                    ):
                        raise BridgeOperationError(
                            "RENDER_RESOLUTION_UNAVAILABLE",
                            "Resolve does not expose the fixed profile resolution.",
                            details={
                                "profile": profile_name,
                                "width": profile["width"],
                                "height": profile["height"],
                            },
                        )
                    if (
                        project.SetCurrentRenderFormatAndCodec("MP4", "H264")
                        is not True
                    ):
                        raise BridgeOperationError(
                            "RENDER_FORMAT_FAILED",
                            "Resolve could not select MP4/H264 rendering.",
                            retryable=True,
                        )
                    selected_format = "MP4"
                    selected_codec = "H264"
                else:
                    format_selected = (
                        project.SetCurrentRenderFormatAndCodec("Wave", "")
                        is True
                    )
                    current_render = project.GetCurrentRenderFormatAndCodec()
                    selected_format = "Wave"
                    selected_codec = ""
                if project.SetCurrentRenderMode(1) is not True:
                    raise BridgeOperationError(
                        "RENDER_MODE_FAILED",
                        "Resolve could not select single-clip render mode.",
                        retryable=True,
                    )
                if profile["kind"] == "audio":
                    target_directory = _audio_source_output_directory()
                    settings = {
                        "SelectAllFrames": True,
                        "TargetDir": str(target_directory),
                        "CustomName": custom_name,
                        "ExportVideo": False,
                        "ExportAudio": True,
                        "AudioBitDepth": profile["audio_bit_depth"],
                        "AudioSampleRate": profile["audio_sample_rate"],
                    }
                else:
                    target_directory = _render_output_directory()
                    settings = {
                        "SelectAllFrames": True,
                        "TargetDir": str(target_directory),
                        "CustomName": custom_name,
                        "ExportVideo": True,
                        "ExportAudio": True,
                        "FormatWidth": profile["width"],
                        "FormatHeight": profile["height"],
                    }
                if project.SetRenderSettings(settings) is not True:
                    raise BridgeOperationError(
                        "RENDER_SETTINGS_FAILED",
                        "Resolve could not apply the fixed render settings.",
                        retryable=True,
                    )
                job_id = project.AddRenderJob()
                if not isinstance(job_id, str) or not job_id:
                    raise BridgeOperationError(
                        "RENDER_JOB_CREATE_FAILED",
                        "Resolve did not add the render job.",
                        retryable=True,
                    )
                prepared_job = _find_render_job(project, job_id)
                expected_name = f"{custom_name}{profile['extension']}"
                if (
                    prepared_job.get("PresetName") != preset_name
                    or Path(str(prepared_job.get("TargetDir", ""))).resolve()
                    != target_directory
                    or (
                        profile["kind"] != "audio"
                        and prepared_job.get("OutputFilename") != expected_name
                    )
                ):
                    raise BridgeOperationError(
                        "RENDER_JOB_POLICY_MISMATCH",
                        "Resolve prepared a job outside the fixed profile policy.",
                        details={"job_id": job_id},
                    )
                if profile["kind"] == "audio" and (
                    Path(str(prepared_job.get("OutputFilename", ""))).suffix.casefold()
                    != ".wav"
                    or
                    prepared_job.get("IsExportVideo") is not False
                    or prepared_job.get("IsExportAudio") is not True
                    or prepared_job.get("AudioBitDepth")
                    != profile["audio_bit_depth"]
                    or prepared_job.get("AudioSampleRate")
                    != profile["audio_sample_rate"]
                ):
                    deleted = project.DeleteRenderJob(job_id)
                    raise BridgeOperationError(
                        "RENDER_JOB_POLICY_MISMATCH",
                        "Resolve did not preserve the fixed PCM WAV settings.",
                        details={
                            "job_id": job_id,
                            "job": _json_safe(prepared_job),
                            "job_deleted": deleted is True,
                            "format_selected": format_selected,
                            "current": _json_safe(current_render),
                        },
                    )
                if profile["kind"] == "audio":
                    selected_codec = str(prepared_job.get("AudioCodec", ""))
            finally:
                if (
                    isinstance(previous_page, str)
                    and previous_page
                    and previous_page != "deliver"
                ):
                    resolve.OpenPage(previous_page)
            result = {
                "job_id": job_id,
                "timeline_id": str(current_timeline.GetUniqueId()),
                "timeline_name": str(current_timeline.GetName()),
                "preset": profile_name,
                "resolve_preset": preset_name,
                "format": selected_format,
                "codec": selected_codec,
                "target_directory": str(target_directory),
                "custom_name": custom_name,
                "started": False,
                "backup_path": backup_path,
            }
            if profile["kind"] == "audio":
                result.update(
                    {
                        "export_video": False,
                        "export_audio": True,
                        "audio_bit_depth": profile["audio_bit_depth"],
                        "audio_sample_rate": profile["audio_sample_rate"],
                    }
                )
            else:
                result.update(
                    {
                        "width": profile["width"],
                        "height": profile["height"],
                    }
                )
        elif action == "start_render_job":
            job_id = _validate_render_job_arguments(action, arguments)
            start_path = _reserve_render_start(
                directories["state"],
                command,
                job_id,
            )
            if project.StartRendering([job_id], False) is not True:
                raise BridgeOperationError(
                    "RENDER_START_FAILED",
                    "Resolve did not start the requested render job.",
                    retryable=False,
                    details={"job_id": job_id},
                )
            status = _render_job_status(resolve, job_id)
            atomic_write_json(
                start_path,
                {
                    "job_id": job_id,
                    "command_id": command["command_id"],
                    "idempotency_key": command["idempotency_key"],
                    "status": "accepted",
                    "created_at": utc_now(),
                },
            )
            result = {
                "job_id": job_id,
                "started": True,
                "rendering_in_progress": status["rendering_in_progress"],
                "status": status["status"],
                "backup_path": backup_path,
            }
        else:
            raise BridgeOperationError(
                "UNSUPPORTED_WRITE_ACTION",
                f"Unsupported write action: {action}",
            )
    except BridgeOperationError as error:
        error.details.setdefault("backup_path", backup_path)
        raise
    except Exception as error:
        raise BridgeOperationError(
            "RESOLVE_OPERATION_FAILED",
            f"Resolve operation failed: {error}",
            retryable=True,
            details={"backup_path": backup_path},
        ) from error

    _write_receipt(command, directories["state"], result)
    return result, []


def process_command_file(
    processing_path: Path,
    response_path: Path,
    failed_path: Path,
    state: dict[str, Any],
    resolve: Any | None = None,
    directories: dict[str, Path] | None = None,
) -> None:
    """Process one claimed command and write a structured response."""
    started_at = utc_now()
    command_id = processing_path.stem
    try:
        with processing_path.open("r", encoding="utf-8") as command_file:
            command = validate_command(json.load(command_file))
        command_id = command["command_id"]
        warnings: list[str] = []
        if command["action"] in WRITE_ACTIONS:
            if resolve is None or directories is None:
                raise BridgeOperationError(
                    "RESOLVE_CONTEXT_REQUIRED",
                    "A live Resolve context is required for write commands.",
                    retryable=True,
                )
            result, warnings = _execute_write_command(
                resolve,
                command,
                directories,
            )
            capability = CAPABILITY_BY_ACTION[command["action"]]
            state["capabilities"][capability] = True
            _record_verified_capability(directories["state"], capability)
            if command["action"] == "close_project":
                state.update(
                    project_open=False, project_name=None, timelines=[],
                    current_timeline_id=None, current_timeline_name=None,
                )
            else:
                refreshed_state = collect_bridge_state(resolve)
                _apply_verified_capabilities(
                    refreshed_state,
                    directories["state"],
                )
                state.clear()
                state.update(refreshed_state)
        else:
            result = command_result(
                command["action"],
                state,
                resolve,
                command["arguments"],
            )
            if command["action"] == "stop_bridge":
                state["stop_requested"] = True
            if (
                command["action"] in {
                    "get_render_environment",
                    "get_workspace_snapshot",
                }
                and directories is not None
            ):
                state["capabilities"]["render.discovery"] = True
                _record_verified_capability(
                    directories["state"],
                    "render.discovery",
                )
            if (
                command["action"] in {
                    "list_timeline_items",
                    "get_workspace_snapshot",
                }
                and directories is not None
            ):
                state["capabilities"]["clip.read"] = True
                _record_verified_capability(
                    directories["state"],
                    "clip.read",
                )
            if (
                command["action"] in {
                    "list_media_pool_items",
                    "get_workspace_snapshot",
                }
                and directories is not None
            ):
                state["capabilities"]["media.read"] = True
                _record_verified_capability(
                    directories["state"],
                    "media.read",
                )
            if (
                command["action"] == "get_editing_metadata"
                and directories is not None
            ):
                state["capabilities"]["media.metadata.read"] = True
                _record_verified_capability(
                    directories["state"],
                    "media.metadata.read",
                )
            if (
                command["action"] == "get_subtitle_environment"
                and directories is not None
            ):
                state["capabilities"]["subtitle.read"] = True
                _record_verified_capability(
                    directories["state"],
                    "subtitle.read",
                )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "command_id": command_id,
            "status": "success",
            "started_at": started_at,
            "finished_at": utc_now(),
            "result": result,
            "error": None,
            "warnings": warnings,
        }
        atomic_write_json(response_path, response)
        processing_path.unlink()
    except BridgeOperationError as error:
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "command_id": command_id,
            "status": "error",
            "started_at": started_at,
            "finished_at": utc_now(),
            "result": None,
            "error": {
                "code": error.code,
                "message": str(error),
                "details": error.details,
                "retryable": error.retryable,
            },
            "warnings": [],
        }
        atomic_write_json(response_path, response)
        processing_path.replace(failed_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "command_id": command_id,
            "status": "error",
            "started_at": started_at,
            "finished_at": utc_now(),
            "result": None,
            "error": {
                "code": "INVALID_OR_UNSUPPORTED_COMMAND",
                "message": str(error),
                "details": {},
                "retryable": False,
            },
            "warnings": [],
        }
        atomic_write_json(response_path, response)
        processing_path.replace(failed_path)


def process_pending_commands(
    directories: dict[str, Path],
    state: dict[str, Any],
    resolve: Any | None = None,
    *,
    max_commands: int | None = None,
) -> int:
    """Claim and process every complete command currently in the queue."""
    if max_commands is not None and max_commands < 1:
        raise ValueError("max_commands must be at least one when set.")
    processed = 0
    for command_path in sorted(directories["commands"].glob("*.json")):
        if max_commands is not None and processed >= max_commands:
            break
        processing_path = directories["processing"] / command_path.name
        try:
            command_path.replace(processing_path)
        except FileNotFoundError:
            continue
        process_command_file(
            processing_path,
            directories["responses"] / command_path.name,
            directories["failed"] / command_path.name,
            state,
            resolve,
            directories,
        )
        processed += 1
    return processed


def run_persistent_bridge(
    resolve: Any,
    directories: dict[str, Path],
    *,
    sleep: Callable[[float], None] = time.sleep,
    max_iterations: int | None = None,
) -> tuple[dict[str, Any], int]:
    """Serve one queued command per cooperative bridge iteration.

    The loop intentionally has no arbitrary execution action: it only consumes
    allowlisted protocol commands. A single command per iteration bounds the
    amount of Resolve work before the next heartbeat and stop check.
    """
    if max_iterations is not None and max_iterations < 1:
        raise ValueError("max_iterations must be at least one when set.")
    state_path = directories["state"] / "bridge.json"
    log_path = directories["logs"] / "bridge.jsonl"
    processed_total = 0
    iterations = 0
    state: dict[str, Any] = {}
    while max_iterations is None or iterations < max_iterations:
        state = collect_bridge_state(resolve)
        _apply_verified_capabilities(state, directories["state"])
        publish_bridge_state(state_path, state, log_path)
        processed_total += process_pending_commands(
            directories,
            state,
            resolve,
            max_commands=1,
        )
        if state.get("stop_requested") is True:
            state.pop("stop_requested", None)
            state["status"] = "stopped"
            state["last_heartbeat"] = utc_now()
            lifecycle = state["lifecycle"]
            if isinstance(lifecycle, dict):
                lifecycle["mode"] = "stopped"
            publish_bridge_state(state_path, state, log_path)
            append_log(
                log_path,
                "INFO",
                "bridge_stopped",
                commands=processed_total,
            )
            return state, processed_total
        publish_bridge_state(state_path, state, log_path)
        iterations += 1
        sleep(POLL_INTERVAL_SECONDS)
    return state, processed_total


def error_state(error: Exception) -> dict[str, Any]:
    """Create a safe, machine-readable bridge failure state."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "bridge_version": BRIDGE_VERSION,
        "status": "error",
        "last_heartbeat": utc_now(),
        "provider": "resolve",
        "product_name": None,
        "resolve_version": None,
        "resolve_version_fields": [],
        "edition": "unknown",
        "project_open": False,
        "project_name": None,
        "current_timeline_id": None,
        "current_timeline_name": None,
        "timelines": [],
        "capabilities": {
            "bridge.ping": False,
            "project.read": "unknown",
            "timeline.read": "unknown",
        },
        "error": {
            "code": "BRIDGE_INITIALIZATION_FAILED",
            "message": str(error),
            "details": {},
            "retryable": True,
        },
    }


def main() -> int:
    """Start the manually launched persistent allowlisted bridge."""
    root = runtime_root()
    directories = ensure_runtime_directories(root)
    log_path = directories["logs"] / "bridge.jsonl"

    try:
        resolve = get_resolve_application()
        state, processed = run_persistent_bridge(resolve, directories)
        append_log(
            log_path,
            "INFO",
            "bridge_run_completed",
            commands=processed,
            status=state["status"],
        )
        print(
            "DaVinci Resolve Agent bridge stopped: "
            f"project={state['project_name']!r}, "
            f"timeline={state['current_timeline_name']!r}, "
            f"commands={processed}"
        )
        return 0
    except Exception as error:
        state = error_state(error)
        atomic_write_json(directories["state"] / "bridge.json", state)
        append_log(
            log_path,
            "ERROR",
            "bridge_run_failed",
            error_code=state["error"]["code"],
            message=state["error"]["message"],
        )
        print(f"DaVinci Resolve Agent bridge failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
