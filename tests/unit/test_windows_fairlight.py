"""Exercise the Fairlight boundary with synthetic results and no live UI calls."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from providers.resolve import windows_fairlight as module
from providers.resolve.windows_fairlight import FairlightTarget, WindowsFairlightAdapter
from providers.resolve.windows_recording import WindowsRecordingError

PROJECT = "Тестовий проєкт"
TRACK = FairlightTarget("Основна", "track", "Голос", 2, 3)
BUS = FairlightTarget("Основна", "bus", "Bus 1")
OPERATIONS = (
    "inspect_processing", "set_processing", "inspect_arm", "set_arm",
    "inspect_patch", "set_patch",
)


def response(
    operation: str = "inspect_processing", target: FairlightTarget = TRACK,
    processing_module: str = "eq",
) -> dict[str, Any]:
    """Return synthetic readback; this fixture cannot establish live support."""
    result: dict[str, Any] = {
        "ok": True,
        "operation": operation,
        "backend": "windows_fairlight",
        "project_name": PROJECT,
        "window": {"pid": 123, "handle": 456},
        "target": target.as_dict(),
        "transport": {
            "recording": False, "playing": False, "stopped": True,
            "verification": "windows_uia_toggle_state",
        },
        "recording_ready": None,
        "compatibility": {
            "status": "live_pending", "profile": "resolve-21.1-english",
        },
    }
    if operation.startswith("set_"):
        result["changed"] = True
    if operation.endswith("processing"):
        result["processing"] = {
            "module": processing_module,
            "enabled": True,
            "verification": (
                "windows_uia_reopened" if processing_module == "eq"
                else "windows_visual_reopened"
            ),
        }
    elif operation.endswith("arm"):
        result["record_arm"] = {
            "armed": True, "verification": "windows_visual_reacquired",
        }
    else:
        result["input_patch"] = {
            "patched": True, "source_name": "Fifine", "source_channel": 1,
            "verification": "windows_uia_patch_reopened",
        }
    return result


def invoke(
    adapter: WindowsFairlightAdapter, operation: str,
    target: FairlightTarget = TRACK, processing_module: str = "eq",
) -> dict[str, Any]:
    """Dispatch the six typed public methods with valid fixture arguments."""
    if operation == "inspect_processing":
        return adapter.inspect_processing(PROJECT, target, processing_module)  # type: ignore[arg-type]
    if operation == "set_processing":
        return adapter.set_processing(PROJECT, target, processing_module, True)  # type: ignore[arg-type]
    if operation == "inspect_arm":
        return adapter.inspect_arm(PROJECT, target)
    if operation == "set_arm":
        return adapter.set_arm(PROJECT, target, True)
    if operation == "inspect_patch":
        return adapter.inspect_patch(PROJECT, target)
    if operation == "set_patch":
        return adapter.set_patch(PROJECT, target, "Fifine", 1)
    raise AssertionError("Unexpected test operation.")


@pytest.fixture
def driver(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Intercept every subprocess call, including on a Windows test runner."""
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[dict[str, Any]] = []
    state: dict[str, Any] = {"result": response(), "returncode": 0}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        if isinstance(state.get("error"), Exception):
            raise state["error"]
        stdout = state.get("stdout", json.dumps(state["result"], ensure_ascii=False))
        return subprocess.CompletedProcess(command, state["returncode"], stdout, "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls, state


def test_target_serializes_all_five_fields_and_is_frozen() -> None:
    assert TRACK.as_dict() == {
        "timeline_name": "Основна", "scope": "track", "name": "Голос",
        "index": 2, "audio_track_count": 3,
    }
    assert BUS.as_dict() == {
        "timeline_name": "Основна", "scope": "bus", "name": "Bus 1",
        "index": None, "audio_track_count": None,
    }
    with pytest.raises(FrozenInstanceError):
        TRACK.name = "Other"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["timeline_name", "name"])
@pytest.mark.parametrize(
    "value", [None, "", " a", "a ", "a\n", "a\x00b", "a\x7f", "x" * 129]
)
def test_target_names_must_be_bounded_control_free_text(field: str, value: Any) -> None:
    arguments = TRACK.as_dict()
    arguments[field] = value
    with pytest.raises(ValueError):
        FairlightTarget(**arguments)


@pytest.mark.parametrize(
    ("index", "count"),
    [(None, None), (0, 3), (-1, 3), (65, 65), (True, 3), (1, True),
     (1.0, 3), (1, 3.0), (2, 1), (1, None), (1, 65)],
)
def test_track_target_requires_bounded_index_and_native_count(
    index: Any, count: Any,
) -> None:
    with pytest.raises(ValueError):
        FairlightTarget("Timeline", "track", "Voice", index, count)


@pytest.mark.parametrize("scope", ["Track", "track; code", "", None, 1])
def test_target_scope_is_closed(scope: Any) -> None:
    with pytest.raises(ValueError):
        FairlightTarget("Timeline", scope, "Voice", 1, 1)


@pytest.mark.parametrize(("index", "count"), [(1, None), (None, 1), (0, 0)])
def test_bus_target_cannot_contain_track_coordinates(index: Any, count: Any) -> None:
    with pytest.raises(ValueError):
        FairlightTarget("Timeline", "bus", "Bus 1", index, count)


@pytest.mark.parametrize("target", [
    FairlightTarget("x" * 128, "track", "y" * 128, 1, 1),
    FairlightTarget("Timeline", "track", "Last", 64, 64), BUS,
])
def test_target_accepts_boundaries(target: FairlightTarget) -> None:
    assert FairlightTarget(**target.as_dict()) == target


def test_default_opt_in_and_non_windows_gate_never_start_a_process(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = driver
    adapter = WindowsFairlightAdapter()
    assert adapter.capabilities()["available"] is False
    assert adapter.capabilities()["live_verification"] == "pending"
    assert adapter.capabilities()["recording_ready"] is None
    with pytest.raises(WindowsRecordingError, match="OPT_IN_REQUIRED"):
        invoke(adapter, "inspect_processing")
    monkeypatch.setattr(sys, "platform", "linux")
    enabled = WindowsFairlightAdapter(enabled=True)
    assert enabled.capabilities()["platform_supported"] is False
    assert enabled.capabilities()["available"] is False
    with pytest.raises(WindowsRecordingError, match="WINDOWS_REQUIRED"):
        invoke(enabled, "inspect_processing")
    assert calls == []


def test_enabled_capabilities_remain_live_pending_and_expose_no_record_operation(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, _ = driver
    capabilities = WindowsFairlightAdapter(enabled=True).capabilities()
    assert capabilities["available"] is True
    assert capabilities["backend"] == "windows_fairlight"
    assert capabilities["operations"] == list(OPERATIONS)
    assert capabilities["controls"] == [
        "track_bus_eq", "track_bus_dynamics", "input_patching", "record_arm"
    ]
    assert capabilities["compatibility"] == {
        "status": "live_pending", "profile": "resolve-21.1-english"
    }
    assert capabilities["recording_ready"] is None
    assert calls == []


@pytest.mark.parametrize("enabled", [None, 1, 0, "true"])
def test_opt_in_requires_a_real_boolean(enabled: Any) -> None:
    with pytest.raises(ValueError, match="boolean"):
        WindowsFairlightAdapter(enabled=enabled)


@pytest.mark.parametrize("value", [0, -1, True, "120", float("inf"), float("nan"), 121])
def test_timeout_must_be_bounded(value: Any) -> None:
    with pytest.raises(ValueError, match="timeout"):
        WindowsFairlightAdapter(enabled=True, timeout_seconds=value)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_each_method_uses_fixed_resource_and_exact_unicode_json_payload(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    calls, state = driver
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    state["result"] = response(operation)
    result = invoke(WindowsFairlightAdapter(enabled=True), operation)
    assert result == state["result"]
    assert result["recording_ready"] is None
    assert len(calls) == 1
    call = calls[0]
    assert call["command"] == [
        str(
            Path(r"C:\Windows") / "System32" / "WindowsPowerShell"
            / "v1.0" / "powershell.exe"
        ),
        "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy",
        "Bypass", "-File",
        str(Path(module.__file__).with_name("ResolveFairlightUI.ps1")),
    ]
    payload: dict[str, Any] = {
        "operation": operation, "expected_project_name": PROJECT,
        "target": TRACK.as_dict(),
    }
    if operation.endswith("processing"):
        payload["module"] = "eq"
    if operation == "set_processing":
        payload["enabled"] = True
    elif operation == "set_arm":
        payload["armed"] = True
    elif operation == "set_patch":
        payload.update(source_name="Fifine", source_channel=1)
    assert json.loads(call["input"]) == payload
    assert PROJECT in call["input"]
    assert PROJECT not in " ".join(call["command"])
    assert call["encoding"] == "utf-8" and call["capture_output"] is True
    assert call["timeout"] == 120 and call["check"] is False
    assert "shell" not in call
    assert "Record" not in payload.values()


@pytest.mark.parametrize("target", [TRACK, BUS])
@pytest.mark.parametrize("operation", ["inspect_processing", "set_processing"])
@pytest.mark.parametrize("processing_module", ["eq", "dynamics"])
def test_processing_accepts_tracks_and_buses_with_module_specific_readback(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    target: FairlightTarget, operation: str, processing_module: str,
) -> None:
    _, state = driver
    state["result"] = response(operation, target, processing_module)
    invoke(WindowsFairlightAdapter(enabled=True), operation, target, processing_module)


@pytest.mark.parametrize(
    "operation", ["inspect_arm", "set_arm", "inspect_patch", "set_patch"]
)
def test_track_only_controls_reject_buses_before_any_process(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], operation: str,
) -> None:
    calls, _ = driver
    with pytest.raises(ValueError, match="track target"):
        invoke(WindowsFairlightAdapter(enabled=True), operation, BUS)
    assert calls == []


@pytest.mark.parametrize("operation", OPERATIONS)
def test_public_methods_reject_raw_target_dicts_before_any_process(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], operation: str,
) -> None:
    calls, _ = driver
    with pytest.raises(ValueError):
        invoke(WindowsFairlightAdapter(enabled=True), operation, TRACK.as_dict())  # type: ignore[arg-type]
    assert calls == []


@pytest.mark.parametrize("name", ["", " a", "a\n", "a\x00b", "x" * 129])
def test_project_name_validation_precedes_subprocess(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], name: str,
) -> None:
    calls, _ = driver
    with pytest.raises(ValueError):
        WindowsFairlightAdapter(enabled=True).inspect_arm(name, TRACK)
    assert calls == []


@pytest.mark.parametrize(
    "processing_module", ["EQ", "compression", "eq; arbitrary", None, 1]
)
def test_processing_module_is_a_closed_choice(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], processing_module: Any,
) -> None:
    calls, _ = driver
    with pytest.raises(ValueError, match="module"):
        WindowsFairlightAdapter(enabled=True).inspect_processing(
            PROJECT, TRACK, processing_module
        )
    assert calls == []


@pytest.mark.parametrize("value", [0, 1, "true", None])
@pytest.mark.parametrize("operation", ["processing", "arm"])
def test_setter_desired_states_require_real_booleans(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], value: Any, operation: str,
) -> None:
    calls, _ = driver
    adapter = WindowsFairlightAdapter(enabled=True)
    with pytest.raises(ValueError, match="boolean"):
        if operation == "processing":
            adapter.set_processing(PROJECT, TRACK, "eq", value)
        else:
            adapter.set_arm(PROJECT, TRACK, value)
    assert calls == []


@pytest.mark.parametrize("name", [None, "", " name", "x\n", "x" * 129])
def test_patch_source_names_are_bounded(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], name: Any,
) -> None:
    calls, _ = driver
    with pytest.raises(ValueError):
        WindowsFairlightAdapter(enabled=True).set_patch(PROJECT, TRACK, name, 1)
    assert calls == []


@pytest.mark.parametrize("channel", [None, 0, -1, 65, 1.0, True, "1"])
def test_patch_source_channel_is_a_bounded_integer(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], channel: Any,
) -> None:
    calls, _ = driver
    with pytest.raises(ValueError):
        WindowsFairlightAdapter(enabled=True).set_patch(
            PROJECT, TRACK, "Fifine", channel
        )
    assert calls == []


def test_source_name_stays_literal_json_data(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    source = 'Мікрофон $(Write-Output "literal"); & example'
    state["result"] = response("set_patch")
    state["result"]["input_patch"]["source_name"] = source
    WindowsFairlightAdapter(enabled=True).set_patch(PROJECT, TRACK, source, 1)
    assert json.loads(calls[0]["input"])["source_name"] == source
    assert source not in " ".join(calls[0]["command"])


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(("key", "value"), [
    ("project_name", "Other"), ("operation", "other"), ("backend", "other"),
    ("recording_ready", True), ("window", {"pid": 0, "handle": 456}),
    ("window", {"pid": 123, "handle": -1}),
    ("window", {"pid": True, "handle": 456}), ("window", {"pid": 123}),
    ("compatibility", {"status": "verified", "profile": "resolve-21.1-english"}),
    ("compatibility", {"status": "live_pending", "profile": "other"}),
    ("unexpected", "unvalidated"),
])
def test_driver_common_identity_and_live_status_are_guarded(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    operation: str, key: str, value: Any,
) -> None:
    _, state = driver
    state["result"] = response(operation)
    state["result"][key] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE") as error:
        invoke(WindowsFairlightAdapter(enabled=True), operation)
    assert error.value.uncertain is operation.startswith("set_")


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(("key", "value"), [
    ("recording", True), ("playing", True), ("stopped", False),
    ("recording", 0), ("playing", "false"), ("stopped", 1),
    ("verification", "visible_only"),
])
def test_every_operation_requires_a_verified_stopped_transport(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    operation: str, key: str, value: Any,
) -> None:
    _, state = driver
    state["result"] = response(operation)
    state["result"]["transport"][key] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE"):
        invoke(WindowsFairlightAdapter(enabled=True), operation)


@pytest.mark.parametrize(("field", "value"), [
    ("timeline_name", "Other"), ("scope", "bus"), ("name", "Other"),
    ("index", 1), ("index", True), ("audio_track_count", 2),
    ("audio_track_count", 3.0), ("other", "selector"),
])
def test_response_target_must_match_every_native_checked_field(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], field: str, value: Any,
) -> None:
    _, state = driver
    state["result"]["target"][field] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE"):
        invoke(WindowsFairlightAdapter(enabled=True), "inspect_processing")


@pytest.mark.parametrize(("field", "value"), [
    ("module", "dynamics"), ("enabled", 1),
    ("verification", "windows_visual_reopened"),
])
def test_eq_readback_requires_exact_module_and_verification(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], field: str, value: Any,
) -> None:
    _, state = driver
    state["result"]["processing"][field] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE"):
        invoke(WindowsFairlightAdapter(enabled=True), "inspect_processing")


@pytest.mark.parametrize(("operation", "field", "value"), [
    ("set_processing", "enabled", False), ("set_arm", "armed", False),
    ("set_patch", "patched", False), ("set_patch", "source_name", "Other"),
    ("set_patch", "source_channel", 2),
])
def test_setter_requires_exact_desired_readback(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    operation: str, field: str, value: Any,
) -> None:
    _, state = driver
    state["result"] = response(operation)
    detail = "processing" if operation.endswith("processing") else (
        "record_arm" if operation.endswith("arm") else "input_patch"
    )
    state["result"][detail][field] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE") as error:
        invoke(WindowsFairlightAdapter(enabled=True), operation)
    assert error.value.uncertain is True


@pytest.mark.parametrize("operation", ["set_processing", "set_arm", "set_patch"])
@pytest.mark.parametrize("changed", [None, 0, 1, "true"])
def test_setter_changed_flag_must_be_boolean(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], operation: str, changed: Any,
) -> None:
    _, state = driver
    state["result"] = response(operation)
    state["result"]["changed"] = changed
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE"):
        invoke(WindowsFairlightAdapter(enabled=True), operation)


@pytest.mark.parametrize("operation", ["set_processing", "set_arm", "set_patch"])
def test_setter_can_report_no_change_after_verified_readback(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], operation: str,
) -> None:
    _, state = driver
    state["result"] = response(operation)
    state["result"]["changed"] = False
    assert invoke(WindowsFairlightAdapter(enabled=True), operation)["changed"] is False


@pytest.mark.parametrize("operation", ["set_processing", "set_arm"])
def test_boolean_setters_can_disable_with_explicit_false_readback(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], operation: str,
) -> None:
    calls, state = driver
    state["result"] = response(operation)
    adapter = WindowsFairlightAdapter(enabled=True)
    if operation == "set_processing":
        state["result"]["processing"]["enabled"] = False
        result = adapter.set_processing(PROJECT, TRACK, "eq", False)
        assert result["processing"]["enabled"] is False
        assert json.loads(calls[0]["input"])["enabled"] is False
    else:
        state["result"]["record_arm"]["armed"] = False
        result = adapter.set_arm(PROJECT, TRACK, False)
        assert result["record_arm"]["armed"] is False
        assert json.loads(calls[0]["input"])["armed"] is False


@pytest.mark.parametrize("value", [1, "true", None])
def test_arm_readback_must_be_boolean(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], value: Any,
) -> None:
    _, state = driver
    state["result"] = response("inspect_arm")
    state["result"]["record_arm"]["armed"] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE"):
        invoke(WindowsFairlightAdapter(enabled=True), "inspect_arm")


@pytest.mark.parametrize(("field", "value"), [
    ("patched", 1), ("source_name", ""), ("source_name", "bad\nname"),
    ("source_channel", 0), ("source_channel", 65), ("source_channel", True),
    ("verification", "visible_only"),
])
def test_patched_readback_requires_source_channel_and_reopened_verification(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], field: str, value: Any,
) -> None:
    _, state = driver
    state["result"] = response("inspect_patch")
    state["result"]["input_patch"][field] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE"):
        invoke(WindowsFairlightAdapter(enabled=True), "inspect_patch")


@pytest.mark.parametrize(("name", "channel", "valid"), [
    (None, None, True), ("Fifine", None, False), (None, 1, False),
])
def test_unpatched_readback_requires_both_source_fields_to_be_null(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    name: Any, channel: Any, valid: bool,
) -> None:
    _, state = driver
    state["result"] = response("inspect_patch")
    state["result"]["input_patch"].update(
        patched=False, source_name=name, source_channel=channel
    )
    if valid:
        result = invoke(WindowsFairlightAdapter(enabled=True), "inspect_patch")
        assert result["input_patch"]["patched"] is False
    else:
        with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE"):
            invoke(WindowsFairlightAdapter(enabled=True), "inspect_patch")


def test_patch_accepts_bounded_source_and_last_channel(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["result"] = response("set_patch")
    state["result"]["input_patch"].update(source_name="x" * 128, source_channel=64)
    result = WindowsFairlightAdapter(enabled=True).set_patch(
        PROJECT, TRACK, "x" * 128, 64
    )
    assert result["input_patch"]["source_channel"] == 64


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "failure", ["timeout", "os_error", "malformed", "non_object", "failed_exit"]
)
def test_child_failures_are_uncertain_only_for_mutations(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], operation: str, failure: str,
) -> None:
    _, state = driver
    state["result"] = response(operation)
    if failure == "timeout":
        state["error"] = subprocess.TimeoutExpired(["fixed-driver"], 120)
    elif failure == "os_error":
        state["error"] = OSError("Cannot create child.")
    elif failure == "malformed":
        state["stdout"] = "not-json"
    elif failure == "non_object":
        state["stdout"] = "[]"
    else:
        state["returncode"] = 1
    with pytest.raises(WindowsRecordingError) as error:
        invoke(WindowsFairlightAdapter(enabled=True), operation)
    assert error.value.uncertain is operation.startswith("set_")


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("uncertain", [None, False, True, 0, "false"])
def test_driver_error_can_clear_mutation_uncertainty_only_with_boolean_false(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], operation: str, uncertain: Any,
) -> None:
    _, state = driver
    state["result"] = {
        "ok": False, "error": {"code": "WRONG_TARGET", "uncertain": uncertain},
    }
    with pytest.raises(WindowsRecordingError, match="WRONG_TARGET") as error:
        invoke(WindowsFairlightAdapter(enabled=True), operation)
    assert error.value.uncertain is (
        operation.startswith("set_") and uncertain is not False
    )


@pytest.mark.parametrize("detail", [None, [], {}, {"code": "bad\ncode"}, {"code": 1}])
def test_driver_error_details_cannot_become_unbounded_exception_text(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], detail: Any,
) -> None:
    _, state = driver
    state["result"] = {"ok": False, "error": detail}
    with pytest.raises(WindowsRecordingError, match="UI_AUTOMATION_FAILED") as error:
        invoke(WindowsFairlightAdapter(enabled=True), "set_arm")
    assert error.value.uncertain is True


def test_success_accepts_utf8_bom_from_fixed_powershell_driver(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["stdout"] = "\ufeff" + json.dumps(response())
    invoke(WindowsFairlightAdapter(enabled=True), "inspect_processing")
