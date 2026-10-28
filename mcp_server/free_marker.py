"""Typed stdio MCP backend for Resolve Free's native marker data channel.

This module never drives a user interface or evaluates caller-supplied code.
The menu script must be started once per native Resolve session.
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

from mcp_server.lua_experimental import create_server as create_lua_server
from providers.resolve.lua_transport import LuaSnapshotClient

MENU_FILE = "Resolve Agent Marker.lua"
MENU_HEADER = "-- DaVinci Resolve Agent: managed Free marker menu script\n"
UNSUPPORTED_RECORDING_CONTROLS = (
    "physical_input_device",
    "input_patching",
    "record_arm",
    "track_bus_eq",
    "track_bus_dynamics",
    "track_bus_effect_inserts",
)


def create_server(root: Path) -> MCPServer:
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
            "never resubmit a write with a new key. Physical microphone routing, "
            "record arm and track/bus processing controls are unsupported. "
            "Native project exports are local responses and can be expensive."
        ),
        excluded_tools=frozenset({"resolve_lua_reload_modules"}),
    )
    client = LuaSnapshotClient(root)

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
    ) -> dict[str, Any]:
        """Verify native liveness and current project through a new request.

        This is not the cached Python bridge heartbeat. A failed read does not
        resolve any pending mutation or establish recording readiness.
        """
        common = {
            "backend": "free_marker",
            "session": client.session,
            "liveness_source": "fresh_native_request",
            "max_payload_bytes": metadata.get("max_payload_bytes", 4096),
            "unsupported_recording_controls": list(UNSUPPORTED_RECORDING_CONTROLS),
        }
        try:
            result = await asyncio.to_thread(
                client.request, "get_current_project", timeout_seconds
            )
        except (OSError, RuntimeError, ValueError) as error:
            return {**common, "connected": False, "reason": str(error)[:500]}
        return {**common, "connected": True, "project": result["project"]}

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


def run(root: Path) -> None:
    """Serve stdio while maintaining the data-channel lease, without auto-play."""
    from providers.resolve.marker_transport import MarkerLeaseKeeper

    with MarkerLeaseKeeper(root):
        create_server(root).run()


def main() -> None:
    """Prepare/install a session or run its MCP stdio server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--install-script", action="store_true")
    parser.add_argument("--scripts-directory", type=Path)
    parser.add_argument("--media-root", type=Path, action="append", default=[])
    parser.add_argument("--lease-seconds", type=int, default=600)
    args = parser.parse_args()
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
        run(args.runtime)


if __name__ == "__main__":
    main()
