"""Canonical protocol example tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from agent.contracts import validate_contract

REPOSITORY_ROOT = Path(__file__).parents[2]
EXAMPLES_ROOT = REPOSITORY_ROOT / "contracts" / "examples"
WRITE_ACTIONS = {
    "close_project",
    "create_project",
    "import_media",
    "create_timeline",
    "ensure_timeline_tracks",
    "duplicate_timeline",
    "set_current_timeline",
    "append_clip",
    "insert_title",
    "insert_animation_template",
    "append_subtitle_file",
    "insert_clip",
    "insert_clips",
    "set_clip_enabled",
    "set_clips_linked",
    "set_clip_transform",
    "set_clip_link_groups",
    "set_clip_transforms",
    "apply_color_preset",
    "delete_clip",
    "add_marker",
    "create_subtitles_from_audio",
    "prepare_render_job",
    "start_render_job",
}


def _load_object(path: Path) -> dict[str, Any]:
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise AssertionError(f"Expected a JSON object in {path}")
    return cast(dict[str, Any], loaded)


def _load_array(path: Path) -> list[dict[str, Any]]:
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list) or not all(
        isinstance(item, dict) for item in loaded
    ):
        raise AssertionError(f"Expected an array of JSON objects in {path}")
    return cast(list[dict[str, Any]], loaded)


def test_command_examples_exactly_cover_schema_actions() -> None:
    command_schema = _load_object(
        REPOSITORY_ROOT / "contracts" / "command.schema.json"
    )
    examples = _load_array(EXAMPLES_ROOT / "commands.json")
    schema_actions = set(
        cast(
            list[str],
            command_schema["properties"]["action"]["enum"],
        )
    )
    example_actions = [cast(str, example["action"]) for example in examples]

    assert len(example_actions) == len(set(example_actions))
    assert set(example_actions) == schema_actions


def test_contract_examples_are_declared_as_package_data() -> None:
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'contracts = ["*.json", "examples/*.json"]' in pyproject


def test_every_command_example_validates_and_has_safe_envelope() -> None:
    examples = _load_array(EXAMPLES_ROOT / "commands.json")
    command_ids: set[str] = set()
    idempotency_keys: set[str] = set()

    for example in examples:
        validate_contract("command", example)
        action = cast(str, example["action"])
        command_id = cast(str, example["command_id"])
        idempotency_key = cast(str, example["idempotency_key"])
        safety = cast(dict[str, bool], example["safety"])
        created_at = datetime.fromisoformat(
            cast(str, example["created_at"]).replace("Z", "+00:00")
        )
        expires_at = datetime.fromisoformat(
            cast(str, example["expires_at"]).replace("Z", "+00:00")
        )

        assert command_id not in command_ids
        assert idempotency_key not in idempotency_keys
        assert expires_at > created_at
        assert safety["create_backup"] is (action in WRITE_ACTIONS)
        assert safety["allow_destructive"] is (action == "delete_clip")
        command_ids.add(command_id)
        idempotency_keys.add(idempotency_key)

    import_example = next(
        example for example in examples if example["action"] == "import_media"
    )
    assert import_example["arguments"] == {"paths": ["media/source.mp4"]}


def test_response_examples_cover_success_and_error_contracts() -> None:
    examples = _load_array(EXAMPLES_ROOT / "responses.json")

    for example in examples:
        validate_contract("response", example)

    assert {example["status"] for example in examples} == {
        "success",
        "error",
    }


def test_capability_example_validates_all_allowed_value_kinds() -> None:
    example = _load_object(EXAMPLES_ROOT / "capabilities.json")

    validate_contract("capability", example)

    assert True in example.values()
    assert "unknown" in example.values()
    assert "requires_confirmation" in example.values()
    assert "requires_studio" in example.values()
