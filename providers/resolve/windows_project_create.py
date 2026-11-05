"""Private fixed UIA creation driver; no user-controlled selectors or input APIs."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from providers.resolve.windows_bootstrap import _unique_object, _validate_process


class WindowsProjectCreateError(RuntimeError):
    """Retain a fixed driver's refusal and whether Invoke may have run."""

    def __init__(self, detail: str, *, uncertain: bool) -> None:
        self.code = "PROJECT_CREATE_UI_FAILED"
        self.uncertain = uncertain
        self.detail = detail
        super().__init__(f"Fixed project creation adapter: {detail}")


class WindowsProjectCreateAdapter:
    """Invoke one fixed button and set one fixed name field, without focus."""

    def request(
        self,
        operation: str,
        process: dict[str, Any],
        *,
        dialog: dict[str, Any] | None = None,
        name: str | None = None,
        timeout_seconds: float = 20,
    ) -> dict[str, Any]:
        if sys.platform != "win32":
            raise RuntimeError("WINDOWS_UIA_REQUIRED")
        if operation not in {
            "begin", "inspect_dialog", "create", "skip_startup_update"
        }:
            raise ValueError("Unknown fixed creation operation.")
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 600
        ):
            raise ValueError("Invalid creation driver timeout.")
        payload: dict[str, Any] = {
            "operation": operation,
            "process": _validate_process(process),
        }
        if process["window_title"] != "Project Manager" or not process["window_handle"]:
            raise ValueError("A bound Project Manager window is required.")
        if operation == "create":
            payload.update(dialog=dialog, name=name)
        executable = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / (
            "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        response = subprocess.run(
            [
                str(executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(Path(__file__).with_name("ResolveProjectCreateUI.ps1")),
            ],
            input=json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
            capture_output=True,
            timeout=min(20, timeout_seconds),
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if len(response.stdout) > 8192:
            raise RuntimeError("CREATE_UI_RESPONSE_BOUND")
        result = json.loads(response.stdout, object_pairs_hook=_unique_object)
        if (
            response.returncode != 0
            and isinstance(result, dict)
            and set(result) == {"ok", "code", "detail", "dispatch_started"}
            and result["ok"] is False
            and result["code"] == "PROJECT_CREATE_UI_FAILED"
            and isinstance(result["detail"], str)
            and 1 <= len(result["detail"]) <= 1024
            and type(result["dispatch_started"]) is bool
        ):
            raise WindowsProjectCreateError(
                result["detail"], uncertain=result["dispatch_started"]
            )
        if (
            response.returncode != 0
            or not isinstance(result, dict)
            or result.get("ok") is not True
            or result.get("operation") != operation
            or set(result) - {"ok", "operation", "dialog", "skipped"}
        ):
            raise RuntimeError("CREATE_UI_RESPONSE_UNVERIFIED")
        if operation == "skip_startup_update":
            if set(result) != {"ok", "operation", "skipped"} or (
                type(result["skipped"]) is not bool
            ):
                raise RuntimeError("STARTUP_UPDATE_RESPONSE_UNVERIFIED")
        elif "skipped" in result:
            raise RuntimeError("CREATE_UI_RESPONSE_UNVERIFIED")
        if operation != "begin" and result.get("dialog") is not None:
            token = result["dialog"]
            if (
                not isinstance(token, dict)
                or set(token) != {"handle", "runtime_id"}
                or type(token["handle"]) is not int
                or token["handle"] <= 0
                or not isinstance(token["runtime_id"], list)
                or not 1 <= len(token["runtime_id"]) <= 32
                or any(type(item) is not int for item in token["runtime_id"])
            ):
                raise RuntimeError("CREATE_DIALOG_TOKEN_INVALID")
        return result
