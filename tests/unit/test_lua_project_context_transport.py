"""Context response identity, capability and special ACK validation offline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from agent.client import BridgeProtocolError
from providers.resolve.lua_transport import LuaCommandTimeoutError, LuaSnapshotClient
from providers.resolve.marker_transport import MarkerSlot, prepare_marker

ARGUMENTS: dict[str, Any] = {
    "expected_database": {"DbType": "Disk", "DbName": "Library"},
    "expected_folder": "",
    "expected_project_name": "Existing project — Україна",
}


def snapshot(path: Path, identity: str, name: str) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "project.xml",
            f'<SM_Project DbId="{identity}">'
            f"<ProjectName>{escape(name)}</ProjectName></SM_Project>",
        )


def reply(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str = "ok_context_verified",
    identity: str = "project-id",
    name: str = ARGUMENTS["expected_project_name"],
) -> list[dict[str, Any]]:
    original = MarkerSlot.publish
    requests: list[dict[str, Any]] = []

    def publish(slot: MarkerSlot, deadline: float) -> None:
        original(slot, deadline)
        requests.append(slot.request)
        snapshot(root / f"{slot.identity}.{suffix}.drp", identity, name)

    monkeypatch.setattr(MarkerSlot, "publish", publish)
    return requests


def test_native_context_response_has_verified_scope_without_write_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    requests = reply(root, monkeypatch)
    result = LuaSnapshotClient(root).request(
        "verify_project_context",
        30,
        expected_project_id="project-id",
        arguments=ARGUMENTS,
    )
    assert result == {
        "project": {"id": "project-id", "name": ARGUMENTS["expected_project_name"]},
        "database": ARGUMENTS["expected_database"],
        "folder": "",
        "context_verified": True,
        "folder_membership_verified": True,
        "readback_verified_by": "ResolveLuaProjectContext",
        "source": "live_lua_api",
        "response_path": str(root / (requests[0]["id"] + ".ok_context_verified.drp")),
    }
    assert requests[0]["action"] == "verify_project_context"
    assert requests[0]["project_id"] == "project-id"
    assert requests[0]["arguments"] == ARGUMENTS
    assert "confirm" not in requests[0]
    assert not list((root / "receipts").glob("*.json"))
    assert not list(root.glob("*.before.drp"))
    assert not (root / "inbox").exists()
    assert not (root / "client.lock").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"suffix": "ok_wrong_proof"},
        {"identity": "other-project"},
        {"name": "Other"},
    ],
)
def test_wrong_native_proof_is_rejected_and_never_becomes_context_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, str],
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    requests = reply(root, monkeypatch, **changes)
    with pytest.raises(BridgeProtocolError, match="unverified"):
        LuaSnapshotClient(root).request(
            "verify_project_context",
            30,
            expected_project_id="project-id",
            arguments=ARGUMENTS,
        )
    assert len(requests) == 1
    assert not list((root / "receipts").glob("*.json"))
    assert not (root / "inbox").exists()


def test_native_context_refusal_is_a_correlated_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    reply(root, monkeypatch, suffix="error_PROJECT_CONTEXT_DATABASE_MISMATCH")
    with pytest.raises(BridgeProtocolError, match="PROJECT_CONTEXT_DATABASE_MISMATCH"):
        LuaSnapshotClient(root).request(
            "verify_project_context",
            30,
            expected_project_id="project-id",
            arguments=ARGUMENTS,
        )
    assert not (root / "inbox").exists()


def test_ordinary_project_export_cannot_substitute_for_context_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)

    def ordinary(slot: MarkerSlot, deadline: float) -> None:
        # Positive cases exercise actual packet publication. Isolate response
        # selection here so a tiny response deadline cannot become a disk test.
        slot.published = True
        snapshot(
            root / f"{slot.identity}.drp",
            "project-id",
            ARGUMENTS["expected_project_name"],
        )

    monkeypatch.setattr(MarkerSlot, "publish", ordinary)
    with pytest.raises(LuaCommandTimeoutError):
        LuaSnapshotClient(root).request(
            "verify_project_context",
            0.05,
            expected_project_id="project-id",
            arguments=ARGUMENTS,
        )


def test_legacy_capability_catalogue_refuses_context_before_any_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    metadata_path = root / "session.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["capabilities"].remove("project_context_v1")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unsupported old session must not publish")

    monkeypatch.setattr(MarkerSlot, "publish", forbidden)
    with pytest.raises(ValueError, match="project_context_v1"):
        LuaSnapshotClient(root).request(
            "verify_project_context",
            30,
            expected_project_id="project-id",
            arguments=ARGUMENTS,
        )
    assert not (root / "client.lock").exists()
    assert not (root / "inbox").exists()


@pytest.mark.parametrize("extras", [{"confirm": True}, {"idempotency_key": "key"}])
def test_context_read_never_accepts_mutation_confirmation_or_receipt_key(
    tmp_path: Path,
    extras: dict[str, Any],
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    with pytest.raises(ValueError, match="read-only"):
        LuaSnapshotClient(root).request(
            "verify_project_context",
            30,
            expected_project_id="project-id",
            arguments=ARGUMENTS,
            **extras,
        )
    assert not (root / "client.lock").exists()
