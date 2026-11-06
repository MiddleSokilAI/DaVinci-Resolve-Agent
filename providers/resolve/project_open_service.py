"""Once-only host coordination for opening one existing Resolve project.

The fixed Windows adapter selects an existing item; only a fresh native context
response can verify the resulting library, root folder, project name and UUID.
Host receipts survive native generations and never replay an uncertain Open.
"""

from __future__ import annotations

import hashlib
import json
import math
import ntpath
import re
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from providers.resolve.lua_editing import atomic_json, bounded_text
from providers.resolve.lua_project_context import (
    PROJECT_CONTEXT_CAPABILITY,
    validate_project_context_request,
)
from providers.resolve.lua_transport import LuaCommandTimeoutError, LuaSnapshotClient
from providers.resolve.marker_supervisor import (
    OperationBusy,
    Profile,
    Supervisor,
    _lock,
    operation_lock,
    project_open_lock,
    read_active,
    runtime_history,
)
from providers.resolve.marker_transport import MarkerLeaseKeeper
from providers.resolve.startup_diagnostics import inspect_startup
from providers.resolve.windows_project_manager import WindowsProjectManagerAdapter

_MAX_RECEIPTS = 1024
_MAX_RECEIPT_BYTES = 65536
_PENDING = frozenset({"prepared", "dispatch_started", "awaiting_native"})
_STATUSES = _PENDING | {"completed", "rejected"}


class ProjectOpenError(RuntimeError):
    """A host request was refused or needs original-operation recovery."""

    def __init__(
        self,
        code: str,
        *,
        uncertain: bool = False,
        cause_code: str | None = None,
        preflight_stage: str | None = None,
        preflight_failure: dict[str, Any] | None = None,
    ) -> None:
        """Preserve the refusal cause across MCP and durable receipt replay."""
        self.code = code
        self.uncertain = uncertain
        self.cause_code = cause_code
        self.preflight_failure = preflight_failure
        if preflight_stage is None and preflight_failure is not None:
            preflight_stage = "/".join(
                str(preflight_failure.get(key, "unknown"))
                for key in ("operation", "stage")
            )
        cause = f" Cause: {cause_code}." if cause_code else ""
        stage = f" Preflight stage: {preflight_stage}." if preflight_stage else ""
        dispatch = preflight_failure.get("dispatch") if preflight_failure else None
        ack = (
            f" Invoke completed={str(dispatch['invoke_completed']).lower()},"
            f" succeeded={str(dispatch['invoke_succeeded']).lower()}."
            if isinstance(dispatch, dict)
            and all(
                type(dispatch.get(key)) is bool
                for key in ("invoke_completed", "invoke_succeeded")
            )
            else ""
        )
        super().__init__(
            f"Project opening: {code}.{cause}{stage}{ack} "
            "No Open action was resubmitted."
        )


def _unlinked(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (
            item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ProjectOpenError("LINKED_RECEIPT_PATH")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate receipt field.")
        result[key] = value
    return result


def _token(process: Any) -> tuple[int, str, str]:
    if (
        not isinstance(process, dict)
        or type(process.get("pid")) is not int
        or process["pid"] <= 0
        or not isinstance(process.get("creation_time"), str)
        or not re.fullmatch(r"[1-9][0-9]{0,18}", process["creation_time"])
        or not isinstance(process.get("executable_path"), str)
        or not process["executable_path"]
        or len(process["executable_path"]) > 1024
    ):
        raise ProjectOpenError("PROCESS_IDENTITY_UNVERIFIED", uncertain=True)
    return (
        process["pid"],
        process["creation_time"],
        ntpath.normcase(ntpath.normpath(process["executable_path"])),
    )


def _saved_process(process: dict[str, Any]) -> dict[str, Any]:
    _token(process)
    return {key: process[key] for key in ("pid", "creation_time", "executable_path")}


def _retryable_preflight(value: dict[str, Any]) -> bool:
    """A terminal pre-Open refusal, never an uncertain or completed Open.

    Older coordinators only persisted the cause. WINDOW_UNAVAILABLE in their
    rejected branch is still explicit no-Open evidence; fresh Manager guards
    must separately rule out a remaining dialog before attempting preflight.
    """
    return (
        value.get("status") == "rejected"
        and value.get("native_binding") is None
        and value.get("recovery_no_op", False) is False
        and value.get("error") == value.get("cause_code")
        and value.get("cause_code") == "WINDOW_UNAVAILABLE"
        and (
            "preflight_failure" not in value
            or value["preflight_failure"]["candidate_export_uncertain"] is False
        )
    )


def _attempt_history(path: Path, receipt: dict[str, Any]) -> list[dict[str, str]]:
    snapshot = {k: v for k, v in receipt.items() if k != "previous_attempts"}
    data = (
        path.read_bytes().decode("utf-8")
        if not receipt.get("previous_attempts")
        else json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)
    )
    previous = [
        *receipt.get("previous_attempts", []),
        {
            "receipt_json": data,
            "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
        },
    ]
    if len(previous) > 8 or len(data.encode("utf-8")) > _MAX_RECEIPT_BYTES:
        raise ProjectOpenError("RECOVERY_HISTORY_LIMIT")
    return previous


def _export_preflight_rejection(value: dict[str, Any]) -> bool:
    """Explicit no-Open evidence; the export outcome itself stays uncertain."""
    failure = value.get("preflight_failure")
    return (
        value.get("status") == "rejected"
        and value.get("native_binding") is None
        and value.get("recovery_no_op", False) is False
        and value.get("error")
        == value.get("cause_code")
        == "EXPORT_DIALOG_NOT_OBSERVED"
        and isinstance(failure, dict)
        and failure.get("operation") == "export_candidate"
        and failure.get("project_open_requested") is False
        and type(failure.get("candidate_export_uncertain")) is bool
    )


def _validate_receipt_value(value: Any, key_hash: str, *, history: bool = True) -> None:
    """Validate a live receipt and bounded, non-recursive prior-attempt snapshots."""
    if (
        not isinstance(value, dict)
        or type(value.get("schema")) is not int
        or value["schema"] not in {1, 2, 3, 4}
        or value.get("action") != "open_existing_project"
        or value.get("status") not in _STATUSES
        or value.get("key_hash") != key_hash
        or not re.fullmatch(r"[0-9a-f]{64}", key_hash)
        or not isinstance(value.get("fingerprint"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["fingerprint"])
        or not isinstance(value.get("arguments"), dict)
    ):
        raise ValueError("Invalid receipt fields.")
    arguments = value["arguments"]
    normalized = validate_project_context_request(
        {
            "expected_database": arguments.get("expected_database"),
            "expected_folder": "",
            "expected_project_name": arguments.get("project_name"),
        },
        arguments.get("expected_project_id"),
    )
    expected_arguments = {
        "project_name": normalized["expected_project_name"],
        "expected_project_id": arguments.get("expected_project_id"),
        "expected_database": normalized["expected_database"],
        "folder_path": [],
    }
    if (
        arguments != expected_arguments
        or _fingerprint(arguments) != value["fingerprint"]
    ):
        raise ValueError("Receipt intent mismatch.")
    _token(value.get("process"))
    binding = value.get("native_binding")
    if binding is not None and (
        not isinstance(binding, dict)
        or set(binding) != {"runtime", "session", "generation"}
        or any(not isinstance(binding[key], str) or not binding[key] for key in binding)
    ):
        raise ValueError("Invalid native binding.")
    if value["status"] == "completed":
        result = value.get("result")
        if (
            not isinstance(result, dict)
            or binding is None
            or result.get("project")
            != {
                "id": arguments["expected_project_id"],
                "name": arguments["project_name"],
            }
            or result.get("database") != arguments["expected_database"]
            or result.get("folder") != ""
            or result.get("context_verified") is not True
            or result.get("folder_membership_verified") is not True
            or result.get("native_connection_verified") is not True
            or result.get("source") != "live_lua_api"
            or result.get("readback_verified_by") != "ResolveLuaProjectContext"
            or result.get("proof_scope") != "fresh_native_context"
            or result.get("session") != binding["session"]
            or result.get("generation") != binding["generation"]
            or type(result.get("no_op")) is not bool
            or result.get("ui_open_dispatched") is not (not result["no_op"])
        ):
            raise ValueError("Completed outcome missing or inconsistent.")
    if value["status"] == "rejected" and not isinstance(value.get("error"), str):
        raise ValueError("Rejected outcome missing.")
    if "cause_code" in value and (
        not isinstance(value["cause_code"], str)
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value["cause_code"])
    ):
        raise ValueError("Invalid refusal cause.")
    failure = value.get("preflight_failure")
    if "preflight_failure" in value and (
        not isinstance(failure, dict)
        or not {
            "operation",
            "stage",
            "project_open_requested",
            "candidate_export_uncertain",
            "view_changes_uncertain",
        }
        <= set(failure)
        or set(failure)
        - {
            "operation",
            "stage",
            "project_open_requested",
            "candidate_export_uncertain",
            "view_changes_uncertain",
            "dispatch",
        }
        or any(
            not isinstance(failure[key], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", failure[key])
            for key in ("operation", "stage")
        )
        or failure["project_open_requested"] is not False
        or any(
            type(failure[key]) is not bool
            for key in (
                "candidate_export_uncertain",
                "view_changes_uncertain",
            )
        )
    ):
        raise ValueError("Invalid preflight refusal evidence.")
    if isinstance(failure, dict) and "dispatch" in failure:
        dispatch = failure["dispatch"]
        if (
            not isinstance(dispatch, dict)
            or set(dispatch) != {"invoke_completed", "invoke_succeeded"}
            or any(type(item) is not bool for item in dispatch.values())
            or dispatch["invoke_succeeded"]
            and not dispatch["invoke_completed"]
        ):
            raise ValueError("Invalid export dispatch evidence.")

    if value["schema"] in {2, 3, 4}:
        if type(value.get("recovery_no_op")) is not bool:
            raise ValueError("Recovery dispatch classification missing.")
        if value["schema"] in {3, 4} and value.get("preflight_retry") is not True:
            raise ValueError("Preflight retry classification missing.")
        if (
            value["schema"] == 4
            and value.get("export_preflight_reconciled") is not True
        ):
            raise ValueError("Export preflight reconciliation missing.")
        if history:
            previous = value.get("previous_attempts")
            if not isinstance(previous, list) or not 1 <= len(previous) <= 8:
                raise ValueError("Recovery history missing or exceeds bound.")
            processes = {_token(value["process"])}
            snapshots = []
            for entry in previous:
                if not isinstance(entry, dict) or set(entry) != {
                    "receipt_json",
                    "sha256",
                }:
                    raise ValueError("Invalid prior attempt.")
                data = entry["receipt_json"]
                if (
                    not isinstance(data, str)
                    or len(data.encode("utf-8")) > _MAX_RECEIPT_BYTES
                    or hashlib.sha256(data.encode("utf-8")).hexdigest()
                    != entry["sha256"]
                ):
                    raise ValueError("Prior receipt digest mismatch.")
                prior = json.loads(data.lstrip("\ufeff"), object_pairs_hook=_object)
                if "previous_attempts" in prior:
                    raise ValueError("Nested recovery history is not accepted.")
                _validate_receipt_value(prior, key_hash, history=False)
                prior_process = _token(prior["process"])
                if value["schema"] == 2 and prior_process in processes:
                    raise ValueError("Recovery history repeats a process identity.")
                processes.add(prior_process)
                if (
                    prior["fingerprint"] != value["fingerprint"]
                    or prior["arguments"] != value["arguments"]
                    or prior["status"] not in _PENDING
                    and not (
                        value["schema"] in {3, 4}
                        and _retryable_preflight(prior)
                        or value["schema"] == 4
                        and _export_preflight_rejection(prior)
                    )
                ):
                    raise ValueError("Prior attempt intent changed.")
                snapshots.append(prior)
            if value["schema"] in {3, 4}:
                if not any(
                    _retryable_preflight(prior)
                    or value["schema"] == 4
                    and _export_preflight_rejection(prior)
                    for prior in snapshots
                ):
                    raise ValueError("Preflight retry has no rejected original.")
                if value["schema"] == 4 and not any(
                    _export_preflight_rejection(prior) for prior in snapshots
                ):
                    raise ValueError("Export reconciliation has no original refusal.")
                seen: dict[tuple[int, str, str], dict[str, Any]] = {}
                for attempt in [*snapshots, value]:
                    token = _token(attempt["process"])
                    if token in seen and not (
                        _retryable_preflight(seen[token])
                        or value["schema"] == 4
                        and _export_preflight_rejection(seen[token])
                    ):
                        raise ValueError("An uncertain process attempt cannot repeat.")
                    seen[token] = attempt
    elif "previous_attempts" in value or "recovery_no_op" in value:
        raise ValueError("Recovery requires the versioned receipt contract.")


def _receipt(path: Path) -> dict[str, Any]:
    _unlinked(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_RECEIPT_BYTES + 1)
        if len(raw) > _MAX_RECEIPT_BYTES:
            raise ValueError("Receipt exceeds its bound.")
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object)
        _validate_receipt_value(value, path.stem)
        assert isinstance(value, dict)
        return value
    except (OSError, ValueError, RecursionError, TypeError) as error:
        raise ProjectOpenError("HOST_RECEIPT_INVALID", uncertain=True) from error


def _fingerprint(arguments: dict[str, Any]) -> str:
    data = json.dumps(arguments, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _receipts(profile: Profile) -> list[tuple[Path, dict[str, Any]]]:
    folder = profile.state_directory / "project-open-receipts"
    _unlinked(folder)
    if not folder.exists():
        return []
    if not folder.is_dir():
        raise ProjectOpenError("HOST_RECEIPT_DIRECTORY_INVALID", uncertain=True)
    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in folder.iterdir():
        if len(entries) >= _MAX_RECEIPTS or not re.fullmatch(
            r"[0-9a-f]{64}\.json", path.name
        ):
            raise ProjectOpenError("HOST_RECEIPT_DIRECTORY_INVALID", uncertain=True)
        entries.append((path, _receipt(path)))
    return entries


def pending_project_open(profile: Profile) -> dict[str, Any]:
    """Inspect bounded host receipts; malformed evidence raises and blocks writes.

    Callers coordinating native writes must perform this check while holding
    operation_lock. Advisory lock-file existence is not treated as ownership.
    """
    pending = [
        {
            "key_hash": path.stem,
            "status": value["status"],
            "project_name": value["arguments"]["project_name"],
            "expected_project_id": value["arguments"]["expected_project_id"],
            "process": value.get("process"),
            "receipt_path": str(path),
        }
        for path, value in _receipts(profile)
        if value["status"] in _PENDING
    ]
    from providers.resolve.project_create_service import pending_project_creation

    pending.extend(
        {
            "key_hash": item["key_hash"],
            "status": item["phase"],
            "project_name": item["name"],
            "operation": "create_project",
        }
        for item in pending_project_creation(profile)
    )
    return {
        "status": "inspected",
        "pending": bool(pending),
        "pending_count": len(pending),
        "receipts": pending,
    }


class ProjectOpenService:
    """Coordinate exact existing-project selection without exposing arbitrary UI."""

    def __init__(
        self,
        profile: Profile,
        *,
        adapter: Any = None,
        supervisor_factory: Callable[[Profile], Any] = Supervisor,
        client_factory: Callable[[Path], Any] = LuaSnapshotClient,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        startup: Callable[[float, str], dict[str, Any]] | None = None,
    ) -> None:
        self.profile = profile
        self.adapter = adapter or WindowsProjectManagerAdapter(
            enabled=profile.enabled,
            evidence_directory=profile.state_directory / "project-open-evidence",
        )
        self.supervisor_factory = supervisor_factory
        self.client_factory = client_factory
        self.clock, self.sleep = clock, sleep
        self.startup = startup or self._startup

    def _startup(self, deadline: float, project_name: str) -> dict[str, Any]:
        """Use the same bounded startup as creation, without any Create/Open call."""
        from providers.resolve.project_manager_startup import (
            ProjectStartupError,
            await_project_window,
        )
        from providers.resolve.windows_bootstrap import (
            WindowsBootstrapAdapter,
            _official_executable,
        )
        from providers.resolve.windows_project_create import WindowsProjectCreateAdapter

        host = WindowsBootstrapAdapter(enabled=self.profile.enabled)
        ui = WindowsProjectCreateAdapter()

        def skip_update(process: dict[str, Any], timeout: float) -> dict[str, Any]:
            return ui.request("skip_startup_update", process, timeout_seconds=timeout)

        try:
            return await_project_window(
                deadline=deadline,
                inspect=host.inspect,
                skip_update=skip_update,
                official_executable=_official_executable,
                process_token=_token,
                clock=self.clock,
                sleep=self.sleep,
                expected_project_name=project_name,
            )
        except ProjectStartupError as error:
            raise ProjectOpenError(error.code, cause_code=error.cause_code) from error

    def _recover_exited(
        self,
        path: Path,
        receipt: dict[str, Any],
        deadline: float,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Keep the key/history; rebind only after proving the original process gone.

        Caller owns project_open_lock. No native write, receipt reset or second
        Open is allowed while the original process token is still observable.
        Startup failure leaves the original receipt unchanged.
        """
        with self._operation_until(deadline):
            observed = self.adapter.inspect()
            current = observed.get("process")
            if current is not None and _token(current) == _token(receipt["process"]):
                return None
            if observed.get("state") not in {
                "resolve_not_running",
                "project_manager",
                "project_manager_unavailable",
            } or (observed["state"] != "resolve_not_running" and current is None):
                raise ProjectOpenError(
                    "ORIGINAL_PROCESS_EXIT_UNVERIFIED", uncertain=True
                )
            self._native_clear(receipt["key_hash"])
            if _receipt(path) != receipt:
                raise ProjectOpenError("ORIGINAL_RECEIPT_CHANGED", uncertain=True)
            previous = _attempt_history(path, receipt)
            try:
                with _lock(self.profile, "connection.lock"):
                    ready = self.startup(deadline, receipt["arguments"]["project_name"])
            except OperationBusy as error:
                raise ProjectOpenError("PROJECT_STARTUP_BUSY") from error
            process = ready["process"]
            if _token(process) == _token(receipt["process"]):
                raise ProjectOpenError(
                    "ORIGINAL_PROCESS_EXIT_UNVERIFIED", uncertain=True
                )
            if ready["state"] == "project_manager":
                ready = self.adapter.inspect()
                if (
                    ready.get("state") != "project_manager"
                    or ready.get("eligible") is not True
                    or _token(ready.get("process")) != _token(process)
                ):
                    raise ProjectOpenError("PROJECT_MANAGER_CHANGED")
            elif (
                ready["state"] != "project_window"
                or process.get("project_name_hint")
                != receipt["arguments"]["project_name"]
            ):
                raise ProjectOpenError("RECOVERY_TARGET_UNVERIFIED")
            candidate = {
                k: v
                for k, v in receipt.items()
                if k
                not in {
                    "result",
                    "error",
                    "cause_code",
                    "last_error",
                    "preflight_failure",
                }
            }
            candidate.update(
                schema=receipt["schema"] if receipt["schema"] in {3, 4} else 2,
                process=_saved_process(process),
                native_binding=None,
                status="prepared",
                previous_attempts=previous,
                recovery_no_op=ready["state"] == "project_window",
            )
            encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            if len(encoded.encode("utf-8")) > _MAX_RECEIPT_BYTES:
                raise ProjectOpenError("RECOVERY_HISTORY_LIMIT")
            _validate_receipt_value(candidate, path.stem)
            return candidate, ready

    def _retry_preflight(
        self,
        path: Path,
        receipt: dict[str, Any],
        deadline: float,
        *,
        reconcile_export: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Keep the original key and refusal; never retry a native Open.

        No startup, focus, modal cancellation or receipt deletion. Require the
        same live Manager process and a fresh eligible context before rearming.
        Every later view/export/Open action independently rechecks its window.
        """
        with self._operation_until(deadline):
            if reconcile_export and not _export_preflight_rejection(receipt):
                raise ProjectOpenError("EXPORT_PREFLIGHT_RECONCILIATION_NOT_SAFE")
            if not reconcile_export and not _retryable_preflight(receipt):
                raise ProjectOpenError("PREFLIGHT_RETRY_NOT_SAFE")
            self._native_clear(receipt["key_hash"])
            if _receipt(path) != receipt:
                raise ProjectOpenError("ORIGINAL_RECEIPT_CHANGED", uncertain=True)
            ready = self.adapter.inspect()
            process = ready.get("process")
            if (
                ready.get("state") != "project_manager"
                or ready.get("eligible") is not True
                or process is None
                or _token(process) != _token(receipt["process"])
            ):
                raise ProjectOpenError("PREFLIGHT_MANAGER_UNVERIFIED")
            if reconcile_export:
                try:
                    inspection = self.adapter.inspect_export_dialog(process)
                except (OSError, ValueError, RuntimeError) as error:
                    raise ProjectOpenError(
                        "EXPORT_PREFLIGHT_INSPECTION_UNAVAILABLE"
                    ) from error
                visible = [
                    item for item in inspection["windows"] if not item["offscreen"]
                ]
                if (
                    inspection["process"] != process
                    or inspection["pm_found"] is not True
                    or inspection["windows_truncated"] is not False
                    or inspection["native_visible_handles"]
                    != [process["window_handle"]]
                    or len(visible) != 1
                    or visible[0]["handle"] != process["window_handle"]
                    or visible[0]["enabled"] is not True
                ):
                    raise ProjectOpenError("EXPORT_PREFLIGHT_DIALOG_REQUIRES_RECOVERY")
            if _receipt(path) != receipt:
                raise ProjectOpenError("ORIGINAL_RECEIPT_CHANGED", uncertain=True)
            candidate = {
                key: value
                for key, value in receipt.items()
                if key not in {"error", "cause_code", "last_error", "preflight_failure"}
            }
            candidate.update(
                schema=4 if reconcile_export or receipt["schema"] == 4 else 3,
                status="prepared",
                preflight_retry=True,
                recovery_no_op=False,
                previous_attempts=_attempt_history(path, receipt),
            )
            if reconcile_export:
                candidate["export_preflight_reconciled"] = True
            if (
                len(json.dumps(candidate, ensure_ascii=False).encode("utf-8"))
                > _MAX_RECEIPT_BYTES
            ):
                raise ProjectOpenError("RECOVERY_HISTORY_LIMIT")
            _validate_receipt_value(candidate, path.stem)
            return candidate, ready

    @contextmanager
    def _operation_until(self, deadline: float) -> Iterator[None]:
        """Wait for coordinator ownership without repeating an Open dispatch.

        The watcher inspects Resolve periodically. Contention between inspection
        and dispatch is expected and must not become a request for manual work.
        Only acquisition retries; exceptions raised by the body are never retried.
        """
        with ExitStack() as stack:
            while True:
                try:
                    stack.enter_context(operation_lock(self.profile))
                    break
                except OperationBusy:
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise ProjectOpenError("HOST_OPERATION_BUSY") from None
                    self.sleep(min(0.1, remaining))
            yield

    def _native_clear(self, key_hash: str) -> None:
        from providers.resolve.project_create_service import pending_project_creation

        if pending_project_creation(self.profile):
            raise ProjectOpenError("PROJECT_CREATION_REQUIRES_RECOVERY", uncertain=True)
        owner = self.profile.state_directory / "write-owners" / f"{key_hash}.json"
        _unlinked(owner)
        if owner.exists():
            raise ProjectOpenError("IDEMPOTENCY_KEY_OWNED_BY_NATIVE_WRITE")
        for root in runtime_history(self.profile):
            receipt = root / "receipts" / f"{key_hash}.json"
            _unlinked(receipt)
            if receipt.exists():
                raise ProjectOpenError("IDEMPOTENCY_KEY_OWNED_BY_NATIVE_WRITE")
            evidence = inspect_startup(root)
            recovery = evidence["recovery"]
            if (
                evidence["metadata_status"] != "valid"
                or evidence["native_start_evidence"] == "evidence_invalid"
                or recovery["status"] != "inspected"
                or recovery["pending_receipt_count"] != 0
                or recovery["inbox_occupied"]
                or recovery["client_lock_present"]
            ):
                raise ProjectOpenError("NATIVE_OPERATION_REQUIRES_RECOVERY")
            native_folder = root / "receipts"
            _unlinked(native_folder)
            if native_folder.exists():
                self._audit_native_receipts(native_folder)

    @staticmethod
    def _audit_native_receipts(folder: Path) -> None:
        """Apply the coordinator's strict terminal-outcome gate before any UI."""
        if not folder.is_dir():
            raise ProjectOpenError("NATIVE_OPERATION_REQUIRES_RECOVERY")
        for count, path in enumerate(folder.iterdir(), 1):
            _unlinked(path)
            try:
                if count > _MAX_RECEIPTS or not re.fullmatch(
                    r"[0-9a-f]{64}\.json", path.name
                ):
                    raise ValueError("Unknown native receipt entry.")
                with path.open("rb") as stream:
                    raw = stream.read(_MAX_RECEIPT_BYTES + 1)
                if len(raw) > _MAX_RECEIPT_BYTES:
                    raise ValueError("Native receipt exceeds its bound.")
                value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object)
                if (
                    not isinstance(value, dict)
                    or value.get("status") not in {"completed", "rejected"}
                    or not isinstance(value.get("fingerprint"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", value["fingerprint"])
                    or not isinstance(value.get("command_id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", value["command_id"])
                    or value["status"] == "completed"
                    and not isinstance(value.get("result"), dict)
                    or value["status"] == "rejected"
                    and not isinstance(value.get("error"), str)
                ):
                    raise ValueError("Native receipt outcome is unverified.")
            except (OSError, ValueError, TypeError, RecursionError) as error:
                raise ProjectOpenError("NATIVE_OPERATION_REQUIRES_RECOVERY") from error

    def _persist_rejected(
        self, path: Path, receipt: dict[str, Any], deadline: float
    ) -> None:
        """Wait boundedly for the watcher before publishing a definite refusal."""
        while True:
            try:
                with operation_lock(self.profile):
                    atomic_json(path, receipt)
                return
            except OperationBusy:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise ProjectOpenError(
                        "HOST_RECEIPT_UPDATE_BUSY", uncertain=True
                    ) from None
                self.sleep(min(self.profile.poll_interval_seconds, remaining))

    def _verify(
        self,
        active: dict[str, Any],
        arguments: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        root = Path(active["runtime"])
        client = self.client_factory(root)
        if PROJECT_CONTEXT_CAPABILITY not in client.capabilities:
            raise ProjectOpenError("PROJECT_CONTEXT_CAPABILITY_REQUIRED")
        with MarkerLeaseKeeper(root):
            result = client.request(
                "verify_project_context",
                timeout,
                expected_project_id=arguments["expected_project_id"],
                arguments={
                    "expected_database": arguments["expected_database"],
                    "expected_folder": "",
                    "expected_project_name": arguments["project_name"],
                },
            )
        if not isinstance(result, dict) or not isinstance(
            result.get("response_path"), str
        ):
            raise ProjectOpenError("NATIVE_CONTEXT_UNVERIFIED", uncertain=True)
        path = Path(result["response_path"])
        if (
            not isinstance(result, dict)
            or result.get("project")
            != {
                "id": arguments["expected_project_id"],
                "name": arguments["project_name"],
            }
            or result.get("database") != arguments["expected_database"]
            or result.get("folder") != ""
            or result.get("context_verified") is not True
            or result.get("folder_membership_verified") is not True
            or result.get("source") != "live_lua_api"
            or result.get("readback_verified_by") != "ResolveLuaProjectContext"
            or not path.is_absolute()
            or path.parent.resolve() != root.resolve()
            or not re.fullmatch(r"[0-9a-f]{32}\.ok_context_verified\.drp", path.name)
            or not path.is_file()
            or path.is_symlink()
        ):
            raise ProjectOpenError("NATIVE_CONTEXT_UNVERIFIED", uncertain=True)
        return result

    @staticmethod
    def _binding(active: dict[str, Any]) -> dict[str, str]:
        return {key: str(active[key]) for key in ("runtime", "session", "generation")}

    def _complete(
        self,
        path: Path,
        receipt: dict[str, Any],
        active: dict[str, Any],
        proof: dict[str, Any],
        *,
        no_op: bool,
    ) -> dict[str, Any]:
        result = {
            **proof,
            "status": "completed",
            "operation": "open_existing_project",
            "generation": active["generation"],
            "session": active["session"],
            "no_op": no_op,
            "ui_open_dispatched": not no_op,
            "native_connection_verified": True,
            "proof_scope": "fresh_native_context",
            "replayed": False,
        }
        if receipt.get("schema") in {2, 3, 4}:
            result["recovered_after_process_exit"] = any(
                _token(json.loads(entry["receipt_json"].lstrip("\ufeff"))["process"])
                != _token(receipt["process"])
                for entry in receipt["previous_attempts"]
            )
            result["previous_attempt_count"] = len(receipt["previous_attempts"])
            result["preflight_retried"] = receipt.get("preflight_retry", False)
            result["export_preflight_reconciled"] = receipt.get(
                "export_preflight_reconciled", False
            )
        receipt.update(
            status="completed", native_binding=self._binding(active), result=result
        )
        atomic_json(path, receipt)
        from providers.resolve.project_close_state import explicit_open_completed

        explicit_open_completed(self.profile)
        return result

    def _wait(
        self,
        path: Path,
        receipt: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        supervisor = self.supervisor_factory(self.profile)
        while self.clock() < deadline:
            try:
                observation = self.adapter.inspect()
            except (OSError, ValueError, RuntimeError) as error:
                raise ProjectOpenError(
                    "PROCESS_IDENTITY_UNVERIFIED", uncertain=True
                ) from error
            process = observation.get("process")
            if process is not None and _token(process) != _token(receipt["process"]):
                raise ProjectOpenError("ORIGINAL_PROCESS_CHANGED", uncertain=True)
            if observation.get("state") == "resolve_not_running":
                raise ProjectOpenError("ORIGINAL_PROCESS_EXITED", uncertain=True)
            try:
                with operation_lock(self.profile):
                    active = read_active(self.profile)
                    if (
                        active is not None
                        and _token(active["process"]) != _token(receipt["process"])
                        and receipt.get("native_binding") is None
                    ):
                        # A fresh process can still have its predecessor's
                        # locator until the supervisor observes its open window.
                        # UI evidence above still belongs to our original PID.
                        active = None
                    if active is not None and active["phase"] == "connected":
                        if _token(active["process"]) != _token(receipt["process"]):
                            raise ProjectOpenError(
                                "ORIGINAL_PROCESS_CHANGED", uncertain=True
                            )
                        binding = self._binding(active)
                        if receipt.get("native_binding") not in (None, binding):
                            raise ProjectOpenError(
                                "ORIGINAL_GENERATION_CHANGED", uncertain=True
                            )
                        receipt.update(native_binding=binding, status="awaiting_native")
                        atomic_json(path, receipt)
                        try:
                            proof = self._verify(
                                active,
                                receipt["arguments"],
                                max(
                                    0.01,
                                    min(
                                        self.profile.native_timeout_seconds,
                                        deadline - self.clock(),
                                    ),
                                ),
                            )
                        except LuaCommandTimeoutError:
                            receipt["last_error"] = "NATIVE_CONTEXT_TIMEOUT"
                            atomic_json(path, receipt)
                        except (OSError, ValueError, RuntimeError) as error:
                            raise ProjectOpenError(
                                "NATIVE_CONTEXT_UNVERIFIED", uncertain=True
                            ) from error
                        else:
                            return self._complete(
                                path,
                                receipt,
                                active,
                                proof,
                                no_op=receipt.get("recovery_no_op", False),
                            )
            except OperationBusy:
                pass
            # Never hold operation_lock across bootstrap: the daemon/tick needs it.
            supervisor.tick()
            remaining = deadline - self.clock()
            if remaining > 0:
                self.sleep(min(self.profile.poll_interval_seconds, remaining))
        raise ProjectOpenError("OPEN_OUTCOME_UNVERIFIED", uncertain=True)

    def open_existing(
        self,
        *,
        project_name: str,
        expected_project_id: str,
        expected_database: dict[str, Any],
        folder_path: list[str] | None = None,
        idempotency_key: str,
        confirm: bool = False,
        confirm_recover_exited_process: bool = False,
        confirm_retry_preflight: bool = False,
        confirm_reconcile_export_preflight: bool = False,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Select once, then require native context; retries never repeat Open."""
        if not self.profile.enabled:
            raise ProjectOpenError("OPT_IN_REQUIRED")
        if confirm is not True:
            raise ProjectOpenError("CONFIRMATION_REQUIRED")
        if type(confirm_recover_exited_process) is not bool:
            raise ValueError("confirm_recover_exited_process must be a boolean.")
        if type(confirm_retry_preflight) is not bool:
            raise ValueError("confirm_retry_preflight must be a boolean.")
        if type(confirm_reconcile_export_preflight) is not bool:
            raise ValueError("confirm_reconcile_export_preflight must be a boolean.")
        if confirm_reconcile_export_preflight and not confirm_retry_preflight:
            raise ValueError("Export reconciliation requires explicit preflight retry.")
        if confirm_retry_preflight and confirm_recover_exited_process:
            raise ValueError("Choose one explicit recovery mode.")
        if folder_path is not None and (
            not isinstance(folder_path, list) or folder_path
        ):
            raise ValueError(
                "Existing-project opening currently supports root folder only."
            )
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 600
        ):
            raise ValueError("Open timeout must be finite and at most 600 seconds.")
        bounded_text(idempotency_key, "Idempotency key")
        context = validate_project_context_request(
            {
                "expected_database": expected_database,
                "expected_folder": "",
                "expected_project_name": project_name,
            },
            expected_project_id,
        )
        arguments = {
            "project_name": context["expected_project_name"],
            "expected_project_id": expected_project_id,
            "expected_database": context["expected_database"],
            "folder_path": [],
        }
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        fingerprint = _fingerprint(arguments)
        deadline = self.clock() + timeout_seconds
        with project_open_lock(self.profile):
            folder = self.profile.state_directory / "project-open-receipts"
            _unlinked(folder)
            folder.mkdir(exist_ok=True)
            path = folder / f"{digest}.json"
            with self._operation_until(deadline):
                from providers.resolve.project_close_state import close_intent

                if (close_intent(self.profile) or {}).get("phase") == "pending":
                    raise ProjectOpenError("PROJECT_CLOSE_REQUIRES_RECOVERY")
                records = dict(_receipts(self.profile))
                receipt = records.get(path)
                if receipt is not None:
                    if receipt["fingerprint"] != fingerprint:
                        raise ValueError(
                            "Idempotency key already owns a different project Open."
                        )
                    if receipt["status"] == "completed":
                        result = dict(receipt["result"])
                        return {
                            **result,
                            "replayed": True,
                            "ui_open_dispatched": False,
                            "native_connection_verified": False,
                            "proof_scope": "historical_completed_receipt",
                        }
                    if receipt["status"] == "rejected":
                        if not confirm_retry_preflight:
                            raise ProjectOpenError(
                                "ORIGINAL_OPEN_REJECTED",
                                cause_code=receipt.get("cause_code", receipt["error"]),
                                preflight_failure=receipt.get("preflight_failure"),
                            )
                    elif confirm_retry_preflight:
                        raise ProjectOpenError("PREFLIGHT_RETRY_REQUIRES_REJECTION")
                for other_path, value in records.items():
                    if other_path != path and value["status"] in _PENDING:
                        raise ProjectOpenError(
                            "ANOTHER_PROJECT_OPEN_PENDING", uncertain=True
                        )
                if receipt is None:
                    if confirm_recover_exited_process or confirm_retry_preflight:
                        raise ProjectOpenError("RECOVERY_REQUIRES_ORIGINAL_RECEIPT")
                    self._native_clear(digest)
            recovered = False
            observation: dict[str, Any]
            if receipt is not None:
                recovery = (
                    self._retry_preflight(
                        path,
                        receipt,
                        deadline,
                        reconcile_export=confirm_reconcile_export_preflight,
                    )
                    if confirm_retry_preflight
                    else self._recover_exited(path, receipt, deadline)
                    if confirm_recover_exited_process
                    else None
                )
                if recovery is None:
                    return self._wait(path, receipt, deadline)
                receipt, observation = recovery
                recovered = True
            if not recovered:
                try:
                    observation = self.adapter.inspect()
                except (OSError, ValueError, RuntimeError) as error:
                    raise ProjectOpenError(
                        "PROJECT_MANAGER_INSPECTION_UNAVAILABLE"
                    ) from error
                process = observation.get("process")
                if process is None:
                    raise ProjectOpenError("PROJECT_MANAGER_PROCESS_UNVERIFIED")
                receipt = {
                    "schema": 1,
                    "action": "open_existing_project",
                    "key_hash": digest,
                    "fingerprint": fingerprint,
                    "arguments": arguments,
                    "status": "prepared",
                    "process": _saved_process(process),
                    "native_binding": None,
                }
            assert receipt is not None
            process = observation["process"]
            attach_only = recovered and receipt["recovery_no_op"]
            with self._operation_until(deadline):
                self._native_clear(digest)
                active = read_active(self.profile)
                if (
                    active is not None
                    and active["phase"] == "connected"
                    and _token(active["process"]) == _token(process)
                ):
                    try:
                        proof = self._verify(
                            active,
                            arguments,
                            min(self.profile.native_timeout_seconds, timeout_seconds),
                        )
                    except (OSError, ValueError, RuntimeError) as error:
                        receipt.update(
                            status="rejected", error="ACTIVE_CONTEXT_UNVERIFIED"
                        )
                        atomic_json(path, receipt)
                        raise ProjectOpenError("ACTIVE_CONTEXT_UNVERIFIED") from error
                    return self._complete(path, receipt, active, proof, no_op=True)
                if not attach_only and (
                    observation.get("state") != "project_manager"
                    or observation.get("eligible") is not True
                ):
                    raise ProjectOpenError(
                        "EXISTING_PROJECT_ALREADY_OPEN_OR_MANAGER_UNAVAILABLE"
                    )
                if context["expected_database"]["DbType"] != "Disk":
                    raise ProjectOpenError("UI_LIBRARY_TYPE_UNSUPPORTED")
                if self.clock() >= deadline:
                    raise ProjectOpenError("OPEN_DEADLINE_BEFORE_DISPATCH")
                receipt["status"] = (
                    "awaiting_native" if attach_only else "dispatch_started"
                )
                atomic_json(path, receipt)
            if attach_only:
                return self._wait(path, receipt, deadline)
            try:
                self.adapter.open_existing(
                    process,
                    library_name=context["expected_database"]["DbName"],
                    folder_path=[],
                    project_name=project_name,
                    expected_project_id=expected_project_id,
                    expected_database=context["expected_database"],
                    timeout_seconds=max(0.01, deadline - self.clock()),
                )
            except (OSError, ValueError, RuntimeError) as error:
                if getattr(error, "uncertain", True) is False:
                    receipt.update(
                        status="rejected",
                        error=str(getattr(error, "code", type(error).__name__))[:128],
                    )
                    cause = str(getattr(error, "code", "UI_OPEN_REJECTED"))
                    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", cause):
                        cause = "UI_OPEN_REJECTED"
                    receipt["cause_code"] = cause
                    diagnostics = getattr(error, "diagnostics", {})
                    if isinstance(diagnostics, dict):
                        # Bounded semantic stage only; no UI tree, names or paths.
                        receipt["preflight_failure"] = {
                            "operation": str(
                                diagnostics.get("preflight_operation", "unknown")
                            )[:64],
                            "stage": str(diagnostics.get("stage", "unknown"))[:64],
                            "project_open_requested": False,
                            "candidate_export_uncertain": bool(
                                getattr(error, "candidate_export_uncertain", True)
                            ),
                            "view_changes_uncertain": bool(
                                getattr(error, "view_changes_uncertain", True)
                            ),
                        }
                        for field in ("operation", "stage"):
                            if not re.fullmatch(
                                r"[a-z][a-z0-9_]{0,63}",
                                receipt["preflight_failure"][field],
                            ):
                                receipt["preflight_failure"][field] = "unknown"
                        if all(
                            type(diagnostics.get(key)) is bool
                            for key in ("invoke_completed", "invoke_succeeded")
                        ):
                            receipt["preflight_failure"]["dispatch"] = {
                                "invoke_completed": diagnostics["invoke_completed"],
                                "invoke_succeeded": diagnostics["invoke_completed"]
                                and diagnostics["invoke_succeeded"],
                            }
                    self._persist_rejected(path, receipt, deadline)
                    raise ProjectOpenError(
                        "UI_OPEN_REJECTED",
                        cause_code=cause,
                        preflight_stage="/".join(
                            receipt.get("preflight_failure", {}).get(key, "unknown")
                            for key in ("operation", "stage")
                        ),
                        preflight_failure=receipt.get("preflight_failure"),
                    ) from error
                receipt["last_error"] = str(
                    getattr(error, "code", type(error).__name__)
                )[:128]
            receipt["status"] = "awaiting_native"
            try:
                with operation_lock(self.profile):
                    atomic_json(path, receipt)
            except OperationBusy:
                # The watcher may already be bootstrapping the just-opened
                # project. Durable dispatch_started still blocks other writes;
                # _wait will persist the next state when that short fence clears.
                pass
            return self._wait(path, receipt, deadline)
