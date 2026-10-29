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
        self.ui_state = "other_project_open"
        return {"state": "open_requested", "dispatch_started": True}

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
    fixture: Fixture, error: Exception,
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
        return {"state": "project_manager", "eligible": True,
                "process": dict(fixture.process)}

    fixture.inspect = inspect  # type: ignore[method-assign]
    with pytest.raises(ProjectOpenError, match="OPEN_DEADLINE_BEFORE_DISPATCH"):
        fixture.open()
    assert fixture.opens == []
    assert not fixture.receipt_path().exists()


def test_non_disk_ui_target_refuses_before_dispatch(fixture: Fixture) -> None:
    with pytest.raises(ProjectOpenError, match="UI_LIBRARY_TYPE_UNSUPPORTED"):
        fixture.open(expected_database={
            "DbType": "PostgreSQL", "DbName": "Remote Library", "IpAddress": "localhost"
        })
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
    with pytest.raises(ProjectOpenError, match="UI_OPEN_REJECTED"):
        fixture.open()
    fixture.ui_error = None
    with pytest.raises(ProjectOpenError, match="ORIGINAL_OPEN_REJECTED"):
        fixture.open()
    assert len(fixture.opens) == 1
    assert pending_project_open(fixture.profile)["pending"] is False


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
