"""Shared cold-start boundaries using fake process/UI evidence only."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from providers.resolve.project_manager_startup import (
    ProjectStartupError,
    await_project_window,
)


@pytest.mark.parametrize("finish", ["manager", "target", "wrong", "modal", "timeout"])
def test_startup_launches_once_and_never_opens_or_creates_a_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    finish: str,
) -> None:
    executable = tmp_path / "Resolve.exe"
    executable.touch()
    now = [0.0]
    process = {
        "pid": 124,
        "creation_time": "638999000000000001",
        "executable_path": str(executable),
        "window_handle": 456,
        "window_title": "Project Manager",
        "window_state": "normal",
        "project_name_hint": None,
    }
    launches: list[dict[str, Any]] = []
    observations = ["resolve_not_running", "window_unavailable"]
    last = {
        "manager": "project_manager",
        "target": "project_window",
        "wrong": "project_window",
        "modal": "modal_blocked",
        "timeout": "window_unavailable",
    }[finish]

    def launch(*args: Any, **kwargs: Any) -> Any:
        launches.append({"args": args, **kwargs})
        return SimpleNamespace(pid=124, poll=lambda: None)

    def inspect() -> dict[str, Any]:
        state = observations.pop(0) if observations else last
        if state == "project_window":
            process["project_name_hint"] = "Target" if finish == "target" else "Other"
        return {
            "state": state,
            "eligible": state == "project_window",
            "process": None if state == "resolve_not_running" else dict(process),
        }

    def sleep(duration: float) -> None:
        now[0] += duration

    monkeypatch.setattr(subprocess, "Popen", launch)
    arguments: dict[str, Any] = {
        "deadline": 1.0,
        "inspect": inspect,
        "skip_update": lambda *args: pytest.fail("Unrelated dialog action"),
        "official_executable": lambda: str(executable),
        "process_token": lambda p: (p["pid"], p["creation_time"], p["executable_path"]),
        "clock": lambda: now[0],
        "sleep": sleep,
        "expected_project_name": "Target",
    }
    if finish in {"manager", "target"}:
        result = await_project_window(**arguments)
        assert result["state"] == last
        assert result["process"]["pid"] == 124
        assert "native_connection_verified" not in result
    else:
        code = "STARTUP_TIMEOUT" if finish == "timeout" else "PROJECT_MANAGER_REQUIRED"
        with pytest.raises(ProjectStartupError, match=code):
            await_project_window(**arguments)
    assert len(launches) == 1
    assert launches[0]["args"] == ([str(executable)],)
    assert launches[0]["shell"] is False
    assert launches[0]["stdin"] == launches[0]["stdout"] == subprocess.DEVNULL
