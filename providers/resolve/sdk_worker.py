"""Automatic provider behind the stable canonical filesystem command queue."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from agent.client import BridgeCommandError
from bridges.resolve import ResolveBridge as bridge
from providers.resolve.automatic_connection import (
    AutomaticConnection,
    process_alive,
    wait_operation,
)
from providers.resolve.lua_editing import atomic_json
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.marker_supervisor import (
    OperationBusy,
    Profile,
    _lock,
    _runtime,
    read_active,
)
from providers.resolve.marker_transport import MarkerLeaseKeeper, MarkerSlot
from providers.resolve.project_close_state import (
    close_intent,
    finish_close,
    prepare_close,
)
from providers.resolve.project_open_service import pending_project_open
from providers.resolve.sdk_command_client import SDKCommandClient
from providers.resolve.windows_bootstrap import _official_executable


class SDKWorker:
    """One worker, one complete canonical command, one selected native route.

    Cached MCP clients keep their existing command/result contract. No write
    switches transport after dispatch, including a timeout or SDK failure.
    """

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.root = profile.runtime_base
        self.directories = bridge.ensure_runtime_directories(self.root)
        self.connection = AutomaticConnection(profile)
        self.status_path = profile.state_directory / "sdk-worker.json"
        self._python_available: dict[tuple[Any, ...], bool] = {}

    def _status(self, phase: str, **details: Any) -> None:
        try:
            atomic_json(
                self.status_path,
                {"phase": phase, "updated_at": time.time(), **details},
            )
        except OSError:
            # Monitoring is advisory; command ownership/receipts remain strict.
            logging.getLogger(__name__).exception("SDK status publication failed")

    def _python(self) -> tuple[Path, Path]:
        executable = (
            Path(_official_executable()).parent / "ResolvePython/ResolvePython.exe"
        )
        script = Path(bridge.__file__).with_name("ResolveSDKCommand.py")
        return executable, script

    def _refresh_allowed(self) -> bool:
        try:
            return not pending_project_open(self.profile)["pending"]
        except (OSError, RuntimeError, ValueError):
            return False

    def _external_available(self, active: dict[str, Any]) -> bool:
        process = active["process"]
        token = (process["pid"], process["creation_time"])
        if token not in self._python_available:
            available = False
            executable, script = self._python()
            if sys.platform == "win32" and executable.is_file():
                try:
                    result = subprocess.run(
                        [str(executable), str(script), "--probe"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    available = (
                        result.returncode == 0
                        and json.loads(result.stdout).get("available") is True
                    )
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            self._python_available[token] = available
        return self._python_available[token]

    def _error(self, path: Path, code: str, message: str) -> None:
        try:
            command = json.loads(path.read_text(encoding="utf-8"))
            identity = command.get("command_id", path.stem)
        except (ValueError, AttributeError):
            identity = path.stem
        response = {
            "protocol_version": bridge.PROTOCOL_VERSION,
            "command_id": identity,
            "status": "error",
            "started_at": bridge.utc_now(),
            "finished_at": bridge.utc_now(),
            "result": None,
            "error": {
                "code": code,
                "message": message,
                "details": {},
                "retryable": False,
            },
            "warnings": [],
        }
        atomic_json(self.directories["responses"] / path.name, response)
        failed = self.directories["failed"] / path.name
        if path != failed:
            path.replace(failed)

    def _external(self, path: Path, active: dict[str, Any]) -> None:
        executable, script = self._python()
        process = subprocess.Popen(
            [
                str(executable),
                str(script),
                "--runtime",
                str(self.root),
                "--command",
                path.stem,
                "--expected-project-id",
                active["sdk_expected_project_id"],
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # A client timeout does not kill a native write or release its gate.
        while process.poll() is None:
            time.sleep(0.1)
        if process.returncode != 0:
            raise RuntimeError("EXTERNAL_SDK_COMMAND_UNCERTAIN")

    def _select_route(self, active: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if not self._external_available(active):
            return "lua_marker_preset", active
        # Prove the binding before occupying the native inbox. scriptapp()
        # can attach another project; the helper checks this fresh identity.
        root = Path(active["runtime"])
        with MarkerLeaseKeeper(root):
            proof = LuaSnapshotClient(root).request(
                "get_current_project", self.profile.native_timeout_seconds
            )
        identity = proof["project"]["id"]
        if not isinstance(identity, str) or not 1 <= len(identity) <= 128:
            raise RuntimeError("INVALID_NATIVE_PROJECT_PROOF")
        return "python_sdk", {**active, "sdk_expected_project_id": identity}

    def _replay_close(self, path: Path, command: dict[str, Any]) -> bool:
        """Read a terminal close receipt before connection.ensure can open anything."""
        digest = hashlib.sha256(command["idempotency_key"].encode()).hexdigest()
        receipt = self.profile.state_directory / "sdk-write-owners" / (digest + ".json")
        if not receipt.exists() and not receipt.is_symlink():
            return False
        if receipt.is_symlink() or receipt.stat().st_size > 4 * 1024 * 1024:
            raise RuntimeError("INVALID_SDK_WRITE_OWNER")
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if value.get("fingerprint") != bridge._request_fingerprint(command):
            raise RuntimeError("SDK_IDEMPOTENCY_CONFLICT")
        if value.get("phase") != "completed":
            raise RuntimeError("ORIGINAL_SDK_OPERATION_REQUIRES_RECOVERY")
        response = dict(value["response"])
        root = _runtime(self.profile, value["runtime"])
        if (root / "inbox").exists():
            slot = MarkerSlot.occupied(root, value["session"], 4096)
            if (
                slot.request["action"] == "sdk_operation"
                and slot.identity == UUID(value["command_id"]).hex
            ):
                slot.clear()  # Complete exact terminal cleanup after a host crash.
        finish_close(
            self.profile, value["command_id"], closed=response["status"] == "success"
        )
        response["command_id"] = command["command_id"]
        response["warnings"] = [
            *response["warnings"], "Historical close receipt; no native call was sent."
        ]
        if response["status"] == "success":
            response["result"] = {**response["result"], "replayed": True,
                                  "evidence": "historical_completed_receipt"}
        atomic_json(self.directories["responses"] / path.name, response)
        path.unlink()
        return True

    def process(self, path: Path) -> None:
        command = bridge.validate_command(json.loads(path.read_text(encoding="utf-8")))
        if command["action"] == "close_project":
            with wait_operation(self.profile, self.profile.startup_timeout_seconds):
                if self._replay_close(path, command):
                    return
        if any(self.directories["processing"].glob("*.json")):
            raise RuntimeError("ORIGINAL_COMMAND_REQUIRES_RECOVERY")
        # A previous process may have died after an SDK mutation. Reads must
        # not reset its native operation or consume its response slot either.
        routes = self.profile.state_directory / "sdk-write-owners"
        for receipt in routes.glob("*.json"):
            if receipt.is_symlink() or receipt.stat().st_size > 4 * 1024 * 1024:
                raise RuntimeError("INVALID_SDK_WRITE_OWNER")
            value = json.loads(receipt.read_text(encoding="utf-8"))
            if value.get("phase") != "completed":
                raise RuntimeError("ORIGINAL_SDK_OPERATION_REQUIRES_RECOVERY")
        active = self.connection.ensure(
            require_sdk_channel=False,
            **(
                {"allow_project_open": False}
                if command["action"] == "close_project" else {}
            ),
        )
        with wait_operation(self.profile, self.profile.startup_timeout_seconds):
            command = bridge.validate_command(command)
            current = read_active(self.profile)
            if not current or current["generation"] != active["generation"]:
                raise RuntimeError("NATIVE_GENERATION_CHANGED_BEFORE_DISPATCH")
            native_root = Path(active["runtime"])
            if (native_root / "inbox").exists():
                slot = MarkerSlot.occupied(native_root, active["session"], 4096)
                completed = False
                if slot.request["action"] == "sdk_operation":
                    for receipt in routes.glob("*.json"):
                        value = json.loads(receipt.read_text(encoding="utf-8"))
                        if (
                            value.get("phase") == "completed"
                            and value.get("session") == active["session"]
                            and UUID(value["command_id"]).hex == slot.identity
                        ):
                            completed = True
                            break
                if not completed:
                    raise RuntimeError("ORIGINAL_NATIVE_OPERATION_REQUIRES_RECOVERY")
                slot.clear()  # Finish exact known-outcome cleanup, never replay.
            write = command["action"] in bridge.WRITE_ACTIONS
            guard: MarkerSlot | None = None
            route_record: dict[str, Any] | None = None
            route_path: Path | None = None
            if write:
                from mcp_server.managed_marker import claim_write_key

                if pending_project_open(self.profile)["pending"]:
                    raise RuntimeError("PROJECT_OPEN_VERIFICATION_PENDING")
                digest = hashlib.sha256(command["idempotency_key"].encode()).hexdigest()
                routes = self.profile.state_directory / "sdk-write-owners"
                routes.mkdir(exist_ok=True)
                if routes.is_symlink():
                    raise RuntimeError("LINKED_SDK_WRITE_OWNERS")
                route_path = routes / f"{digest}.json"
                claim_write_key(
                    self.profile, active, command["idempotency_key"], route="sdk"
                )
                fingerprint = bridge._request_fingerprint(command)
                if route_path.exists():
                    if route_path.is_symlink():
                        raise RuntimeError("LINKED_SDK_WRITE_OWNER")
                    route_record = json.loads(route_path.read_text(encoding="utf-8"))
                    if route_record.get("fingerprint") != fingerprint:
                        raise RuntimeError("SDK_IDEMPOTENCY_CONFLICT")
                    if route_record.get("phase") == "completed":
                        response = dict(route_record["response"])
                        response["command_id"] = command["command_id"]
                        atomic_json(self.directories["responses"] / path.name, response)
                        path.unlink()
                        return
                    raise RuntimeError("ORIGINAL_SDK_OPERATION_REQUIRES_RECOVERY")
                route, active = self._select_route(active)
                route_record = {
                    "generation": active["generation"],
                    "session": active["session"],
                    "runtime": str(native_root),
                    "command_id": path.stem,
                    "fingerprint": fingerprint,
                    "phase": "prepared",
                }
                atomic_json(route_path, route_record)
                guard = MarkerSlot(
                    native_root,
                    {
                        "session": active["session"],
                        "id": UUID(path.stem).hex,
                        "action": "sdk_operation",
                        "expires": int(time.time()) + 650,
                    },
                    4096,
                )
                try:
                    guard.publish(time.monotonic() + 10)
                except Exception as error:
                    # This guard is ignored by Lua and no SDK call has begun.
                    # A known pre-dispatch failure must not poison every later
                    # read/write as an uncertain native operation.
                    self._error(path, "SDK_PRE_DISPATCH_FAILED", str(error))
                    route_record.update(
                        phase="completed",
                        response=json.loads(
                            (self.directories["responses"] / path.name).read_text(
                                encoding="utf-8"
                            )
                        ),
                    )
                    atomic_json(route_path, route_record)
                    if guard.published:
                        guard.clear()
                    return
            else:
                route, active = self._select_route(active)
            if route_record is not None and route_path is not None:
                route_record.update(phase="dispatch_started", transport=route)
                atomic_json(route_path, route_record)
            if command["action"] == "close_project":
                prepare_close(
                    self.profile, command["command_id"],
                    bridge._request_fingerprint(command),
                )
            self._status("processing", transport=route, command_id=path.stem)
            try:
                with MarkerLeaseKeeper(native_root):
                    if route == "python_sdk":
                        self._external(path, active)
                        response = json.loads(
                            (self.directories["responses"] / path.name).read_text()
                        )
                        trace = json.loads(
                            (
                                self.directories["state"] / (path.stem + ".sdk.json")
                            ).read_text()
                        )
                        if (
                            write
                            and response["status"] != "success"
                            and trace["mutations_sent"]
                        ):
                            raise RuntimeError("EXTERNAL_SDK_WRITE_OUTCOME_UNCERTAIN")
                    else:
                        client = SDKCommandClient(
                            native_root,
                            active["session"],
                            runtime_root=self.root,
                        )
                        with suppress(BridgeCommandError):
                            client._await_response(
                                command_id=path.stem,
                                response_path=self.directories["responses"] / path.name,
                                timeout_seconds=max(
                                    1, self.profile.native_timeout_seconds
                                ),
                            )
                if route_record is not None and route_path is not None:
                    route_record.update(
                        phase="completed",
                        response=json.loads(
                            (self.directories["responses"] / path.name).read_text(
                                encoding="utf-8"
                            )
                        ),
                    )
                    atomic_json(route_path, route_record)
                if command["action"] == "close_project":
                    assert route_record is not None
                    finish_close(self.profile, command["command_id"],
                                 closed=route_record["response"]["status"] == "success")
                if guard is not None:
                    guard.clear()
                if command["action"] == "stop_bridge":
                    LuaSnapshotClient(native_root).request("stop", 10)
                self._status("ready", transport=route, generation=active["generation"])
            except Exception:
                # The slot and ledger retain ownership after any uncertain SDK
                # exception. Neither a new key nor another transport is tried.
                self._status("recovery_required", transport=route, command_id=path.stem)
                if (
                    route_record is not None
                    and route_path is not None
                    and route_record["phase"] != "completed"
                ):
                    route_record["phase"] = "uncertain"
                    atomic_json(route_path, route_record)
                raise

    def run(self, stop: threading.Event) -> None:
        last_refresh = 0.0
        try:
            with _lock(self.profile, "sdk-worker.lock"):
                while not stop.is_set():
                    paths = sorted(
                        self.directories["commands"].glob("*.json"),
                        key=lambda path: (path.stat().st_mtime_ns, path.name),
                    )
                    if not paths:
                        try:
                            active = read_active(self.profile)
                        except (OSError, RuntimeError, ValueError) as error:
                            # Windows may retain the replaced snapshot briefly.
                            # This idle read has not dispatched native work.
                            self._status("waiting_state", reason=str(error)[:256])
                            stop.wait(0.2)
                            continue
                        if (
                            active
                            and not close_intent(self.profile)
                            and active["phase"] == "connected"
                            and process_alive(active["process"])
                            and not any(
                                Path(active["runtime"]).glob("marker-exit-*.drp")
                            )
                            and (
                                Path(active["runtime"]) / "sdk-template.preset"
                            ).is_file()
                            and time.monotonic() - last_refresh > 20
                            and self._refresh_allowed()
                        ):
                            # Publish native state before high-level workflows
                            # consult capabilities; no UI or Resolve launch here.
                            # Queue one read; process it in this same worker on
                            # the next iteration, without waiting on ourselves.
                            import datetime
                            from uuid import uuid4

                            identity = str(uuid4())
                            now = datetime.datetime.now(datetime.timezone.utc)
                            atomic_json(
                                self.directories["commands"] / f"{identity}.json",
                                {
                                    "protocol_version": "1.0",
                                    "command_id": identity,
                                    "idempotency_key": identity,
                                    "created_at": now.isoformat(),
                                    "expires_at": (
                                        now + datetime.timedelta(seconds=60)
                                    ).isoformat(),
                                    "provider": "resolve",
                                    "action": "get_capabilities",
                                    "arguments": {},
                                    "safety": {
                                        "create_backup": False,
                                        "allow_destructive": False,
                                    },
                                },
                            )
                            last_refresh = time.monotonic()
                        stop.wait(0.2)
                        continue
                    path = paths[0]
                    try:
                        self.process(path)
                    except Exception as error:
                        self._status("recovery_required", reason=str(error)[:256])
                        if not path.exists():
                            path = self.directories["processing"] / path.name
                        if path.exists():
                            self._error(
                                path,
                                getattr(error, "code", "AUTOMATIC_PROVIDER_FAILED"),
                                str(error),
                            )
                    # User/readiness work just refreshed the native state too.
                    # Do not insert a slow discovery read ahead of the caller's
                    # next command or starve project-open context verification.
                    last_refresh = time.monotonic()
                    stop.wait(0.05)
        except OperationBusy:
            return
