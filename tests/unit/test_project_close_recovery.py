"""Original-close readback recovery never submits another native mutation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from test_project_close import arguments, empty_placeholder
from test_sdk_command_client import queued, setup_worker

import providers.resolve.project_close_recovery as module
from bridges.resolve import ResolveBridge as bridge
from providers.resolve.lua_editing import atomic_json
from providers.resolve.project_close_state import close_intent, prepare_close


def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    worker, native, active = setup_worker(tmp_path, monkeypatch)
    path = queued(worker, "close_project", arguments(native), "original-close")
    command = json.loads(path.read_text(encoding="utf-8"))
    identity = command["command_id"]
    fingerprint = bridge._request_fingerprint(command)
    key = hashlib.sha256(b"original-close").hexdigest()
    owner_path = worker.profile.state_directory / "sdk-write-owners" / (key + ".json")
    owner_path.parent.mkdir(parents=True)
    owner = dict(command_id=identity, fingerprint=fingerprint, phase="uncertain",
        runtime=active["runtime"], session=active["session"],
        generation=active["generation"], transport="lua_marker_preset")
    atomic_json(owner_path, owner)
    ledger_path = worker.root / "state/sdk-operations" / (key + ".json")
    ledger_path.parent.mkdir(parents=True)
    ledger = dict(command_id=identity, fingerprint=fingerprint,
        phase="dispatch_started", session=active["session"])
    atomic_json(ledger_path, ledger)
    backup = worker.root / "backups/original.drp"
    backup.write_bytes(b"native backup fixture")
    response_path = worker.root / "responses" / path.name
    failed = dict(protocol_version="1.0", command_id=identity, status="error",
        started_at=command["created_at"], finished_at=command["created_at"],
        result=None, warnings=[], error=dict(code="PROJECT_CLOSE_READBACK_FAILED",
            message="Closure could not be verified", retryable=False,
            details=dict(backup_path=str(backup))))
    atomic_json(response_path, failed)
    path.replace(worker.root / "failed" / path.name)
    prepare_close(worker.profile, identity, fingerprint)
    trace_root = Path(active["runtime"]) / "sdk-trace"
    trace_root.mkdir()
    for method in ("SaveProject", "ExportProject", "CloseProject"):
        args: list[Any] = []
        if method == "ExportProject":
            args = [native.project.GetName(), str(backup), False]
        elif method == "CloseProject":
            args = [{"r": "target"}]
        trace = dict(
            session=active["session"], phase="completed",
            operation=dict(id=UUID(identity).hex, action="close_project",
                fingerprint=fingerprint),
            call=dict(kind="call", method=method, arguments={"a": args}),
            response=dict(error=None, result=True, objects={"target": {"cache": [
                dict(method="GetName", result=native.project.GetName()),
                dict(method="GetUniqueId", result=native.project.GetUniqueId()),
            ]}}),
        )
        request = json.dumps({
            "operation": trace["operation"], "call": trace["call"]
        }).encode()
        trace["request_sha256"] = hashlib.sha256(request).hexdigest()
        trace["response"]["request_hex"] = request.hex()
        atomic_json(trace_root / (method + ".json"), trace)
    placeholder = empty_placeholder()
    monkeypatch.setattr(native.project_manager, "GetCurrentProject",
        lambda: placeholder)
    monkeypatch.setattr(native.project_manager, "GetProjectListInCurrentFolder",
        lambda: [native.project.GetName()], raising=False)
    monkeypatch.setattr(module, "read_active", lambda profile: active)
    reads: list[dict[str, Any]] = []

    def read(root: Path, session: str, operation: dict[str, Any]) -> Any:
        reads.append(operation)
        return native

    monkeypatch.setattr(module, "_native", read)
    return worker, owner_path, owner, ledger_path, response_path, placeholder, reads


def test_original_key_recovers_acknowledged_close_and_replays_without_native_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, path, owner, ledger, response, placeholder, reads = fixture(
        tmp_path, monkeypatch)
    expected = json.loads(
        (worker.root / "failed" / (owner["command_id"] + ".json")).read_text())
    args = expected["arguments"]
    worker.process(queued(worker, "close_project", args, "original-close"))
    assert len(reads) == 1 and reads[0]["action"] == "get_capabilities"
    assert reads[0]["create_backup"] is False
    intent = close_intent(worker.profile)
    assert intent is not None and intent["phase"] == "closed"
    assert json.loads(path.read_text())["phase"] == "completed"
    assert json.loads(ledger.read_text())["phase"] == "completed"
    assert json.loads(response.read_text())["result"]["recovered"] is True
    archive = next((worker.root / "state/close-reconciliations").glob("*.json"))
    archived = json.loads(archive.read_text())
    assert archived["original_error_response"]["status"] == "error"
    worker.process(queued(worker, "close_project", args, "original-close"))
    assert len(reads) == 1


@pytest.mark.parametrize("fault", [None, "unavailable", "running", "race", "ack"])
def test_exited_original_session_recovers_only_with_ack_and_two_absence_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str | None,
) -> None:
    import providers.resolve.windows_bootstrap as bootstrap

    worker, path, owner, ledger, response, _, reads = fixture(tmp_path, monkeypatch)
    active = module.read_active(worker.profile)
    assert active is not None
    executable = "C:/Program Files/Blackmagic Design/DaVinci Resolve/Resolve.exe"
    monkeypatch.setattr(bootstrap, "_official_executable", lambda: executable)
    active.update(phase="resolve_absent", process={
        "pid": 123, "creation_time": "638999000000000000",
        "executable_path": executable, "window_handle": 456,
        "window_title": "DaVinci Resolve - New Project", "window_state": "normal",
        "project_name_hint": "New Project",
    })
    observations: list[dict[str, Any]] = []

    def host() -> dict[str, Any]:
        value: dict[str, Any] = dict(status="observed", running=False,
            source="windows_process_metadata", processes=[])
        if len(observations) == 1:
            if fault == "unavailable":
                value.update(status="unavailable", running=None)
            elif fault == "running":
                value.update(running=True, processes=[{"pid": 999}])
            elif fault == "race":
                active["generation"] = "changed"
        observations.append(value)
        return value

    monkeypatch.setattr(module, "inspect_windows_host", host)
    monkeypatch.setattr(module, "_native", lambda *args: pytest.fail("Native call"))
    if fault == "ack":
        (Path(owner["runtime"]) / "sdk-trace/CloseProject.json").unlink()
    before = {p: p.read_bytes() for p in (path, ledger, response)}
    if fault:
        with pytest.raises(RuntimeError):
            module.recover_acknowledged_close(worker.profile, path, owner)
        assert all(p.read_bytes() == data for p, data in before.items())
        assert close_intent(worker.profile)["phase"] == "pending"
    else:
        recovered = module.recover_acknowledged_close(worker.profile, path, owner)
        result = recovered["response"]["result"]
        assert result["evidence"] == "original_native_close_ack_and_process_exit"
        assert result["post_close_context"]["host_observations"] == observations
        assert len(observations) == 2
        # The worker's same-key terminal replay completes the close intent.
        command = json.loads(
            (worker.root / "failed" / (owner["command_id"] + ".json")).read_text())
        worker.process(queued(
            worker, "close_project", command["arguments"], "original-close"))
        assert close_intent(worker.profile)["phase"] == "closed"
        assert len(observations) == 2
    assert not reads


@pytest.mark.parametrize("fault", [
    "ack", "target", "backup", "content", "generation", "binding", "duplicate"
])
def test_missing_proof_preserves_all_close_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    worker, path, owner, ledger, response, placeholder, reads = fixture(
        tmp_path, monkeypatch)
    root = Path(owner["runtime"])
    if fault == "ack":
        (root / "sdk-trace/CloseProject.json").unlink()
    elif fault == "target":
        saved = json.loads((root / "sdk-trace/SaveProject.json").read_text())
        saved["response"]["objects"]["target"]["cache"] = []
        atomic_json(root / "sdk-trace/SaveProject.json", saved)
    elif fault == "backup":
        (worker.root / "backups/original.drp").unlink()
    elif fault == "content":
        placeholder.GetTimelineCount = lambda: 1
    elif fault == "binding":
        trace = json.loads((root / "sdk-trace/CloseProject.json").read_text())
        trace["call"]["arguments"]["a"] = [{"r": "other-target"}]
        atomic_json(root / "sdk-trace/CloseProject.json", trace)
    elif fault == "duplicate":
        trace = json.loads((root / "sdk-trace/CloseProject.json").read_text())
        atomic_json(root / "sdk-trace/AnotherClose.json", trace)
    else:
        monkeypatch.setattr(module, "read_active", lambda profile: None)
    before = {p: p.read_bytes() for p in (path, ledger, response)}
    with pytest.raises((RuntimeError, bridge.BridgeOperationError)):
        module.recover_acknowledged_close(worker.profile, path, owner)
    assert all(p.read_bytes() == data for p, data in before.items())
    intent = close_intent(worker.profile)
    assert intent is not None and intent["phase"] == "pending"
    assert not list((worker.root / "state/close-reconciliations").glob("*.json"))


def test_host_commit_crash_resumes_archived_proof_without_new_native_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, path, owner, ledger, response, placeholder, reads = fixture(
        tmp_path, monkeypatch)
    original = atomic_json

    def interrupted(target: Path, value: Any) -> None:
        if target == path:
            raise OSError("host crashed before final owner commit")
        original(target, value)

    monkeypatch.setattr(module, "atomic_json", interrupted)
    with pytest.raises(OSError):
        module.recover_acknowledged_close(worker.profile, path, owner)
    assert json.loads(response.read_text())["status"] == "success"
    assert json.loads(path.read_text())["phase"] == "uncertain"
    monkeypatch.setattr(module, "atomic_json", original)
    archive = next((worker.root / "state/close-reconciliations").glob("*.json"))
    archived = json.loads(archive.read_text())
    tampered = json.loads(archive.read_text())
    tampered["response"]["result"]["project"]["project_id"] = "different"
    atomic_json(archive, tampered)
    with pytest.raises(RuntimeError, match="ARCHIVE_CHANGED"):
        module.recover_acknowledged_close(worker.profile, path, owner)
    assert len(reads) == 1 and json.loads(path.read_text())["phase"] == "uncertain"
    atomic_json(archive, archived)
    recovered = module.recover_acknowledged_close(worker.profile, path, owner)
    assert recovered["phase"] == "completed" and len(reads) == 1


def test_expired_original_is_reconciled_without_extending_its_write_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, path, owner, ledger, response, placeholder, reads = fixture(
        tmp_path, monkeypatch)
    original_path = worker.root / "failed" / (owner["command_id"] + ".json")
    command = json.loads(original_path.read_text())
    command.update(created_at="2020-01-01T00:00:00Z", expires_at="2020-01-01T00:01:00Z")
    atomic_json(original_path, command)
    before = original_path.read_bytes()
    with pytest.raises(ValueError, match="expired"):
        bridge.validate_command(command)
    result = module.recover_acknowledged_close(worker.profile, path, owner)
    assert result["phase"] == "completed" and len(reads) == 1
    assert original_path.read_bytes() == before
    assert reads[0]["action"] == "get_capabilities"
