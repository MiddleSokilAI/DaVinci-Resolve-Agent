"""Offline creation fault boundaries; these tests never inspect or control Resolve."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client

import providers.resolve.project_create_service as creation_module
from mcp_server.automatic import AutomaticServer
from mcp_server.managed_marker import claim_write_key
from providers.resolve.automatic_connection import (
    AutomaticConnection,
    AutomaticConnectionError,
)
from providers.resolve.marker_supervisor import Profile, Supervisor, _lock, read_active
from providers.resolve.project_create_service import (
    ProjectCreateError,
    ProjectCreateService,
    creation_receipts,
)
from providers.resolve.project_open_service import (
    ProjectOpenService,
    pending_project_open,
)
from providers.resolve.windows_bootstrap import WindowsBootstrapError
from providers.resolve.windows_project_create import WindowsProjectCreateError

PROJECT = {"id": "11111111-2222-4333-8444-555555555555", "name": "Новий проєкт"}
DATABASE = {"DbType": "Disk", "DbName": "Local Database"}
KEY = "original-key"


class Fixture:
    def __init__(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        self.profile = Profile(tmp_path / "profile.json", runtime, (), enabled=True)
        self.process = {
            "pid": 123,
            "creation_time": "638999000000000000",
            "executable_path": (
                "C:/Program Files/Blackmagic Design/DaVinci Resolve/Resolve.exe"
            ),
            "window_handle": 456,
            "window_title": "Project Manager",
            "project_name_hint": None,
            "window_state": "normal",
        }
        self.state = "project_manager"
        self.calls: list[str] = []
        self.created = False
        self.native_fail = False
        self.lost_create_ack = False
        self.lost_begin_ack = False
        self.lost_bootstrap_ack = False
        self.persistence_fail = False
        self.dialog_waits = 0
        self.dialog_present = False
        self.bootstrap_error: WindowsBootstrapError | None = None
        self.open_name = PROJECT["name"]
        self.existing: list[dict[str, str]] = []

    def inspect(self) -> dict[str, Any]:
        process = dict(self.process)
        if self.created and self.state == "project_window":
            process.update(
                project_name_hint=self.open_name,
                window_title=f"DaVinci Resolve - {self.open_name}",
            )
        return {
            "state": self.state,
            "process": None if self.state == "resolve_not_running" else process,
            "eligible": self.state != "resolve_not_running",
        }

    def catalogue(self) -> dict[str, Any]:
        projects = [*self.existing]
        if self.created and not self.persistence_fail:
            projects.append(dict(PROJECT))
        return {
            "database": DATABASE,
            "projects": projects,
            "library_path": "owned-library",
        }

    def request(self, operation: str, process: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(operation)
        assert pending_project_open(self.profile)["pending"] is True
        if operation == "begin":
            self.dialog_present = True
            if self.lost_begin_ack:
                raise RuntimeError("Lost dialog acknowledgment")
        if operation == "inspect_dialog":
            if not self.dialog_present:
                return {"dialog": None}
            if self.dialog_waits:
                self.dialog_waits -= 1
                return {"dialog": None}
            return {"dialog": {"handle": 111, "runtime_id": [42, 111]}}
        if operation == "create":
            assert kwargs["name"] == PROJECT["name"]
            self.created = True
            self.dialog_present = False
            self.state = "project_window"
            if self.lost_create_ack:
                raise RuntimeError("Lost Create acknowledgment")
        return {}

    def invoke_managed_marker(self, root: Any, process: Any, **kwargs: Any) -> None:
        assert kwargs == {"background_only": True}
        self.calls.append("bootstrap")
        if self.bootstrap_error is not None:
            raise self.bootstrap_error
        if self.lost_bootstrap_ack:
            raise RuntimeError("Lost bootstrap acknowledgment")

    def client(self, root: Path) -> Any:
        fixture = self

        class Client:
            capabilities = {"project_context_v1"}

            def request(self, action: str, timeout: float, **kwargs: Any) -> Any:
                fixture.calls.append(action)
                if fixture.native_fail:
                    raise RuntimeError("native timeout")
                if action == "get_current_project":
                    return {"project": dict(PROJECT)}
                response = root / ("b" * 32 + ".ok_context_verified.drp")
                response.write_bytes(b"offline context fixture")
                return {
                    "project": dict(PROJECT),
                    "database": dict(DATABASE),
                    "folder": "",
                    "context_verified": True,
                    "folder_membership_verified": True,
                    "source": "live_lua_api",
                    "readback_verified_by": "ResolveLuaProjectContext",
                    "response_path": str(response),
                }

        return Client()

    def service(self) -> ProjectCreateService:
        return ProjectCreateService(
            self.profile,
            ui=self,
            host=self,
            manager=self,
            catalogue=self.catalogue,
            supervisor_factory=lambda p: Supervisor(p, menu_installer=lambda _: None),
            client_factory=self.client,
        )

    def create(self, **kwargs: Any) -> dict[str, Any]:
        return self.service().create(
            **{
                "name": PROJECT["name"],
                "confirm_create": True,
                "idempotency_key": KEY,
                "timeout_seconds": 30,
                **kwargs,
            }
        )


def test_creates_once_and_replay_does_not_inspect_or_open(tmp_path: Path) -> None:
    f = Fixture(tmp_path)
    f.dialog_waits = 2
    result = f.create()
    assert result["project"] == PROJECT and result["persisted"] is True
    assert not pending_project_open(f.profile)["pending"]
    assert (
        f.calls.count("begin")
        == f.calls.count("create")
        == f.calls.count("bootstrap")
        == 1
    )
    before = list(f.calls)
    f.inspect = lambda: pytest.fail("Replay inspected UI")  # type: ignore[method-assign]
    replay = f.create()
    assert replay["replayed"] is True
    assert replay["native_connection_verified"] is False
    assert replay["ui_create_dispatched"] is False
    assert replay["proof_scope"] == "historical_completed_receipt"
    assert f.calls == before


@pytest.mark.parametrize(
    "fault",
    ["lost_create_ack", "lost_bootstrap_ack", "native_fail", "persistence_fail"],
)
def test_uncertain_recovery_never_repeats_create_or_bootstrap(
    tmp_path: Path, fault: str
) -> None:
    f = Fixture(tmp_path)
    setattr(f, fault, True)
    with pytest.raises(ProjectCreateError, match="RECOVERY|PERSISTENCE"):
        f.create()
    assert pending_project_open(f.profile)["pending"]
    before = copy.deepcopy(f.calls)
    with pytest.raises(ProjectCreateError, match="ANOTHER"):
        f.create(idempotency_key="new-key")
    assert f.calls == before
    # Both background watcher and automatic readiness must stop before UI.
    watcher = Supervisor(f.profile, adapter=f)
    assert watcher.tick()["reason"] == "PROJECT_CREATION_REQUIRES_RECOVERY"
    with pytest.raises(RuntimeError, match="CREATION_REQUIRES_RECOVERY"):
        AutomaticConnection(f.profile).ensure(timeout=1)
    with pytest.raises(RuntimeError, match="CREATION_REQUIRES_RECOVERY"):
        ProjectOpenService(f.profile)._native_clear("c" * 64)
    setattr(f, fault, False)
    assert f.create()["persisted"] is True
    assert f.calls.count("create") == 1
    assert f.calls.count("bootstrap") == 1


@pytest.mark.parametrize("uncertain", [False, True])
def test_bootstrap_error_preserves_cause_and_original_creation(
    tmp_path: Path, uncertain: bool,
) -> None:
    f = Fixture(tmp_path)
    f.bootstrap_error = WindowsBootstrapError(
        "MENU_ITEM_UNAVAILABLE", uncertain=uncertain
    )
    with pytest.raises(ProjectCreateError, match="MENU_ITEM_UNAVAILABLE") as failure:
        f.create()
    assert failure.value.uncertain is True
    assert failure.value.cause_code == "MENU_ITEM_UNAVAILABLE"
    assert failure.value.bridge_dispatch_uncertain is uncertain
    active = read_active(f.profile)
    assert active is not None
    assert active["dispatch_started"] is uncertain
    assert active["last_error"] == "MENU_ITEM_UNAVAILABLE"
    receipt = next(iter(creation_receipts(f.profile).values()))
    assert receipt["phase"] == "bootstrapping"
    assert receipt["native_binding"]["generation"] == active["generation"]
    assert f.calls.count("create") == f.calls.count("bootstrap") == 1
    assert "get_current_project" not in f.calls
    f.bootstrap_error = None
    result = f.create()
    assert result["persisted"] is True
    assert result["generation"] == active["generation"]
    assert f.calls.count("create") == 1
    assert f.calls.count("bootstrap") == (1 if uncertain else 2)
    before = list(f.calls)
    assert f.create()["replayed"] is True
    assert f.calls == before


def test_bootstrap_unknown_error_keeps_dispatch_owned(tmp_path: Path) -> None:
    f = Fixture(tmp_path)
    f.lost_bootstrap_ack = True
    with pytest.raises(ProjectCreateError):
        f.create()
    active = read_active(f.profile)
    assert active is not None and active["dispatch_started"] is True
    f.lost_bootstrap_ack = False
    assert f.create()["persisted"] is True
    assert f.calls.count("create") == f.calls.count("bootstrap") == 1


def test_definite_bridge_refusal_does_not_retry_in_another_project(
    tmp_path: Path,
) -> None:
    f = Fixture(tmp_path)
    f.bootstrap_error = WindowsBootstrapError("MENU_ITEM_UNAVAILABLE")
    with pytest.raises(ProjectCreateError):
        f.create()
    f.bootstrap_error = None
    f.open_name = "Another project"
    before = list(f.calls)
    with pytest.raises(ProjectCreateError, match="CREATED_PROJECT_WINDOW_CHANGED"):
        f.create()
    assert f.calls == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_mcp_exposes_bridge_cause_without_discarding_creation_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    f = Fixture(tmp_path)
    f.bootstrap_error = WindowsBootstrapError(
        "MENU_ITEM_UNAVAILABLE", uncertain=uncertain
    )
    monkeypatch.setattr(creation_module, "ProjectCreateService", lambda _: f.service())
    server = AutomaticServer(f.profile, start_worker=False)
    args = {"name": PROJECT["name"], "confirm_create": True, "idempotency_key": KEY}

    async def scenario() -> None:
        async with Client(server) as client:
            response = await client.call_tool("resolve_create_project", args)
            assert response.is_error
            assert response.structured_content is not None
            assert response.structured_content["code"] == (
                "PROJECT_BRIDGE_BOOTSTRAP_FAILED"
            )
            assert response.structured_content["uncertain"] is True
            assert response.structured_content["cause_code"] == "MENU_ITEM_UNAVAILABLE"
            assert response.structured_content["bridge_dispatch_uncertain"] is uncertain
            f.bootstrap_error = None
            result = await client.call_tool("resolve_create_project", args)
            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["persisted"] is True

    asyncio.run(scenario())
    assert f.calls.count("create") == 1


def test_lost_begin_ack_recovers_dialog_without_new_project_again(
    tmp_path: Path,
) -> None:
    f = Fixture(tmp_path)
    f.lost_begin_ack = True
    with pytest.raises(RuntimeError):
        f.create()
    f.lost_begin_ack = False
    assert f.create()["persisted"]
    assert f.calls.count("begin") == 1


def test_original_key_recovers_absent_dialog_before_any_create(
    tmp_path: Path,
) -> None:
    f = Fixture(tmp_path)
    request = f.request
    rejected = False

    def reject_once(operation: str, process: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal rejected
        if operation == "begin" and not rejected:
            rejected = True
            f.calls.append("begin_rejected")
            raise RuntimeError("MANAGER_BLOCKED before Invoke")
        return request(operation, process, **kwargs)

    f.request = reject_once  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="MANAGER_BLOCKED"):
        f.create()
    assert next(iter(creation_receipts(f.profile).values()))["phase"] == (
        "dialog_pending"
    )
    assert not f.created and not f.dialog_present
    result = f.create(timeout_seconds=1)
    assert result["persisted"] is True
    assert f.calls.count("begin") == f.calls.count("create") == 1
    assert f.create()["replayed"] is True
    assert f.calls.count("create") == 1


@pytest.mark.parametrize("change", ["library", "process", "window", "open_project"])
def test_missing_dialog_recovery_refuses_changed_original_context(
    tmp_path: Path, change: str,
) -> None:
    f = Fixture(tmp_path)
    f.lost_begin_ack = True
    with pytest.raises(RuntimeError):
        f.create()
    f.lost_begin_ack = False
    f.dialog_present = False
    if change == "library":
        f.existing = [{"id": "22222222-2222-4333-8444-555555555555", "name": "Other"}]
    elif change == "process":
        f.process = {**f.process, "creation_time": "638999000000000001"}
    elif change == "window":
        f.process = {**f.process, "window_handle": 999}
    else:
        f.state = "project_window"
    before = list(f.calls)
    with pytest.raises(ProjectCreateError, match="MANAGER_CHANGED|LIBRARY_CHANGED"):
        f.create()
    assert f.calls == [*before, "inspect_dialog"]
    assert f.calls.count("create") == 0


def test_original_precreate_key_restarts_stopped_process_without_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.lost_begin_ack = True
    with pytest.raises(RuntimeError):
        f.create()
    original = dict(f.process)
    f.lost_begin_ack = False
    f.dialog_present = False
    f.state = "resolve_not_running"
    executable = tmp_path / "Resolve.exe"
    executable.touch()
    monkeypatch.setattr(
        creation_module, "_official_executable", lambda: str(executable)
    )
    launches: list[Any] = []

    def launch(*args: Any, **kwargs: Any) -> Any:
        launches.append(args)
        f.process = {**original, "pid": 124, "creation_time": "638999000000000001",
                     "window_handle": 457}
        f.state = "project_manager"
        return SimpleNamespace(pid=124, poll=lambda: None)

    monkeypatch.setattr("subprocess.Popen", launch)
    result = f.create()
    assert result["persisted"] is True
    receipt = next(iter(creation_receipts(f.profile).values()))
    assert receipt["previous_dialog_processes"] == [original]
    assert receipt["process"] == f.process
    assert len(launches) == f.calls.count("create") == 1
    assert f.create()["replayed"] is True
    assert len(launches) == f.calls.count("create") == 1


def test_recovery_keeps_fixed_driver_refusal_detail_and_dispatch_flag(
    tmp_path: Path,
) -> None:
    f = Fixture(tmp_path)
    f.lost_begin_ack = True
    with pytest.raises(RuntimeError):
        f.create()
    f.lost_begin_ack = False
    f.dialog_present = False
    original_request = f.request

    def refuse(operation: str, process: Any, **kwargs: Any) -> dict[str, Any]:
        if operation == "begin":
            raise WindowsProjectCreateError("MANAGER_BLOCKED", uncertain=False)
        return original_request(operation, process, **kwargs)

    f.request = refuse  # type: ignore[method-assign]
    with pytest.raises(WindowsProjectCreateError, match="MANAGER_BLOCKED") as failure:
        f.create()
    assert failure.value.uncertain is False
    assert f.calls.count("create") == 0
    f.request = original_request  # type: ignore[method-assign]
    assert f.create()["persisted"] is True
    assert f.calls.count("create") == 1
    assert f.create()["replayed"] is True
    assert f.calls.count("create") == 1


def test_precreate_restart_refuses_changed_library_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.lost_begin_ack = True
    with pytest.raises(RuntimeError):
        f.create()
    f.lost_begin_ack = False
    f.dialog_present = False
    f.state = "resolve_not_running"
    f.existing = [{"id": "22222222-2222-4333-8444-555555555555", "name": "Other"}]
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("Launched"))
    with pytest.raises(ProjectCreateError, match="PRECREATE_LIBRARY_CHANGED"):
        f.create()
    assert not f.created


def test_stopped_process_after_create_dispatch_never_restarts_or_creates_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.lost_create_ack = True
    with pytest.raises(ProjectCreateError):
        f.create()
    f.state = "resolve_not_running"
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("Launched"))
    with pytest.raises(ProjectCreateError, match="ORIGINAL_PROCESS_CHANGED"):
        f.create()
    assert f.calls.count("create") == 1


def test_changed_process_and_key_conflict_never_dispatch_again(tmp_path: Path) -> None:
    f = Fixture(tmp_path)
    f.lost_create_ack = True
    with pytest.raises(ProjectCreateError):
        f.create()
    f.process = {**f.process, "creation_time": "638999000000000001"}
    with pytest.raises(ProjectCreateError, match="ORIGINAL_PROCESS_CHANGED"):
        f.create()
    with pytest.raises(ValueError, match="different"):
        f.create(name="Different")
    assert f.calls.count("create") == 1


def test_collision_has_no_ui_or_receipt(tmp_path: Path) -> None:
    f = Fixture(tmp_path)
    f.existing = [{**PROJECT, "name": PROJECT["name"].upper()}]
    with pytest.raises(ProjectCreateError, match="ALREADY_EXISTS"):
        f.create()
    assert not f.calls and not creation_receipts(f.profile)


def test_completed_host_key_cannot_be_claimed_by_sdk_or_lua(tmp_path: Path) -> None:
    f = Fixture(tmp_path)
    f.create()
    for route in ("sdk", "marker"):
        with pytest.raises(RuntimeError, match="host project creation"):
            claim_write_key(f.profile, {}, KEY, route=route)
    digest = hashlib.sha256(KEY.encode()).hexdigest()
    owner = f.profile.state_directory / "write-owners" / f"{digest}.json"
    assert json.loads(owner.read_text())["schema"] == 3


def test_malformed_receipt_blocks_watcher_and_other_writes(tmp_path: Path) -> None:
    f = Fixture(tmp_path)
    f.create()
    path = next(iter(creation_receipts(f.profile)))
    path.write_text('{"schema":1,"schema":1}')
    with pytest.raises(ProjectCreateError, match="INVALID"):
        pending_project_open(f.profile)
    assert Supervisor(f.profile).tick()["phase"] == "recovery_required"


def test_automatic_routes_before_readiness_and_replays_without_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = Fixture(tmp_path)
    server = AutomaticServer(f.profile, start_worker=False)
    monkeypatch.setattr(
        "providers.resolve.project_create_service.ProjectCreateService",
        lambda _: f.service(),
    )

    async def forbidden(**kwargs: Any) -> None:
        pytest.fail("Historical readiness called")

    monkeypatch.setattr(server, "_ready", forbidden)
    args = {"name": PROJECT["name"], "confirm_create": True, "idempotency_key": KEY}
    result = asyncio.run(server.call_tool("resolve_create_project", args))
    assert not result.is_error
    f.inspect = lambda: pytest.fail("Replay inspected UI")  # type: ignore[method-assign]
    assert not asyncio.run(server.call_tool("resolve_create_project", args)).is_error


def test_cold_mcp_launches_for_create_and_replays_original_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.state = "resolve_not_running"
    launches: list[Any] = []
    executable = tmp_path / "Resolve.exe"
    executable.touch()

    def launch(*args: Any, **kwargs: Any) -> Any:
        launches.append((args, kwargs))
        f.state = "project_manager"
        return SimpleNamespace(pid=f.process["pid"], poll=lambda: None)

    monkeypatch.setattr(
        creation_module, "_official_executable", lambda: str(executable)
    )
    monkeypatch.setattr("subprocess.Popen", launch)
    monkeypatch.setattr(
        creation_module, "ProjectCreateService", lambda _: f.service(),
    )
    server = AutomaticServer(f.profile, start_worker=False)

    async def forbidden(**kwargs: Any) -> None:
        pytest.fail("Creation required an existing project")

    monkeypatch.setattr(server, "_ready", forbidden)
    args = {"name": PROJECT["name"], "confirm_create": True, "idempotency_key": KEY}
    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("resolve_create_project", args)
            assert not result.is_error, result
            assert result.structured_content is not None
            assert result.structured_content["project"] == PROJECT
            replay = await client.call_tool("resolve_create_project", args)
            assert not replay.is_error
            assert replay.structured_content is not None
            assert replay.structured_content["replayed"] is True

    asyncio.run(scenario())
    assert len(launches) == f.calls.count("create") == 1


def test_unavailable_window_does_not_fall_back_to_saved_project_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    inspections = 0
    original_inspect = f.inspect

    def inspect() -> dict[str, Any]:
        nonlocal inspections
        inspections += 1
        observation = original_inspect()
        if inspections == 1:
            observation.update(state="window_unavailable", eligible=False)
        return observation

    f.inspect = inspect  # type: ignore[method-assign]
    monkeypatch.setattr(creation_module, "ProjectCreateService", lambda _: f.service())
    server = AutomaticServer(f.profile, start_worker=False)

    async def forbidden(**kwargs: Any) -> None:
        pytest.fail("Window discovery race used existing-project readiness")

    monkeypatch.setattr(server, "_ready", forbidden)
    result = asyncio.run(server.call_tool("resolve_create_project", {
        "name": PROJECT["name"], "confirm_create": True, "idempotency_key": KEY,
    }))
    assert not result.is_error, result
    assert f.calls.count("create") == 1


def test_startup_timeout_does_not_own_key_and_same_key_can_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.state = "window_unavailable"
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("Relaunched"))
    with pytest.raises(ProjectCreateError, match="STARTUP_TIMEOUT") as failure:
        f.create(timeout_seconds=0.01)
    assert failure.value.uncertain is False
    assert not creation_receipts(f.profile)
    assert not list((f.profile.state_directory / "write-owners").glob("*.json"))
    assert not f.calls
    f.state = "project_manager"
    assert f.create()["persisted"] is True
    assert f.calls.count("create") == 1


def test_preflight_timeout_before_intent_does_not_strand_original_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    catalogue = f.catalogue
    clock = {"now": 100.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])

    def slow_catalogue() -> dict[str, Any]:
        clock["now"] += 1
        return catalogue()

    f.catalogue = slow_catalogue  # type: ignore[method-assign]
    with pytest.raises(ProjectCreateError, match="PREFLIGHT_TIMEOUT") as failure:
        f.create(timeout_seconds=0.5)
    assert failure.value.uncertain is False
    assert not f.calls and not creation_receipts(f.profile)
    assert not list((f.profile.state_directory / "write-owners").glob("*.json"))
    f.catalogue = catalogue  # type: ignore[method-assign]
    assert f.create()["persisted"] is True


@pytest.mark.parametrize(
    "state", ["project_window", "modal_blocked", "process_ambiguous"]
)
def test_cold_service_never_switches_or_creates_over_another_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    f = Fixture(tmp_path)
    f.state = state
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("Launched"))
    with pytest.raises(ProjectCreateError, match="PROJECT_MANAGER_REQUIRED"):
        f.create()
    assert not f.calls and not creation_receipts(f.profile)


def test_startup_process_replacement_is_rejected_before_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.state = "resolve_not_running"
    executable = tmp_path / "Resolve.exe"
    executable.touch()
    monkeypatch.setattr(
        creation_module, "_official_executable", lambda: str(executable)
    )

    def launch(*args: Any, **kwargs: Any) -> Any:
        f.state = "project_manager"
        return SimpleNamespace(pid=999, poll=lambda: None)

    monkeypatch.setattr("subprocess.Popen", launch)
    with pytest.raises(ProjectCreateError, match="STARTUP_PROCESS_CHANGED"):
        f.create()
    assert not f.calls and not creation_receipts(f.profile)


def test_launch_error_is_definite_and_does_not_consume_creation_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.state = "resolve_not_running"
    executable = tmp_path / "Resolve.exe"
    executable.touch()
    monkeypatch.setattr(
        creation_module, "_official_executable", lambda: str(executable)
    )

    def launch(*args: Any, **kwargs: Any) -> Any:
        raise OSError("Fixture launch failure")

    monkeypatch.setattr("subprocess.Popen", launch)
    with pytest.raises(ProjectCreateError, match="RESOLVE_STARTUP_FAILED") as failure:
        f.create()
    assert failure.value.uncertain is False
    assert not f.calls and not creation_receipts(f.profile)
    f.state = "project_manager"
    assert f.create()["persisted"] is True


def test_cold_start_waits_for_process_visibility_and_loading_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.state = "resolve_not_running"
    original_inspect = f.inspect
    executable = tmp_path / "Resolve.exe"
    executable.touch()
    monkeypatch.setattr(
        creation_module, "_official_executable", lambda: str(executable)
    )
    launches: list[Any] = []
    observations = iter(
        ["resolve_not_running", "window_unavailable", "project_manager"]
    )

    def launch(*args: Any, **kwargs: Any) -> Any:
        assert args == ([str(executable)],)
        assert kwargs["shell"] is False
        launches.append(args)
        return SimpleNamespace(pid=f.process["pid"], poll=lambda: None)

    def inspect() -> dict[str, Any]:
        if launches and not f.created:
            f.state = next(observations, "project_manager")
        return original_inspect()

    monkeypatch.setattr("subprocess.Popen", launch)
    f.inspect = inspect  # type: ignore[method-assign]
    assert f.create()["persisted"] is True
    assert len(launches) == f.calls.count("create") == 1


@pytest.mark.parametrize("restored_project", [False, True])
def test_launch_exit_or_restored_project_never_relaunches_or_creates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_project: bool,
) -> None:
    f = Fixture(tmp_path)
    f.state = "resolve_not_running"
    executable = tmp_path / "Resolve.exe"
    executable.touch()
    monkeypatch.setattr(
        creation_module, "_official_executable", lambda: str(executable)
    )
    launches: list[Any] = []

    def launch(*args: Any, **kwargs: Any) -> Any:
        launches.append(args)
        if restored_project:
            f.state = "project_window"
        return SimpleNamespace(pid=f.process["pid"], poll=lambda: 1)

    monkeypatch.setattr("subprocess.Popen", launch)
    code = "PROJECT_MANAGER_REQUIRED" if restored_project else "RESOLVE_STARTUP_EXITED"
    with pytest.raises(ProjectCreateError, match=code) as failure:
        f.create()
    assert failure.value.uncertain is False
    assert len(launches) == 1
    assert not f.calls and not creation_receipts(f.profile)


def test_concurrent_connection_startup_does_not_launch_or_consume_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.state = "resolve_not_running"
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("Launched"))
    with (
        _lock(f.profile, "connection.lock"),
        pytest.raises(ProjectCreateError, match="PROJECT_STARTUP_BUSY") as failure,
    ):
        f.create()
    assert failure.value.uncertain is False
    assert not f.calls and not creation_receipts(f.profile)
    f.state = "project_manager"
    assert f.create()["persisted"] is True


def test_missing_cold_key_is_rejected_without_starting_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.state = "resolve_not_running"
    monkeypatch.setattr(creation_module, "ProjectCreateService", lambda _: f.service())
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("Launched"))
    server = AutomaticServer(f.profile, start_worker=False)
    result = asyncio.run(server.call_tool("resolve_create_project", {
        "name": PROJECT["name"], "confirm_create": True,
    }))
    assert result.is_error
    assert result.structured_content["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert not f.calls and not creation_receipts(f.profile)


def test_window_closed_during_warm_readiness_can_create_without_opening_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    f.state = "project_window"
    monkeypatch.setattr(creation_module, "ProjectCreateService", lambda _: f.service())
    server = AutomaticServer(f.profile, start_worker=False)

    async def ready(**kwargs: Any) -> None:
        assert kwargs == {"allow_project_open": False}
        f.state = "project_manager"
        raise AutomaticConnectionError("NO_ACTIVE_PROJECT")

    monkeypatch.setattr(server, "_ready", ready)
    result = asyncio.run(server.call_tool("resolve_create_project", {
        "name": PROJECT["name"], "confirm_create": True, "idempotency_key": KEY,
    }))
    assert not result.is_error, result
    assert f.calls.count("create") == 1


def test_no_active_project_does_not_report_cold_creation_as_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = Fixture(tmp_path)
    server = AutomaticServer(f.profile, start_worker=False)

    async def ready(**kwargs: Any) -> None:
        raise AutomaticConnectionError("NO_ACTIVE_PROJECT")

    monkeypatch.setattr(server, "_ready", ready)
    result = asyncio.run(server.call_tool("video_agent_capabilities", {
        "verify_connection": True,
    }))
    report = result.structured_content
    assert report["connected"] is False
    rows = {row["workflow"]: row for row in report["workflows"]}
    assert rows["project_creation"]["status"] == "unverified"
    assert rows["project_creation"]["reason"] == (
        "independent_creation_preflight_required"
    )
    assert rows["media_import"]["status"] == "blocked"


@pytest.mark.parametrize(
    "changes",
    [
        {"confirm_create": False},
        {"idempotency_key": ""},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": True},
        {"name": " ../project"},
        {"name": "bad/name"},
    ],
)
def test_validation_precedes_all_ui_and_receipts(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    f = Fixture(tmp_path)
    f.inspect = lambda: pytest.fail("Invalid request inspected UI")  # type: ignore[method-assign]
    with pytest.raises(ValueError):
        f.create(**changes)
    assert not f.calls and not creation_receipts(f.profile)


def test_readonly_adapter_accepts_project_manager_without_project_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from providers.resolve.windows_project_create import WindowsProjectCreateAdapter

    f = Fixture(tmp_path)
    calls: list[Any] = []

    def run(command: Any, **kwargs: Any) -> Any:
        calls.append((command, kwargs))
        assert json.loads(kwargs["input"])["process"]["project_name_hint"] is None
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "ok": True,
                    "operation": "inspect_dialog",
                    "dialog": None,
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("sys.platform", "win32")
    result = WindowsProjectCreateAdapter().request("inspect_dialog", f.process)
    assert result["dialog"] is None and len(calls) == 1
    assert Path(calls[0][0][-1]).name == "ResolveProjectCreateUI.ps1"


def test_capabilities_readiness_does_not_open_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = Fixture(tmp_path)
    server = AutomaticServer(f.profile, start_worker=False)
    observed: list[Any] = []

    def ensure(**kwargs: Any) -> Any:
        observed.append(kwargs)
        raise RuntimeError("No active native project")

    monkeypatch.setattr(server.connection, "ensure", ensure)
    result = asyncio.run(
        server.call_tool("video_agent_capabilities", {"verify_connection": True})
    )
    assert not result.is_error
    assert observed and observed[0]["allow_project_open"] is False
