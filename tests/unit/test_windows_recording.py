"""Validate the opt-in UI boundary without launching PowerShell or Resolve."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from providers.resolve import windows_recording as module
from providers.resolve.windows_recording import (
    WindowsRecordingAdapter,
    WindowsRecordingError,
)


def response(operation: str = "inspect_transport") -> dict[str, Any]:
    """Return a synthetic driver result, never live acceptance evidence."""
    result: dict[str, Any] = {
        "ok": True,
        "operation": operation,
        "backend": "windows_uia",
        "project_name": "Тестовий проєкт",
        "window": {"pid": 123, "handle": 456},
        "transport": {
            "recording": False,
            "playing": False,
            "stopped": True,
            "verification": "windows_uia_toggle_state",
        },
        "recording_ready": None,
    }
    if operation == "set_input_device":
        result.update(
            input_device={
                "name": "Мікрофон (Example USB)",
                "changed": True,
                "verification": "windows_uia_reopened",
            },
            input_patching_verified=False,
            routing_may_require_repatch=True,
        )
    elif operation == "inspect_input_device":
        result["input_device"] = {
            "name": "Мікрофон (Example USB)",
            "verification": "windows_uia_preferences_readback",
        }
    return result


@pytest.fixture
def driver(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Intercept every child-process call, including on a Windows test host."""
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[dict[str, Any]] = []
    state: dict[str, Any] = {"result": response(), "returncode": 0}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        error = state.get("error")
        if isinstance(error, Exception):
            raise error
        output = state.get("stdout", json.dumps(state["result"], ensure_ascii=False))
        return subprocess.CompletedProcess(command, state["returncode"], output, "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls, state


def test_adapter_is_disabled_until_explicitly_enabled(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, _ = driver
    adapter = WindowsRecordingAdapter()
    assert adapter.capabilities()["recording_ready"] is None
    assert adapter.capabilities()["available"] is False
    with pytest.raises(WindowsRecordingError, match="OPT_IN_REQUIRED"):
        adapter.inspect_transport("Тестовий проєкт")
    assert calls == []


def test_non_windows_failure_never_starts_a_process(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = driver
    monkeypatch.setattr(sys, "platform", "linux")
    adapter = WindowsRecordingAdapter(enabled=True)
    assert not adapter.capabilities()["platform_supported"]
    assert adapter.capabilities()["available"] is False
    with pytest.raises(WindowsRecordingError, match="WINDOWS_REQUIRED"):
        adapter.inspect_transport("Тестовий проєкт")
    assert calls == []


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), 121])
def test_timeout_must_be_bounded(value: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        WindowsRecordingAdapter(enabled=True, timeout_seconds=value)


@pytest.mark.parametrize("value", ["", " a", "a\n", "a\x00b", "x" * 129])
def test_bad_project_names_cannot_launch_the_driver(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], value: str
) -> None:
    calls, _ = driver
    with pytest.raises(ValueError):
        WindowsRecordingAdapter(enabled=True).inspect_transport(value)
    assert calls == []


def test_read_uses_fixed_script_with_unicode_json_on_stdin(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, _ = driver
    result = WindowsRecordingAdapter(enabled=True).inspect_transport("Тестовий проєкт")
    assert result["transport"]["stopped"] is True
    assert len(calls) == 1
    call = calls[0]
    command = call["command"]
    assert command[1:-1] == [
        "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
        "-ExecutionPolicy", "Bypass", "-File",
    ]
    assert Path(command[-1]).name == "ResolveRecordingUI.ps1"
    assert "Тестовий проєкт" not in " ".join(command)
    assert json.loads(call["input"]) == {
        "operation": "inspect_transport",
        "expected_project_name": "Тестовий проєкт",
    }
    assert call["encoding"] == "utf-8"
    assert call["check"] is False
    assert "shell" not in call


def test_device_name_is_data_not_powershell_source(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    name = 'Мікрофон $(Write-Output "literal"); & example'
    state["result"] = response("set_input_device")
    state["result"]["input_device"]["name"] = name
    result = WindowsRecordingAdapter(enabled=True).set_input_device(
        "Тестовий проєкт", name
    )
    assert result["input_device"]["name"] == name
    assert json.loads(calls[0]["input"])["device_name"] == name
    assert name not in " ".join(calls[0]["command"])


def test_unchanged_device_does_not_claim_repatch_is_needed(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["result"] = response("set_input_device")
    state["result"]["input_device"]["changed"] = False
    state["result"]["routing_may_require_repatch"] = False
    result = WindowsRecordingAdapter(enabled=True).set_input_device(
        "Тестовий проєкт", "Мікрофон (Example USB)"
    )
    assert result["input_device"]["changed"] is False
    assert result["input_patching_verified"] is False


def test_input_reconciliation_read_does_not_send_an_expected_device_or_write(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    state["result"] = response("inspect_input_device")
    result = WindowsRecordingAdapter(enabled=True).inspect_input_device(
        "Тестовий проєкт"
    )
    assert result["input_device"]["name"] == "Мікрофон (Example USB)"
    assert json.loads(calls[0]["input"]) == {
        "operation": "inspect_input_device",
        "expected_project_name": "Тестовий проєкт",
    }
    assert "set_input_device" not in calls[0]["input"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", ""), ("name", "bad\nname"), ("verification", "visible_only")],
)
def test_reconciliation_input_readback_must_be_verified_without_claiming_a_write(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], field: str, value: str
) -> None:
    _, state = driver
    state["result"] = response("inspect_input_device")
    state["result"]["input_device"][field] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE") as error:
        WindowsRecordingAdapter(enabled=True).inspect_input_device("Тестовий проєкт")
    assert error.value.uncertain is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("project_name", "Other project"),
        ("operation", "other"),
        ("backend", "other"),
        ("recording_ready", True),
        ("window", {"pid": True, "handle": 456}),
        ("transport", {}),
    ],
)
def test_response_identity_and_readiness_are_not_trusted(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], key: str, value: Any
) -> None:
    _, state = driver
    state["result"][key] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE"):
        WindowsRecordingAdapter(enabled=True).inspect_transport("Тестовий проєкт")


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", "Other"), ("verification", "visible_only"), ("changed", 1)],
)
def test_input_change_requires_reopened_readback(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], field: str, value: Any
) -> None:
    _, state = driver
    state["result"] = response("set_input_device")
    state["result"]["input_device"][field] = value
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE") as error:
        WindowsRecordingAdapter(enabled=True).set_input_device(
            "Тестовий проєкт", "Мікрофон (Example USB)"
        )
    assert error.value.uncertain


@pytest.mark.parametrize("field", ["recording", "playing", "stopped"])
def test_mutation_rejects_a_changed_transport(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], field: str
) -> None:
    _, state = driver
    state["result"] = response("set_input_device")
    state["result"]["transport"][field] = field != "stopped"
    with pytest.raises(WindowsRecordingError, match="UNVERIFIED_RESPONSE"):
        WindowsRecordingAdapter(enabled=True).set_input_device(
            "Тестовий проєкт", "Мікрофон (Example USB)"
        )


@pytest.mark.parametrize(
    "operation", ["inspect_transport", "inspect_input_device", "set_input_device"]
)
def test_timeout_marks_only_mutation_as_uncertain(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], operation: str
) -> None:
    _, state = driver
    state["error"] = subprocess.TimeoutExpired(["fixed-driver"], 60)
    adapter = WindowsRecordingAdapter(enabled=True)
    with pytest.raises(WindowsRecordingError, match="TIMEOUT") as error:
        if operation == "inspect_transport":
            adapter.inspect_transport("Тестовий проєкт")
        elif operation == "inspect_input_device":
            adapter.inspect_input_device("Тестовий проєкт")
        else:
            adapter.set_input_device("Тестовий проєкт", "Мікрофон (Example USB)")
    assert error.value.uncertain is (operation == "set_input_device")


@pytest.mark.parametrize("output", ["", "not-json", "[]", "{}"])
def test_unparseable_or_non_object_driver_output_is_not_success(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], output: str
) -> None:
    _, state = driver
    state["stdout"] = output
    with pytest.raises(WindowsRecordingError):
        WindowsRecordingAdapter(enabled=True).inspect_transport("Тестовий проєкт")


def test_structured_driver_error_preserves_safe_code_and_uncertainty(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["returncode"] = 1
    state["result"] = {
        "ok": False, "error": {"code": "CURSOR_INTERFERENCE", "uncertain": False}
    }
    with pytest.raises(WindowsRecordingError, match="CURSOR_INTERFERENCE") as error:
        WindowsRecordingAdapter(enabled=True).set_input_device(
            "Тестовий проєкт", "Мікрофон (Example USB)"
        )
    assert error.value.uncertain is False


def test_a_success_payload_cannot_override_failed_child_exit(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["returncode"] = 1
    with pytest.raises(WindowsRecordingError, match="DRIVER_FAILED"):
        WindowsRecordingAdapter(enabled=True).inspect_transport("Тестовий проєкт")


def test_driver_is_a_closed_semantic_interface_with_no_record_action() -> None:
    script = Path(module.__file__).with_name("ResolveRecordingUI.ps1").read_text(
        encoding="utf-8"
    )
    assert "ReadToEnd()" in script and "ConvertFrom-Json" in script
    assert "PREFERENCES_ALREADY_OPEN" in script
    assert "INPUT_REOPENED_READBACK_MISMATCH" in script
    assert "CURSOR_INTERFERENCE" in script and "WRONG_TARGET" in script
    assert "SetValue(" not in script and "SelectionItemPattern" not in script
    assert "Invoke-Expression" not in script and "SendKeys" not in script
    assert "StartRecording" not in script
    assert "GetPhysicalCursorPos" in script and "FromPoint" in script
    assert "System.Threading.Thread.Sleep(80)" in script
    assert "-ceq 'Record'" in script
    assert "routing_may_require_repatch=$changed" in script
