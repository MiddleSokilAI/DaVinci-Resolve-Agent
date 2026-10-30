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

    def __init__(self, code: str, *, uncertain: bool = False) -> None:
        self.code = code
        self.uncertain = uncertain
        super().__init__(f"Project opening: {code}. No Open action was resubmitted.")


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


def _receipt(path: Path) -> dict[str, Any]:
    _unlinked(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_RECEIPT_BYTES + 1)
        if len(raw) > _MAX_RECEIPT_BYTES:
            raise ValueError("Receipt exceeds its bound.")
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object)
        if (
            not isinstance(value, dict)
            or type(value.get("schema")) is not int
            or value["schema"] != 1
            or value.get("action") != "open_existing_project"
            or value.get("status") not in _STATUSES
            or value.get("key_hash") != path.stem
            or not re.fullmatch(r"[0-9a-f]{64}", path.stem)
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
            or any(
                not isinstance(binding[key], str) or not binding[key] for key in binding
            )
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
    ) -> None:
        self.profile = profile
        self.adapter = adapter or WindowsProjectManagerAdapter(
            enabled=profile.enabled,
            evidence_directory=profile.state_directory / "project-open-evidence",
        )
        self.supervisor_factory = supervisor_factory
        self.client_factory = client_factory
        self.clock, self.sleep = clock, sleep

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
                                path, receipt, active, proof, no_op=False
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
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        """Select once, then require native context; retries never repeat Open."""
        if not self.profile.enabled:
            raise ProjectOpenError("OPT_IN_REQUIRED")
        if confirm is not True:
            raise ProjectOpenError("CONFIRMATION_REQUIRED")
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
                        raise ProjectOpenError("ORIGINAL_OPEN_REJECTED")
                for other_path, value in records.items():
                    if other_path != path and value["status"] in _PENDING:
                        raise ProjectOpenError(
                            "ANOTHER_PROJECT_OPEN_PENDING", uncertain=True
                        )
                if receipt is None:
                    self._native_clear(digest)
            if receipt is not None:
                return self._wait(path, receipt, deadline)
            try:
                observation = self.adapter.inspect()
            except (OSError, ValueError, RuntimeError) as error:
                raise ProjectOpenError(
                    "PROJECT_MANAGER_INSPECTION_UNAVAILABLE"
                ) from error
            process = observation.get("process")
            if process is None:
                raise ProjectOpenError("PROJECT_MANAGER_PROCESS_UNVERIFIED")
            token = _saved_process(process)
            receipt = {
                "schema": 1,
                "action": "open_existing_project",
                "key_hash": digest,
                "fingerprint": fingerprint,
                "arguments": arguments,
                "status": "prepared",
                "process": token,
                "native_binding": None,
            }
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
                if (
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
                receipt["status"] = "dispatch_started"
                atomic_json(path, receipt)
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
                    self._persist_rejected(path, receipt, deadline)
                    raise ProjectOpenError("UI_OPEN_REJECTED") from error
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
