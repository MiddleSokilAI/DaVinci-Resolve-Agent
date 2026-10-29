"""Global input changes require fresh identity, stopped UI and durable receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.recording_ui_service import RecordingUIService
from providers.resolve.windows_recording import WindowsRecordingError


class Client:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0
        self.project_id = "project-id"

    def request(self, action: str) -> dict[str, Any]:
        assert action == "get_current_project"
        if (self.root / "client.lock").exists():
            raise FileExistsError("Mailbox is owned")
        self.calls += 1
        return {"project": {"id": self.project_id, "name": "Voice project"}}


class Adapter:
    def __init__(self, root: Path, client: Client) -> None:
        self.root = root
        self.client = client
        self.calls = 0
        self.available = True
        self.error: WindowsRecordingError | None = None
        self.switch_project = False
        self.inspections = 0
        self.observed_input = "USB microphone"

    def capabilities(self) -> dict[str, Any]:
        return {"available": self.available}

    def inspect_transport(self, name: str) -> dict[str, Any]:
        assert name == "Voice project"
        return {"recording": False, "playing": False, "stopped": True}

    def set_input_device(self, name: str, device: str) -> dict[str, Any]:
        assert name == "Voice project" and device == "USB microphone"
        assert (self.root / "client.lock").exists()
        self.calls += 1
        if self.error:
            raise self.error
        if self.switch_project:
            self.client.project_id = "other-project"
        return {"verification": "preferences_reopened", "changed": False}

    def inspect_input_device(self, name: str) -> dict[str, Any]:
        assert name == "Voice project"
        assert (self.root / "client.lock").exists()
        self.inspections += 1
        return {"input_device": {"name": self.observed_input}}


@pytest.fixture
def service(tmp_path: Path) -> RecordingUIService:
    result = object.__new__(RecordingUIService)
    result.root = tmp_path
    client = Client(tmp_path)
    result.client = client  # type: ignore[assignment]
    result.adapter = Adapter(tmp_path, client)  # type: ignore[assignment]
    return result


def call(service: RecordingUIService, **kwargs: Any) -> dict[str, Any]:
    return service.select_input("USB microphone", "project-id", "input-key", **kwargs)


def test_explicit_confirmation_and_opt_in_before_any_native_or_gui_work(
    service: RecordingUIService,
) -> None:
    with pytest.raises(ValueError, match="confirm"):
        call(service)
    service.adapter.available = False  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Enable"):
        call(service, confirm=True)
    assert service.client.calls == 0  # type: ignore[attr-defined]


def test_input_change_holds_mailbox_and_replays_without_another_click(
    service: RecordingUIService,
) -> None:
    result = call(service, confirm=True)
    assert result["status"] == "completed"
    assert result["recording_readiness"] == "not_assessed"
    assert call(service, confirm=True)["replayed"] is True
    assert service.adapter.calls == 1  # type: ignore[attr-defined]
    assert not (service.root / "client.lock").exists()
    with pytest.raises(ValueError, match="different arguments"):
        service.select_input(
            "Other microphone", "project-id", "input-key", confirm=True,
        )


@pytest.mark.parametrize("uncertain", [False, True])
def test_failure_retains_uncertainty_and_never_reexecutes(
    service: RecordingUIService, uncertain: bool,
) -> None:
    service.adapter.error = WindowsRecordingError(  # type: ignore[attr-defined]
        "READBACK_FAILED", uncertain=uncertain,
    )
    with pytest.raises(WindowsRecordingError):
        call(service, confirm=True)
    path = next((service.root / "receipts").glob("*.json"))
    receipt = json.loads(path.read_text(encoding="utf-8"))
    assert receipt["status"] == ("pending" if uncertain else "rejected")
    if uncertain:
        recovered = call(service, confirm=True)
        assert recovered["state_reconciled"] is True
        assert service.adapter.inspections == 1  # type: ignore[attr-defined]
    else:
        with pytest.raises(RuntimeError, match="No write was resubmitted"):
            call(service, confirm=True)
    assert service.adapter.calls == 1  # type: ignore[attr-defined]
    assert not (service.root / "client.lock").exists()


def test_foreign_project_before_write_refused_without_click(
    service: RecordingUIService,
) -> None:
    service.client.project_id = "foreign"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="identity changed"):
        call(service, confirm=True)
    assert service.adapter.calls == 0  # type: ignore[attr-defined]


def test_project_changed_after_ui_write_remains_uncertain(
    service: RecordingUIService,
) -> None:
    service.adapter.switch_project = True  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="identity changed"):
        call(service, confirm=True)
    path = next((service.root / "receipts").glob("*.json"))
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "pending"
    assert "ui_evidence" in json.loads(path.read_text(encoding="utf-8"))


def test_uncertain_input_mismatch_is_not_reapplied(
    service: RecordingUIService,
) -> None:
    service.adapter.error = WindowsRecordingError(  # type: ignore[attr-defined]
        "TIMEOUT", uncertain=True,
    )
    with pytest.raises(WindowsRecordingError):
        call(service, confirm=True)
    service.adapter.observed_input = "Other microphone"  # type: ignore[attr-defined]
    with pytest.raises(WindowsRecordingError, match="INPUT_RECONCILE_MISMATCH"):
        call(service, confirm=True)
    assert service.adapter.calls == 1  # type: ignore[attr-defined]
    path = next((service.root / "receipts").glob("*.json"))
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "pending"


def test_existing_mailbox_or_uncertain_write_blocks_gui_work(
    service: RecordingUIService,
) -> None:
    (service.root / "client.lock").write_text("foreign-owner", encoding="ascii")
    with pytest.raises(FileExistsError):
        call(service, confirm=True)
    assert (service.root / "client.lock").read_text() == "foreign-owner"
    (service.root / "client.lock").unlink()
    receipts = service.root / "receipts"
    receipts.mkdir(exist_ok=True)
    (receipts / "pending.json").write_text('{"status":"pending"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="earlier write"):
        call(service, confirm=True)
    assert service.adapter.calls == 0  # type: ignore[attr-defined]


def test_status_is_native_fenced_and_does_not_claim_readiness(
    service: RecordingUIService,
) -> None:
    result = service.status("project-id")
    assert result["ui"]["recording"] is False
    assert result["recording_readiness"] == "not_assessed"
    assert service.client.calls == 2  # type: ignore[attr-defined]
