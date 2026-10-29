"""Offline startup evidence never substitutes for fresh native identity."""

from __future__ import annotations

import json
import os
import zipfile
import zlib
from pathlib import Path
from typing import Any

import pytest

from providers.resolve import startup_diagnostics as diagnostics
from providers.resolve.startup_diagnostics import (
    MENU_FILE,
    MENU_HEADER,
    inspect_startup,
)

SESSION = "1" * 32


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "receipts").mkdir()
    (root / "session.json").write_text(json.dumps({
        "session": SESSION,
        "transport": "marker",
        "protocol": 4,
        "marker_protocol": 1,
        "lease_seconds": 600,
        "max_payload_bytes": 4096,
    }), encoding="utf-8")
    (root / "marker-bridge.lua").write_text(
        f'local session="{SESSION}"\nreturn\n', encoding="utf-8"
    )
    return root


@pytest.fixture
def scripts(runtime: Path, tmp_path: Path) -> Path:
    result = tmp_path / "Utility"
    result.mkdir()
    source = (runtime / "marker-bridge.lua").read_text(encoding="utf-8")
    (result / MENU_FILE).write_text(MENU_HEADER + source, encoding="utf-8")
    return result


def snapshot(root: Path, filename: str) -> None:
    with zipfile.ZipFile(root / filename, "w") as archive:
        archive.writestr(
            "project.xml",
            '<SM_Project DbId="test-id"><ProjectName>Test</ProjectName></SM_Project>',
        )


def codes(result: dict[str, Any]) -> list[str]:
    return [step["code"] for step in result["handoff"]]


def test_fresh_session_has_manual_handoff_and_no_native_claim(
    runtime: Path, scripts: Path,
) -> None:
    host = {"state_hint": "project_manager", "resolve_process_count": 1}
    result = inspect_startup(
        runtime, scripts_directory=scripts, target_project_name="Existing project",
        host=host,
    )
    assert result["session"] == SESSION
    assert result["state"] == "bootstrap_required"
    assert result["metadata_status"] == "valid"
    assert result["native_start_evidence"] == "not_observed"
    assert result["terminal"] is False
    assert result["project_state"] == "unverified"
    assert result["recording_readiness"] == "not_assessed"
    assert result["native_connection_verified"] is False
    assert result["host"] == host
    assert "Existing project" in result["handoff"][0]["instruction"]
    assert codes(result) == [
        "open_existing_saved_target_project_then_run_menu_once", "fresh_native_check",
    ]
    assert result["startup_capabilities"] == {
        "unattended_cold_start": False, "launch": False, "open_existing": False,
        "list_existing": False, "bootstrap_from_ProjectManager": False,
        "saved_project_required": True, "managed_menu_required": True,
    }


def test_valid_consumption_alone_requires_native_check(
    runtime: Path, scripts: Path,
) -> None:
    snapshot(runtime, "marker-session-consumed.drp")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["native_start_evidence"] == "consumed"
    assert result["terminal"] is False
    assert result["state"] == "native_check_required"
    assert codes(result) == ["fresh_native_check"]


@pytest.mark.parametrize("reason", diagnostics.EXIT_REASONS)
def test_valid_lifecycle_exports_establish_exit_but_never_connection(
    runtime: Path, scripts: Path, reason: str,
) -> None:
    snapshot(runtime, "marker-session-consumed.drp")
    snapshot(runtime, f"marker-exit-{reason}.drp")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["terminal"] is True
    assert result["state"] == "session_exited"
    assert result["native_exit_evidence"] == [reason]
    assert result["native_connection_verified"] is False
    assert codes(result) == ["prepare_fresh_runtime_after_reconciliation"]


@pytest.mark.parametrize("name", [
    "marker-session-consumed.drp", "marker-exit-lease_expired.drp",
])
@pytest.mark.parametrize("invalid", [b"not a ZIP", b"", b"PK\x03\x04"])
def test_invalid_lifecycle_file_cannot_establish_terminal_state(
    runtime: Path, scripts: Path, name: str, invalid: bytes,
) -> None:
    snapshot(runtime, "marker-session-consumed.drp")
    snapshot(runtime, "marker-exit-lease_expired.drp")
    (runtime / name).write_bytes(invalid)
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["native_start_evidence"] == "evidence_invalid"
    assert result["terminal"] is False
    assert result["state"] == "inspection_required"
    assert codes(result) == ["audit_local_evidence"]


@pytest.mark.parametrize("xml", [
    '<SM_Project><ProjectName>No ID</ProjectName></SM_Project>',
    '<!DOCTYPE x><SM_Project DbId="id"><ProjectName>X</ProjectName></SM_Project>',
    '<SM_Project DbId="id"><ProjectName>broken',
])
def test_zip_requires_bounded_valid_project_identity(
    runtime: Path, scripts: Path, xml: str,
) -> None:
    with zipfile.ZipFile(runtime / "marker-session-consumed.drp", "w") as archive:
        archive.writestr("project.xml", xml)
    assert inspect_startup(runtime, scripts_directory=scripts)[
        "native_start_evidence"
    ] == "evidence_invalid"


def test_zip_decompression_failure_is_invalid_evidence(
    runtime: Path, scripts: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bad_snapshot(path: Path) -> dict[str, str]:
        raise zlib.error("invalid compressed payload")

    monkeypatch.setattr(diagnostics, "read_project_snapshot", bad_snapshot)
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["native_start_evidence"] == "evidence_invalid"
    assert result["terminal"] is False


def test_lifecycle_export_outside_runtime_is_not_trusted(
    runtime: Path, scripts: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot(runtime, "marker-session-consumed.drp")
    snapshot(runtime, "marker-exit-error.drp")
    original = Path.resolve

    def redirected(path: Path, strict: bool = False) -> Path:
        if path.name == "marker-exit-error.drp":
            return tmp_path / path.name
        return original(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", redirected)
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["native_lifecycle"]["exits"]["error"] == "invalid"
    assert result["terminal"] is False


def test_exit_without_consumption_is_incomplete_evidence(
    runtime: Path, scripts: Path,
) -> None:
    snapshot(runtime, "marker-exit-error.drp")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["terminal"] is False
    assert result["native_start_evidence"] == "not_observed"
    assert result["state"] == "inspection_required"


def test_unknown_exit_filename_is_not_native_terminal_evidence(
    runtime: Path, scripts: Path,
) -> None:
    snapshot(runtime, "marker-session-consumed.drp")
    snapshot(runtime, "marker-exit-invented.drp")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["terminal"] is False
    assert result["native_exit_evidence"] == []


@pytest.mark.parametrize("payload,status", [
    ("[]", "corrupt"), ("not JSON", "corrupt"),
    ('{"session": "invalid"}', "corrupt"),
    ("x" * (diagnostics.MAX_METADATA_BYTES + 1), "oversize"),
], ids=["array", "invalid_json", "invalid_session", "oversize"])
def test_invalid_metadata_is_a_recovery_handoff(
    runtime: Path, scripts: Path, payload: str, status: str,
) -> None:
    (runtime / "session.json").write_text(payload, encoding="utf-8")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["metadata_status"] == status
    assert result["session"] is None
    assert result["terminal"] is False
    assert codes(result) == ["prepare_new_runtime_only_after_recovery_audit"]


def test_missing_runtime_is_diagnosable_without_creating_it(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    result = inspect_startup(root, scripts_directory=tmp_path / "scripts")
    assert result["metadata_status"] == "missing"
    assert result["state"] == "runtime_invalid"
    assert not root.exists()


def test_metadata_read_error_is_structured(
    runtime: Path, scripts: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = diagnostics._read_text

    def denied(path: Path, limit: int) -> str:
        if path.name == "session.json":
            raise PermissionError("test denial")
        return original(path, limit)

    monkeypatch.setattr(diagnostics, "_read_text", denied)
    assert inspect_startup(runtime, scripts_directory=scripts)[
        "metadata_status"
    ] == "unreadable"


def test_deeply_nested_metadata_is_corrupt(runtime: Path, scripts: Path) -> None:
    (runtime / "session.json").write_text("[" * 1200 + "]" * 1200, encoding="utf-8")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["metadata_status"] == "corrupt"


def test_metadata_validation_checks_marker_policy(runtime: Path, scripts: Path) -> None:
    path = runtime / "session.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["lease_seconds"] = 0
    path.write_text(json.dumps(metadata), encoding="utf-8")
    assert inspect_startup(runtime, scripts_directory=scripts)[
        "metadata_status"
    ] == "corrupt"


@pytest.mark.parametrize("replacement,expected", [
    (None, "missing"), ("-- personal script\n", "unmanaged"),
    (MENU_HEADER + "-- another session\n", "different_runtime"),
    ("x" * (diagnostics.MAX_SCRIPT_BYTES + 1), "unreadable"),
], ids=["missing", "unmanaged", "different_runtime", "oversize"])
def test_menu_binding_mismatch_requires_alignment(
    runtime: Path, scripts: Path, replacement: str | None, expected: str,
) -> None:
    target = scripts / MENU_FILE
    if replacement is None:
        target.unlink()
    else:
        target.write_text(replacement, encoding="utf-8")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["menu_binding"] == expected
    assert result["state"] == "menu_alignment_required"
    assert codes(result) == ["align_config_and_managed_menu"]


def test_menu_comparison_normalizes_only_bom_and_line_endings(
    runtime: Path, scripts: Path,
) -> None:
    target = scripts / MENU_FILE
    text = target.read_text(encoding="utf-8").replace("\n", "\r\n")
    target.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    assert inspect_startup(runtime, scripts_directory=scripts)[
        "menu_binding"
    ] == "matching"
    target.write_bytes(target.read_bytes() + b" ")
    assert inspect_startup(runtime, scripts_directory=scripts)[
        "menu_binding"
    ] == "different_runtime"


def test_missing_prepared_menu_source_is_unreadable(
    runtime: Path, scripts: Path,
) -> None:
    (runtime / "marker-bridge.lua").unlink()
    assert inspect_startup(runtime, scripts_directory=scripts)[
        "menu_binding"
    ] == "unreadable"


def test_terminal_menu_mismatch_reconnects_before_fresh_preparation(
    runtime: Path, scripts: Path,
) -> None:
    snapshot(runtime, "marker-session-consumed.drp")
    snapshot(runtime, "marker-exit-acknowledged_stop.drp")
    (scripts / MENU_FILE).write_text(MENU_HEADER + "return true\n", encoding="utf-8")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["state"] == "session_exited"
    assert codes(result) == [
        "reload_mcp_matching_prepared_runtime",
        "prepare_fresh_runtime_after_reconciliation",
    ]


def test_pending_receipt_has_priority_over_terminal_and_menu_mismatch(
    runtime: Path, scripts: Path,
) -> None:
    snapshot(runtime, "marker-session-consumed.drp")
    snapshot(runtime, "marker-exit-lease_expired.drp")
    (runtime / "receipts" / "receipt.json").write_text(json.dumps({
        "status": "pending", "command_id": "2" * 32,
    }), encoding="utf-8")
    (scripts / MENU_FILE).write_text(MENU_HEADER + "return true\n", encoding="utf-8")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["terminal"] is True
    assert result["state"] == "recovery_required"
    assert result["recovery"]["pending_receipt_count"] == 1
    assert codes(result) == ["reconcile_original_operation"]


def test_occupied_mailbox_retains_recovery_priority_with_invalid_metadata(
    runtime: Path, scripts: Path,
) -> None:
    (runtime / "inbox").mkdir()
    (runtime / "session.json").write_text("broken", encoding="utf-8")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["recovery"]["inbox_occupied"] is True
    assert result["state"] == "recovery_required"
    assert codes(result) == ["reconcile_original_operation"]


def test_valid_occupied_mailbox_is_reported_without_dispatch(
    runtime: Path, scripts: Path,
) -> None:
    inbox = runtime / "inbox"
    inbox.mkdir()
    (inbox / ".owner.json").write_text(json.dumps({
        "session": SESSION, "id": "2" * 32, "action": "import_media", "expires": 1,
    }), encoding="utf-8")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["recovery"]["status"] == "inspected"
    assert result["recovery"]["inbox"]["session_matches"] is True
    assert result["state"] == "recovery_required"


def test_foreign_mailbox_keeps_original_operation_handoff(
    runtime: Path, scripts: Path,
) -> None:
    inbox = runtime / "inbox"
    inbox.mkdir()
    (inbox / ".owner.json").write_text(json.dumps({
        "session": "3" * 32, "id": "2" * 32,
    }), encoding="utf-8")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["recovery"]["inbox"]["session_matches"] is False
    assert codes(result) == ["reconcile_original_operation"]


@pytest.mark.parametrize("filename", ["client.lock", "lease-publish.lock"])
def test_lock_alone_is_busy_evidence_without_uncertain_mutation_claim(
    runtime: Path, scripts: Path, filename: str,
) -> None:
    (runtime / filename).touch()
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["recovery"]["pending_receipt_count"] == 0
    assert result["state"] == "inspection_required"
    assert codes(result) == ["wait_then_retry_diagnostics"]


@pytest.mark.parametrize("payload", ["[]", "broken JSON", "x" * 65537],
                         ids=["array", "invalid_json", "oversize"])
def test_invalid_receipts_require_local_evidence_inspection(
    runtime: Path, scripts: Path, payload: str,
) -> None:
    (runtime / "receipts" / "bad.json").write_text(payload, encoding="utf-8")
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["recovery"]["status"] == "inspection_required"
    assert result["recovery"]["pending_receipt_count"] is None
    assert codes(result) == ["audit_local_evidence"]


def test_receipt_count_is_bounded(runtime: Path, scripts: Path) -> None:
    for index in range(diagnostics.MAX_RECEIPTS + 1):
        (runtime / "receipts" / f"{index}.json").write_text(
            '{"status":"completed"}', encoding="utf-8"
        )
    result = inspect_startup(runtime, scripts_directory=scripts)
    assert result["recovery"]["receipt_count"] == diagnostics.MAX_RECEIPTS
    assert result["recovery"]["receipt_count_truncated"] is True
    assert result["state"] == "inspection_required"


def test_receipts_path_must_be_a_directory(runtime: Path, scripts: Path) -> None:
    receipts = runtime / "receipts"
    receipts.rmdir()
    receipts.write_text("foreign content", encoding="utf-8")
    assert inspect_startup(runtime, scripts_directory=scripts)[
        "recovery"
    ]["status"] == "inspection_required"


@pytest.mark.parametrize("name", ["", " x", "x ", "x\n", "x" * 129],
                         ids=["empty", "leading_space", "trailing_space", "lf", "long"])
def test_target_project_name_is_bounded_handoff_data(runtime: Path, name: str) -> None:
    with pytest.raises(ValueError, match="Target project name"):
        inspect_startup(runtime, target_project_name=name)


def test_default_menu_location_uses_windows_appdata_when_available(
    runtime: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        assert inspect_startup(runtime)["menu_binding"] == "not_checked"
        return
    monkeypatch.setenv("APPDATA", str(tmp_path))
    result = inspect_startup(runtime)
    assert result["menu_binding"] == "missing"
    monkeypatch.delenv("APPDATA")
    assert inspect_startup(runtime)["menu_binding"] == "not_checked"


def test_diagnostic_does_not_modify_runtime_or_menu(
    runtime: Path, scripts: Path,
) -> None:
    snapshot(runtime, "marker-session-consumed.drp")
    snapshot(runtime, "marker-exit-error.drp")
    (runtime / "client.lock").write_text("existing", encoding="utf-8")
    paths = [*runtime.rglob("*"), *scripts.rglob("*")]
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in paths if path.is_file()
    }
    inspect_startup(runtime, scripts_directory=scripts)
    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in [*runtime.rglob("*"), *scripts.rglob("*")] if path.is_file()
    }
    assert after == before
