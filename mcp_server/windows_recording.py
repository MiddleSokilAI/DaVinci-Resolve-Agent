"""Explicit opt-in MCP tools for semantic Windows recording settings."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from providers.resolve.recording_ui_service import RecordingUIService


def register_windows_recording_tools(
    server: MCPServer, service: RecordingUIService,
) -> None:
    """Expose fixed recording operations, never generic clicks or scripts."""
    @server.tool(annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True,
        open_world_hint=False,
    ))
    async def resolve_free_windows_recording_status(
        expected_project_id: str,
    ) -> dict[str, Any]:
        """Read live Play/Stop/Record via Windows UI with native identity fences.

        Requires --enable-windows-recording-ui. Does not capture microphone
        audio or establish input patching, processing bypass or signal quality.
        """
        return await asyncio.to_thread(service.status, expected_project_id)

    @server.tool(annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True,
        open_world_hint=False,
    ))
    async def resolve_free_select_audio_input(
        device_name: str,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Choose a physical input in Resolve Preferences and reopen to verify.

        Explicit Windows UI opt-in, exact project identity and stopped
        transport required. Changes global audio preferences and may clear
        track patches; it does not patch, arm or start recording. Existing
        Preferences dialogs are refused to preserve user pending settings.
        An interrupted GUI write is uncertain and is never auto-resubmitted.
        """
        return await asyncio.to_thread(
            service.select_input, device_name, expected_project_id,
            idempotency_key, confirm=confirm,
        )
