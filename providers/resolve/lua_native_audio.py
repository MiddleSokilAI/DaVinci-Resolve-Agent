"""Strict inputs for documented Resolve audio APIs; no offline processing."""

import math
from typing import Any

from providers.resolve.lua_editing import bounded_text


def validate_native_audio(a: dict[str, Any]) -> dict[str, Any]:
    if set(a) != {"timeline_name", "native_audio"}:
        raise ValueError("Expected an exact timeline and native audio operation.")
    name = bounded_text(a["timeline_name"], "Timeline name")
    spec = a["native_audio"]
    if not isinstance(spec, dict):
        raise ValueError("Native audio operation must be an object.")
    if spec.get("operation") == "voice_isolation":
        if set(spec) != {
            "operation",
            "track_index",
            "enabled",
            "amount",
            "expected_state",
        }:
            raise ValueError("Expected exact voice-isolation state and track.")
        if type(spec["track_index"]) is not int or not 1 <= spec["track_index"] <= 64:
            raise ValueError("Invalid audio track.")
        if (
            type(spec["enabled"]) is not bool
            or type(spec["amount"]) is not int
            or not 0 <= spec["amount"] <= 40
        ):
            raise ValueError("Voice-isolation pilot amount must be 0..40.")
        state = spec["expected_state"]
        if (
            not isinstance(state, dict)
            or set(state) != {"isEnabled", "amount"}
            or type(state["isEnabled"]) is not bool
            or type(state["amount"]) is not int
            or not 0 <= state["amount"] <= 100
        ):
            raise ValueError("Invalid expected voice-isolation state.")
        return {"timeline_name": name, "native_audio": spec}
    if spec.get("operation") == "discard_failed_batch":
        if (
            set(spec) != {"operation", "source_count", "items"}
            or type(spec["source_count"]) is not int
            or not 1 <= spec["source_count"] <= 1000
        ):
            raise ValueError("Expected exact failed batch.")
        if not isinstance(spec["items"], list) or not 1 <= len(spec["items"]) <= 2000:
            raise ValueError("Expected failed batch items.")
        for item in spec["items"]:
            if (
                set(item) != {"start_frame", "end_frame", "source_start_frame"}
                or any(type(v) is not int or v < 0 for v in item.values())
                or item["end_frame"] <= item["start_frame"]
            ):
                raise ValueError("Invalid failed batch item.")
        return {"timeline_name": name, "native_audio": spec}
    if spec.get("operation") == "discard_failed_pilot":
        if set(spec) != {
            "operation",
            "source_count",
            "start_frame",
            "end_frame",
            "source_start_frame",
        }:
            raise ValueError("Expected the exact muted failed-pilot item.")
        if any(
            type(v) is not int or v < 0 for k, v in spec.items() if k != "operation"
        ):
            raise ValueError("Invalid failed-pilot bounds.")
        if (
            not 1 <= spec["source_count"] <= 1000
            or spec["end_frame"] <= spec["start_frame"]
        ):
            raise ValueError("Invalid failed-pilot bounds.")
        return {"timeline_name": name, "native_audio": spec}
    if spec.get("operation") == "phrase_batch":
        if set(spec) != {
            "operation",
            "source_count",
            "first_item",
            "last_item",
            "expected_a2_count",
            "partitions",
            "finalize",
        }:
            raise ValueError("Invalid phrase batch.")
        if any(
            type(spec[k]) is not int
            for k in ("source_count", "first_item", "last_item", "expected_a2_count")
        ):
            raise ValueError("Batch indexes must be integers.")
        if not (
            1 <= spec["first_item"] <= spec["last_item"] <= spec["source_count"] <= 1000
        ):
            raise ValueError("Invalid batch range.")
        if (
            spec["last_item"] - spec["first_item"] >= 20
            or not 0 <= spec["expected_a2_count"] <= 20000
        ):
            raise ValueError("Batch is too large.")
        if (spec["first_item"] == 1) != (spec["expected_a2_count"] == 0):
            raise ValueError("Invalid initial batch.")
        if type(spec["finalize"]) is not bool or spec["finalize"] != (
            spec["last_item"] == spec["source_count"]
        ):
            raise ValueError("Only the complete final batch may enable A2.")
        partitions = spec["partitions"]
        if (
            not isinstance(partitions, list)
            or len(partitions) != spec["last_item"] - spec["first_item"] + 1
        ):
            raise ValueError("Missing source partitions.")
        for index, part in enumerate(partitions, spec["first_item"]):
            if not isinstance(part, dict) or part.get("item_index") != index:
                raise ValueError("Source partitions must be ordered.")
            validate_native_audio(
                {
                    "timeline_name": name,
                    "native_audio": dict(part, operation="phrase_pilot"),
                }
            )
            if any("max_gain_db" not in region for region in part["regions"]):
                raise ValueError("Batch regions require measured peak gain caps.")
        return {"timeline_name": name, "native_audio": spec}
    if spec.get("operation") == "phrase_pilot":
        if set(spec) != {
            "operation",
            "item_index",
            "start_frame",
            "end_frame",
            "regions",
        }:
            raise ValueError("Expected one exact pilot item and its partition.")
        if any(
            type(spec[k]) is not int for k in ("item_index", "start_frame", "end_frame")
        ):
            raise ValueError("Pilot bounds must be frames.")
        if not (
            1 <= spec["item_index"] <= 10000
            and 0 <= spec["start_frame"] < spec["end_frame"] <= 2147483647
        ):
            raise ValueError("Invalid pilot selector.")
        regions = spec["regions"]
        if not isinstance(regions, list) or not 1 <= len(regions) <= 128:
            raise ValueError("Requires 1..128 contiguous regions.")
        cursor = spec["start_frame"]
        for region in regions:
            if not isinstance(region, dict) or set(region) not in (
                {"start_frame", "end_frame", "normalize"},
                {"start_frame", "end_frame", "normalize", "max_gain_db"},
            ):
                raise ValueError("Invalid pilot region.")
            cap = region.get("max_gain_db", 4)
            if (
                type(cap) not in (int, float)
                or not math.isfinite(cap)
                or not -24 <= cap <= 4
            ):
                raise ValueError("Invalid measured peak gain cap.")
            if (
                type(region["start_frame"]) is not int
                or type(region["end_frame"]) is not int
                or type(region["normalize"]) is not bool
            ):
                raise ValueError("Invalid pilot region values.")
            if (
                region["start_frame"] != cursor
                or not cursor < region["end_frame"] <= spec["end_frame"]
            ):
                raise ValueError(
                    "Pilot regions must cover the item without gaps or overlaps."
                )
            cursor = region["end_frame"]
        if cursor != spec["end_frame"]:
            raise ValueError("Incomplete pilot coverage.")
        return {"timeline_name": name, "native_audio": spec}
    if spec.get("operation") == "preset":
        if set(spec) != {"operation", "preset_name"}:
            raise ValueError("Expected one existing Fairlight preset.")
        return {
            "timeline_name": name,
            "native_audio": {
                "operation": "preset",
                "preset_name": bounded_text(spec["preset_name"], "Preset name"),
            },
        }
    if (
        set(spec)
        != {
            "operation",
            "track_index",
            "items",
            "normalization_mode",
            "target_loudness",
            "target_peak",
        }
        or spec["operation"] != "normalize"
    ):
        raise ValueError("Unsupported native audio operation.")
    if type(spec["track_index"]) is not int or not 1 <= spec["track_index"] <= 64:
        raise ValueError("Invalid audio track.")
    for key, low, high in (("target_loudness", -24, -14), ("target_peak", -12, -1)):
        value = spec[key]
        if (
            type(value) not in (float, int)
            or not math.isfinite(value)
            or not low <= value <= high
        ):
            raise ValueError("Audio target outside the supported range.")
    mode = bounded_text(spec["normalization_mode"], "Normalization mode")
    items = spec["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 500:
        raise ValueError("Select 1..500 exact audio items.")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "index",
            "start_frame",
            "end_frame",
        }:
            raise ValueError("Expected exact item index and timeline bounds.")
        if (
            any(type(v) is not int for v in item.values())
            or not (
                1 <= item["index"] <= 10000
                and 0 <= item["start_frame"] < item["end_frame"] <= 2147483647
            )
            or item["index"] in seen
        ):
            raise ValueError("Invalid or duplicate audio item.")
        seen.add(item["index"])
    return {"timeline_name": name, "native_audio": dict(spec, normalization_mode=mode)}
