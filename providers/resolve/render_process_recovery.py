"""Retire an uncertain render start only after its exact native process exits.

This records an error, never a successful render or permission to replay it.
Caller holds operation.lock. Original packets and records remain archived.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from bridges.resolve import ResolveBridge as bridge
from providers.resolve.lua_editing import atomic_json
from providers.resolve.marker_supervisor import (
    Profile,
    _object,
    _runtime,
    _validate_runtime_policy,
)
from providers.resolve.marker_transport import MarkerSlot
from providers.resolve.windows_bootstrap import _validate_process

_LIMIT = 4 * 1024 * 1024
_TAG = "render_start_process_exited_v1"


def _guard(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (
            part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise RuntimeError("LINKED_RENDER_RECOVERY_EVIDENCE")


def _read(path: Path) -> dict[str, Any]:
    _guard(path)
    with path.open("rb") as stream:
        raw = stream.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise RuntimeError("RENDER_RECOVERY_EVIDENCE_BOUND")
    value = json.loads(raw, object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise ValueError("Invalid render recovery evidence")
    return value


def _process_exit_proof(process: dict[str, Any]) -> dict[str, Any]:
    """Access denied and failed process queries are unknown, not exit proof."""
    process = _validate_process(process)
    if sys.platform != "win32":
        raise RuntimeError("RENDER_PROCESS_EXIT_UNVERIFIED")
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (
        ctypes.POINTER(wintypes.FILETIME),
    ) * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
    )
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    ctypes.set_last_error(0)
    handle = kernel.OpenProcess(0x1000, False, process["pid"])
    if not handle:
        if ctypes.get_last_error() == 87:
            # Confirm absence independently. A failed OpenProcess alone is
            # not sufficient evidence to retire an uncertain native write.
            kernel.K32EnumProcesses.argtypes = (
                ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            )
            kernel.K32EnumProcesses.restype = wintypes.BOOL
            identifiers = (wintypes.DWORD * 65536)()
            used = wintypes.DWORD()
            if (not kernel.K32EnumProcesses(
                identifiers, ctypes.sizeof(identifiers), ctypes.byref(used)
            ) or used.value >= ctypes.sizeof(identifiers)
                    or used.value % ctypes.sizeof(wintypes.DWORD)):
                raise RuntimeError("RENDER_PROCESS_EXIT_UNVERIFIED")
            count = used.value // ctypes.sizeof(wintypes.DWORD)
            if process["pid"] not in identifiers[:count]:
                return {"state": "original_pid_absent", "original_process": process,
                        "enumerated_process_count": count}
        raise RuntimeError("RENDER_PROCESS_EXIT_UNVERIFIED")
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        code = wintypes.DWORD()
        if (not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in times))
                or not kernel.GetExitCodeProcess(handle, ctypes.byref(code))):
            raise RuntimeError("RENDER_PROCESS_EXIT_UNVERIFIED")
        created = str(
            (times[0].dwHighDateTime << 32) + times[0].dwLowDateTime
            + 504911232000000000
        )
        if created == process["creation_time"] and code.value == 259:
            raise RuntimeError("ORIGINAL_RENDER_PROCESS_STILL_RUNNING")
        return {"state": "original_process_exited", "original_process": process,
                "observed_creation_time": created, "exit_code": code.value}
    finally:
        kernel.CloseHandle(handle)


def _packet(root: Path, archived: Path, session: str) -> MarkerSlot:
    _guard(root / "inbox")
    _guard(archived / "inbox")
    present = (root / "inbox").exists()
    retained = (archived / "inbox").exists()
    if present == retained:
        raise RuntimeError("RENDER_RECOVERY_PACKET_OWNERSHIP_CHANGED")
    return MarkerSlot.occupied(root if present else archived, session, 4096)


def _archive(slot: MarkerSlot, destination: Path) -> None:
    if slot.root == destination:
        return
    _guard(destination)
    destination.mkdir(parents=True, exist_ok=True)
    # Exact validated packet, moved intact; no evidence bits are removed.
    slot.path.rename(destination / "inbox")


def _retire(profile: Profile, path: Path, owner: dict[str, Any]) -> None:
    key = path.stem
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid SDK owner key")
    root = _runtime(profile, owner["runtime"])
    session, generation = owner["session"], owner["generation"]
    if (owner.get("transport") != "lua_marker_preset"
            or UUID(session).hex != session or UUID(generation).hex != generation):
        raise ValueError("Invalid render recovery binding")
    command_id = str(UUID(owner["command_id"]))
    identity = UUID(command_id).hex
    command_path = profile.runtime_base / "failed" / (command_id + ".json")
    _guard(command_path)
    if not command_path.exists():
        return  # Prepared/crashed writes without a failed command stay fenced.
    # Historical validation only: this command is never dispatched again.
    command = bridge.validate_command(_read(command_path), allow_expired=True)
    if command["action"] != "start_render_job":
        return  # Other uncertain writes remain fenced by their owning recovery.
    if (hashlib.sha256(command["idempotency_key"].encode()).hexdigest() != key
            or command["command_id"] != command_id
            or bridge._request_fingerprint(command) != owner["fingerprint"]):
        raise ValueError("Render owner intent mismatch")
    binding = _read(profile.state_directory / "generations" / (generation + ".json"))
    metadata = _read(root / "session.json")
    _validate_runtime_policy(profile, metadata)
    if (binding.get("runtime") != str(root)
            or binding.get("generation") != generation
            or binding.get("session") != session or metadata.get("session") != session):
        raise ValueError("Render generation mismatch")
    process = _validate_process(binding["process"])
    proofs = [_process_exit_proof(process), _process_exit_proof(process)]
    ledger_path = profile.runtime_base / "state/sdk-operations" / (key + ".json")
    ledger = _read(ledger_path)
    current_ledger = ledger
    if any(ledger.get(k) != owner.get(k) for k in (
        "session", "command_id", "fingerprint"
    )):
        raise ValueError("Render ledger mismatch")
    journal_path = profile.state_directory / "render-exit-recovery" / (key + ".json")
    _guard(journal_path)
    journal = _read(journal_path) if journal_path.exists() else None
    if journal is not None:
        if (journal.get("kind") != _TAG or journal.get("owner") != owner
                or journal.get("command") != command):
            raise ValueError("Render recovery journal mismatch")
        ledger = journal["ledger"]
    archive = root / ("retired-render-" + identity)
    guard = _packet(root, archive / "operation", session)
    if (guard.identity != identity or guard.request["action"] != "sdk_operation"
            or set(guard.request) != {"session", "id", "action", "expires"}):
        raise ValueError("Render guard mismatch")
    rpc = _packet(root / "sdk-rpc", archive / "rpc", session)
    sdk = rpc.request.get("arguments", {}).get("sdk", {})
    if (rpc.request.get("action") != "ping" or sdk.get("seq") != 1
            or sdk.get("count") != 1 or not isinstance(sdk.get("id"), str)
            or len(sdk["id"]) != 32 or not isinstance(sdk.get("data"), str)):
        raise ValueError("Render RPC packet mismatch")
    wire = json.loads(sdk["data"], object_pairs_hook=_object)
    trace = _read(root / "sdk-trace" / (UUID(sdk["id"]).hex + ".json"))
    operation = wire.get("operation", {})
    job_argument = {"a": [command["arguments"]["job_id"]]}
    expected_call = {"kind": "call", "method": "StartRendering",
                     "arguments": {"a": [job_argument, False]},
                     "object": wire.get("call", {}).get("object")}
    if (operation.get("id") != identity
            or operation.get("action") != "start_render_job"
            or operation.get("fingerprint") != owner["fingerprint"]
            or wire.get("call") != expected_call
            or trace.get("session") != session or trace.get("phase") != "uncertain"
            or trace.get("id") != sdk["id"] or trace.get("packet") != rpc.request
            or trace.get("operation") != operation or trace.get("call") != expected_call
            or trace.get("request_sha256") != hashlib.sha256(
                sdk["data"].encode("ascii")
            ).hexdigest()):
        raise ValueError("Render dispatch evidence mismatch")
    for suffix in (".preset", ".ready.preset"):
        late = root / ("sdk-" + sdk["id"] + suffix)
        _guard(late)
        if late.exists():
            raise RuntimeError("RENDER_LATE_NATIVE_RESPONSE_REQUIRES_RECOVERY")
    if journal is not None and (
        journal.get("trace") != trace or journal.get("guard") != guard.request
        or journal.get("rpc") != rpc.request
    ):
        raise ValueError("Render recovery packet history mismatch")
    if journal is None:
        if ledger.get("phase") not in {"uncertain", "dispatch_started"}:
            raise ValueError("Original render ledger is not uncertain")
        original_response = _read(
            profile.runtime_base / "responses" / (command_id + ".json")
        )
        if (original_response.get("command_id") != command_id
                or original_response.get("protocol_version") != "1.0"
                or original_response.get("status") != "error"):
            raise ValueError("Original render response mismatch")
        journal_path.parent.mkdir(exist_ok=True)
        journal = {"kind": _TAG, "owner": owner, "ledger": ledger,
                   "command": command, "trace": trace, "guard": guard.request,
                   "rpc": rpc.request, "process_exit_proofs": proofs,
                   "original_response": original_response}
        atomic_json(journal_path, journal)
    # The write's native outcome stays UNKNOWN. A terminal error permits other
    # projects to proceed but this key can never start rendering again.
    response = dict(journal["original_response"])
    response.update(status="error", result=None, error={
        "code": "ORIGINAL_RENDER_PROCESS_EXITED",
        "message": "Original render process exited; native outcome remains unknown. "
                   "This key will not restart rendering.",
        "retryable": False,
        "details": {"job_id": command["arguments"]["job_id"],
                    "native_outcome": "unknown", "render_not_restarted": True,
                    "recovery_record": str(journal_path)},
    })
    tag = {"kind": _TAG, "record": str(journal_path)}
    terminal_ledger = {**ledger, "phase": "completed", "response": response,
                       "terminal_recovery": tag}
    if current_ledger not in (ledger, terminal_ledger):
        raise ValueError("Render ledger changed during recovery")
    _archive(rpc, archive / "rpc")
    _archive(guard, archive / "operation")
    atomic_json(ledger_path, terminal_ledger)
    atomic_json(path, {**owner, "phase": "completed", "response": response,
                       "terminal_recovery": tag})


def recover_exited_render_starts(profile: Profile) -> None:
    """Caller owns operation.lock; no native/launch/UI command is ever sent."""
    folder = profile.state_directory / "sdk-write-owners"
    _guard(folder)
    for path in folder.glob("*.json"):
        owner = _read(path)
        if (owner.get("phase") == "uncertain" and all(
            k in owner for k in ("runtime", "session", "generation", "command_id",
                                  "fingerprint")
        )):
            _retire(profile, path, owner)


def replay_exited_render(
    profile: Profile, command: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the terminal failure BEFORE connection.ensure could open a project."""
    if command["action"] != "start_render_job":
        return None
    key = hashlib.sha256(command["idempotency_key"].encode()).hexdigest()
    path = profile.state_directory / "sdk-write-owners" / (key + ".json")
    _guard(path)
    if not path.exists():
        return None
    owner = _read(path)
    if owner.get("terminal_recovery", {}).get("kind") != _TAG:
        return None
    if owner["fingerprint"] != bridge._request_fingerprint(command):
        raise RuntimeError("SDK_IDEMPOTENCY_CONFLICT")
    if owner.get("phase") != "completed":
        raise RuntimeError("ORIGINAL_SDK_OPERATION_REQUIRES_RECOVERY")
    return {**owner["response"], "command_id": command["command_id"]}
