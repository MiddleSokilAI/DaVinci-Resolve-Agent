"""Host boundaries for data-only publication and acknowledgement-only recovery."""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from agent.client import BridgeProtocolError, CommandTimeoutError
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.marker_transport import (
    HEADER,
    MAGIC,
    WIRE_VERSION,
    MarkerLeaseKeeper,
    MarkerSlot,
    MarkerSlotBusyError,
    MarkerTransportError,
    encode_request,
    inspect_pending,
    prepare_marker,
    publish_lease,
)


def request(root: Path, **extra: Any) -> dict[str, Any]:
    metadata = json.loads((root / "session.json").read_text(encoding="utf-8"))
    return (
        dict(
            session=metadata["session"],
            id=uuid4().hex,
            action="ping",
            expires=int(time.time()) + 30,
        )
        | extra
    )


def snapshot(path: Path, identity: str = "project-id") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "project.xml",
            f'<SM_Project DbId="{identity}">'
            "<ProjectName>Test</ProjectName></SM_Project>",
        )


def slot_request(root: Path) -> MarkerSlot:
    owner = json.loads((root / "inbox" / ".owner.json").read_text())
    return MarkerSlot.existing(root, owner["session"], owner["id"], 4096)


def dispatch_without_ack(
    client: LuaSnapshotClient,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    **arguments: Any,
) -> None:
    """Keep real publication; expire ACK waiting independently of disk speed."""
    monotonic = time.monotonic
    publish = MarkerSlot.publish
    advance = 0.0

    def clock() -> float:
        return monotonic() + advance

    def publish_then_expire(slot: MarkerSlot, deadline: float) -> None:
        nonlocal advance
        publish(slot, deadline)
        advance = 120

    with monkeypatch.context() as context:
        context.setattr(time, "monotonic", clock)
        context.setattr(MarkerSlot, "publish", publish_then_expire)
        with pytest.raises(CommandTimeoutError, match="marker response"):
            client.request(action, 30, **arguments)


def test_atomic_marker_slot_roundtrips_unicode_without_executable_payload(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    envelope = request(
        root,
        action="create_timeline",
        project_id="project-id",
        confirm=True,
        arguments={"name": 'Український "тест"; os.execute("never")'},
    )
    slot = MarkerSlot(root, envelope, 4096)
    assert not (root / "inbox").exists()
    slot.publish(time.monotonic() + 30)
    assert slot_request(root).request == envelope
    assert (root / "request.lua").read_text() == "return nil\n"
    magic, version, length, _ = HEADER.unpack(slot.packet[:8])
    assert (magic, version, length) == (MAGIC, WIRE_VERSION, len(slot.packet) - 8)
    slot.clear()
    assert not (root / "inbox").exists()
    assert not list(root.glob(".inbox-*.tmp"))


def test_occupied_and_modified_slots_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    first = MarkerSlot(root, request(root), 4096)
    first.publish(time.monotonic() + 30)
    second = MarkerSlot(root, request(root), 4096)
    with pytest.raises(MarkerSlotBusyError):
        second.publish(time.monotonic() + 30)
    foreign = root / "inbox" / "keep-me.txt"
    foreign.write_text("foreign data")
    with pytest.raises(MarkerTransportError, match="unknown content"):
        first.clear()
    assert foreign.read_text() == "foreign data"
    assert (root / "inbox" / "ready.marker").is_file()


def test_corrupt_marker_cannot_be_used_for_recovery(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    slot = MarkerSlot(root, request(root), 4096)
    slot.publish(time.monotonic() + 30)
    (root / "inbox" / "b00000_0.bit").unlink()
    with pytest.raises(MarkerTransportError, match="integrity"):
        slot_request(root)


@pytest.mark.parametrize("limit", [True, 0, 4097])
def test_invalid_payload_policy_does_not_create_runtime(
    tmp_path: Path,
    limit: Any,
) -> None:
    root = tmp_path / "session"
    with pytest.raises(ValueError):
        prepare_marker(root, max_payload_bytes=limit)
    assert not root.exists()


def test_size_limit_failure_does_not_leave_an_uncertain_receipt(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare_marker(root, max_payload_bytes=128)
    with pytest.raises(ValueError, match="payload bytes"):
        LuaSnapshotClient(root).request(
            "create_timeline",
            arguments={"name": "Проба"},
            expected_project_id="project-id",
            confirm=True,
            idempotency_key="oversize",
        )
    assert not (root / "inbox").exists()
    assert not list((root / "receipts").glob("*.json"))
    assert not (root / "client.lock").exists()


def test_read_timeout_releases_own_slot_but_does_not_rewrite_request_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    dispatch_without_ack(LuaSnapshotClient(root), monkeypatch, "ping")
    assert not (root / "inbox").exists()
    assert (root / "request.lua").read_text() == "return nil\n"
    assert not (root / "client.lock").exists()


def test_uncertain_write_resumes_original_ack_without_republication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    client = LuaSnapshotClient(root)
    arguments: dict[str, Any] = dict(
        arguments={"name": "Пробний таймлайн"},
        expected_project_id="project-id",
        confirm=True,
        idempotency_key="write-once",
    )
    dispatch_without_ack(client, monkeypatch, "create_timeline", **arguments)
    original = slot_request(root)
    diagnostics = inspect_pending(root)
    assert diagnostics["pending_receipts"][0]["command_id"] == original.identity
    assert diagnostics["inbox"]["id"] == original.identity
    with pytest.raises(RuntimeError, match="uncertain"):
        client.request(
            "create_timeline", 0.01, **dict(arguments, idempotency_key="different")
        )
    with pytest.raises(ValueError, match="different arguments"):
        client.request(
            "create_timeline", 0.01, **dict(arguments, arguments={"name": "Changed"})
        )
    # The late native result belongs to the original ID and requires its backup.
    snapshot(root / f"{original.identity}.before.drp")
    snapshot(root / f"{original.identity}.ok.drp")

    def forbid_publication(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Reconciliation must not republish a command")

    monkeypatch.setattr(MarkerSlot, "publish", forbid_publication)
    result = client.request("create_timeline", 1, **arguments)
    assert result["status"] == "completed"
    assert result["reconciled_original_command"] is True
    assert result["response_path"].endswith(f"{original.identity}.ok.drp")
    assert not (root / "inbox").exists()
    assert inspect_pending(root)["pending_receipts"] == []
    assert client.request("create_timeline", 1, **arguments)["replayed"] is True


def test_pending_recovery_rejects_wrong_project_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    client = LuaSnapshotClient(root)
    arguments: dict[str, Any] = dict(
        arguments={"name": "Test"},
        expected_project_id="project-id",
        confirm=True,
        idempotency_key="mismatch",
    )
    dispatch_without_ack(client, monkeypatch, "create_timeline", **arguments)
    original = slot_request(root)
    snapshot(root / f"{original.identity}.before.drp", "other-project")
    snapshot(root / f"{original.identity}.ok.drp")
    with pytest.raises(BridgeProtocolError, match="backup identity"):
        client.request("create_timeline", 1, **arguments)
    assert (root / "inbox").is_dir()
    assert len(inspect_pending(root)["pending_receipts"]) == 1


def test_marker_hot_reload_rejected_without_publication(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    with pytest.raises(ValueError, match="embedded"):
        LuaSnapshotClient(root).request("reload_modules")
    assert not (root / "client.lock").exists()
    assert not (root / "inbox").exists()


def test_lease_rotation_preserves_foreign_files_and_keeper_stops(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root, lease_seconds=5)
    with MarkerLeaseKeeper(root) as keeper:
        assert keeper.expires is not None
        assert keeper.last_error is None
    assert keeper._thread is not None and not keeper._thread.is_alive()
    foreign = root / "lease" / "foreign.txt"
    foreign.write_text("keep")
    with pytest.raises(MarkerTransportError, match="unknown content"):
        publish_lease(root)
    assert foreign.read_text() == "keep"
    assert (root / "lease" / "ready.marker").exists()
    assert not (root / "lease-publish.lock").exists()


def test_lease_rotation_retries_transient_windows_directory_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    rename = Path.rename
    attempts = 0

    def locked(source: Path, target: Path) -> Path:
        nonlocal attempts
        if source == root / "lease":
            attempts += 1
            if attempts < 3:
                raise PermissionError("temporary directory handle")
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", locked)
    publish_lease(root)
    assert attempts == 3
    assert (root / "lease/ready.marker").is_file()
    assert not (root / "lease-publish.lock").exists()
    assert not list(root.glob(".lease-retired-*"))


def test_permanent_lease_directory_lock_keeps_original_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    before = {path.name: path.read_bytes() for path in (root / "lease").iterdir()}
    rename = Path.rename
    clock = 0.0

    def now() -> float:
        nonlocal clock
        clock += 0.5
        return clock

    def locked(source: Path, target: Path) -> Path:
        if source == root / "lease":
            raise PermissionError("persistent directory handle")
        return rename(source, target)

    monkeypatch.setattr(time, "monotonic", now)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(PermissionError, match="persistent"):
        publish_lease(root)
    assert before == {
        path.name: path.read_bytes() for path in (root / "lease").iterdir()
    }
    assert not (root / "lease-publish.lock").exists()


def test_nonfinite_json_and_unexpected_envelope_fields_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    with pytest.raises(ValueError):
        encode_request(request(root, arguments={"value": float("nan")}))
    with pytest.raises(ValueError, match="fields"):
        encode_request(request(root, code="os.execute('never')"))


def test_staging_past_deadline_never_exposes_write_or_uncertain_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from providers.resolve import marker_transport

    root = tmp_path / "session"
    prepare_marker(root)
    original = marker_transport._stage_bits

    def slow_stage(*args: Any, **kwargs: Any) -> Path:
        result = original(*args, **kwargs)
        if result.name.startswith(".inbox-"):
            time.sleep(0.05)
        return result

    monkeypatch.setattr(marker_transport, "_stage_bits", slow_stage)
    with pytest.raises(TimeoutError, match="before publication"):
        LuaSnapshotClient(root).request(
            "create_timeline",
            0.01,
            arguments={"name": "Not dispatched"},
            expected_project_id="project-id",
            confirm=True,
            idempotency_key="not-dispatched",
        )
    saved = json.loads(next((root / "receipts").glob("*.json")).read_text())
    assert (saved["status"], saved["error"]) == ("rejected", "HOST_NOT_PUBLISHED")
    assert not (root / "inbox").exists()
    assert not list(root.glob(".inbox-*.tmp"))
    assert not (root / "client.lock").exists()


def test_completed_replay_retries_only_owned_partial_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    client = LuaSnapshotClient(root)
    arguments: dict[str, Any] = dict(
        arguments={"name": "Test"},
        expected_project_id="project-id",
        confirm=True,
        idempotency_key="cleanup-once",
    )
    dispatch_without_ack(client, monkeypatch, "create_timeline", **arguments)
    original = slot_request(root)
    snapshot(root / f"{original.identity}.before.drp")
    snapshot(root / f"{original.identity}.ok.drp")
    unlink = Path.unlink
    denied = True

    def deny_one_bit(path: Path, *args: Any, **kwargs: Any) -> None:
        if (
            path.parent.name.startswith(".inbox-retired-")
            and path.name.endswith(".bit")
            and denied
        ):
            raise PermissionError("temporary Windows file lock")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_one_bit)
    with pytest.raises(PermissionError, match="temporary"):
        client.request("create_timeline", 1, **arguments)
    retired = root / f".inbox-retired-{original.identity}"
    assert not (root / "inbox").exists()
    assert (retired / ".owner.json").is_file()
    assert inspect_pending(root)["pending_receipts"] == []
    denied = False
    assert client.request("create_timeline", 1, **arguments)["replayed"] is True
    assert not retired.exists()
    newer = MarkerSlot(root, request(root), 4096)
    newer.publish(time.monotonic() + 30)
    assert client.request("create_timeline", 1, **arguments)["replayed"] is True
    assert slot_request(root).identity == newer.identity
    newer.clear()


def test_lease_cleanup_failure_still_releases_publication_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from providers.resolve import marker_transport

    root = tmp_path / "session"
    prepare_marker(root)
    original_lease = (root / "lease" / ".owner.json").read_text()
    rename = Path.rename
    remove = marker_transport._remove_owned

    def deny_publication(path: Path, target: Path) -> Path:
        if (
            path.name.startswith(".lease-")
            and path.name.endswith(".tmp")
            and target.name == "lease"
        ):
            raise OSError("publication failed")
        return rename(path, target)

    def deny_staging_cleanup(path: Path, *args: Any) -> None:
        if path.name.startswith(".lease-") and path.name.endswith(".tmp"):
            raise PermissionError("preserve locked staging evidence")
        remove(path, *args)

    monkeypatch.setattr(Path, "rename", deny_publication)
    monkeypatch.setattr(marker_transport, "_remove_owned", deny_staging_cleanup)
    with pytest.raises(PermissionError, match="staging evidence"):
        publish_lease(root)
    assert not (root / "lease-publish.lock").exists()
    assert (root / "lease" / ".owner.json").read_text() == original_lease
    assert (root / "lease" / "ready.marker").is_file()


def test_cleanup_rejects_symlink_marker_without_touching_target(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    slot = MarkerSlot(root, request(root), 4096)
    slot.publish(time.monotonic() + 30)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep target")
    bit = root / "inbox" / "b00000_0.bit"
    bit.unlink()
    try:
        bit.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Creating symlinks is unavailable on this host: {error}")
    with pytest.raises(MarkerTransportError, match="unknown content"):
        slot.clear()
    assert outside.read_text() == "keep target"
    assert (root / "inbox" / "ready.marker").exists()


@pytest.mark.parametrize("acknowledged", [True, False])
def test_orphaned_read_after_cleanup_failure_recovers_without_republication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    acknowledged: bool,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    old = MarkerSlot(
        root,
        request(
            root,
            expires=int(time.time()) + 30 if acknowledged else int(time.time()) - 1,
        ),
        4096,
    )
    old.publish(time.monotonic() + 30)
    if acknowledged:
        snapshot(root / f"{old.identity}.drp")
    rename = Path.rename
    blocked = True

    def sharing_failure(path: Path, target: Path) -> Path:
        if path.name == "inbox" and blocked:
            raise PermissionError("temporary directory sharing violation")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", sharing_failure)
    with pytest.raises(PermissionError):
        old.clear()
    assert (root / "inbox").exists()
    blocked = False
    publish = MarkerSlot.publish
    published: list[str] = []

    def native_reply(slot: MarkerSlot, deadline: float) -> None:
        publish(slot, deadline)
        published.append(slot.identity)
        snapshot(root / f"{slot.identity}.drp")

    monkeypatch.setattr(MarkerSlot, "publish", native_reply)
    assert LuaSnapshotClient(root).request("ping", 30)["message"] == "pong"
    assert len(published) == 1 and published[0] != old.identity
    assert not (root / "inbox").exists()


def test_read_recovery_rejects_owner_claim_disagreeing_with_write_packet(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    slot = MarkerSlot(
        root,
        request(
            root,
            action="create_timeline",
            confirm=True,
            project_id="project-id",
            arguments={"name": "Never replay"},
            expires=int(time.time()) - 1,
        ),
        4096,
    )
    slot.publish(time.monotonic() + 30)
    owner_path = root / "inbox" / ".owner.json"
    owner = json.loads(owner_path.read_text())
    owner["action"] = "ping"
    owner_path.write_text(json.dumps(owner))
    snapshot(root / f"{slot.identity}.drp")
    with pytest.raises(MarkerTransportError, match="differs from its owner"):
        LuaSnapshotClient(root).request("ping", 5)
    assert (root / "inbox" / "ready.marker").exists()


def test_unacknowledged_unexpired_read_is_not_reclaimed(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    slot = MarkerSlot(root, request(root), 4096)
    slot.publish(time.monotonic() + 30)
    with pytest.raises(MarkerSlotBusyError):
        LuaSnapshotClient(root).request("ping", 5)
    assert slot_request(root).identity == slot.identity


def test_verified_native_success_survives_transient_retired_directory_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    publish = MarkerSlot.publish
    rmdir = Path.rmdir
    published: list[str] = []
    failed = False

    def native_write(slot: MarkerSlot, deadline: float) -> None:
        publish(slot, deadline)
        published.append(slot.identity)
        snapshot(root / f"{slot.identity}.before.drp")
        snapshot(root / f"{slot.identity}.ok.drp")

    def sharing_failure(path: Path) -> None:
        nonlocal failed
        if path.name.startswith(".inbox-retired-") and not failed:
            failed = True
            raise PermissionError("temporary Windows directory handle")
        rmdir(path)

    monkeypatch.setattr(MarkerSlot, "publish", native_write)
    monkeypatch.setattr(Path, "rmdir", sharing_failure)
    result = LuaSnapshotClient(root).request(
        "create_timeline",
        30,
        arguments={"name": "Verified once"},
        expected_project_id="project-id",
        confirm=True,
        idempotency_key="once",
    )
    assert result["status"] == "completed"
    assert failed and len(published) == 1
    assert inspect_pending(root)["pending_receipts"] == []
    assert not (root / "inbox").exists()
    assert not list(root.glob(".inbox-retired-*"))
