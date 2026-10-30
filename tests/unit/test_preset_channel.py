"""Expired reads recover automatically; no write scope is released this way."""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path
from typing import Any

import pytest

from agent.client import BridgeProtocolError
from providers.resolve.lua_editing import atomic_json
from providers.resolve.marker_transport import MarkerSlot
from providers.resolve.preset_channel import PresetSDKChannel
from providers.resolve.preset_envelope import HEADER


def pending(
    tmp_path: Path, action: str, *, expired: bool = True
) -> tuple[PresetSDKChannel, Path, dict[str, Any]]:
    session = "a" * 32
    name = ("DRA_TEMPLATE_" + session).encode("utf-16be")
    template = struct.pack(">II", 2, len(name)) + name + b"opaque"
    (tmp_path / "sdk-template.preset").write_bytes(
        HEADER + b"\n" + template.hex().encode()
    )
    channel = PresetSDKChannel(tmp_path, session, {})
    packet = {
        "session": session,
        "id": "b" * 32,
        "action": "ping",
        "expires": int(time.time()) + (-5 if expired else 60),
    }
    MarkerSlot(channel.packets, packet, 4096).publish(time.monotonic() + 5)
    trace = channel.trace / ("c" * 32 + ".json")
    record = {
        "session": session,
        "message_id": packet["id"],
        "packet": packet,
        "operation": {"action": action},
        "phase": "uncertain",
    }
    atomic_json(trace, record)
    return channel, trace, record


def test_expired_read_is_released_by_exact_native_packet(tmp_path: Path) -> None:
    channel, trace, _ = pending(tmp_path, "get_current_project")
    channel._recover_expired_read()
    assert not (channel.packets / "inbox").exists()
    assert json.loads(trace.read_text())["phase"] == "expired_read"


@pytest.mark.parametrize("action", ["create_timeline", "set_clip_transform", "unknown"])
def test_write_or_unknown_scope_remains_fenced(tmp_path: Path, action: str) -> None:
    channel, trace, _ = pending(tmp_path, action)
    channel._recover_expired_read()
    assert (channel.packets / "inbox").is_dir()
    assert json.loads(trace.read_text())["phase"] == "uncertain"


def test_unexpired_read_is_not_taken_from_its_owner(tmp_path: Path) -> None:
    channel, trace, _ = pending(tmp_path, "get_current_project", expired=False)
    channel._recover_expired_read()
    assert (channel.packets / "inbox").is_dir()


def test_mismatched_trace_cannot_authorize_packet_cleanup(tmp_path: Path) -> None:
    channel, trace, record = pending(tmp_path, "get_current_project")
    record["packet"]["action"] = "stop"
    atomic_json(trace, record)
    with pytest.raises(BridgeProtocolError, match="differs"):
        channel._recover_expired_read()
    assert (channel.packets / "inbox").is_dir()
