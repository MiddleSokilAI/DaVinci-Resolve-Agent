"""On-demand connection and safe migration of the owned native bridge."""

from __future__ import annotations

import ctypes
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.marker_supervisor import (
    OperationBusy,
    Profile,
    Supervisor,
    _lock,
    _process_token,
    operation_lock,
    read_active,
)
from providers.resolve.preset_channel import PresetSDKChannel
from providers.resolve.project_open_service import (
    ProjectOpenError,
    ProjectOpenService,
    pending_project_open,
)
from providers.resolve.windows_bootstrap import (
    WindowsBootstrapAdapter,
    WindowsBootstrapError,
    _official_executable,
)


class AutomaticConnectionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code + ": no native mutation was resubmitted.")


def process_alive(process: dict[str, Any]) -> bool:
    """Compare the OS creation token, never just a reusable PID or old file."""
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (
        ctypes.POINTER(wintypes.FILETIME),
    ) * 4
    kernel.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, process["pid"])
    if not handle:
        return False
    try:
        fields = [wintypes.FILETIME() for _ in range(4)]
        code = wintypes.DWORD()
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in fields)):
            return False
        if (
            not kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            or code.value != 259
        ):
            return False
        created = (fields[0].dwHighDateTime << 32) + fields[0].dwLowDateTime
        return bool(str(created + 504911232000000000) == process["creation_time"])
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def wait_operation(profile: Profile, timeout: float = 30) -> Iterator[None]:
    """Retry ownership acquisition only, never the operation's body."""
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        while True:
            try:
                stack.enter_context(operation_lock(profile))
                break
            except OperationBusy:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        yield


class AutomaticConnection:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.adapter = WindowsBootstrapAdapter(enabled=profile.enabled)

    def _release_expired_read(self, active: dict[str, Any] | None) -> None:
        if active:
            root = Path(active["runtime"])
            if (root / "sdk-rpc/inbox").exists() and (
                root / "sdk-template.preset"
            ).is_file():
                PresetSDKChannel(root, active["session"], {})._recover_expired_read()

    def _last_project(self) -> dict[str, Any]:
        # Read validated host receipts rather than guessing a project by caption.
        pending_project_open(self.profile)
        active = read_active(self.profile)
        preferred = (active or {}).get("last_native_project") or {}
        candidates: list[tuple[float, dict[str, Any]]] = []
        folder = self.profile.state_directory / "project-open-receipts"
        for path in folder.glob("*.json"):
            if path.is_symlink() or path.stat().st_size > 65536:
                raise AutomaticConnectionError("INVALID_PROJECT_HISTORY")
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("status") == "completed":
                args = value["arguments"]
                if preferred and args["expected_project_id"] != preferred.get("id"):
                    continue
                candidates.append((path.stat().st_mtime, args))
        if not candidates:
            raise AutomaticConnectionError("PROJECT_INTENT_UNAVAILABLE")
        return max(candidates, key=lambda item: item[0])[1]

    def _open(self, process: dict[str, Any], timeout: float) -> None:
        target = self._last_project()
        digest = hashlib.sha256(
            json.dumps(
                {
                    "process": [process["pid"], process["creation_time"]],
                    "target": target,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:32]
        # A rejected preflight never dispatched Open. Only those explicitly
        # transient failures get a new, linked attempt; uncertain opens keep key.
        transient = {
            "PROJECT_MANAGER_NOT_FOREGROUND",
            "PROJECT_MANAGER_ACTIVATION_DENIED",
            "USER_INPUT_CHANGED",
            "FOCUS_LOST",
            "PROJECT_MANAGER_NOT_READY",
        }
        for attempt in range(3):
            key = f"auto-open-{digest}-{attempt}"
            try:
                ProjectOpenService(self.profile).open_existing(
                    **target,
                    idempotency_key=key,
                    confirm=True,
                    timeout_seconds=max(1, min(timeout, 600)),
                )
                return
            except ProjectOpenError as error:
                receipt = (
                    self.profile.state_directory
                    / "project-open-receipts"
                    / (hashlib.sha256(key.encode()).hexdigest() + ".json")
                )
                record = json.loads(receipt.read_text()) if receipt.is_file() else {}
                if (
                    error.uncertain
                    or record.get("status") != "rejected"
                    or record.get("error") not in transient
                ):
                    raise
                time.sleep(0.2)
        raise AutomaticConnectionError("PROJECT_PREFLIGHT_BUSY")

    def ensure(
        self,
        timeout: float | None = None,
        *,
        require_sdk_channel: bool = True,
    ) -> dict[str, Any]:
        if not self.profile.enabled:
            raise AutomaticConnectionError("AUTOMATIC_CONNECTION_DISABLED")
        limit = self.profile.startup_timeout_seconds if timeout is None else timeout
        deadline = time.monotonic() + limit
        launched = False
        while time.monotonic() < deadline:
            active = read_active(self.profile)
            if (
                active
                and active["phase"] == "connected"
                and process_alive(active["process"])
            ):
                root = Path(active["runtime"])
                if (root / "sdk-template.preset").is_file() and not any(
                    root.glob("marker-exit-*.drp")
                ):
                    return active
                metadata = json.loads((root / "session.json").read_text())
                if metadata.get("sdk_proxy") and not any(
                    root.glob("marker-exit-*.drp")
                ):
                    if not require_sdk_channel:
                        return active
                    raise AutomaticConnectionError("SDK_CHANNEL_UNAVAILABLE")
            try:
                with _lock(self.profile, "connection.lock"):
                    try:
                        host = self.adapter.inspect()
                    except WindowsBootstrapError as error:
                        if error.uncertain or error.code not in {
                            "DRIVER_FAILURE", "TIMEOUT", "DRIVER_IO_FAILED"
                        }:
                            raise
                        # Only inspection repeats; no launch or native/menu
                        # dispatch has occurred in this attempt.
                        host = {"state": "inspection_unavailable"}
                    if host["state"] == "resolve_not_running":
                        if not launched:
                            executable = Path(_official_executable())
                            if not executable.is_file():
                                raise AutomaticConnectionError("RESOLVE_NOT_INSTALLED")
                            subprocess.Popen(
                                [str(executable)],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(
                                    subprocess, "CREATE_NO_WINDOW", 0
                                ),
                            )
                            launched = True
                    elif host["state"] == "project_manager":
                        with wait_operation(self.profile, min(10, limit)):
                            self._release_expired_read(active)
                        self._open(host["process"], deadline - time.monotonic())
                    elif host["state"] == "project_window" and host["eligible"]:
                        if (
                            active
                            and _process_token(active["process"])
                            == _process_token(host["process"])
                            and (
                                active["phase"] == "connected"
                                or any(
                                    Path(active["runtime"]).glob("marker-exit-*.drp")
                                )
                            )
                        ):
                            with wait_operation(self.profile, min(10, limit)):
                                fresh = read_active(self.profile)
                                if (
                                    fresh
                                    and fresh["generation"] == active["generation"]
                                ):
                                    supervisor = Supervisor(self.profile)
                                    self._release_expired_read(active)
                                    supervisor._audit_rotation()
                                    root = Path(active["runtime"])
                                    if not any(root.glob("marker-exit-*.drp")):
                                        LuaSnapshotClient(root).request("stop", 10)
                                        stopped = time.monotonic() + 10
                                        while not any(root.glob("marker-exit-*.drp")):
                                            if time.monotonic() >= stopped:
                                                raise AutomaticConnectionError(
                                                    "NATIVE_STOP_UNVERIFIED"
                                                )
                                            time.sleep(0.05)
                                    supervisor._new(host["process"], root)
                        else:
                            Supervisor(self.profile).tick()
                    elif host["state"] in {"process_ambiguous", "desktop_unavailable"}:
                        raise AutomaticConnectionError(str(host["state"]).upper())
            except OperationBusy:
                pass
            time.sleep(0.2)
        raise AutomaticConnectionError("AUTOMATIC_CONNECTION_TIMEOUT")
