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
from mcp.types import CallToolResult

import mcp_server.managed_marker as module
from mcp_server.managed_marker import ManagedMarkerServer
from providers.resolve.marker_supervisor import OperationBusy, Profile, operation_lock
from providers.resolve.marker_transport import prepare_marker


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
        assert len(tools) == 52
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
        assert [future.result(20) for future in futures] == [52, 52]
    assert (profile.state_directory / "catalogue-runtime/session.json").is_file()
