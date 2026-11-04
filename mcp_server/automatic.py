"""Stable full tool catalogue with automatic native provider selection."""

from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Any

from jsonschema import ValidationError, validate
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, Tool

from agent.application import AgentApplication
from agent.bridge_state import load_bridge_state
from agent.capabilities import WORKFLOWS, workflow_capabilities
from agent.client import AgentClientError, FilesystemCommandClient
from agent.contracts import validate_contract
from agent.media import MediaPolicy, MediaPolicyError
from agent.paths import runtime_directory
from agent.workflow_audit import WorkflowAuditLog
from mcp_server.managed_marker import ManagedMarkerServer, _complete_work, _error
from mcp_server.project_support import OPTIONAL_PROJECT_SUPPORT
from mcp_server.server import create_server as create_primary
from mcp_server.server import render_tool_error
from providers.resolve.automatic_connection import (
    AutomaticConnection,
    AutomaticConnectionError,
    wait_operation,
)
from providers.resolve.client import ResolveProviderClient
from providers.resolve.lua_editing import atomic_json
from providers.resolve.marker_supervisor import Profile
from providers.resolve.project_open_service import ProjectOpenError


class _RuntimeMediaPolicy:
    """Load approved local roots lazily and publish to the selected queue."""

    def __init__(self, runtime: Path) -> None:
        self.runtime = runtime

    def prepare_import(self, paths: list[str]) -> list[str]:
        policy = MediaPolicy.from_local_config(runtime_root=self.runtime)
        return policy.prepare_import(paths)

    def validate_files(self, paths: list[str]) -> list[str]:
        policy = MediaPolicy.from_local_config(runtime_root=self.runtime)
        return policy.validate_files(paths)

    def validate_export_directory(self, directory: str) -> str:
        """Validate an export location against current local configuration."""
        policy = MediaPolicy.from_local_config(runtime_root=self.runtime)
        return policy.validate_export_directory(directory)

    def prepare_export_directory(self, directory: str) -> str:
        """Publish the same allowed roots to the selected SDK queue."""
        policy = MediaPolicy.from_local_config(runtime_root=self.runtime)
        return policy.prepare_export_directory(directory)


def default_profile(path: Path | None = None) -> Profile:
    root = runtime_directory()
    target = path or root.parent / "config/managed-free/profile.json"
    if not target.exists():
        root.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            roots = MediaPolicy.from_local_config().allowed_roots
        except (MediaPolicyError, ValueError, OSError):
            roots = ()
        with wait_operation(Profile(target, root, ()), 10):
            if not target.exists():
                atomic_json(
                    target,
                    {
                        "schema": 1,
                        "enabled": True,
                        "runtime_base": str(root),
                        "media_roots": [
                            str(value) for value in roots if Path(value).is_dir()
                        ],
                    },
                )
    return Profile.load(target)


class AutomaticServer(MCPServer[Any]):
    def __init__(
        self,
        profile: Profile,
        *,
        enable_windows_recording_ui: bool = False,
        start_worker: bool = True,
    ) -> None:
        super().__init__(
            name="davinci-resolve-agent",
            instructions=(
                "DaVinci Resolve connects automatically. The full tool catalogue "
                "is stable across Python SDK and Lua transports. Call "
                "video_agent_capabilities to inspect actual workflow availability. "
                "No manual script launch or protocol choice is needed. Native "
                "writes retain confirmations, backups, stable keys and readbacks. "
                "A failed write is never replayed through another transport."
            )
            + OPTIONAL_PROJECT_SUPPORT,
        )
        self.profile = profile
        self.connection = AutomaticConnection(profile)
        self.primary = create_primary(
            AgentApplication(
                resolve=ResolveProviderClient(
                    FilesystemCommandClient(profile.runtime_base)
                ),
                state_loader=lambda: load_bridge_state(profile.runtime_base),
                workflow_audit=WorkflowAuditLog(profile.runtime_base / "logs"),
                media_policy=_RuntimeMediaPolicy(profile.runtime_base),
            )
        )
        self.marker = ManagedMarkerServer(
            profile,
            enable_windows_recording_ui=enable_windows_recording_ui,
        )
        self.native_tools = {
            name
            for workflow in WORKFLOWS
            if not workflow.local
            for name in (*workflow.python_tools, *workflow.marker_tools)
        }
        self.native_tools.update(
            {
                "preview_broll_plan",
                "preview_animation_template",
                "preview_color_treatment",
                "preview_synchronized_pause_compaction",
                "preview_take_sequence_timeline_mapping",
                "preview_take_sequence_media_import",
                "apply_take_sequence_media_import",
                "preview_take_sequence_timeline_apply",
                "preview_take_sequence_render",
            }
        )
        self.native_tools.update(
            {
                "resolve_free_connection_status",
                "resolve_free_get_track_record_arm",
                "resolve_free_set_track_record_arm",
                "resolve_free_get_audio_input_patch",
                "resolve_free_patch_audio_input",
            }
        )
        if start_worker and profile.enabled and sys.platform == "win32":
            from agent.resolve_watch import start_hidden

            start_hidden(profile)

    async def list_tools(self) -> list[Tool]:
        tools = {tool.name: tool for tool in await self.marker.list_tools()}
        tools.update({tool.name: tool for tool in await self.primary.list_tools()})
        return list(tools.values())

    async def _connection_status(self, arguments: dict[str, Any], context: Any) -> Any:
        """Attach to the current project without opening a historical target.

        The backend must still perform its correlated native status request;
        successful host bootstrap alone is not connection proof.
        """
        timeout = arguments.get("timeout_seconds", 30)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 600
        ):
            return _error("INVALID_ARGUMENTS", "timeout_seconds must be in (0, 600].")
        backend_called = False
        try:
            await _complete_work(
                asyncio.to_thread(
                    self.connection.ensure,
                    timeout=timeout,
                    require_sdk_channel=False,
                    allow_project_open=False,
                )
            )
            backend_called = True
            return await _complete_work(
                self.marker.call_tool(
                    "resolve_free_connection_status", arguments, context
                )
            )
        except (OSError, RuntimeError, ValueError) as error:
            code = str(getattr(error, "code", "NATIVE_CONNECTION_FAILED"))
            cause = (
                error.cause_code if isinstance(error, ProjectOpenError) else None
            ) or getattr(error.__cause__, "code", None)
            evidence = {
                "code": code,
                "detail": str(error),
                "connected": False,
                "native_roundtrip_verified": False,
                # A backend exception may precede its native request; do not
                # infer dispatch merely from delegation.
                "native_request_attempted": None if backend_called else False,
                "liveness_source": "local_connection_diagnostics",
                "recording_readiness": "not_assessed",
                "uncertain": getattr(error, "uncertain", None),
                "project_open_requested": False,
            }
            if isinstance(cause, str):
                evidence["cause_code"] = cause
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(evidence))],
                structured_content=evidence,
                is_error=True,
            )

    async def _create_project(self, arguments: dict[str, Any], context: Any) -> Any:
        """Route cold creation before historical-project readiness."""
        from providers.resolve.project_create_service import (
            ProjectCreateService,
            validate_creation,
        )

        service = ProjectCreateService(self.profile)
        key = arguments.get("idempotency_key")
        creation_attempted = False
        try:
            # Validate the public native path too; missing cold keys are rejected
            # before a New Project invocation, but remain optional for warm SDK.
            validate_creation(
                arguments.get("name"),
                arguments.get("confirm_create"),
                key if key is not None else "warm-path-validation",
                arguments.get("timeout_seconds", 30),
            )
            owned = await asyncio.to_thread(service.owns_key, key)
            host = None if owned else await asyncio.to_thread(service.host.inspect)
            if not owned and host and (
                host.get("state") == "project_window" and host.get("eligible") is True
            ):
                try:
                    await _complete_work(self._ready(allow_project_open=False))
                except AutomaticConnectionError as error:
                    if error.code not in {"NO_ACTIVE_PROJECT", "PROJECT_CLOSED"}:
                        raise
                    # The window may have closed between the two read-only
                    # inspections. No create command has been dispatched yet.
                else:
                    creation_attempted = True
                    return await _complete_work(
                        self.primary.call_tool(
                            "resolve_create_project", arguments, context
                        )
                    )
            # A closed application, a loading window or Project Manager needs
            # the creation service, never existing-project readiness/history.
            if not key:
                return _error(
                    "IDEMPOTENCY_KEY_REQUIRED",
                    "Cold creation requires a stable key.",
                )
            creation_attempted = True
            result = await _complete_work(
                asyncio.to_thread(service.create, **arguments)
            )
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(result))],
                structured_content=result,
            )
        except (OSError, ValueError, RuntimeError) as error:
            evidence = {
                "code": str(getattr(error, "code", "PROJECT_CREATION_FAILED")),
                "detail": str(error),
                "uncertain": getattr(error, "uncertain", creation_attempted),
                "project_open_requested": False,
            }
            cause_code = getattr(error, "cause_code", None)
            if isinstance(cause_code, str):
                evidence["cause_code"] = cause_code
            bridge_uncertain = getattr(error, "bridge_dispatch_uncertain", None)
            if type(bridge_uncertain) is bool:
                evidence["bridge_dispatch_uncertain"] = bridge_uncertain
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(evidence))],
                structured_content=evidence,
                is_error=True,
            )

    async def _ready(self, *, allow_project_open: bool = True) -> None:
        # Bootstrap precedes command creation, so the native timeout/expiry
        # covers SDK work rather than application launch and project opening.
        client = FilesystemCommandClient(self.profile.runtime_base)
        for attempt in range(2):
            await asyncio.to_thread(
                self.connection.ensure,
                require_sdk_channel=False,
                allow_project_open=allow_project_open,
            )
            try:
                await asyncio.to_thread(
                    client.request,
                    provider="resolve",
                    action="get_capabilities",
                    timeout_seconds=60,
                )
                return
            except (OSError, RuntimeError):
                if attempt:
                    raise
                # Only the readiness read repeats. No editing command exists yet.
                await asyncio.sleep(0.2)

    async def _capabilities(self, arguments: dict[str, Any]) -> CallToolResult:
        verify = arguments.get("verify_connection", False)
        marker_arguments = dict(arguments)
        connection_error = None
        if verify:
            try:
                await self._ready(allow_project_open=False)
            except (OSError, RuntimeError, ValueError) as error:
                connection_error = getattr(error, "code", "automatic_connection_failed")
                arguments = {**arguments, "verify_connection": False}
                if connection_error in {
                    "PROJECT_CLOSED",
                    "PROJECT_CLOSE_REQUIRES_RECOVERY",
                    "PROJECT_CREATION_REQUIRES_RECOVERY",
                }:
                    marker_arguments = {**marker_arguments, "verify_connection": False}
        tools = await self.list_tools()
        primary = await self.primary.call_tool("video_agent_capabilities", arguments)
        marker = await self.marker.call_tool(
            "video_agent_capabilities", marker_arguments
        )
        reports: list[dict[str, Any]] = []
        for result in (primary, marker):
            if isinstance(result, CallToolResult) and result.structured_content:
                candidate = result.structured_content
                if "workflows" in candidate:
                    reports.append(candidate)
        if not reports:
            reports = [
                workflow_capabilities(
                    backend="python",
                    tools=[tool.name for tool in tools],
                    connected=False,
                    evidence="automatic_connection_waiting",
                )
            ]
        report = dict(reports[0])
        report["backend"] = "auto"
        report["registered_tools"] = sorted(tool.name for tool in tools)
        priority = {
            "available": 5,
            "blocked": 4,
            "unverified": 3,
            "partial": 2,
            "unsupported": 1,
        }
        rows: dict[str, dict[str, Any]] = {}
        for candidate in reports:
            for row in candidate["workflows"]:
                previous = rows.get(row["workflow"])
                if (
                    previous is None
                    or priority[row["status"]] > priority[previous["status"]]
                ):
                    rows[row["workflow"]] = row
            if candidate.get("connected") is True:
                report.update(connected=True, evidence=candidate["evidence"])
            if candidate.get("project") and candidate["project"].get("id"):
                report["project"] = candidate["project"]
        report["workflows"] = list(rows.values())
        if connection_error and report.get("connected") is not True:
            report.update(connected=False, evidence=str(connection_error)[:64])
            for row in report["workflows"]:
                if row["scope"] == "resolve" and row["supported"]:
                    if row["workflow"] == "project_creation" and connection_error in {
                        "NO_ACTIVE_PROJECT", "PROJECT_CLOSED",
                    }:
                        row.update(
                            available=False, status="unverified",
                            reason="independent_creation_preflight_required",
                        )
                        continue
                    row.update(
                        available=False, status="blocked", reason=connection_error
                    )
        validate_contract("workflow-capabilities", report)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(report))],
            structured_content=report,
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
    ) -> Any:
        definitions = {tool.name: tool for tool in await self.list_tools()}
        if name not in definitions:
            return _error("UNKNOWN_TOOL", f"Unknown tool: {name}")
        try:
            validate(arguments, definitions[name].input_schema)
        except ValidationError as error:
            return _error("INVALID_ARGUMENTS", error.message)
        if arguments.get("output_directory") is not None and name in {
            "resolve_prepare_render_job",
            "resolve_verify_render_output",
            "resolve_lua_prepare_render",
        }:
            try:
                policy = (
                    MediaPolicy(self.profile.media_roots)
                    if name == "resolve_lua_prepare_render"
                    else MediaPolicy.from_local_config(
                        runtime_root=self.profile.runtime_base
                    )
                )
                policy.validate_export_directory(arguments["output_directory"])
            except (ValueError, OSError) as error:
                return _error("INVALID_ARGUMENTS", str(error))
        if name == "video_agent_capabilities":
            return await _complete_work(self._capabilities(arguments))
        if name == "resolve_free_connection_status":
            return await self._connection_status(arguments, context)
        if name == "resolve_create_project":
            return await self._create_project(arguments, context)
        primary = {tool.name for tool in await self.primary.list_tools()}
        native = (
            name in self.native_tools
            or name.startswith("resolve_lua_")
            or name.startswith("resolve_")
            and not name.startswith("resolve_free_")
        ) and name != "resolve_verify_render_output"
        if native and name != "resolve_close_project":
            if name in primary:
                try:
                    await _complete_work(self._ready())
                except (
                    AgentClientError,
                    AutomaticConnectionError,
                    ValueError,
                ) as error:
                    if name not in {
                        "resolve_get_render_options",
                        "resolve_prepare_render_job",
                    }:
                        raise
                    result = render_tool_error(error)
                    assert result.structured_content is not None
                    result.structured_content.update(
                        phase="readiness",
                        outcome="not_dispatched",
                        detail="Render request was not dispatched. "
                        f"Native readiness failed: {error}",
                    )
                    result.content = [
                        TextContent(
                            type="text",
                            text=f"{result.structured_content['code']}: "
                            f"{result.structured_content['detail']}",
                        )
                    ]
                    return result
            else:
                await _complete_work(
                    asyncio.to_thread(
                        self.connection.ensure,
                        require_sdk_channel=False,
                        allow_project_open=name != "resolve_lua_create_project",
                    )
                )
        target = self.primary if name in primary else self.marker
        return await _complete_work(target.call_tool(name, arguments, context))


def run(
    profile_path: Path | None, *, enable_windows_recording_ui: bool = False
) -> None:
    AutomaticServer(
        default_profile(profile_path),
        enable_windows_recording_ui=enable_windows_recording_ui,
    ).run()
