"""Render calls retain diagnostics and budgets across the public MCP boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client

from agent.application import AgentApplication
from agent.client import (
    BridgeCommandError,
    CommandTimeoutError,
    FilesystemCommandClient,
)
from mcp_server.server import create_server


@pytest.mark.parametrize(
    "name", ["resolve_get_render_options", "resolve_prepare_render_job"]
)
@pytest.mark.parametrize("timeout", [None, 120])
def test_render_default_budget_and_explicit_override(
    name: str, timeout: int | None
) -> None:
    calls = []

    class Service:
        def resolve_get_render_options(self, timeout_seconds: float) -> dict[str, Any]:
            calls.append(timeout_seconds)
            return {"formats": [], "jobs": []}

        def resolve_prepare_render_job(
            self, custom_name: str, **kwargs: Any
        ) -> dict[str, Any]:
            calls.append(kwargs["timeout_seconds"])
            assert kwargs["output_directory"] == "G:\\Videos"
            assert kwargs["idempotency_key"] == "original-key"
            return {"job_id": "original-job"}

    async def exercise() -> None:
        async with Client(create_server(cast(AgentApplication, Service()))) as client:
            schemas = {
                t.name: t.input_schema for t in (await client.list_tools()).tools
            }
            assert schemas[name]["properties"]["timeout_seconds"]["default"] == 300
            args: dict[str, Any] = (
                {}
                if name.endswith("options")
                else {
                    "custom_name": "Final",
                    "output_directory": "G:\\Videos",
                    "idempotency_key": "original-key",
                }
            )
            if timeout is not None:
                args["timeout_seconds"] = timeout
            result = await client.call_tool(name, args)
            assert not result.is_error
            expected: dict[str, Any] = (
                {"formats": [], "jobs": []}
                if name.endswith("options")
                else {"job_id": "original-job"}
            )
            assert result.structured_content == expected

    asyncio.run(exercise())
    assert calls == [300 if timeout is None else timeout]


@pytest.mark.parametrize(
    "name", ["resolve_get_render_options", "resolve_prepare_render_job"]
)
@pytest.mark.parametrize(
    "error,code",
    [
        (ValueError("Unsupported render profile: invalid"), "INVALID_ARGUMENTS"),
        (
            BridgeCommandError(
                code="RENDER_PRESET_UNAVAILABLE",
                message="Missing preset",
                retryable=False,
            ),
            "RENDER_PRESET_UNAVAILABLE",
        ),
        (CommandTimeoutError("original-command", 30), "COMMAND_TIMEOUT"),
    ],
)
def test_render_errors_survive_mcp_and_never_repeat_call(
    name: str, error: Exception, code: str
) -> None:
    calls = []

    class Service:
        def resolve_get_render_options(
            self, *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            calls.append(name)
            raise error

        resolve_prepare_render_job = resolve_get_render_options

    async def exercise() -> None:
        async with Client(create_server(cast(AgentApplication, Service()))) as client:
            args = {} if name.endswith("options") else {"custom_name": "Final"}
            result = await client.call_tool(name, args)
            assert result.is_error
            data = result.structured_content
            assert data["code"] == code
            assert str(error) in data["detail"]
            assert data["retryable"] is False
            if code == "COMMAND_TIMEOUT":
                assert data["command_id"] == "original-command"
                assert data["outcome"] == "unverified"
                assert "new idempotency key" in data["detail"]

    asyncio.run(exercise())
    assert calls == [name]


@pytest.mark.parametrize("key", [None, "stable-render-key"])
def test_timeout_exposes_the_exact_durable_key(tmp_path: Path, key: str | None) -> None:
    import json

    client = FilesystemCommandClient(tmp_path, poll_interval_seconds=0.001)
    with pytest.raises(CommandTimeoutError) as caught:
        client.request(
            provider="resolve",
            action="prepare_render_job",
            arguments={"custom_name": "Final"},
            create_backup=True,
            timeout_seconds=0.01,
            idempotency_key=key,
        )
    commands = list((tmp_path / "commands").glob("*.json"))
    assert len(commands) == 1
    command = json.loads(commands[0].read_text())
    assert caught.value.idempotency_key == command["idempotency_key"]
    assert caught.value.command_id == command["command_id"]
    assert command["safety"]["create_backup"] is True


def test_invalid_profile_reports_error_without_queueing() -> None:
    class Resolve:
        def prepare_render_job(self, *args: Any, **kwargs: Any) -> Any:
            pytest.fail("Invalid profile reached native preparation")

    async def exercise() -> None:
        application = AgentApplication(resolve=cast(Any, Resolve()))
        async with Client(create_server(application)) as client:
            result = await client.call_tool(
                "resolve_prepare_render_job",
                {
                    "custom_name": "Final",
                    "profile": "not-a-render-profile",
                },
            )
            assert result.is_error
            assert result.structured_content["code"] == "INVALID_ARGUMENTS"
            assert "youtube-1080p-h264-v1" in result.structured_content["detail"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "name", ["resolve_get_render_options", "resolve_prepare_render_job"]
)
def test_readiness_failure_is_not_reported_as_a_dispatched_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    from mcp_server.automatic import AutomaticServer
    from providers.resolve.marker_supervisor import Profile

    server = AutomaticServer(
        Profile(tmp_path / "profile.json", tmp_path, ()), start_worker=False
    )
    error = CommandTimeoutError("readiness-command", 60)

    async def not_ready() -> None:
        raise error

    monkeypatch.setattr(server, "_ready", not_ready)

    async def scenario() -> None:
        args = {} if name.endswith("options") else {"custom_name": "Final"}
        result = await server.call_tool(name, args)
        assert result.is_error
        assert result.structured_content["code"] == "COMMAND_TIMEOUT"
        assert result.structured_content["phase"] == "readiness"
        assert result.structured_content["outcome"] == "not_dispatched"

    asyncio.run(scenario())
