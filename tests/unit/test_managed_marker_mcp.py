"""Whole-call routing and cross-generation write ownership, without native UI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.types import CallToolResult, ToolAnnotations

import mcp_server.managed_marker as module
from mcp_server.managed_marker import ManagedMarkerServer
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.marker_supervisor import OperationBusy, Profile, operation_lock
from providers.resolve.marker_transport import prepare_marker
from providers.resolve.project_open_service import ProjectOpenError, ProjectOpenService


async def call_result(
    server: ManagedMarkerServer, name: str, arguments: dict[str, Any]
) -> CallToolResult:
    result = await server.call_tool(name, arguments)
    assert isinstance(result, CallToolResult)
    return result


def profile_at(tmp_path: Path) -> Profile:
    base = tmp_path / "runtimes"
    base.mkdir()
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({
        "schema": 1, "enabled": True,
        "runtime_base": str(base), "media_roots": [str(tmp_path)],
    }), encoding="utf-8")
    return Profile.load(path)


def active_at(profile: Profile, name: str) -> dict[str, Any]:
    root = profile.runtime_base / name
    prepare_marker(root, list(profile.media_roots))
    metadata = json.loads((root / "session.json").read_text(encoding="utf-8"))
    return {"generation": metadata["session"], "session": metadata["session"],
            "runtime": str(root), "phase": "connected"}


def install_state(
    monkeypatch: pytest.MonkeyPatch,
    profile: Profile,
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    selected = {"active": first}
    monkeypatch.setattr(module, "read_active", lambda profile: selected["active"])
    monkeypatch.setattr(module, "runtime_history", lambda profile: [
        Path(first["runtime"]), Path(second["runtime"])
    ])
    return selected


def test_catalogue_available_without_native_or_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    monkeypatch.setattr(module, "read_active", lambda profile: None)
    server = ManagedMarkerServer(profile)

    async def scenario() -> None:
        tools = await server.list_tools()
        assert len(tools) == 54
        assert "resolve_free_open_project" in {tool.name for tool in tools}
        capabilities = await call_result(server, "video_agent_capabilities", {
            "verify_connection": True
        })
        assert not capabilities.is_error
        assert capabilities.structured_content is not None
        assert capabilities.structured_content["connected"] is False
        assert not any(
            row["available"] for row in capabilities.structured_content["workflows"]
        )
        status = await call_result(server, "resolve_free_autostart_status", {})
        assert status.structured_content is not None
        assert status.structured_content["native_connection_verified"] is False
        assert status.structured_content["state"] == "watcher_status_unknown"
        result = await call_result(server, "resolve_free_connection_status", {})
        assert result.is_error
        assert not list(profile.runtime_base.glob("*/inbox"))

    asyncio.run(scenario())


def test_all_calls_pin_backend_and_old_write_key_never_reexecutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    first, second = active_at(profile, "first"), active_at(profile, "second")
    selected = install_state(monkeypatch, profile, first, second)
    mutations: list[str] = []

    def factory(root: Path, **kwargs: Any) -> MCPServer[Any]:
        backend: MCPServer[Any] = MCPServer("fake-native")

        @backend.tool()
        async def mutate(idempotency_key: str) -> dict[str, Any]:
            mutations.append(str(root))
            selected["active"] = second
            await asyncio.sleep(0)
            return {"runtime": str(root), "key": idempotency_key}

        @backend.tool()
        async def read() -> dict[str, Any]:
            return {"runtime": str(root)}

        return backend

    server = ManagedMarkerServer(profile, backend_factory=factory)

    async def scenario() -> None:
        result = await call_result(server, "mutate", {"idempotency_key": "one"})
        assert result.structured_content == {"runtime": first["runtime"], "key": "one"}
        following = await call_result(server, "read", {})
        assert following.structured_content == {"runtime": second["runtime"]}
        replay = await call_result(server, "mutate", {"idempotency_key": "one"})
        assert replay.is_error
        assert "ORIGINAL_GENERATION_REQUIRED" in str(replay.content)
        assert mutations == [first["runtime"]]
        fresh = await call_result(server, "mutate", {"idempotency_key": "two"})
        assert not fresh.is_error
        assert mutations == [first["runtime"], second["runtime"]]

    asyncio.run(scenario())


def test_adopted_static_receipt_cannot_be_repeated_in_new_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    first, second = active_at(profile, "first"), active_at(profile, "second")
    selected = install_state(monkeypatch, profile, first, second)
    selected["active"] = second
    receipt_dir = Path(first["runtime"]) / "receipts"
    receipt_dir.mkdir()
    key = "static-client-write"
    (receipt_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json").write_text(
        '{"status":"completed"}', encoding="utf-8"
    )
    with operation_lock(profile), pytest.raises(
        module.OriginalGenerationRequired, match="prior receipt"
    ):
        module.claim_write_key(profile, second, key)
    assert not list((profile.state_directory / "write-owners").glob("*.json"))


def test_busy_operation_does_not_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    active = active_at(profile, "active")
    install_state(monkeypatch, profile, active, active)
    server = ManagedMarkerServer(profile)
    with operation_lock(profile):
        result = asyncio.run(call_result(server, "resolve_free_connection_status", {}))
    assert result.is_error
    assert "OPERATION_BUSY" in str(result.content)
    assert not (Path(active["runtime"]) / "inbox").exists()


def test_invalid_owner_refuses_before_dispatch(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    active = active_at(profile, "active")
    folder = profile.state_directory / "write-owners"
    folder.mkdir()
    key = "ambiguous-owner"
    path = folder / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
    path.write_text('{"schema":true}', encoding="utf-8")
    with operation_lock(profile), pytest.raises(ValueError, match="ownership"):
        module.claim_write_key(profile, active, key)


def test_cancellation_holds_operation_fence_until_worker_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    active = active_at(profile, "active")
    install_state(monkeypatch, profile, active, active)
    started, release = threading.Event(), threading.Event()

    def worker() -> None:
        started.set()
        assert release.wait(10)

    def factory(root: Path, **kwargs: Any) -> MCPServer[Any]:
        backend: MCPServer[Any] = MCPServer("blocked-worker")

        @backend.tool()
        async def blocking() -> dict[str, Any]:
            await asyncio.to_thread(worker)
            return {"completed": True}

        return backend

    server = ManagedMarkerServer(profile, backend_factory=factory)

    async def scenario() -> None:
        request = asyncio.create_task(server.call_tool("blocking", {}))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            request.cancel()
            await asyncio.sleep(0)
            with pytest.raises(OperationBusy), operation_lock(profile):
                pytest.fail("Canceled request released its running worker fence")
            assert not request.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        with operation_lock(profile):
            pass

    asyncio.run(scenario())


def test_concurrent_catalogue_startup_is_serialized(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    gate = threading.Barrier(2)

    def make() -> int:
        gate.wait(5)
        return len(asyncio.run(ManagedMarkerServer(profile).list_tools()))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(make), pool.submit(make)]
        assert [future.result(20) for future in futures] == [54, 54]
    assert (profile.state_directory / "catalogue-runtime/session.json").is_file()


def test_capabilities_pin_active_session_and_respect_pending_project_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    first, second = active_at(profile, "first"), active_at(profile, "second")
    selected = install_state(monkeypatch, profile, first, second)
    requests: list[str] = []

    def request(self: LuaSnapshotClient, action: str, timeout: float) -> dict[str, Any]:
        requests.append(str(self.root))
        return {"project": {"id": "native-id", "name": "Current"}}

    monkeypatch.setattr(LuaSnapshotClient, "request", request)
    server = ManagedMarkerServer(profile)

    async def scenario() -> None:
        report = await call_result(server, "video_agent_capabilities", {
            "verify_connection": True
        })
        assert not report.is_error
        assert report.structured_content is not None
        assert report.structured_content["session"] == first["session"]
        assert "resolve_free_open_project" in report.structured_content[
            "registered_tools"
        ]
        assert requests == [first["runtime"]]
        selected["active"] = second
        monkeypatch.setattr(module, "pending_project_open", lambda profile: {
            "pending": True
        })
        report = await call_result(server, "video_agent_capabilities", {
            "verify_connection": True
        })
        assert report.structured_content is not None
        assert report.structured_content["session"] == second["session"]
        assert report.structured_content["mutation_blocker"] == (
            "project_open_verification_pending"
        )
        for workflow in report.structured_content["workflows"]:
            if workflow["requires_confirmation"]:
                assert workflow["available"] is False
        assert requests == [first["runtime"], second["runtime"]]
        assert report.content[0].type == "text"
        assert json.loads(report.content[0].text) == report.structured_content

    asyncio.run(scenario())


def open_arguments() -> dict[str, Any]:
    return {
        "project_name": "Existing Project", "expected_project_id": "project-uuid",
        "expected_database": {"DbType": "Disk", "DbName": "Local Library"},
        "folder_path": [], "idempotency_key": "open-one", "confirm": True,
    }


def test_host_open_available_without_native_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = profile_at(tmp_path)
    monkeypatch.setattr(module, "read_active", lambda profile: None)
    calls: list[dict[str, Any]] = []

    class Opener(ProjectOpenService):
        def open_existing(self, **arguments: Any) -> dict[str, Any]:
            calls.append(arguments)
            return {"native_connection_verified": True, "project": "verified"}

    server = ManagedMarkerServer(profile, project_opener=Opener(profile))
    result = asyncio.run(call_result(
        server, "resolve_free_open_project", open_arguments()
    ))
    assert result.structured_content == {
        "native_connection_verified": True, "project": "verified"
    }
    assert calls == [open_arguments()]


def test_host_open_errors_are_structured(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)

    class Opener(ProjectOpenService):
        def open_existing(self, **arguments: Any) -> dict[str, Any]:
            raise ProjectOpenError("AMBIGUOUS_PROJECT", uncertain=False)

    server = ManagedMarkerServer(profile, project_opener=Opener(profile))
    result = asyncio.run(call_result(
        server, "resolve_free_open_project", open_arguments()
    ))
    assert result.is_error
    assert "AMBIGUOUS_PROJECT" in str(result.content)


@pytest.mark.parametrize("corrupt", [False, True])
def test_pending_or_invalid_host_open_blocks_writes_but_allows_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool,
) -> None:
    profile = profile_at(tmp_path)
    active = active_at(profile, "active")
    install_state(monkeypatch, profile, active, active)
    mutations: list[str] = []

    def pending(profile: Profile) -> dict[str, Any]:
        if corrupt:
            raise ProjectOpenError("INVALID_HOST_RECEIPT")
        return {"pending": True}

    monkeypatch.setattr(module, "pending_project_open", pending)

    def factory(root: Path, **kwargs: Any) -> MCPServer[Any]:
        backend: MCPServer[Any] = MCPServer("pending-host")

        @backend.tool(annotations=ToolAnnotations(read_only_hint=False))
        async def mutate(idempotency_key: str) -> dict[str, Any]:
            mutations.append(idempotency_key)
            return {"changed": True}

        @backend.tool(annotations=ToolAnnotations(read_only_hint=True))
        async def read() -> dict[str, Any]:
            return {"read": True}

        return backend

    server = ManagedMarkerServer(profile, backend_factory=factory)

    async def scenario() -> None:
        result = await call_result(server, "mutate", {"idempotency_key": "mutation"})
        assert result.is_error
        assert ("INVALID_HOST_RECEIPT" if corrupt else
                "PROJECT_OPEN_VERIFICATION_PENDING") in str(result.content)
        read = await call_result(server, "read", {})
        assert read.structured_content == {"read": True}
        assert mutations == []
        assert not (profile.state_directory / "write-owners").exists()

    asyncio.run(scenario())


def test_project_open_key_cannot_be_claimed_by_native_write(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    active = active_at(profile, "active")
    key = "host-operation"
    folder = profile.state_directory / "project-open-receipts"
    folder.mkdir()
    (folder / f"{hashlib.sha256(key.encode()).hexdigest()}.json").write_text(
        "{}", encoding="utf-8"
    )
    with operation_lock(profile), pytest.raises(
        module.OriginalGenerationRequired, match="host project-open"
    ):
        module.claim_write_key(profile, active, key)
    assert not (profile.state_directory / "write-owners").exists()


def test_host_open_cancellation_drains_worker(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    started, release = threading.Event(), threading.Event()

    class Opener(ProjectOpenService):
        def open_existing(self, **arguments: Any) -> dict[str, Any]:
            started.set()
            assert release.wait(10)
            return {"finished": True}

    server = ManagedMarkerServer(profile, project_opener=Opener(profile))

    async def scenario() -> None:
        request = asyncio.create_task(server.call_tool(
            "resolve_free_open_project", open_arguments()
        ))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            request.cancel()
            await asyncio.sleep(0)
            assert not request.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await request

    asyncio.run(scenario())
