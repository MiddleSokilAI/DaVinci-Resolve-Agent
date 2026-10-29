"""Read persisted watcher and generation evidence without claiming liveness."""

from __future__ import annotations

import time
from typing import Any

from providers.resolve.marker_supervisor import (
    Profile,
    SupervisorError,
    read_active,
    read_status,
)


def managed_status(profile: Profile) -> dict[str, Any]:
    """Keep watcher failures/age distinct from historical native project proof."""
    active_error = None
    try:
        active = read_active(profile)
    except (OSError, ValueError, SupervisorError) as error:
        active = None
        active_error = str(error)[:128]
    watcher_error = None
    try:
        watcher = read_status(profile)
    except (OSError, ValueError, SupervisorError) as error:
        watcher = None
        watcher_error = str(error)[:128]
    health = "unknown"
    state = "watcher_status_unknown"
    if watcher is not None:
        age = time.time() - watcher["updated_at"]
        maximum_age = max(
            90,
            45 + profile.native_timeout_seconds
            + profile.poll_interval_seconds * 2 + 30,
        )
        health = "recent_heartbeat" if 0 <= age <= maximum_age else "stale"
        state = watcher["phase"] if health == "recent_heartbeat" else "watcher_stale"
    if watcher_error is not None:
        state, health = "watcher_status_invalid", "invalid"
    if active_error is not None:
        state = "active_state_invalid"
    return {
        "backend": "free_marker_managed", "active": active,
        "state": state, "watcher_status": watcher, "watcher_health": health,
        "active_state_error": active_error, "watcher_status_error": watcher_error,
        "native_connection_verified": False,
        "proof_scope": "persisted_supervisor_evidence_only",
    }
