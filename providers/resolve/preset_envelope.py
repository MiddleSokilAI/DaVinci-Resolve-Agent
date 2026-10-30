"""Bounded data envelopes carried by Resolve's native UI-preset exporter.

Only the versioned name header is decoded. The opaque layout is never applied,
interpreted or logged. This is a transport artifact, not a project interchange.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

from agent.client import BridgeProtocolError

HEADER = b"PresetType: UIPreset"
PREFIX = "DRA_RPC1_"
MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_PRESET_BYTES = 16 * 1024 * 1024
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")


def _raw(data: bytes) -> tuple[bytes, int]:
    if len(data) > MAX_PRESET_BYTES:
        raise BridgeProtocolError("Preset envelope exceeds its size bound.")
    lines = data.splitlines()
    if len(lines) != 2 or lines[0] != HEADER:
        raise BridgeProtocolError("Unexpected native preset envelope.")
    text = lines[1]
    if not text or len(text) % 2 or re.fullmatch(rb"[0-9a-fA-F]+", text) is None:
        raise BridgeProtocolError("Invalid preset envelope encoding.")
    raw = bytes.fromhex(text.decode("ascii"))
    if len(raw) < 8:
        raise BridgeProtocolError("Truncated preset envelope header.")
    version, length = struct.unpack(">II", raw[:8])
    if (
        version != 2
        or length % 2
        or length > MAX_PAYLOAD_BYTES * 4 + 1024
        or length > len(raw) - 8
        or len(raw) == length + 8
    ):
        raise BridgeProtocolError("Unsupported native preset name header.")
    return raw, length


def name_from_preset(data: bytes) -> str:
    raw, length = _raw(data)
    try:
        return raw[8 : 8 + length].decode("utf-16be")
    except UnicodeError as error:
        raise BridgeProtocolError("Invalid native preset name.") from error


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError("Nonfinite JSON value: " + value)


def payload_name(
    session: str,
    command_id: str,
    direction: str,
    payload: dict[str, Any],
) -> str:
    if not _TOKEN.fullmatch(session) or not _TOKEN.fullmatch(command_id):
        raise ValueError(
            "Preset transport identifiers must be 32 lowercase hex digits."
        )
    if direction not in {"I", "O"}:
        raise ValueError("Invalid preset envelope direction.")
    data = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(data) > MAX_PAYLOAD_BYTES:
        raise BridgeProtocolError("Preset payload exceeds its size bound.")
    return f"{PREFIX}{session}_{command_id}_{direction}_{data.hex()}"


def decode_payload(
    data: bytes,
    *,
    session: str,
    command_id: str,
    direction: str,
) -> dict[str, Any]:
    # Validate expected identity as well as the untrusted native response header.
    prefix = payload_name(session, command_id, direction, {})[:-4]
    name = name_from_preset(data)
    if not name.startswith(prefix):
        raise BridgeProtocolError("Preset response belongs to another request.")
    encoded = name[len(prefix) :]
    if not encoded or re.fullmatch(r"(?:[0-9a-f]{2})+", encoded) is None:
        raise BridgeProtocolError("Invalid preset payload encoding.")
    if len(encoded) > MAX_PAYLOAD_BYTES * 2:
        raise BridgeProtocolError("Preset payload exceeds its size bound.")
    try:
        result = json.loads(
            bytes.fromhex(encoded).decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise BridgeProtocolError("Invalid JSON in preset response.") from error
    if not isinstance(result, dict):
        raise BridgeProtocolError("Preset payload must be an object.")
    return result


def read_payload(
    path: Path,
    *,
    session: str,
    command_id: str,
    direction: str,
) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > MAX_PRESET_BYTES:
        raise BridgeProtocolError("Invalid preset response path or size.")
    with path.open("rb") as stream:
        data = stream.read(MAX_PRESET_BYTES + 1)
    return decode_payload(
        data,
        session=session,
        command_id=command_id,
        direction=direction,
    )
