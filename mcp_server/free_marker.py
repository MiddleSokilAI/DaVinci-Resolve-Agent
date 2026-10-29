"""Typed stdio MCP backend for Resolve Free's native marker data channel.

The marker channel never evaluates caller-supplied code. Optional Windows UI
tools use a separate, fixed semantic adapter only with explicit host opt-in.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from mcp_server.fairlight import register_fairlight_tools
from mcp_server.lua_experimental import create_server as create_lua_server
from mcp_server.windows_recording import register_windows_recording_tools
from providers.resolve.fairlight_ui_service import FairlightUIService
from providers.resolve.lua_transport import LuaCommandTimeoutError, LuaSnapshotClient
from providers.resolve.recording_ui_service import RecordingUIService
from providers.resolve.startup_diagnostics import (
    MENU_FILE as MENU_FILE,
)
from providers.resolve.startup_diagnostics import (
    MENU_HEADER as MENU_HEADER,
)
from providers.resolve.startup_diagnostics import (
    inspect_startup,
)

UNSUPPORTED_RECORDING_CONTROLS = (
    "physical_input_device",
    "input_patching",
    "record_arm",
    "track_bus_eq",
    "track_bus_dynamics",
    "track_bus_effect_inserts",
)


def create_server(
    root: Path,
    *,
    enable_windows_recording_ui: bool = False,
) -> MCPServer:
    """Create a marker-backed MCP surface, retaining the typed Lua guards."""
    root = root.resolve()
    metadata = json.loads((root / "session.json").read_text(encoding="utf-8"))
    if metadata.get("transport") != "marker":
        raise ValueError("Prepare a marker session before starting this backend.")
    server = create_lua_server(
        root,
        name="davinci-resolve-agent-free-marker",
        instructions=(
            "Resolve Free marker backend. Start its managed script from "
            "Workspace > Scripts once per Resolve session. Call "
            "resolve_free_connection_status for a fresh native round trip. "
            "Only typed bounded commands are supported; no arbitrary code. "
            "Writes require exact project identity, confirmation, and stable "
            "idempotency keys. After a timeout reconcile the original receipt; "
            "never resubmit a write with a new key. Named mono tracks, sample-rate "
            "inspection, seek and project media roots use guarded native tools. "
            "Physical input selection and scoped Fairlight EQ/Dynamics, mono input "
            "patching and track arm use opt-in Windows UI adapters. New Fairlight "
            "controls await live Windows acceptance. "
            "Effect inserts remain unsupported. "
            "No tool starts recording. "
            "Cold start from Project Manager is unsupported. Use "
            "resolve_free_startup_status for session/menu evidence and a manual "
            "handoff. Open the existing saved target project and launch the managed "
            "Scripts-menu bridge once, then verify a fresh native round trip. "
            "Do not create a substitute project or import media while disconnected. "
            "Native project exports are local responses and can be expensive."
        ),
        excluded_tools=frozenset({"resolve_lua_reload_modules"}),
    )
    client = LuaSnapshotClient(root)
    ui_service = RecordingUIService(root, client, enabled=enable_windows_recording_ui)
    register_windows_recording_tools(server, ui_service)
    fairlight_service = FairlightUIService(
        root,
        client,
        enabled=enable_windows_recording_ui,
    )
    register_fairlight_tools(server, fairlight_service)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    async def resolve_free_connection_status(
        timeout_seconds: float = 30,
        target_project_name: str | None = None,
    ) -> dict[str, Any]:
        """Verify native liveness and current project through a new request.

        This is not the cached Python bridge heartbeat. A failed read does not
        resolve any pending mutation or establish recording readiness. A valid
        terminal lifecycle export returns disconnected without publishing a new
        request into an exited immutable session. Local diagnostics never infer
        a selected project from window captions or host lease renewal.
        """
        common = {
            "backend": "free_marker",
            "session": client.session,
            "liveness_source": "fresh_native_request",
            "max_payload_bytes": metadata.get("max_payload_bytes", 4096),
            "unsupported_recording_controls": [
                control
                for control in UNSUPPORTED_RECORDING_CONTROLS
                if not (
                    control == "physical_input_device"
                    and ui_service.adapter.capabilities().get("available")
                )
                and not (
                    control in fairlight_service.adapter.capabilities()["controls"]
                    and fairlight_service.adapter.capabilities().get("available")
                )
            ],
            "recording_native_api": "recording_api_v1" in client.capabilities,
            "recording_windows_ui": ui_service.adapter.capabilities(),
            "fairlight_windows_ui": fairlight_service.adapter.capabilities(),
            "recording_readiness": "not_assessed",
        }
        diagnostics = await asyncio.to_thread(
            inspect_startup,
            root,
            target_project_name=target_project_name,
        )
        if (
            diagnostics["metadata_status"] != "valid"
            or diagnostics["session"] != client.session
        ):
            return {
                **common,
                "connected": False,
                "failure_code": "runtime_metadata_changed_or_invalid",
                "reason": ("Inspect the runtime and reconnect MCP before native work."),
                "native_request_attempted": False,
                "native_roundtrip_verified": False,
                "liveness_source": "local_runtime_diagnostics",
                "startup": diagnostics,
            }
        recovery = diagnostics["recovery"]
        recovery_clear = (
            recovery["status"] == "inspected"
            and recovery["pending_receipt_count"] == 0
            and recovery["inbox_occupied"] is False
        )
        if diagnostics["terminal"] and not recovery_clear:
            return {
                **common,
                "connected": False,
                "failure_code": "runtime_recovery_required",
                "reason": "Inspect the original operations before reconnecting.",
                "native_request_attempted": False,
                "native_roundtrip_verified": False,
                "liveness_source": "local_runtime_diagnostics",
                "startup": diagnostics,
            }
        if diagnostics["terminal"] and recovery_clear:
            return {
                **common,
                "connected": False,
                "failure_code": "native_session_exited",
                "reason": (
                    "Native lifecycle exports show this immutable session exited."
                ),
                "native_request_attempted": False,
                "native_roundtrip_verified": False,
                "liveness_source": "native_lifecycle_exports",
                "startup": diagnostics,
            }
        try:
            result = await asyncio.to_thread(
                client.request, "get_current_project", timeout_seconds
            )
        except (OSError, RuntimeError, ValueError) as error:
            code = (
                "no_native_response"
                if isinstance(error, LuaCommandTimeoutError)
                else "transport_io_error"
                if isinstance(error, OSError)
                else "session_request_rejected"
                if isinstance(error, ValueError)
                else "native_request_failed"
            )
            return {
                **common,
                "connected": False,
                "failure_code": code,
                "reason": str(error)[:500],
                "native_request_attempted": True,
                "native_roundtrip_verified": False,
                "startup": await asyncio.to_thread(
                    inspect_startup,
                    root,
                    target_project_name=target_project_name,
                ),
            }
        diagnostics.update(
            native_connection_verified=True,
            project_state="verified",
            current_project=result["project"],
            target_project_matches=(
                None
                if target_project_name is None
                else result["project"]["name"] == target_project_name
            ),
        )
        priority_handoff = diagnostics["state"] in {
            "recovery_required",
            "inspection_required",
            "runtime_invalid",
            "menu_alignment_required",
            "session_exited",
        }
        if not priority_handoff:
            diagnostics["state"] = (
                "target_project_mismatch"
                if diagnostics["target_project_matches"] is False
                else "connected"
            )
            diagnostics["handoff"] = []
        if diagnostics["target_project_matches"] is False:
            diagnostics["handoff"].append(
                {
                    "code": "open_existing_target_then_verify",
                    "instruction": (
                        f"Open the existing saved project {target_project_name!r}, "
                        "then verify a fresh native project name and ID. Do not create "
                        "a substitute project or import media into the wrong project."
                    ),
                }
            )
        return {
            **common,
            "connected": True,
            "project": result["project"],
            "native_request_attempted": True,
            "native_roundtrip_verified": True,
            "target_project_matches": diagnostics["target_project_matches"],
            "startup": diagnostics,
        }

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    async def resolve_free_startup_status(
        target_project_name: str | None = None,
        inspect_host_metadata: bool = False,
    ) -> dict[str, Any]:
        """Inspect local session/menu evidence and return a cold-start handoff.

        Never dispatches a native request, launches Resolve, opens/creates a
        project, bootstraps a bridge or imports media. Optional Windows process
        metadata is a host hint only: Project Manager captions cannot prove the
        active native project or its absence. Startup support remains manual:
        an existing saved project and its managed Scripts-menu bridge are needed.
        """
        host = None
        if inspect_host_metadata:
            from providers.resolve.windows_host import inspect_windows_host

            host = await asyncio.to_thread(inspect_windows_host)
        return await asyncio.to_thread(
            inspect_startup,
            root,
            target_project_name=target_project_name,
            host=host,
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    async def resolve_free_pending_requests() -> dict[str, Any]:
        """Inspect owned pending writes and lifecycle evidence without dispatch.

        A pending write must be reconciled with the original tool, exact
        arguments and idempotency key. This resumes acknowledgement collection
        for its original command; it never publishes a replacement mutation.
        A new key or a new bootstrap is not a recovery procedure.
        """
        from providers.resolve.marker_transport import inspect_pending

        return await asyncio.to_thread(inspect_pending, root)

    return server


def install_menu_script(root: Path, scripts_directory: Path | None = None) -> Path:
    """Install only this backend's managed menu file; preserve other scripts."""
    source = root.resolve() / "marker-bridge.lua"
    if scripts_directory is None:
        appdata = os.environ.get("APPDATA")
        if os.name != "nt" or not appdata:
            raise ValueError("Pass --scripts-directory on non-Windows hosts.")
        scripts_directory = (
            Path(appdata)
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Support"
            / "Fusion"
            / "Scripts"
            / "Utility"
        )
    if not scripts_directory.is_dir():
        raise ValueError("Resolve's existing user Scripts directory is required.")
    target = scripts_directory / MENU_FILE
    if target.is_symlink():
        raise ValueError("Refusing to replace a symbolic menu script.")
    if target.exists() and not target.read_text(encoding="utf-8").startswith(
        MENU_HEADER
    ):
        raise ValueError("Refusing to overwrite an unmanaged menu script.")
    text = MENU_HEADER + source.read_text(encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    return target


def run(root: Path, *, enable_windows_recording_ui: bool = False) -> None:
    """Serve stdio while maintaining the data-channel lease, without auto-play."""
    from providers.resolve.marker_transport import MarkerLeaseKeeper

    with MarkerLeaseKeeper(root):
        server = create_server(
            root,
            enable_windows_recording_ui=enable_windows_recording_ui,
        )
        server.run()


def main() -> None:
    """Prepare/install a session or run its MCP stdio server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--install-script", action="store_true")
    parser.add_argument("--scripts-directory", type=Path)
    parser.add_argument("--media-root", type=Path, action="append", default=[])
    parser.add_argument("--lease-seconds", type=int, default=600)
    parser.add_argument("--enable-windows-recording-ui", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--target-project-name")
    parser.add_argument("--inspect-host-metadata", action="store_true")
    args = parser.parse_args()
    if args.diagnose:
        if args.prepare or args.install_script or args.media_root:
            parser.error(
                "--diagnose cannot prepare/install scripts or change media roots."
            )
        host = None
        if args.inspect_host_metadata:
            from providers.resolve.windows_host import inspect_windows_host

            host = inspect_windows_host()
        print(
            json.dumps(
                inspect_startup(
                    args.runtime,
                    target_project_name=args.target_project_name,
                    host=host,
                    scripts_directory=args.scripts_directory,
                ),
            )
        )
        return
    if args.target_project_name is not None or args.inspect_host_metadata:
        parser.error("Startup diagnostic options require --diagnose.")
    if args.prepare:
        from providers.resolve.marker_transport import prepare_marker

        script = prepare_marker(
            args.runtime, args.media_root, lease_seconds=args.lease_seconds
        )
        result = {"runtime": str(args.runtime.resolve()), "script": str(script)}
        if args.install_script:
            result["menu_script"] = str(
                install_menu_script(args.runtime, args.scripts_directory)
            )
        print(json.dumps(result))
    elif args.install_script:
        print(install_menu_script(args.runtime, args.scripts_directory))
    else:
        run(args.runtime, enable_windows_recording_ui=args.enable_windows_recording_ui)


if __name__ == "__main__":
    main()
