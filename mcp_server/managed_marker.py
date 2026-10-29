"""Stable MCP endpoint routing whole operations to immutable marker sessions.

The supervisor owns marker startup. One explicit host operation opens an exact
saved project through the fixed Windows adapter and verifies fresh native context.
Durable caller keys retain their original generation; uncertain opens do not repeat.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, TypeVar

from jsonschema import validate
from mcp.server import MCPServer
from mcp.types import (
    CallToolResult,
    InputRequiredResult,
    TextContent,
    Tool,
    ToolAnnotations,
)

from mcp_server.free_marker import create_server as create_marker_server
from providers.resolve.lua_editing import atomic_json, bounded_text
from providers.resolve.managed_status import managed_status
from providers.resolve.marker_supervisor import (
    OperationBusy,
    Profile,
    catalogue_lock,
    operation_lock,
    read_active,
    runtime_history,
)
from providers.resolve.marker_transport import MarkerLeaseKeeper, prepare_marker
from providers.resolve.project_open_service import (
    ProjectOpenError,
    ProjectOpenService,
    pending_project_open,
)

_Result = TypeVar("_Result")


async def _complete_work(work: Coroutine[Any, Any, _Result]) -> _Result:
    """Drain a worker before propagating cancellation of its MCP request."""
    task = asyncio.create_task(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


class OriginalGenerationRequired(RuntimeError):
    """A caller key already belongs to another immutable native session."""


def _read_owner(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
        raise ValueError("Invalid managed write ownership record.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {
        "schema", "runtime", "session", "generation"
    } or type(value["schema"]) is not int or value["schema"] != 1 or any(
        not isinstance(value[field], str) or not value[field]
        for field in ("runtime", "session", "generation")
    ):
        raise ValueError("Invalid managed write ownership record.")
    return value


def claim_write_key(
    profile: Profile, active: dict[str, Any], key: str
) -> None:
    """Reserve a key before dispatch; a rotation never resets its ownership.

    Must run inside operation_lock. Prior receipts are also inspected so an
    adopted static MCP client cannot evade ownership by writing outside us.
    """
    bounded_text(key, "Idempotency key")
    digest = hashlib.sha256(key.encode()).hexdigest()
    host_receipt = profile.state_directory / "project-open-receipts" / f"{digest}.json"
    if host_receipt.exists() or host_receipt.is_symlink():
        raise OriginalGenerationRequired(
            "Idempotency key already belongs to a host project-open operation; "
            "no native mutation was dispatched."
        )
    folder = profile.state_directory / "write-owners"
    if folder.is_symlink():
        raise ValueError("Managed ownership directory cannot be a symbolic link.")
    folder.mkdir(exist_ok=True)
    path = folder / f"{digest}.json"
    current = Path(active["runtime"]).resolve()
    if path.exists() or path.is_symlink():
        previous = _read_owner(path)
        if (
            previous["runtime"] != str(current)
            or previous["session"] != active["session"]
            or previous["generation"] != active["generation"]
        ):
            raise OriginalGenerationRequired(
                "Idempotency key belongs to its original runtime "
                f"{previous['runtime']}. Inspect/reconcile that original receipt; "
                "no command was dispatched into the current generation."
            )
        return
    for previous_root in runtime_history(profile):
        if previous_root.resolve() == current:
            continue
        receipt = previous_root / "receipts" / f"{digest}.json"
        if receipt.exists() or receipt.is_symlink():
            raise OriginalGenerationRequired(
                f"Idempotency key has a prior receipt in {previous_root}. "
                "Recover the original operation; no new mutation was dispatched."
            )
    atomic_json(path, {
        "schema": 1,
        "runtime": str(current),
        "session": active["session"],
        "generation": active["generation"],
    })


def _error(code: str, detail: str, *, cause_code: str | None = None) -> CallToolResult:
    evidence = {"code": code, "detail": detail}
    if cause_code is not None:
        evidence["cause_code"] = cause_code
    return CallToolResult(
        content=[TextContent(type="text", text=f"{code}: {detail}")],
        structured_content=evidence,
        is_error=True,
    )


class ManagedMarkerServer(MCPServer[Any]):
    """Delegate via public MCP APIs, pinning one backend for each complete call."""

    def __init__(
        self,
        profile: Profile,
        *,
        enable_windows_recording_ui: bool = False,
        backend_factory: Callable[..., MCPServer[Any]] = create_marker_server,
        project_opener: ProjectOpenService | None = None,
    ) -> None:
        super().__init__(
            name="davinci-resolve-agent-free-marker",
            instructions=(
                "Managed Resolve Free marker connection. A separate opt-in Windows "
                "watcher starts the fixed menu bridge after a saved project opens. "
                "Use resolve_free_autostart_status to inspect waiting/error states. "
                "Every tool stays pinned to one native session for its full operation. "
                "Verify fresh native project identity before writes. Preserve stable "
                "idempotency keys: keys from older generations require original "
                "receipt recovery and are never dispatched again. No tool records."
            ),
        )
        self.profile = profile
        self._factory = backend_factory
        self._ui_enabled = enable_windows_recording_ui
        self._backends: dict[str, MCPServer[Any]] = {}
        self._project_opener = project_opener or ProjectOpenService(profile)
        # Catalogue construction must work while Resolve is closed. Its private
        # session is never installed or used for native command dispatch.
        catalogue = profile.state_directory / "catalogue-runtime"
        deadline = time.monotonic() + 10
        while True:
            try:
                with catalogue_lock(profile):
                    if catalogue.is_symlink():
                        raise ValueError("Catalogue runtime cannot be a symbolic link.")
                    if not catalogue.exists():
                        prepare_marker(catalogue, list(profile.media_roots))
                    self._catalogue = self._backend(catalogue)
                break
            except OperationBusy:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _backend(self, root: Path) -> MCPServer[Any]:
        key = str(root.resolve())
        if key not in self._backends:
            self._backends[key] = self._factory(
                root, enable_windows_recording_ui=self._ui_enabled
            )
        return self._backends[key]

    async def list_tools(self) -> list[Tool]:
        tools = await self._catalogue.list_tools()
        return [*tools, Tool(
            name="resolve_free_autostart_status",
            description=(
                "Read the managed watcher state and historical native proof. "
                "This does not invoke UI or establish fresh native liveness."
            ),
            input_schema={"type": "object", "properties": {},
                          "additionalProperties": False},
            annotations=ToolAnnotations(
                read_only_hint=True, destructive_hint=False,
                idempotent_hint=True, open_world_hint=False,
            ),
        ), Tool(
            name="resolve_free_open_project",
            description=(
                "Open one exact existing saved project from Project Manager via "
                "the fixed local Windows adapter, bootstrap its marker and verify "
                "fresh native library/root-folder/project identity. No Computer "
                "Use, project creation, import, database reset or recording. "
                "A prior uncertain key is reconciled without reopening."
            ),
            input_schema={
                "type": "object", "additionalProperties": False,
                "properties": {
                    "project_name": {"type": "string", "minLength": 1,
                                     "maxLength": 128},
                    "expected_project_id": {"type": "string", "minLength": 1,
                                            "maxLength": 128},
                    "expected_database": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "DbType": {"type": "string", "enum": ["Disk"]},
                            "DbName": {"type": "string", "minLength": 1,
                                       "maxLength": 128},
                        }, "required": ["DbType", "DbName"],
                    },
                    "folder_path": {"type": "array", "items": {"type": "string"},
                                    "maxItems": 0, "default": []},
                    "idempotency_key": {"type": "string", "minLength": 1,
                                        "maxLength": 128},
                    "confirm": {"type": "boolean", "default": False},
                    "timeout_seconds": {"type": "number", "minimum": 1,
                                        "maximum": 180, "default": 120},
                },
                "required": ["project_name", "expected_project_id",
                             "expected_database", "idempotency_key"],
            },
            annotations=ToolAnnotations(
                read_only_hint=False, destructive_hint=False,
                idempotent_hint=True, open_world_hint=False,
            ),
        )]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
    ) -> CallToolResult | InputRequiredResult:
        definitions = {tool.name: tool for tool in await self.list_tools()}
        if name not in definitions:
            return _error("UNKNOWN_TOOL", name)
        validate(arguments, definitions[name].input_schema)
        if name == "resolve_free_autostart_status":
            status = await asyncio.to_thread(managed_status, self.profile)
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(status))],
                structured_content=status,
            )
        if name == "resolve_free_open_project":
            try:
                result = await _complete_work(asyncio.to_thread(
                    self._project_opener.open_existing, **arguments
                ))
                return CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(result))],
                    structured_content=result,
                )
            except ProjectOpenError as error:
                cause = getattr(error.__cause__, "code", None)
                return _error(
                    error.code, str(error),
                    cause_code=cause if isinstance(cause, str) else None,
                )
            except OperationBusy:
                return _error(
                    "OPERATION_BUSY",
                    "Another chat or watcher owns this project operation; retry later.",
                )
            except ValueError as error:
                return _error("INVALID_INPUT", str(error))
        # The nonblocking file gate serializes other chats and the supervisor.
        try:
            with operation_lock(self.profile):
                active = read_active(self.profile)
                if not active:
                    return _error("BOOTSTRAP_WAITING", "Watcher has no active session.")
                root = Path(active["runtime"])
                local_diagnostics = name in {
                    "resolve_free_startup_status", "resolve_free_pending_requests"
                }
                if active["phase"] != "connected" and not local_diagnostics:
                    return _error(
                        "BOOTSTRAP_WAITING",
                        f"Watcher state is {active['phase']}; no native command sent.",
                    )
                key = arguments.get("idempotency_key")
                annotations = definitions[name].annotations
                is_write = annotations is None or annotations.read_only_hint is not True
                if is_write:
                    host_open = pending_project_open(self.profile)
                    if host_open["pending"]:
                        return _error(
                            "PROJECT_OPEN_VERIFICATION_PENDING",
                            "An existing host project-open operation still needs "
                            "native context verification; no mutation dispatched.",
                        )
                if key is not None and is_write:
                    if not isinstance(key, str):
                        raise ValueError("Idempotency key must be text.")
                    claim_write_key(self.profile, active, key)
                backend = self._backend(root)
                with MarkerLeaseKeeper(root):
                    return await _complete_work(
                        backend.call_tool(name, arguments, context)
                    )
        except OriginalGenerationRequired as error:
            return _error("ORIGINAL_GENERATION_REQUIRED", str(error))
        except ProjectOpenError as error:
            return _error(error.code, str(error))
        except OperationBusy:
            return _error(
                "OPERATION_BUSY",
                "Another chat or watcher owns the operation; retry later.",
            )


def run(profile_path: Path, *, enable_windows_recording_ui: bool = False) -> None:
    """Serve one stable profile; native startup belongs to its hidden watcher."""
    profile = Profile.load(profile_path)
    ManagedMarkerServer(
        profile, enable_windows_recording_ui=enable_windows_recording_ui
    ).run()
