"""Native-fenced, backed-up and receipt-backed Fairlight UI operations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from providers.resolve.lua_editing import WriteReceipt, atomic_json, bounded_text
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.windows_fairlight import (
    FairlightTarget,
    ProcessingModule,
    WindowsFairlightAdapter,
)
from providers.resolve.windows_recording import (
    WindowsRecordingAdapter,
    WindowsRecordingError,
)
from providers.resolve.windows_ui_common import hold_ui_mailbox, native_project

CONTROLS = frozenset({"eq", "dynamics", "record_arm", "input_patch"})


class FairlightUIService:
    """Keep scoped Windows controls separate from the native marker protocol."""

    def __init__(self, root: Path, client: LuaSnapshotClient, *, enabled: bool) -> None:
        self.root = root
        self.client = client
        self.adapter = WindowsFairlightAdapter(enabled=enabled)
        self.transport = WindowsRecordingAdapter(enabled=enabled)

    def _validate(
        self,
        control: str,
        timeline: str,
        name: str,
        project_id: str,
        scope: str,
        index: int | None,
        desired: dict[str, Any] | None = None,
    ) -> None:
        if control not in CONTROLS:
            raise ValueError("Unknown Fairlight control.")
        for value, label in (
            (timeline, "Timeline name"),
            (name, "Track or bus name"),
            (project_id, "Expected project ID"),
        ):
            bounded_text(value, label)
        if scope not in {"track", "bus"}:
            raise ValueError("Scope must be track or bus.")
        if scope == "bus":
            if index is not None or control in {"record_arm", "input_patch"}:
                raise ValueError("Bus targets support only EQ and Dynamics.")
        elif type(index) is not int or not 1 <= index <= 64:
            raise ValueError("Track targets require expected_track_index from 1 to 64.")
        if desired is not None:
            field = "enabled" if control in {"eq", "dynamics"} else "armed"
            if control == "input_patch":
                if set(desired) != {"source_name", "source_channel"}:
                    raise ValueError("An exact source name and channel are required.")
                bounded_text(desired["source_name"], "Audio source name")
                channel = desired["source_channel"]
                if type(channel) is not int or not 1 <= channel <= 64:
                    raise ValueError("Source channel must be from 1 to 64.")
            elif set(desired) != {field} or type(desired[field]) is not bool:
                raise ValueError(f"{field} must be an explicit boolean.")
        if not self.adapter.capabilities().get("available"):
            raise ValueError("Enable the Windows recording UI adapter explicitly.")

    def _target(
        self,
        timeline: str,
        name: str,
        project_id: str,
        scope: str,
        index: int | None,
        *,
        mono: bool,
    ) -> tuple[dict[str, str], FairlightTarget]:
        project = native_project(self.client, project_id)
        arguments: dict[str, Any] = {"timeline_name": timeline}
        if scope == "track":
            arguments["recording"] = {"operation": "inspect", "track_name": name}
        snapshot = self.client.request(
            "get_timeline_summary",
            arguments=arguments,
            expected_project_id=project_id,
        )
        if snapshot["project"] != project:
            raise ValueError("Active native project identity changed.")
        count = None
        if scope == "bus" and snapshot.get("timeline") != timeline:
            raise ValueError("Native timeline no longer matches the bus target.")
        if "timeline" in snapshot and snapshot["timeline"] != timeline:
            raise ValueError("Native timeline no longer matches the track target.")
        if scope == "track":
            recording = snapshot["recording"]
            track = recording["track"]
            count = recording["audio_track_count"]
            if (
                recording["timeline"] != timeline
                or track.get("found") is not True
                or track.get("name") != name
                or type(track.get("index")) is not int
                or track["index"] != index
                or track.get("verification") != "native_readback"
            ):
                raise ValueError(
                    "Native track name/index no longer matches the target."
                )
            if mono and track.get("subtype") != "mono":
                raise ValueError(
                    "Input patching currently requires a native mono track."
                )
        return project, FairlightTarget(timeline, scope, name, index, count)  # type: ignore[arg-type]

    def _inspect(
        self,
        control: str,
        project: dict[str, str],
        target: FairlightTarget,
    ) -> dict[str, Any]:
        if control in {"eq", "dynamics"}:
            return self.adapter.inspect_processing(
                project["name"],
                target,
                cast(ProcessingModule, control),
            )
        if control == "record_arm":
            return self.adapter.inspect_arm(project["name"], target)
        return self.adapter.inspect_patch(project["name"], target)

    def _set(
        self,
        control: str,
        project: dict[str, str],
        target: FairlightTarget,
        desired: dict[str, Any],
    ) -> dict[str, Any]:
        if control in {"eq", "dynamics"}:
            return self.adapter.set_processing(
                project["name"],
                target,
                cast(ProcessingModule, control),
                desired["enabled"],
            )
        if control == "record_arm":
            return self.adapter.set_arm(project["name"], target, desired["armed"])
        return self.adapter.set_patch(
            project["name"],
            target,
            desired["source_name"],
            desired["source_channel"],
        )

    @staticmethod
    def _matches(control: str, ui: dict[str, Any], desired: dict[str, Any]) -> bool:
        state = ui[
            "processing"
            if control in {"eq", "dynamics"}
            else "record_arm"
            if control == "record_arm"
            else "input_patch"
        ]
        return all(state.get(key) == value for key, value in desired.items()) and (
            control != "input_patch" or state.get("patched") is True
        )

    def inspect(
        self,
        control: str,
        timeline_name: str,
        target_name: str,
        expected_project_id: str,
        *,
        scope: str = "track",
        expected_track_index: int | None = None,
    ) -> dict[str, Any]:
        """Read a scoped control; UI navigation requires the same host opt-in."""
        self._validate(
            control,
            timeline_name,
            target_name,
            expected_project_id,
            scope,
            expected_track_index,
        )
        project, target = self._target(
            timeline_name,
            target_name,
            expected_project_id,
            scope,
            expected_track_index,
            mono=control == "input_patch",
        )
        with hold_ui_mailbox(self.root):
            ui = self._inspect(control, project, target)
        after = self._target(
            timeline_name,
            target_name,
            expected_project_id,
            scope,
            expected_track_index,
            mono=control == "input_patch",
        )
        if after != (project, target):
            raise ValueError("Native Fairlight target changed during UI inspection.")
        return {"project": project, "ui": ui, "recording_readiness": "not_assessed"}

    def change(
        self,
        control: str,
        desired: dict[str, Any],
        timeline_name: str,
        target_name: str,
        expected_project_id: str,
        idempotency_key: str,
        *,
        scope: str = "track",
        expected_track_index: int | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Back up once and dispatch once; recover only by reading original state."""
        if confirm is not True:
            raise ValueError("Fairlight changes require confirm=true.")
        self._validate(
            control,
            timeline_name,
            target_name,
            expected_project_id,
            scope,
            expected_track_index,
            desired,
        )
        bounded_text(idempotency_key, "Idempotency key")
        arguments = {
            "timeline_name": timeline_name,
            "scope": scope,
            "name": target_name,
            "expected_track_index": expected_track_index,
            **desired,
        }
        action = f"windows_fairlight_set_{control}"
        receipt = WriteReceipt(
            self.root,
            idempotency_key,
            action,
            expected_project_id,
            arguments,
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
        project, target = self._target(
            timeline_name,
            target_name,
            expected_project_id,
            scope,
            expected_track_index,
            mono=control == "input_patch",
        )
        if reconcile:
            if receipt.value.get("target") != target.as_dict():
                raise ValueError(
                    "Native Fairlight target changed since the pending write."
                )
        else:
            # Do not select a timeline or save a backup during active capture.
            with hold_ui_mailbox(self.root):
                state = self.transport.inspect_transport(project["name"])["transport"]
                if (
                    state["recording"] is not False
                    or state["playing"] is not False
                    or state["stopped"] is not True
                ):
                    raise WindowsRecordingError("TRANSPORT_NOT_STOPPED")
            # Reuse the existing native guarded page operation: save and export
            # the project BEFORE host-side mutation, then select this timeline.
            # It must finish before beginning our pending GUI receipt.
            backup = self.client.request(
                "set_clip_properties",
                arguments={
                    "timeline_name": timeline_name,
                    "open_page": "fairlight",
                },
                expected_project_id=expected_project_id,
                confirm=True,
                idempotency_key="fairlight-backup-"
                + hashlib.sha256(
                    idempotency_key.encode("utf-8"),
                ).hexdigest(),
            )
        with hold_ui_mailbox(self.root):
            if not reconcile:
                receipt.begin(uuid4().hex)
                receipt.value.update(
                    action=action,
                    expected_project_id=expected_project_id,
                    target=target.as_dict(),
                    desired=desired,
                    backup=backup,
                )
                atomic_json(receipt.path, receipt.value)
            try:
                ui = (
                    self._inspect(control, project, target)
                    if reconcile
                    else self._set(control, project, target, desired)
                )
                if not self._matches(control, ui, desired):
                    raise WindowsRecordingError(
                        "STATE_RECONCILE_MISMATCH", uncertain=True
                    )
                receipt.value[
                    "ui_reconcile_evidence" if reconcile else "ui_evidence"
                ] = ui
                atomic_json(receipt.path, receipt.value)
            except WindowsRecordingError as error:
                if not reconcile and not error.uncertain:
                    receipt.reject(error.code)
                raise
        after = self._target(
            timeline_name,
            target_name,
            expected_project_id,
            scope,
            expected_track_index,
            mono=control == "input_patch",
        )
        if after != (project, target):
            raise ValueError("Native Fairlight target changed during the UI write.")
        result = {
            "status": "completed",
            "action": action,
            "project": project,
            "target": target.as_dict(),
            "desired": desired,
            "ui": ui,
            "backup": receipt.value["backup"],
            "replayed": False,
            "state_reconciled": reconcile,
            "recording_readiness": "not_assessed",
            "save_status": (
                "not_reverified"
                if reconcile
                else "ui_save_invoked"
                if ui["changed"]
                else "not_needed"
            ),
            "persistence_verified": False,
        }
        receipt.complete(result)
        return result
