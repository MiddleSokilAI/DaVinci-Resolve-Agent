"""Managed per-process marker sessions with conservative, durable startup state.

The watcher never opens projects or replays operations. Native sessions and
their receipts stay in their original directories across application restarts.
"""

from __future__ import annotations

import json
import math
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import uuid4

from providers.resolve.lua_editing import atomic_json
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.marker_transport import (
    MarkerLeaseKeeper,
    prepare_marker,
    publish_lease,
)
from providers.resolve.startup_diagnostics import inspect_startup

SCHEMA = 1
MAX_JSON_BYTES = 65536
MAX_GENERATIONS = 1024
PHASES = frozenset(
    {
        "prepared",
        "dispatch_started",
        "awaiting_native",
        "connected",
        "dispatch_unverified",
        "dispatch_failed",
        "resolve_absent",
        "waiting_project",
        "recovery_required",
        "installation_failed",
        "lease_failed",
    }
)
STATUS_PHASES = PHASES | {"disabled", "operation_busy", "dispatch_retry_wait"}


class SupervisorError(RuntimeError):
    """A managed state cannot be safely read, routed or advanced."""


class OperationBusy(SupervisorError):
    """Another process owns a managed operation; wait without deleting its lock."""


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate fields in durable coordinator configuration/state."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SupervisorError("DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def _json(path: Path) -> dict[str, Any]:
    """Read bounded local JSON without following a symbolic evidence file."""
    try:
        if path.is_symlink():
            raise SupervisorError("LINKED_STATE_FILE")
        with path.open("rb") as stream:
            data = stream.read(MAX_JSON_BYTES + 1)
        if len(data) > MAX_JSON_BYTES:
            raise SupervisorError("STATE_SIZE_EXCEEDED")
        result = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_object)
        if not isinstance(result, dict):
            raise SupervisorError("STATE_OBJECT_REQUIRED")
        return result
    except FileNotFoundError:
        raise
    except (OSError, ValueError, RecursionError) as error:
        raise SupervisorError("STATE_UNREADABLE") from error


def _identity(value: Any) -> bool:
    """Recognize generated tokens without coercing booleans or other values."""
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _number(value: Any, minimum: float, maximum: float) -> bool:
    """Accept finite real configuration values within their explicit bounds."""
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and minimum <= value <= maximum
    )


def _absolute(value: Any) -> Path:
    """Resolve one explicitly absolute path without accepting linked leaves."""
    if not isinstance(value, str) or not value or "\0" in value:
        raise SupervisorError("ABSOLUTE_PATH_REQUIRED")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise SupervisorError("ABSOLUTE_UNLINKED_PATH_REQUIRED")
    return path.resolve()


def _media_roots(values: Any) -> tuple[Path, ...]:
    """Canonicalize one bounded, explicit media scope without widening it."""
    if not isinstance(values, list) or len(values) > 20:
        raise SupervisorError("INVALID_MEDIA_ROOT_POLICY")
    roots = tuple(_absolute(value) for value in values)
    keys = {os.path.normcase(str(root)) for root in roots}
    if len(keys) != len(roots) or any(not root.is_dir() for root in roots):
        raise SupervisorError("MEDIA_ROOT_MUST_EXIST_AND_BE_UNIQUE")
    return roots


def _validate_runtime_policy(profile: Profile, metadata: dict[str, Any]) -> None:
    """Bind prepared native media scope and lease to the current explicit profile."""
    actual = {
        os.path.normcase(str(root))
        for root in _media_roots(
            metadata.get("media_roots"),
        )
    }
    expected = {os.path.normcase(str(root)) for root in profile.media_roots}
    if actual != expected:
        raise SupervisorError("RUNTIME_MEDIA_SCOPE_MISMATCH")
    if (
        type(metadata.get("lease_seconds")) is not int
        or metadata["lease_seconds"] != profile.lease_seconds
    ):
        raise SupervisorError("RUNTIME_LEASE_POLICY_MISMATCH")


@dataclass(frozen=True)
class Profile:
    """One explicitly enabled watcher profile and its controlled runtime base."""

    path: Path
    runtime_base: Path
    media_roots: tuple[Path, ...]
    enabled: bool = False
    initial_runtime: Path | None = None
    lease_seconds: int = 600
    native_timeout_seconds: float = 10
    poll_interval_seconds: float = 2
    startup_timeout_seconds: float = 180

    @property
    def state_directory(self) -> Path:
        """Keep the manifest, journal and cross-process locks beside the profile."""
        return self.path.parent

    @classmethod
    def load(cls, path: Path) -> Profile:
        """Load bounded, explicit opt-in configuration without creating anything."""
        path = path.absolute()
        data = _json(path)
        allowed = {
            "schema",
            "enabled",
            "runtime_base",
            "media_roots",
            "initial_runtime",
            "lease_seconds",
            "native_timeout_seconds",
            "poll_interval_seconds",
            "startup_timeout_seconds",
        }
        if set(data) - allowed or type(data.get("schema")) is not int:
            raise SupervisorError("INVALID_PROFILE_FIELDS")
        if data["schema"] != SCHEMA or type(data.get("enabled")) is not bool:
            raise SupervisorError("INVALID_PROFILE_SCHEMA_OR_OPT_IN")
        base = _absolute(data.get("runtime_base"))
        if not base.is_dir():
            raise SupervisorError("INVALID_PROFILE_DIRECTORIES")
        media_roots = _media_roots(data.get("media_roots", []))
        initial = (
            _absolute(data["initial_runtime"])
            if data.get("initial_runtime") is not None
            else None
        )
        if initial is not None and (
            not initial.is_relative_to(base) or initial == base or not initial.is_dir()
        ):
            raise SupervisorError("INITIAL_RUNTIME_OUTSIDE_BASE_OR_MISSING")
        lease = data.get("lease_seconds", 600)
        native = data.get("native_timeout_seconds", 10)
        poll = data.get("poll_interval_seconds", 2)
        startup = data.get("startup_timeout_seconds", 180)
        if (
            type(lease) is not int
            or not 5 <= lease <= 3600
            or not _number(native, 0.01, 120)
            or not _number(poll, 0.1, 60)
            or not _number(startup, 10, 600)
        ):
            raise SupervisorError("INVALID_PROFILE_TIMEOUT")
        return cls(
            path.resolve(),
            base,
            media_roots,
            data["enabled"],
            initial,
            lease,
            float(native),
            float(poll),
            float(startup),
        )


@contextmanager
def _lock(profile: Profile, filename: str) -> Iterator[None]:
    """Hold an OS-released advisory lock on a persistent bounded local file."""
    path = profile.state_directory / filename
    if path.is_symlink() or (
        path.exists()
        and getattr(path.lstat(), "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise SupervisorError("LINKED_LOCK_FILE")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    locked = False
    try:
        if os.fstat(stream.fileno()).st_size > 1024:
            raise SupervisorError("LOCK_FILE_SIZE_EXCEEDED")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = import_module("fcntl")
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise OperationBusy(filename) from error
        locked = True
        stream.seek(0)
        stream.write(f"{os.getpid()}:{uuid4().hex}\n".encode("ascii"))
        stream.truncate()
        yield
    finally:
        try:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl = import_module("fcntl")
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


@contextmanager
def operation_lock(profile: Profile) -> Iterator[None]:
    """Pin frontend operations and coordinator transitions under the same lock."""
    with _lock(profile, "operation.lock"):
        yield


@contextmanager
def catalogue_lock(profile: Profile) -> Iterator[None]:
    """Serialize private catalogue initialization without claiming an operation."""
    with _lock(profile, "catalogue.lock"):
        yield


@contextmanager
def project_open_lock(profile: Profile) -> Iterator[None]:
    """Serialize an entire host project-open flow without blocking bootstrap ticks."""
    with _lock(profile, "project-open.lock"):
        yield


def _runtime(profile: Profile, value: Any) -> Path:
    """Require a non-base runtime inside the explicitly controlled directory."""
    root = _absolute(value)
    if root == profile.runtime_base or not root.is_relative_to(profile.runtime_base):
        raise SupervisorError("RUNTIME_OUTSIDE_BASE")
    return root


def _snapshot(profile: Profile, value: dict[str, Any]) -> dict[str, Any]:
    """Validate a generation locator against its actual prepared marker session."""
    if (
        value.get("schema") != SCHEMA
        or not _identity(value.get("generation"))
        or not _identity(value.get("session"))
        or not isinstance(value.get("phase"), str)
        or value["phase"] not in PHASES
        or type(value.get("dispatch_started")) is not bool
    ):
        raise SupervisorError("INVALID_ACTIVE_GENERATION")
    root = _runtime(profile, value.get("runtime"))
    metadata = _json(root / "session.json")
    from providers.resolve.marker_transport import validate_marker_metadata

    try:
        validate_marker_metadata(metadata)
    except (ValueError, TypeError) as error:
        raise SupervisorError("INVALID_RUNTIME_METADATA") from error
    if metadata["session"] != value["session"]:
        raise SupervisorError("ACTIVE_SESSION_MISMATCH")
    _validate_runtime_policy(profile, metadata)
    process = value.get("process")
    if (
        not isinstance(process, dict)
        or type(process.get("pid")) is not int
        or process["pid"] <= 0
        or not isinstance(process.get("creation_time"), str)
        or not process["creation_time"].isdigit()
        or not isinstance(process.get("executable_path"), str)
        or not process["executable_path"]
        or type(process.get("window_handle")) is not int
        or process["window_handle"] <= 0
    ):
        raise SupervisorError("INVALID_PROCESS_TOKEN")
    if value.get("last_native_project") is not None:
        _project(value["last_native_project"])
    return value


def read_active(profile: Profile) -> dict[str, Any] | None:
    """Read the current validated generation; its proof remains historical."""
    try:
        return _snapshot(profile, _json(profile.state_directory / "active.json"))
    except FileNotFoundError:
        return None


def read_status(profile: Profile) -> dict[str, Any] | None:
    """Read a bounded watcher heartbeat, keeping absence separate from corruption.

    The returned phase and timestamp describe the watcher, never native liveness.
    Invalid evidence raises rather than manufacturing a healthy or absent state.
    """
    try:
        value = _json(profile.state_directory / "status.json")
    except FileNotFoundError:
        return None
    allowed = {"schema", "phase", "updated_at", "reason", "generation", "host_state"}
    if (
        set(value) - allowed
        or type(value.get("schema")) is not int
        or value["schema"] != SCHEMA
        or not isinstance(value.get("phase"), str)
        or value["phase"] not in STATUS_PHASES
        or not _number(value.get("updated_at"), 0, 1e20)
        or "generation" in value
        and not _identity(value["generation"])
        or any(
            field in value
            and (not isinstance(value[field], str) or len(value[field]) > 128)
            for field in ("reason", "host_state")
        )
    ):
        raise SupervisorError("INVALID_SUPERVISOR_STATUS")
    return value


def runtime_history(profile: Profile) -> list[Path]:
    """List only journaled generations and the explicit initial runtime."""
    roots: list[Path] = []
    directory = profile.state_directory / "generations"
    if directory.is_symlink() or directory.resolve().parent != profile.state_directory:
        raise SupervisorError("INVALID_GENERATION_JOURNAL")
    paths: list[Path] = []
    for path in directory.glob("*.json"):
        paths.append(path)
        if len(paths) > MAX_GENERATIONS:
            raise SupervisorError("GENERATION_HISTORY_BOUND_EXCEEDED")
    for path in sorted(paths):
        value = _snapshot(profile, _json(path))
        if path.stem != value["generation"]:
            raise SupervisorError("GENERATION_JOURNAL_NAME_MISMATCH")
        root = _runtime(profile, value["runtime"])
        if root not in roots:
            roots.append(root)
    if profile.initial_runtime is not None and profile.initial_runtime not in roots:
        roots.append(profile.initial_runtime)
    return roots


def _process_token(process: dict[str, Any]) -> tuple[Any, ...]:
    """Application identity survives window/title changes but not PID reuse."""
    return (
        process["pid"],
        process["creation_time"],
        os.path.normcase(process["executable_path"]),
    )


def _project(value: Any) -> dict[str, str]:
    """Accept only a fresh bounded native project identity response."""
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("id"), str)
        or not 1 <= len(value["id"]) <= 128
        or not isinstance(value.get("name"), str)
        or not 1 <= len(value["name"]) <= 512
    ):
        raise SupervisorError("INVALID_NATIVE_PROJECT_PROOF")
    return {"id": value["id"], "name": value["name"]}


class Supervisor:
    """Coordinate once-only menu dispatch for each observed Resolve process."""

    def __init__(
        self,
        profile: Profile,
        *,
        adapter: Any = None,
        client_factory: Callable[[Path], Any] = LuaSnapshotClient,
        menu_installer: Callable[[Path], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Inject offline doubles without changing the production command path."""
        self.profile = profile
        if adapter is None:
            from providers.resolve.windows_bootstrap import WindowsBootstrapAdapter

            adapter = WindowsBootstrapAdapter(enabled=profile.enabled)
        self.adapter = adapter
        self.client_factory = client_factory
        self.menu_installer = menu_installer or self._install_menu
        self.clock = clock
        self._stop = threading.Event()

    @staticmethod
    def _install_menu(root: Path) -> Path:
        """Reuse the existing managed-file overwrite protection."""
        from mcp_server.free_marker import install_menu_script

        return install_menu_script(root)

    def _status(self, phase: str, **details: Any) -> dict[str, Any]:
        """Publish watcher heartbeat separately from native generation proof."""
        result = {
            "schema": SCHEMA,
            "phase": phase,
            "updated_at": self.clock(),
            **details,
        }
        atomic_json(self.profile.state_directory / "status.json", result)
        return result

    def _save(self, active: dict[str, Any]) -> None:
        """Persist generation state before any corresponding UI dispatch."""
        _snapshot(self.profile, active)
        directory = self.profile.state_directory / "generations"
        directory.mkdir(exist_ok=True)
        if (
            directory.is_symlink()
            or directory.resolve().parent != self.profile.state_directory
        ):
            raise SupervisorError("INVALID_GENERATION_JOURNAL")
        atomic_json(directory / f"{active['generation']}.json", active)
        atomic_json(self.profile.state_directory / "active.json", active)

    def _audit_rotation(self) -> list[Path]:
        """Refuse migration around any earlier uncertain operation evidence."""
        roots = runtime_history(self.profile)
        for root in roots:
            _validate_runtime_policy(self.profile, _json(root / "session.json"))
            evidence = inspect_startup(root)
            recovery = evidence["recovery"]
            if (
                evidence["metadata_status"] != "valid"
                or evidence["native_start_evidence"] == "evidence_invalid"
                or recovery["status"] != "inspected"
                or recovery["pending_receipt_count"] != 0
                or recovery["inbox_occupied"]
                or recovery["client_lock_present"]
                or recovery["lease_publish_lock_present"]
            ):
                raise SupervisorError("PREVIOUS_RUNTIME_REQUIRES_RECOVERY")
            for path in (root / "receipts").glob("*.json"):
                receipt = _json(path)
                fingerprint = receipt.get("fingerprint")
                if (
                    receipt.get("status") not in {"completed", "rejected"}
                    or len(path.stem) != 64
                    or any(
                        character not in "0123456789abcdef" for character in path.stem
                    )
                    or not isinstance(fingerprint, str)
                    or len(fingerprint) != 64
                    or any(
                        character not in "0123456789abcdef" for character in fingerprint
                    )
                    or not _identity(receipt.get("command_id"))
                    or receipt["status"] == "completed"
                    and not isinstance(receipt.get("result"), dict)
                    or receipt["status"] == "rejected"
                    and not isinstance(receipt.get("error"), str)
                ):
                    raise SupervisorError("PREVIOUS_RECEIPT_OUTCOME_UNKNOWN")
        return roots

    def _new(
        self,
        process: dict[str, Any],
        previous_runtime: Path | None = None,
    ) -> dict[str, Any]:
        """Adopt an explicit pristine root once, otherwise prepare a new one."""
        self._audit_rotation()
        initial = self.profile.initial_runtime
        journaled = any((self.profile.state_directory / "generations").glob("*.json"))
        if initial is not None and not journaled:
            evidence = inspect_startup(initial)
            if evidence["native_start_evidence"] != "not_observed":
                raise SupervisorError("INITIAL_RUNTIME_ALREADY_CONSUMED")
            root = initial
        else:
            root = self.profile.runtime_base / ("managed-" + uuid4().hex)
            prepare_marker(
                root,
                list(self.profile.media_roots),
                lease_seconds=self.profile.lease_seconds,
            )
        metadata = _json(root / "session.json")
        active: dict[str, Any] = {
            "schema": SCHEMA,
            "generation": uuid4().hex,
            "runtime": str(root),
            "session": metadata["session"],
            "process": dict(process),
            "phase": "prepared",
            "dispatch_started": False,
            "last_native_project": None,
            "last_native_verified_at": None,
            "previous_runtime": (
                str(previous_runtime) if previous_runtime is not None else None
            ),
            "dispatch_started_at": None,
            "prepared_at": self.clock(),
        }
        self._save(active)
        return active

    @contextmanager
    def _lease_scope(
        self,
        active: dict[str, Any] | None,
    ) -> Iterator[Callable[[], None]]:
        """Keep a known native generation leased across host waits and polling."""
        keeper: MarkerLeaseKeeper | None = None
        if (
            self.profile.enabled
            and active is not None
            and active["phase"]
            not in {
                "resolve_absent",
                "dispatch_unverified",
                "dispatch_failed",
                "recovery_required",
            }
        ):
            keeper = MarkerLeaseKeeper(_runtime(self.profile, active["runtime"]))
            keeper.__enter__()

        def release() -> None:
            nonlocal keeper
            if keeper is not None:
                keeper.__exit__(None, None, None)
                keeper = None

        try:
            yield release
        finally:
            release()

    def _tick(self) -> dict[str, Any]:
        """Advance one bounded observation while both coordinator locks are held."""
        if not self.profile.enabled:
            return self._status("disabled")
        active = read_active(self.profile)
        with self._lease_scope(active) as release_previous_lease:
            return self._observe(active, release_previous_lease)

    def _observe(
        self,
        active: dict[str, Any] | None,
        release_previous_lease: Callable[[], None],
    ) -> dict[str, Any]:
        """Inspect and advance state while retaining the original lease as needed."""
        evidence = self.adapter.inspect()
        state, process = evidence.get("state"), evidence.get("process")
        if state == "resolve_not_running":
            release_previous_lease()
            if active is not None:
                active["phase"] = "resolve_absent"
                self._save(active)
            return self._status("resolve_absent")
        same_process = (
            active is not None
            and isinstance(process, dict)
            and _process_token(active["process"]) == _process_token(process)
        )
        if active is not None and isinstance(process, dict) and not same_process:
            release_previous_lease()
        if (
            active is not None
            and active["phase"] == "connected"
            and (same_process or process is None)
        ):
            lifecycle = inspect_startup(_runtime(self.profile, active["runtime"]))
            if lifecycle["terminal"] or (
                lifecycle["native_start_evidence"] == "evidence_invalid"
            ):
                active.update(
                    phase="recovery_required",
                    last_error=(
                        "NATIVE_SESSION_EXITED"
                        if lifecycle["terminal"]
                        else "NATIVE_LIFECYCLE_EVIDENCE_INVALID"
                    ),
                )
                self._save(active)
                return self._status("recovery_required", reason=active["last_error"])
            return self._status(
                "connected",
                generation=active["generation"],
                host_state=str(state)[:128],
            )
        if state != "project_window" or evidence.get("eligible") is not True:
            if active is not None:
                if same_process:
                    publish_lease(_runtime(self.profile, active["runtime"]))
                if active["phase"] not in {
                    "dispatch_unverified",
                    "dispatch_failed",
                    "recovery_required",
                }:
                    active["phase"] = "waiting_project"
                    self._save(active)
            return self._status("waiting_project", host_state=state)
        if not isinstance(process, dict):
            raise SupervisorError("BOOTSTRAP_PROCESS_EVIDENCE_MISSING")
        if active is None or _process_token(active["process"]) != _process_token(
            process
        ):
            active = self._new(
                process,
                _runtime(self.profile, active["runtime"])
                if active is not None
                else None,
            )
        root = _runtime(self.profile, active["runtime"])
        publish_lease(root)
        lifecycle = inspect_startup(root)
        if lifecycle["terminal"] or (
            lifecycle["native_start_evidence"] == "evidence_invalid"
        ):
            active.update(
                phase="recovery_required",
                last_error=(
                    "NATIVE_SESSION_EXITED"
                    if lifecycle["terminal"]
                    else "NATIVE_LIFECYCLE_EVIDENCE_INVALID"
                ),
            )
            self._save(active)
            return self._status("recovery_required", reason=active["last_error"])
        if active["phase"] == "connected":
            return self._status("connected", generation=active["generation"])
        if active["phase"] in {
            "dispatch_unverified",
            "dispatch_failed",
            "recovery_required",
        }:
            return self._status(active["phase"], generation=active["generation"])
        if not active["dispatch_started"]:
            prepared = active.get("prepared_at")
            if not isinstance(prepared, (int, float)) or (
                self.clock() - prepared > self.profile.startup_timeout_seconds
            ):
                active.update(phase="dispatch_failed", last_error="STARTUP_TIMEOUT")
                self._save(active)
                return self._status("dispatch_failed", reason="STARTUP_TIMEOUT")
            try:
                self.menu_installer(root)
            except (OSError, ValueError, RuntimeError) as error:
                active.update(
                    phase="installation_failed", last_error=type(error).__name__
                )
                self._save(active)
                return self._status(
                    "installation_failed", generation=active["generation"]
                )
            active.update(
                phase="dispatch_started",
                dispatch_started=True,
                dispatch_started_at=self.clock(),
            )
            self._save(active)
            try:
                with MarkerLeaseKeeper(root):
                    self.adapter.invoke_managed_marker(root, process)
            except (OSError, ValueError, RuntimeError) as error:
                reason = str(getattr(error, "code", type(error).__name__))[:128]
                if getattr(error, "uncertain", True) is False:
                    active.update(
                        phase="prepared",
                        dispatch_started=False,
                        dispatch_started_at=None,
                        last_error=reason,
                    )
                    self._save(active)
                    return self._status("dispatch_retry_wait", reason=reason)
                active["last_error"] = reason
        started = active.get("prepared_at")
        started_seconds = float(started) if isinstance(started, (int, float)) else None
        if active["last_native_project"] is None and (
            started_seconds is None
            or started_seconds > self.clock()
            or self.clock() - started_seconds > self.profile.startup_timeout_seconds
        ):
            active["phase"] = "dispatch_unverified"
            self._save(active)
            return self._status("dispatch_unverified", generation=active["generation"])
        try:
            with MarkerLeaseKeeper(root):
                result = self.client_factory(root).request(
                    "get_current_project",
                    self.profile.native_timeout_seconds,
                )
            if not isinstance(result, dict):
                raise SupervisorError("INVALID_NATIVE_PROJECT_PROOF")
            project = _project(result.get("project"))
        except (OSError, RuntimeError, ValueError) as error:
            active.update(phase="awaiting_native", last_error=type(error).__name__)
        else:
            active.update(
                phase="connected",
                last_native_project=project,
                last_native_verified_at=self.clock(),
            )
            active.pop("last_error", None)
        self._save(active)
        return self._status(active["phase"], generation=active["generation"])

    def tick(self) -> dict[str, Any]:
        """Perform one bounded tick; lock contention never removes another lock."""
        try:
            with _lock(self.profile, "supervisor.lock"), operation_lock(self.profile):
                return self._tick()
        except OperationBusy:
            return {"schema": SCHEMA, "phase": "operation_busy"}
        except (OSError, ValueError, RuntimeError) as error:
            return self._status("recovery_required", reason=str(error)[:128])

    def run(self, stop_event: threading.Event | None = None) -> None:
        """Poll in this user session without launching, closing or recording."""
        stop = self._stop if stop_event is None else stop_event
        with _lock(self.profile, "supervisor.lock"):
            while not stop.is_set():
                try:
                    with operation_lock(self.profile):
                        self._tick()
                except OperationBusy:
                    self._status("operation_busy")
                except (OSError, ValueError, RuntimeError) as error:
                    self._status("recovery_required", reason=str(error)[:128])
                try:
                    with self._lease_scope(read_active(self.profile)):
                        stop.wait(self.profile.poll_interval_seconds)
                except (OSError, ValueError, RuntimeError) as error:
                    self._status("recovery_required", reason=str(error)[:128])
                    stop.wait(self.profile.poll_interval_seconds)

    def close(self) -> None:
        """Request watcher shutdown; no native bridge or application is stopped."""
        self._stop.set()
