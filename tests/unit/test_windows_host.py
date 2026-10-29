"""Exercise synthetic metadata only; never inspect the Windows desktop."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from providers.resolve import windows_host as module


def process(**changes: Any) -> dict[str, Any]:
    """Create bounded fake process evidence with Unicode preserved."""
    return {
        "pid": 123,
        "title": "DaVinci Resolve — Тестовий проєкт",
        "main_window_handle": 456,
        "responding": True,
        "executable_path": r"C:\Program Files\Blackmagic Design\Resolve\Resolve.exe",
        **changes,
    }


@pytest.fixture
def driver(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Intercept every subprocess call, including on a Windows test machine."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    calls: list[dict[str, Any]] = []
    state: dict[str, Any] = {
        "result": {"status": "observed", "processes": [process()]},
        "returncode": 0,
    }

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        error = state.get("error")
        if isinstance(error, Exception):
            raise error
        stdout = state.get("stdout", json.dumps(state["result"], ensure_ascii=False))
        return subprocess.CompletedProcess(command, state["returncode"], stdout, "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls, state


def test_non_windows_does_not_start_a_process(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = driver
    monkeypatch.setattr(sys, "platform", "linux")
    result = module.inspect_windows_host()
    assert result == {
        "status": "unavailable",
        "reason_code": "windows_required",
        "source": "windows_process_metadata",
        "processes": [],
        "running": None,
        "project_state": "unverified",
    }
    assert calls == []


def test_probe_has_no_caller_parameters_and_uses_only_fixed_bundled_driver(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, _ = driver
    assert len(inspect.signature(module.inspect_windows_host).parameters) == 0
    result = module.inspect_windows_host()
    assert result["status"] == "observed"
    assert result["processes"][0]["title"] == process()["title"]
    assert len(calls) == 1
    call = calls[0]
    assert call["command"] == [
        str(Path(r"C:\Windows") / "System32" / "WindowsPowerShell" / "v1.0"
            / "powershell.exe"),
        "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
        str(Path(module.__file__).with_name("ResolveHostMetadata.ps1")),
    ]
    assert call["input"] == ""
    assert call["encoding"] == "utf-8"
    assert call["timeout"] == 10
    assert call["shell"] is False
    assert call["check"] is False
    assert call["capture_output"] is True
    assert call["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.mark.parametrize(
    ("processes", "running", "hint"),
    [
        ([], False, "no_window"),
        ([process(title="Project Manager")], True, "project_manager"),
        ([process(title="project manager")], True, "named_window"),
        ([process(title="Project Manager — Project")], True, "named_window"),
        ([process()], True, "named_window"),
        ([process(title="")], True, "no_window"),
        ([process(title="Project Manager", main_window_handle=0)], True, "no_window"),
        ([process(responding=False)], True, "named_window"),
        ([process(), process(pid=321)], True, "multiple_processes"),
    ],
)
def test_window_titles_are_hints_without_native_project_or_readiness_claims(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    processes: list[dict[str, Any]],
    running: bool,
    hint: str,
) -> None:
    _, state = driver
    state["result"]["processes"] = processes
    result = module.inspect_windows_host()
    assert result == {
        "status": "observed",
        "source": "windows_process_metadata",
        "processes": processes,
        "running": running,
        "window_hint": hint,
        "project_state": "unverified",
    }


@pytest.mark.parametrize("executable_path", [None, "", "x" * 1024])
def test_executable_path_is_optional_bounded_evidence(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    executable_path: str | None,
) -> None:
    _, state = driver
    state["result"]["processes"] = [process(executable_path=executable_path)]
    result = module.inspect_windows_host()
    assert result["status"] == "observed"
    assert result["processes"][0]["executable_path"] == executable_path


@pytest.mark.parametrize(
    "changes",
    [
        {"pid": 0}, {"pid": -1}, {"pid": True}, {"pid": 1.0},
        {"title": None}, {"title": 3}, {"title": "x" * 513},
        {"main_window_handle": -1}, {"main_window_handle": False},
        {"main_window_handle": 1.0}, {"responding": 1}, {"responding": None},
        {"executable_path": 4}, {"executable_path": "x" * 1025},
        {"project_id": "untrusted"}, {"arguments": ["-Command", "untrusted"]},
    ],
)
def test_invalid_process_metadata_is_unavailable_never_not_running(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], changes: dict[str, Any]
) -> None:
    _, state = driver
    state["result"]["processes"] = [process(**changes)]
    result = module.inspect_windows_host()
    assert result["status"] == "unavailable"
    assert result["reason_code"] == "invalid_metadata_response"
    assert result["running"] is None
    assert result["processes"] == []


@pytest.mark.parametrize(
    "result",
    [
        [], None, True,
        {"status": "observed", "processes": {}},
        {"status": "observed", "processes": [process()] * 2},
        {"status": "observed", "processes": [process(pid=i) for i in range(1, 10)]},
        {"status": "observed", "processes": [None]},
        {"status": "observed", "processes": [{"pid": 123}]},
        {"status": "observed", "processes": [], "ready": True},
        {"status": "observed", "processes": [], "source": "untrusted"},
        {"status": "unknown", "processes": []},
        {"status": "unavailable", "reason_code": "untrusted"},
        {"status": "unavailable", "reason_code": []},
        {"status": "unavailable", "reason_code": "process_metadata_unavailable",
         "running": False},
    ],
)
def test_invalid_envelopes_are_unavailable(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], result: Any,
) -> None:
    _, state = driver
    state["result"] = result
    actual = module.inspect_windows_host()
    assert actual["status"] == "unavailable"
    assert actual["reason_code"] == "invalid_metadata_response"
    assert actual["running"] is None


@pytest.mark.parametrize(
    "stdout",
    ["", "not json", "{" * 2000, "x" * 65537,
     '{"status":"observed","processes":[],"processes":[]}',
     '{"status":"observed","processes":[{"pid":1,"pid":2}]}'],
    ids=["empty", "non_json", "deep", "oversized", "duplicate_envelope_key",
         "duplicate_process_key"],
)
def test_malformed_or_oversized_json_is_unavailable(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], stdout: str,
) -> None:
    _, state = driver
    state["stdout"] = stdout
    result = module.inspect_windows_host()
    assert result["reason_code"] == "invalid_metadata_response"
    assert result["running"] is None


def test_bom_prefixed_unicode_response_is_accepted(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["stdout"] = "\ufeff" + json.dumps(state["result"], ensure_ascii=False)
    assert module.inspect_windows_host()["status"] == "observed"


@pytest.mark.parametrize(
    "reason_code",
    ["process_enumeration_unavailable", "process_metadata_unavailable",
     "process_limit_exceeded", "metadata_bounds_exceeded", "invalid_driver_invocation"],
)
def test_driver_exit_and_access_races_do_not_claim_stopped(
    driver: tuple[list[dict[str, Any]], dict[str, Any]], reason_code: str,
) -> None:
    _, state = driver
    state["result"] = {"status": "unavailable", "reason_code": reason_code}
    result = module.inspect_windows_host()
    assert result["reason_code"] == reason_code
    assert result["running"] is None


@pytest.mark.parametrize(
    ("error", "reason_code"),
    [
        (subprocess.TimeoutExpired("fixed-driver", 10), "metadata_probe_timeout"),
        (FileNotFoundError(), "powershell_unavailable"),
        (PermissionError(), "powershell_unavailable"),
        (UnicodeError(), "invalid_metadata_response"),
    ],
)
def test_probe_errors_are_diagnostic_results(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    error: Exception, reason_code: str,
) -> None:
    _, state = driver
    state["error"] = error
    result = module.inspect_windows_host()
    assert result["reason_code"] == reason_code
    assert result["running"] is None


def test_unsuccessful_driver_exit_discards_even_plausible_stdout(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["returncode"] = 1
    state["result"]["processes"] = []
    result = module.inspect_windows_host()
    assert result["reason_code"] == "metadata_probe_failed"
    assert result["running"] is None


def test_missing_bundled_driver_does_not_start_a_process(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = driver
    monkeypatch.setattr(Path, "is_file", lambda _: False)
    result = module.inspect_windows_host()
    assert result["reason_code"] == "bundled_driver_unavailable"
    assert result["running"] is None
    assert calls == []


def test_schema_maximum_process_count_and_title_are_accepted(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["result"]["processes"] = [
        process(pid=i, title="ж" * 512) for i in range(1, 9)
    ]
    result = module.inspect_windows_host()
    assert result["status"] == "observed"
    assert len(result["processes"]) == 8
    assert result["window_hint"] == "multiple_processes"


def test_driver_access_race_does_not_start_a_process(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = driver

    def inaccessible(_: Path) -> bool:
        raise PermissionError("Synthetic bundled-resource access race.")

    monkeypatch.setattr(Path, "is_file", inaccessible)
    result = module.inspect_windows_host()
    assert result["reason_code"] == "bundled_driver_unavailable"
    assert result["running"] is None
    assert calls == []
