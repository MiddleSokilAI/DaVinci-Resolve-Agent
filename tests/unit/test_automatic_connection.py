"""Automatic connection selection never replays an uncertain native operation."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest

import mcp_server.automatic as facade
import providers.resolve.automatic_connection as module
from agent.contracts import validate_contract
from mcp_server.automatic import AutomaticServer
from providers.resolve.automatic_connection import (
    AutomaticConnection,
    AutomaticConnectionError,
    wait_operation,
)
from providers.resolve.marker_supervisor import OperationBusy, Profile, operation_lock


def profile_at(tmp_path: Path, *, enabled: bool = True) -> Profile:
    root = tmp_path / "runtime"
    root.mkdir()
    return Profile(tmp_path / "profile.json", root, (), enabled=enabled)


def test_disabled_profile_never_inspects_or_launches_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = AutomaticConnection(profile_at(tmp_path, enabled=False))
    monkeypatch.setattr(
        type(connection.adapter), "inspect", lambda self: pytest.fail("inspected")
    )
    with pytest.raises(AutomaticConnectionError, match="DISABLED"):
        connection.ensure()


def test_live_process_and_sdk_session_need_no_foreground_or_ui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    native = profile.runtime_base / "native"
    native.mkdir()
    (native / "sdk-template.preset").touch()
    active = {"runtime": str(native), "phase": "connected", "process": {}}
    monkeypatch.setattr(module, "read_active", lambda profile: active)
    monkeypatch.setattr(module, "process_alive", lambda process: True)
    connection = AutomaticConnection(profile)
    monkeypatch.setattr(
        type(connection.adapter), "inspect", lambda self: pytest.fail("UI used")
    )
    assert connection.ensure() is active


def test_wait_retries_lock_acquisition_but_never_its_body(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    release = threading.Event()
    held = threading.Event()

    def holder() -> None:
        with operation_lock(profile):
            held.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(2)
    timer = threading.Timer(0.05, release.set)
    timer.start()
    count = 0
    try:
        with pytest.raises(OperationBusy, match="body"), wait_operation(profile, 1):
            count += 1
            raise OperationBusy("body")
    finally:
        release.set()
        thread.join()
        timer.join()
    assert count == 1


def test_catalogue_and_unverified_capabilities_do_not_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = AutomaticServer(profile_at(tmp_path, enabled=False), start_worker=False)

    async def forbidden() -> None:
        pytest.fail("Native bootstrap during discovery")

    monkeypatch.setattr(server, "_ready", forbidden)

    async def scenario() -> None:
        tools = await server.list_tools()
        assert len(tools) == len({tool.name for tool in tools}) == 156
        assert {
            "resolve_create_project",
            "sync_screen_and_webcam",
            "resolve_lua_native_audio",
        } <= {tool.name for tool in tools}
        result = await server.call_tool("video_agent_capabilities", {})
        report = result.structured_content
        validate_contract("workflow-capabilities", report)
        assert report["backend"] == "auto"
        assert not any(
            row["available"] for row in report["workflows"] if row["scope"] == "resolve"
        )

    asyncio.run(scenario())


def test_disabled_connection_still_returns_truthful_capability_report(
    tmp_path: Path,
) -> None:
    server = AutomaticServer(profile_at(tmp_path, enabled=False), start_worker=False)
    result = asyncio.run(
        server.call_tool(
            "video_agent_capabilities",
            {
                "verify_connection": True,
            },
        )
    )
    report = result.structured_content
    assert report["connected"] is False
    assert report["evidence"] == "AUTOMATIC_CONNECTION_DISABLED"
    assert any(
        row["available"] for row in report["workflows"] if row["scope"] == "local"
    )


def test_native_call_bootstraps_before_creating_command_and_runs_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = AutomaticServer(profile_at(tmp_path, enabled=False), start_worker=False)
    events: list[Any] = []

    async def ready() -> None:
        events.append("connected")

    async def call(name: str, args: dict[str, Any], context: Any = None) -> Any:
        events.append((name, args))
        return "native response"

    monkeypatch.setattr(server, "_ready", ready)
    monkeypatch.setattr(server.primary, "call_tool", call)
    args = {"name": "New timeline", "confirm_create": True}
    assert (
        asyncio.run(server.call_tool("resolve_create_timeline", args))
        == "native response"
    )
    assert events == ["connected", ("resolve_create_timeline", args)]


def test_existing_opt_out_profile_is_not_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path, enabled=False)
    data = {
        "schema": 1,
        "enabled": False,
        "runtime_base": str(profile.runtime_base),
        "media_roots": [],
    }
    profile.path.write_text(json.dumps(data))
    monkeypatch.setattr(facade, "runtime_directory", lambda: profile.runtime_base)
    before = profile.path.read_bytes()
    assert facade.default_profile(profile.path).enabled is False
    assert profile.path.read_bytes() == before


def test_automatic_facade_preserves_workflow_audit_in_its_runtime(
    tmp_path: Path,
) -> None:
    profile = profile_at(tmp_path, enabled=False)
    server = AutomaticServer(profile, start_worker=False)
    result = asyncio.run(server.call_tool("list_editing_recipes", {}))
    assert not result.is_error
    records = list((profile.runtime_base / "logs/workflow").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["operation"] == "list_editing_recipes"
    assert record["status"] == "success"


def test_external_sdk_can_connect_without_the_optional_lua_sdk_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    native = profile.runtime_base / "native"
    native.mkdir()
    (native / "session.json").write_text(json.dumps({"sdk_proxy": {"version": 1}}))
    active = {"runtime": str(native), "phase": "connected", "process": {}}
    monkeypatch.setattr(module, "read_active", lambda profile: active)
    monkeypatch.setattr(module, "process_alive", lambda process: True)
    connection = AutomaticConnection(profile)
    assert connection.ensure(require_sdk_channel=False) is active
    with pytest.raises(AutomaticConnectionError, match="SDK_CHANNEL_UNAVAILABLE"):
        connection.ensure()


def test_legacy_native_tools_do_not_depend_on_the_primary_sdk_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = AutomaticServer(profile_at(tmp_path, enabled=False), start_worker=False)
    monkeypatch.setattr(server.connection, "ensure", lambda **kwargs: {})

    async def forbidden() -> None:
        pytest.fail("Primary SDK was required for a legacy marker tool")

    async def native(*args: Any) -> Any:
        return "native marker result"

    monkeypatch.setattr(server, "_ready", forbidden)
    monkeypatch.setattr(server.marker, "call_tool", native)
    assert (
        asyncio.run(server.call_tool("resolve_lua_get_project", {}))
        == "native marker result"
    )
