"""Offline creation fault boundaries; these tests never inspect or control Resolve."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from mcp_server.automatic import AutomaticServer
from mcp_server.managed_marker import claim_write_key
from providers.resolve.automatic_connection import AutomaticConnection
from providers.resolve.marker_supervisor import Profile, Supervisor
from providers.resolve.project_create_service import (
    ProjectCreateError,
    ProjectCreateService,
    creation_receipts,
)
from providers.resolve.project_open_service import (
    ProjectOpenService,
    pending_project_open,
)

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
        self.existing: list[dict[str, str]] = []

    def inspect(self) -> dict[str, Any]:
        return {"state": self.state, "process": self.process, "eligible": True}

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
        if operation == "begin" and self.lost_begin_ack:
            raise RuntimeError("Lost dialog acknowledgment")
        if operation == "inspect_dialog":
            if self.dialog_waits:
                self.dialog_waits -= 1
                return {"dialog": None}
            return {"dialog": {"handle": 111, "runtime_id": [42, 111]}}
        if operation == "create":
            assert kwargs["name"] == PROJECT["name"]
            self.created = True
            self.state = "project_window"
            if self.lost_create_ack:
                raise RuntimeError("Lost Create acknowledgment")
        return {}

    def invoke_managed_marker(self, root: Any, process: Any, **kwargs: Any) -> None:
        assert kwargs == {"background_only": True}
        self.calls.append("bootstrap")
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
