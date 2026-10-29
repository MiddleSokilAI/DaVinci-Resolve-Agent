"""Recording response and mailbox boundaries; no desktop or audio is opened."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from agent.client import BridgeProtocolError
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.marker_transport import MarkerSlot, prepare_marker
from providers.resolve.recording_result import recording_result

PROJECT = "recording-project"
NAME = "Чистий голос"


def arguments(operation: str = "ensure_mono", **spec: Any) -> dict[str, Any]:
    """Return a fixed request envelope without source-code interpolation."""
    values: dict[str, Any] = {"operation": operation, **spec}
    if operation in {"inspect", "ensure_mono"}:
        values.setdefault("track_name", NAME)
    if operation == "ensure_mono":
        values.setdefault("expected_audio_track_count", 1)
    return {"timeline_name": "Голос", "recording": values}


def snapshot(path: Path, identity: str = PROJECT) -> None:
    """Write the small real ZIP response accepted by the host transport."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "project.xml",
            f'<SM_Project DbId="{identity}">'
            "<ProjectName>Recording test</ProjectName></SM_Project>",
        )


def native_reply(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str = "recording_ensure_mono_2_2_0_mono_48000_0",
    identity: str = PROJECT,
    error_code: str | None = None,
) -> list[str]:
    """Publish typed responses while retaining actual marker slot ownership."""
    publish = MarkerSlot.publish
    commands: list[str] = []

    def reply(slot: MarkerSlot, deadline: float) -> None:
        publish(slot, deadline)
        command = slot.request["id"]
        commands.append(command)
        if slot.request["action"] == "set_clip_properties":
            snapshot(root / f"{command}.before.drp")
        suffix = "error_" + error_code if error_code else "ok_" + token
        snapshot(root / f"{command}.{suffix}.drp", identity)

    monkeypatch.setattr(MarkerSlot, "publish", reply)
    return commands


def test_recording_ack_is_native_evidence_not_readiness() -> None:
    result = recording_result(
        "recording_inspect_2_2_0_mono_48000_0", arguments("inspect")
    )
    assert result["sample_rate"] == 48000
    assert result["track"] == {
        "name": NAME,
        "found": True,
        "index": 2,
        "subtype": "mono",
        "clip_count": 0,
        "verification": "native_readback",
    }
    assert result["recording_readiness"] == "not_assessed"
    assert result["starts_recording"] is False
    missing = recording_result(
        "recording_inspect_0_1_0_missing_0_0", arguments("inspect")
    )
    assert missing["sample_rate"] is None
    assert missing["track"]["found"] is False
    assert missing["track"]["clip_count"] is None


@pytest.mark.parametrize(
    "token",
    [
        None,
        "recording_ensure_mono_2_2_0_mono_48000_0_extra",
        "recording_inspect_2_2_0_mono_48000_0",
        "recording_ensure_mono_0_2_0_missing_48000_0",
        "recording_ensure_mono_2_2_0_stereo_48000_0",
        "recording_ensure_mono_3_2_0_mono_48000_0",
        "recording_ensure_mono_1_2_0_mono_48000_0",
        "recording_ensure_mono_3_3_0_mono_48000_0",
        "recording_ensure_mono_2_2_0_mono_48000_01000000",
        "recording_ensure_mono_2_65_0_mono_48000_0",
        "recording_ensure_mono_2_2_1_mono_48000_0",
    ],
)
def test_recording_rejects_inconsistent_or_unrelated_ack(token: str | None) -> None:
    with pytest.raises(BridgeProtocolError):
        recording_result(token, arguments())


def test_seek_requires_exact_requested_timecode_readback() -> None:
    spec = arguments("seek", timecode="01:00:00:00")
    result = recording_result("recording_seek_0_0_0_missing_48000_01000000", spec)
    assert result["timecode"] == "01:00:00:00"
    assert result["audio_track_count"] is None
    assert "track" not in result
    with pytest.raises(BridgeProtocolError, match="Seek readback"):
        recording_result("recording_seek_0_0_0_missing_48000_01000001", spec)


@pytest.mark.parametrize("rate", [0, 3000, 768000])
def test_native_sample_rate_bounds_preserve_unknown(rate: int) -> None:
    result = recording_result(
        f"recording_inspect_1_1_0_mono_{rate}_0",
        arguments("inspect"),
    )
    assert result["sample_rate"] == (rate or None)


@pytest.mark.parametrize(
    "token",
    [
        "recording_inspect_1_1_10001_mono_48000_0",
        "recording_inspect_1_1_0_mono_1_0",
        "recording_inspect_1_1_0_mono_2999_0",
        "recording_inspect_1_1_0_mono_768001_0",
        "recording_inspect_0_1_1_missing_48000_0",
    ],
)
def test_inspection_rejects_impossible_native_counts_and_rates(token: str) -> None:
    with pytest.raises(BridgeProtocolError, match="Inconsistent recording track"):
        recording_result(token, arguments("inspect"))


def test_reusing_existing_mono_track_may_report_its_existing_clips() -> None:
    result = recording_result(
        "recording_ensure_mono_1_1_3_mono_48000_0",
        arguments(),
    )
    assert result["audio_track_count"] == 1
    assert result["track"]["clip_count"] == 3


@pytest.mark.parametrize("operation", ["seek", "configure_media_root"])
def test_non_track_operations_report_count_as_unqueried(operation: str) -> None:
    spec = (
        arguments(operation, timecode="01:00:00:00")
        if operation == "seek"
        else arguments(operation, media_root="C:/allowed/media")
    )
    digits = "01000000" if operation == "seek" else "0"
    result = recording_result(
        f"recording_{operation}_0_0_0_missing_48000_{digits}",
        spec,
    )
    assert result["audio_track_count"] is None
    assert "track" not in result
    if operation == "configure_media_root":
        assert result["project_media_location"] == "C:/allowed/media"
        assert result["media_location_verification"] == "native_exact_readback"


@pytest.mark.parametrize("operation", ["seek", "configure_media_root"])
@pytest.mark.parametrize("fields", ["0_2_0_missing", "1_1_0_mono"])
def test_non_track_operations_reject_unrequested_track_evidence(
    operation: str,
    fields: str,
) -> None:
    spec = (
        arguments(operation, timecode="01:00:00:00")
        if operation == "seek"
        else arguments(operation, media_root="C:/allowed/media")
    )
    digits = "01000000" if operation == "seek" else "0"
    with pytest.raises(BridgeProtocolError, match="Unexpected track data"):
        recording_result(f"recording_{operation}_{fields}_48000_{digits}", spec)


@pytest.mark.parametrize("write", [False, True])
def test_old_native_bundle_refuses_recording_before_publication(
    tmp_path: Path,
    write: bool,
) -> None:
    root = tmp_path / "old"
    prepare_marker(root)
    metadata_path = root / "session.json"
    metadata = json.loads(metadata_path.read_text("utf-8"))
    metadata["capabilities"].remove("recording_api_v1")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="freshly prepared"):
        LuaSnapshotClient(root).request(
            "set_clip_properties" if write else "get_timeline_summary",
            1,
            arguments=arguments() if write else arguments("inspect"),
            expected_project_id=PROJECT,
            confirm=write,
            idempotency_key="ensure-one" if write else None,
        )
    assert not (root / "inbox").exists()
    assert not (root / "receipts").exists()
    assert not (root / "client.lock").exists()


def test_mono_write_replays_exact_native_readback_without_redispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    commands = native_reply(root, monkeypatch)
    request: dict[str, Any] = {
        "arguments": arguments(),
        "expected_project_id": PROJECT,
        "confirm": True,
        "idempotency_key": "one-mono-track",
    }
    result = LuaSnapshotClient(root).request("set_clip_properties", 5, **request)
    assert result["recording"]["track"]["index"] == 2
    assert result["readback_verified_by"] == "ResolveLuaRecording"
    assert not (root / "inbox").exists()
    replay = LuaSnapshotClient(root).request("set_clip_properties", 5, **request)
    assert replay == {**result, "replayed": True}
    assert len(commands) == 1
    with pytest.raises(ValueError, match="different arguments"):
        LuaSnapshotClient(root).request(
            "set_clip_properties",
            5,
            **{**request, "arguments": arguments(track_name="Another")},
        )
    assert len(commands) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"identity": "foreign-project"},
        {"token": "recording_ensure_mono_2_2_0_stereo_48000_0"},
        {"token": "recording_ensure_mono_1_2_0_mono_48000_0"},
        {"token": "recording_seek_0_0_0_missing_48000_01000000"},
    ],
)
def test_unverified_write_remains_uncertain_and_blocks_another_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, str],
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    commands = native_reply(root, monkeypatch, **changes)
    request: dict[str, Any] = {
        "arguments": arguments(),
        "expected_project_id": PROJECT,
        "confirm": True,
        "idempotency_key": "one-mono-track",
    }
    with pytest.raises(BridgeProtocolError):
        LuaSnapshotClient(root).request("set_clip_properties", 5, **request)
    receipts = list((root / "receipts").glob("*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text("utf-8"))["status"] == "pending"
    assert (root / "inbox").exists()
    with pytest.raises(RuntimeError):
        LuaSnapshotClient(root).request(
            "set_clip_properties",
            5,
            **{**request, "idempotency_key": "do-not-retry-with-new-key"},
        )
    assert len(commands) == 1


@pytest.mark.parametrize("identity", [PROJECT, "foreign-project"])
def test_recording_inspection_uses_fresh_typed_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    native_reply(
        root,
        monkeypatch,
        token="recording_inspect_2_2_0_mono_48000_0",
        identity=identity,
    )
    if identity != PROJECT:
        with pytest.raises(BridgeProtocolError, match="project changed"):
            LuaSnapshotClient(root).request(
                "get_timeline_summary",
                5,
                arguments=arguments("inspect"),
                expected_project_id=PROJECT,
            )
    else:
        result = LuaSnapshotClient(root).request(
            "get_timeline_summary",
            5,
            arguments=arguments("inspect"),
            expected_project_id=PROJECT,
        )
        assert result["recording"]["track"]["subtype"] == "mono"
        assert result["source"] == "live_lua_api"
    assert not (root / "inbox").exists()


@pytest.mark.parametrize(
    "code,expected_status",
    [
        ("RECORDING_TRACK_NOT_MONO", "rejected"),
        ("RECORDING_TRACK_LIMIT", "rejected"),
        ("RECORDING_MEDIA_POLICY_UNAVAILABLE", "rejected"),
        ("RECORDING_MEDIA_ROOT_READBACK_UNAVAILABLE", "rejected"),
        ("RECORDING_TRACK_RENAME_FAILED", "pending"),
        ("RECORDING_TRACK_CREATE_READBACK_FAILED", "pending"),
        ("RECORDING_MEDIA_ROOT_SET_FAILED", "pending"),
    ],
)
def test_pre_mutation_rejection_releases_slot_but_partial_errors_block_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    expected_status: str,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    request: dict[str, Any] = {
        "arguments": arguments(),
        "expected_project_id": PROJECT,
        "confirm": True,
        "idempotency_key": "conflicting-track",
    }
    with monkeypatch.context() as first_patch:
        first_commands = native_reply(root, first_patch, error_code=code)
        with pytest.raises(BridgeProtocolError, match=code):
            LuaSnapshotClient(root).request("set_clip_properties", 5, **request)
        assert len(first_commands) == 1
        first_receipt = next((root / "receipts").glob("*.json"))
        assert json.loads(first_receipt.read_text("utf-8"))["status"] == expected_status
        assert (root / "inbox").exists() is (expected_status == "pending")
        # A rejected or uncertain command is never blindly submitted again.
        with pytest.raises((BridgeProtocolError, RuntimeError)):
            LuaSnapshotClient(root).request("set_clip_properties", 5, **request)
        assert len(first_commands) == 1
    second_commands = native_reply(root, monkeypatch)
    alternative = {
        **request,
        "arguments": arguments(track_name="Alternative RAW"),
        "idempotency_key": "alternative-track",
    }
    if expected_status == "pending":
        with pytest.raises(RuntimeError):
            LuaSnapshotClient(root).request("set_clip_properties", 5, **alternative)
        assert second_commands == []
        assert (root / "inbox").exists()
    else:
        result = LuaSnapshotClient(root).request(
            "set_clip_properties", 5, **alternative
        )
        assert result["status"] == "completed"
        assert result["recording"]["track"]["name"] == "Alternative RAW"
        assert len(second_commands) == 1
        assert not (root / "inbox").exists()
