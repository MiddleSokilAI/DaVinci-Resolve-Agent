"""Bounded finishing inputs and verification of managed render outputs."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from agent.media import MediaPolicy
from providers.resolve.lua_camera_visibility import validate_camera_visibility
from providers.resolve.lua_editing import bounded_text
from providers.resolve.lua_native_audio import validate_native_audio
from providers.resolve.lua_privacy import validate_privacy, validate_privacy_batch
from providers.resolve.lua_recording import validate_recording
from providers.resolve.lua_ripple import validate_batch, validate_ripple, validate_span

FINISH_ACTIONS = frozenset(
    {"set_clip_properties", "add_subtitles", "prepare_render", "start_render"}
)
PROPERTY_LIMITS = {
    "ZoomX": (0.1, 4),
    "ZoomY": (0.1, 4),
    "Pan": (-3840, 3840),
    "Tilt": (-2160, 2160),
    "Opacity": (0, 100),
    "AudioVolume": (-60, 12),
}


def validate_finishing(
    action: str, a: dict[str, Any], roots: list[Path]
) -> dict[str, Any]:
    if "recording" in a:
        return validate_recording(action, a, roots)
    if action == "set_clip_properties" and "native_audio" in a:
        return validate_native_audio(a)
    if (
        action in {"get_timeline_summary", "set_clip_properties"}
        and "ripple_batch" in a
    ):
        return validate_batch(a, roots)
    if action == "set_clip_properties" and "ripple_repair" in a:
        if set(a) != {"timeline_name", "ripple_repair"}:
            raise ValueError("Invalid repair fields.")
        result = validate_ripple(
            {"timeline_name": a["timeline_name"], "ripple_cut": a["ripple_repair"]},
            roots,
        )
        return {
            "timeline_name": result["timeline_name"],
            "ripple_repair": result["ripple_cut"],
        }
    if action == "set_clip_properties" and "camera_visibility" in a:
        return validate_camera_visibility(a, roots)
    if action == "get_timeline_summary" and "ripple_span" in a:
        return validate_span(a, roots)
    if action == "set_clip_properties" and "ripple_span" in a:
        return validate_span(a, roots)
    if action == "set_clip_properties" and "privacy_batch" in a:
        return validate_privacy_batch(a, roots)
    if action == "set_clip_properties" and "privacy_blur" in a:
        return validate_privacy(a, roots)
    if action in {"get_timeline_summary", "set_clip_properties"} and "ripple_cut" in a:
        return validate_ripple(a, roots)
    if action in {"start_render", "get_render_status"}:
        if (
            set(a) != {"job_id"}
            or not isinstance(a["job_id"], str)
            or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", a["job_id"])
        ):
            raise ValueError("Expected one safe render job ID.")
        return dict(a)
    name = bounded_text(a.get("timeline_name"), "Timeline name")
    if action == "set_clip_properties" and "open_page" in a:
        if set(a) != {"timeline_name", "open_page"} or a["open_page"] not in {
            "edit", "fairlight"
        }:
            raise ValueError("Choose the Edit or Fairlight page.")
        return dict(a, timeline_name=name)
    if action == "get_timeline_summary" and a.get("inspect_camera_visibility") is True:
        base = dict(a)
        del base["inspect_camera_visibility"]
        if "inspect_circle" in base or "inspect_privacy" in base:
            raise ValueError("Choose one inspection type.")
        result = validate_finishing(action, base, roots)
        if result.get("track_type") != "video" or result.get("track_index") != 2:
            raise ValueError("Visibility inspection requires camera V2.")
        return dict(result, inspect_camera_visibility=True)
    if action == "get_timeline_summary" and a.get("inspect_privacy") is True:
        base = dict(a)
        del base["inspect_privacy"]
        if "inspect_circle" in base:
            raise ValueError("Choose one inspection type.")
        result = validate_finishing(action, base, roots)
        if result.get("track_type") != "video" or result.get("track_index") != 1:
            raise ValueError("Privacy inspection requires screen V1.")
        return dict(result, inspect_privacy=True)
    if action == "set_clip_properties" and "replacement_take_path" in a:
        if set(a) != {
            "timeline_name",
            "track_index",
            "item_index",
            "expected_media_path",
            "replacement_take_path",
        }:
            raise ValueError("Invalid replacement take fields.")
        if any(
            type(a[k]) is not int or not 1 <= a[k] <= 1000
            for k in ("track_index", "item_index")
        ):
            raise ValueError("Invalid video item selector.")
        checked_paths = MediaPolicy(roots).validate_files(
            [a["expected_media_path"], a["replacement_take_path"]]
        )
        if Path(checked_paths[0]) == Path(checked_paths[1]):
            raise ValueError("Replacement must be a distinct media file.")
        return dict(
            a,
            timeline_name=name,
            expected_media_path=Path(checked_paths[0]).as_posix(),
            replacement_take_path=Path(checked_paths[1]).as_posix(),
        )
    if action == "set_clip_properties" and set(a) == {
        "timeline_name",
        "empty_timeline_fps",
    }:
        fps = a["empty_timeline_fps"]
        if type(fps) is not int or fps not in {24, 25, 30, 50, 60}:
            raise ValueError("Unsupported timeline frame rate.")
        return {"timeline_name": name, "empty_timeline_fps": fps}
    if action == "get_timeline_summary" and a.get("inspect_circle") is True:
        base = dict(a)
        del base["inspect_circle"]
        result = validate_finishing(action, base, roots)
        if result.get("track_type") != "video":
            raise ValueError("Circle inspection needs a video item.")
        return dict(result, inspect_circle=True)
    if action == "set_clip_properties" and "circle_mask" in a:
        if set(a) != {
            "timeline_name",
            "track_index",
            "item_index",
            "expected_media_path",
            "circle_mask",
        }:
            raise ValueError("Invalid circle mask fields.")
        if any(
            type(a[k]) is not int or not 1 <= a[k] <= 1000
            for k in ("track_index", "item_index")
        ):
            raise ValueError("Invalid circle item selector.")
        mask = a["circle_mask"]
        if not isinstance(mask, dict) or set(mask) != {
            "center_x",
            "center_y",
            "diameter",
        }:
            raise ValueError("Expected bounded circle geometry.")
        for key, value in mask.items():
            low, high = (0.05, 0.9) if key == "diameter" else (0.05, 0.95)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError("Invalid circle geometry.")
        circle_path = MediaPolicy(roots).validate_files([a["expected_media_path"]])[0]
        return dict(
            a, timeline_name=name, expected_media_path=Path(circle_path).as_posix()
        )
    if action == "set_clip_properties" and "preview_start" in a:
        if set(a) - {"timeline_name", "preview_start", "preview_frame"}:
            raise ValueError("Invalid preview fields.")
        if a["preview_start"] is not True:
            raise ValueError("Preview start must be true.")
        if "preview_frame" in a:
            frame = a["preview_frame"]
            if type(frame) is not int or not 0 <= frame <= 10_000_000:
                raise ValueError("Invalid preview frame.")
            return {
                "timeline_name": name,
                "preview_start": True,
                "preview_frame": frame,
            }
        return {"timeline_name": name, "preview_start": True}
    if action == "get_timeline_summary" and set(a) == {
        "timeline_name",
        "track_type",
        "track_index",
        "item_index",
        "expected_media_path",
    }:
        if a["track_type"] not in {"video", "audio"} or any(
            type(a[k]) is not int or not 1 <= a[k] <= 1000
            for k in ("track_index", "item_index")
        ):
            raise ValueError("Invalid timeline item selector.")
        item_path = MediaPolicy(roots).validate_files([a["expected_media_path"]])[0]
        return dict(
            a, timeline_name=name, expected_media_path=Path(item_path).as_posix()
        )
    if action == "prepare_render" and set(a) == {
        "timeline_name",
        "start_frame",
        "end_frame",
    }:
        start, end = a["start_frame"], a["end_frame"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= 2_147_483_647
        ):
            raise ValueError("Render range requires integer start and exclusive end.")
        return {"timeline_name": name, "start_frame": start, "end_frame": end}
    if action in {"prepare_render", "get_timeline_summary"} and set(a) == {
        "timeline_name"
    }:
        return {"timeline_name": name}
    if action == "set_clip_properties" and set(a) == {
        "timeline_name",
        "track_type",
        "track_index",
        "item_index",
        "expected_media_path",
        "properties",
    }:
        kind = a["track_type"]
        if kind not in {"video", "audio"} or any(
            type(a[k]) is not int or not 1 <= a[k] <= 1000
            for k in ("track_index", "item_index")
        ):
            raise ValueError("Invalid timeline item selector.")
        media_path = MediaPolicy(roots).validate_files([a["expected_media_path"]])[0]
        props = a["properties"]
        if not isinstance(props, dict) or not props:
            raise ValueError("At least one allowlisted property is required.")
        for key, value in props.items():
            if key not in PROPERTY_LIMITS or (kind == "audio") != (
                key == "AudioVolume"
            ):
                raise ValueError("Unsupported property for this track type.")
            low, high = PROPERTY_LIMITS[key]
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError("Property value outside allowed range.")
        return dict(
            a, timeline_name=name, expected_media_path=Path(media_path).as_posix()
        )
    if action == "add_subtitles" and set(a) == {"timeline_name", "subtitle_path"}:
        path = Path(MediaPolicy(roots).validate_files([a["subtitle_path"]])[0])
        if path.suffix.lower() != ".srt" or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Expected a bounded UTF-8 SRT file.")
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
        cues = []

        def seconds(value: str) -> float:
            h, m, s, ms = map(int, re.split("[:,]", value))
            if m >= 60 or s >= 60:
                raise ValueError("Invalid SRT timestamp.")
            return h * 3600 + m * 60 + s + ms / 1000

        previous_end = 0.0
        for block in re.split(r"\n\s*\n", text):
            lines = block.splitlines()
            if len(lines) < 3 or not lines[0].isdigit():
                raise ValueError("Malformed SRT cue.")
            match = re.fullmatch(
                r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})", lines[1]
            )
            if not match:
                raise ValueError("Malformed SRT timing.")
            start, end = seconds(match[1]), seconds(match[2])
            if start < previous_end or end <= start or end > 12 * 3600:
                raise ValueError("Overlapping, reversed or excessive subtitle range.")
            previous_end = end
            cues.append({"start": start, "end": end})
        if not 1 <= len(cues) <= 2000:
            raise ValueError("Expected 1–2000 subtitle cues.")
        return {
            "timeline_name": name,
            "subtitle_path": path.as_posix(),
            "cue_count": len(cues),
            "first_start": cues[0]["start"],
            "last_end": cues[-1]["end"],
        }
    raise ValueError("Unknown finishing action or unexpected fields.")


def verify_video(path: Path) -> dict[str, Any]:
    import av

    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Completed render has no non-empty output file.")
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("Render contains no video stream.")
        video = container.streams.video[0]
        frame = next(container.decode(video), None)
        if frame is None:
            raise ValueError("Rendered video cannot be decoded.")
        audio_verified = None
        if container.streams.audio:
            container.seek(0)
            audio = next(container.decode(audio=0), None)
            if audio is None or audio.samples == 0:
                raise ValueError("Rendered audio stream cannot be decoded.")
            audio_verified = True
        return {
            "path": str(path),
            "bytes": path.stat().st_size,
            "width": video.width,
            "height": video.height,
            "audio_streams": len(container.streams.audio),
            "duration_seconds": container.duration / 1_000_000
            if container.duration
            else None,
            "video_decode_verified": True,
            "audio_decode_verified": audio_verified,
        }
