"""Project-fenced, receipt-backed access to the opt-in Windows input adapter."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from providers.resolve.lua_editing import WriteReceipt, atomic_json, bounded_text
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.windows_recording import (
    WindowsRecordingAdapter,
    WindowsRecordingError,
)


class RecordingUIService:
    """Keep GUI actions outside the pure marker channel with exact native fences."""

    def __init__(self, root: Path, client: LuaSnapshotClient, *, enabled: bool) -> None:
        """Bind one immutable runtime to an explicitly enabled Windows adapter."""
        self.root = root
        self.client = client
        self.adapter = WindowsRecordingAdapter(enabled=enabled)

    def _project(self, expected: str) -> dict[str, str]:
        """Require a fresh native identity before trusting the title-scoped UI."""
        bounded_text(expected, "Expected project ID")
        project: dict[str, str] = self.client.request("get_current_project")["project"]
        if project["id"] != expected:
            raise ValueError("Active native project identity changed.")
        return project

    def status(self, expected_project_id: str) -> dict[str, Any]:
        """Inspect live transport state; never equate it with recording readiness."""
        available = self.adapter.capabilities()
        if not available.get("available"):
            return {"adapter": available, "recording_readiness": "not_assessed"}
        project = self._project(expected_project_id)
        ui = self.adapter.inspect_transport(project["name"])
        self._project(expected_project_id)
        return {
            "project": project, "adapter": available, "ui": ui,
            "recording_readiness": "not_assessed",
        }

    def select_input(
        self, device_name: str, expected_project_id: str,
        idempotency_key: str, *, confirm: bool = False,
    ) -> dict[str, Any]:
        """Set the physical input once, holding the native mailbox during UI work."""
        if confirm is not True:
            raise ValueError("Selecting the physical input requires confirm=true.")
        bounded_text(device_name, "Input device name")
        bounded_text(expected_project_id, "Expected project ID")
        bounded_text(idempotency_key, "Idempotency key")
        if not self.adapter.capabilities().get("available"):
            raise ValueError("Enable the Windows recording UI adapter explicitly.")
        receipt = WriteReceipt(
            self.root, idempotency_key, "windows_select_audio_input",
            expected_project_id, {"device_name": device_name},
        )
        reconcile = False
        if receipt.path.exists():
            saved = json.loads(receipt.path.read_text(encoding="utf-8"))
            if saved.get("fingerprint") != receipt.fingerprint:
                raise ValueError(
                    "Idempotency key was already used for different arguments."
                )
            reconcile = saved.get("status") == "pending"
            if reconcile:
                receipt.value = saved
            else:
                replay = receipt.replay()
                if replay is not None:
                    return replay
        project = self._project(expected_project_id)
        lock = self.root / "client.lock"
        owner = f"{os.getpid()}:windows-ui:{uuid4().hex}"
        with lock.open("x", encoding="ascii") as stream:
            stream.write(owner)
        try:
            # WriteReceipt.begin refuses earlier uncertain native or GUI writes.
            if not reconcile:
                receipt.begin(uuid4().hex)
                receipt.value.update(
                    action="windows_select_audio_input",
                    expected_project_id=expected_project_id,
                    device_name=device_name,
                )
                atomic_json(receipt.path, receipt.value)
            try:
                if reconcile:
                    ui = self.adapter.inspect_input_device(project["name"])
                    if ui["input_device"]["name"] != device_name:
                        raise WindowsRecordingError(
                            "INPUT_RECONCILE_MISMATCH", uncertain=True,
                        )
                else:
                    ui = self.adapter.set_input_device(project["name"], device_name)
                receipt.value[
                    "ui_reconcile_evidence" if reconcile else "ui_evidence"
                ] = ui
                atomic_json(receipt.path, receipt.value)
            except WindowsRecordingError as error:
                if not reconcile and not error.uncertain:
                    receipt.reject(error.code)
                raise
        finally:
            with suppress(FileNotFoundError):
                if lock.read_text(encoding="ascii") == owner:
                    lock.unlink()
        self._project(expected_project_id)
        result = {
            "status": "completed", "action": "windows_select_audio_input",
            "project": project, "device_name": device_name, "ui": ui,
            "recording_readiness": "not_assessed", "replayed": False,
            "state_reconciled": reconcile,
            "routing_may_require_repatch": (
                reconcile or ui.get("routing_may_require_repatch", True)
            ),
        }
        receipt.complete(result)
        return result
