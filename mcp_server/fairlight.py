"""Typed, opt-in Fairlight controls absent from the native Resolve SDK."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from providers.resolve.fairlight_ui_service import FairlightUIService


def register_fairlight_tools(server: MCPServer, service: FairlightUIService) -> None:
    """Expose explicit audio controls without a global recording command."""
    # Inspectors navigate owned windows, so they are not silent read-only tools.
    annotations = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    @server.tool(annotations=annotations)
    async def resolve_free_get_audio_processing(
        timeline_name: str,
        target_name: str,
        module: Literal["eq", "dynamics"],
        expected_project_id: str,
        scope: Literal["track", "bus"] = "track",
        expected_track_index: int | None = None,
    ) -> dict[str, Any]:
        """Read the track/bus master EQ or Dynamics switch, reopening its window.

        Requires Windows UI opt-in, a stopped Fairlight timeline and an exact
        track name/index, or an exact bus name. Windows live acceptance pending.
        This does not verify effect inserts, other DSP or microphone signal.
        """
        return await asyncio.to_thread(
            service.inspect,
            module,
            timeline_name,
            target_name,
            expected_project_id,
            scope=scope,
            expected_track_index=expected_track_index,
        )

    @server.tool(annotations=annotations)
    async def resolve_free_set_audio_eq(
        timeline_name: str,
        target_name: str,
        enabled: bool,
        expected_project_id: str,
        idempotency_key: str,
        scope: Literal["track", "bus"] = "track",
        expected_track_index: int | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Set a track/bus master EQ switch and verify after reopening.

        Back up the project before the first change. Requires host opt-in,
        explicit boolean, exact project/target and confirm=true. Reuse the same
        key after interruption for read-only reconciliation, never a new key.
        No individual band values or effect inserts are changed. Live pending.
        """
        return await asyncio.to_thread(
            service.change,
            "eq",
            {"enabled": enabled},
            timeline_name,
            target_name,
            expected_project_id,
            idempotency_key,
            scope=scope,
            expected_track_index=expected_track_index,
            confirm=confirm,
        )

    @server.tool(annotations=annotations)
    async def resolve_free_set_audio_dynamics(
        timeline_name: str,
        target_name: str,
        enabled: bool,
        expected_project_id: str,
        idempotency_key: str,
        scope: Literal["track", "bus"] = "track",
        expected_track_index: int | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Set and reopen-check a track/bus master Dynamics switch.

        Uses the stopped Windows Fairlight profile with host opt-in, a saved
        project backup and durable receipt. Does not edit compressor, limiter
        or expander parameters. Reuse the original key after a timeout.
        Live Windows acceptance pending; recording is never started.
        """
        return await asyncio.to_thread(
            service.change,
            "dynamics",
            {"enabled": enabled},
            timeline_name,
            target_name,
            expected_project_id,
            idempotency_key,
            scope=scope,
            expected_track_index=expected_track_index,
            confirm=confirm,
        )

    @server.tool(annotations=annotations)
    async def resolve_free_get_track_record_arm(
        timeline_name: str,
        track_name: str,
        expected_track_index: int,
        expected_project_id: str,
    ) -> dict[str, Any]:
        """Read only the exact track's R state; never press global Record.

        Requires a stopped Fairlight timeline, native name/index match and
        Windows UI opt-in. Reacquires the track row. Live acceptance pending.
        """
        return await asyncio.to_thread(
            service.inspect,
            "record_arm",
            timeline_name,
            track_name,
            expected_project_id,
            expected_track_index=expected_track_index,
        )

    @server.tool(annotations=annotations)
    async def resolve_free_set_track_record_arm(
        timeline_name: str,
        track_name: str,
        expected_track_index: int,
        armed: bool,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Set an exact track's R switch with reopened row-state verification.

        Does not start recording or change other tracks. Requires opt-in,
        confirm=true and native project/track identity. An uncertain write is
        reconciled by reading the same target with the same key. Live pending.
        """
        return await asyncio.to_thread(
            service.change,
            "record_arm",
            {"armed": armed},
            timeline_name,
            track_name,
            expected_project_id,
            idempotency_key,
            expected_track_index=expected_track_index,
            confirm=confirm,
        )

    @server.tool(annotations=annotations)
    async def resolve_free_get_audio_input_patch(
        timeline_name: str,
        track_name: str,
        expected_track_index: int,
        expected_project_id: str,
    ) -> dict[str, Any]:
        """Read the exact mono track's physical input assignment from Patch I/O.

        Requires Windows UI opt-in and stopped transport. Full source name and
        channel must be readable; ambiguous/truncated assignments are refused.
        Live Windows acceptance pending. Does not capture microphone audio.
        """
        return await asyncio.to_thread(
            service.inspect,
            "input_patch",
            timeline_name,
            track_name,
            expected_project_id,
            expected_track_index=expected_track_index,
        )

    @server.tool(annotations=annotations)
    async def resolve_free_patch_audio_input(
        timeline_name: str,
        track_name: str,
        expected_track_index: int,
        source_name: str,
        source_channel: int,
        expected_project_id: str,
        idempotency_key: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Patch one physical source channel to one exact native mono track.

        Read available full source labels in Patch I/O first. Source channel
        must be 1..64. Reopens Patch I/O to verify the assignment; no blind
        unpatch or rollback. Requires host opt-in, backup, confirm=true and
        durable key. Stereo/bus patches unsupported. Does not arm or record.
        Live Windows acceptance pending.
        """
        return await asyncio.to_thread(
            service.change,
            "input_patch",
            {
                "source_name": source_name,
                "source_channel": source_channel,
            },
            timeline_name,
            track_name,
            expected_project_id,
            idempotency_key,
            expected_track_index=expected_track_index,
            confirm=confirm,
        )
