"""Offline managed startup lifecycle tests; no Windows or Resolve is touched."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from providers.resolve import marker_supervisor as module
from providers.resolve.marker_supervisor import (
    OperationBusy,
    Profile,
    Supervisor,
    SupervisorError,
    catalogue_lock,
    operation_lock,
    read_active,
    read_status,
    runtime_history,
)
from providers.resolve.marker_transport import prepare_marker

PROJECT = {"id": "native-project", "name": "Existing project"}


class BootstrapFailure(RuntimeError):
    def __init__(self, *, uncertain: bool) -> None:
        super().__init__("synthetic bootstrap failure")
        self.uncertain = uncertain
        self.code = "MENU_NOT_ACCESSIBLE"


class Adapter:
    """Fixed fake evidence and once-only dispatch with no process access."""

    def __init__(self) -> None:
        self.state = "project_window"
        self.process = {
            "pid": 123,
            "creation_time": "638999000000000000",
            "executable_path": str(Path("C:/Blackmagic/Resolve.exe")),
            "window_handle": 456,
            "window_title": "DaVinci Resolve - Existing project",
            "project_name_hint": "Existing project",
            "window_state": "normal",
        }
        self.inspections = 0
        self.invocations: list[Path] = []
        self.failure: BootstrapFailure | None = None
        self.profile: Profile | None = None

    def inspect(self) -> dict[str, Any]:
        self.inspections += 1
        return {
            "state": self.state,
            "eligible": self.state == "project_window",
            "process": None
            if self.state == "resolve_not_running"
            else dict(self.process),
        }

    def invoke_managed_marker(
        self,
        root: Path,
        process: dict[str, Any],
    ) -> dict[str, Any]:
        assert process == self.process
        assert self.profile is not None
        active = read_active(self.profile)
        assert active is not None and active["dispatch_started"] is True
        assert active["phase"] == "dispatch_started"
        self.invocations.append(root)
        if self.failure is not None:
            raise self.failure
        return {"state": "dispatch_requested", "dispatch_started": True}


class Native:
    """A native proof double exposes only the read operation under test."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str, float]] = []
        self.error: RuntimeError | None = None
        self.project: Any = dict(PROJECT)

    def client(self, root: Path) -> Any:
        parent = self

        class Client:
            def request(self, action: str, timeout: float) -> dict[str, Any]:
                assert action == "get_current_project"
                parent.calls.append((root, action, timeout))
                if parent.error is not None:
                    raise parent.error
                return {"project": parent.project}

        return Client()


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Profile, Adapter, Native, list[Path], list[float]]:
    base, media, state = tmp_path / "runtimes", tmp_path / "media", tmp_path / "watcher"
    base.mkdir()
    media.mkdir()
    state.mkdir()
    path = state / "profile.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "enabled": True,
                "runtime_base": str(base),
                "media_roots": [str(media)],
                "lease_seconds": 60,
                "native_timeout_seconds": 0.1,
                "startup_timeout_seconds": 10,
                "poll_interval_seconds": 0.1,
            }
        ),
        encoding="utf-8",
    )
    profile = Profile.load(path)
    adapter = Adapter()
    adapter.profile = profile
    return profile, adapter, Native(), [], [1000.0]


def watcher(
    setup: tuple[Profile, Adapter, Native, list[Path], list[float]],
) -> Supervisor:
    profile, adapter, native, installs, now = setup
    return Supervisor(
        profile,
        adapter=adapter,
        client_factory=native.client,
        menu_installer=installs.append,
        clock=lambda: now[0],
    )


def fixture_receipt(root: Path, *, status: str = "completed") -> Path:
    folder = root / "receipts"
    folder.mkdir(exist_ok=True)
    path = folder / ("a" * 64 + ".json")
    path.write_text(
        json.dumps(
            {
                "status": status,
                "fingerprint": "b" * 64,
                "command_id": "c" * 32,
                "result": {"action": "create_timeline"},
                "error": "REFUSED",
            }
        ),
        encoding="utf-8",
    )
    return path


def with_initial(
    setup: tuple[Profile, Adapter, Native, list[Path], list[float]],
) -> Path:
    profile, adapter, _, _, _ = setup
    initial = profile.runtime_base / "explicit-initial"
    prepare_marker(initial, list(profile.media_roots), lease_seconds=60)
    data = json.loads(profile.path.read_text(encoding="utf-8"))
    data["initial_runtime"] = str(initial)
    profile.path.write_text(json.dumps(data), encoding="utf-8")
    adapter.profile = Profile.load(profile.path)
    return initial


def test_complete_startup_is_once_only_and_proof_is_native(setup: Any) -> None:
    profile, adapter, native, installs, _ = setup
    supervisor = watcher(setup)
    assert supervisor.tick()["phase"] == "connected"
    active = read_active(profile)
    assert active is not None
    assert active["last_native_project"] == PROJECT
    assert active["last_native_verified_at"] == 1000.0
    assert active["dispatch_started"] is True
    root = Path(active["runtime"])
    assert installs == adapter.invocations == [root]
    assert native.calls == [(root, "get_current_project", 0.1)]
    assert runtime_history(profile) == [root]
    assert supervisor.tick()["phase"] == "connected"
    assert len(adapter.invocations) == len(native.calls) == 1
    assert json.loads((profile.state_directory / "status.json").read_text())[
        "phase"
    ] == ("connected")


@pytest.mark.parametrize(
    "state",
    [
        "resolve_not_running",
        "project_manager",
        "window_unavailable",
        "modal_blocked",
        "desktop_unavailable",
        "process_ambiguous",
    ],
)
def test_waits_without_preparing_or_dispatching(setup: Any, state: str) -> None:
    profile, adapter, native, installs, _ = setup
    adapter.state = state
    assert watcher(setup).tick()["phase"] == (
        "resolve_absent" if state == "resolve_not_running" else "waiting_project"
    )
    assert read_active(profile) is None
    assert not list(profile.runtime_base.iterdir())
    assert adapter.invocations == native.calls == installs == []


def test_disabled_profile_never_inspects_ui(setup: Any) -> None:
    profile, adapter, native, installs, now = setup
    data = json.loads(profile.path.read_text())
    data["enabled"] = False
    profile.path.write_text(json.dumps(data))
    disabled = Profile.load(profile.path)
    supervisor = Supervisor(
        disabled,
        adapter=adapter,
        client_factory=native.client,
        menu_installer=installs.append,
        clock=lambda: now[0],
    )
    assert supervisor.tick()["phase"] == "disabled"
    assert adapter.inspections == 0
    assert adapter.invocations == native.calls == installs == []


def test_process_creation_token_rotates_and_preserves_old_roots(setup: Any) -> None:
    profile, adapter, native, _, _ = setup
    supervisor = watcher(setup)
    supervisor.tick()
    first = read_active(profile)
    assert first is not None
    fixture_receipt(Path(first["runtime"]))
    adapter.process["creation_time"] = "638999000000000001"
    assert supervisor.tick()["phase"] == "connected"
    second = read_active(profile)
    assert second is not None
    assert first["generation"] != second["generation"]
    assert first["session"] != second["session"]
    assert second["previous_runtime"] == first["runtime"]
    assert Path(first["runtime"]).is_dir()
    assert len(runtime_history(profile)) == 2
    assert len(native.calls) == len(adapter.invocations) == 2


def test_window_and_caption_change_do_not_rotate_process_generation(setup: Any) -> None:
    profile, adapter, _, _, _ = setup
    supervisor = watcher(setup)
    supervisor.tick()
    first = read_active(profile)
    adapter.process.update(window_handle=789, window_title="Another window")
    supervisor.tick()
    assert read_active(profile) == first
    assert len(adapter.invocations) == 1


@pytest.mark.parametrize(
    "problem",
    [
        "pending",
        "unknown",
        "corrupt",
        "malformed_completed",
        "inbox",
        "client_lock",
        "lease_lock",
        "invalid_consumed",
        "missing_metadata",
    ],
)
def test_rotation_refuses_uncertain_original_evidence(setup: Any, problem: str) -> None:
    profile, adapter, native, _, _ = setup
    supervisor = watcher(setup)
    supervisor.tick()
    first = read_active(profile)
    assert first is not None
    root = Path(first["runtime"])
    if problem in {"pending", "unknown"}:
        fixture_receipt(root, status=problem)
    elif problem in {"corrupt", "malformed_completed"}:
        path = fixture_receipt(root)
        path.write_text("broken" if problem == "corrupt" else '{"status":"completed"}')
    elif problem == "inbox":
        (root / "inbox").mkdir()
    elif problem == "client_lock":
        (root / "client.lock").write_text("foreign")
    elif problem == "lease_lock":
        (root / "lease-publish.lock").write_text("foreign")
    elif problem == "invalid_consumed":
        (root / "marker-session-consumed.drp").write_text("not a ZIP")
    else:
        (root / "session.json").unlink()
    adapter.process["creation_time"] = "638999000000000001"
    before = {
        path: path.read_bytes()
        for path in profile.runtime_base.rglob("*")
        if path.is_file() and root / "lease" not in path.parents
    }
    assert supervisor.tick()["phase"] == "recovery_required"
    assert len(adapter.invocations) == len(native.calls) == 1
    assert len(list(profile.runtime_base.iterdir())) == 1
    after = {
        path: path.read_bytes()
        for path in profile.runtime_base.rglob("*")
        if path.is_file() and root / "lease" not in path.parents
    }
    assert after == before


def test_audits_every_managed_predecessor_and_ignores_unrelated_directories(
    setup: Any,
) -> None:
    profile, adapter, _, _, _ = setup
    supervisor = watcher(setup)
    supervisor.tick()
    first = read_active(profile)
    assert first is not None
    adapter.process["pid"] += 1
    supervisor.tick()
    fixture_receipt(Path(first["runtime"]), status="pending")
    unrelated = profile.runtime_base / "unrelated"
    unrelated.mkdir()
    (unrelated / "session.json").write_text("not owned")
    adapter.process["pid"] += 1
    assert supervisor.tick()["phase"] == "recovery_required"
    assert len(adapter.invocations) == 2
    assert unrelated not in runtime_history(profile)


def test_explicit_pristine_runtime_is_adopted_without_rewriting_it(setup: Any) -> None:
    initial = with_initial(setup)
    _, adapter, native, installs, now = setup
    assert adapter.profile is not None
    before = (initial / "session.json").read_bytes()
    supervisor = Supervisor(
        adapter.profile,
        adapter=adapter,
        client_factory=native.client,
        menu_installer=installs.append,
        clock=lambda: now[0],
    )
    assert supervisor.tick()["phase"] == "connected"
    active = read_active(adapter.profile)
    assert active is not None and Path(active["runtime"]) == initial
    assert (initial / "session.json").read_bytes() == before
    assert len(list(adapter.profile.runtime_base.iterdir())) == 1


def test_consumed_initial_runtime_is_not_adopted(setup: Any) -> None:
    initial = with_initial(setup)
    with ZipFile(initial / "marker-session-consumed.drp", "w") as archive:
        archive.writestr(
            "project.xml",
            '<SM_Project DbId="id"><ProjectName>Test</ProjectName></SM_Project>',
        )
    _, adapter, native, installs, _ = setup
    assert adapter.profile is not None
    supervisor = Supervisor(
        adapter.profile,
        adapter=adapter,
        client_factory=native.client,
        menu_installer=installs.append,
    )
    assert supervisor.tick()["phase"] == "recovery_required"
    assert adapter.invocations == native.calls == installs == []


def test_uncertain_dispatch_is_never_reinvoked_even_after_watcher_restart(
    setup: Any,
) -> None:
    profile, adapter, native, _, now = setup
    adapter.failure = BootstrapFailure(uncertain=True)
    native.error = RuntimeError("no native response")
    supervisor = watcher(setup)
    assert supervisor.tick()["phase"] == "awaiting_native"
    assert read_active(profile)["dispatch_started"] is True  # type: ignore[index]
    adapter.failure = None
    assert watcher(setup).tick()["phase"] == "awaiting_native"
    assert len(adapter.invocations) == 1
    native.error = None
    assert supervisor.tick()["phase"] == "connected"
    assert len(adapter.invocations) == 1
    assert len(native.calls) == 3
    now[0] += 100
    assert supervisor.tick()["phase"] == "connected"
    assert len(native.calls) == 3


def test_unverified_dispatch_has_bounded_native_probe_window(setup: Any) -> None:
    _, adapter, native, _, now = setup
    native.error = RuntimeError("not connected")
    supervisor = watcher(setup)
    assert supervisor.tick()["phase"] == "awaiting_native"
    now[0] += 11
    assert supervisor.tick()["phase"] == "dispatch_unverified"
    native.error = None
    assert supervisor.tick()["phase"] == "dispatch_unverified"
    assert len(adapter.invocations) == len(native.calls) == 1


def test_certain_dispatch_failure_retries_only_within_initial_deadline(
    setup: Any,
) -> None:
    profile, adapter, native, _, now = setup
    adapter.failure = BootstrapFailure(uncertain=False)
    supervisor = watcher(setup)
    assert supervisor.tick()["phase"] == "dispatch_retry_wait"
    assert supervisor.tick()["phase"] == "dispatch_retry_wait"
    assert read_active(profile)["last_error"] == "MENU_NOT_ACCESSIBLE"  # type: ignore[index]
    now[0] += 11
    assert supervisor.tick()["phase"] == "dispatch_failed"
    assert len(adapter.invocations) == 2
    assert native.calls == []


def test_installation_failure_cannot_dispatch_or_claim_native_proof(setup: Any) -> None:
    profile, adapter, native, _, now = setup

    def refuse(root: Path) -> None:
        raise ValueError("unmanaged menu")

    supervisor = Supervisor(
        profile,
        adapter=adapter,
        client_factory=native.client,
        menu_installer=refuse,
        clock=lambda: now[0],
    )
    assert supervisor.tick()["phase"] == "installation_failed"
    active = read_active(profile)
    assert active is not None and active["dispatch_started"] is False
    assert active["last_native_project"] is None
    assert adapter.invocations == native.calls == []


@pytest.mark.parametrize("project", [None, {}, {"id": "id", "name": ""}])
def test_invalid_native_proof_does_not_become_connected(
    setup: Any, project: Any
) -> None:
    profile, _, native, _, _ = setup
    native.project = project
    assert watcher(setup).tick()["phase"] == "awaiting_native"
    assert read_active(profile)["last_native_project"] is None  # type: ignore[index]


def test_process_absence_preserves_generation_and_never_dispatches(setup: Any) -> None:
    profile, adapter, native, _, _ = setup
    supervisor = watcher(setup)
    supervisor.tick()
    first = read_active(profile)
    adapter.state = "resolve_not_running"
    assert supervisor.tick()["phase"] == "resolve_absent"
    active = read_active(profile)
    assert active is not None and first is not None
    assert active["generation"] == first["generation"]
    assert active["last_native_project"] == PROJECT
    assert len(adapter.invocations) == len(native.calls) == 1


@pytest.mark.parametrize(
    "state",
    [
        "project_manager",
        "window_unavailable",
        "modal_blocked",
    ],
)
def test_verified_process_stays_connected_when_bootstrap_ui_is_unavailable(
    setup: Any,
    state: str,
) -> None:
    profile, adapter, native, _, now = setup
    supervisor = watcher(setup)
    supervisor.tick()
    active = read_active(profile)
    assert active is not None
    with ZipFile(
        Path(active["runtime"]) / "marker-session-consumed.drp", "w"
    ) as archive:
        archive.writestr(
            "project.xml",
            '<SM_Project DbId="native-project">'
            "<ProjectName>Existing project</ProjectName>"
            "</SM_Project>",
        )
    adapter.state = state
    now[0] += 100
    assert supervisor.tick()["phase"] == "connected"
    status = read_status(profile)
    assert status is not None and status["host_state"] == state
    adapter.state = "project_window"
    assert supervisor.tick()["phase"] == "connected"
    assert len(adapter.invocations) == 1 and len(native.calls) == 1


def test_missing_ui_process_evidence_does_not_erase_historical_native_proof(
    setup: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, adapter, native, _, _ = setup
    supervisor = watcher(setup)
    supervisor.tick()
    monkeypatch.setattr(
        adapter,
        "inspect",
        lambda: {
            "state": "window_unavailable",
            "eligible": False,
            "process": None,
        },
    )
    assert supervisor.tick()["phase"] == "connected"
    assert read_active(profile)["last_native_project"] == PROJECT  # type: ignore[index]
    assert len(adapter.invocations) == len(native.calls) == 1


@pytest.mark.parametrize("kind", ["terminal", "invalid"])
def test_known_connected_session_stops_on_local_exit_or_invalid_evidence(
    setup: Any,
    kind: str,
) -> None:
    profile, adapter, native, _, _ = setup
    supervisor = watcher(setup)
    supervisor.tick()
    active = read_active(profile)
    assert active is not None
    root = Path(active["runtime"])
    for name in ("marker-session-consumed.drp", "marker-exit-lease_expired.drp"):
        with ZipFile(root / name, "w") as archive:
            archive.writestr(
                "project.xml",
                '<SM_Project DbId="id"><ProjectName>Test</ProjectName></SM_Project>',
            )
    if kind == "invalid":
        (root / "marker-exit-lease_expired.drp").write_bytes(b"bad ZIP")
    assert supervisor.tick()["phase"] == "recovery_required"
    assert supervisor.tick()["phase"] == "recovery_required"
    assert len(adapter.invocations) == len(native.calls) == 1


def test_known_predispatch_error_can_recover_without_new_runtime(setup: Any) -> None:
    profile, adapter, native, _, _ = setup
    adapter.failure = BootstrapFailure(uncertain=False)
    supervisor = watcher(setup)
    assert supervisor.tick()["phase"] == "dispatch_retry_wait"
    first = read_active(profile)
    adapter.failure = None
    assert supervisor.tick()["phase"] == "connected"
    second = read_active(profile)
    assert first is not None and second is not None
    assert first["generation"] == second["generation"]
    assert len(adapter.invocations) == 2 and len(native.calls) == 1


def test_operation_lock_is_cross_component_and_never_clears_foreign_lock(
    setup: Any,
) -> None:
    profile, adapter, native, _, _ = setup
    with operation_lock(profile):
        with pytest.raises(OperationBusy), operation_lock(profile):
            pytest.fail("Nested operation must not take another owner's lock")
        assert watcher(setup).tick()["phase"] == "operation_busy"
        assert (profile.state_directory / "operation.lock").exists()
    assert (profile.state_directory / "operation.lock").exists()
    assert adapter.inspections == 0 and native.calls == []
    with operation_lock(profile):
        assert watcher(setup).tick()["phase"] == "operation_busy"


def test_second_supervisor_cannot_take_an_existing_supervisor_lock(setup: Any) -> None:
    profile, adapter, _, _, _ = setup
    with module._lock(profile, "supervisor.lock"):
        assert watcher(setup).tick()["phase"] == "operation_busy"
    assert adapter.inspections == 0


def test_abrupt_child_exit_releases_advisory_operation_lock(setup: Any) -> None:
    profile, _, _, _, _ = setup
    command = (
        "import os,sys; from pathlib import Path; "
        "from providers.resolve.marker_supervisor import Profile,operation_lock; "
        "profile=Profile.load(Path(sys.argv[1])); "
        "guard=operation_lock(profile); guard.__enter__(); os._exit(7)"
    )
    child = subprocess.run(
        [sys.executable, "-c", command, str(profile.path)],
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert child.returncode == 7, child.stderr
    with operation_lock(profile):
        assert (profile.state_directory / "operation.lock").stat().st_size <= 1024


def test_linked_lock_file_is_rejected_before_opening(
    setup: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, _, _, _, _ = setup
    original = Path.is_symlink

    def linked(path: Path) -> bool:
        return path.name == "operation.lock" or original(path)

    monkeypatch.setattr(Path, "is_symlink", linked)
    with pytest.raises(SupervisorError, match="LINKED_LOCK"), operation_lock(profile):
        pytest.fail("Linked lock must not open")


def test_oversized_lock_file_is_not_truncated(setup: Any) -> None:
    profile, _, _, _, _ = setup
    path = profile.state_directory / "operation.lock"
    path.write_bytes(b"x" * 1025)
    with (
        pytest.raises(SupervisorError, match="LOCK_FILE_SIZE"),
        operation_lock(profile),
    ):
        pytest.fail("Oversized lock must not be overwritten")
    assert path.read_bytes() == b"x" * 1025


def test_stopped_run_does_no_work_or_native_shutdown(setup: Any) -> None:
    _, adapter, native, _, _ = setup
    stop = threading.Event()
    stop.set()
    supervisor = watcher(setup)
    supervisor.run(stop)
    supervisor.close()
    assert adapter.inspections == 0 and native.calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", 1),
        ("schema", True),
        ("runtime_base", "relative"),
        ("lease_seconds", 4),
        ("native_timeout_seconds", float("inf")),
        ("poll_interval_seconds", 0),
        ("startup_timeout_seconds", 601),
        ("unexpected", "field"),
    ],
)
def test_profile_rejects_invalid_fields_without_creating_state(
    setup: Any,
    field: str,
    value: Any,
) -> None:
    profile, _, _, _, _ = setup
    data = json.loads(profile.path.read_text())
    data[field] = value
    profile.path.write_text(json.dumps(data))
    with pytest.raises(SupervisorError):
        Profile.load(profile.path)
    assert not list(profile.runtime_base.iterdir())
    assert not (profile.state_directory / "active.json").exists()


def test_profile_rejects_initial_runtime_outside_controlled_base(setup: Any) -> None:
    profile, _, _, _, _ = setup
    data = json.loads(profile.path.read_text())
    data["initial_runtime"] = str(profile.state_directory)
    profile.path.write_text(json.dumps(data))
    with pytest.raises(SupervisorError, match="INITIAL_RUNTIME"):
        Profile.load(profile.path)


def test_active_session_drift_and_path_escape_fail_closed(setup: Any) -> None:
    profile, _, _, _, _ = setup
    watcher(setup).tick()
    path = profile.state_directory / "active.json"
    active = json.loads(path.read_text())
    active["session"] = "f" * 32
    path.write_text(json.dumps(active))
    with pytest.raises(SupervisorError, match="SESSION_MISMATCH"):
        read_active(profile)
    active["runtime"] = str(profile.state_directory)
    path.write_text(json.dumps(active))
    with pytest.raises(SupervisorError, match="RUNTIME_OUTSIDE_BASE"):
        read_active(profile)


def test_corrupt_generation_journal_prevents_rotation(setup: Any) -> None:
    profile, adapter, _, _, _ = setup
    supervisor = watcher(setup)
    supervisor.tick()
    active = read_active(profile)
    assert active is not None
    journal = profile.state_directory / "generations" / f"{active['generation']}.json"
    journal.write_text("invalid")
    adapter.process["pid"] += 1
    assert supervisor.tick()["phase"] == "recovery_required"
    assert len(adapter.invocations) == 1


def test_catalogue_lock_is_independent_and_exclusive(setup: Any) -> None:
    profile, _, _, _, _ = setup
    with (
        operation_lock(profile),
        catalogue_lock(profile),
        pytest.raises(OperationBusy),
        catalogue_lock(profile),
    ):
        pytest.fail("Catalogue must have one initializer")
    with catalogue_lock(profile), operation_lock(profile):
        assert (profile.state_directory / "catalogue.lock").exists()


def test_read_status_distinguishes_absence_and_valid_historical_heartbeat(
    setup: Any,
) -> None:
    profile, _, _, _, _ = setup
    assert read_status(profile) is None
    watcher(setup).tick()
    status = read_status(profile)
    assert status is not None
    assert status["phase"] == "connected"
    assert status["updated_at"] == 1000.0
    assert "last_native_project" not in status


@pytest.mark.parametrize(
    "change",
    [
        {"schema": True},
        {"phase": "future_unknown_state"},
        {"phase": []},
        {"updated_at": True},
        {"updated_at": -1},
        {"updated_at": float("nan")},
        {"updated_at": float("inf")},
        {"reason": "x" * 129},
        {"generation": "bad"},
        {"unrecognized": "field"},
    ],
    ids=[
        "schema",
        "unknown_phase",
        "phase_type",
        "bool_time",
        "negative_time",
        "nan",
        "infinite",
        "long_reason",
        "generation",
        "unknown_field",
    ],
)
def test_invalid_status_never_becomes_healthy_or_absent(
    setup: Any,
    change: dict[str, Any],
) -> None:
    profile, _, _, _, _ = setup
    value = {"schema": 1, "phase": "recovery_required", "updated_at": 1000.0}
    value.update(change)
    path = profile.state_directory / "status.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SupervisorError, match="INVALID_SUPERVISOR_STATUS"):
        read_status(profile)


@pytest.mark.parametrize(
    "payload",
    ["invalid", "[]", "x" * 65537],
    ids=["bad_json", "not_object", "oversize"],
)
def test_status_json_uses_bounded_state_reader(setup: Any, payload: str) -> None:
    profile, _, _, _, _ = setup
    (profile.state_directory / "status.json").write_text(payload, encoding="utf-8")
    with pytest.raises(SupervisorError):
        read_status(profile)


@pytest.mark.parametrize("policy", ["extra_scope", "different_scope", "lease"])
def test_initial_adoption_requires_exact_profile_scope_and_lease(
    setup: Any,
    policy: str,
) -> None:
    initial = with_initial(setup)
    profile, adapter, native, installs, _ = setup
    metadata_path = initial / "session.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if policy == "extra_scope":
        metadata["media_roots"].append(str(profile.runtime_base))
    elif policy == "different_scope":
        metadata["media_roots"] = [str(profile.runtime_base)]
    else:
        metadata["lease_seconds"] = 600
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    before = metadata_path.read_bytes()
    assert adapter.profile is not None
    supervisor = Supervisor(
        adapter.profile,
        adapter=adapter,
        client_factory=native.client,
        menu_installer=installs.append,
    )
    result = supervisor.tick()
    assert result["phase"] == "recovery_required"
    assert "POLICY_MISMATCH" in result["reason"] or "SCOPE_MISMATCH" in result["reason"]
    assert adapter.invocations == installs == native.calls == []
    assert metadata_path.read_bytes() == before
    assert read_active(adapter.profile) is None


@pytest.mark.parametrize("change", ["widen_scope", "narrow_scope", "lease"])
def test_active_runtime_rejects_policy_drift_before_routing(
    setup: Any,
    change: str,
) -> None:
    profile, _, _, _, _ = setup
    watcher(setup).tick()
    active = read_active(profile)
    assert active is not None
    config = json.loads(profile.path.read_text(encoding="utf-8"))
    if change == "widen_scope":
        config["media_roots"].append(str(profile.runtime_base))
    elif change == "narrow_scope":
        config["media_roots"] = []
    else:
        config["lease_seconds"] = 600
    profile.path.write_text(json.dumps(config), encoding="utf-8")
    changed = Profile.load(profile.path)
    with pytest.raises(SupervisorError, match="MISMATCH"):
        read_active(changed)
    with pytest.raises(SupervisorError, match="MISMATCH"):
        runtime_history(changed)


def test_media_policy_comparison_uses_canonical_paths_and_order(setup: Any) -> None:
    profile, _, _, _, _ = setup
    watcher(setup).tick()
    active = read_active(profile)
    assert active is not None
    path = Path(active["runtime"]) / "session.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["media_roots"] = [str(profile.media_roots[0] / ".")]
    path.write_text(json.dumps(metadata), encoding="utf-8")
    assert read_active(profile) == active


@pytest.mark.parametrize(
    "roots", [None, "relative", ["relative"]], ids=["missing", "not_list", "relative"]
)
def test_active_runtime_rejects_malformed_media_policy(setup: Any, roots: Any) -> None:
    profile, _, _, _, _ = setup
    watcher(setup).tick()
    active = read_active(profile)
    assert active is not None
    path = Path(active["runtime"]) / "session.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["media_roots"] = roots
    path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(SupervisorError):
        read_active(profile)


def short_lease_setup(setup: Any, poll: float = 0.1) -> Any:
    profile, adapter, native, installs, now = setup
    data = json.loads(profile.path.read_text(encoding="utf-8"))
    data.update(lease_seconds=5, poll_interval_seconds=poll)
    profile.path.write_text(json.dumps(data), encoding="utf-8")
    profile = Profile.load(profile.path)
    adapter.profile = profile
    return profile, adapter, native, installs, now


def lease_renewal_event(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    from providers.resolve.marker_transport import MarkerLeaseKeeper

    event = threading.Event()
    original = MarkerLeaseKeeper.renew
    main = threading.get_ident()

    def observed(keeper: MarkerLeaseKeeper) -> None:
        original(keeper)
        if threading.get_ident() != main:
            event.set()

    monkeypatch.setattr(MarkerLeaseKeeper, "renew", observed)
    return event


def test_short_lease_keeps_renewing_during_slow_host_inspection(
    setup: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = short_lease_setup(setup)
    _, adapter, native, _, _ = setup
    supervisor = watcher(setup)
    assert supervisor.tick()["phase"] == "connected"
    renewed = lease_renewal_event(monkeypatch)
    original = adapter.inspect

    def slow_inspect() -> dict[str, Any]:
        assert renewed.wait(4), "Host inspection must not suspend lease renewal"
        result = original()
        assert isinstance(result, dict)
        return result

    monkeypatch.setattr(adapter, "inspect", slow_inspect)
    assert supervisor.tick()["phase"] == "connected"
    assert len(native.calls) == 1


def test_short_lease_remains_renewed_across_long_poll_interval(
    setup: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = short_lease_setup(setup, poll=60)
    _, _, native, _, _ = setup
    supervisor = watcher(setup)
    assert supervisor.tick()["phase"] == "connected"
    renewed = lease_renewal_event(monkeypatch)

    class StopAfterPoll(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            assert timeout == 60
            renewed.clear()
            assert renewed.wait(4), "Polling must retain the active host lease"
            self.set()
            return True

    supervisor.run(StopAfterPoll())
    assert len(native.calls) == 1


def test_absent_process_does_not_keep_renewing_old_root_after_observation(
    setup: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, adapter, _, _, _ = setup
    supervisor = watcher(setup)
    supervisor.tick()
    adapter.state = "resolve_not_running"
    supervisor.tick()

    def no_keeper(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Already absent process must not receive a new lease")

    monkeypatch.setattr(module, "MarkerLeaseKeeper", no_keeper)
    assert supervisor.tick()["phase"] == "resolve_absent"
