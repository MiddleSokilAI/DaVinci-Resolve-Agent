"""Opt-in, bounded Windows UI controls absent from Resolve's scripting SDK.

The bundled driver accepts three semantic operations as JSON data. It never
accepts caller-supplied scripts, selectors, coordinates, or keystrokes. Native
project-ID fencing and durable write receipts belong to the MCP caller.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from providers.resolve.lua_editing import bounded_text

UNSUPPORTED_CONTROLS = (
    "input_patching",
    "record_arm",
    "track_bus_eq",
    "track_bus_dynamics",
    "track_bus_effect_inserts",
)


class WindowsRecordingError(RuntimeError):
    """A bounded UI operation failed, possibly after changing an audio setting."""

    def __init__(self, code: str, *, uncertain: bool = False) -> None:
        self.code = code
        self.uncertain = uncertain
        super().__init__(
            f"Windows recording adapter: {code}. "
            + (
                "The audio setting may have changed; reconcile its original receipt."
                if uncertain
                else "No recording was requested."
            )
        )


@dataclass(frozen=True)
class WindowsRecordingAdapter:
    """Run the fixed UI driver only after an explicit host-side opt-in."""

    enabled: bool = False
    timeout_seconds: float = 60

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("Windows recording UI opt-in must be boolean.")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 120
        ):
            raise ValueError(
                "Windows UI timeout must be finite and at most 120 seconds."
            )

    def capabilities(self) -> dict[str, Any]:
        """Report platform/opt-in separately from live recording readiness."""
        return {
            "backend": "windows_uia",
            "enabled": self.enabled,
            "platform_supported": sys.platform == "win32",
            "available": self.enabled and sys.platform == "win32",
            "operations": [
                "inspect_transport", "inspect_input_device", "set_input_device"
            ],
            "unsupported_controls": list(UNSUPPORTED_CONTROLS),
            "recording_ready": None,
        }

    def inspect_transport(self, expected_project_name: str) -> dict[str, Any]:
        """Read the main Fairlight transport without input, focus, or capture."""
        return self._request("inspect_transport", expected_project_name)

    def inspect_input_device(self, expected_project_name: str) -> dict[str, Any]:
        """Read the stopped project's input from owned Preferences, then Cancel.

        This is a private-driver reconciliation primitive, not a write retry.
        It never selects a device or presses Save and refuses pre-opened dialogs.
        """
        return self._request("inspect_input_device", expected_project_name)

    def set_input_device(
        self, expected_project_name: str, device_name: str
    ) -> dict[str, Any]:
        """Select an existing input and verify it after reopening Preferences.

        Requires stopped transport and a fresh native project-ID fence supplied
        by the caller. Changing devices can invalidate existing physical input
        patches; this method does not claim that any track is ready to record.
        """
        return self._request(
            "set_input_device",
            expected_project_name,
            device_name=bounded_text(device_name, "Input device name", 256),
        )

    def _request(
        self, operation: str, project_name: str, **arguments: str
    ) -> dict[str, Any]:
        project_name = bounded_text(project_name, "Expected project name", 128)
        if not self.enabled:
            raise WindowsRecordingError("OPT_IN_REQUIRED")
        if sys.platform != "win32":
            raise WindowsRecordingError("WINDOWS_REQUIRED")
        driver = Path(__file__).with_name("ResolveRecordingUI.ps1")
        executable = (
            Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        payload = {
            "operation": operation,
            "expected_project_name": project_name,
            **arguments,
        }
        mutation = operation == "set_input_device"
        try:
            completed = subprocess.run(
                [
                    str(executable),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-STA",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(driver),
                ],
                input=json.dumps(payload, ensure_ascii=False, allow_nan=False),
                encoding="utf-8",
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as error:
            raise WindowsRecordingError("TIMEOUT", uncertain=mutation) from error
        except OSError as error:
            raise WindowsRecordingError("DRIVER_UNAVAILABLE") from error
        try:
            result = json.loads(completed.stdout.lstrip("\ufeff"))
        except (ValueError, TypeError) as error:
            raise WindowsRecordingError(
                "INVALID_RESPONSE", uncertain=mutation
            ) from error
        if not isinstance(result, dict):
            raise WindowsRecordingError("INVALID_RESPONSE", uncertain=mutation)
        if result.get("ok") is False:
            detail = result.get("error", {})
            code = detail.get("code") if isinstance(detail, dict) else None
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,80}", code):
                code = "UI_AUTOMATION_FAILED"
            uncertain = mutation and (
                not isinstance(detail, dict) or detail.get("uncertain") is not False
            )
            raise WindowsRecordingError(code, uncertain=uncertain)
        if completed.returncode or result.get("ok") is not True:
            raise WindowsRecordingError("DRIVER_FAILED", uncertain=mutation)
        self._validate_response(result, operation, project_name, arguments)
        return result

    @staticmethod
    def _validate_response(
        result: dict[str, Any],
        operation: str,
        project_name: str,
        arguments: dict[str, str],
    ) -> None:
        mutation = operation == "set_input_device"

        def invalid() -> None:
            raise WindowsRecordingError("UNVERIFIED_RESPONSE", uncertain=mutation)

        window = result.get("window")
        transport = result.get("transport")
        if (
            result.get("operation") != operation
            or result.get("project_name") != project_name
            or result.get("backend") != "windows_uia"
            or result.get("recording_ready") is not None
            or not isinstance(window, dict)
            or not isinstance(transport, dict)
        ):
            invalid()
        assert isinstance(window, dict) and isinstance(transport, dict)
        if any(
            type(window.get(key)) is not int or window[key] <= 0
            for key in ("pid", "handle")
        ):
            invalid()
        if set(transport) != {"recording", "playing", "stopped", "verification"} or any(
            type(transport.get(key)) is not bool
            for key in ("recording", "playing", "stopped")
        ):
            invalid()
        if transport.get("verification") != "windows_uia_toggle_state":
            invalid()
        if operation == "inspect_input_device":
            device = result.get("input_device")
            if (
                transport["recording"]
                or transport["playing"]
                or not transport["stopped"]
                or not isinstance(device, dict)
                or not isinstance(device.get("name"), str)
                or not device["name"]
                or device.get("verification") != "windows_uia_preferences_readback"
            ):
                invalid()
            assert isinstance(device, dict)
            try:
                bounded_text(device["name"], "Input readback", 256)
            except ValueError:
                invalid()
        if mutation:
            device = result.get("input_device")
            if (
                transport["recording"]
                or transport["playing"]
                or not transport["stopped"]
                or not isinstance(device, dict)
                or device.get("name") != arguments["device_name"]
                or device.get("verification") != "windows_uia_reopened"
                or type(device.get("changed")) is not bool
                or result.get("input_patching_verified") is not False
                or result.get("routing_may_require_repatch") is not device["changed"]
            ):
                invalid()
