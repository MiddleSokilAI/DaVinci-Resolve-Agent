"""Canonical commands, backups and uncertain-write fences through SDK proxies."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from test_resolve_bridge_writes import FakeResolve, _command

from agent.client import BridgeProtocolError
from bridges.resolve import ResolveBridge as bridge
from bridges.resolve.SDKCapabilities import augment_sdk_state
from bridges.resolve.SDKTracker import SDKTracker
from providers.resolve.marker_supervisor import Profile
from providers.resolve.marker_transport import prepare_marker
from providers.resolve.sdk_api import SDK_CONSTANTS, SDK_METHODS
from providers.resolve.sdk_command_client import SDKCommandClient
from providers.resolve.sdk_worker import SDKWorker


class Channel:
    def __init__(self, native: Any) -> None:
        self.native = native
        self.objects: dict[str, Any] = {}
        self.catalogue: dict[str, Any] = {}
        self.calls = 0

    def pack(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, float, int)):
            return value
        if isinstance(value, (list, tuple)):
            return {"a": [self.pack(item) for item in value]}
        if isinstance(value, dict):
            return {"m": [[key, self.pack(item)] for key, item in value.items()]}
        key = str(id(value))
        self.objects[key] = value
        self.catalogue[key] = {
            "kind": type(value).__name__.removeprefix("Fake"),
            "methods": sorted(
                method
                for method in SDK_METHODS
                if callable(getattr(value, method, None))
            ),
        }
        return {"r": key}

    def unpack(self, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "r" in value:
            return self.objects[value["r"]]
        if "a" in value:
            return [self.unpack(item) for item in value["a"]]
        return {key: self.unpack(item) for key, item in value["m"]}

    def exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.catalogue = {}
        try:
            value = (
                self.native
                if payload["kind"] == "root"
                else getattr(self.objects[payload["object"]], payload["method"])(
                    *self.unpack(payload["arguments"])
                )
            )
            return {"result": self.pack(value), "objects": self.catalogue}
        except Exception as error:
            return {"error": str(error)}


def test_canonical_backup_readback_and_exactly_once_replay(tmp_path: Path) -> None:
    native = FakeResolve()
    channels: list[Channel] = []

    def factory(operation: dict[str, Any], timeout: float) -> Channel:
        channel = Channel(native)
        channels.append(channel)
        return channel

    client = SDKCommandClient(tmp_path, "a" * 32, channel_factory=factory)
    arguments = {"name": "SDK Test"}
    result = client.request(
        provider="resolve",
        action="create_timeline",
        arguments=arguments,
        idempotency_key="same",
        create_backup=True,
    )
    assert result["timeline"]["timeline_id"]
    assert Path(result["backup_path"]).is_file()
    assert native.project_manager.export_count == 1
    assert (
        client.request(
            provider="resolve",
            action="create_timeline",
            arguments=arguments,
            idempotency_key="same",
            create_backup=True,
        )
        == result
    )
    assert len(channels) == 1
    assert native.project_manager.export_count == 1
    with pytest.raises(BridgeProtocolError, match="original"):
        client.request(
            provider="resolve",
            action="create_timeline",
            arguments={"name": "Changed"},
            idempotency_key="same",
            create_backup=True,
        )
    assert len(channels) == 1


def setup_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SDKWorker, FakeResolve, dict[str, Any]]:
    import providers.resolve.sdk_worker as module

    root = tmp_path / "runtime"
    root.mkdir()
    profile = Profile(tmp_path / "profile.json", root, (), enabled=True)
    native_root = root / "native"
    prepare_marker(native_root, [])
    session = json.loads((native_root / "session.json").read_text())["session"]
    active = {
        "runtime": str(native_root),
        "session": session,
        "generation": session,
        "phase": "connected",
    }
    native = FakeResolve()
    worker = SDKWorker(profile)
    monkeypatch.setattr(worker.connection, "ensure", lambda **kwargs: active)
    monkeypatch.setattr(worker, "_external_available", lambda active: False)
    monkeypatch.setattr(module, "read_active", lambda profile: active)
    monkeypatch.setattr(module, "LuaSnapshotClient", lambda root: SimpleNamespace(
        request=lambda *args: {"project": {"id": native.project.GetUniqueId()}},
    ))
    monkeypatch.setattr(
        module,
        "SDKCommandClient",
        lambda root, session, **kwargs: SDKCommandClient(
            root,
            session,
            **kwargs,
            channel_factory=lambda operation, timeout: Channel(native),
        ),
    )
    return worker, native, active


def queued(worker: SDKWorker, action: str, arguments: dict[str, Any], key: str) -> Path:
    identity = str(uuid4())
    path = worker.directories["commands"] / (identity + ".json")
    bridge.atomic_write_json(
        path, _command(identity, action, arguments, idempotency_key=key)
    )
    return path


def test_worker_replays_terminal_result_without_second_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, native, active = setup_worker(tmp_path, monkeypatch)
    worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert not (Path(active["runtime"]) / "inbox").exists()
    monkeypatch.setattr(worker, "_select_route", lambda active: pytest.fail(
        "Completed replay probed or contacted a native provider"
    ))
    worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert native.project_manager.export_count == 1


def test_previous_sdk_owner_is_upgraded_before_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server.managed_marker import OriginalGenerationRequired, claim_write_key

    worker, native, active = setup_worker(tmp_path, monkeypatch)
    worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    path = worker.profile.state_directory / "write-owners" / (
        hashlib.sha256(b"key").hexdigest() + ".json"
    )
    previous = json.loads(path.read_text())
    previous.pop("route")
    previous["schema"] = 1
    bridge.atomic_write_json(path, previous)
    worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert json.loads(path.read_text())["schema"] == 2
    with pytest.raises(OriginalGenerationRequired, match="route"):
        claim_write_key(worker.profile, active, "key")
    assert native.project_manager.export_count == 1


def test_sdk_reservation_without_dispatch_can_resume_same_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server.managed_marker import claim_write_key

    worker, native, active = setup_worker(tmp_path, monkeypatch)
    claim_write_key(worker.profile, active, "key", route="sdk")
    worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert native.project_manager.export_count == 1


def test_recent_client_work_prevents_an_immediate_background_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import providers.resolve.sdk_worker as module

    worker, _, active = setup_worker(tmp_path, monkeypatch)
    active["process"] = {}
    (Path(active["runtime"]) / "sdk-template.preset").touch()
    monkeypatch.setattr(module, "process_alive", lambda process: True)
    queued(worker, "get_current_project", {}, "read")
    stop = threading.Event()
    waits = 0

    def wait(timeout: float) -> bool:
        nonlocal waits
        waits += 1
        if waits == 2:
            stop.set()
        return stop.is_set()

    monkeypatch.setattr(stop, "wait", wait)
    worker.run(stop)
    assert waits == 2
    assert not list(worker.directories["commands"].glob("*.json"))


def test_transient_idle_snapshot_error_does_not_kill_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import providers.resolve.sdk_worker as module

    worker, _, _ = setup_worker(tmp_path, monkeypatch)
    stop = threading.Event()
    calls = 0

    def read(profile: Profile) -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("STATE_UNREADABLE")
        stop.set()
        return None

    monkeypatch.setattr(module, "read_active", read)
    monkeypatch.setattr(stop, "wait", lambda seconds: None)
    worker.run(stop)
    assert calls == 2
    assert not list(worker.directories["commands"].glob("*.json"))


def test_status_sharing_violation_preserves_a_completed_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import providers.resolve.sdk_worker as module

    worker, native, _ = setup_worker(tmp_path, monkeypatch)
    publish = module.atomic_json

    def failed_status(path: Path, value: Any) -> None:
        if path == worker.status_path:
            raise PermissionError("Windows sharing violation")
        publish(path, value)

    monkeypatch.setattr(module, "atomic_json", failed_status)
    path = queued(worker, "create_timeline", {"name": "One"}, "key")
    worker.process(path)
    assert json.loads((worker.directories["responses"] / path.name).read_text())[
        "status"
    ] == "success"
    worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert native.project_manager.export_count == 1


@pytest.mark.parametrize("invalid", [False, True])
def test_background_read_does_not_compete_with_pending_project_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: bool,
) -> None:
    import providers.resolve.sdk_worker as module

    worker, _, active = setup_worker(tmp_path, monkeypatch)
    active["process"] = {}
    (Path(active["runtime"]) / "sdk-template.preset").touch()
    monkeypatch.setattr(module, "process_alive", lambda process: True)

    def pending(profile: Profile) -> dict[str, Any]:
        if invalid:
            raise ValueError("unreadable host receipt")
        return {"pending": True}

    monkeypatch.setattr(module, "pending_project_open", pending)
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda seconds: stop.set())
    worker.run(stop)
    assert not list(worker.directories["commands"].glob("*.json"))


def test_queued_commands_are_processed_in_arrival_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _, _ = setup_worker(tmp_path, monkeypatch)
    first = queued(worker, "get_current_project", {}, "first")
    second = queued(worker, "get_capabilities", {}, "second")
    first = first.rename(first.with_name("ffffffff-ffff-4fff-8fff-ffffffffffff.json"))
    second = second.rename(
        second.with_name("00000000-0000-4000-8000-000000000000.json")
    )
    os.utime(first, ns=(1000000000, 1000000000))
    os.utime(second, ns=(2000000000, 2000000000))
    seen: list[Path] = []
    stop = threading.Event()

    def process(path: Path) -> None:
        seen.append(path)
        path.unlink()
        if len(seen) == 2:
            stop.set()

    monkeypatch.setattr(worker, "process", process)
    worker.run(stop)
    assert seen == [first, second]


def test_sdk_key_cannot_be_reused_by_legacy_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server.managed_marker import OriginalGenerationRequired, claim_write_key

    worker, native, active = setup_worker(tmp_path, monkeypatch)
    worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    with pytest.raises(OriginalGenerationRequired, match="route"):
        claim_write_key(worker.profile, active, "key")
    assert native.project_manager.export_count == 1


def test_static_marker_receipt_in_current_session_blocks_sdk_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server.managed_marker import OriginalGenerationRequired

    worker, native, active = setup_worker(tmp_path, monkeypatch)
    receipts = Path(active["runtime"]) / "receipts"
    receipts.mkdir(exist_ok=True)
    (receipts / (hashlib.sha256(b"key").hexdigest() + ".json")).write_text(
        '{"status":"completed"}'
    )
    with pytest.raises(OriginalGenerationRequired, match="receipt"):
        worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert native.project_manager.export_count == 0


def test_post_command_state_publication_failure_keeps_known_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, native, active = setup_worker(tmp_path, monkeypatch)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise OSError("state publication failed after canonical readback")

    monkeypatch.setattr(bridge, "publish_bridge_state", failed)
    first = queued(worker, "create_timeline", {"name": "One"}, "key")
    worker.process(first)
    response = json.loads((worker.directories["responses"] / first.name).read_text())
    assert response["status"] == "success"
    worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert not (Path(active["runtime"]) / "inbox").exists()
    assert native.project_manager.export_count == 1


def test_guard_publication_failure_does_not_poison_subsequent_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from providers.resolve.marker_transport import MarkerSlot

    worker, native, active = setup_worker(tmp_path, monkeypatch)
    publish = MarkerSlot.publish

    def failed(self: MarkerSlot, deadline: float) -> None:
        raise OSError("failed before SDK dispatch")

    monkeypatch.setattr(MarkerSlot, "publish", failed)
    first = queued(worker, "create_timeline", {"name": "Rejected"}, "key")
    worker.process(first)
    response = json.loads((worker.directories["responses"] / first.name).read_text())
    assert response["error"]["code"] == "SDK_PRE_DISPATCH_FAILED"
    assert native.project_manager.export_count == 0
    monkeypatch.setattr(MarkerSlot, "publish", publish)
    worker.process(queued(worker, "get_capabilities", {}, "read"))
    worker.process(queued(worker, "create_timeline", {"name": "Allowed"}, "new-key"))
    worker.process(queued(worker, "create_timeline", {"name": "Rejected"}, "key"))
    assert not (Path(active["runtime"]) / "inbox").exists()
    assert native.project_manager.export_count == 1


def test_marker_owned_key_cannot_enter_sdk_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server.managed_marker import OriginalGenerationRequired, claim_write_key

    worker, native, active = setup_worker(tmp_path, monkeypatch)
    claim_write_key(worker.profile, active, "key")
    with pytest.raises(OriginalGenerationRequired, match="route"):
        worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert native.project_manager.export_count == 0


@pytest.mark.parametrize("matching", [False, True])
def test_external_helper_checks_native_project_and_preserves_terminal_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, matching: bool,
) -> None:
    import sys

    from bridges.resolve import ResolveSDKCommand as helper

    native = FakeResolve()
    worker, _, _ = setup_worker(tmp_path, monkeypatch)
    path = queued(worker, "create_timeline", {"name": "External"}, "key")
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript", SimpleNamespace(
        scriptapp=lambda name: native,
    ))
    monkeypatch.setattr(sys, "argv", [
        "ResolveSDKCommand.py", "--runtime", str(worker.root), "--command", path.stem,
        "--expected-project-id", native.project.GetUniqueId() if matching else "other",
    ])

    def failed(*args: Any, **kwargs: Any) -> None:
        raise OSError("optional state publication failed")

    monkeypatch.setattr(bridge, "publish_bridge_state", failed)
    assert helper.main() == (0 if matching else 4)
    assert native.project_manager.export_count == (1 if matching else 0)
    assert path.exists() is not matching
    if matching:
        response = json.loads((worker.directories["responses"] / path.name).read_text())
        assert response["status"] == "success"
        trace = json.loads(
            (worker.directories["state"] / (path.stem + ".sdk.json")).read_text()
        )
        assert trace["mutations_sent"] > 0


def test_unknown_native_write_retains_guard_and_blocks_all_new_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, native, active = setup_worker(tmp_path, monkeypatch)
    original = native.project.media_pool.CreateEmptyTimeline

    def failed(name: str) -> Any:
        original(name)
        raise RuntimeError("lost native response after edit")

    monkeypatch.setattr(native.project.media_pool, "CreateEmptyTimeline", failed)
    with pytest.raises(RuntimeError):
        worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert (Path(active["runtime"]) / "inbox").is_dir()
    receipt = (
        worker.profile.state_directory
        / "sdk-write-owners"
        / (hashlib.sha256(b"key").hexdigest() + ".json")
    )
    assert json.loads(receipt.read_text())["phase"] == "uncertain"
    for action, args, key in [
        ("get_current_project", {}, "read"),
        ("create_timeline", {"name": "Two"}, "new-key"),
    ]:
        with pytest.raises(RuntimeError, match="REQUIRES_RECOVERY"):
            worker.process(queued(worker, action, args, key))
    assert native.project_manager.export_count == 1


def test_expired_command_does_not_bootstrap_or_claim_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, native, active = setup_worker(tmp_path, monkeypatch)
    path = queued(worker, "create_timeline", {"name": "Expired"}, "key")
    command = json.loads(path.read_text())
    command["expires_at"] = "2000-01-01T00:00:00+00:00"
    bridge.atomic_write_json(path, command)
    monkeypatch.setattr(
        worker.connection, "ensure", lambda: pytest.fail("bootstrapped")
    )
    with pytest.raises(ValueError):
        worker.process(path)
    assert not (worker.profile.state_directory / "write-owners").exists()
    assert native.project_manager.export_count == 0


def test_external_binding_discovery_is_shared_and_version_scoped(
    tmp_path: Path,
) -> None:
    native = FakeResolve()
    state = bridge.collect_bridge_state(native)
    augment_sdk_state(state, native, tmp_path, discover=True, transport="python_sdk")
    assert state["capabilities"]["timeline.create"] is True
    assert state["capabilities"]["subtitle.auto_caption"] is False
    assert state["native_connection"]["transport"] == "python_sdk"
    # Corrupt optional discovery cache never becomes an operation blocker.
    (tmp_path / "sdk-surface.json").write_text("{broken")
    augment_sdk_state(state, native, tmp_path)


def test_external_tracker_preserves_sdk_references_and_counts_failed_dispatch() -> None:
    native = FakeResolve()
    tracker = SDKTracker()
    root = tracker.wrap(native)
    project = root.GetProjectManager().GetCurrentProject()
    timeline = project.GetMediaPool().CreateEmptyTimeline("Tracked")
    assert project.SetCurrentTimeline(timeline)
    assert tracker.mutations_sent == 2
    assert project.GetCurrentTimeline().GetUniqueId() == timeline.GetUniqueId()


def test_external_failure_never_falls_back_to_lua_after_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, native, active = setup_worker(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "_external_available", lambda active: True)

    def failed(*args: Any) -> None:
        raise RuntimeError("external SDK response lost")

    monkeypatch.setattr(worker, "_external", failed)
    with pytest.raises(RuntimeError, match="external SDK response lost"):
        worker.process(queued(worker, "create_timeline", {"name": "One"}, "key"))
    assert (Path(active["runtime"]) / "inbox").is_dir()
    assert native.project_manager.export_count == 0


def test_canonical_sdk_calls_are_covered_by_the_adapter_allowlist() -> None:
    import ast

    tree = ast.parse(Path(bridge.__file__).read_text(encoding="utf-8"))
    calls = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr[0].isupper()
        and node.attr != "JSONDecodeError"
    }
    assert calls <= SDK_METHODS | SDK_CONSTANTS
