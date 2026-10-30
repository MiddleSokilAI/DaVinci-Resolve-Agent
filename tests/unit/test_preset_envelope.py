"""Native preset header bounds and operation identity, without Resolve."""

from __future__ import annotations

import struct

import pytest

from agent.client import BridgeProtocolError
from providers.resolve.preset_envelope import (
    HEADER,
    decode_payload,
    name_from_preset,
    payload_name,
)

SESSION = "a" * 32
COMMAND = "b" * 32


def envelope(name: str, *, version: int = 2, length: int | None = None) -> bytes:
    encoded = name.encode("utf-16be")
    raw = struct.pack(">II", version, len(encoded) if length is None else length)
    return HEADER + b"\r\n" + (raw + encoded + b"opaque-layout").hex().encode()


def test_unicode_and_tag_like_data_roundtrip_without_layout_interpretation() -> None:
    payload = {"name": "ШІ — книга", "value": False, "nested": {"r": "not-an-object"}}
    data = envelope(payload_name(SESSION, COMMAND, "O", payload))
    assert (
        decode_payload(data, session=SESSION, command_id=COMMAND, direction="O")
        == payload
    )


@pytest.mark.parametrize("change", ["session", "command_id", "direction"])
def test_response_cannot_be_reused_for_another_request(change: str) -> None:
    data = envelope(payload_name(SESSION, COMMAND, "O", {"status": "ok"}))
    expected = {"session": SESSION, "command_id": COMMAND, "direction": "O"}
    expected[change] = "c" * 32 if change != "direction" else "I"
    with pytest.raises(BridgeProtocolError):
        decode_payload(data, **expected)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        HEADER + b"\nxyz",
        HEADER + b"\n00",
        envelope("x", version=3),
        envelope("x", length=1),
        envelope("x", length=0xFFFFFFFF),
        envelope("x", length=300),
    ],
)
def test_malformed_or_unsupported_headers_are_rejected(data: bytes) -> None:
    with pytest.raises(BridgeProtocolError):
        name_from_preset(data)


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_ambiguous_or_nonfinite_json_is_rejected(text: str) -> None:
    name = payload_name(SESSION, COMMAND, "O", {})[:-4] + text.encode().hex()
    with pytest.raises(BridgeProtocolError):
        decode_payload(
            envelope(name), session=SESSION, command_id=COMMAND, direction="O"
        )


def test_non_object_payload_is_rejected() -> None:
    name = payload_name(SESSION, COMMAND, "O", {})[:-4] + b"[]".hex()
    with pytest.raises(BridgeProtocolError):
        decode_payload(
            envelope(name), session=SESSION, command_id=COMMAND, direction="O"
        )
