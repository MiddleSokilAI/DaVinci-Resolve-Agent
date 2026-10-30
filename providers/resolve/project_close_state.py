"""Durable user intent: automatic recovery must never undo project closure."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from providers.resolve.lua_editing import atomic_json

if TYPE_CHECKING:
    from providers.resolve.marker_supervisor import Profile


def close_intent(profile: Profile) -> dict[str, Any] | None:
    """Read bounded evidence, failing closed on corrupt or linked state."""
    from providers.resolve.marker_supervisor import _json

    try:
        value = _json(profile.state_directory / "project-close.json")
    except FileNotFoundError:
        return None
    if (
        set(value) != {"schema", "phase", "command_id", "fingerprint"}
        or value["schema"] != 1
        or value["phase"] not in {"pending", "closed"}
        or not isinstance(value["fingerprint"], str)
        or len(value["fingerprint"]) != 64
        or any(c not in "0123456789abcdef" for c in value["fingerprint"])
        or not isinstance(value["command_id"], str)
        or str(UUID(value["command_id"])) != value["command_id"]
    ):
        raise RuntimeError("INVALID_PROJECT_CLOSE_INTENT")
    return value


def prepare_close(profile: Profile, command_id: str, fingerprint: str) -> None:
    """Persist ownership before dispatch; caller holds the operation lock."""
    if (close_intent(profile) or {}).get("phase") == "pending":
        raise RuntimeError("PROJECT_CLOSE_REQUIRES_RECOVERY")
    atomic_json(
        profile.state_directory / "project-close.json",
        {
            "schema": 1,
            "phase": "pending",
            "command_id": command_id,
            "fingerprint": fingerprint,
        },
    )


def finish_close(profile: Profile, command_id: str, *, closed: bool) -> None:
    """Finalize only this original dispatch, including known receipt recovery."""
    intent = close_intent(profile)
    if not intent or intent["command_id"] != command_id:
        return
    path = profile.state_directory / "project-close.json"
    if closed:
        atomic_json(path, {**intent, "phase": "closed"})
    else:
        path.unlink()


def explicit_open_completed(profile: Profile) -> None:
    """Only a freshly verified explicit open releases the automatic-open hold."""
    intent = close_intent(profile)
    if intent and intent["phase"] == "closed":
        (profile.state_directory / "project-close.json").unlink()
