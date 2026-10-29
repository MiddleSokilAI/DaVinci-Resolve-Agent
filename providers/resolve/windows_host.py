"""Read-only Windows process evidence, separate from native Resolve identity.

Only an explicit caller should invoke this probe. A process or window caption
does not establish a selected project, a native connection, or recording readiness.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_SOURCE = "windows_process_metadata"
_TIMEOUT_SECONDS = 10
_MAX_PROCESSES = 8
_MAX_TITLE_LENGTH = 512
_MAX_PATH_LENGTH = 1024
_MAX_OUTPUT_LENGTH = 65536
_PROCESS_FIELDS = {
    "pid", "title", "main_window_handle", "responding", "executable_path"
}
_DRIVER_REASON_CODES = {
    "process_enumeration_unavailable",
    "process_metadata_unavailable",
    "process_limit_exceeded",
    "metadata_bounds_exceeded",
    "invalid_driver_invocation",
}


def _unavailable(reason_code: str) -> dict[str, Any]:
    """Keep missing evidence distinct from a successful empty enumeration."""
    return {
        "status": "unavailable",
        "reason_code": reason_code,
        "source": _SOURCE,
        "processes": [],
        "running": None,
        "project_state": "unverified",
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys instead of silently accepting the last value."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate metadata key.")
        result[key] = value
    return result


def _validated_processes(value: Any) -> list[dict[str, Any]] | None:
    """Accept only the bounded, fixed process-metadata schema."""
    if not isinstance(value, list) or len(value) > _MAX_PROCESSES:
        return None
    processes: list[dict[str, Any]] = []
    process_ids: set[int] = set()
    for process in value:
        if not isinstance(process, dict) or set(process) != _PROCESS_FIELDS:
            return None
        pid = process["pid"]
        title = process["title"]
        handle = process["main_window_handle"]
        responding = process["responding"]
        executable_path = process["executable_path"]
        if (
            type(pid) is not int
            or pid <= 0
            or pid in process_ids
            or not isinstance(title, str)
            or len(title) > _MAX_TITLE_LENGTH
            or type(handle) is not int
            or handle < 0
            or type(responding) is not bool
            or (
                executable_path is not None
                and (
                    not isinstance(executable_path, str)
                    or len(executable_path) > _MAX_PATH_LENGTH
                )
            )
        ):
            return None
        process_ids.add(pid)
        processes.append(dict(process))
    return processes


def inspect_windows_host() -> dict[str, Any]:
    """Observe bounded Windows process metadata without UI or native SDK access.

    The bundled PowerShell driver accepts no parameters or caller-supplied code.
    Unavailable metadata never means Resolve is stopped. Even successful evidence
    leaves native project identity and readiness explicitly unverified.
    """
    if sys.platform != "win32":
        return _unavailable("windows_required")
    driver = Path(__file__).with_name("ResolveHostMetadata.ps1")
    try:
        driver_available = driver.is_file()
    except OSError:
        driver_available = False
    if not driver_available:
        return _unavailable("bundled_driver_unavailable")
    executable = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    try:
        completed = subprocess.run(
            [
                str(executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(driver),
            ],
            input="",
            encoding="utf-8",
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return _unavailable("metadata_probe_timeout")
    except OSError:
        return _unavailable("powershell_unavailable")
    except UnicodeError:
        return _unavailable("invalid_metadata_response")
    if completed.returncode != 0:
        return _unavailable("metadata_probe_failed")
    if (
        not isinstance(completed.stdout, str)
        or len(completed.stdout) > _MAX_OUTPUT_LENGTH
    ):
        return _unavailable("invalid_metadata_response")
    try:
        result = json.loads(
            completed.stdout.lstrip("\ufeff"), object_pairs_hook=_unique_object
        )
    except (TypeError, ValueError, RecursionError):
        return _unavailable("invalid_metadata_response")
    if not isinstance(result, dict):
        return _unavailable("invalid_metadata_response")
    if result.get("status") == "unavailable":
        reason_code = result.get("reason_code")
        if (
            set(result) == {"status", "reason_code"}
            and isinstance(reason_code, str)
            and reason_code in _DRIVER_REASON_CODES
        ):
            return _unavailable(reason_code)
        return _unavailable("invalid_metadata_response")
    if set(result) != {"status", "processes"} or result["status"] != "observed":
        return _unavailable("invalid_metadata_response")
    processes = _validated_processes(result["processes"])
    if processes is None:
        return _unavailable("invalid_metadata_response")
    if len(processes) > 1:
        window_hint = "multiple_processes"
    elif (
        processes
        and processes[0]["main_window_handle"] != 0
        and processes[0]["title"] == "Project Manager"
    ):
        window_hint = "project_manager"
    elif (
        processes
        and processes[0]["main_window_handle"] != 0
        and processes[0]["title"]
    ):
        window_hint = "named_window"
    else:
        window_hint = "no_window"
    return {
        "status": "observed",
        "source": _SOURCE,
        "processes": processes,
        "running": bool(processes),
        "window_hint": window_hint,
        "project_state": "unverified",
    }
