"""Project closure preserves content, exact identity and original replay evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from test_resolve_bridge_writes import FakeResolve, _command, _run_command
from test_sdk_command_client import Channel, queued, setup_worker

from agent.application import AgentApplication
from agent.contracts import validate_contract
from bridges.resolve import ResolveBridge as bridge
from mcp_server.automatic import AutomaticServer
from providers.resolve.automatic_connection import (
    AutomaticConnection,
    AutomaticConnectionError,
)
from providers.resolve.marker_supervisor import Profile
from providers.resolve.project_close_state import (
    close_intent,
    explicit_open_completed,
    finish_close,
    prepare_close,
)
from providers.resolve.sdk_command_client import SDKCommandClient


def closable(native: FakeResolve, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []
    current: dict[str, Any] = {"value": native.project}

    def close(project: Any) -> bool:
        assert project is current["value"]
        calls.append(project)
        current["value"] = None
        return True

    monkeypatch.setattr(
        native.project_manager, "GetCurrentProject", lambda: current["value"]
    )
    monkeypatch.setattr(native.project_manager, "CloseProject", close, raising=False)
    return calls


def arguments(native: FakeResolve) -> dict[str, Any]:
    return {"expected_project_id": native.project.GetUniqueId(), "confirm_close": True}


def test_native_close_saves_backs_up_and_replays_without_changing_new_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = FakeResolve()
    calls = closable(native, monkeypatch)
    saves: list[bool] = []

    def save() -> bool:
        saves.append(True)
        return True

    monkeypatch.setattr(native.project_manager, "SaveProject", save)
    command = _command(
        str(uuid4()), "close_project", arguments(native), idempotency_key="close"
    )
    validate_contract("command", command)
    response = _run_command(
        tmp_path, native, bridge.collect_bridge_state(native), command
    )
    assert response["status"] == "success"
    assert response["result"]["closed"] is True
    assert response["result"]["saved"] is True
    assert Path(response["result"]["backup_path"]).is_file()
    assert native.project_manager.GetCurrentProject() is None
    assert len(saves) == 2
    assert len(calls) == 1
    monkeypatch.setattr(
        native.project_manager, "GetCurrentProject", lambda: native.project
    )
    command["command_id"] = str(uuid4())
    replay = _run_command(
        tmp_path, native, bridge.collect_bridge_state(native), command
    )
    assert replay["result"] == response["result"]
    assert len(saves) == 2
    assert len(calls) == 1


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("identity", "PROJECT_CHANGED"),
        ("render", "RENDER_ALREADY_IN_PROGRESS"),
        ("save", "PROJECT_SAVE_FAILED"),
        ("backup", "PROJECT_BACKUP_FAILED"),
        ("after_backup", "PROJECT_CHANGED"),
        ("missing", "UNSUPPORTED_CAPABILITY"),
    ],
)
def test_native_preflights_never_close_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected: str,
) -> None:
    native = FakeResolve()
    calls = closable(native, monkeypatch)
    args = arguments(native)
    if failure == "identity":
        args["expected_project_id"] = "another-project"
    elif failure == "render":
        monkeypatch.setattr(native.project, "IsRenderingInProgress", lambda: True)
    elif failure == "save":
        monkeypatch.setattr(native.project_manager, "SaveProject", lambda: False)
    elif failure == "backup":
        monkeypatch.setattr(
            native.project_manager, "ExportProject", lambda *args: False
        )
    elif failure == "after_backup":
        original = native.project_manager.ExportProject

        def export(*args: Any) -> bool:
            result = original(*args)
            monkeypatch.setattr(native.project, "GetUniqueId", lambda: "changed")
            return result

        monkeypatch.setattr(native.project_manager, "ExportProject", export)
    else:
        monkeypatch.setattr(native.project_manager, "CloseProject", None)
    response = _run_command(
        tmp_path,
        native,
        bridge.collect_bridge_state(native),
        _command(str(uuid4()), "close_project", args),
    )
    assert response["error"]["code"] == expected
    assert not calls


@pytest.mark.parametrize("failure", ["false", "readback"])
def test_close_requires_acknowledgement_and_empty_current_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    native = FakeResolve()
    monkeypatch.setattr(
        native.project_manager,
        "CloseProject",
        lambda project: failure == "readback",
        raising=False,
    )
    response = _run_command(
        tmp_path,
        native,
        bridge.collect_bridge_state(native),
        _command(str(uuid4()), "close_project", arguments(native)),
    )
    assert response["status"] == "error"
    assert response["error"]["code"] in {
        "PROJECT_CLOSE_FAILED",
        "PROJECT_CLOSE_READBACK_FAILED",
    }
    assert Path(response["error"]["details"]["backup_path"]).exists()


def test_sdk_proxy_replay_never_acquires_a_new_native_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = FakeResolve()
    calls = closable(native, monkeypatch)
    channels: list[Channel] = []

    def factory(operation: dict[str, Any], timeout: float) -> Channel:
        channel = Channel(native)
        channels.append(channel)
        return channel

    client = SDKCommandClient(tmp_path, "a" * 32, channel_factory=factory)
    kwargs: dict[str, Any] = dict(
        provider="resolve",
        action="close_project",
        arguments=arguments(native),
        create_backup=True,
        idempotency_key="close",
    )
    result = client.request(**kwargs)
    assert result["closed"] is True
    assert client.request(**kwargs) == result
    assert len(channels) == len(calls) == 1


def test_worker_close_replay_skips_connection_even_after_project_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, native, active = setup_worker(tmp_path, monkeypatch)
    calls = closable(native, monkeypatch)
    args = arguments(native)
    worker.process(queued(worker, "close_project", args, "close"))
    assert close_intent(worker.profile)["phase"] == "closed"  # type: ignore[index]
    active["generation"] = "new-generation"
    monkeypatch.setattr(
        worker.connection,
        "ensure",
        lambda **kwargs: pytest.fail("connection used on replay"),
    )
    path = queued(worker, "close_project", args, "close")
    worker.process(path)
    response = json.loads((worker.directories["responses"] / path.name).read_text())
    assert response["result"]["replayed"] is True
    assert response["result"]["evidence"] == "historical_completed_receipt"
    assert len(calls) == 1
    with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
        worker.process(
            queued(
                worker,
                "close_project",
                {**args, "expected_project_id": "changed"},
                "close",
            )
        )


def test_worker_wrong_identity_does_not_leave_automatic_recovery_suspended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, native, active = setup_worker(tmp_path, monkeypatch)
    calls = closable(native, monkeypatch)
    worker.process(
        queued(
            worker,
            "close_project",
            {"expected_project_id": "wrong", "confirm_close": True},
            "wrong",
        )
    )
    assert close_intent(worker.profile) is None
    assert not calls


def test_uncertain_close_is_never_resent_with_old_or_new_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, native, active = setup_worker(tmp_path, monkeypatch)
    calls = closable(native, monkeypatch)
    original = native.project_manager.CloseProject  # type: ignore[attr-defined]

    def lost(project: Any) -> bool:
        original(project)
        raise RuntimeError("response lost")

    monkeypatch.setattr(native.project_manager, "CloseProject", lost)
    args = arguments(native)
    with pytest.raises(RuntimeError):
        worker.process(queued(worker, "close_project", args, "close"))
    assert close_intent(worker.profile)["phase"] == "pending"  # type: ignore[index]
    monkeypatch.setattr(
        worker.connection,
        "ensure",
        lambda **kwargs: pytest.fail("bootstrap after uncertainty"),
    )
    for key in ("close", "new-key"):
        with pytest.raises(RuntimeError, match="REQUIRES_RECOVERY"):
            worker.process(queued(worker, "close_project", args, key))
    assert len(calls) == 1


def profile_at(tmp_path: Path) -> Profile:
    root = tmp_path / "runtime"
    root.mkdir()
    return Profile(tmp_path / "profile.json", root, (), enabled=True)


@pytest.mark.parametrize("phase", ["pending", "closed"])
@pytest.mark.parametrize("host", ["project_manager", "resolve_not_running"])
def test_closed_project_never_auto_opens_or_launches_even_with_cached_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    host: str,
) -> None:
    profile = profile_at(tmp_path)
    identity = str(uuid4())
    prepare_close(profile, identity, "a" * 64)
    if phase == "closed":
        finish_close(profile, identity, closed=True)
    connection = AutomaticConnection(profile)
    native = profile.runtime_base / "native"
    native.mkdir()
    (native / "sdk-template.preset").touch()
    monkeypatch.setattr(
        "providers.resolve.automatic_connection.read_active",
        lambda profile: {
            "runtime": str(native),
            "phase": "connected",
            "process": {},
        },
    )
    monkeypatch.setattr(
        "providers.resolve.automatic_connection.process_alive", lambda p: True
    )
    monkeypatch.setattr(
        type(connection.adapter), "inspect", lambda self: {"state": host}
    )
    monkeypatch.setattr(connection, "_open", lambda *args: pytest.fail("reopened"))
    monkeypatch.setattr(
        "providers.resolve.automatic_connection.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("launched"),
    )
    with pytest.raises(AutomaticConnectionError, match="PROJECT_CLOSE"):
        connection.ensure()


def test_fresh_explicit_open_releases_hold_but_never_pending_intent(
    tmp_path: Path,
) -> None:
    profile = profile_at(tmp_path)
    identity = str(uuid4())
    prepare_close(profile, identity, "a" * 64)
    explicit_open_completed(profile)
    assert close_intent(profile) is not None
    finish_close(profile, identity, closed=True)
    explicit_open_completed(profile)
    assert close_intent(profile) is None


def test_close_without_a_project_does_not_open_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = AutomaticConnection(profile_at(tmp_path))
    monkeypatch.setattr(
        type(connection.adapter), "inspect", lambda self: {"state": "project_manager"}
    )
    monkeypatch.setattr(connection, "_open", lambda *args: pytest.fail("reopened"))
    with pytest.raises(AutomaticConnectionError, match="NO_ACTIVE_PROJECT"):
        connection.ensure(allow_project_open=False)


def test_auto_mcp_close_reaches_primary_without_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = AutomaticServer(profile_at(tmp_path), start_worker=False)

    async def ready() -> None:
        pytest.fail("close bootstrapped readiness")

    async def call(*args: Any, **kwargs: Any) -> Any:
        return {"closed": True}

    monkeypatch.setattr(server, "_ready", ready)
    monkeypatch.setattr(server.primary, "call_tool", call)
    result = asyncio.run(
        server.call_tool(
            "resolve_close_project",
            {
                "expected_project_id": "project",
                "idempotency_key": "close",
                "confirm_close": True,
            },
        )
    )
    assert result == {"closed": True}


@pytest.mark.parametrize(
    "changes",
    [{"confirm_close": False}, {"expected_project_id": ""}, {"idempotency_key": ""}],
)
def test_application_invalid_close_never_dispatches(changes: dict[str, Any]) -> None:
    def close(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid close dispatched")

    service = AgentApplication(resolve=SimpleNamespace(close_project=close))
    args: dict[str, Any] = {
        "expected_project_id": "project",
        "idempotency_key": "close",
        "confirm_close": True,
    }
    with pytest.raises(ValueError):
        service.resolve_close_project(**(args | changes))


@pytest.mark.parametrize("closed", [False, True])
def test_watcher_reports_user_close_instead_of_cached_connection(
    tmp_path: Path,
    closed: bool,
) -> None:
    from providers.resolve.marker_supervisor import Supervisor

    profile = profile_at(tmp_path)
    identity = str(uuid4())
    prepare_close(profile, identity, "a" * 64)
    if closed:
        finish_close(profile, identity, closed=True)
    adapter = SimpleNamespace(inspect=lambda: {"state": "project_manager"})
    supervisor = Supervisor(profile, adapter=adapter)
    result = supervisor._observe(None, lambda: None)
    assert result["phase"] == ("waiting_project" if closed else "recovery_required")


def test_close_capability_requires_observed_native_methods() -> None:
    from agent.capabilities import workflow_capabilities

    for available in (False, True):
        report = workflow_capabilities(
            backend="python",
            tools=["resolve_close_project"],
            connected=True,
            evidence="fresh_native_request",
            native_capabilities={"project.close": available},
        )
        row = next(
            row for row in report["workflows"] if row["workflow"] == "project_closure"
        )
        assert row["available"] is available


def test_mcp_close_transfers_confirmation_and_exact_identity() -> None:
    from mcp import Client
    from test_mcp_server import StubResolveReader

    from mcp_server.server import create_server

    server = create_server(AgentApplication(resolve=StubResolveReader()))

    async def scenario() -> None:
        async with Client(server) as client:
            response = await client.call_tool(
                "resolve_close_project",
                {
                    "expected_project_id": "project-id",
                    "confirm_close": True,
                    "idempotency_key": "stable-close",
                },
            )
            assert response.structured_content is not None
            assert response.structured_content["closed"] is True
            assert response.structured_content["project"]["project_id"] == "project-id"

    asyncio.run(scenario())


def test_corrupt_close_state_blocks_before_reopening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    path = profile.state_directory / "project-close.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema": 1, "phase": "closed"}')
    connection = AutomaticConnection(profile)
    monkeypatch.setattr(
        type(connection.adapter),
        "inspect",
        lambda self: pytest.fail("inspected corrupt intent"),
    )
    with pytest.raises(RuntimeError, match="INVALID_PROJECT_CLOSE_INTENT"):
        connection.ensure()


def test_terminal_close_replay_finishes_owned_guard_after_host_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    import time
    from uuid import UUID

    from providers.resolve.marker_transport import MarkerSlot

    worker, native, active = setup_worker(tmp_path, monkeypatch)
    closable(native, monkeypatch)
    args = arguments(native)
    worker.process(queued(worker, "close_project", args, "close"))
    receipt = (
        worker.profile.state_directory
        / "sdk-write-owners"
        / (hashlib.sha256(b"close").hexdigest() + ".json")
    )
    record = json.loads(receipt.read_text())
    guard = MarkerSlot(
        Path(active["runtime"]),
        {
            "session": active["session"],
            "id": UUID(record["command_id"]).hex,
            "action": "sdk_operation",
            "expires": int(time.time()) + 60,
        },
        4096,
    )
    guard.publish(time.monotonic() + 1)
    intent_path = worker.profile.state_directory / "project-close.json"
    intent = json.loads(intent_path.read_text())
    intent_path.write_text(json.dumps({**intent, "phase": "pending"}))
    monkeypatch.setattr(
        worker.connection,
        "ensure",
        lambda **kwargs: pytest.fail("bootstrap on terminal recovery"),
    )
    worker.process(queued(worker, "close_project", args, "close"))
    assert not (Path(active["runtime"]) / "inbox").exists()
    assert close_intent(worker.profile)["phase"] == "closed"  # type: ignore[index]
