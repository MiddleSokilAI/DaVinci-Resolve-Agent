"""Stable full tool catalogue with automatic native provider selection."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, Tool

from agent.application import AgentApplication
from agent.bridge_state import load_bridge_state
from agent.capabilities import WORKFLOWS, workflow_capabilities
from agent.client import FilesystemCommandClient
from agent.contracts import validate_contract
from agent.media import MediaPolicy, MediaPolicyError
from agent.paths import runtime_directory
from agent.workflow_audit import WorkflowAuditLog
from mcp_server.managed_marker import ManagedMarkerServer, _complete_work
from mcp_server.server import create_server as create_primary
from providers.resolve.automatic_connection import AutomaticConnection, wait_operation
from providers.resolve.client import ResolveProviderClient
from providers.resolve.lua_editing import atomic_json
from providers.resolve.marker_supervisor import Profile


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
            ),
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

    async def _ready(self) -> None:
        # Bootstrap precedes command creation, so the native timeout/expiry
        # covers SDK work rather than application launch and project opening.
        client = FilesystemCommandClient(self.profile.runtime_base)
        for attempt in range(2):
            await asyncio.to_thread(self.connection.ensure, require_sdk_channel=False)
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
                await self._ready()
            except (OSError, RuntimeError, ValueError) as error:
                connection_error = getattr(error, "code", "automatic_connection_failed")
                arguments = {**arguments, "verify_connection": False}
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
        if name == "video_agent_capabilities":
            return await _complete_work(self._capabilities(arguments))
        primary = {tool.name for tool in await self.primary.list_tools()}
        native = (
            name in self.native_tools
            or name.startswith("resolve_lua_")
            or name.startswith("resolve_")
            and not name.startswith("resolve_free_")
        ) and name != "resolve_verify_render_output"
        if native:
            if name in primary:
                await _complete_work(self._ready())
            else:
                await _complete_work(
                    asyncio.to_thread(
                        self.connection.ensure,
                        require_sdk_channel=False,
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
