"""Status checks never open a remembered project or replace native proof."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from mcp_server.automatic import AutomaticServer
from providers.resolve.automatic_connection import AutomaticConnectionError
from providers.resolve.marker_supervisor import Profile
from providers.resolve.project_open_service import ProjectOpenError


def profile_at(tmp_path: Path) -> Profile:
    root = tmp_path / "runtime"
    root.mkdir()
    return Profile(tmp_path / "profile.json", root, (), enabled=True)


@pytest.mark.parametrize("host_state", ["resolve_not_running", "project_manager"])
def test_status_never_launches_resolve_or_opens_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_state: str,
) -> None:
    server = AutomaticServer(profile_at(tmp_path), start_worker=False)
    monkeypatch.setattr(
        type(server.connection.adapter), "inspect", lambda self: {"state": host_state}
    )
    monkeypatch.setattr(
        server.connection, "_open", lambda *a: pytest.fail("Opened history")
    )
    monkeypatch.setattr(
        "providers.resolve.automatic_connection.subprocess.Popen",
        lambda *a, **kw: pytest.fail("Launched Resolve"),
    )

    async def scenario() -> None:
        async with Client(server) as client:
            for _ in range(2):
                result = await client.call_tool(
                    "resolve_free_connection_status", {"timeout_seconds": 10}
                )
                data = result.structured_content
                assert result.is_error and data is not None
                assert data["code"] == "NO_ACTIVE_PROJECT"
                assert data["connected"] is False
                assert data["native_roundtrip_verified"] is False
                assert data["native_request_attempted"] is False
                assert data["project_open_requested"] is False

    asyncio.run(scenario())
    assert not list(server.profile.state_directory.glob("project-open-receipts/*"))


def test_ready_host_still_requires_fresh_native_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = AutomaticServer(profile_at(tmp_path), start_worker=False)
    calls: list[dict[str, Any]] = []

    def ensure(**kwargs: Any) -> dict[str, Any]:
        assert kwargs == {
            "timeout": 10, "require_sdk_channel": False, "allow_project_open": False,
        }
        return {"phase": "connected", "last_native_project": {"id": "stale"}}

    async def fresh(name: str, arguments: dict[str, Any], context: Any) -> Any:
        calls.append(arguments)
        assert name == "resolve_free_connection_status"
        data = {"connected": True, "native_roundtrip_verified": True,
                "project": {"id": "fresh"}, "liveness_source": "fresh_native_request"}
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(data))],
            structured_content=data,
        )

    monkeypatch.setattr(server.connection, "ensure", ensure)
    monkeypatch.setattr(server.marker, "call_tool", fresh)
    result = asyncio.run(server.call_tool(
        "resolve_free_connection_status", {"timeout_seconds": 10}
    ))
    assert result.structured_content["project"] == {"id": "fresh"}
    assert calls == [{"timeout_seconds": 10}]


@pytest.mark.parametrize("error,code,cause,uncertain", [
    (AutomaticConnectionError("PROJECT_CLOSED"), "PROJECT_CLOSED", None, None),
    (ProjectOpenError(
        "UI_OPEN_REJECTED", cause_code="NESTED_OR_UNKNOWN_PROJECT_LAYOUT"),
     "UI_OPEN_REJECTED", "NESTED_OR_UNKNOWN_PROJECT_LAYOUT", False),
    (ProjectOpenError("OPEN_OUTCOME_UNVERIFIED", uncertain=True),
     "OPEN_OUTCOME_UNVERIFIED", None, True),
])
def test_status_failures_are_structured_without_repeating_an_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    error: Exception, code: str, cause: str | None, uncertain: bool | None,
) -> None:
    server = AutomaticServer(profile_at(tmp_path), start_worker=False)
    count = 0

    def refuse(**kwargs: Any) -> Any:
        nonlocal count
        count += 1
        assert kwargs["allow_project_open"] is False
        raise error

    monkeypatch.setattr(server.connection, "ensure", refuse)

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("resolve_free_connection_status", {})
            data = result.structured_content
            assert result.is_error and data is not None
            assert data["code"] == code and data.get("cause_code") == cause
            assert data["uncertain"] is uncertain
            assert data["recording_readiness"] == "not_assessed"
            assert data["connected"] is data["native_roundtrip_verified"] is False
            assert data["liveness_source"] == "local_connection_diagnostics"

    asyncio.run(scenario())
    assert count == 1


@pytest.mark.parametrize("timeout", [0, -1, 601, float("inf"), float("nan")])
def test_invalid_timeout_never_bootstraps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout: float,
) -> None:
    server = AutomaticServer(profile_at(tmp_path), start_worker=False)
    monkeypatch.setattr(
        server.connection, "ensure", lambda **kw: pytest.fail("bootstrap")
    )
    result = asyncio.run(server.call_tool(
        "resolve_free_connection_status", {"timeout_seconds": timeout}
    ))
    assert result.is_error and result.structured_content["code"] == "INVALID_ARGUMENTS"
