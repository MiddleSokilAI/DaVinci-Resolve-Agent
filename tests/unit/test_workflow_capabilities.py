"""Workflow discovery never promotes primitive support or cached evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

from agent.application import AgentApplication
from agent.capabilities import WORKFLOWS, workflow_capabilities
from mcp_server.free_marker import create_server
from mcp_server.server import create_server as create_python_server
from providers.resolve.lua_transport import LuaSnapshotClient
from providers.resolve.marker_transport import prepare_marker


def rows(report: dict[str, Any]) -> dict[str, Any]:
    return {row["workflow"]: row for row in report["workflows"]}


def marker_report(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "backend": "free_marker",
        "tools": [tool for workflow in WORKFLOWS for tool in (
            *workflow.marker_tools, *workflow.marker_alternatives
        )],
        "connected": True, "evidence": "fresh_native_request",
        "session_capabilities": ["recording_api_v1"],
        "media_roots_configured": True,
        "ui": {"available": True},
        "fairlight": {"available": True, "live_verification": "pending"},
    }
    return workflow_capabilities(**(arguments | overrides))


def test_marker_alternatives_never_claim_python_workflow_support() -> None:
    report = rows(marker_report())
    assert report["timeline_assembly"]["available"] is True
    for name in ("screen_webcam_sync", "webcam_picture_in_picture",
                 "pause_compaction", "automatic_subtitles", "broll_workflow"):
        assert report[name]["status"] == "partial"
        assert report[name]["supported"] is False
        assert report[name]["available"] is False
        assert report[name]["alternative_tools"]
        assert report[name]["tools"] == []
    for name in ("approved_take_delivery", "recording_start", "effect_inserts",
                 "color_treatment", "animation_templates", "baseline_edit",
                 "editing_recipes", "visual_treatment", "finalized_audio_delivery",
                 "finalized_render"):
        assert report[name]["status"] == "unsupported"
    assert report["fairlight_processing"]["status"] == "unverified"


@pytest.mark.parametrize("connected", [False, None])
def test_catalogue_and_cache_cannot_prove_native_availability(
    connected: bool | None,
) -> None:
    report = marker_report(connected=connected, evidence="cached_supervisor")
    assert not any(row["available"] for row in report["workflows"])


def test_pending_write_blocks_mutations_but_retains_read_inspection() -> None:
    report = rows(marker_report(
        mutation_blocker="original_operation_recovery_required"
    ))
    assert report["project_inspection"]["available"] is True
    assert report["timeline_assembly"]["reason"] == (
        "original_operation_recovery_required"
    )
    assert report["timeline_assembly"]["available"] is False


def test_session_media_and_ui_gates() -> None:
    report = rows(marker_report(
        session_capabilities=[], media_roots_configured=False, ui={"available": False}
    ))
    assert report["media_import"]["reason"] == "media_roots_not_verified"
    assert report["recording_track_setup"]["reason"] == "session_upgrade_required"
    assert report["physical_audio_input"]["reason"] == (
        "windows_ui_unavailable_or_disabled"
    )
    report = rows(marker_report(tools=["resolve_lua_create_timeline"]))
    assert report["timeline_assembly"]["supported"] is False
    assert report["timeline_assembly"]["missing_tools"] == ["resolve_lua_append_clip"]


def test_python_local_tools_work_offline_and_native_capabilities_fail_closed() -> None:
    tools = [tool for workflow in WORKFLOWS for tool in workflow.python_tools]
    report = rows(workflow_capabilities(
        backend="python", tools=tools, connected=None, evidence="cached_python_state"
    ))
    assert report["take_analysis"]["available"] is True
    assert report["timeline_assembly"]["available"] is False
    report = rows(workflow_capabilities(
        backend="python", tools=tools, connected=True, evidence="fresh_native_request",
        native_capabilities={"timeline.create": True, "clip.insert": "unknown"},
        media_roots_configured=True,
    ))
    assert report["timeline_assembly"]["reason"] == "native_capability_not_confirmed"


def test_known_edition_restriction_is_unsupported_rather_than_unverified() -> None:
    report = rows(
        workflow_capabilities(
            backend="python",
            tools=["resolve_create_subtitles_from_audio"],
            connected=True,
            evidence="fresh_native_request",
            native_capabilities={"subtitle.auto_caption": False},
        )
    )
    assert report["resolve_auto_captions"]["status"] == "unsupported"
    assert report["resolve_auto_captions"]["reason"] == "native_capability_unavailable"


def test_marker_capability_tool_default_never_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root, [tmp_path])
    server = create_server(root)

    def forbidden(*args: Any, **kwargs: Any) -> dict[str, Any]:
        pytest.fail("Offline capability discovery dispatched a native request")

    monkeypatch.setattr(LuaSnapshotClient, "request", forbidden)
    result = asyncio.run(server.call_tool("video_agent_capabilities", {}))
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["connected"] is None
    assert str(tmp_path) not in json.dumps(result.structured_content)
    assert not (root / "inbox").exists()


def test_marker_capability_tool_uses_fresh_project_and_recovery_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    prepare_marker(root, [tmp_path])
    server = create_server(root)
    requests: list[str] = []

    def request(self: LuaSnapshotClient, action: str, timeout: float) -> dict[str, Any]:
        requests.append(action)
        return {"project": {"id": "project-id", "name": "Current"}}

    monkeypatch.setattr(LuaSnapshotClient, "request", request)
    arguments = {"verify_connection": True}
    result = asyncio.run(server.call_tool("video_agent_capabilities", arguments))
    assert isinstance(result, CallToolResult)
    report = result.structured_content
    assert report is not None
    assert report["connected"] is True
    assert report["project"]["id"] == "project-id"
    assert rows(report)["timeline_assembly"]["available"] is True
    receipts = root / "receipts"
    receipts.mkdir(exist_ok=True)
    (receipts / ("a" * 64 + ".json")).write_text(json.dumps({
        "status": "pending", "command_id": "b" * 32, "fingerprint": "c" * 64
    }))
    result = asyncio.run(server.call_tool("video_agent_capabilities", arguments))
    assert isinstance(result, CallToolResult)
    report = result.structured_content
    assert report is not None
    assert rows(report)["timeline_assembly"]["available"] is False
    assert requests == ["get_current_project", "get_current_project"]


def test_python_tool_requires_explicit_fresh_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = AgentApplication()
    calls: list[float] = []

    def project(timeout: float) -> dict[str, Any]:
        calls.append(timeout)
        return {"project_id": "python-project", "name": "Current"}

    monkeypatch.setattr(application, "resolve_get_project", project)
    monkeypatch.setattr(application, "status", lambda: {
        "healthy": True, "bridge": {"capabilities": {"timeline.read": True}}
    })
    server = create_python_server(application)
    cached = asyncio.run(server.call_tool("video_agent_capabilities", {}))
    assert isinstance(cached, CallToolResult)
    assert cached.structured_content is not None
    assert cached.structured_content["connected"] is None
    assert calls == []
    fresh = asyncio.run(server.call_tool("video_agent_capabilities", {
        "verify_connection": True, "timeout_seconds": 7
    }))
    assert isinstance(fresh, CallToolResult)
    assert fresh.structured_content is not None
    assert fresh.structured_content["connected"] is True
    assert rows(fresh.structured_content)["project_inspection"]["available"] is True
    assert calls == [7]


def test_python_discovery_remains_usable_without_bridge_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = AgentApplication()

    def unavailable(*args: Any) -> dict[str, Any]:
        raise RuntimeError("Bridge unavailable")

    monkeypatch.setattr(application, "resolve_get_project", unavailable)
    monkeypatch.setattr(application, "status", unavailable)
    result = asyncio.run(create_python_server(application).call_tool(
        "video_agent_capabilities", {"verify_connection": True}
    ))
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["connected"] is False
    assert rows(result.structured_content)["take_analysis"]["available"] is True
