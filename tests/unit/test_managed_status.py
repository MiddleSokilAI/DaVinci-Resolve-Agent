"""Historical connection evidence cannot hide watcher failure or a stale pulse."""

import time
from pathlib import Path
from typing import Any

import pytest

import providers.resolve.managed_status as module
from providers.resolve.marker_supervisor import Profile, SupervisorError


def setup_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Profile:
    monkeypatch.setattr(module, "read_active", lambda profile: {"phase": "connected"})
    monkeypatch.setattr(time, "time", lambda: 1000)
    return Profile(tmp_path / "profile.json", tmp_path, ())


@pytest.mark.parametrize("phase", ["recovery_required", "dispatch_failed", "disabled"])
def test_watcher_problem_overrides_old_active_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, phase: str,
) -> None:
    profile = setup_status(monkeypatch, tmp_path)
    watcher = {"phase": phase, "updated_at": 999}
    monkeypatch.setattr(module, "read_status", lambda profile: watcher)
    result = module.managed_status(profile)
    assert result["state"] == phase
    assert result["active"]["phase"] == "connected"
    assert result["watcher_health"] == "recent_heartbeat"
    assert result["native_connection_verified"] is False


def test_stale_heartbeat_is_not_running_or_connected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    profile = setup_status(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "read_status", lambda profile: {
        "phase": "connected", "updated_at": 1,
    })
    result = module.managed_status(profile)
    assert result["state"] == "watcher_stale"
    assert result["watcher_health"] == "stale"


def test_invalid_watcher_status_does_not_upgrade_native_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    profile = setup_status(monkeypatch, tmp_path)

    def broken(profile: Profile) -> dict[str, Any]:
        raise SupervisorError("INVALID_SUPERVISOR_STATUS")

    monkeypatch.setattr(module, "read_status", broken)
    result = module.managed_status(profile)
    assert result["state"] == "watcher_status_invalid"
    assert result["native_connection_verified"] is False


def test_invalid_active_preserves_independent_watcher_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    profile = setup_status(monkeypatch, tmp_path)

    def broken(profile: Profile) -> dict[str, Any]:
        raise SupervisorError("RUNTIME_POLICY_DRIFT")

    watcher = {"phase": "recovery_required", "updated_at": 999}
    monkeypatch.setattr(module, "read_active", broken)
    monkeypatch.setattr(module, "read_status", lambda profile: watcher)
    result = module.managed_status(profile)
    assert result["state"] == "active_state_invalid"
    assert result["watcher_status"] == watcher
    assert result["active_state_error"] == "RUNTIME_POLICY_DRIFT"
    assert result["native_connection_verified"] is False
