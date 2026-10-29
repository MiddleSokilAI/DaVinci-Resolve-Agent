"""Opt-in invocation of the fixed managed Resolve marker menu entry.

The supervisor owns dispatch receipts and session rotation. This adapter only
reports UI dispatch evidence; a fresh native response must establish connection.
It accepts no script text, menu selectors, coordinates, or key sequences.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import ntpath
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from providers.resolve.lua_transport import lua_string
from providers.resolve.marker_transport import validate_marker_metadata
from providers.resolve.startup_diagnostics import (
    MAX_METADATA_BYTES,
    MAX_SCRIPT_BYTES,
    MENU_FILE,
    MENU_HEADER,
)

_PROCESS_FIELDS = {
    "pid",
    "creation_time",
    "executable_path",
    "window_handle",
    "window_title",
    "project_name_hint",
    "window_state",
}
_COMMON_FIELDS = {
    "ok",
    "operation",
    "backend",
    "state",
    "eligible",
    "process",
    "project_state",
    "native_connection_verified",
    "dispatch_started",
}
_INSPECTION_STATES = {
    "resolve_not_running",
    "window_unavailable",
    "project_manager",
    "project_window",
    "modal_blocked",
    "desktop_unavailable",
    "process_ambiguous",
}
_MENU_PATH = ["Workspace", "Scripts", "Utility", "Resolve Agent Marker"]
_PROJECT_PREFIX = "DaVinci Resolve - "


class WindowsBootstrapError(RuntimeError):
    """A fixed bootstrap operation failed before or possibly after dispatch."""

    def __init__(self, code: str, *, uncertain: bool = False) -> None:
        self.code = code
        self.uncertain = uncertain
        self.dispatch_started = uncertain
        self.phase = "after_dispatch" if uncertain else "before_dispatch"
        super().__init__(
            f"Windows bootstrap adapter: {code}. "
            + (
                "Dispatch may have started; inspect native evidence, do not reinvoke."
                if uncertain
                else "The managed marker script was not dispatched."
            )
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous JSON objects at the subprocess and metadata boundaries."""
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key.")
        value[key] = item
    return value


def _known_program_files() -> str:
    """Read Windows' 64-bit known folder; portable tests may inject this seam."""
    if os.name != "nt":
        # The Windows adapters remain unavailable outside Windows. This fixed
        # fixture value keeps their pure response boundaries portable.
        return r"C:\Program Files"

    class FolderId(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        ]

    folder = FolderId.from_buffer_copy(
        UUID("6d809377-6af0-444b-8957-a3773f02200e").bytes_le
    )
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    task = ctypes.WinDLL("ole32", use_last_error=True)
    get_folder = shell.SHGetKnownFolderPath
    get_folder.argtypes = [
        ctypes.POINTER(FolderId),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_folder.restype = ctypes.c_long
    release = task.CoTaskMemFree
    release.argtypes = [ctypes.c_void_p]
    release.restype = None
    allocated = ctypes.c_void_p()
    try:
        result = get_folder(ctypes.byref(folder), 0, None, ctypes.byref(allocated))
        if result != 0 or not allocated.value:
            raise ValueError("Windows Program Files known folder is unavailable.")
        program_files = ctypes.wstring_at(allocated.value)
        if not program_files or not ntpath.isabs(program_files):
            raise ValueError("Invalid Windows Program Files known folder.")
        return ntpath.normpath(program_files)
    finally:
        if allocated.value:
            release(allocated)


def _official_executable() -> str:
    """Select the OS-defined installed application path, never caller data."""
    return ntpath.join(
        _known_program_files(), "Blackmagic Design", "DaVinci Resolve", "Resolve.exe"
    )


def _project_hint(title: str) -> str | None:
    """Extract a routing hint only; captions never prove native project identity."""
    if not title.startswith(_PROJECT_PREFIX):
        return None
    name = title[len(_PROJECT_PREFIX) :]
    if (
        not name
        or len(name) > 256
        or name != name.strip()
        or name == "Untitled Project"
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        return None
    return name


def _validate_process(value: Any, *, eligible: bool = False) -> dict[str, Any]:
    """Validate the process generation and UI hint without inferring liveness."""
    if not isinstance(value, dict) or set(value) != _PROCESS_FIELDS:
        raise ValueError("Invalid bootstrap process evidence.")
    creation = value["creation_time"]
    executable = value["executable_path"]
    title = value["window_title"]
    if (
        type(value["pid"]) is not int
        or not 0 < value["pid"] <= 0xFFFFFFFF
        or not isinstance(creation, str)
        or not re.fullmatch(r"[1-9][0-9]{0,18}", creation)
        or int(creation) > 3155378975999999999
        or not isinstance(executable, str)
        or len(executable) > 1024
        or ntpath.normcase(ntpath.normpath(executable))
        != ntpath.normcase(ntpath.normpath(_official_executable()))
        or type(value["window_handle"]) is not int
        or not 0 <= value["window_handle"] <= 0x7FFFFFFFFFFFFFFF
        or not isinstance(title, str)
        or len(title) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in title)
        or not isinstance(value["window_state"], str)
        or value["window_state"] not in {"normal", "maximized", "minimized"}
        or value["project_name_hint"] != _project_hint(title)
        or eligible
        and (value["window_handle"] == 0 or value["project_name_hint"] is None)
    ):
        raise ValueError("Invalid bootstrap process evidence.")
    return dict(value)


def _read_owned(path: Path, limit: int) -> bytes:
    """Reject linked evidence and bound each read, including the fixed menu file."""
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (
            candidate.exists()
            and getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Linked bootstrap paths are not accepted.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Bootstrap evidence exceeds its size bound.")
    return data


def _normalized_script(data: bytes) -> str:
    """Compare source text across the generated Windows newline convention."""
    return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def _managed_script_hash(root: Path) -> str:
    """Require matching immutable session source at the one fixed menu path."""
    root = root.absolute()
    metadata = json.loads(
        _read_owned(root / "session.json", MAX_METADATA_BYTES).decode("utf-8-sig"),
        object_pairs_hook=_unique_object,
    )
    if not isinstance(metadata, dict):
        raise ValueError("Invalid marker metadata.")
    validate_marker_metadata(metadata)
    for name in ("marker-session-consumed.drp", "stopped.json"):
        path = root / name
        if path.exists() or path.is_symlink():
            raise WindowsBootstrapError("SESSION_ALREADY_CONSUMED")
    source = _normalized_script(
        _read_owned(root / "marker-bridge.lua", MAX_SCRIPT_BYTES)
    )
    if (
        not source.startswith("-- Generated self-contained Free marker bridge.")
        or source.count("local root=" + lua_string(root.resolve().as_posix())) != 1
        or source.count("local session=" + lua_string(metadata["session"])) != 1
    ):
        raise ValueError("Prepared marker source does not match its runtime.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise WindowsBootstrapError("APPDATA_UNAVAILABLE")
    installed = _read_owned(
        Path(appdata)
        / "Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts"
        / "Utility"
        / MENU_FILE,
        MAX_SCRIPT_BYTES,
    )
    if _normalized_script(installed) != MENU_HEADER + source:
        raise WindowsBootstrapError("MANAGED_SCRIPT_MISMATCH")
    return hashlib.sha256(installed).hexdigest()


@dataclass(frozen=True)
class WindowsBootstrapAdapter:
    """Inspect or invoke the shipped fixed UI driver after explicit host opt-in.

    ``inspect()`` returns ``state``, ``eligible`` and a ``process`` dictionary.
    Pass that dictionary unchanged to ``invoke_managed_marker(root, process)``.
    Its PID, UTC creation ticks, executable and UI window are checked again by
    the driver. The supervisor must persist dispatch intent before invocation.
    """

    enabled: bool = False
    timeout_seconds: float = 45

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("Windows bootstrap opt-in must be boolean.")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 120
        ):
            raise ValueError(
                "Bootstrap timeout must be finite and at most 120 seconds."
            )

    def capabilities(self) -> dict[str, Any]:
        """Separate configured availability from dispatch and native acceptance."""
        return {
            "backend": "windows_bootstrap",
            "enabled": self.enabled,
            "platform_supported": sys.platform == "win32",
            "available": self.enabled and sys.platform == "win32",
            "operations": ["inspect", "invoke_managed_marker"],
            "native_connection_verified": False,
        }

    def _available(self) -> None:
        if not self.enabled:
            raise WindowsBootstrapError("OPT_IN_REQUIRED")
        if sys.platform != "win32":
            raise WindowsBootstrapError("WINDOWS_REQUIRED")

    def inspect(self) -> dict[str, Any]:
        """Read process/UI eligibility without focusing or changing the desktop."""
        self._available()
        return self._request({"operation": "inspect"})

    def invoke_managed_marker(
        self,
        root: Path,
        process: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch one verified managed menu entry; never claim native connection.

        A timeout or malformed response is uncertain: the supervisor must inspect
        native evidence instead of invoking again. This method does not retry.
        """
        self._available()
        process = _validate_process(process, eligible=True)
        try:
            digest = _managed_script_hash(root)
        except WindowsBootstrapError:
            raise
        except (OSError, ValueError, TypeError, RecursionError) as error:
            raise WindowsBootstrapError("MANAGED_SCRIPT_UNVERIFIED") from error
        return self._request(
            {
                "operation": "invoke_managed_marker",
                "expected_process": process,
                "expected_sha256": digest,
            }
        )

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send bounded data to a fixed subprocess, never a shell expression."""
        dispatch = payload["operation"] == "invoke_managed_marker"
        executable = (
            Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell/v1.0/powershell.exe"
        )
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
                    str(Path(__file__).with_name("ResolveBootstrapUI.ps1")),
                ],
                input=json.dumps(payload, ensure_ascii=False, allow_nan=False),
                encoding="utf-8",
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as error:
            raise WindowsBootstrapError("TIMEOUT", uncertain=dispatch) from error
        except FileNotFoundError as error:
            raise WindowsBootstrapError("DRIVER_UNAVAILABLE") from error
        except OSError as error:
            raise WindowsBootstrapError(
                "DRIVER_IO_FAILED", uncertain=dispatch
            ) from error
        except UnicodeError as error:
            raise WindowsBootstrapError(
                "INVALID_RESPONSE", uncertain=dispatch
            ) from error
        try:
            if not isinstance(completed.stdout, str) or len(completed.stdout) > 65536:
                raise ValueError("Invalid response size.")
            result = json.loads(
                completed.stdout.lstrip("\ufeff"),
                object_pairs_hook=_unique_object,
            )
            if not isinstance(result, dict):
                raise ValueError("Invalid response object.")
        except (TypeError, ValueError, RecursionError) as error:
            raise WindowsBootstrapError(
                "INVALID_RESPONSE", uncertain=dispatch
            ) from error
        if result.get("ok") is False:
            detail = result.get("error")
            if (
                set(result) != {"ok", "error"}
                or not isinstance(detail, dict)
                or set(detail) != {"code", "uncertain", "phase"}
                or not isinstance(detail["code"], str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", detail["code"])
                or type(detail["uncertain"]) is not bool
                or detail["phase"]
                != ("after_dispatch" if detail["uncertain"] else "before_dispatch")
                or not dispatch
                and detail["uncertain"]
            ):
                raise WindowsBootstrapError("INVALID_RESPONSE", uncertain=dispatch)
            raise WindowsBootstrapError(detail["code"], uncertain=detail["uncertain"])
        if completed.returncode != 0 or result.get("ok") is not True:
            raise WindowsBootstrapError("DRIVER_FAILED", uncertain=dispatch)
        try:
            self._validate_response(result, payload)
        except (ValueError, TypeError, KeyError) as error:
            raise WindowsBootstrapError(
                "UNVERIFIED_RESPONSE", uncertain=dispatch
            ) from error
        return result

    @staticmethod
    def _validate_response(result: dict[str, Any], payload: dict[str, Any]) -> None:
        """Check the exact evidence schema and keep UI separate from native state."""
        dispatch = payload["operation"] == "invoke_managed_marker"
        expected_fields = _COMMON_FIELDS | (
            {"script_sha256", "menu_path", "minimized_restored"}
            if dispatch
            else {"window_candidates", "window_candidates_truncated"}
        )
        if (
            set(result) != expected_fields
            or result["operation"] != payload["operation"]
            or result["backend"] != "windows_bootstrap"
            or result["project_state"] != "unverified"
            or result["native_connection_verified"] is not False
            or type(result["eligible"]) is not bool
            or result["dispatch_started"] is not dispatch
        ):
            raise ValueError("Unexpected bootstrap response.")
        state = result["state"]
        if dispatch:
            process = _validate_process(result["process"], eligible=True)
            expected = payload["expected_process"]
            if (
                state != "dispatch_requested"
                or result["eligible"] is not True
                or any(
                    process[key] != expected[key]
                    for key in _PROCESS_FIELDS
                    if key != "window_state"
                )
                or result["script_sha256"] != payload["expected_sha256"]
                or result["menu_path"]
                not in (
                    _MENU_PATH,
                    ["Workspace", "Scripts", "Resolve Agent Marker"],
                )
                or type(result["minimized_restored"]) is not bool
                or result["minimized_restored"]
                != (expected["window_state"] == "minimized")
            ):
                raise ValueError("Dispatch identity changed.")
        else:
            candidates = result["window_candidates"]
            if (
                not isinstance(candidates, list)
                or len(candidates) > 16
                or type(result["window_candidates_truncated"]) is not bool
            ):
                raise ValueError("Invalid window diagnostics.")
            for candidate in candidates:
                if (
                    not isinstance(candidate, dict)
                    or set(candidate)
                    != {
                        "pid",
                        "class_name",
                        "automation_id",
                        "name",
                        "native_handle",
                        "control_type",
                        "is_offscreen",
                    }
                    or type(candidate["pid"]) is not int
                    or not 0 < candidate["pid"] <= 0xFFFFFFFF
                    or type(candidate["native_handle"]) is not int
                    or not 0 <= candidate["native_handle"] <= 0x7FFFFFFFFFFFFFFF
                    or type(candidate["is_offscreen"]) is not bool
                    or any(
                        not isinstance(candidate[key], str)
                        or len(candidate[key]) > bound
                        for key, bound in (
                            ("class_name", 128),
                            ("automation_id", 256),
                            ("name", 512),
                            ("control_type", 64),
                        )
                    )
                ):
                    raise ValueError("Invalid window diagnostics.")
            if state not in _INSPECTION_STATES or result["eligible"] != (
                state == "project_window"
            ):
                raise ValueError("Invalid inspection state.")
            if result["process"] is not None:
                _validate_process(result["process"], eligible=result["eligible"])
            elif state not in {
                "resolve_not_running",
                "desktop_unavailable",
                "process_ambiguous",
            }:
                raise ValueError("Missing process evidence.")
