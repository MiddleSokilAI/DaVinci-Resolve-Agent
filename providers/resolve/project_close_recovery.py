"""Reconcile only an acknowledged original close, using native getters exclusively."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from bridges.resolve import ResolveBridge as bridge
from providers.resolve.lua_editing import atomic_json
from providers.resolve.marker_supervisor import Profile, read_active
from providers.resolve.marker_transport import MarkerLeaseKeeper
from providers.resolve.preset_channel import PresetSDKChannel
from providers.resolve.remote_sdk import RemoteSDK


def _guard(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (
            candidate.exists()
            and getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise RuntimeError("LINKED_CLOSE_RECOVERY_EVIDENCE")


def _read(path: Path) -> dict[str, Any]:
    _guard(path)
    with path.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("CLOSE_RECOVERY_EVIDENCE_BOUND")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("INVALID_CLOSE_RECOVERY_EVIDENCE")
    return value


def _native(root: Path, session: str, operation: dict[str, Any]) -> Any:
    return RemoteSDK(PresetSDKChannel(root, session, operation)).root()


def _commit(
    profile: Profile, owner_path: Path, owner: dict[str, Any],
    command: dict[str, Any], ledger: dict[str, Any], response: dict[str, Any],
) -> dict[str, Any]:
    """Finalize already archived proof; a crash never causes another native close."""
    key = owner_path.stem
    bridge._write_receipt(command, profile.runtime_base / "state", response["result"])
    atomic_json(profile.runtime_base / "responses" / (owner["command_id"] + ".json"),
                response)
    atomic_json(profile.runtime_base / "state/sdk-operations" / (key + ".json"),
                {**ledger, "phase": "completed", "response": response})
    completed = {**owner, "phase": "completed", "response": response}
    atomic_json(owner_path, completed)
    return completed


def recover_acknowledged_close(
    profile: Profile, owner_path: Path, owner: dict[str, Any],
) -> dict[str, Any]:
    """Caller holds operation lock; refuse missing ack, changed binding or content.

    A failed getter never authorizes CloseProject again. Original failed records
    are archived before any host receipt is finalized, for crash-safe replay.
    """
    active = read_active(profile)
    if not active or any(owner.get(key) != active.get(key) for key in (
        "runtime", "session", "generation"
    )):
        raise RuntimeError("ORIGINAL_CLOSE_GENERATION_CHANGED")
    if owner.get("transport") != "lua_marker_preset":
        raise RuntimeError("ORIGINAL_SDK_OPERATION_REQUIRES_RECOVERY")
    root = Path(owner["runtime"])
    if root.resolve().parent != profile.runtime_base.resolve():
        raise RuntimeError("ORIGINAL_CLOSE_RUNTIME_INVALID")
    identity, fingerprint = owner["command_id"], owner["fingerprint"]
    original = _read(profile.runtime_base / "failed" / (identity + ".json"))
    # This historical envelope authorizes no new write. A separate read-only
    # operation below gets its own expiry after the original ack is proven.
    command = bridge.validate_command(original, allow_expired=True)
    if command["action"] != "close_project" or (
        bridge._request_fingerprint(command) != fingerprint
    ):
        raise RuntimeError("ORIGINAL_CLOSE_COMMAND_CHANGED")
    key = hashlib.sha256(command["idempotency_key"].encode()).hexdigest()
    if owner_path.name != key + ".json":
        raise RuntimeError("ORIGINAL_CLOSE_KEY_CHANGED")
    expected = command["arguments"]["expected_project_id"]
    archive = profile.runtime_base / "state/close-reconciliations" / (key + ".json")
    _guard(archive)
    if archive.exists() or archive.is_symlink():
        saved = _read(archive)
        result = saved.get("response", {}).get("result", {})
        if (
            saved.get("original_owner") != owner
            or saved.get("response", {}).get("command_id") != identity
            or saved.get("response", {}).get("status") != "success"
            or saved.get("original_sdk_ledger", {}).get("fingerprint") != fingerprint
            or saved.get("original_sdk_ledger", {}).get("session") != owner["session"]
            or saved.get("original_sdk_ledger", {}).get("command_id") != identity
            or result.get("closed") is not True or result.get("saved") is not True
            or result.get("project", {}).get("project_id") != expected
            or result.get("post_close_context") != saved.get("readback")
            or result.get("evidence") != (
                "original_native_close_ack_and_fresh_readback"
            )
            or result.get("backup_path") != saved.get(
                "original_error_response", {}
            ).get("error", {}).get("details", {}).get("backup_path")
            or not isinstance(saved.get("native_trace_hashes"), dict)
            or not 1 <= len(saved["native_trace_hashes"]) <= 128
        ):
            raise RuntimeError("ORIGINAL_CLOSE_RECOVERY_ARCHIVE_CHANGED")
        for path_name, digest in saved["native_trace_hashes"].items():
            path = Path(path_name)
            if path.resolve().parent != (root / "sdk-trace").resolve():
                raise RuntimeError("ORIGINAL_CLOSE_TRACE_CHANGED")
            _read(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError("ORIGINAL_CLOSE_TRACE_CHANGED")
        return _commit(profile, owner_path, owner, command,
                       saved["original_sdk_ledger"], saved["response"])
    response_path = profile.runtime_base / "responses" / (identity + ".json")
    failed = _read(response_path)
    ledger_path = profile.runtime_base / "state/sdk-operations" / (key + ".json")
    ledger = _read(ledger_path)
    if (
        failed.get("command_id") != identity or failed.get("status") != "error"
        or failed.get("error", {}).get("code") != "PROJECT_CLOSE_READBACK_FAILED"
        or ledger.get("session") != owner["session"]
        or ledger.get("command_id") != identity
        or ledger.get("fingerprint") != fingerprint
        or ledger.get("phase") not in {"dispatch_started", "uncertain"}
    ):
        raise RuntimeError("ORIGINAL_SDK_OPERATION_REQUIRES_RECOVERY")
    backup = Path(failed["error"]["details"]["backup_path"])
    if backup.is_symlink() or backup.resolve().parent != (
        profile.runtime_base / "backups"
    ).resolve() or not backup.is_file() or backup.stat().st_size == 0:
        raise RuntimeError("ORIGINAL_CLOSE_BACKUP_UNVERIFIED")
    closes: list[dict[str, Any]] = []
    saves: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []
    name: str | None = None
    evidence: dict[str, str] = {}
    for count, path in enumerate((root / "sdk-trace").glob("*.json")):
        if count >= 4096:
            raise RuntimeError("CLOSE_RECOVERY_TRACE_BOUND")
        trace = _read(path)
        op = trace.get("operation", {})
        if op.get("id") != UUID(identity).hex:
            continue
        if (
            trace.get("session") != owner["session"]
            or op.get("action") != "close_project"
            or op.get("fingerprint") != fingerprint
        ):
            raise RuntimeError("ORIGINAL_CLOSE_TRACE_CHANGED")
        call, reply = trace.get("call", {}), trace.get("response", {})
        if trace.get("phase") != "completed" or reply.get("error") is not None:
            continue
        if reply.get("result") is not True:
            continue
        request = bytes.fromhex(reply.get("request_hex", ""))
        if (
            hashlib.sha256(request).hexdigest() != trace.get("request_sha256")
            or json.loads(request) != {"operation": op, "call": call}
        ):
            raise RuntimeError("ORIGINAL_CLOSE_RESPONSE_BINDING_CHANGED")
        method = call.get("method")
        if method == "CloseProject":
            closes.append(trace)
        elif method == "SaveProject":
            saves.append(trace)
        elif method == "ExportProject":
            args = call.get("arguments", {}).get("a", [])
            if len(args) == 3 and Path(args[1]) == backup and args[2] is False:
                exports.append(trace)
                name = args[0]
        else:
            continue
        evidence[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    if len(closes) != 1 or not saves or len(exports) != 1 or not isinstance(name, str):
        raise RuntimeError("ORIGINAL_CLOSE_ACKNOWLEDGEMENT_UNVERIFIED")
    target_ref = closes[0]["call"].get("arguments", {}).get("a", [])
    if len(target_ref) != 1 or not isinstance(target_ref[0], dict):
        raise RuntimeError("ORIGINAL_CLOSE_TARGET_UNVERIFIED")
    target = target_ref[0].get("r")
    bound = False
    for saved in saves:
        objects = saved["response"].get("objects", {})
        cache = objects.get(target, {}).get("cache", [])
        values = {item["method"]: item.get("result") for item in cache}
        if values.get("GetUniqueId") == expected and values.get("GetName") == name:
            bound = True
    if not bound:
        raise RuntimeError("ORIGINAL_CLOSE_TARGET_UNVERIFIED")
    operation = {
        "id": uuid4().hex, "fingerprint": fingerprint, "action": "get_capabilities",
        "create_backup": False, "allow_destructive": False,
        "expires": int(time.time()) + 120,
    }
    with MarkerLeaseKeeper(root):
        native = _native(root, owner["session"], operation)
        context = bridge._closed_project_context(native.GetProjectManager(), expected)
    result = {
        "project": {"project_id": expected, "name": name},
        "closed": True, "saved": True, "backup_path": str(backup),
        "evidence": "original_native_close_ack_and_fresh_readback",
        "post_close_context": context, "recovered": True,
    }
    response = {**failed, "status": "success", "error": None,
                "result": result, "finished_at": bridge.utc_now()}
    archive.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(archive, {
        "original_owner": owner, "original_sdk_ledger": ledger,
        "original_error_response": failed, "native_trace_hashes": evidence,
        "read_only_operation": operation, "readback": context, "response": response,
    })
    return _commit(profile, owner_path, owner, command, ledger, response)
