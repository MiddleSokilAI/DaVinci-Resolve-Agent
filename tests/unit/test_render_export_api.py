"""Selected export parents survive the application/provider boundary."""

from pathlib import Path
from typing import Any

import pytest

from agent.application import AgentApplication
from agent.media import MediaPolicy, MediaPolicyError
from agent.rendering import RenderOutputVerificationError
from providers.resolve.client import ResolveProviderClient


class RenderCommands:
    def __init__(self, target: Path) -> None:
        self.target = target
        self.calls: list[dict[str, Any]] = []

    def request(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs["action"] == "prepare_render_job":
            return {"job_id": "job-1", "target_directory": str(self.target)}
        return {
            "job_id": "job-1", "rendering_in_progress": False,
            "job": {"TargetDir": str(self.target), "OutputFilename": "Final.mp4"},
            "status": {"CompletionPercentage": 100},
        }


def test_application_prepares_and_verifies_selected_export(tmp_path: Path) -> None:
    destination = tmp_path / "Videos"
    target = destination / ("render-" + "a" * 32)
    target.mkdir(parents=True)
    (target / "Final.mp4").write_bytes(b"rendered file fixture")
    commands = RenderCommands(target)
    app = AgentApplication(
        resolve=ResolveProviderClient(commands),
        media_policy=MediaPolicy([tmp_path], tmp_path / "runtime"),
    )
    app.resolve_prepare_render_job(
        "Final", output_directory=str(destination), idempotency_key="selected",
    )
    assert commands.calls[-1]["arguments"]["output_directory"] == str(destination)
    assert commands.calls[-1]["create_backup"] is True
    assert (tmp_path / "runtime/state/media-policy.json").is_file()
    verified = app.resolve_verify_render_output(
        "job-1", output_directory=str(destination),
    )
    assert verified["validation"]["passed"] is True
    assert verified["output"]["path"] == str(target / "Final.mp4")
    with pytest.raises(ValueError, match="not an export under"):
        app.resolve_verify_render_output("job-1", output_directory=str(tmp_path))
    with pytest.raises(RenderOutputVerificationError, match="outside"):
        app.resolve_verify_render_output("job-1")


def test_invalid_export_is_rejected_before_provider_request(tmp_path: Path) -> None:
    destination = tmp_path / "Videos"
    destination.mkdir()
    commands = RenderCommands(destination)
    app = AgentApplication(
        resolve=ResolveProviderClient(commands),
        media_policy=MediaPolicy([destination], tmp_path / "runtime"),
    )
    with pytest.raises(MediaPolicyError):
        app.resolve_prepare_render_job("Final", output_directory=str(tmp_path))
    assert commands.calls == []
    assert not (tmp_path / "runtime").exists()
