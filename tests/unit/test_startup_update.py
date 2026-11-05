"""Startup update handling is limited to Skip before any project creation."""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client
from test_project_create_service import KEY, PROJECT, Fixture

import providers.resolve.project_create_service as creation_module
from mcp_server.automatic import AutomaticServer
from providers.resolve.project_close_state import prepare_close
from providers.resolve.project_create_service import (
    ProjectCreateError,
    creation_receipts,
)
from providers.resolve.project_open_service import pending_project_open
from providers.resolve.windows_project_create import (
    WindowsProjectCreateAdapter,
    WindowsProjectCreateError,
)


def update_observation(fixture: Fixture) -> dict[str, Any]:
    return dict(state="modal_blocked", process=dict(fixture.process), eligible=False,
        window_candidates=[dict(pid=123, name="Software Update",
            class_name="UiAutoUpdateDialogImp", automation_id="UiAutoUpdateDialog",
            control_type="ControlType.Window", is_offscreen=False)])


def update_fixture(tmp_path: Path, *, delay: bool = False) -> Fixture:
    f = Fixture(tmp_path)
    f.state = "modal_blocked"
    inspect, request = f.inspect, f.request
    waiting = delay

    def observe() -> dict[str, Any]:
        nonlocal waiting
        if f.state == "modal_blocked":
            result = update_observation(f)
            if "skip_startup_update" in f.calls and waiting:
                waiting = False
                f.state = "project_manager"
            return result
        return inspect()

    def dispatch(operation: str, process: Any, **kwargs: Any) -> dict[str, Any]:
        if operation == "skip_startup_update":
            f.calls.append(operation)
            assert not creation_receipts(f.profile)
            assert pending_project_open(f.profile)["pending"] is False
            if not delay:
                f.state = "project_manager"
            return dict(ok=True, operation=operation, skipped=True)
        return request(operation, process, **kwargs)

    f.inspect = observe  # type: ignore[method-assign]
    f.request = dispatch  # type: ignore[method-assign]
    return f


@pytest.mark.parametrize("cold", [False, True])
def test_first_mcp_call_skips_update_creates_once_and_replay_has_no_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cold: bool,
) -> None:
    f = update_fixture(tmp_path, delay=True)
    launches = []
    if cold:
        f.state = "resolve_not_running"
        executable = tmp_path / "Resolve.exe"
        executable.touch()
        monkeypatch.setattr(
            creation_module, "_official_executable", lambda: str(executable))

        def launch(*args: Any, **kwargs: Any) -> Any:
            launches.append(args)
            f.state = "modal_blocked"
            return SimpleNamespace(pid=123, poll=lambda: None)

        monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(creation_module, "ProjectCreateService", lambda _: f.service())
    server = AutomaticServer(f.profile, start_worker=False)
    arguments = dict(name=PROJECT["name"], confirm_create=True,
        idempotency_key=KEY, timeout_seconds=30)

    async def run() -> None:
        async with Client(server) as client:
            first = await client.call_tool("resolve_create_project", arguments)
            assert not first.is_error
            assert first.structured_content["persisted"] is True
            assert first.structured_content["startup_update_skipped"] is True
            assert f.calls.count("skip_startup_update") == 1
            assert f.calls.count("create") == 1
            assert f.calls.index("skip_startup_update") < f.calls.index("begin")
            before = list(f.calls)
            f.inspect = lambda: pytest.fail("Replay inspected UI")  # type: ignore[method-assign]
            replay = await client.call_tool("resolve_create_project", arguments)
            assert not replay.is_error
            assert replay.structured_content["replayed"] is True
            assert replay.structured_content["ui_create_dispatched"] is False
            assert replay.structured_content["startup_update_skipped"] is True
            assert f.calls == before

    asyncio.run(run())
    assert len(launches) == int(cold)


@pytest.mark.parametrize("fault", ["name", "class", "id", "pid", "hidden", "editor"])
def test_unknown_modal_is_never_dismissed_or_used_for_creation(
    tmp_path: Path, fault: str,
) -> None:
    f = update_fixture(tmp_path)
    observation = update_observation(f)
    item = observation["window_candidates"][0]
    field, value = {
        "name": ("name", "Save Project"), "class": ("class_name", "OtherDialog"),
        "id": ("automation_id", "OtherDialog"), "pid": ("pid", 999),
        "hidden": ("is_offscreen", True), "editor": ("name", "Software Update"),
    }[fault]
    item[field] = value
    if fault == "editor":
        observation["process"].update(window_title="DaVinci Resolve - Existing",
            project_name_hint="Existing")
    f.inspect = lambda: observation  # type: ignore[method-assign]
    with pytest.raises(ProjectCreateError, match="PROJECT_MANAGER_REQUIRED"):
        f.create()
    assert not f.calls and not creation_receipts(f.profile)


@pytest.mark.parametrize("uncertain", [False, True])
def test_skip_failure_has_no_create_receipt_and_exposes_separate_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    f = update_fixture(tmp_path)

    def fail(operation: str, process: Any, **kwargs: Any) -> Any:
        assert operation == "skip_startup_update"
        f.calls.append(operation)
        raise WindowsProjectCreateError("UPDATE_DIALOG_CHANGED", uncertain=uncertain)

    f.request = fail  # type: ignore[method-assign]
    monkeypatch.setattr(creation_module, "ProjectCreateService", lambda _: f.service())
    server = AutomaticServer(f.profile, start_worker=False)
    result = asyncio.run(server.call_tool("resolve_create_project", dict(
        name=PROJECT["name"], confirm_create=True, idempotency_key=KEY)))
    assert result.is_error
    assert result.structured_content["code"] == "PROJECT_STARTUP_UPDATE_BLOCKED"
    assert result.structured_content["uncertain"] is False
    assert result.structured_content["startup_update_dispatch_uncertain"] is uncertain
    assert result.structured_content["cause_code"] == "UPDATE_DIALOG_CHANGED"
    assert not creation_receipts(f.profile) and f.calls == ["skip_startup_update"]


@pytest.mark.parametrize("change", ["process", "window", "project"])
def test_context_change_after_skip_prevents_create(tmp_path: Path, change: str) -> None:
    f = update_fixture(tmp_path)
    original = f.request

    def dispatch(operation: str, process: Any, **kwargs: Any) -> Any:
        value = original(operation, process, **kwargs)
        if operation == "skip_startup_update":
            if change == "process":
                f.process["creation_time"] = "638999000000000001"
            elif change == "window":
                f.process["window_handle"] = 999
            else:
                f.state = "project_window"
        return value

    f.request = dispatch  # type: ignore[method-assign]
    with pytest.raises(ProjectCreateError):
        f.create()
    assert f.calls == ["skip_startup_update"] and not creation_receipts(f.profile)


@pytest.mark.parametrize(
    "changes", [dict(confirm_create=False), dict(idempotency_key="")]
)
def test_invalid_confirmation_or_key_cannot_dismiss_update(
    tmp_path: Path, changes: dict[str, Any],
) -> None:
    f = update_fixture(tmp_path)
    with pytest.raises(ValueError):
        f.create(**changes)
    assert not f.calls and not creation_receipts(f.profile)


@pytest.mark.parametrize("value", [True, False, "yes", 1, None])
def test_skip_adapter_validates_the_fixed_response_without_dialog_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any,
) -> None:
    f = Fixture(tmp_path)
    calls: list[Any] = []

    def run(command: Any, **kwargs: Any) -> Any:
        sent = json.loads(kwargs["input"])
        assert sent == dict(operation="skip_startup_update", process=f.process)
        assert kwargs["shell"] is False
        calls.append(sent)
        return subprocess.CompletedProcess(command, 0, json.dumps(dict(
            ok=True, operation="skip_startup_update", skipped=value)), "")

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(subprocess, "run", run)
    if type(value) is bool:
        assert WindowsProjectCreateAdapter().request(
            "skip_startup_update", f.process)["skipped"] is value
    else:
        with pytest.raises(RuntimeError, match="STARTUP_UPDATE_RESPONSE_UNVERIFIED"):
            WindowsProjectCreateAdapter().request("skip_startup_update", f.process)
    assert len(calls) == 1


def test_skip_is_not_repeated_while_the_same_dialog_is_still_closing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = update_fixture(tmp_path)
    clock = {"now": 100.0}

    def observe() -> dict[str, Any]:
        clock["now"] += 1
        return update_observation(f)

    def dispatch(operation: str, process: Any, **kwargs: Any) -> Any:
        assert operation == "skip_startup_update"
        f.calls.append(operation)
        return dict(ok=True, operation=operation, skipped=True)

    f.inspect = observe  # type: ignore[method-assign]
    f.request = dispatch  # type: ignore[method-assign]
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(ProjectCreateError, match="PROJECT_MANAGER_STARTUP_TIMEOUT"):
        f.create(timeout_seconds=3)
    assert f.calls == ["skip_startup_update"]
    assert not creation_receipts(f.profile)


def test_pending_close_blocks_skip_before_any_ui_inspection(tmp_path: Path) -> None:
    f = update_fixture(tmp_path)
    prepare_close(f.profile, "11111111-2222-4333-8444-555555555555", "a" * 64)
    f.inspect = lambda: pytest.fail("Pending close inspected UI")  # type: ignore[method-assign]
    with pytest.raises(ProjectCreateError, match="PROJECT_CLOSE_REQUIRES_RECOVERY"):
        f.create()
    assert not f.calls and not creation_receipts(f.profile)
