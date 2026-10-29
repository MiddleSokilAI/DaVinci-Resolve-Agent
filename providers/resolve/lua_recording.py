"""Strict native track inspection and recording-preparation inputs.

These operations do not expose microphone routing, signal processing or a
transport start command. Native readback is not proof of recording readiness.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from providers.resolve.lua_editing import bounded_text


def validate_recording(
    action: str, arguments: dict[str, Any], roots: list[Path]
) -> dict[str, Any]:
    """Validate one exact native operation without permitting API expressions.

    Inspection is read-only; writes inherit the caller's project, backup and
    receipt guards. Media-directory changes accept only an existing directory
    resolved inside ``roots``; a symlink cannot escape that allowlist.
    """
    if set(arguments) != {"timeline_name", "recording"}:
        raise ValueError("Expected an exact timeline and recording operation.")
    name = bounded_text(arguments["timeline_name"], "Timeline name")
    spec = arguments["recording"]
    if not isinstance(spec, dict):
        raise ValueError("Recording operation must be an object.")
    operation = spec.get("operation")
    if operation == "inspect":
        if action != "get_timeline_summary" or set(spec) != {"operation", "track_name"}:
            raise ValueError("Inspection requires the read-only action and track name.")
        checked = dict(
            spec, track_name=bounded_text(spec["track_name"], "Audio track name")
        )
    elif operation == "ensure_mono":
        if action != "set_clip_properties" or set(spec) != {
            "operation",
            "track_name",
            "expected_audio_track_count",
        }:
            raise ValueError("Mono preparation requires an exact track and count.")
        count = spec["expected_audio_track_count"]
        if type(count) is not int or not 0 <= count <= 64:
            raise ValueError("Expected audio-track count must be 0..64.")
        checked = dict(
            spec, track_name=bounded_text(spec["track_name"], "Audio track name")
        )
    elif operation == "seek":
        if action != "set_clip_properties" or set(spec) not in (
            {"operation", "timecode"},
            {"operation", "timecode", "page"},
        ):
            raise ValueError("Seeking requires an exact timecode and optional page.")
        timecode = spec["timecode"]
        if not isinstance(timecode, str) or not re.fullmatch(
            r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]:[0-9]{2}", timecode
        ):
            raise ValueError("Timecode must be bounded HH:MM:SS:FF without drop-frame.")
        if "page" in spec and (
            not isinstance(spec["page"], str)
            or spec["page"] not in {"fairlight", "edit"}
        ):
            raise ValueError("Choose the Fairlight or Edit page.")
        checked = dict(spec)
    elif operation == "configure_media_root":
        if action != "set_clip_properties" or set(spec) != {"operation", "media_root"}:
            raise ValueError("Media-root preparation requires one exact directory.")
        raw = bounded_text(spec["media_root"], "Project media directory", 2048)
        candidate = Path(raw)
        if not candidate.is_absolute() or not candidate.is_dir():
            raise ValueError("Project media directory must be absolute and exist.")
        resolved = candidate.resolve(strict=True)
        if not any(resolved.is_relative_to(root.resolve()) for root in roots):
            raise ValueError("Project media directory is outside allowed roots.")
        checked = dict(spec, media_root=resolved.as_posix())
    else:
        raise ValueError("Unknown recording operation.")
    return {"timeline_name": name, "recording": checked}
