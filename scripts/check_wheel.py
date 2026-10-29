"""Validate the built wheel boundary without installing or publishing it."""

from __future__ import annotations

import argparse
import configparser
import re
import sys
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Final

from agent import __version__

EXPECTED_DISTRIBUTION: Final = "davinci-resolve-agent"
EXPECTED_REQUIRES_PYTHON: Final = frozenset({">=3.10", "<3.13"})
REQUIRED_MEMBERS: Final = frozenset(
    {
        "agent/__init__.py",
        "agent/cli.py",
        "agent/recipes.py",
        "agent/animation_templates.py",
        "agent/animation_workflow.py",
        "agent/color_presets.py",
        "agent/color_workflow.py",
        "agent/take_selection.py",
        "agent/capabilities.py",
        "contracts/workflow-capabilities.schema.json",
        "agent/storage.py",
        "bridges/resolve/ResolveBridge.py",
        "config/default.toml",
        "config/recipes/tutorial-layout-v1.json",
        "config/animation_templates/accent-card-v1.json",
        "config/color_presets/tutorial-clean-v1.json",
        "config/fusion_templates/Edit/Titles/DaVinci Agent Accent Card.setting",
        "contracts/command.schema.json",
        "contracts/response.schema.json",
        "contracts/capability.schema.json",
        "contracts/editing-recipe.schema.json",
        "contracts/editing-recipe-run.schema.json",
        "contracts/animation-template.schema.json",
        "contracts/animation-template-preview.schema.json",
        "contracts/animation-template-result.schema.json",
        "contracts/color-preset.schema.json",
        "contracts/color-treatment-preview.schema.json",
        "contracts/color-treatment-result.schema.json",
        "contracts/take-selection.schema.json",
        "contracts/take-selection-review.schema.json",
        "contracts/examples/commands.json",
        "contracts/examples/responses.json",
        "contracts/examples/capabilities.json",
        "mcp_server/server.py",
        "providers/base.py",
        "providers/resolve/client.py",
        "providers/resolve/ResolveRecordingUI.ps1",
        "providers/resolve/ResolveFairlightUI.ps1",
        "providers/resolve/ResolveFairlightCore.psm1",
        "providers/resolve/ResolveFairlightPatch.psm1",
        "providers/resolve/windows_fairlight.py",
        "providers/resolve/fairlight_ui_service.py",
        "providers/resolve/windows_ui_common.py",
        "mcp_server/fairlight.py",
        "providers/resolve/startup_diagnostics.py",
        "providers/resolve/windows_host.py",
        "providers/resolve/ResolveHostMetadata.ps1",
        "providers/resolve/ResolveBootstrapUI.ps1",
        "providers/resolve/ResolveAutostartEntry.ps1",
        "providers/resolve/ResolveProjectManagerUI.ps1",
        "providers/resolve/windows_project_manager.py",
        "providers/resolve/project_open_service.py",
        "providers/resolve/project_library_catalogue.py",
        "providers/resolve/lua_project_context.py",
        "bridges/resolve/ResolveLuaProjectContext.lua",
        "providers/resolve/windows_bootstrap.py",
        "providers/resolve/marker_supervisor.py",
        "providers/resolve/managed_status.py",
        "mcp_server/managed_marker.py",
        "agent/resolve_watch.py",
        "bridges/resolve/ResolveLuaRecording.lua",
        "transports/filesystem.py",
    }
)
FORBIDDEN_PREFIXES: Final = (
    "docs/",
    "installer/",
    "runtime/",
    "tests/",
)
EXPECTED_ENTRY_POINTS: Final = {
    "davinci-agent": "agent.cli:main",
    "davinci-agent-mcp": "mcp_server.server:main",
}


class WheelValidationError(ValueError):
    """Raised when a built wheel violates the package boundary."""


def _normalized_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _validate_archive_paths(names: set[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or ".." in path.parts
        ):
            raise WheelValidationError(f"Unsafe wheel archive path: {name!r}")


def _one_metadata_member(names: set[str], filename: str) -> str:
    suffix = f".dist-info/{filename}"
    matches = sorted(name for name in names if name.endswith(suffix))
    if len(matches) != 1:
        raise WheelValidationError(
            f"Expected one {filename} member, found {len(matches)}."
        )
    return matches[0]


def _validate_metadata(archive: zipfile.ZipFile, member: str) -> None:
    metadata = BytesParser(policy=policy.default).parsebytes(archive.read(member))
    distribution = metadata.get("Name")
    version = metadata.get("Version")
    requires_python = metadata.get("Requires-Python")

    if not distribution or (
        _normalized_distribution(distribution) != EXPECTED_DISTRIBUTION
    ):
        raise WheelValidationError(
            f"Unexpected wheel distribution name: {distribution!r}"
        )
    if version != __version__:
        raise WheelValidationError(
            f"Wheel version {version!r} does not match agent {__version__!r}."
        )
    actual_python_specifiers = {
        specifier.strip() for specifier in (requires_python or "").split(",")
    }
    if actual_python_specifiers != EXPECTED_REQUIRES_PYTHON:
        raise WheelValidationError(
            "Unexpected Requires-Python: "
            f"{requires_python!r}; expected {sorted(EXPECTED_REQUIRES_PYTHON)!r}."
        )


def _validate_entry_points(archive: zipfile.ZipFile, member: str) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(archive.read(member).decode("utf-8"))
    if not parser.has_section("console_scripts"):
        raise WheelValidationError("Wheel has no console_scripts entry points.")

    actual = dict(parser.items("console_scripts"))
    if actual != EXPECTED_ENTRY_POINTS:
        raise WheelValidationError(
            f"Unexpected console entry points: {actual!r}"
        )


def validate_wheel_directory(directory: Path) -> Path:
    """Validate exactly one wheel in *directory* and return its path."""
    resolved_directory = directory.resolve()
    if not resolved_directory.is_dir():
        raise WheelValidationError(
            f"Wheel directory does not exist: {resolved_directory}"
        )

    wheels = sorted(resolved_directory.glob("*.whl"))
    if len(wheels) != 1:
        raise WheelValidationError(
            f"Expected exactly one wheel, found {len(wheels)}."
        )

    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        _validate_archive_paths(names)

        missing = sorted(REQUIRED_MEMBERS - names)
        if missing:
            raise WheelValidationError(
                f"Wheel is missing required members: {', '.join(missing)}"
            )

        forbidden = sorted(
            name
            for name in names
            if name.startswith(FORBIDDEN_PREFIXES)
        )
        if forbidden:
            raise WheelValidationError(
                "Wheel contains repository-only members: "
                + ", ".join(forbidden)
            )

        metadata_member = _one_metadata_member(names, "METADATA")
        entry_points_member = _one_metadata_member(names, "entry_points.txt")
        _validate_metadata(archive, metadata_member)
        _validate_entry_points(archive, entry_points_member)

    return wheel


def build_parser() -> argparse.ArgumentParser:
    """Build the wheel-check command parser."""
    parser = argparse.ArgumentParser(
        description="Validate one DaVinci Resolve Agent wheel."
    )
    parser.add_argument(
        "wheel_directory",
        type=Path,
        help="Directory containing exactly one .whl file.",
    )
    return parser


def main() -> int:
    """Run the wheel package check."""
    arguments = build_parser().parse_args()
    try:
        wheel = validate_wheel_directory(arguments.wheel_directory)
    except (OSError, UnicodeError, WheelValidationError, zipfile.BadZipFile) as error:
        print(f"Wheel validation failed: {error}", file=sys.stderr)
        return 1

    print(f"Wheel validation passed: {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
