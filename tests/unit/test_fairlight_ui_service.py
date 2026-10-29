"""Exercise native fences and durable Fairlight writes using offline doubles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from providers.resolve.fairlight_ui_service import FairlightUIService
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

PROJECT_ID = "native-project-id"
PROJECT_NAME = "Тестовий проєкт"
TIMELINE = "Основна"
TRACK_NAME = "Fifine RAW"
BUS_NAME = "Bus 1"
KEY = "fairlight-key"
CONTROLS = ("eq", "dynamics", "record_arm", "input_patch")


def desired(control: str, *, enabled: bool = True) -> dict[str, Any]:
    """Create one supported, typed desired state for a public service control."""
    if control in {"eq", "dynamics"}:
        return {"enabled": enabled}
    if control == "record_arm":
        return {"armed": enabled}
    return {"source_name": "Fifine", "source_channel": 1}


class NativeClient:
    """Model native requests while rejecting requests during UI lock ownership."""

    def __init__(self, root: Path, events: list[str]) -> None:
        self.root = root
        self.events = events
        self.calls: list[dict[str, Any]] = []
        self.project = {"id": PROJECT_ID, "name": PROJECT_NAME}
        self.snapshot_project: dict[str, str] | None = None
        self.timeline = TIMELINE
        self.recording_timeline = TIMELINE
        self.audio_track_count: Any = 3
        self.track: dict[str, Any] = {
            "found": True, "name": TRACK_NAME, "index": 2,
            "subtype": "mono", "verification": "native_readback",
        }
        self.backup_error: Exception | None = None
        self.backup_result = {
            "status": "completed", "backup_path": "synthetic.before.drp",
            "action": "set_clip_properties",
        }

    def request(
        self, action: str, *, arguments: dict[str, Any] | None = None,
        expected_project_id: str | None = None, confirm: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if (self.root / "client.lock").exists():
            raise FileExistsError("Native requests cannot overlap UI ownership.")
        self.events.append(f"native:{action}")
        self.calls.append({
            "action": action, "arguments": deepcopy(arguments),
            "expected_project_id": expected_project_id, "confirm": confirm,
            "idempotency_key": idempotency_key,
        })
        if action == "get_current_project":
            assert arguments is None and expected_project_id is None
            return {"project": deepcopy(self.project)}
        assert expected_project_id == PROJECT_ID
        if action == "get_timeline_summary":
            assert arguments is not None and arguments["timeline_name"] == TIMELINE
            if "recording" in arguments:
                assert arguments["recording"] == {
                    "operation": "inspect", "track_name": TRACK_NAME
                }
            return {
                "project": deepcopy(self.snapshot_project or self.project),
                "timeline": self.timeline,
                "recording": {
                    "timeline": self.recording_timeline,
                    "audio_track_count": self.audio_track_count,
                    "track": deepcopy(self.track),
                },
            }
        assert action == "set_clip_properties"
        assert arguments == {"timeline_name": TIMELINE, "open_page": "fairlight"}
        assert confirm is True and idempotency_key is not None
        if self.backup_error is not None:
            raise self.backup_error
        return deepcopy(self.backup_result)

    @property
    def backups(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["action"] == "set_clip_properties"]


class TransportAdapter:
    """Expose only stopped-status reads; device writes are a test failure."""

    def __init__(self, root: Path, events: list[str]) -> None:
        self.root = root
        self.events = events
        self.calls = 0
        self.error: WindowsRecordingError | None = None
        self.state: dict[str, Any] = {
            "recording": False, "playing": False, "stopped": True,
            "verification": "windows_uia_toggle_state",
        }

    def inspect_transport(self, project_name: str) -> dict[str, Any]:
        assert project_name == PROJECT_NAME
        assert (self.root / "client.lock").exists()
        self.events.append("transport:inspect")
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"transport": deepcopy(self.state)}

    def set_input_device(self, project_name: str, name: str) -> dict[str, Any]:
        raise AssertionError("Fairlight service must never change the physical device.")


class FairlightAdapter:
    """Keep UI state, enforce lock ownership, and simulate ambiguous writes."""

    def __init__(self, root: Path, events: list[str]) -> None:
        self.root = root
        self.events = events
        self.available = True
        self.changed = True
        self.calls: list[dict[str, Any]] = []
        self.setter_error: WindowsRecordingError | None = None
        self.getter_error: WindowsRecordingError | None = None
        self.after_ui: Callable[[], None] | None = None
        self.states = {control: desired(control) for control in CONTROLS}

    def capabilities(self) -> dict[str, Any]:
        return {"available": self.available}

    def _call(
        self, kind: str, project_name: str, target: FairlightTarget,
        control: str, state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert project_name == PROJECT_NAME
        assert (self.root / "client.lock").exists()
        self.events.append(f"ui:{kind}:{control}")
        self.calls.append({
            "kind": kind, "project_name": project_name,
            "target": target.as_dict(), "control": control,
            "desired": deepcopy(state),
        })
        if kind == "set":
            assert state is not None
            self.states[control] = deepcopy(state)
            if self.setter_error is not None:
                raise self.setter_error
        elif self.getter_error is not None:
            raise self.getter_error
        value = deepcopy(self.states[control])
        if control in {"eq", "dynamics"}:
            key = "processing"
            value.update(module=control, verification=(
                "windows_uia_reopened" if control == "eq"
                else "windows_visual_reopened"
            ))
        elif control == "record_arm":
            key = "record_arm"
            value["verification"] = "windows_visual_reacquired"
        else:
            key = "input_patch"
            value.update(patched=True, verification="windows_uia_patch_reopened")
        result: dict[str, Any] = {key: value, "recording_ready": None}
        if kind == "set":
            result["changed"] = self.changed
        if self.after_ui is not None:
            self.after_ui()
        return result

    def inspect_processing(
        self, project_name: str, target: FairlightTarget, module: ProcessingModule,
    ) -> dict[str, Any]:
        return self._call("inspect", project_name, target, module)

    def set_processing(
        self, project_name: str, target: FairlightTarget, module: ProcessingModule,
        enabled: bool,
    ) -> dict[str, Any]:
        return self._call("set", project_name, target, module, {"enabled": enabled})

    def inspect_arm(
        self, project_name: str, target: FairlightTarget,
    ) -> dict[str, Any]:
        return self._call("inspect", project_name, target, "record_arm")

    def set_arm(
        self, project_name: str, target: FairlightTarget, armed: bool,
    ) -> dict[str, Any]:
        return self._call("set", project_name, target, "record_arm", {"armed": armed})

    def inspect_patch(
        self, project_name: str, target: FairlightTarget,
    ) -> dict[str, Any]:
        return self._call("inspect", project_name, target, "input_patch")

    def set_patch(
        self, project_name: str, target: FairlightTarget,
        source_name: str, source_channel: int,
    ) -> dict[str, Any]:
        return self._call("set", project_name, target, "input_patch", {
            "source_name": source_name, "source_channel": source_channel,
        })


@dataclass
class Harness:
    service: FairlightUIService
    client: NativeClient
    adapter: FairlightAdapter
    transport: TransportAdapter
    events: list[str]

    def change(self, control: str = "eq", **kwargs: Any) -> dict[str, Any]:
        options = {
            "confirm": True, "scope": "track", "expected_track_index": 2,
            **kwargs,
        }
        state = options.pop("desired", desired(control))
        name = options.pop("target_name", TRACK_NAME)
        key = options.pop("idempotency_key", KEY)
        return self.service.change(
            control, state, TIMELINE, name, PROJECT_ID, key, **options,
        )

    def inspect(self, control: str = "eq", **kwargs: Any) -> dict[str, Any]:
        options = {"scope": "track", "expected_track_index": 2, **kwargs}
        name = options.pop("target_name", TRACK_NAME)
        return self.service.inspect(control, TIMELINE, name, PROJECT_ID, **options)

    def receipt(self, key: str = KEY) -> dict[str, Any]:
        path = self.service.root / "receipts" / (
            hashlib.sha256(key.encode()).hexdigest() + ".json"
        )
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """Build the service without constructing or invoking any real adapter."""
    events: list[str] = []
    client = NativeClient(tmp_path, events)
    adapter = FairlightAdapter(tmp_path, events)
    transport = TransportAdapter(tmp_path, events)
    service = object.__new__(FairlightUIService)
    service.root = tmp_path
    service.client = cast(LuaSnapshotClient, client)
    service.adapter = cast(WindowsFairlightAdapter, adapter)
    service.transport = cast(WindowsRecordingAdapter, transport)
    return Harness(service, client, adapter, transport, events)


@pytest.mark.parametrize("control", CONTROLS)
def test_inspection_native_fences_both_sides_and_owns_mailbox(
    harness: Harness, control: str,
) -> None:
    result = harness.inspect(control)
    assert result["recording_readiness"] == "not_assessed"
    assert result["project"] == {"id": PROJECT_ID, "name": PROJECT_NAME}
    assert harness.events == [
        "native:get_current_project", "native:get_timeline_summary",
        f"ui:inspect:{control}",
        "native:get_current_project", "native:get_timeline_summary",
    ]
    assert harness.adapter.calls[0]["target"] == {
        "timeline_name": TIMELINE, "scope": "track", "name": TRACK_NAME,
        "index": 2, "audio_track_count": 3,
    }
    assert harness.transport.calls == 0 and harness.client.backups == []
    assert not (harness.service.root / "client.lock").exists()
    assert not (harness.service.root / "receipts").exists()


@pytest.mark.parametrize("control", CONTROLS)
def test_change_reads_stopped_status_then_backs_up_before_one_ui_dispatch(
    harness: Harness, control: str,
) -> None:
    result = harness.change(control)
    assert result["status"] == "completed"
    assert result["desired"] == desired(control)
    assert result["state_reconciled"] is False and result["replayed"] is False
    assert result["recording_readiness"] == "not_assessed"
    assert result["save_status"] == "ui_save_invoked"
    assert result["persistence_verified"] is False
    assert result["backup"] == harness.client.backup_result
    assert harness.events == [
        "native:get_current_project", "native:get_timeline_summary",
        "transport:inspect", "native:set_clip_properties", f"ui:set:{control}",
        "native:get_current_project", "native:get_timeline_summary",
    ]
    assert harness.client.backups[0]["idempotency_key"] == (
        "fairlight-backup-" + hashlib.sha256(KEY.encode()).hexdigest()
    )
    receipt = harness.receipt()
    assert receipt["status"] == "completed"
    assert receipt["desired"] == desired(control)
    assert receipt["target"] == result["target"]
    assert receipt["backup"] == harness.client.backup_result
    assert receipt["ui_evidence"] == result["ui"]
    assert not (harness.service.root / "client.lock").exists()


@pytest.mark.parametrize("control", CONTROLS)
def test_completed_replay_performs_no_native_transport_backup_or_ui_work(
    harness: Harness, control: str,
) -> None:
    initial = harness.change(control)
    before = list(harness.events)
    result = harness.change(control)
    assert result == dict(initial, replayed=True)
    assert harness.events == before
    assert len(harness.adapter.calls) == 1


@pytest.mark.parametrize("control", CONTROLS)
def test_no_op_does_not_claim_a_new_save_or_proven_persistence(
    harness: Harness, control: str,
) -> None:
    harness.adapter.changed = False
    result = harness.change(control)
    assert result["ui"]["changed"] is False
    assert result["save_status"] == "not_needed"
    assert result["persistence_verified"] is False


@pytest.mark.parametrize("control", ["eq", "dynamics"])
@pytest.mark.parametrize("write", [False, True])
def test_bus_controls_preserve_exact_name_and_null_track_coordinates(
    harness: Harness, control: str, write: bool,
) -> None:
    options = {"scope": "bus", "expected_track_index": None, "target_name": BUS_NAME}
    result = (
        harness.change(control, **options)
        if write else harness.inspect(control, **options)
    )
    assert result["recording_readiness"] == "not_assessed"
    assert harness.adapter.calls[0]["target"] == {
        "timeline_name": TIMELINE, "scope": "bus", "name": BUS_NAME,
        "index": None, "audio_track_count": None,
    }
    summaries = [
        call for call in harness.client.calls
        if call["action"] == "get_timeline_summary"
    ]
    assert all(call["arguments"] == {"timeline_name": TIMELINE} for call in summaries)


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize(("control", "scope", "index"), [
    ("input_patch", "bus", None), ("record_arm", "bus", None),
    ("eq", "bus", 1), ("eq", "track", None), ("eq", "track", True),
    ("eq", "track", 0), ("eq", "track", 65), ("eq", "unknown", 2),
    ("other", "track", 2),
])
def test_invalid_scopes_controls_and_indices_fail_before_native_or_ui_work(
    harness: Harness, write: bool, control: str, scope: str, index: Any,
) -> None:
    with pytest.raises(ValueError):
        if write:
            harness.change(control, scope=scope, expected_track_index=index)
        else:
            harness.inspect(control, scope=scope, expected_track_index=index)
    assert harness.events == []


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize(("field", "value"), [
    ("found", False), ("found", 1), ("name", "Other track"),
    ("index", 1), ("index", True), ("verification", "visual_only"),
])
def test_native_track_name_index_and_evidence_fence_dispatch(
    harness: Harness, write: bool, field: str, value: Any,
) -> None:
    harness.client.track[field] = value
    with pytest.raises(ValueError, match="Native track"):
        harness.change() if write else harness.inspect()
    assert harness.adapter.calls == []
    assert harness.transport.calls == 0 and harness.client.backups == []


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("count", [None, True, 1, 65])
def test_native_audio_count_must_bound_the_selected_track_before_dispatch(
    harness: Harness, write: bool, count: Any,
) -> None:
    harness.client.audio_track_count = count
    with pytest.raises(ValueError):
        harness.change() if write else harness.inspect()
    assert harness.adapter.calls == []
    assert harness.transport.calls == 0 and harness.client.backups == []


@pytest.mark.parametrize("write", [False, True])
def test_input_patch_requires_native_mono_before_transport_backup_or_ui(
    harness: Harness, write: bool,
) -> None:
    harness.client.track["subtype"] = "stereo"
    with pytest.raises(ValueError, match="mono"):
        harness.change("input_patch") if write else harness.inspect("input_patch")
    assert harness.adapter.calls == []
    assert harness.transport.calls == 0 and harness.client.backups == []


@pytest.mark.parametrize("control", ["eq", "dynamics", "record_arm"])
def test_processing_and_arm_do_not_impose_a_mono_restriction(
    harness: Harness, control: str,
) -> None:
    harness.client.track["subtype"] = "stereo"
    assert harness.change(control)["status"] == "completed"


@pytest.mark.parametrize("scope", ["track", "bus"])
@pytest.mark.parametrize("write", [False, True])
def test_timeline_identity_is_checked_even_for_bus_targets(
    harness: Harness, scope: str, write: bool,
) -> None:
    harness.client.timeline = "Other timeline"
    options: dict[str, Any] = {
        "scope": scope, "expected_track_index": 2 if scope == "track" else None
    }
    with pytest.raises(ValueError, match="[Tt]imeline"):
        harness.change(**options) if write else harness.inspect(**options)
    assert harness.adapter.calls == []
    assert harness.transport.calls == 0 and harness.client.backups == []


@pytest.mark.parametrize("write", [False, True])
def test_recording_readback_timeline_must_also_match(
    harness: Harness, write: bool,
) -> None:
    harness.client.recording_timeline = "Other timeline"
    with pytest.raises(ValueError):
        harness.change() if write else harness.inspect()
    assert harness.adapter.calls == []
    assert harness.transport.calls == 0 and harness.client.backups == []


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("stage", ["current", "snapshot"])
def test_native_project_mismatch_blocks_all_ui_dispatch(
    harness: Harness, write: bool, stage: str,
) -> None:
    if stage == "current":
        harness.client.project["id"] = "foreign"
    else:
        harness.client.snapshot_project = {"id": "foreign", "name": PROJECT_NAME}
    with pytest.raises(ValueError, match="identity changed"):
        harness.change() if write else harness.inspect()
    assert harness.adapter.calls == []
    assert harness.transport.calls == 0 and harness.client.backups == []


@pytest.mark.parametrize("confirm", [False, None, 0, 1, "true"])
def test_changes_require_explicit_boolean_confirmation_before_any_work(
    harness: Harness, confirm: Any,
) -> None:
    with pytest.raises(ValueError, match="confirm"):
        harness.change(confirm=confirm)
    assert harness.events == []


@pytest.mark.parametrize("write", [False, True])
def test_opt_in_is_required_before_native_or_ui_work(
    harness: Harness, write: bool,
) -> None:
    harness.adapter.available = False
    with pytest.raises(ValueError, match="Enable"):
        harness.change() if write else harness.inspect()
    assert harness.events == []


@pytest.mark.parametrize(("control", "state"), [
    ("eq", {"enabled": 1}), ("eq", {"enabled": "true"}),
    ("dynamics", {"enabled": None}), ("eq", {}),
    ("eq", {"enabled": True, "code": "arbitrary"}),
    ("record_arm", {"armed": 1}), ("record_arm", {"enabled": True}),
    ("input_patch", {"source_name": "Fifine"}),
    ("input_patch", {"source_name": "Fifine", "source_channel": True}),
    ("input_patch", {"source_name": "Fifine", "source_channel": 0}),
    ("input_patch", {"source_name": "Fifine", "source_channel": 65}),
    ("input_patch", {"source_name": "Fifine", "source_channel": "1"}),
    ("input_patch", {"source_name": " bad", "source_channel": 1}),
    ("input_patch", {"source_name": "bad\nname", "source_channel": 1}),
    ("input_patch", {"source_name": "x" * 129, "source_channel": 1}),
    ("input_patch", {"source_name": "Fifine", "source_channel": 1, "selector": "x"}),
])
def test_desired_boolean_and_source_shapes_are_validated_before_work(
    harness: Harness, control: str, state: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        harness.change(control, desired=state)
    assert harness.events == []


@pytest.mark.parametrize("control", ["eq", "dynamics", "record_arm"])
def test_explicit_false_is_preserved_as_desired_state(
    harness: Harness, control: str,
) -> None:
    state = desired(control, enabled=False)
    assert harness.change(control, desired=state)["desired"] == state
    assert harness.adapter.calls[0]["desired"] == state


@pytest.mark.parametrize(("field", "value"), [
    ("recording", True), ("playing", True), ("stopped", False),
    ("recording", 0), ("playing", "false"), ("stopped", 1),
])
def test_non_stopped_or_untyped_status_blocks_native_backup_and_ui(
    harness: Harness, field: str, value: Any,
) -> None:
    harness.transport.state[field] = value
    with pytest.raises(WindowsRecordingError, match="TRANSPORT_NOT_STOPPED"):
        harness.change()
    assert harness.transport.calls == 1
    assert harness.client.backups == [] and harness.adapter.calls == []
    assert not (harness.service.root / "client.lock").exists()


def test_transport_read_failure_blocks_backup_without_creating_a_ui_receipt(
    harness: Harness,
) -> None:
    harness.transport.error = WindowsRecordingError("UNVERIFIED_RESPONSE")
    with pytest.raises(WindowsRecordingError):
        harness.change()
    assert harness.client.backups == [] and harness.adapter.calls == []
    assert list((harness.service.root / "receipts").glob("*.json")) == []


def test_native_backup_failure_blocks_ui_and_does_not_begin_a_ui_receipt(
    harness: Harness,
) -> None:
    harness.client.backup_error = RuntimeError("Native backup was not verified.")
    with pytest.raises(RuntimeError, match="backup"):
        harness.change()
    assert len(harness.client.backups) == 1 and harness.adapter.calls == []
    assert list((harness.service.root / "receipts").glob("*.json")) == []
    assert not (harness.service.root / "client.lock").exists()


@pytest.mark.parametrize("control", CONTROLS)
def test_uncertain_setter_reconciles_same_key_with_getter_only(
    harness: Harness, control: str,
) -> None:
    harness.adapter.setter_error = WindowsRecordingError("TIMEOUT", uncertain=True)
    with pytest.raises(WindowsRecordingError, match="TIMEOUT"):
        harness.change(control)
    assert harness.receipt()["status"] == "pending"
    before = len(harness.events)
    result = harness.change(control)
    assert result["state_reconciled"] is True and result["status"] == "completed"
    assert result["save_status"] == "not_reverified"
    assert result["persistence_verified"] is False
    assert harness.events[before:] == [
        "native:get_current_project", "native:get_timeline_summary",
        f"ui:inspect:{control}",
        "native:get_current_project", "native:get_timeline_summary",
    ]
    assert [call["kind"] for call in harness.adapter.calls] == ["set", "inspect"]
    assert len(harness.client.backups) == 1 and harness.transport.calls == 1
    assert harness.receipt()["ui_reconcile_evidence"] == result["ui"]
    assert harness.receipt()["backup"] == harness.client.backup_result


@pytest.mark.parametrize("control", CONTROLS)
def test_failed_original_save_recovers_observed_state_without_claiming_persistence(
    harness: Harness, control: str,
) -> None:
    harness.adapter.setter_error = WindowsRecordingError("SAVE_FAILED", uncertain=True)
    with pytest.raises(WindowsRecordingError, match="SAVE_FAILED"):
        harness.change(control)
    result = harness.change(control)
    assert result["state_reconciled"] is True
    assert result["save_status"] == "not_reverified"
    assert result["persistence_verified"] is False
    assert "changed" not in result["ui"]
    assert [call["kind"] for call in harness.adapter.calls] == ["set", "inspect"]
    assert len(harness.client.backups) == 1 and harness.transport.calls == 1
    assert harness.receipt()["result"]["persistence_verified"] is False


@pytest.mark.parametrize("control", CONTROLS)
def test_reconcile_mismatch_stays_pending_and_never_repeats_setter(
    harness: Harness, control: str,
) -> None:
    harness.adapter.setter_error = WindowsRecordingError("TIMEOUT", uncertain=True)
    with pytest.raises(WindowsRecordingError):
        harness.change(control)
    harness.adapter.states[control] = (
        {"source_name": "Other input", "source_channel": 2}
        if control == "input_patch" else desired(control, enabled=False)
    )
    for _ in range(2):
        with pytest.raises(WindowsRecordingError, match="STATE_RECONCILE_MISMATCH"):
            harness.change(control)
    assert harness.receipt()["status"] == "pending"
    assert [call["kind"] for call in harness.adapter.calls] == [
        "set", "inspect", "inspect"
    ]
    assert len(harness.client.backups) == 1 and harness.transport.calls == 1


@pytest.mark.parametrize("control", CONTROLS)
def test_definite_setter_failure_rejects_receipt_and_cannot_be_resubmitted(
    harness: Harness, control: str,
) -> None:
    harness.adapter.setter_error = WindowsRecordingError(
        "WRONG_TARGET", uncertain=False
    )
    with pytest.raises(WindowsRecordingError):
        harness.change(control)
    assert harness.receipt()["status"] == "rejected"
    assert harness.receipt()["error"] == "WRONG_TARGET"
    before = list(harness.events)
    with pytest.raises(RuntimeError, match="No write was resubmitted"):
        harness.change(control)
    assert harness.events == before


@pytest.mark.parametrize("control", CONTROLS)
def test_native_post_fence_failure_retains_ui_evidence_and_pending_backup(
    harness: Harness, control: str,
) -> None:
    def switch_project() -> None:
        harness.client.project["id"] = "foreign"

    harness.adapter.after_ui = switch_project
    with pytest.raises(ValueError, match="identity changed"):
        harness.change(control)
    receipt = harness.receipt()
    assert receipt["status"] == "pending"
    assert receipt["ui_evidence"]["recording_ready"] is None
    assert receipt["backup"] == harness.client.backup_result
    assert len(harness.adapter.calls) == 1
    assert not (harness.service.root / "client.lock").exists()


def test_reconcile_native_post_fence_failure_also_retains_readback_evidence(
    harness: Harness,
) -> None:
    harness.adapter.setter_error = WindowsRecordingError("TIMEOUT", uncertain=True)
    with pytest.raises(WindowsRecordingError):
        harness.change()

    def change_count() -> None:
        harness.client.audio_track_count = 4

    harness.adapter.after_ui = change_count
    with pytest.raises(ValueError, match="target changed"):
        harness.change()
    receipt = harness.receipt()
    assert receipt["status"] == "pending" and "ui_reconcile_evidence" in receipt
    assert len(harness.client.backups) == 1 and harness.transport.calls == 1


def test_pending_target_change_blocks_getter_and_preserves_original_receipt(
    harness: Harness,
) -> None:
    harness.adapter.setter_error = WindowsRecordingError("TIMEOUT", uncertain=True)
    with pytest.raises(WindowsRecordingError):
        harness.change()
    original = harness.receipt()
    harness.client.audio_track_count = 4
    with pytest.raises(ValueError, match="changed since the pending write"):
        harness.change()
    assert harness.receipt() == original
    assert len(harness.adapter.calls) == 1
    assert len(harness.client.backups) == 1 and harness.transport.calls == 1


@pytest.mark.parametrize("control", CONTROLS)
def test_same_key_different_arguments_never_reaches_native_or_ui(
    harness: Harness, control: str,
) -> None:
    harness.change(control)
    before = list(harness.events)
    state = (
        {"source_name": "Other", "source_channel": 2}
        if control == "input_patch" else desired(control, enabled=False)
    )
    with pytest.raises(ValueError, match="different arguments"):
        harness.change(control, desired=state)
    assert harness.events == before


@pytest.mark.parametrize("write", [False, True])
def test_existing_foreign_mailbox_blocks_work_and_remains_owned(
    harness: Harness, write: bool,
) -> None:
    lock = harness.service.root / "client.lock"
    lock.write_text("foreign-owner", encoding="ascii")
    with pytest.raises(FileExistsError):
        harness.change() if write else harness.inspect()
    assert lock.read_text(encoding="ascii") == "foreign-owner"
    assert harness.events == []


def test_lock_cleanup_never_removes_a_replacement_owner(harness: Harness) -> None:
    lock = harness.service.root / "client.lock"

    def replace_owner() -> None:
        lock.write_text("replacement-owner", encoding="ascii")

    harness.adapter.after_ui = replace_owner
    with pytest.raises(FileExistsError):
        harness.change()
    assert lock.read_text(encoding="ascii") == "replacement-owner"
    assert harness.receipt()["status"] == "pending"
    assert "ui_evidence" in harness.receipt()


def test_getter_error_releases_owned_mailbox_without_a_write_receipt(
    harness: Harness,
) -> None:
    harness.adapter.getter_error = WindowsRecordingError("WRONG_TARGET")
    with pytest.raises(WindowsRecordingError):
        harness.inspect()
    assert not (harness.service.root / "client.lock").exists()
    assert not (harness.service.root / "receipts").exists()
