"""Validation and durable, session-scoped write receipts for the Lua bridge."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from agent.media import MediaPolicy

WRITE_ACTIONS = frozenset(
    {
        "import_media",
        "create_timeline",
        "create_project",
        "duplicate_timeline",
        "append_clip",
        "quit_resolve",
    }
)


def bounded_text(value: Any, label: str, limit: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > limit
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError(f"{label} must be non-empty bounded text without controls.")
    return value


def validate_edit(
    action: str, arguments: dict[str, Any], roots: list[Path]
) -> dict[str, Any]:
    if action == "quit_resolve" and not arguments:
        return {}

    def paths(values: Any) -> list[str]:
        if not isinstance(values, list) or not 1 <= len(values) <= 20:
            raise ValueError("Import requires 1 to 20 paths.")
        normalized = MediaPolicy(roots).validate_files(values)
        if len({os.path.normcase(p) for p in normalized}) != len(normalized):
            raise ValueError("Duplicate media paths are not accepted.")
        if any(
            Path(p).suffix.lower()
            not in {".mp4", ".mov", ".mkv", ".wav", ".mp3", ".flac", ".mxf", ".avi"}
            for p in normalized
        ):
            raise ValueError("Unsupported media extension.")
        return [Path(p).as_posix() for p in normalized]

    if action == "import_media" and set(arguments) == {"paths"}:
        return {"paths": paths(arguments["paths"])}
    if action in {"create_timeline", "create_project"} and set(arguments) == {"name"}:
        label = "Project name" if action == "create_project" else "Timeline name"
        return {"name": bounded_text(arguments["name"], label)}
    if action == "duplicate_timeline" and set(arguments) == {
        "name",
        "source_timeline_name",
    }:
        name = bounded_text(arguments["name"], "Timeline name")
        source = bounded_text(arguments["source_timeline_name"], "Source timeline name")
        if name == source:
            raise ValueError("Duplicate destination must differ from its source.")
        return {"name": name, "source_timeline_name": source}
    if action == "append_clip" and set(arguments) == {"timeline_name", "sync_groups"}:
        groups = arguments["sync_groups"]
        if not isinstance(groups, list) or not 1 <= len(groups) <= 25:
            raise ValueError("Provide 1 to 25 synchronized groups.")
        normalized_groups = []
        for group in groups:
            if isinstance(group, dict) and set(group) == {
                "screen_path",
                "camera_path",
                "camera_delay_frames",
            }:
                screen, camera = paths([group["screen_path"], group["camera_path"]])
                delay = group["camera_delay_frames"]
                if type(delay) is not int or not 0 <= delay <= 600:
                    raise ValueError("Full-pair camera delay must be 0 to 600 frames.")
                normalized_groups.append(
                    dict(group, screen_path=screen, camera_path=camera)
                )
                continue
            if not isinstance(group, dict) or set(group) != {
                "screen_path",
                "camera_path",
                "screen_start_frame",
                "camera_start_frame",
                "frame_count",
            }:
                raise ValueError("Invalid synchronized group fields.")
            screen, camera = paths([group["screen_path"], group["camera_path"]])
            for key in ("screen_start_frame", "camera_start_frame", "frame_count"):
                if type(group[key]) is not int or not 0 <= group[key] <= 2_147_483_647:
                    raise ValueError(
                        "Synchronized ranges require nonnegative integer frames."
                    )
            if group["frame_count"] < 1:
                raise ValueError("Synchronized duration must be positive.")
            normalized_groups.append(
                dict(group, screen_path=screen, camera_path=camera)
            )
        return {
            "timeline_name": bounded_text(arguments["timeline_name"], "Timeline name"),
            "sync_groups": normalized_groups,
        }
    if action == "append_clip" and set(arguments) == {
        "timeline_name",
        "media_path",
        "start_frame",
        "end_frame",
    }:
        start, end = arguments["start_frame"], arguments["end_frame"]
        if (start is None) != (end is None):
            raise ValueError("Provide both source frame bounds or neither.")
        if start is not None and (
            any(type(v) is not int for v in (start, end))
            or not 0 <= start <= end <= 2_147_483_647
        ):
            raise ValueError(
                "Source range must be nonnegative inclusive integer frames."
            )
        return {
            "timeline_name": bounded_text(arguments["timeline_name"], "Timeline name"),
            "media_path": paths([arguments["media_path"]])[0],
            "start_frame": start,
            "end_frame": end,
        }
    raise ValueError("Unknown edit action or unexpected arguments.")


def lua_value(value: Any) -> str:
    from providers.resolve.lua_transport import lua_string

    if value is None:
        return "nil"
    if type(value) is bool:
        return "true" if value else "false"
    if isinstance(value, str):
        return lua_string(value)
    if type(value) in (int, float) and math.isfinite(value):
        return str(value)
    if isinstance(value, list):
        return "{" + ",".join(lua_value(v) for v in value) + "}"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                "[" + lua_string(k) + "]=" + lua_value(v) for k, v in value.items()
            )
            + "}"
        )
    raise ValueError("Unsupported Lua envelope value.")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class WriteReceipt:
    def __init__(
        self, root: Path, key: str, action: str, project: str, arguments: dict[str, Any]
    ) -> None:
        bounded_text(key, "Idempotency key")
        folder = root / "receipts"
        folder.mkdir(exist_ok=True)
        self.path = folder / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        self.fingerprint = hashlib.sha256(
            json.dumps(
                [action, project, arguments],
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        self.value: dict[str, Any] = {}

    def replay(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        self.value = json.loads(self.path.read_text(encoding="utf-8"))
        if self.value.get("fingerprint") != self.fingerprint:
            raise ValueError(
                "Idempotency key was already used for different arguments."
            )
        if self.value.get("status") != "completed":
            raise RuntimeError(
                "Previous write has no confirmed success; inspect its backup/response "
                "before retrying. No write was resubmitted."
            )
        return dict(self.value["result"], replayed=True)

    def begin(self, command_id: str) -> None:
        for path in self.path.parent.glob("*.json"):
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous.get("status") == "pending":
                raise RuntimeError(
                    "An earlier write has an uncertain outcome. Resolve that receipt "
                    "before submitting another write."
                )
        self.value = {
            "fingerprint": self.fingerprint,
            "command_id": command_id,
            "status": "pending",
        }
        atomic_json(self.path, self.value)

    def complete(self, result: dict[str, Any]) -> None:
        self.value.update(status="completed", result=result)
        atomic_json(self.path, self.value)

    def reject(self, code: str) -> None:
        self.value.update(status="rejected", error=code)
        atomic_json(self.path, self.value)
