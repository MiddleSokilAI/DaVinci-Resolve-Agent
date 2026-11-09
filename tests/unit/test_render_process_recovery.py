"""Exited render starts never replay, and never lose original packet evidence."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from test_sdk_command_client import queued, setup_worker

from bridges.resolve import ResolveBridge as bridge
from providers.resolve import render_process_recovery as module
from providers.resolve.lua_editing import atomic_json
from providers.resolve.marker_transport import MarkerSlot
from providers.resolve.windows_bootstrap import _official_executable


def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    worker, native, active = setup_worker(tmp_path, monkeypatch)
    root = Path(active["runtime"])
    path = queued(worker, "start_render_job", {"job_id": "owned-job"}, "original-start")
    command = json.loads(path.read_text())
    identity = UUID(command["command_id"]).hex
    fingerprint = bridge._request_fingerprint(command)
    key = hashlib.sha256(b"original-start").hexdigest()
    owner_path = worker.profile.state_directory / "sdk-write-owners" / (key + ".json")
    owner_path.parent.mkdir(parents=True)
    owner = dict(command_id=command["command_id"], fingerprint=fingerprint,
                 phase="uncertain", runtime=str(root), session=active["session"],
                 generation=active["generation"], transport="lua_marker_preset")
    atomic_json(owner_path, owner)
    ledger_path = worker.root / "state/sdk-operations" / (key + ".json")
    ledger_path.parent.mkdir(parents=True)
    ledger = {k: owner[k] for k in ("command_id", "fingerprint", "phase", "session")}
    atomic_json(ledger_path, ledger)
    active["process"] = dict(pid=123, creation_time="638999000000000000",
        executable_path=_official_executable(), window_handle=456,
        window_title="DaVinci Resolve - Original", window_state="normal",
        project_name_hint="Original")
    generations = worker.profile.state_directory / "generations"
    generations.mkdir()
    atomic_json(generations / (active["generation"] + ".json"), active)
    response = dict(protocol_version="1.0", command_id=command["command_id"],
        status="error", result=None, started_at=command["created_at"],
        finished_at=command["created_at"], warnings=[],
        error=dict(code="TIMEOUT", message="uncertain", details={}, retryable=True))
    atomic_json(worker.root / "responses" / path.name, response)
    path.replace(worker.root / "failed" / path.name)
    operation = dict(id=identity, fingerprint=fingerprint, action="start_render_job",
                     create_backup=True, allow_destructive=False, expires=123)
    call = dict(kind="call", method="StartRendering", object="project",
                arguments={"a": [{"a": ["owned-job"]}, False]})
    wire = json.dumps(dict(operation=operation, call=call), separators=(",", ":"))
    sdk_id = uuid4().hex
    packet = dict(session=active["session"], id=uuid4().hex, action="ping",
        expires=123, arguments={"sdk": dict(id=sdk_id, seq=1, count=1, data=wire)})
    rpc_root = root / "sdk-rpc"
    rpc_root.mkdir()
    rpc = MarkerSlot(rpc_root, packet, 4096)
    rpc.publish(time.monotonic() + 10)
    guard = MarkerSlot(root, dict(session=active["session"], id=identity,
                      action="sdk_operation", expires=123), 4096)
    guard.publish(time.monotonic() + 10)
    trace = dict(session=active["session"], id=sdk_id, phase="uncertain",
        operation=operation, call=call, packet=packet,
        request_sha256=hashlib.sha256(wire.encode("ascii")).hexdigest())
    trace_path = root / "sdk-trace" / (sdk_id + ".json")
    trace_path.parent.mkdir()
    atomic_json(trace_path, trace)
    monkeypatch.setattr(module, "_process_exit_proof", lambda process: {
        "state": "original_pid_absent", "original_process": process,
    })
    monkeypatch.setattr(worker.connection, "ensure", lambda **kw: pytest.fail(
        "Retired render replay must not connect or open any project"))
    return worker, native, root, owner_path, ledger_path, command, trace_path


def packet_hashes(root: Path) -> list[tuple[str, str]]:
    return sorted((p.name, hashlib.sha256(p.read_bytes()).hexdigest())
                  for p in root.iterdir() if p.is_file())


def test_retirement_archives_packets_and_replay_never_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, native, root, owner_path, ledger_path, command, trace_path = fixture(
        tmp_path, monkeypatch)
    before = [packet_hashes(root / p) for p in ("inbox", "sdk-rpc/inbox")]
    trace_before = trace_path.read_bytes()
    module.recover_exited_render_starts(worker.profile)
    owner = json.loads(owner_path.read_text())
    assert owner["phase"] == "completed"
    assert owner["response"]["error"]["code"] == "ORIGINAL_RENDER_PROCESS_EXITED"
    assert owner["response"]["error"]["retryable"] is False
    assert json.loads(ledger_path.read_text())["response"] == owner["response"]
    archive = root / ("retired-render-" + UUID(command["command_id"]).hex)
    assert before == [packet_hashes(archive / p / "inbox")
                      for p in ("operation", "rpc")]
    assert not (root / "inbox").exists() and not (root / "sdk-rpc/inbox").exists()
    assert trace_path.read_bytes() == trace_before
    assert (worker.root / "failed" / (command["command_id"] + ".json")).is_file()
    first = owner_path.read_bytes()
    module.recover_exited_render_starts(worker.profile)
    assert owner_path.read_bytes() == first
    retry = queued(worker, "start_render_job", {"job_id": "owned-job"},
                   "original-start")
    worker.process(retry)
    response = json.loads((worker.root / "responses" / retry.name).read_text())
    assert response["error"]["code"] == "ORIGINAL_RENDER_PROCESS_EXITED"
    assert native.project_manager.export_count == 0


@pytest.mark.parametrize("failure", ["live", "unknown", "fingerprint", "ledger",
    "packet", "trace", "generation", "late", "unsupported"])
def test_uncertain_or_changed_evidence_stays_fenced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    worker, _, root, owner_path, ledger_path, command, trace_path = fixture(
        tmp_path, monkeypatch)
    if failure in {"live", "unknown"}:
        def refuse(process: Any) -> Any:
            reason = "STILL_RUNNING" if failure == "live" else "ACCESS_DENIED"
            raise RuntimeError(reason)
        monkeypatch.setattr(module, "_process_exit_proof", refuse)
    elif failure in {"fingerprint", "ledger", "generation"}:
        path = ledger_path if failure == "ledger" else owner_path
        value = json.loads(path.read_text())
        value["generation" if failure == "generation" else "fingerprint"] = "0" * 32
        atomic_json(path, value)
    elif failure in {"trace", "packet"}:
        value = json.loads(trace_path.read_text())
        value["call" if failure == "trace" else "packet"] = {}
        atomic_json(trace_path, value)
    elif failure == "late":
        (root / ("sdk-" + trace_path.stem + ".ready.preset")).write_bytes(b"late")
    else:
        path = worker.root / "failed" / (command["command_id"] + ".json")
        value = json.loads(path.read_text())
        value.update(action="create_timeline", arguments={"name": "Uncertain"})
        atomic_json(path, value)
    before = owner_path.read_bytes(), ledger_path.read_bytes()
    if failure == "unsupported":
        module.recover_exited_render_starts(worker.profile)
    else:
        with pytest.raises((RuntimeError, ValueError, OSError)):
            module.recover_exited_render_starts(worker.profile)
    assert before == (owner_path.read_bytes(), ledger_path.read_bytes())
    assert (root / "inbox").is_dir() and (root / "sdk-rpc/inbox").is_dir()


@pytest.mark.parametrize("crash_after", [1, 2, 3])
def test_crash_resume_keeps_history_and_never_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after: int,
) -> None:
    worker, _, root, owner_path, _, _, _ = fixture(tmp_path, monkeypatch)
    archive = module._archive
    atomic = atomic_json
    writes = 0

    def archive_then_crash(slot: Any, destination: Path) -> None:
        nonlocal writes
        archive(slot, destination)
        writes += 1
        if writes == crash_after:
            raise OSError("synthetic crash after packet archive")

    def commit_then_crash(path: Path, value: Any) -> None:
        atomic(path, value)
        if crash_after == 3 and "sdk-operations" in str(path):
            raise OSError("synthetic crash after ledger commit")

    monkeypatch.setattr(module, "_archive", archive_then_crash)
    monkeypatch.setattr(module, "atomic_json", commit_then_crash)
    with pytest.raises(OSError):
        module.recover_exited_render_starts(worker.profile)
    monkeypatch.setattr(module, "_archive", archive)
    monkeypatch.setattr(module, "atomic_json", atomic)
    module.recover_exited_render_starts(worker.profile)
    assert json.loads(owner_path.read_text())["phase"] == "completed"
    assert not (root / "inbox").exists()


def test_same_key_different_job_refuses_before_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, *_ = fixture(tmp_path, monkeypatch)
    module.recover_exited_render_starts(worker.profile)
    retry = queued(worker, "start_render_job", {"job_id": "other"}, "original-start")
    with pytest.raises(RuntimeError, match="SDK_IDEMPOTENCY_CONFLICT"):
        worker.process(retry)


def test_create_preflight_recovers_without_opening_any_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from providers.resolve import project_open_service as opening

    worker, native, root, owner_path, *_ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(opening, "runtime_history", lambda profile: [root])
    opening.ProjectOpenService(worker.profile)._native_clear("f" * 64)
    assert json.loads(owner_path.read_text())["phase"] == "completed"
    assert native.project_manager.export_count == 0


def test_supervisor_rotation_accepts_only_retired_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from providers.resolve import marker_supervisor as supervisor

    worker, _, root, owner_path, *_ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(supervisor, "runtime_history", lambda profile: [root])
    assert supervisor.Supervisor(worker.profile)._audit_rotation() == [root]
    assert json.loads(owner_path.read_text())["phase"] == "completed"


@pytest.mark.parametrize("state", ["absent", "denied", "live", "reused", "exited",
    "times_failed", "exit_failed", "absent_but_listed", "enum_failed"])
def test_os_exit_probe_never_treats_denied_or_failed_queries_as_exit(
    monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    from providers.resolve import windows_bootstrap as bootstrap

    process: dict[str, Any] = dict(pid=123, creation_time="638999000000000000",
        executable_path=_official_executable(), window_handle=456,
        window_title="DaVinci Resolve - Original", window_state="normal",
        project_name_hint="Original")
    monkeypatch.setattr(bootstrap, "_official_executable",
                        lambda: process["executable_path"])
    closed = []

    class Function:
        def __init__(self, call: Any) -> None:
            self.call = call

        def __call__(self, *args: Any) -> Any:
            return self.call(*args)

    def times(handle: Any, created: Any, *rest: Any) -> bool:
        token = int(process["creation_time"]) - 504911232000000000
        if state == "reused":
            token += 1
        value = ctypes.cast(created, ctypes.POINTER(wintypes.FILETIME)).contents
        value.dwHighDateTime, value.dwLowDateTime = token >> 32, token & 0xFFFFFFFF
        return state != "times_failed"

    def exit_code(handle: Any, out: Any) -> bool:
        ctypes.cast(out, ctypes.POINTER(wintypes.DWORD)).contents.value = (
            0 if state == "exited" else 259
        )
        return state != "exit_failed"

    absent_states = {"absent", "denied", "absent_but_listed", "enum_failed"}

    def enumerate_processes(values: Any, size: int, out: Any) -> bool:
        values[0] = 123 if state == "absent_but_listed" else 456
        ctypes.cast(out, ctypes.POINTER(wintypes.DWORD)).contents.value = 4
        return state != "enum_failed"

    kernel = SimpleNamespace(
        OpenProcess=Function(lambda *a: 0 if state in absent_states else 99),
        GetProcessTimes=Function(times), GetExitCodeProcess=Function(exit_code),
        K32EnumProcesses=Function(enumerate_processes),
        CloseHandle=Function(lambda handle: closed.append(handle)),
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel)
    monkeypatch.setattr(ctypes, "get_last_error",
                        lambda: 5 if state == "denied" else 87)
    if state in {"denied", "live", "times_failed", "exit_failed",
                 "absent_but_listed", "enum_failed"}:
        with pytest.raises(RuntimeError):
            module._process_exit_proof(process)
    else:
        assert module._process_exit_proof(process)["original_process"] == process
    assert closed == ([] if state in absent_states else [99])
