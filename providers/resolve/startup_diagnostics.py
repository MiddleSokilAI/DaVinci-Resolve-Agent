"""Read-only local startup evidence for the saved-project Free marker channel.

Lifecycle exports describe earlier native execution, never present liveness.
No diagnostic dispatches a command, launches Resolve, or inspects its UI.
"""

from __future__ import annotations

import json
import os
import zlib
from itertools import islice
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from agent.client import BridgeProtocolError
from providers.resolve.lua_editing import bounded_text
from providers.resolve.lua_transport import read_project_snapshot
from providers.resolve.marker_transport import inspect_pending, validate_marker_metadata

MENU_FILE = "Resolve Agent Marker.lua"
MENU_HEADER = "-- DaVinci Resolve Agent: managed Free marker menu script\n"
MAX_METADATA_BYTES = 65536
MAX_SCRIPT_BYTES = 4 * 1024 * 1024
MAX_RECEIPTS = 128
EXIT_REASONS = ("acknowledged_stop", "lease_expired", "quit_requested", "error")


def _read_text(path: Path, limit: int) -> str:
    """Read bounded UTF-8 text, rejecting linked or oversized local evidence."""
    if path.is_symlink():
        raise ValueError("Linked diagnostic evidence is not accepted.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise OverflowError("Diagnostic evidence exceeds its size bound.")
    return data.decode("utf-8-sig")


def _metadata(root: Path) -> tuple[str, str | None]:
    """Validate marker metadata without constructing a native request client."""
    try:
        metadata = json.loads(_read_text(root / "session.json", MAX_METADATA_BYTES))
        if not isinstance(metadata, dict):
            raise ValueError("Session metadata must be an object.")
        validate_marker_metadata(metadata)
    except FileNotFoundError:
        return "missing", None
    except OverflowError:
        return "oversize", None
    except OSError:
        return "unreadable", None
    except (ValueError, TypeError, RecursionError):
        return "corrupt", None
    return "valid", metadata["session"]


def _snapshot_status(root: Path, name: str) -> str:
    """Validate a fixed runtime-local DRP without exposing its project identity."""
    path = root / name
    try:
        if path.is_symlink() or path.resolve().parent != root:
            return "invalid"
        read_project_snapshot(path)
    except FileNotFoundError:
        return "not_observed"
    except (
        OSError, ValueError, RuntimeError, KeyError, BadZipFile, ParseError,
        BridgeProtocolError, NotImplementedError, EOFError, OverflowError, zlib.error,
    ):
        return "invalid"
    return "valid"


def _menu_binding(root: Path, scripts_directory: Path | None) -> str:
    """Compare normalized managed menu bytes with this prepared runtime."""
    if scripts_directory is None:
        appdata = os.environ.get("APPDATA")
        if os.name != "nt" or not appdata:
            return "not_checked"
        scripts_directory = Path(appdata) / (
            "Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Utility"
        )
    target = scripts_directory / MENU_FILE
    try:
        installed = _read_text(target, MAX_SCRIPT_BYTES).replace("\r\n", "\n")
        installed = installed.replace("\r", "\n")
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError, OverflowError):
        return "unreadable"
    if not installed.startswith(MENU_HEADER):
        return "unmanaged"
    try:
        prepared = _read_text(root / "marker-bridge.lua", MAX_SCRIPT_BYTES)
    except (OSError, ValueError, OverflowError):
        return "unreadable"
    expected = MENU_HEADER + prepared.replace("\r\n", "\n").replace("\r", "\n")
    return "matching" if installed == expected else "different_runtime"


def _recovery(root: Path, metadata_status: str) -> dict[str, Any]:
    """Inspect bounded recovery evidence, retaining uncertainty on read errors."""
    result: dict[str, Any] = {
        "status": "not_checked",
        "pending_receipt_count": None,
        "receipt_count": None,
        "inbox_occupied": False,
        "client_lock_present": False,
        "lease_publish_lock_present": False,
    }
    try:
        for name, field in (
            ("inbox", "inbox_occupied"),
            ("client.lock", "client_lock_present"),
            ("lease-publish.lock", "lease_publish_lock_present"),
        ):
            path = root / name
            result[field] = path.exists() or path.is_symlink()
        if metadata_status != "valid":
            return result
        receipts = root / "receipts"
        if (
            receipts.is_symlink() or receipts.resolve().parent != root
            or receipts.exists() and not receipts.is_dir()
        ):
            raise ValueError("Receipt directory is not runtime-local.")
        paths = list(islice(receipts.glob("*.json"), MAX_RECEIPTS + 1))
        result["receipt_count"] = min(len(paths), MAX_RECEIPTS)
        if len(paths) > MAX_RECEIPTS:
            result["receipt_count_truncated"] = True
            raise ValueError("Receipt inspection bound reached.")
        for path in paths:
            if path.is_symlink() or path.resolve().parent != receipts.resolve():
                raise ValueError("Receipt is not runtime-local.")
        pending = inspect_pending(root)
        result.update(
            status="inspected",
            pending_receipt_count=len(pending["pending_receipts"]),
            pending_receipts=pending["pending_receipts"],
            inbox=pending["inbox"],
        )
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError):
        result["status"] = "inspection_required"
    return result


def inspect_startup(
    root: Path,
    *,
    target_project_name: str | None = None,
    host: dict[str, Any] | None = None,
    scripts_directory: Path | None = None,
) -> dict[str, Any]:
    """Return local startup evidence and an ordered handoff without mutation.

    The optional target name labels a manual step only. Host metadata remains
    separate and cannot establish the current native project or connection.
    """
    if target_project_name is not None:
        bounded_text(target_project_name, "Target project name")
    root = root.resolve()
    metadata_status, session = _metadata(root)
    consumed = _snapshot_status(root, "marker-session-consumed.drp")
    exits = {
        reason: _snapshot_status(root, f"marker-exit-{reason}.drp")
        for reason in EXIT_REASONS
    }
    invalid = consumed == "invalid" or "invalid" in exits.values()
    valid_exits = [reason for reason, status in exits.items() if status == "valid"]
    terminal = (
        metadata_status == "valid" and consumed == "valid"
        and bool(valid_exits) and not invalid
    )
    start_evidence = (
        "evidence_invalid" if invalid
        else "consumed" if consumed == "valid" else "not_observed"
    )
    recovery = _recovery(root, metadata_status)
    menu = _menu_binding(root, scripts_directory)
    handoff: list[dict[str, str]] = []

    def step(code: str, instruction: str) -> None:
        handoff.append({"code": code, "instruction": instruction})

    if recovery["inbox_occupied"] or recovery["pending_receipt_count"]:
        state = "recovery_required"
        step("reconcile_original_operation", (
            "Inspect the original request and receipt with the same arguments and "
            "idempotency key. Do not delete evidence or prepare a replacement runtime."
        ))
    elif recovery["client_lock_present"] or recovery["lease_publish_lock_present"]:
        state = "inspection_required"
        step("wait_then_retry_diagnostics", (
            "A client or lease publisher may be active. Wait, then inspect again; "
            "do not delete locks or rotate the runtime."
        ))
    elif metadata_status != "valid":
        state = "runtime_invalid"
        step("prepare_new_runtime_only_after_recovery_audit", (
            "Audit this runtime's original requests and receipts before preparing "
            "a new runtime. Preserve its evidence."
        ))
    elif recovery["status"] != "inspected" or invalid or (
        valid_exits and consumed != "valid"
    ):
        state = "inspection_required"
        step("audit_local_evidence", (
            "Local evidence is incomplete or invalid. Inspect it before restarting "
            "the bridge or rotating the runtime; preserve the original files."
        ))
    elif terminal:
        state = "session_exited"
        if menu == "different_runtime":
            step("reload_mcp_matching_prepared_runtime", (
                "The managed menu belongs to another runtime. Align the MCP "
                "configuration with that prepared runtime and reconnect first."
            ))
        step("prepare_fresh_runtime_after_reconciliation", (
            "This consumed native session has exited. Recheck any newly aligned "
            "runtime first. If it has also exited, reconcile the original "
            "operations before preparing and configuring a fresh runtime."
        ))
    elif menu in {"missing", "different_runtime", "unmanaged", "unreadable"}:
        state = "menu_alignment_required"
        step("align_config_and_managed_menu", (
            "Align the configured runtime and managed menu script, preserving "
            "unmanaged scripts, then reconnect MCP."
        ))
    elif consumed == "not_observed":
        state = "bootstrap_required"
        label = (
            f"the existing saved project '{target_project_name}'"
            if target_project_name else "the intended existing saved project"
        )
        step("open_existing_saved_target_project_then_run_menu_once", (
            f"Open {label}; do not create or import a replacement project. "
            "Run Resolve Agent Marker from Workspace > Scripts once."
        ))
    else:
        state = "native_check_required"
    if state in {"bootstrap_required", "native_check_required"}:
        step("fresh_native_check", (
            "Request a fresh native connection check and verify the project name "
            "and UUID before changing project media or timelines."
        ))

    return {
        "backend": "free_marker",
        "diagnostic_source": "local_runtime",
        "session": session,
        "metadata_status": metadata_status,
        "native_start_evidence": start_evidence,
        "native_exit_evidence": valid_exits,
        "native_lifecycle": {"consumed": consumed, "exits": exits},
        "terminal": terminal,
        "recovery": recovery,
        "menu_binding": menu,
        "startup_capabilities": {
            "unattended_cold_start": False,
            "launch": False,
            "open_existing": False,
            "list_existing": False,
            "bootstrap_from_ProjectManager": False,
            "saved_project_required": True,
            "managed_menu_required": True,
        },
        "state": state,
        "handoff": handoff,
        "host": host,
        "project_state": "unverified",
        "recording_readiness": "not_assessed",
        "native_connection_verified": False,
    }
