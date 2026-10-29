"""Validate compact native recording acknowledgements without guessing readiness."""

from __future__ import annotations

import re
from typing import Any

from agent.client import BridgeProtocolError


def recording_result(token: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
    """Decode one bounded ACK and require agreement with its original operation."""
    match = re.fullmatch(
        r"recording_(inspect|ensure_mono|seek|configure_media_root)_"
        r"(\d+)_(\d+)_(\d+)_(mono|stereo|missing)_(\d+)_(0|\d{8})",
        token or "",
    )
    if not match or match[1] != arguments["recording"]["operation"]:
        raise BridgeProtocolError("Invalid recording acknowledgement.")
    index, count, clips, rate = (int(match[n]) for n in (2, 3, 4, 6))
    subtype, digits = match[5], match[7]
    if (
        not 0 <= index <= count <= 64
        or clips > 10000
        or rate != 0 and not 3000 <= rate <= 768000
        or (index == 0) != (subtype == "missing")
        or index == 0 and clips != 0
    ):
        raise BridgeProtocolError("Inconsistent recording track acknowledgement.")
    spec = arguments["recording"]
    if match[1] == "ensure_mono":
        expected = spec["expected_audio_track_count"]
        if index == 0 or subtype != "mono" or count not in {expected, expected + 1}:
            raise BridgeProtocolError("Mono track readback did not match the request.")
        if count == expected + 1 and index != count:
            raise BridgeProtocolError("New mono track was not appended as requested.")
        if count == expected + 1 and clips != 0:
            raise BridgeProtocolError("New mono track unexpectedly contains clips.")
    if match[1] in {"seek", "configure_media_root"} and (
        index != 0 or count != 0 or clips != 0 or subtype != "missing"
    ):
        raise BridgeProtocolError("Unexpected track data for a non-track operation.")
    if match[1] == "seek":
        if digits != spec["timecode"].replace(":", ""):
            raise BridgeProtocolError(
                "Seek readback did not match the requested timecode."
            )
    elif digits != "0":
        raise BridgeProtocolError("Unexpected playhead acknowledgement.")
    result: dict[str, Any] = {
        "timeline": arguments["timeline_name"],
        "operation": match[1],
        "audio_track_count": count if "track_name" in spec else None,
        "sample_rate": rate or None,
        "source": "live_lua_api",
        "recording_readiness": "not_assessed",
        "starts_recording": False,
    }
    if "track_name" in spec:
        result["track"] = {
            "name": spec["track_name"],
            "found": index > 0,
            "index": index or None,
            "subtype": None if subtype == "missing" else subtype,
            "clip_count": clips if index else None,
            "verification": "native_readback",
        }
    if match[1] == "seek":
        result["timecode"] = spec["timecode"]
    if match[1] == "configure_media_root":
        result["project_media_location"] = spec["media_root"]
        result["media_location_verification"] = "native_exact_readback"
    return result
