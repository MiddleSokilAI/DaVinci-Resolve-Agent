"""Selected render parents must cross canonical filesystem validation."""

from pathlib import Path
from typing import Any

import pytest

from agent.application import AgentApplication
from agent.client import CommandTimeoutError, FilesystemCommandClient
from agent.contracts import ContractValidationError, validate_contract
from agent.media import MediaPolicy
from providers.resolve.client import ResolveProviderClient
from transports.filesystem import read_json_object


def test_selected_parent_reaches_the_real_filesystem_queue(tmp_path: Path) -> None:
    destination = tmp_path / "Videos"
    destination.mkdir()
    runtime = tmp_path / "runtime"
    app = AgentApplication(
        resolve=ResolveProviderClient(
            FilesystemCommandClient(runtime, poll_interval_seconds=0.001)
        ),
        media_policy=MediaPolicy([tmp_path], runtime),
    )
    # No native worker: reaching a timeout proves the actual validated command
    # was queued. Before this fix it failed schema validation before enqueue.
    with pytest.raises(CommandTimeoutError) as caught:
        app.resolve_prepare_render_job(
            "Final",
            timeline_id="timeline-original",
            output_directory=str(destination),
            timeout_seconds=0.01,
            idempotency_key="original-key",
        )
    paths = list((runtime / "commands").glob("*.json"))
    assert len(paths) == 1
    command = read_json_object(paths[0])
    validate_contract("command", command)
    assert command["arguments"]["output_directory"] == str(destination)
    assert command["arguments"]["timeline_id"] == "timeline-original"
    assert command["idempotency_key"] == caught.value.idempotency_key == "original-key"
    assert command["safety"]["create_backup"] is True


def render_command() -> dict[str, Any]:
    return {
        "protocol_version": "1.0",
        "command_id": "render-command",
        "idempotency_key": "stable-render-key",
        "created_at": "2026-11-02T09:00:00Z",
        "expires_at": "2026-11-02T09:05:00Z",
        "provider": "resolve",
        "action": "prepare_render_job",
        "arguments": {"custom_name": "Final"},
        "safety": {"allow_destructive": False, "create_backup": True},
    }


@pytest.mark.parametrize("directory", [None, "G:/Videos", r"G:\Videos"])
def test_omitted_and_selected_render_parents_are_valid(directory: str | None) -> None:
    command = render_command()
    if directory is not None:
        command["arguments"]["output_directory"] = directory
    validate_contract("command", command)


@pytest.mark.parametrize("directory", [None, "", 4, [], {}])
def test_malformed_selected_parents_are_rejected(directory: Any) -> None:
    command = render_command()
    command["arguments"]["output_directory"] = directory
    with pytest.raises(ContractValidationError):
        validate_contract("command", command)


def test_unknown_preparation_fields_remain_rejected() -> None:
    command = render_command()
    command["arguments"]["unexpected"] = "not-allowed"
    with pytest.raises(ContractValidationError):
        validate_contract("command", command)
