"""Windowless watcher launch and fixed Startup registration boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import agent.resolve_watch as module
from providers.resolve.marker_supervisor import Profile


def make_profile(tmp_path: Path) -> Profile:
    profile = tmp_path / "watch profile.json"
    base = tmp_path / "runtime"
    base.mkdir()
    profile.write_text(json.dumps({"schema": 1, "enabled": True,
        "runtime_base": str(base), "media_roots": []}), encoding="utf-8")
    return Profile.load(profile)


def test_startup_registration_runs_only_fixed_installed_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = make_profile(tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"ok":true}', '')

    monkeypatch.setattr(subprocess, "run", run)
    assert module.startup_entry(profile, "install") == {"ok": True}
    command, options = calls[0]
    assert command[-2] == "-File"
    policy_index = command.index("-ExecutionPolicy")
    assert command[policy_index + 1] == "Bypass"
    assert "Set-ExecutionPolicy" not in command
    assert Path(command[-1]).name == "ResolveAutostartEntry.ps1"
    assert "-Command" not in command
    payload = json.loads(options["input"])
    assert payload["profile_path"] == str(profile.path)
    assert payload["operation"] == "install"
    assert Path(payload["pythonw_executable"]).name == "pythonw.exe"
    assert options["timeout"] == 20
    with pytest.raises(ValueError, match="Unknown"):
        module.startup_entry(profile, "run arbitrary command")
    assert len(calls) == 1


def test_duplicate_watcher_does_not_start_an_orphan_sdk_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from providers.resolve.marker_supervisor import _lock
    from providers.resolve.sdk_worker import SDKWorker

    profile = make_profile(tmp_path)
    monkeypatch.setattr(SDKWorker, "run", lambda *args: pytest.fail("duplicate worker"))
    with _lock(profile, "automatic-watcher.lock"):
        module.run_automatic_watch(profile)


def test_hidden_start_has_no_shell_or_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = make_profile(tmp_path)
    python = tmp_path / "python.exe"
    (tmp_path / "pythonw.exe").touch()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(python))
    calls: list[tuple[list[str], dict[str, Any]]] = []

    class Process:
        pid = 123

    def popen(command: list[str], **kwargs: Any) -> Process:
        calls.append((command, kwargs))
        return Process()

    monkeypatch.setattr(subprocess, "Popen", popen)
    result = module.start_hidden(profile)
    assert result["native_connection_verified"] is False
    command, options = calls[0]
    assert command == [str(tmp_path / "pythonw.exe"), "-m", "agent.resolve_watch",
                       "--profile", str(profile.path)]
    assert not options.get("shell")
    assert options["stdin"] == subprocess.DEVNULL
    assert options["cwd"] == profile.state_directory


def test_script_startup_failure_reports_invocation_instead_of_json_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = make_profile(tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")

    def failed(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "script was not started")

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="no valid result.*exit 1"):
        module.startup_entry(profile, "status")


def test_cli_open_existing_forwards_exact_target_and_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = make_profile(tmp_path)
    calls: list[dict[str, Any]] = []

    class Opener:
        def __init__(self, received: Profile) -> None:
            assert received.path == profile.path

        def open_existing(self, **arguments: Any) -> dict[str, Any]:
            calls.append(arguments)
            return {"context_verified": True}

    monkeypatch.setattr(module, "ProjectOpenService", Opener)
    monkeypatch.setattr(sys, "argv", [
        "watch", "--profile", str(profile.path), "--open-project", "Exact Project",
        "--expected-project-id", "project-uuid", "--database-name", "Local Library",
        "--idempotency-key", "open-once", "--confirm",
    ])
    module.main()
    assert json.loads(capsys.readouterr().out) == {"context_verified": True}
    assert calls == [{
        "project_name": "Exact Project", "expected_project_id": "project-uuid",
        "expected_database": {"DbType": "Disk", "DbName": "Local Library"},
        "folder_path": [], "idempotency_key": "open-once", "confirm": True,
        "timeout_seconds": 120,
    }]


@pytest.mark.parametrize("arguments", [
    ["--open-project", "Exact Project"],
    ["--expected-project-id", "project-uuid"], ["--confirm"],
    ["--project-open-timeout", "90"],
])
def test_cli_refuses_incomplete_or_unscoped_open_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    profile = make_profile(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "watch", "--profile", str(profile.path), *arguments,
    ])
    with pytest.raises(SystemExit) as exit_info:
        module.main()
    assert exit_info.value.code == 2
