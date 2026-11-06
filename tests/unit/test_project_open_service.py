"""Offline host selection/receipt tests; never access desktop or native Resolve."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.lua_editing import atomic_json
from providers.resolve.lua_transport import LuaCommandTimeoutError
from providers.resolve.marker_supervisor import (
    OperationBusy,
    Profile,
    _lock,
    operation_lock,
    project_open_lock,
)
from providers.resolve.marker_transport import prepare_marker
from providers.resolve.project_open_service import (
    ProjectOpenError,
    ProjectOpenService,
    pending_project_open,
)

PROJECT = {"id": "exact-project-uuid", "name": "Existing project"}
DATABASE = {"DbType": "Disk", "DbName": "Local Database"}


class UIError(RuntimeError):
    def __init__(self, uncertain: bool) -> None:
        super().__init__("offline UI failure")
        self.uncertain = uncertain
        self.code = "SYNTHETIC_UI_FAILURE"
        self.candidate_export_uncertain = False
        self.view_changes_uncertain = False
        self.diagnostics: dict[str, Any] = {}


class Fixture:
    """One controllable process/bridge with lock and durable-intent assertions."""

    def __init__(self, tmp_path: Path) -> None:
        self.base, self.state = tmp_path / "runtime", tmp_path / "state"
        self.base.mkdir()
        self.state.mkdir()
        self.root = self.base / "session"
        prepare_marker(self.root, [], lease_seconds=60)
        self.profile = Profile(
            self.state / "profile.json",
            self.base,
            (),
            True,
            self.root,
            lease_seconds=60,
            native_timeout_seconds=0.1,
            poll_interval_seconds=0.1,
        )
        self.now = 100.0
        self.ui_state = "project_manager"
        self.process: dict[str, Any] = {
            "pid": 123,
            "creation_time": "638999000000000000",
            "executable_path": (
                "C:/Program Files/Blackmagic Design/DaVinci Resolve/Resolve.exe"
            ),
            "window_handle": 456,
            "window_title": "Project Manager",
            "project_name_hint": None,
            "window_state": "normal",
        }
        self.inspections = 0
        self.opens: list[dict[str, Any]] = []
        self.accepted_opens = 0
        self.export_inspections = 0
        self.export_inspection_error: Exception | None = None
        self.export_inspection_overrides: dict[str, Any] = {}
        self.native_calls: list[dict[str, Any]] = []
        self.ticks = 0
        self.bootstrap = True
        self.ui_error: UIError | None = None
        self.native_error: Exception | None = None
        self.native_override: dict[str, Any] = {}
        self.capabilities = {"project_context_v1"}
        self.generation = "a" * 32
        self.lock_after_open = False
        self.held_lock: Any = None
        self.startups: list[str] = []
        self.startup_error: Exception | None = None
        self.restore_target = False

    def startup(self, deadline: float, name: str) -> dict[str, Any]:
        self.startups.append(name)
        if self.startup_error is not None:
            raise self.startup_error
        self.process = {
            **self.process,
            "creation_time": str(int(self.process["creation_time"]) + 1),
        }
        self.ui_state = "project_window" if self.restore_target else "project_manager"
        self.process["project_name_hint"] = name if self.restore_target else None
        self.generation = "c" * 32
        return {"state": self.ui_state, "process": dict(self.process)}

    def inspect(self) -> dict[str, Any]:
        self.inspections += 1
        return {
            "state": self.ui_state,
            "eligible": self.ui_state == "project_manager",
            "process": None
            if self.ui_state == "resolve_not_running"
            else dict(self.process),
        }

    def open_existing(
        self, process: dict[str, Any], **arguments: Any
    ) -> dict[str, Any]:
        assert process == self.process
        with operation_lock(self.profile):
            pending = pending_project_open(self.profile)
            assert pending["pending"] is True
            assert pending["receipts"][0]["status"] == "dispatch_started"
        self.opens.append(arguments)
        if self.lock_after_open:
            self.held_lock = operation_lock(self.profile)
            self.held_lock.__enter__()
        if self.ui_error is not None:
            raise self.ui_error
        self.accepted_opens += 1
        self.ui_state = "other_project_open"
        return {"state": "open_requested", "dispatch_started": True}

    def inspect_export_dialog(self, process: dict[str, Any]) -> dict[str, Any]:
        assert process == self.process
        self.export_inspections += 1
        if self.export_inspection_error is not None:
            raise self.export_inspection_error
        return {
            "process": dict(process),
            "pm_found": True,
            "windows_truncated": False,
            "native_visible_handles": [process["window_handle"]],
            "windows": [
                {
                    "handle": process["window_handle"],
                    "offscreen": False,
                    "enabled": True,
                }
            ],
            **self.export_inspection_overrides,
        }

    def active(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "generation": self.generation,
            "runtime": str(self.root),
            "session": json.loads((self.root / "session.json").read_text())["session"],
            "process": dict(self.process),
            "phase": "connected",
            "dispatch_started": True,
            "last_native_project": dict(PROJECT),
        }

    def connect(self) -> None:
        atomic_json(self.state / "active.json", self.active())

    def tick(self) -> dict[str, Any]:
        try:
            with operation_lock(self.profile):
                self.ticks += 1
                if self.bootstrap:
                    self.connect()
        except OperationBusy:
            return {"phase": "operation_busy"}
        return {"phase": "connected" if self.bootstrap else "waiting_project"}

    def sleep(self, duration: float) -> None:
        if self.held_lock is not None:
            self.held_lock.__exit__(None, None, None)
            self.held_lock = None
        self.now += duration

    def client(self, root: Path) -> Any:
        assert root == self.root
        fixture = self

        class Client:
            capabilities = fixture.capabilities

            def request(
                self, action: str, timeout: float, **arguments: Any
            ) -> dict[str, Any]:
                with pytest.raises(OperationBusy), operation_lock(fixture.profile):
                    pass
                fixture.native_calls.append(
                    {"action": action, "timeout": timeout, **arguments}
                )
                if fixture.native_error is not None:
                    raise fixture.native_error
                response = root / ("b" * 32 + ".ok_context_verified.drp")
                response.write_bytes(b"offline native export fixture")
                return {
                    "project": dict(PROJECT),
                    "database": dict(DATABASE),
                    "folder": "",
                    "context_verified": True,
                    "folder_membership_verified": True,
                    "source": "live_lua_api",
                    "readback_verified_by": "ResolveLuaProjectContext",
                    "response_path": str(response),
                    **fixture.native_override,
                }

        return Client()

    def service(self) -> ProjectOpenService:
        return ProjectOpenService(
            self.profile,
            adapter=self,
            supervisor_factory=lambda _: self,
            client_factory=self.client,
            clock=lambda: self.now,
            sleep=self.sleep,
            startup=self.startup,
        )

    def open(self, **kwargs: Any) -> dict[str, Any]:
        return self.service().open_existing(
            **{
                "project_name": PROJECT["name"],
                "expected_project_id": PROJECT["id"],
                "expected_database": dict(DATABASE),
                "folder_path": [],
                "idempotency_key": "open-project",
                "confirm": True,
                "timeout_seconds": 1,
                **kwargs,
            }
        )

    def receipt_path(self, key: str = "open-project") -> Path:
        return (
            self.state
            / "project-open-receipts"
            / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        )


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path)


def rejected_window(fixture: Fixture) -> bytes:
    error = UIError(False)
    error.code = "WINDOW_UNAVAILABLE"
    fixture.ui_error = error
    with pytest.raises(ProjectOpenError, match="UI_OPEN_REJECTED"):
        fixture.open()
    fixture.ui_error = None
    return fixture.receipt_path().read_bytes()


def test_explicit_preflight_retry_keeps_key_history_and_never_repeats_completed_open(
    fixture: Fixture,
) -> None:
    before = rejected_window(fixture)
    with pytest.raises(ProjectOpenError, match="ORIGINAL_OPEN_REJECTED"):
        fixture.open()
    assert fixture.receipt_path().read_bytes() == before
    result = fixture.open(confirm_retry_preflight=True)
    assert result["context_verified"] and result["preflight_retried"]
    assert result["recovered_after_process_exit"] is False
    saved = json.loads(fixture.receipt_path().read_text())
    assert saved["schema"] == 3
    assert saved["previous_attempts"][0]["receipt_json"].encode() == before
    assert fixture.startups == [] and len(fixture.opens) == 2
    assert fixture.open(confirm_retry_preflight=True)["replayed"] is True
    assert len(fixture.opens) == 2


@pytest.mark.parametrize(
    "state", ["other_project_open", "resolve_not_running", "modal_blocked"]
)
def test_preflight_retry_never_starts_or_switches_project(
    fixture: Fixture, state: str
) -> None:
    before = rejected_window(fixture)
    fixture.ui_state = state
    with pytest.raises(ProjectOpenError, match="PREFLIGHT_MANAGER_UNVERIFIED"):
        fixture.open(confirm_retry_preflight=True)
    assert fixture.receipt_path().read_bytes() == before
    assert fixture.startups == [] and len(fixture.opens) == 1


def test_preflight_retry_rejects_replaced_process(fixture: Fixture) -> None:
    before = rejected_window(fixture)
    fixture.process["creation_time"] = "638999000000000001"
    with pytest.raises(ProjectOpenError, match="PREFLIGHT_MANAGER_UNVERIFIED"):
        fixture.open(confirm_retry_preflight=True)
    assert fixture.receipt_path().read_bytes() == before
    assert len(fixture.opens) == 1


def test_legacy_window_refusal_can_resume_without_discarding_original(
    fixture: Fixture,
) -> None:
    rejected_window(fixture)
    path = fixture.receipt_path()
    value = json.loads(path.read_text())
    value.pop("preflight_failure")
    atomic_json(path, value)
    before = path.read_bytes()
    fixture.open(confirm_retry_preflight=True)
    saved = json.loads(path.read_text())
    assert saved["previous_attempts"][0]["receipt_json"].encode() == before


def test_window_refusal_with_uncertain_export_is_not_retryable(
    fixture: Fixture,
) -> None:
    error = UIError(False)
    error.code = "WINDOW_UNAVAILABLE"
    error.candidate_export_uncertain = True
    fixture.ui_error = error
    with pytest.raises(ProjectOpenError):
        fixture.open()
    before = fixture.receipt_path().read_bytes()
    with pytest.raises(ProjectOpenError, match="PREFLIGHT_RETRY_NOT_SAFE"):
        fixture.open(confirm_retry_preflight=True)
    assert fixture.receipt_path().read_bytes() == before and len(fixture.opens) == 1


def test_repeated_preflight_refusal_keeps_each_failure_and_stage(
    fixture: Fixture,
) -> None:
    first = rejected_window(fixture)
    error = UIError(False)
    error.code = "WINDOW_UNAVAILABLE"
    error.diagnostics = {
        "preflight_operation": "filter_candidate",
        "stage": "context_window",
    }
    fixture.ui_error = error
    with pytest.raises(ProjectOpenError, match="filter_candidate/context_window"):
        fixture.open(confirm_retry_preflight=True)
    second = json.loads(fixture.receipt_path().read_text())
    assert second["preflight_failure"]["operation"] == "filter_candidate"
    assert second["preflight_failure"]["project_open_requested"] is False
    fixture.ui_error = None
    fixture.open(confirm_retry_preflight=True)
    saved = json.loads(fixture.receipt_path().read_text())
    assert len(saved["previous_attempts"]) == 2
    assert saved["previous_attempts"][0]["receipt_json"].encode() == first
    snapshot = dict(second)
    snapshot.pop("previous_attempts")
    assert json.loads(saved["previous_attempts"][1]["receipt_json"]) == snapshot


def test_preflight_history_rejects_repeated_uncertain_process(fixture: Fixture) -> None:
    rejected_window(fixture)
    fixture.open(confirm_retry_preflight=True)
    saved = json.loads(fixture.receipt_path().read_text())
    entry = saved["previous_attempts"][0]
    prior = json.loads(entry["receipt_json"])
    prior["status"] = "dispatch_started"
    entry["receipt_json"] = json.dumps(prior)
    entry["sha256"] = hashlib.sha256(entry["receipt_json"].encode()).hexdigest()
    atomic_json(fixture.receipt_path(), saved)
    with pytest.raises(ProjectOpenError, match="HOST_RECEIPT_INVALID"):
        fixture.open(confirm_retry_preflight=True)
    assert len(fixture.opens) == 2


def test_preflight_flag_never_retries_unknown_rejection_or_pending_open(
    fixture: Fixture,
) -> None:
    fixture.ui_error = UIError(False)
    with pytest.raises(ProjectOpenError):
        fixture.open()
    before = fixture.receipt_path().read_bytes()
    with pytest.raises(ProjectOpenError, match="PREFLIGHT_RETRY_NOT_SAFE"):
        fixture.open(confirm_retry_preflight=True)
    assert fixture.receipt_path().read_bytes() == before and len(fixture.opens) == 1


def test_preflight_retry_preserves_exited_process_history(fixture: Fixture) -> None:
    original = interrupted_open(fixture)
    error = UIError(False)
    error.code = "WINDOW_UNAVAILABLE"
    fixture.ui_error = error
    with pytest.raises(ProjectOpenError, match="UI_OPEN_REJECTED"):
        fixture.open(confirm_recover_exited_process=True)
    rejected = json.loads(fixture.receipt_path().read_text())
    fixture.ui_error = None
    fixture.bootstrap = True
    result = fixture.open(confirm_retry_preflight=True)
    saved = json.loads(fixture.receipt_path().read_text())
    assert saved["previous_attempts"][0]["receipt_json"].encode() == original
    snapshot = dict(rejected)
    snapshot.pop("previous_attempts")
    assert json.loads(saved["previous_attempts"][1]["receipt_json"]) == snapshot
    assert result["recovered_after_process_exit"] and result["preflight_retried"]
    assert len(fixture.opens) == 3 and len(fixture.startups) == 1


def test_preflight_retry_does_not_touch_pending_open(fixture: Fixture) -> None:
    interrupted_open(fixture)
    before = fixture.receipt_path().read_bytes()
    with pytest.raises(ProjectOpenError, match="PREFLIGHT_RETRY_REQUIRES_REJECTION"):
        fixture.open(confirm_retry_preflight=True)
    assert fixture.receipt_path().read_bytes() == before and len(fixture.opens) == 1


@pytest.mark.parametrize(
    "flags",
    [
        {"confirm_retry_preflight": 1},
        {"confirm_retry_preflight": True, "confirm_recover_exited_process": True},
    ],
)
def test_preflight_flag_requires_strict_explicit_mode(
    fixture: Fixture, flags: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        fixture.open(**flags)
    assert fixture.opens == []


def rejected_export(fixture: Fixture) -> bytes:
    error = UIError(False)
    error.code = "EXPORT_DIALOG_NOT_OBSERVED"
    error.candidate_export_uncertain = True
    error.diagnostics = {
        "preflight_operation": "export_candidate",
        "stage": "export_wait_dialog",
        "invoke_completed": True,
        "invoke_succeeded": True,
    }
    fixture.ui_error = error
    with pytest.raises(ProjectOpenError, match="UI_OPEN_REJECTED") as result:
        fixture.open()
    assert result.value.preflight_failure is not None
    assert result.value.preflight_failure["dispatch"] == {
        "invoke_completed": True,
        "invoke_succeeded": True,
    }
    fixture.ui_error = None
    return fixture.receipt_path().read_bytes()


def test_export_reconciliation_requires_explicit_consent_and_keeps_failure(
    fixture: Fixture,
) -> None:
    before = rejected_export(fixture)
    with pytest.raises(ProjectOpenError, match="ORIGINAL_OPEN_REJECTED") as replay:
        fixture.open()
    assert replay.value.preflight_failure == json.loads(before)["preflight_failure"]
    with pytest.raises(ProjectOpenError, match="PREFLIGHT_RETRY_NOT_SAFE"):
        fixture.open(confirm_retry_preflight=True)
    assert fixture.receipt_path().read_bytes() == before
    assert fixture.export_inspections == fixture.accepted_opens == 0
    result = fixture.open(
        confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
    )
    assert result["export_preflight_reconciled"] is True
    assert result["native_connection_verified"] and fixture.accepted_opens == 1
    saved = json.loads(fixture.receipt_path().read_text())
    assert saved["schema"] == 4
    assert saved["previous_attempts"][0]["receipt_json"].encode() == before
    assert fixture.export_inspections == 1 and fixture.startups == []
    assert fixture.open(
        confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
    )["replayed"]
    assert fixture.accepted_opens == 1 and fixture.export_inspections == 1


@pytest.mark.parametrize("cause", [
    "PROJECT_SELECTION_UNAVAILABLE", "PROJECT_SELECTION_REQUIRED",
    "PROJECT_SELECTION_UNVERIFIED", "PROJECT_SELECTION_CHANGED",
])
def test_selection_refusal_retains_original_export_history_and_never_replays(
    fixture: Fixture, cause: str
) -> None:
    original = rejected_export(fixture)
    error = UIError(False)
    error.code = cause
    error.diagnostics = {
        "preflight_operation": "export_candidate", "stage": "export_selection"
    }
    fixture.ui_error = error
    with pytest.raises(ProjectOpenError, match=cause) as failure:
        fixture.open(
            confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
        )
    assert failure.value.preflight_failure == {
        "operation": "export_candidate", "stage": "export_selection",
        "project_open_requested": False, "candidate_export_uncertain": False,
        "view_changes_uncertain": False,
    }
    before = fixture.receipt_path().read_bytes()
    saved = json.loads(before)
    assert saved["schema"] == 4
    assert saved["key_hash"] == json.loads(original)["key_hash"]
    assert saved["previous_attempts"][0]["receipt_json"].encode() == original
    with pytest.raises(ProjectOpenError, match="ORIGINAL_OPEN_REJECTED") as replay:
        fixture.open()
    assert replay.value.cause_code == cause
    assert fixture.receipt_path().read_bytes() == before
    assert len(fixture.opens) == 2 and fixture.accepted_opens == 0
    assert fixture.native_calls == [] and fixture.startups == []


@pytest.mark.parametrize(
    "override",
    [
        {"native_visible_handles": [456, 999]},  # Native dialog missing from UIA.
        {"native_visible_handles": []},
        {"windows_truncated": True},
        {"pm_found": False},
        {"windows": [{"handle": 456, "enabled": False, "offscreen": False}]},
        {"windows": [{"handle": 456, "enabled": True, "offscreen": True}]},
        {
            "windows": [
                {"handle": 456, "enabled": True, "offscreen": False},
                {"handle": 999, "enabled": True, "offscreen": False},
            ]
        },
    ],
)
def test_export_reconciliation_never_saves_or_cancels_existing_dialog(
    fixture: Fixture,
    override: dict[str, Any],
) -> None:
    before = rejected_export(fixture)
    fixture.export_inspection_overrides = override
    with pytest.raises(
        ProjectOpenError, match="EXPORT_PREFLIGHT_DIALOG_REQUIRES_RECOVERY"
    ):
        fixture.open(
            confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
        )
    assert fixture.receipt_path().read_bytes() == before
    assert len(fixture.opens) == 1 and fixture.accepted_opens == 0


@pytest.mark.parametrize(
    "state", ["resolve_not_running", "other_project_open", "modal_blocked"]
)
def test_export_reconciliation_never_launches_or_switches_project(
    fixture: Fixture, state: str
) -> None:
    before = rejected_export(fixture)
    fixture.ui_state = state
    with pytest.raises(ProjectOpenError, match="PREFLIGHT_MANAGER_UNVERIFIED"):
        fixture.open(
            confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
        )
    assert fixture.receipt_path().read_bytes() == before
    assert fixture.accepted_opens == fixture.export_inspections == 0
    assert fixture.startups == []


def test_export_probe_failure_is_typed_and_preserves_receipt(fixture: Fixture) -> None:
    before = rejected_export(fixture)
    fixture.export_inspection_error = RuntimeError("Read-only probe unavailable")
    with pytest.raises(
        ProjectOpenError, match="EXPORT_PREFLIGHT_INSPECTION_UNAVAILABLE"
    ):
        fixture.open(
            confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
        )
    assert fixture.receipt_path().read_bytes() == before and fixture.accepted_opens == 0


def test_export_reconciliation_preserves_prior_exit_and_preflight_history(
    fixture: Fixture,
) -> None:
    original = interrupted_open(fixture)
    error = UIError(False)
    error.code = "WINDOW_UNAVAILABLE"
    fixture.ui_error = error
    with pytest.raises(ProjectOpenError, match="UI_OPEN_REJECTED"):
        fixture.open(confirm_recover_exited_process=True)
    second = json.loads(fixture.receipt_path().read_text())
    error.code = "EXPORT_DIALOG_NOT_OBSERVED"
    error.candidate_export_uncertain = True
    error.diagnostics = {
        "preflight_operation": "export_candidate",
        "stage": "export_wait_dialog",
    }
    with pytest.raises(ProjectOpenError, match="UI_OPEN_REJECTED"):
        fixture.open(confirm_retry_preflight=True)
    third = json.loads(fixture.receipt_path().read_text())
    fixture.ui_error = None
    fixture.bootstrap = True
    fixture.open(confirm_retry_preflight=True, confirm_reconcile_export_preflight=True)
    saved = json.loads(fixture.receipt_path().read_text())
    assert saved["schema"] == 4 and len(saved["previous_attempts"]) == 3
    assert saved["previous_attempts"][0]["receipt_json"].encode() == original
    for prior, entry in zip(
        (second, third), saved["previous_attempts"][1:], strict=True
    ):
        assert json.loads(entry["receipt_json"]) == {
            k: v for k, v in prior.items() if k != "previous_attempts"
        }
    assert (
        fixture.accepted_opens == 2
    )  # One original uncertain Open, one recovered Open.


@pytest.mark.parametrize("fault", ["digest", "target", "pending_open", "ack"])
def test_export_history_corruption_blocks_completed_replay(
    fixture: Fixture, fault: str
) -> None:
    rejected_export(fixture)
    fixture.open(confirm_retry_preflight=True, confirm_reconcile_export_preflight=True)
    path = fixture.receipt_path()
    saved = json.loads(path.read_text())
    entry = saved["previous_attempts"][0]
    if fault == "digest":
        entry["sha256"] = "0" * 64
    else:
        prior = json.loads(entry["receipt_json"])
        if fault == "target":
            prior["arguments"]["project_name"] = "Another project"
        elif fault == "pending_open":
            prior["status"] = "dispatch_started"
        else:
            prior["preflight_failure"]["dispatch"]["invoke_completed"] = False
        entry["receipt_json"] = json.dumps(prior)
        entry["sha256"] = hashlib.sha256(entry["receipt_json"].encode()).hexdigest()
    atomic_json(path, saved)
    before = path.read_bytes()
    with pytest.raises(ProjectOpenError, match="HOST_RECEIPT_INVALID"):
        fixture.open()
    assert path.read_bytes() == before
    assert fixture.accepted_opens == fixture.export_inspections == 1


def test_repeated_export_refusals_keep_bounded_flat_history(fixture: Fixture) -> None:
    rejected_export(fixture)
    error = UIError(False)
    error.code = "EXPORT_DIALOG_NOT_OBSERVED"
    error.candidate_export_uncertain = True
    error.diagnostics = {"preflight_operation": "export_candidate"}
    fixture.ui_error = error
    for count in range(1, 9):
        with pytest.raises(ProjectOpenError, match="UI_OPEN_REJECTED"):
            fixture.open(
                confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
            )
        saved = json.loads(fixture.receipt_path().read_text())
        assert saved["schema"] == 4 and len(saved["previous_attempts"]) == count
        assert all(
            "previous_attempts" not in json.loads(entry["receipt_json"])
            for entry in saved["previous_attempts"]
        )
    before = fixture.receipt_path().read_bytes()
    attempts = len(fixture.opens)
    with pytest.raises(ProjectOpenError, match="RECOVERY_HISTORY_LIMIT"):
        fixture.open(
            confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
        )
    assert fixture.receipt_path().read_bytes() == before
    assert len(fixture.opens) == attempts and fixture.accepted_opens == 0


@pytest.mark.parametrize(
    "corrupt", ["open_requested", "native_binding", "operation", "cause"]
)
def test_export_reconciliation_refuses_missing_no_open_evidence(
    fixture: Fixture, corrupt: str
) -> None:
    rejected_export(fixture)
    value = json.loads(fixture.receipt_path().read_text())
    if corrupt == "open_requested":
        value["preflight_failure"]["project_open_requested"] = True
    elif corrupt == "native_binding":
        value["native_binding"] = {
            "runtime": "other",
            "session": "other",
            "generation": "other",
        }
    elif corrupt == "operation":
        value["preflight_failure"]["operation"] = "open_existing"
    else:
        value["cause_code"] = value["error"] = "UNKNOWN_FAILURE"
    atomic_json(fixture.receipt_path(), value)
    before = fixture.receipt_path().read_bytes()
    with pytest.raises(ProjectOpenError):
        fixture.open(
            confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
        )
    assert fixture.receipt_path().read_bytes() == before
    assert fixture.accepted_opens == fixture.export_inspections == 0


@pytest.mark.parametrize(
    "flags",
    [
        {"confirm_reconcile_export_preflight": 1},
        {"confirm_reconcile_export_preflight": True},
        {
            "confirm_reconcile_export_preflight": True,
            "confirm_retry_preflight": True,
            "confirm_recover_exited_process": True,
        },
    ],
)
def test_export_reconciliation_flags_are_strict(
    fixture: Fixture, flags: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        fixture.open(**flags)
    assert fixture.opens == [] and fixture.export_inspections == 0


def test_export_flag_never_rearms_pending_native_open(fixture: Fixture) -> None:
    interrupted_open(fixture)
    before = fixture.receipt_path().read_bytes()
    with pytest.raises(ProjectOpenError, match="PREFLIGHT_RETRY_REQUIRES_REJECTION"):
        fixture.open(
            confirm_retry_preflight=True, confirm_reconcile_export_preflight=True
        )
    assert fixture.receipt_path().read_bytes() == before
    assert fixture.export_inspections == 0 and len(fixture.opens) == 1


def test_incomplete_invoke_is_never_reported_as_success(fixture: Fixture) -> None:
    error = UIError(False)
    error.diagnostics = {"invoke_completed": False, "invoke_succeeded": True}
    fixture.ui_error = error
    with pytest.raises(ProjectOpenError):
        fixture.open()
    failure = json.loads(fixture.receipt_path().read_text())["preflight_failure"]
    assert failure["dispatch"] == {"invoke_completed": False, "invoke_succeeded": False}


def test_opens_once_then_verifies_exact_native_context(fixture: Fixture) -> None:
    result = fixture.open()
    assert fixture.opens == [
        {
            "library_name": DATABASE["DbName"],
            "folder_path": [],
            "project_name": PROJECT["name"],
            "expected_project_id": PROJECT["id"],
            "expected_database": DATABASE,
            "timeout_seconds": 1,
        }
    ]
    assert len(fixture.native_calls) == 1
    assert fixture.native_calls[0]["action"] == "verify_project_context"
    assert fixture.native_calls[0]["expected_project_id"] == PROJECT["id"]
    assert fixture.native_calls[0]["arguments"] == {
        "expected_database": DATABASE,
        "expected_folder": "",
        "expected_project_name": PROJECT["name"],
    }
    assert result["context_verified"] and result["folder_membership_verified"]
    assert result["ui_open_dispatched"] and not result["no_op"]
    assert result["proof_scope"] == "fresh_native_context"
    assert pending_project_open(fixture.profile)["pending"] is False


@pytest.mark.parametrize(
    "error", [UIError(False), OSError("unavailable"), ValueError("invalid")]
)
def test_inspection_failure_never_creates_open_intent(
    fixture: Fixture,
    error: Exception,
) -> None:
    def inspect() -> dict[str, Any]:
        raise error

    fixture.inspect = inspect  # type: ignore[method-assign]
    with pytest.raises(
        ProjectOpenError, match="PROJECT_MANAGER_INSPECTION_UNAVAILABLE"
    ) as caught:
        fixture.open()
    assert caught.value.__cause__ is error
    assert caught.value.uncertain is False
    assert not fixture.receipt_path().exists()
    assert not fixture.opens and not fixture.native_calls
    assert pending_project_open(fixture.profile)["pending"] is False


def test_expired_preflight_does_not_dispatch_ui(fixture: Fixture) -> None:
    def inspect() -> dict[str, Any]:
        fixture.now += 2
        return {
            "state": "project_manager",
            "eligible": True,
            "process": dict(fixture.process),
        }

    fixture.inspect = inspect  # type: ignore[method-assign]
    with pytest.raises(ProjectOpenError, match="OPEN_DEADLINE_BEFORE_DISPATCH"):
        fixture.open()
    assert fixture.opens == []
    assert not fixture.receipt_path().exists()


def test_non_disk_ui_target_refuses_before_dispatch(fixture: Fixture) -> None:
    with pytest.raises(ProjectOpenError, match="UI_LIBRARY_TYPE_UNSUPPORTED"):
        fixture.open(
            expected_database={
                "DbType": "PostgreSQL",
                "DbName": "Remote Library",
                "IpAddress": "localhost",
            }
        )
    assert fixture.opens == []
    assert not fixture.receipt_path().exists()


def test_matching_already_connected_project_is_fresh_no_op(fixture: Fixture) -> None:
    fixture.connect()
    fixture.ui_state = "other_project_open"
    result = fixture.open()
    assert result["no_op"] is True and result["ui_open_dispatched"] is False
    assert not fixture.opens and len(fixture.native_calls) == 1


def test_stale_predecessor_locator_waits_for_current_process_bootstrap(
    fixture: Fixture,
) -> None:
    fixture.connect()
    fixture.process["pid"] = 999
    result = fixture.open()
    assert result["context_verified"] is True
    assert len(fixture.opens) == 1 and fixture.ticks == 1


def test_connected_without_context_capability_refuses_ui(fixture: Fixture) -> None:
    fixture.connect()
    fixture.capabilities.clear()
    with pytest.raises(ProjectOpenError, match="ACTIVE_CONTEXT_UNVERIFIED"):
        fixture.open()
    assert not fixture.opens and not fixture.native_calls


def test_foreign_connected_project_refuses_before_ui(fixture: Fixture) -> None:
    fixture.connect()
    fixture.native_override["project"] = {"id": "foreign", "name": "Other"}
    with pytest.raises(ProjectOpenError, match="ACTIVE_CONTEXT_UNVERIFIED"):
        fixture.open()
    assert not fixture.opens
    assert json.loads(fixture.receipt_path().read_text())["status"] == "rejected"


@pytest.mark.parametrize(
    "changed",
    [
        {"project": {"id": "wrong", "name": PROJECT["name"]}},
        {"database": {"DbType": "Disk", "DbName": "Other"}},
        {"folder": "Nested"},
        {"folder_membership_verified": False},
        {"context_verified": False},
        {"source": "window_caption"},
        {"response_path": None},
    ],
)
def test_wrong_native_context_leaves_original_open_pending(
    fixture: Fixture,
    changed: dict[str, Any],
) -> None:
    fixture.native_override.update(changed)
    with pytest.raises(ProjectOpenError, match="NATIVE_CONTEXT_UNVERIFIED"):
        fixture.open()
    assert len(fixture.opens) == 1
    assert pending_project_open(fixture.profile)["pending"] is True
    with pytest.raises(ProjectOpenError, match="NATIVE_CONTEXT_UNVERIFIED"):
        fixture.open()
    assert len(fixture.opens) == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"confirm": False},
        {"folder_path": ["Nested"]},
        {"folder_path": ""},
        {"timeout_seconds": True},
        {"timeout_seconds": float("inf")},
        {"expected_database": {"DbType": "Disk", "DbName": "Local", "IpAddress": "x"}},
    ],
)
def test_invalid_intent_never_inspects_or_opens(
    fixture: Fixture, arguments: dict[str, Any]
) -> None:
    with pytest.raises((ValueError, ProjectOpenError)):
        fixture.open(**arguments)
    assert not fixture.inspections and not fixture.opens and not fixture.native_calls


def test_completed_retry_is_historical_even_after_process_generation_change(
    fixture: Fixture,
) -> None:
    original = fixture.open()
    fixture.process["pid"] = 999
    fixture.generation = "c" * 32
    fixture.connect()
    inspections, calls = fixture.inspections, len(fixture.native_calls)
    replay = fixture.open()
    assert replay["replayed"] is True
    assert replay["proof_scope"] == "historical_completed_receipt"
    assert replay["native_connection_verified"] is False
    assert replay["ui_open_dispatched"] is False
    assert replay["generation"] == original["generation"]
    assert fixture.inspections == inspections and len(fixture.native_calls) == calls
    assert len(fixture.opens) == 1


def test_same_key_changed_target_is_rejected(fixture: Fixture) -> None:
    fixture.open()
    with pytest.raises(ValueError, match="different project Open"):
        fixture.open(project_name="Different")
    assert len(fixture.opens) == 1


def test_uncertain_ui_reply_is_recovered_without_reopening(fixture: Fixture) -> None:
    fixture.ui_error = UIError(uncertain=True)
    result = fixture.open()
    assert result["context_verified"] is True and len(fixture.opens) == 1


def test_pending_same_key_only_waits_and_reads(fixture: Fixture) -> None:
    fixture.ui_error = UIError(uncertain=True)
    fixture.bootstrap = False
    with pytest.raises(ProjectOpenError, match="OPEN_OUTCOME_UNVERIFIED"):
        fixture.open()
    assert len(fixture.opens) == 1
    fixture.bootstrap = True
    result = fixture.open()
    assert result["status"] == "completed" and len(fixture.opens) == 1


def test_pending_different_key_is_refused(fixture: Fixture) -> None:
    fixture.bootstrap = False
    with pytest.raises(ProjectOpenError):
        fixture.open()
    inspections = fixture.inspections
    with pytest.raises(ProjectOpenError, match="ANOTHER_PROJECT_OPEN_PENDING"):
        fixture.open(idempotency_key="other-key")
    assert fixture.inspections == inspections and len(fixture.opens) == 1


def test_pending_original_process_gone_requires_recovery(fixture: Fixture) -> None:
    fixture.bootstrap = False
    with pytest.raises(ProjectOpenError):
        fixture.open()
    fixture.process["creation_time"] = "638999000000000001"
    with pytest.raises(ProjectOpenError, match="ORIGINAL_PROCESS_CHANGED"):
        fixture.open()
    assert len(fixture.opens) == 1 and not fixture.native_calls


@pytest.mark.parametrize("replacement", [False, True])
def test_interrupted_open_keeps_original_key_after_hidden_process_exit(
    fixture: Fixture,
    replacement: bool,
) -> None:
    fixture.bootstrap = False
    key = "api-laws-uk-open-20261106"
    with pytest.raises(ProjectOpenError, match="OPEN_OUTCOME_UNVERIFIED"):
        fixture.open(idempotency_key=key)
    path = fixture.receipt_path(key)
    before = path.read_bytes()
    fixture.ui_state = "resolve_not_running"
    code = "ORIGINAL_PROCESS_EXITED"
    if replacement:
        # Even a reused PID and matching caption cannot rebind the old Open.
        fixture.ui_state = "project_manager"
        fixture.process["creation_time"] = "638999000000000001"
        code = "ORIGINAL_PROCESS_CHANGED"
    with pytest.raises(ProjectOpenError, match=code):
        fixture.open(idempotency_key=key)
    assert path.read_bytes() == before
    assert list(path.parent.glob("*.json")) == [path]
    assert len(fixture.opens) == 1 and not fixture.native_calls


def interrupted_open(fixture: Fixture) -> bytes:
    fixture.bootstrap = False
    with pytest.raises(ProjectOpenError, match="OPEN_OUTCOME_UNVERIFIED"):
        fixture.open()
    before = fixture.receipt_path().read_bytes()
    fixture.ui_state = "resolve_not_running"
    return before


@pytest.mark.parametrize("restored", [False, True])
def test_explicit_exit_recovery_keeps_history_and_replays_without_duplicate(
    fixture: Fixture,
    restored: bool,
) -> None:
    original_bytes = interrupted_open(fixture)
    original = json.loads(original_bytes)
    fixture.restore_target = restored
    fixture.bootstrap = True
    result = fixture.open(confirm_recover_exited_process=True)
    assert result["context_verified"] is True
    assert result["recovered_after_process_exit"] is True
    assert result["no_op"] is restored
    assert result["ui_open_dispatched"] is (not restored)
    assert fixture.startups == [PROJECT["name"]]
    assert len(fixture.opens) == (1 if restored else 2)
    saved = json.loads(fixture.receipt_path().read_text())
    assert saved["schema"] == 2 and saved["key_hash"] == original["key_hash"]
    assert saved["arguments"] == original["arguments"]
    (previous,) = saved["previous_attempts"]
    assert json.loads(previous["receipt_json"]) == original
    assert previous["receipt_json"].encode() == original_bytes
    assert (
        hashlib.sha256(previous["receipt_json"].encode()).hexdigest()
        == previous["sha256"]
    )
    replay = fixture.open(confirm_recover_exited_process=True)
    assert replay["replayed"] is True and replay["ui_open_dispatched"] is False
    assert replay["native_connection_verified"] is False
    assert len(fixture.opens) == (1 if restored else 2)
    assert list(fixture.receipt_path().parent.glob("*.json")) == [
        fixture.receipt_path()
    ]


def test_recovery_flag_does_not_repeat_open_while_original_process_lives(
    fixture: Fixture,
) -> None:
    interrupted_open(fixture)
    fixture.ui_state = "project_manager"
    fixture.bootstrap = True
    result = fixture.open(confirm_recover_exited_process=True)
    assert result["context_verified"] is True
    assert "recovered_after_process_exit" not in result
    assert fixture.startups == [] and len(fixture.opens) == 1


def test_exit_recovery_preserves_original_receipt_bom_and_whitespace(
    fixture: Fixture,
) -> None:
    interrupted_open(fixture)
    path = fixture.receipt_path()
    original = b"\xef\xbb\xbf\n" + path.read_bytes() + b"\n"
    path.write_bytes(original)
    fixture.bootstrap = True
    fixture.open(confirm_recover_exited_process=True)
    saved = json.loads(path.read_text(encoding="utf-8"))
    previous = saved["previous_attempts"][0]
    assert previous["receipt_json"].encode() == original
    assert previous["sha256"] == hashlib.sha256(original).hexdigest()


def test_exit_recovery_never_waits_for_connection_lock_while_holding_operation(
    fixture: Fixture,
) -> None:
    before = interrupted_open(fixture)
    with (
        _lock(fixture.profile, "connection.lock"),
        pytest.raises(ProjectOpenError, match="PROJECT_STARTUP_BUSY"),
    ):
        fixture.open(confirm_recover_exited_process=True)
    assert fixture.receipt_path().read_bytes() == before
    assert not fixture.startups and len(fixture.opens) == 1


def test_recovery_failure_before_open_keeps_original_receipt(fixture: Fixture) -> None:
    before = interrupted_open(fixture)
    fixture.startup_error = RuntimeError("STARTUP_REFUSED")
    with pytest.raises(RuntimeError, match="STARTUP_REFUSED"):
        fixture.open(confirm_recover_exited_process=True)
    assert fixture.receipt_path().read_bytes() == before
    assert len(fixture.opens) == 1 and not fixture.native_calls


def test_recovery_history_limit_refuses_before_startup(fixture: Fixture) -> None:
    interrupted_open(fixture)
    with pytest.raises(ProjectOpenError):
        fixture.open(confirm_recover_exited_process=True)
    path = fixture.receipt_path()
    receipt = json.loads(path.read_text())
    prior = json.loads(receipt["previous_attempts"][0]["receipt_json"])
    receipt["previous_attempts"] = []
    for number in range(8):
        prior["process"]["creation_time"] = str(638999000000000000 + number)
        data = json.dumps(prior)
        receipt["previous_attempts"].append(
            {
                "receipt_json": data,
                "sha256": hashlib.sha256(data.encode()).hexdigest(),
            }
        )
    receipt["process"]["creation_time"] = "638999000000000009"
    fixture.process["creation_time"] = receipt["process"]["creation_time"]
    fixture.ui_state = "resolve_not_running"
    atomic_json(path, receipt)
    before = path.read_bytes()
    with pytest.raises(ProjectOpenError, match="RECOVERY_HISTORY_LIMIT"):
        fixture.open(confirm_recover_exited_process=True)
    assert path.read_bytes() == before
    assert len(fixture.startups) == 1 and len(fixture.opens) == 2


def test_second_uncertain_open_is_not_repeated_by_the_recovery_flag(
    fixture: Fixture,
) -> None:
    interrupted_open(fixture)
    with pytest.raises(ProjectOpenError, match="OPEN_OUTCOME_UNVERIFIED"):
        fixture.open(confirm_recover_exited_process=True)
    before = fixture.receipt_path().read_bytes()
    with pytest.raises(ProjectOpenError, match="OPEN_OUTCOME_UNVERIFIED"):
        fixture.open(confirm_recover_exited_process=True)
    assert fixture.receipt_path().read_bytes() == before
    assert len(fixture.opens) == 2 and len(fixture.startups) == 1


def test_a_later_confirmed_exit_appends_history_without_nesting(
    fixture: Fixture,
) -> None:
    original = interrupted_open(fixture)
    with pytest.raises(ProjectOpenError, match="OPEN_OUTCOME_UNVERIFIED"):
        fixture.open(confirm_recover_exited_process=True)
    fixture.ui_state = "resolve_not_running"
    fixture.bootstrap = True
    result = fixture.open(confirm_recover_exited_process=True)
    assert result["previous_attempt_count"] == 2
    saved = json.loads(fixture.receipt_path().read_text())
    previous = saved["previous_attempts"]
    assert previous[0]["receipt_json"].encode() == original
    assert "previous_attempts" not in json.loads(previous[1]["receipt_json"])
    assert len(fixture.opens) == 3 and len(fixture.startups) == 2


def test_recovery_requires_original_receipt_and_a_strict_boolean(
    fixture: Fixture,
) -> None:
    with pytest.raises(ProjectOpenError, match="RECOVERY_REQUIRES_ORIGINAL_RECEIPT"):
        fixture.open(confirm_recover_exited_process=True)
    with pytest.raises(ValueError, match="must be a boolean"):
        fixture.open(confirm_recover_exited_process=1)
    assert not fixture.opens and not fixture.startups and not fixture.inspections


@pytest.mark.parametrize("fault", ["digest", "target"])
def test_corrupt_recovery_history_blocks_replay(fixture: Fixture, fault: str) -> None:
    interrupted_open(fixture)
    fixture.bootstrap = True
    fixture.open(confirm_recover_exited_process=True)
    path = fixture.receipt_path()
    receipt = json.loads(path.read_text())
    entry = receipt["previous_attempts"][0]
    if fault == "digest":
        entry["sha256"] = "0" * 64
    else:
        prior = json.loads(entry["receipt_json"])
        prior["arguments"]["project_name"] = "Another project"
        entry["receipt_json"] = json.dumps(prior)
        entry["sha256"] = hashlib.sha256(entry["receipt_json"].encode()).hexdigest()
    atomic_json(path, receipt)
    with pytest.raises(ProjectOpenError, match="HOST_RECEIPT_INVALID"):
        fixture.open(confirm_recover_exited_process=True)
    assert len(fixture.opens) == 2 and len(fixture.startups) == 1


def test_recovery_still_requires_fresh_matching_native_uuid(fixture: Fixture) -> None:
    interrupted_open(fixture)
    fixture.bootstrap = True
    fixture.native_override = {"project": {**PROJECT, "id": "wrong-project"}}
    with pytest.raises(ProjectOpenError, match="NATIVE_CONTEXT_UNVERIFIED"):
        fixture.open(confirm_recover_exited_process=True)
    assert pending_project_open(fixture.profile)["pending"] is True
    assert len(fixture.opens) == 2


def test_recovery_flag_cannot_bypass_another_key_or_native_owner(
    fixture: Fixture,
) -> None:
    before = interrupted_open(fixture)
    with pytest.raises(ProjectOpenError, match="ANOTHER_PROJECT_OPEN_PENDING"):
        fixture.open(confirm_recover_exited_process=True, idempotency_key="another-key")
    owner = fixture.state / "write-owners" / fixture.receipt_path().name
    owner.parent.mkdir()
    owner.write_text("{}")
    with pytest.raises(ProjectOpenError, match="IDEMPOTENCY_KEY_OWNED_BY_NATIVE_WRITE"):
        fixture.open(confirm_recover_exited_process=True)
    assert fixture.receipt_path().read_bytes() == before
    assert not fixture.startups and len(fixture.opens) == 1


def test_pending_original_generation_cannot_migrate(fixture: Fixture) -> None:
    fixture.native_error = LuaCommandTimeoutError("d" * 32, 0.1)
    with pytest.raises(ProjectOpenError, match="OPEN_OUTCOME_UNVERIFIED"):
        fixture.open()
    fixture.native_error = None
    fixture.generation = "c" * 32
    fixture.connect()
    with pytest.raises(ProjectOpenError, match="ORIGINAL_GENERATION_CHANGED"):
        fixture.open()
    assert len(fixture.opens) == 1


def test_definite_ui_refusal_is_terminal_for_same_key(fixture: Fixture) -> None:
    fixture.ui_error = UIError(uncertain=False)
    with pytest.raises(ProjectOpenError, match="UI_OPEN_REJECTED") as initial:
        fixture.open()
    assert initial.value.cause_code == "SYNTHETIC_UI_FAILURE"
    receipt = json.loads(fixture.receipt_path().read_text())
    assert receipt["cause_code"] == "SYNTHETIC_UI_FAILURE"
    fixture.ui_error = None
    with pytest.raises(ProjectOpenError, match="ORIGINAL_OPEN_REJECTED") as replay:
        fixture.open()
    assert replay.value.cause_code == "SYNTHETIC_UI_FAILURE"
    assert len(fixture.opens) == 1
    assert pending_project_open(fixture.profile)["pending"] is False


def test_legacy_rejected_receipt_keeps_known_error_without_new_open(
    fixture: Fixture,
) -> None:
    fixture.ui_error = UIError(uncertain=False)
    with pytest.raises(ProjectOpenError):
        fixture.open()
    path = fixture.receipt_path()
    record = json.loads(path.read_text())
    record.pop("cause_code")
    atomic_json(path, record)
    with pytest.raises(ProjectOpenError) as replay:
        fixture.open()
    assert replay.value.cause_code == "SYNTHETIC_UI_FAILURE"
    assert len(fixture.opens) == 1


def test_watcher_lock_after_ui_dispatch_does_not_escape_wait_flow(
    fixture: Fixture,
) -> None:
    fixture.lock_after_open = True
    result = fixture.open()
    assert result["context_verified"] is True and len(fixture.opens) == 1
    assert pending_project_open(fixture.profile)["pending"] is False


def test_definite_refusal_waits_for_watcher_lock_before_terminal_receipt(
    fixture: Fixture,
) -> None:
    fixture.lock_after_open = True
    fixture.ui_error = UIError(uncertain=False)
    with pytest.raises(ProjectOpenError, match="UI_OPEN_REJECTED"):
        fixture.open()
    assert len(fixture.opens) == 1
    assert json.loads(fixture.receipt_path().read_text())["status"] == "rejected"
    assert pending_project_open(fixture.profile)["pending"] is False


@pytest.mark.parametrize("where", ["owner", "receipt"])
def test_native_key_namespace_cannot_be_reused(fixture: Fixture, where: str) -> None:
    digest = fixture.receipt_path().stem
    folder = (
        fixture.state / "write-owners"
        if where == "owner"
        else fixture.root / "receipts"
    )
    folder.mkdir(exist_ok=True)
    (folder / f"{digest}.json").write_text("{}")
    with pytest.raises(ProjectOpenError, match="IDEMPOTENCY_KEY_OWNED_BY_NATIVE_WRITE"):
        fixture.open()
    assert not fixture.inspections and not fixture.opens


def test_native_pending_operation_blocks_project_open(fixture: Fixture) -> None:
    folder = fixture.root / "receipts"
    folder.mkdir()
    (folder / ("f" * 64 + ".json")).write_text(json.dumps({"status": "pending"}))
    with pytest.raises(ProjectOpenError, match="NATIVE_OPERATION_REQUIRES_RECOVERY"):
        fixture.open()
    assert not fixture.opens


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"status": "unknown"},
        {"status": "completed", "result": {}},
        {"status": "rejected", "error": "known"},
        {
            "status": "completed",
            "fingerprint": "a" * 64,
            "command_id": "b" * 32,
            "result": "missing typed outcome",
        },
    ],
    ids=["empty", "unknown", "missing-fingerprint", "missing-command", "bad-result"],
)
def test_malformed_or_unknown_native_outcome_blocks_before_ui(
    fixture: Fixture,
    value: dict[str, Any],
) -> None:
    folder = fixture.root / "receipts"
    folder.mkdir()
    (folder / ("f" * 64 + ".json")).write_text(json.dumps(value))
    with pytest.raises(ProjectOpenError, match="NATIVE_OPERATION_REQUIRES_RECOVERY"):
        fixture.open()
    assert not fixture.inspections and not fixture.opens


def test_known_terminal_native_receipt_does_not_block_new_open(
    fixture: Fixture,
) -> None:
    folder = fixture.root / "receipts"
    folder.mkdir()
    (folder / ("f" * 64 + ".json")).write_text(
        json.dumps(
            {
                "status": "completed",
                "fingerprint": "a" * 64,
                "command_id": "b" * 32,
                "result": {},
            }
        )
    )
    assert fixture.open()["context_verified"] is True
    assert len(fixture.opens) == 1


@pytest.mark.parametrize(
    "data", [b"broken-json", b"x" * 65537], ids=["malformed", "oversized"]
)
def test_pending_helper_rejects_malformed_or_oversized_receipt(
    fixture: Fixture, data: bytes
) -> None:
    path = fixture.receipt_path()
    path.parent.mkdir()
    path.write_bytes(data)
    with pytest.raises(ProjectOpenError, match="HOST_RECEIPT_INVALID"):
        pending_project_open(fixture.profile)
    with pytest.raises(ProjectOpenError):
        fixture.open()
    assert not fixture.opens


def test_durable_project_open_lock_is_released_by_context(fixture: Fixture) -> None:
    with project_open_lock(fixture.profile), pytest.raises(OperationBusy):
        fixture.open()
    assert not fixture.opens
    fixture.open()
    assert len(fixture.opens) == 1


def test_nested_unexpected_receipt_entry_fails_closed(fixture: Fixture) -> None:
    folder = fixture.receipt_path().parent
    folder.mkdir()
    (folder / "foreign").mkdir()
    with pytest.raises(ProjectOpenError, match="HOST_RECEIPT_DIRECTORY_INVALID"):
        pending_project_open(fixture.profile)


def test_completed_receipt_mismatched_result_is_not_replayed(fixture: Fixture) -> None:
    fixture.open()
    path = fixture.receipt_path()
    saved = json.loads(path.read_text())
    saved["result"]["project"]["id"] = "foreign"
    path.write_text(json.dumps(saved))
    with pytest.raises(ProjectOpenError, match="HOST_RECEIPT_INVALID"):
        fixture.open()
    assert len(fixture.opens) == 1


def test_waits_for_watcher_lock_without_repeating_open(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.held_lock = operation_lock(fixture.profile)
    fixture.held_lock.__enter__()
    result = fixture.open()
    assert result["status"] == "completed"
    assert len(fixture.opens) == 1


def test_operation_wait_is_bounded(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    with (
        operation_lock(fixture.profile),
        pytest.raises(ProjectOpenError, match="HOST_OPERATION_BUSY"),
    ):
        fixture.open()
    assert fixture.opens == []
    assert fixture.now == pytest.approx(101.0)


def test_operation_body_is_never_retried(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    attempts = 0
    with pytest.raises(OperationBusy), fixture.service()._operation_until(101.0):
        attempts += 1
        raise OperationBusy("operation.lock")
    assert attempts == 1


@pytest.mark.parametrize("historical", [False, True])
def test_only_fresh_verified_open_releases_closed_project_hold(
    fixture: Fixture,
    historical: bool,
) -> None:
    from uuid import uuid4

    from providers.resolve.project_close_state import (
        close_intent,
        finish_close,
        prepare_close,
    )

    if historical:
        fixture.open()
    identity = str(uuid4())
    prepare_close(fixture.profile, identity, "a" * 64)
    finish_close(fixture.profile, identity, closed=True)
    fixture.open()
    assert (close_intent(fixture.profile) is not None) is historical


def test_open_refuses_pending_close_before_inspection_or_dispatch(
    fixture: Fixture,
) -> None:
    from uuid import uuid4

    from providers.resolve.project_close_state import prepare_close

    prepare_close(fixture.profile, str(uuid4()), "a" * 64)
    with pytest.raises(ProjectOpenError, match="PROJECT_CLOSE_REQUIRES_RECOVERY"):
        fixture.open()
    assert not fixture.inspections and not fixture.opens and not fixture.native_calls
