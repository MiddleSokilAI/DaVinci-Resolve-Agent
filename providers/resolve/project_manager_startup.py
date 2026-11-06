"""Bounded official Resolve startup without opening history or creating projects."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ProjectStartupError(RuntimeError):
    """Startup failed before any project Open/Create dispatch."""

    def __init__(
        self,
        code: str,
        *,
        cause_code: str | None = None,
        startup_update_dispatch_uncertain: bool | None = None,
    ) -> None:
        self.code = code
        self.cause_code = cause_code
        self.startup_update_dispatch_uncertain = startup_update_dispatch_uncertain
        super().__init__(code)


def startup_update_observed(observation: dict[str, Any]) -> bool:
    """Route only the observed fixed update prompt; the driver revalidates it."""
    process = observation.get("process") or {}
    candidates = observation.get("window_candidates")
    if (
        observation.get("state") != "modal_blocked"
        or process.get("window_title") != "Project Manager"
        or process.get("project_name_hint") is not None
        or process.get("window_state") == "minimized"
        or not isinstance(candidates, list)
        or not 1 <= len(candidates) <= 64
    ):
        return False
    return any(
        isinstance(item, dict)
        and item.get("pid") == process.get("pid")
        and item.get("name") == "Software Update"
        and item.get("class_name") == "UiAutoUpdateDialogImp"
        and item.get("automation_id") == "UiAutoUpdateDialog"
        and item.get("control_type") == "ControlType.Window"
        and item.get("is_offscreen") is False
        for item in candidates
    )


def await_project_window(
    *,
    deadline: float,
    inspect: Callable[[], dict[str, Any]],
    skip_update: Callable[[dict[str, Any], float], dict[str, Any]],
    official_executable: Callable[[], str],
    process_token: Callable[[Any], Any],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    expected_project_name: str | None = None,
) -> dict[str, Any]:
    """Launch once per call, never switch an existing project or force focus."""
    update_skipped = False
    launched: subprocess.Popen[bytes] | None = None
    launched_pid: int | None = None
    observed_process: tuple[int, str, str] | None = None
    update_manager_handle: int | None = None
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise ProjectStartupError("PROJECT_MANAGER_STARTUP_TIMEOUT")
        observation = inspect()
        state, process = observation.get("state"), observation.get("process")
        if process is not None:
            token = process_token(process)
            if (
                launched_pid is not None
                and token[0] != launched_pid
                or observed_process is not None
                and token != observed_process
            ):
                raise ProjectStartupError("STARTUP_PROCESS_CHANGED")
            observed_process = token
            if update_manager_handle is not None and (
                process["window_handle"] != update_manager_handle
            ):
                raise ProjectStartupError("STARTUP_WINDOW_CHANGED")
        if state == "project_manager" and process is not None:
            return {
                "state": state,
                "process": dict(process),
                "startup_update_skipped": update_skipped,
            }
        if (
            state == "project_window"
            and process is not None
            and expected_project_name is not None
            and process.get("project_name_hint") == expected_project_name
            and observation.get("eligible") is True
            and process.get("window_state") != "minimized"
        ):
            return {
                "state": state,
                "process": dict(process),
                "startup_update_skipped": update_skipped,
            }
        if process is not None and startup_update_observed(observation):
            if update_manager_handle is None:
                remaining = deadline - clock()
                if remaining <= 0:
                    raise ProjectStartupError("PROJECT_MANAGER_STARTUP_TIMEOUT")
                update_manager_handle = process["window_handle"]
                try:
                    skipped = skip_update(process, min(20, remaining))
                    update_skipped = skipped["skipped"] is True
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    subprocess.TimeoutExpired,
                ) as error:
                    # Only Skip may have run. There is no creation intent
                    # or Create dispatch, even if the Skip ack was lost.
                    raise ProjectStartupError(
                        "PROJECT_STARTUP_UPDATE_BLOCKED",
                        cause_code=getattr(
                            error, "detail", "UPDATE_RESPONSE_UNVERIFIED"
                        ),
                        startup_update_dispatch_uncertain=getattr(
                            error, "uncertain", True
                        ),
                    ) from error
            sleep(min(0.2, max(0, deadline - clock())))
            continue
        if state == "resolve_not_running":
            if observed_process is not None or (
                launched is not None and launched.poll() is not None
            ):
                # Do not relaunch a failed/replaced process in this call.
                raise ProjectStartupError("RESOLVE_STARTUP_EXITED")
            if launched is None:
                executable = Path(official_executable())
                if not executable.is_file():
                    raise ProjectStartupError("RESOLVE_NOT_INSTALLED")
                try:
                    launched = subprocess.Popen(
                        [str(executable)],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except OSError as error:
                    raise ProjectStartupError("RESOLVE_STARTUP_FAILED") from error
                launched_pid = launched.pid
        elif state != "window_unavailable":
            # An existing project, a modal or ambiguous desktop is never
            # switched away, focused or treated as an empty Project Manager.
            raise ProjectStartupError("PROJECT_MANAGER_REQUIRED")
        sleep(min(0.2, remaining))
