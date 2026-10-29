"""Creation acknowledges a new identity while preserving the original receipt."""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pytest

from agent.client import BridgeProtocolError, CommandTimeoutError
from providers.resolve.lua_editing import validate_edit
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.marker_transport import MarkerSlot, prepare_marker

OLD = "previous-project"
NEW = "created-project"
NAME = "Новий проєкт"


def snapshot(path: Path, identity: str, name: str = NAME) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "project.xml",
            f'<SM_Project DbId="{identity}">'
            f"<ProjectName>{escape(name)}</ProjectName></SM_Project>",
        )


def arguments() -> dict[str, Any]:
    return {
        "arguments": {"name": NAME},
        "expected_project_id": OLD,
        "idempotency_key": "create-one-project",
        "confirm": True,
    }


def receipt(root: Path) -> dict[str, Any]:
    paths = list((root / "receipts").glob("*.json"))
    assert len(paths) == 1
    value: dict[str, Any] = json.loads(paths[0].read_text("utf-8"))
    return value


def native_reply(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    identity: str = NEW,
    name: str = NAME,
    backup_identity: str = OLD,
    suffix: str = "ok_project_" + NEW,
) -> list[str]:
    publish = MarkerSlot.publish
    commands: list[str] = []

    def reply(slot: MarkerSlot, deadline: float) -> None:
        publish(slot, deadline)
        command = slot.request["id"]
        commands.append(command)
        snapshot(root / f"{command}.before.drp", backup_identity, "Previous")
        snapshot(root / f"{command}.{suffix}.drp", identity, name)

    monkeypatch.setattr(MarkerSlot, "publish", reply)
    return commands


def test_creation_replay_retains_original_identity_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    commands = native_reply(root, monkeypatch)
    result = LuaSnapshotClient(root).request("create_project", 30, **arguments())
    assert result["project"]["id"] == NEW
    assert result["project"]["name"] == NAME
    assert result["previous_project_id"] == OLD
    assert result["status"] == "completed"
    assert result["readback_verified_by"] == "ResolveLuaEditing"
    assert not (root / "inbox").exists()
    replay = LuaSnapshotClient(root).request("create_project", 30, **arguments())
    assert replay == {**result, "replayed": True}
    assert len(commands) == 1
    for changed in (
        {"expected_project_id": NEW},
        {"arguments": {"name": "Another"}},
    ):
        with pytest.raises(ValueError, match="different arguments"):
            LuaSnapshotClient(root).request(
                "create_project", 30, **(arguments() | changed)
            )
    assert len(commands) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"identity": "foreign-project"},
        {"identity": OLD, "suffix": "ok_project_" + OLD},
        {"name": "Another"},
        {"backup_identity": "foreign-project"},
        {"suffix": "ok_project_invalid.id"},
    ],
)
def test_wrong_creation_identity_remains_uncertain_and_blocks_new_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changes: dict[str, str]
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    commands = native_reply(root, monkeypatch, **changes)
    with pytest.raises(BridgeProtocolError):
        LuaSnapshotClient(root).request("create_project", 30, **arguments())
    assert receipt(root)["status"] == "pending"
    assert (root / "inbox").exists()
    with pytest.raises(RuntimeError):
        LuaSnapshotClient(root).request(
            "create_project", 30,
            **(arguments() | {"idempotency_key": "do-not-create-again"}),
        )
    assert len(commands) == 1


@pytest.mark.parametrize(
    "code,expected",
    [
        ("NAME_EXISTS", "rejected"),
        ("CREATE_PROJECT_UNAVAILABLE", "rejected"),
        ("PROJECT_LIST_UNAVAILABLE", "rejected"),
        ("CREATE_PROJECT_FAILED", "pending"),
        ("VERIFY_FAILED", "pending"),
        ("POST_SAVE_FAILED", "pending"),
    ],
)
def test_creation_preflight_rejection_is_distinct_from_partial_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, expected: str
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    native_reply(root, monkeypatch, suffix="error_" + code)
    with pytest.raises(BridgeProtocolError, match=code):
        LuaSnapshotClient(root).request("create_project", 30, **arguments())
    assert receipt(root)["status"] == expected
    assert (root / "inbox").exists() is (expected == "pending")


def test_old_bundle_rejects_creation_before_receipt_or_publication(
    tmp_path: Path,
) -> None:
    root = tmp_path / "old-session"
    prepare_marker(root)
    metadata_path = root / "session.json"
    metadata = json.loads(metadata_path.read_text("utf-8"))
    metadata["capabilities"].remove("create_project")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="freshly prepared"):
        LuaSnapshotClient(root).request("create_project", 30, **arguments())
    assert not (root / "receipts").exists()
    assert not (root / "inbox").exists()


@pytest.mark.parametrize(
    "name", ["", " ", " padded", "padded ", "a\nb", "a\x00b", "a\x7fb", "a" * 129]
)
def test_invalid_project_name_is_rejected_before_dispatch(name: str) -> None:
    with pytest.raises(ValueError):
        validate_edit("create_project", {"name": name}, [])


def test_untyped_response_cannot_acknowledge_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "session"
    prepare_marker(root)
    reply = native_reply(root, monkeypatch, suffix="ok")
    publish = MarkerSlot.publish
    monotonic = time.monotonic
    advance = 0.0

    def clock() -> float:
        return monotonic() + advance

    def expire_after_untyped_reply(slot: MarkerSlot, deadline: float) -> None:
        nonlocal advance
        publish(slot, deadline)
        advance = 120

    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(MarkerSlot, "publish", expire_after_untyped_reply)
    with pytest.raises(CommandTimeoutError):
        LuaSnapshotClient(root).request("create_project", 30, **arguments())
    assert len(reply) == 1
    assert receipt(root)["status"] == "pending"
    assert (root / "inbox").exists()
