"""Media allowlist and bridge-policy tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.media import MediaPolicy, MediaPolicyError


def _write_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                "[agent]",
                'provider = "resolve"',
                "[runtime]",
                'root = "${LOCALAPPDATA}/DaVinciResolveAgent/runtime"',
                "[resolve]",
                'scripts_category = "Edit"',
                "[safety]",
                "allow_destructive = false",
                "create_backup = true",
                "[media]",
                'allowed_roots = ["${USERPROFILE}/Videos"]',
            )
        ),
        encoding="utf-8",
    )


def test_media_policy_validates_and_publishes_resolved_roots(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    media_root = profile / "Videos"
    media_root.mkdir(parents=True)
    media_file = media_root / "sample.wav"
    media_file.write_bytes(b"fixture")
    config_path = tmp_path / "config.toml"
    _write_config(config_path)
    runtime_root = tmp_path / "runtime"
    policy = MediaPolicy.from_local_config(
        config_path=config_path,
        runtime_root=runtime_root,
        environment={
            "USERPROFILE": str(profile),
            "LOCALAPPDATA": str(tmp_path / "local"),
        },
    )

    assert policy.prepare_import([str(media_file)]) == [
        str(media_file.resolve())
    ]

    published = json.loads(
        (runtime_root / "state" / "media-policy.json").read_text(
            encoding="utf-8"
        )
    )
    assert published == {
        "policy_version": "1.0",
        "allowed_roots": [
            str(media_root.resolve()),
            str((tmp_path / "local" / "DaVinciResolveAgent" / "media").resolve()),
        ],
    }


def test_media_policy_rejects_path_outside_allowed_roots(
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside_file = tmp_path / "outside.wav"
    outside_file.write_bytes(b"fixture")
    policy = MediaPolicy([allowed_root], tmp_path / "runtime")

    with pytest.raises(MediaPolicyError, match="outside configured"):
        policy.prepare_import([str(outside_file)])


def test_export_parent_is_read_only_until_policy_publication(tmp_path: Path) -> None:
    destination = tmp_path / "Videos"
    destination.mkdir()
    runtime = tmp_path / "runtime"
    policy = MediaPolicy([destination], runtime)
    assert policy.validate_export_directory(str(destination)) == str(destination)
    assert not runtime.exists()
    assert list(destination.iterdir()) == []
    assert policy.prepare_export_directory(str(destination)) == str(destination)
    published = json.loads((runtime / "state/media-policy.json").read_text())
    assert published["allowed_roots"] == [str(destination)]
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("value", ["relative", "", None, 12, "nul\x00path"])
def test_export_parent_rejects_invalid_values(tmp_path: Path, value: Any) -> None:
    with pytest.raises(MediaPolicyError):
        MediaPolicy([tmp_path]).validate_export_directory(value)


def test_export_parent_rejects_missing_file_and_outside(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    file = allowed / "file.mp4"
    file.touch()
    policy = MediaPolicy([allowed], tmp_path / "runtime")
    for value in (tmp_path, allowed / "missing", file):
        with pytest.raises(MediaPolicyError):
            policy.prepare_export_directory(str(value))
    assert not (tmp_path / "runtime").exists()


def test_export_parent_rejects_symlink_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "escape"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks require Windows Developer Mode or privilege")
    with pytest.raises(MediaPolicyError, match="outside"):
        MediaPolicy([allowed]).validate_export_directory(str(link))
