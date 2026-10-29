"""Bounded, opt-in Fairlight UI operations absent from Resolve's scripting SDK.

Only typed semantic requests reach the bundled driver. Project-ID fencing,
mono-track checks, and durable mutation receipts belong to the MCP service.
Offline validation never establishes live compatibility or recording readiness.
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
from typing import Any, Literal, NoReturn

from providers.resolve.lua_editing import bounded_text
from providers.resolve.windows_recording import WindowsRecordingError

ProcessingModule = Literal["eq", "dynamics"]
_OPERATIONS = (
    "inspect_processing",
    "set_processing",
    "inspect_arm",
    "set_arm",
    "inspect_patch",
    "set_patch",
)
_MUTATIONS = frozenset({"set_processing", "set_arm", "set_patch"})


@dataclass(frozen=True)
class FairlightTarget:
    """Identify one native-checked track or bus without arbitrary UI selectors."""

    timeline_name: str
    scope: Literal["track", "bus"]
    name: str
    index: int | None = None
    audio_track_count: int | None = None

    def __post_init__(self) -> None:
        bounded_text(self.timeline_name, "Timeline name", 128)
        bounded_text(self.name, "Fairlight target name", 128)
        if self.scope == "track":
            if (
                type(self.index) is not int
                or not 1 <= self.index <= 64
                or type(self.audio_track_count) is not int
                or not self.index <= self.audio_track_count <= 64
            ):
                raise ValueError("Track index and audio track count must be 1 to 64.")
        elif self.scope == "bus":
            if self.index is not None or self.audio_track_count is not None:
                raise ValueError("Bus targets cannot include track index or count.")
        else:
            raise ValueError("Fairlight target scope must be track or bus.")

    def as_dict(self) -> dict[str, Any]:
        """Serialize all target fields for exact driver-response reconciliation."""
        return {
            "timeline_name": self.timeline_name,
            "scope": self.scope,
            "name": self.name,
            "index": self.index,
            "audio_track_count": self.audio_track_count,
        }


@dataclass(frozen=True)
class WindowsFairlightAdapter:
    """Run the fixed Fairlight driver only after explicit host-side opt-in."""

    enabled: bool = False
    timeout_seconds: float = 120

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("Windows Fairlight UI opt-in must be boolean.")
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
        """Separate platform availability from pending live verification."""
        return {
            "backend": "windows_fairlight",
            "enabled": self.enabled,
            "platform_supported": sys.platform == "win32",
            "available": self.enabled and sys.platform == "win32",
            "operations": list(_OPERATIONS),
            "controls": [
                "track_bus_eq",
                "track_bus_dynamics",
                "input_patching",
                "record_arm",
            ],
            "live_verification": "pending",
            "compatibility": {
                "status": "live_pending",
                "profile": "resolve-21.1-english",
            },
            "recording_ready": None,
        }

    def inspect_processing(
        self, expected_project_name: str, target: FairlightTarget,
        module: ProcessingModule,
    ) -> dict[str, Any]:
        """Read the target's EQ or dynamics master-enable state while stopped."""
        self._validate_module(module)
        return self._request(
            "inspect_processing", expected_project_name, target, module=module
        )

    def set_processing(
        self, expected_project_name: str, target: FairlightTarget,
        module: ProcessingModule, enabled: bool,
    ) -> dict[str, Any]:
        """Set a module's master enable and require its reopened readback."""
        self._validate_module(module)
        self._validate_boolean(enabled, "Processing enabled state")
        return self._request(
            "set_processing", expected_project_name, target,
            module=module, enabled=enabled,
        )

    def inspect_arm(
        self, expected_project_name: str, target: FairlightTarget
    ) -> dict[str, Any]:
        """Read one track's arm state; transport must remain stopped."""
        self._validate_track(target)
        return self._request("inspect_arm", expected_project_name, target)

    def set_arm(
        self, expected_project_name: str, target: FairlightTarget, armed: bool
    ) -> dict[str, Any]:
        """Set one track's arm state without starting transport or capture."""
        self._validate_track(target)
        self._validate_boolean(armed, "Record arm state")
        return self._request("set_arm", expected_project_name, target, armed=armed)

    def inspect_patch(
        self, expected_project_name: str, target: FairlightTarget
    ) -> dict[str, Any]:
        """Read a native-checked mono track's physical input patch."""
        self._validate_track(target)
        return self._request("inspect_patch", expected_project_name, target)

    def set_patch(
        self, expected_project_name: str, target: FairlightTarget,
        source_name: str, source_channel: int,
    ) -> dict[str, Any]:
        """Patch one physical source channel and require reopened readback."""
        self._validate_track(target)
        bounded_text(source_name, "Input source name", 128)
        if type(source_channel) is not int or not 1 <= source_channel <= 64:
            raise ValueError("Input source channel must be an integer from 1 to 64.")
        return self._request(
            "set_patch", expected_project_name, target,
            source_name=source_name, source_channel=source_channel,
        )

    @staticmethod
    def _validate_module(module: ProcessingModule) -> None:
        if module not in ("eq", "dynamics"):
            raise ValueError("Processing module must be eq or dynamics.")

    @staticmethod
    def _validate_boolean(value: bool, label: str) -> None:
        if type(value) is not bool:
            raise ValueError(f"{label} must be boolean.")

    @staticmethod
    def _validate_track(target: FairlightTarget) -> None:
        if not isinstance(target, FairlightTarget) or target.scope != "track":
            raise ValueError("Input patching and record arm require a track target.")

    def _request(
        self, operation: str, project_name: str, target: FairlightTarget,
        **arguments: str | bool | int,
    ) -> dict[str, Any]:
        project_name = bounded_text(project_name, "Expected project name", 128)
        if not isinstance(target, FairlightTarget):
            raise ValueError("Fairlight target must be a validated FairlightTarget.")
        if not self.enabled:
            raise WindowsRecordingError("OPT_IN_REQUIRED")
        if sys.platform != "win32":
            raise WindowsRecordingError("WINDOWS_REQUIRED")
        driver = Path(__file__).with_name("ResolveFairlightUI.ps1")
        executable = (
            Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
            / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        )
        payload = {
            "operation": operation,
            "expected_project_name": project_name,
            "target": target.as_dict(),
            **arguments,
        }
        mutation = operation in _MUTATIONS
        try:
            completed = subprocess.run(
                [
                    str(executable), "-NoLogo", "-NoProfile", "-NonInteractive",
                    "-STA", "-ExecutionPolicy", "Bypass", "-File", str(driver),
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
            raise WindowsRecordingError(
                "DRIVER_UNAVAILABLE", uncertain=mutation
            ) from error
        try:
            result = json.loads(completed.stdout.lstrip("\ufeff"))
        except (ValueError, TypeError, AttributeError) as error:
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
        self._validate_response(result, operation, project_name, target, arguments)
        return result

    @staticmethod
    def _validate_response(
        result: dict[str, Any], operation: str, project_name: str,
        target: FairlightTarget, arguments: dict[str, str | bool | int],
    ) -> None:
        mutation = operation in _MUTATIONS

        def invalid() -> NoReturn:
            raise WindowsRecordingError("UNVERIFIED_RESPONSE", uncertain=mutation)

        field = (
            "processing" if operation.endswith("processing")
            else "record_arm" if operation.endswith("arm") else "input_patch"
        )
        expected_fields = {
            "ok", "operation", "backend", "project_name", "window", "target",
            "transport", "recording_ready", "compatibility", field,
        }
        if mutation:
            expected_fields.add("changed")
        if (
            set(result) != expected_fields
            or result.get("operation") != operation
            or result.get("project_name") != project_name
            or result.get("backend") != "windows_fairlight"
            or result.get("recording_ready") is not None
            or result.get("compatibility") != {
                "status": "live_pending", "profile": "resolve-21.1-english"
            }
            or (mutation and type(result.get("changed")) is not bool)
        ):
            invalid()
        returned_target = result.get("target")
        if not isinstance(returned_target, dict) or set(returned_target) != {
            "timeline_name", "scope", "name", "index", "audio_track_count"
        }:
            invalid()
        assert isinstance(returned_target, dict)
        try:
            verified_target = FairlightTarget(**returned_target)
        except (TypeError, ValueError):
            invalid()
        if verified_target != target:
            invalid()
        window = result.get("window")
        if not isinstance(window, dict) or set(window) != {"pid", "handle"}:
            invalid()
        assert isinstance(window, dict)
        if any(type(window[key]) is not int or window[key] <= 0 for key in window):
            invalid()
        transport = result.get("transport")
        if not isinstance(transport, dict) or set(transport) != {
            "recording", "playing", "stopped", "verification"
        }:
            invalid()
        assert isinstance(transport, dict)
        if (
            transport.get("recording") is not False
            or transport.get("playing") is not False
            or transport.get("stopped") is not True
            or transport.get("verification") != "windows_uia_toggle_state"
        ):
            invalid()
        detail = result.get(field)
        if not isinstance(detail, dict):
            invalid()
        assert isinstance(detail, dict)
        if field == "processing":
            verification = (
                "windows_uia_reopened" if arguments["module"] == "eq"
                else "windows_visual_reopened"
            )
            if (
                set(detail) != {"module", "enabled", "verification"}
                or detail.get("module") != arguments["module"]
                or type(detail.get("enabled")) is not bool
                or detail.get("verification") != verification
                or (mutation and detail.get("enabled") is not arguments["enabled"])
            ):
                invalid()
        elif field == "record_arm":
            if (
                set(detail) != {"armed", "verification"}
                or type(detail.get("armed")) is not bool
                or detail.get("verification") != "windows_visual_reacquired"
                or (mutation and detail.get("armed") is not arguments["armed"])
            ):
                invalid()
        else:
            if (
                set(detail) != {
                    "patched", "source_name", "source_channel", "verification"
                }
                or type(detail.get("patched")) is not bool
                or detail.get("verification") != "windows_uia_patch_reopened"
            ):
                invalid()
            if detail.get("patched"):
                try:
                    bounded_text(
                        detail.get("source_name"), "Input source readback", 128
                    )
                except ValueError:
                    invalid()
                channel = detail.get("source_channel")
                if type(channel) is not int or not 1 <= channel <= 64:
                    invalid()
            elif (
                detail.get("source_name") is not None
                or detail.get("source_channel") is not None
            ):
                invalid()
            if mutation and (
                detail.get("patched") is not True
                or detail.get("source_name") != arguments["source_name"]
                or detail.get("source_channel") != arguments["source_channel"]
            ):
                invalid()
