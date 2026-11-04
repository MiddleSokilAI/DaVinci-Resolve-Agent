"""Once-only cold project creation with fixed background UIA and native proof.

Every dispatch has a durable boundary. Recovery reads the original process and
generation, never repeats New Project, Create or an uncertain bridge invocation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any

from providers.resolve.lua_editing import atomic_json, bounded_text
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.marker_supervisor import (
    OperationBusy,
    Profile,
    Supervisor,
    _lock,
    read_active,
)
from providers.resolve.marker_transport import MarkerLeaseKeeper
from providers.resolve.project_library_catalogue import selected_project_library
from providers.resolve.project_open_service import (
    ProjectOpenService,
    _fingerprint,
    _object,
    _token,
    _unlinked,
)
from providers.resolve.windows_bootstrap import (
    WindowsBootstrapAdapter,
    WindowsBootstrapError,
    _official_executable,
)
from providers.resolve.windows_project_create import (
    WindowsProjectCreateAdapter,
    WindowsProjectCreateError,
)
from providers.resolve.windows_project_manager import WindowsProjectManagerAdapter

_PHASES = {"dialog_pending", "create_pending", "bootstrapping", "completed"}
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def _valid_project(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"id", "name"}
        and isinstance(value["id"], str)
        and _UUID.fullmatch(value["id"]) is not None
        and isinstance(value["name"], str)
        and 1 <= len(value["name"]) <= 128
    )


class ProjectCreateError(RuntimeError):
    def __init__(
        self, code: str, *, uncertain: bool = False,
        cause_code: str | None = None,
        bridge_dispatch_uncertain: bool | None = None,
    ) -> None:
        self.code, self.uncertain = code, uncertain
        self.cause_code = cause_code
        self.bridge_dispatch_uncertain = bridge_dispatch_uncertain
        cause = f" Bridge cause: {cause_code}." if cause_code is not None else ""
        super().__init__(
            f"Project creation: {code}.{cause} No creation action was repeated."
        )


def validate_creation(name: Any, confirm: Any, key: Any, timeout: Any) -> None:
    """Validate before any UI inspection, ownership change or dispatch."""
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 128
        or name != name.strip()
        or re.search(r"[\x00-\x1f\x7f]", name)
        or any(char in name for char in '<>:"/\\|?*')
        or name.endswith(".")
    ):
        raise ValueError(
            "A safe, trimmed project name of 1..128 characters is required."
        )
    if confirm is not True:
        raise ValueError("confirm_create must be true.")
    bounded_text(key, "Idempotency key")
    if (
        type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or not 0 < timeout <= 600
    ):
        raise ValueError("timeout_seconds must be finite and in (0, 600].")


def creation_receipts(profile: Profile) -> dict[Path, dict[str, Any]]:
    """Bound and validate all durable creation evidence; corruption blocks writes."""
    folder = profile.state_directory / "project-create-receipts"
    _unlinked(folder)
    if not folder.exists():
        return {}
    if not folder.is_dir():
        raise ProjectCreateError("CREATION_RECEIPTS_INVALID", uncertain=True)
    records = {}
    for index, path in enumerate(folder.iterdir()):
        _unlinked(path)
        if index >= 1024 or not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
            raise ProjectCreateError("CREATION_RECEIPTS_INVALID", uncertain=True)
        try:
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError("Receipt bound")
            value = json.loads(raw, object_pairs_hook=_object)
            if (
                not isinstance(value, dict)
                or type(value.get("schema")) is not int
                or value["schema"] != 1
                or value.get("action") != "create_project"
                or value.get("key_hash") != path.stem
                or value.get("phase") not in _PHASES
                or not isinstance(value.get("name"), str)
                or value.get("fingerprint") != _fingerprint({"name": value["name"]})
                or not isinstance(value.get("before"), dict)
            ):
                raise ValueError("Receipt fields")
            validate_creation(value["name"], True, path.stem, 30)
            _token(value.get("process"))
            before = value["before"]
            if (
                set(before) != {"database", "projects", "library_path"}
                or not isinstance(before["database"], dict)
                or set(before["database"]) != {"DbType", "DbName"}
                or before["database"]["DbType"] != "Disk"
                or not isinstance(before["database"]["DbName"], str)
                or not 1 <= len(before["database"]["DbName"]) <= 128
                or not isinstance(before["library_path"], str)
                or not before["library_path"]
                or not isinstance(before["projects"], list)
                or len(before["projects"]) > 128
                or not all(_valid_project(item) for item in before["projects"])
                or len({item["id"] for item in before["projects"]})
                != len(before["projects"])
            ):
                raise ValueError("Precreation evidence")
            binding = value.get("native_binding")
            previous_processes = value.get("previous_dialog_processes", [])
            if not isinstance(previous_processes, list) or len(previous_processes) > 8:
                raise ValueError("Dialog process history")
            for previous_process in previous_processes:
                _token(previous_process)
            if binding is not None and (
                not isinstance(binding, dict)
                or set(binding) != {"runtime", "session", "generation"}
                or any(
                    not isinstance(item, str) or not item for item in binding.values()
                )
            ):
                raise ValueError("Native binding")
            if value["phase"] == "completed":
                result = value.get("result")
                if (
                    not isinstance(result, dict)
                    or not _valid_project(result.get("project"))
                    or result.get("project", {}).get("name") != value["name"]
                    or result.get("project") in before["projects"]
                    or binding is None
                    or result.get("session") != binding["session"]
                    or result.get("generation") != binding["generation"]
                    or result.get("database") != before["database"]
                    or result.get("folder") != ""
                    or result.get("context_verified") is not True
                    or result.get("folder_membership_verified") is not True
                    or result.get("source") != "live_lua_api"
                    or result.get("readback_verified_by") != "ResolveLuaProjectContext"
                    or result.get("persisted") is not True
                    or result.get("native_connection_verified") is not True
                    or result.get("proof_scope") != "fresh_native_context"
                ):
                    raise ValueError("Completion proof")
            records[path] = value
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            RecursionError,
        ) as error:
            raise ProjectCreateError(
                "CREATION_RECEIPTS_INVALID", uncertain=True
            ) from error
    return records


def pending_project_creation(profile: Profile) -> list[dict[str, Any]]:
    return [
        value
        for value in creation_receipts(profile).values()
        if value["phase"] != "completed"
    ]


class ProjectCreateService:
    """Create only from the current Project Manager; never open a saved project."""

    def __init__(
        self,
        profile: Profile,
        *,
        ui: Any = None,
        host: Any = None,
        manager: Any = None,
        catalogue: Any = selected_project_library,
        supervisor_factory: Any = Supervisor,
        client_factory: Any = LuaSnapshotClient,
    ) -> None:
        self.profile = profile
        self.ui = ui or WindowsProjectCreateAdapter()
        self.host = host or WindowsBootstrapAdapter(enabled=profile.enabled)
        self.manager = manager or WindowsProjectManagerAdapter(enabled=profile.enabled)
        self.catalogue = catalogue
        self.supervisor_factory = supervisor_factory
        self.client_factory = client_factory

    def owns_key(self, key: str | None) -> bool:
        if not isinstance(key, str):
            return False
        digest = hashlib.sha256(key.encode()).hexdigest()
        return any(path.stem == digest for path in creation_receipts(self.profile))

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProjectCreateError(
                "ORIGINAL_CREATION_REQUIRES_RECOVERY", uncertain=True
            )
        return min(remaining, self.profile.native_timeout_seconds)

    def _manager(self, deadline: float) -> dict[str, Any]:
        """Launch at most once and await Project Manager without opening history.

        This runs under the operation lock after pending-write guards. Startup
        alone never owns the creation key; a later call can use that same key
        after a startup timeout. Creation intent is persisted before New Project.
        """
        launched: subprocess.Popen[bytes] | None = None
        launched_pid: int | None = None
        observed_process: tuple[int, str, str] | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProjectCreateError("PROJECT_MANAGER_STARTUP_TIMEOUT")
            observation = self.host.inspect()
            state, process = observation.get("state"), observation.get("process")
            if process is not None:
                token = _token(process)
                if (
                    launched_pid is not None and token[0] != launched_pid
                    or observed_process is not None and token != observed_process
                ):
                    raise ProjectCreateError("STARTUP_PROCESS_CHANGED")
                observed_process = token
            if state == "project_manager" and process is not None:
                return dict(process)
            if state == "resolve_not_running":
                if observed_process is not None or (
                    launched is not None and launched.poll() is not None
                ):
                    # Do not relaunch a failed/replaced process in this call.
                    raise ProjectCreateError("RESOLVE_STARTUP_EXITED")
                if launched is None:
                    executable = Path(_official_executable())
                    if not executable.is_file():
                        raise ProjectCreateError("RESOLVE_NOT_INSTALLED")
                    try:
                        launched = subprocess.Popen(
                            [str(executable)],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            shell=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                    except OSError as error:
                        raise ProjectCreateError("RESOLVE_STARTUP_FAILED") from error
                    launched_pid = launched.pid
            elif state != "window_unavailable":
                # An existing project, a modal or ambiguous desktop is never
                # switched away, focused or treated as an empty Project Manager.
                raise ProjectCreateError("PROJECT_MANAGER_REQUIRED")
            time.sleep(min(0.2, remaining))

    def create(
        self,
        *,
        name: str,
        confirm_create: bool,
        idempotency_key: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        validate_creation(name, confirm_create, idempotency_key, timeout_seconds)
        if not self.profile.enabled:
            raise ProjectCreateError("AUTOMATIC_CONNECTION_DISABLED")
        # Local import avoids the automatic-connection/project-open import cycle.
        from providers.resolve.automatic_connection import wait_operation
        from providers.resolve.project_close_state import close_intent
        from providers.resolve.project_open_service import pending_project_open

        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        path = (
            self.profile.state_directory / "project-create-receipts" / f"{digest}.json"
        )
        deadline = time.monotonic() + timeout_seconds
        with wait_operation(self.profile, timeout_seconds):
            begin_attempted = False
            records = creation_receipts(self.profile)
            receipt = records.get(path)
            if receipt is not None:
                if receipt["name"] != name:
                    raise ValueError(
                        "Idempotency key owns a different project creation."
                    )
                if receipt["phase"] == "completed":
                    return {
                        **receipt["result"],
                        "replayed": True,
                        "ui_create_dispatched": False,
                        "native_connection_verified": False,
                        "proof_scope": "historical_completed_receipt",
                    }
            if any(p != path and r["phase"] != "completed" for p, r in records.items()):
                raise ProjectCreateError(
                    "ANOTHER_PROJECT_CREATION_PENDING", uncertain=True
                )
            if (close_intent(self.profile) or {}).get("phase") == "pending":
                raise ProjectCreateError("PROJECT_CLOSE_REQUIRES_RECOVERY")
            if receipt is None:
                if pending_project_open(self.profile)["pending"]:
                    raise ProjectCreateError("PROJECT_OPEN_REQUIRES_RECOVERY")
                guard = ProjectOpenService(self.profile)
                guard._native_clear(digest)
                supervisor = self.supervisor_factory(self.profile)
                supervisor._audit_rotation()
                try:
                    # Share launch ownership with AutomaticConnection.ensure.
                    # Acquisition is nonblocking: never wait for connection.lock
                    # while holding operation.lock (the warm path takes both).
                    with _lock(self.profile, "connection.lock"):
                        process = self._manager(deadline)
                except OperationBusy as error:
                    raise ProjectCreateError("PROJECT_STARTUP_BUSY") from error
                manager = self.manager.inspect()
                if (
                    manager.get("state") != "project_manager"
                    or manager.get("eligible") is not True
                    or _token(manager.get("process")) != _token(process)
                ):
                    raise ProjectCreateError("PROJECT_MANAGER_REQUIRED")
                before = self.catalogue()
                folded = unicodedata.normalize("NFKC", name).casefold()
                if any(
                    unicodedata.normalize("NFKC", item["name"]).casefold() == folded
                    for item in before["projects"]
                ):
                    raise ProjectCreateError("PROJECT_NAME_ALREADY_EXISTS")
                # Exhausted read-only preflight must not publish a dialog intent
                # that recovery would wait for even though Begin never ran.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProjectCreateError("PROJECT_CREATION_PREFLIGHT_TIMEOUT")
                remaining = min(remaining, self.profile.native_timeout_seconds)
                receipt = {
                    "schema": 1,
                    "action": "create_project",
                    "key_hash": digest,
                    "fingerprint": _fingerprint({"name": name}),
                    "name": name,
                    "phase": "dialog_pending",
                    "process": process,
                    "before": {
                        "database": before["database"],
                        "projects": before["projects"],
                        "library_path": before["library_path"],
                    },
                }
                path.parent.mkdir(exist_ok=True)
                # The host receipt also blocks new native claims. Schema 3 makes
                # cached older clients refuse the cross-route ownership record.
                owner = self.profile.state_directory / "write-owners" / f"{digest}.json"
                _unlinked(owner)
                owner.parent.mkdir(exist_ok=True)
                atomic_json(
                    owner,
                    {
                        "schema": 3,
                        "route": "project_manager_create",
                        "receipt": str(path),
                    },
                )
                atomic_json(path, receipt)
                begin_attempted = True
                self.ui.request(
                    "begin", process, timeout_seconds=remaining
                )
            try:
                return self._recover(
                    path, receipt, deadline, begin_attempted=begin_attempted
                )
            except (ProjectCreateError, WindowsProjectCreateError):
                raise
            except (OSError, ValueError, RuntimeError) as error:
                raise ProjectCreateError(
                    "ORIGINAL_CREATION_REQUIRES_RECOVERY", uncertain=True
                ) from error

    def _restart_before_dialog(
        self, path: Path, receipt: dict[str, Any], deadline: float,
    ) -> None:
        """Rebind a stopped application only before any Create was dispatched.

        The unchanged library proves no project was added. A live replacement
        process is never adopted, and later phases keep their original binding.
        """
        if receipt["phase"] != "dialog_pending":
            raise ProjectCreateError(
                "ORIGINAL_CREATION_REQUIRES_RECOVERY", uncertain=True
            )
        host = self.host.inspect()
        if (
            host.get("state") != "resolve_not_running"
            or host.get("process") is not None
        ):
            return
        if (
            receipt.get("dialog") is not None
            or receipt.get("native_binding") is not None
        ):
            raise ProjectCreateError(
                "ORIGINAL_CREATION_REQUIRES_RECOVERY", uncertain=True
            )
        catalogue = self.catalogue()
        if any(
            catalogue[field] != receipt["before"][field]
            for field in ("database", "library_path", "projects")
        ):
            raise ProjectCreateError("PRECREATE_LIBRARY_CHANGED", uncertain=True)
        history = receipt.get("previous_dialog_processes", [])
        if len(history) >= 8:
            raise ProjectCreateError("DIALOG_RESTART_LIMIT", uncertain=True)
        self.supervisor_factory(self.profile)._audit_rotation()
        # The caller already owns operation.lock. Use the same nonblocking
        # connection gate as first startup; never reverse-wait for that lock.
        try:
            with _lock(self.profile, "connection.lock"):
                process = self._manager(deadline)
        except OperationBusy as error:
            raise ProjectCreateError("PROJECT_STARTUP_BUSY") from error
        manager = self.manager.inspect()
        if (
            manager.get("state") != "project_manager"
            or manager.get("eligible") is not True
            or _token(manager.get("process")) != _token(process)
        ):
            raise ProjectCreateError("PROJECT_MANAGER_REQUIRED")
        receipt["previous_dialog_processes"] = [*history, receipt["process"]]
        receipt["process"] = process
        atomic_json(path, receipt)

    def _restore_empty_dialog(self, receipt: dict[str, Any], deadline: float) -> None:
        """Restore a proven empty dialog state; this phase has never sent Create.

        Only the original process/window and unchanged selected library can
        resume. Later create_pending/bootstrapping phases never enter this path.
        The fixed Begin driver itself refuses any dialog appearing concurrently.
        """
        process = receipt["process"]
        host = self.host.inspect()
        current = host.get("process")
        if (
            host.get("state") != "project_manager"
            or not isinstance(current, dict)
            or _token(current) != _token(process)
            or current.get("window_handle") != process["window_handle"]
        ):
            raise ProjectCreateError("ORIGINAL_MANAGER_CHANGED", uncertain=True)
        manager = self.manager.inspect()
        if (
            manager.get("state") != "project_manager"
            or manager.get("eligible") is not True
            or _token(manager.get("process")) != _token(process)
        ):
            raise ProjectCreateError("ORIGINAL_MANAGER_CHANGED", uncertain=True)
        catalogue = self.catalogue()
        if any(
            catalogue[field] != receipt["before"][field]
            for field in ("database", "library_path", "projects")
        ):
            raise ProjectCreateError("PRECREATE_LIBRARY_CHANGED", uncertain=True)
        self.ui.request("begin", process, timeout_seconds=self._remaining(deadline))

    def _recover(
        self,
        path: Path,
        receipt: dict[str, Any],
        deadline: float,
        *,
        begin_attempted: bool = False,
    ) -> dict[str, Any]:
        if receipt["phase"] == "dialog_pending" and not begin_attempted:
            self._restart_before_dialog(path, receipt, deadline)
        process = receipt["process"]
        while receipt["phase"] == "dialog_pending":
            dialog = self.ui.request(
                "inspect_dialog",
                process,
                timeout_seconds=self._remaining(deadline),
            ).get("dialog")
            if dialog is None:
                if not begin_attempted:
                    # Receipt proves Create was never dispatched; absence was
                    # read back. Restore Begin once, never blindly in a loop.
                    begin_attempted = True
                    self._restore_empty_dialog(receipt, deadline)
                time.sleep(0.1)
                continue
            current_catalogue = self.catalogue()
            if any(
                current_catalogue[field] != receipt["before"][field]
                for field in ("database", "library_path", "projects")
            ):
                raise ProjectCreateError("PRECREATE_LIBRARY_CHANGED", uncertain=True)
            receipt.update(phase="create_pending", dialog=dialog)
            remaining = self._remaining(deadline)
            atomic_json(path, receipt)
            self.ui.request(
                "create",
                process,
                dialog=dialog,
                name=receipt["name"],
                timeout_seconds=remaining,
            )
            break
        # No Create call occurs for a replay in create_pending/bootstrapping.
        while True:
            remaining = self._remaining(deadline)
            observation = self.host.inspect()
            current = observation.get("process")
            if current is None or _token(current) != _token(process):
                raise ProjectCreateError("ORIGINAL_PROCESS_CHANGED", uncertain=True)
            if observation.get("state") == "project_window" and observation.get(
                "eligible"
            ):
                break
            time.sleep(min(0.1, remaining))
        supervisor = self.supervisor_factory(self.profile)
        active = read_active(self.profile)
        binding = receipt.get("native_binding")
        if binding is None:
            if receipt["phase"] == "bootstrapping":
                raise ProjectCreateError(
                    "ORIGINAL_GENERATION_UNVERIFIED", uncertain=True
                )
            receipt["phase"] = "bootstrapping"
            atomic_json(path, receipt)
            active = supervisor._new(
                current, Path(active["runtime"]) if active else None,
                creation_context={
                    "name": receipt["name"],
                    "database": receipt["before"]["database"],
                    "excluded_project_ids": [
                        project["id"] for project in receipt["before"]["projects"]
                    ],
                },
            )
            receipt["native_binding"] = ProjectOpenService._binding(active)
            atomic_json(path, receipt)
        elif active is None or ProjectOpenService._binding(active) != binding:
            raise ProjectCreateError("ORIGINAL_GENERATION_CHANGED", uncertain=True)
        assert active is not None
        if _token(active["process"]) != _token(process):
            raise ProjectCreateError("ORIGINAL_PROCESS_CHANGED", uncertain=True)
        root = Path(active["runtime"])
        with MarkerLeaseKeeper(root):
            if not active["dispatch_started"]:
                if current.get("project_name_hint") != receipt["name"]:
                    raise ProjectCreateError(
                        "CREATED_PROJECT_WINDOW_CHANGED", uncertain=True
                    )
                supervisor.menu_installer(root)
                self._remaining(deadline)
                active.update(
                    dispatch_started=True,
                    phase="dispatch_started",
                    dispatch_started_at=supervisor.clock(),
                )
                supervisor._save(active)
                try:
                    self.host.invoke_managed_marker(root, current, background_only=True)
                except WindowsBootstrapError as error:
                    active["last_error"] = error.code
                    native_start_observed = any(
                        evidence.exists() or evidence.is_symlink()
                        for evidence in (
                            root / "marker-session-consumed.drp", root / "stopped.json"
                        )
                    )
                    if error.uncertain is False and not native_start_observed:
                        # This attempt definitely did not invoke the bridge.
                        # Keep the original Create receipt/generation; only its
                        # bridge startup may resume on the next same-key call.
                        active.update(
                            phase="prepared", dispatch_started=False,
                            dispatch_started_at=None,
                        )
                    supervisor._save(active)
                    # Create was already sent. A definite bridge refusal must
                    # not imply a definite failure of the project creation.
                    raise ProjectCreateError(
                        "PROJECT_BRIDGE_BOOTSTRAP_FAILED", uncertain=True,
                        cause_code=error.code,
                        bridge_dispatch_uncertain=error.uncertain,
                    ) from error
            result = self.client_factory(root).request(
                "get_current_project",
                self._remaining(deadline),
            )
            project = result.get("project") if isinstance(result, dict) else None
            if (
                not isinstance(project, dict)
                or not _valid_project(project)
                or project.get("name") != receipt["name"]
                or any(
                    item["id"] == project["id"]
                    for item in receipt["before"]["projects"]
                )
            ):
                raise ProjectCreateError(
                    "NEW_NATIVE_PROJECT_UNVERIFIED", uncertain=True
                )
            verifier = ProjectOpenService(
                self.profile, client_factory=self.client_factory
            )
            proof = verifier._verify(
                active,
                {
                    "project_name": receipt["name"],
                    "expected_project_id": project["id"],
                    "expected_database": receipt["before"]["database"],
                },
                self._remaining(deadline),
            )
            after = self.catalogue()
            if (
                after["database"] != receipt["before"]["database"]
                or after["library_path"] != receipt["before"]["library_path"]
                or sorted(after["projects"], key=lambda p: p["id"])
                != sorted(
                    [*receipt["before"]["projects"], project], key=lambda p: p["id"]
                )
            ):
                raise ProjectCreateError(
                    "NEW_PROJECT_PERSISTENCE_UNVERIFIED", uncertain=True
                )
            result = {
                **proof,
                "status": "completed",
                "operation": "create_project",
                "persisted": True,
                "persistence_source": "readonly_project_library_copy_v1",
                "native_connection_verified": True,
                "proof_scope": "fresh_native_context",
                "ui_create_dispatched": True,
                "replayed": False,
                "session": active["session"],
                "generation": active["generation"],
            }
            active.update(
                phase="connected",
                last_native_project=project,
                last_native_verified_at=supervisor.clock(),
            )
            supervisor._save(active)
            receipt.update(phase="completed", result=result)
            atomic_json(path, receipt)
            from providers.resolve.project_close_state import explicit_open_completed

            explicit_open_completed(self.profile)
            return result
