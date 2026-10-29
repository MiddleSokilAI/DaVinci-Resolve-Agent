"""Offline Free startup contracts through the real stdio and CLI boundaries.

All runtimes, menu scripts, and lifecycle exports are temporary fixtures. Native
requests and optional Windows metadata are doubled in-process only. No test
launches Resolve, executes its Lua bundle, or inspects the Windows desktop.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

from mcp_server.free_marker import create_server
from providers.resolve import windows_host
from providers.resolve.lua_transport import LuaCommandTimeoutError, LuaSnapshotClient
from providers.resolve.marker_transport import prepare_marker
from providers.resolve.startup_diagnostics import MENU_FILE, MENU_HEADER

TARGET_PROJECT = "Existing saved project — Україна"
NATIVE_PROJECT = {"name": TARGET_PROJECT, "id": "native-project-identity"}


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every child and in-process menu lookup out of the real user profile."""
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    return appdata


def unpack(result: CallToolResult) -> dict[str, Any]:
    """Require successful structured output from the actual MCP SDK."""
    assert not result.is_error, result.content
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def runtime_bytes(root: Path, *, exclude_host_lease: bool = False) -> dict[str, bytes]:
    """Snapshot fixture bytes, optionally excluding normal stdio lease renewal."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and not (exclude_host_lease and path.relative_to(root).parts[0] == "lease")
    }


def managed_menu(root: Path, scripts_directory: Path) -> Path:
    """Write a matching managed menu only inside this test's temporary folder."""
    scripts_directory.mkdir(parents=True)
    target = scripts_directory / MENU_FILE
    target.write_text(
        MENU_HEADER + (root / "marker-bridge.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return target


def appdata_menu(root: Path, appdata: Path) -> Path:
    """Use the exact production menu suffix under the isolated APPDATA root."""
    return managed_menu(
        root,
        appdata / "Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Utility",
    )


def lifecycle_export(path: Path) -> None:
    """Create a valid ZIP snapshot as historical evidence without Resolve."""
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "project.xml",
            '<SM_Project DbId="historic-native-project">'
            "<ProjectName>Historical saved project</ProjectName></SM_Project>",
        )


def assert_no_native_publication(root: Path) -> None:
    """Require an untouched native request placeholder and no mutation receipts."""
    assert (root / "request.lua").read_text(encoding="ascii") == "return nil\n"
    assert not (root / "inbox").exists()
    assert not (root / "client.lock").exists()
    assert not list((root / "receipts").glob("*.json"))
    assert not list(root.glob("response-*.drp"))
    assert not list(root.glob("*.before.drp"))


def assert_manual_capabilities(diagnostics: dict[str, Any]) -> None:
    """Keep local startup evidence separate from unsupported cold-start actions."""
    assert diagnostics["startup_capabilities"] == {
        "unattended_cold_start": False,
        "launch": False,
        "open_existing": False,
        "list_existing": False,
        "bootstrap_from_ProjectManager": False,
        "saved_project_required": True,
        "managed_menu_required": True,
    }
    assert diagnostics["recording_readiness"] == "not_assessed"


def diagnose_cli(
    runtime: Path, appdata: Path, *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Run only Python's offline diagnostic entrypoint with an isolated profile."""
    return subprocess.run(
        [
            sys.executable, "-m", "mcp_server.free_marker",
            "--runtime", str(runtime), "--diagnose", *arguments,
        ],
        env={**os.environ, "APPDATA": str(appdata), "PYTHONIOENCODING": "utf-8"},
        encoding="utf-8",
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_stdio_discovery_and_startup_are_local_only(
    tmp_path: Path, isolated_appdata: Path,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    menu = appdata_menu(root, isolated_appdata)
    original_menu = menu.read_bytes()
    before = runtime_bytes(root, exclude_host_lease=True)

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.free_marker", "--runtime", str(root)],
            env={"APPDATA": str(isolated_appdata)},
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert len(tools) == 51
            startup = tools["resolve_free_startup_status"]
            assert startup.annotations is not None
            assert startup.annotations.read_only_hint is True
            assert startup.annotations.destructive_hint is False
            assert startup.input_schema["properties"]["inspect_host_metadata"][
                "default"
            ] is False
            assert "target_project_name" in startup.input_schema["properties"]
            assert "target_project_name" in tools[
                "resolve_free_connection_status"
            ].input_schema["properties"]
            diagnostics = unpack(await session.call_tool(
                "resolve_free_startup_status", {"target_project_name": TARGET_PROJECT},
            ))
            assert diagnostics["metadata_status"] == "valid"
            assert diagnostics["native_start_evidence"] == "not_observed"
            assert diagnostics["terminal"] is False
            assert diagnostics["state"] == "bootstrap_required"
            assert diagnostics["menu_binding"] == (
                "matching" if os.name == "nt" else "not_checked"
            )
            assert diagnostics["project_state"] == "unverified"
            assert diagnostics["native_connection_verified"] is False
            assert diagnostics["host"] is None
            assert TARGET_PROJECT in diagnostics["handoff"][0]["instruction"]
            assert diagnostics["handoff"][0]["code"] == (
                "open_existing_saved_target_project_then_run_menu_once"
            )
            assert diagnostics["handoff"][1]["code"] == "fresh_native_check"
            assert "current_project" not in diagnostics
            assert_manual_capabilities(diagnostics)
            assert_no_native_publication(root)

    asyncio.run(exercise())
    assert runtime_bytes(root, exclude_host_lease=True) == before
    assert menu.read_bytes() == original_menu
    assert_no_native_publication(root)


@pytest.mark.parametrize("exit_reason", ["acknowledged_stop", "lease_expired"])
def test_stdio_terminal_native_exports_return_disconnected_without_a_request(
    tmp_path: Path, isolated_appdata: Path, exit_reason: str,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    appdata_menu(root, isolated_appdata)
    lifecycle_export(root / "marker-session-consumed.drp")
    lifecycle_export(root / f"marker-exit-{exit_reason}.drp")
    before = runtime_bytes(root, exclude_host_lease=True)

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.free_marker", "--runtime", str(root)],
            env={"APPDATA": str(isolated_appdata)},
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            result = unpack(await session.call_tool(
                "resolve_free_connection_status",
                {"target_project_name": TARGET_PROJECT, "timeout_seconds": 0.1},
            ))
            assert result["connected"] is False
            assert result["failure_code"] == "native_session_exited"
            assert result["liveness_source"] == "native_lifecycle_exports"
            assert result["native_request_attempted"] is False
            assert result["native_roundtrip_verified"] is False
            diagnostics = result["startup"]
            assert diagnostics["terminal"] is True
            assert diagnostics["state"] == "session_exited"
            assert diagnostics["native_start_evidence"] == "consumed"
            assert diagnostics["native_exit_evidence"] == [exit_reason]
            assert diagnostics["project_state"] == "unverified"
            assert diagnostics["native_connection_verified"] is False
            assert "current_project" not in diagnostics
            assert_manual_capabilities(diagnostics)
            assert_no_native_publication(root)

    asyncio.run(exercise())
    assert runtime_bytes(root, exclude_host_lease=True) == before
    assert_no_native_publication(root)


def test_cli_matching_menu_and_named_manual_handoff_do_not_write(
    tmp_path: Path, isolated_appdata: Path,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    menu = managed_menu(root, tmp_path / "explicit-scripts-directory")
    before = runtime_bytes(tmp_path)
    completed = diagnose_cli(
        root, isolated_appdata,
        "--scripts-directory", str(menu.parent),
        "--target-project-name", TARGET_PROJECT,
    )
    assert completed.returncode == 0, completed.stderr
    diagnostics = json.loads(completed.stdout)
    assert diagnostics["metadata_status"] == "valid"
    assert diagnostics["menu_binding"] == "matching"
    assert diagnostics["state"] == "bootstrap_required"
    assert diagnostics["project_state"] == "unverified"
    assert diagnostics["host"] is None
    assert TARGET_PROJECT in diagnostics["handoff"][0]["instruction"]
    assert "current_project" not in diagnostics
    assert_manual_capabilities(diagnostics)
    assert runtime_bytes(tmp_path) == before
    assert_no_native_publication(root)


@pytest.mark.parametrize("metadata_case", ["missing_runtime", "missing", "corrupt"])
def test_cli_invalid_metadata_is_structured_and_does_not_create_or_change_files(
    tmp_path: Path, isolated_appdata: Path, metadata_case: str,
) -> None:
    root = tmp_path / "runtime"
    if metadata_case != "missing_runtime":
        root.mkdir()
        (root / "preserve.txt").write_text("fixture evidence", encoding="utf-8")
    if metadata_case == "corrupt":
        (root / "session.json").write_text("{invalid metadata", encoding="utf-8")
    before = runtime_bytes(tmp_path)
    completed = diagnose_cli(root, isolated_appdata)
    assert completed.returncode == 0, completed.stderr
    diagnostics = json.loads(completed.stdout)
    assert diagnostics["state"] == "runtime_invalid"
    assert diagnostics["metadata_status"] == (
        "corrupt" if metadata_case == "corrupt" else "missing"
    )
    assert diagnostics["session"] is None
    assert diagnostics["project_state"] == "unverified"
    assert diagnostics["native_connection_verified"] is False
    assert diagnostics["host"] is None
    assert runtime_bytes(tmp_path) == before
    assert root.exists() is (metadata_case != "missing_runtime")


@pytest.mark.parametrize("prepared", [False, True])
def test_cli_diagnose_and_prepare_are_refused_without_writes(
    tmp_path: Path, isolated_appdata: Path, prepared: bool,
) -> None:
    root = tmp_path / "runtime"
    if prepared:
        prepare_marker(root)
    before = runtime_bytes(tmp_path)
    completed = diagnose_cli(root, isolated_appdata, "--prepare")
    assert completed.returncode == 2
    assert "--diagnose cannot prepare/install scripts" in completed.stderr
    assert completed.stdout == ""
    assert runtime_bytes(tmp_path) == before
    assert root.exists() is prepared


def fake_project_manager_host() -> dict[str, Any]:
    """Return synthetic host evidence which cannot establish native identity."""
    return {
        "status": "observed",
        "source": "windows_process_metadata",
        "processes": [{
            "pid": 123, "title": "Project Manager", "main_window_handle": 456,
            "responding": True, "executable_path": None,
        }],
        "running": True,
        "window_hint": "project_manager",
        "project_state": "unverified",
    }


@pytest.mark.parametrize("target_matches", [False, True])
def test_inprocess_native_proof_controls_target_match_while_host_hint_stays_unverified(
    tmp_path: Path, isolated_appdata: Path,
    monkeypatch: pytest.MonkeyPatch, target_matches: bool,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    appdata_menu(root, isolated_appdata)
    calls: list[str] = []
    host_calls: list[bool] = []
    target = TARGET_PROJECT if target_matches else "Another existing saved project"

    def host() -> dict[str, Any]:
        host_calls.append(True)
        return fake_project_manager_host()

    def native_request(
        self: LuaSnapshotClient, action: str, timeout_seconds: float, **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(action)
        assert action == "get_current_project"
        assert timeout_seconds == 0.1
        assert kwargs == {}
        return {"project": dict(NATIVE_PROJECT)}

    monkeypatch.setattr(windows_host, "inspect_windows_host", host)
    monkeypatch.setattr(LuaSnapshotClient, "request", native_request)

    async def exercise() -> None:
        async with Client(create_server(root)) as client:
            hinted = unpack(await client.call_tool(
                "resolve_free_startup_status",
                {"target_project_name": target, "inspect_host_metadata": True},
            ))
            assert hinted["host"]["window_hint"] == "project_manager"
            assert hinted["host"]["project_state"] == "unverified"
            assert hinted["project_state"] == "unverified"
            assert hinted["native_connection_verified"] is False
            assert calls == []
            result = unpack(await client.call_tool(
                "resolve_free_connection_status",
                {"target_project_name": target, "timeout_seconds": 0.1},
            ))
            assert result["connected"] is True
            assert result["native_request_attempted"] is True
            assert result["native_roundtrip_verified"] is True
            assert result["liveness_source"] == "fresh_native_request"
            assert result["project"] == NATIVE_PROJECT
            diagnostics = result["startup"]
            assert diagnostics["project_state"] == "verified"
            assert diagnostics["native_connection_verified"] is True
            assert diagnostics["current_project"] == NATIVE_PROJECT
            assert diagnostics["target_project_matches"] is target_matches
            assert diagnostics["state"] == (
                "connected" if target_matches else "target_project_mismatch"
            )
            assert diagnostics["host"] is None
            if target_matches:
                assert diagnostics["handoff"] == []
            else:
                assert diagnostics["handoff"][0]["code"] == (
                    "open_existing_target_then_verify"
                )
                assert target in diagnostics["handoff"][0]["instruction"]
            assert hinted["host"] == fake_project_manager_host()
            assert hinted["project_state"] == "unverified"

    asyncio.run(exercise())
    assert calls == ["get_current_project"]
    assert host_calls == [True]
    assert_no_native_publication(root)


def test_inprocess_timeout_has_specific_failure_and_no_false_native_proof(
    tmp_path: Path, isolated_appdata: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    appdata_menu(root, isolated_appdata)
    calls: list[str] = []

    def native_request(
        self: LuaSnapshotClient, action: str, timeout_seconds: float, **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(action)
        assert kwargs == {}
        raise LuaCommandTimeoutError("f" * 32, timeout_seconds, marker=True)

    monkeypatch.setattr(LuaSnapshotClient, "request", native_request)

    async def exercise() -> None:
        async with Client(create_server(root)) as client:
            result = unpack(await client.call_tool(
                "resolve_free_connection_status",
                {"target_project_name": TARGET_PROJECT, "timeout_seconds": 0.1},
            ))
            assert result["connected"] is False
            assert result["failure_code"] == "no_native_response"
            assert result["native_request_attempted"] is True
            assert result["native_roundtrip_verified"] is False
            diagnostics = result["startup"]
            assert diagnostics["project_state"] == "unverified"
            assert diagnostics["native_connection_verified"] is False
            assert "current_project" not in diagnostics

    asyncio.run(exercise())
    assert calls == ["get_current_project"]
    assert_no_native_publication(root)


def recovery_receipt(root: Path, kind: str) -> Path:
    """Create pending or unreadable receipt evidence without a native inbox."""
    receipts = root / "receipts"
    receipts.mkdir(exist_ok=True)
    receipt = receipts / "original-operation.json"
    receipt.write_text(
        json.dumps({"status": "pending", "command_id": "2" * 32})
        if kind == "pending" else "{malformed receipt",
        encoding="utf-8",
    )
    assert not (root / "inbox").exists()
    return receipt


def test_inprocess_metadata_drift_takes_priority_over_valid_terminal_exports(
    tmp_path: Path, isolated_appdata: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    appdata_menu(root, isolated_appdata)
    server = create_server(root)
    metadata_path = root / "session.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    original_session = metadata["session"]
    replacement_session = "a" * 32 if original_session != "a" * 32 else "b" * 32
    metadata["session"] = replacement_session
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    lifecycle_export(root / "marker-session-consumed.drp")
    lifecycle_export(root / "marker-exit-acknowledged_stop.drp")
    before = runtime_bytes(root)
    calls: list[str] = []

    def forbidden_request(
        self: LuaSnapshotClient, action: str, timeout_seconds: float, **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(action)
        raise AssertionError("Metadata drift must refuse native dispatch.")

    monkeypatch.setattr(LuaSnapshotClient, "request", forbidden_request)

    async def exercise() -> None:
        async with Client(server) as client:
            result = unpack(await client.call_tool(
                "resolve_free_connection_status",
                {"target_project_name": TARGET_PROJECT, "timeout_seconds": 0.1},
            ))
            assert result["connected"] is False
            assert result["session"] == original_session
            assert result["failure_code"] == "runtime_metadata_changed_or_invalid"
            assert result["native_request_attempted"] is False
            assert result["native_roundtrip_verified"] is False
            assert result["liveness_source"] == "local_runtime_diagnostics"
            assert result["startup"]["metadata_status"] == "valid"
            assert result["startup"]["session"] == replacement_session
            assert result["startup"]["terminal"] is True
            assert result["startup"]["project_state"] == "unverified"

    asyncio.run(exercise())
    assert calls == []
    assert runtime_bytes(root) == before
    assert_no_native_publication(root)


@pytest.mark.parametrize("receipt_kind", ["pending", "malformed"])
@pytest.mark.parametrize("target_matches", [False, True])
def test_inprocess_native_proof_preserves_original_recovery_handoff(
    tmp_path: Path, isolated_appdata: Path, monkeypatch: pytest.MonkeyPatch,
    receipt_kind: str, target_matches: bool,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    appdata_menu(root, isolated_appdata)
    recovery_receipt(root, receipt_kind)
    before = runtime_bytes(root)
    calls: list[str] = []
    target = TARGET_PROJECT if target_matches else "Another existing saved project"
    expected_state = (
        "recovery_required" if receipt_kind == "pending" else "inspection_required"
    )
    expected_first_step = (
        "reconcile_original_operation" if receipt_kind == "pending"
        else "audit_local_evidence"
    )

    def native_request(
        self: LuaSnapshotClient, action: str, timeout_seconds: float, **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(action)
        assert action == "get_current_project"
        assert kwargs == {}
        return {"project": dict(NATIVE_PROJECT)}

    monkeypatch.setattr(LuaSnapshotClient, "request", native_request)

    async def exercise() -> None:
        async with Client(create_server(root)) as client:
            original = unpack(await client.call_tool(
                "resolve_free_startup_status", {"target_project_name": target},
            ))
            assert original["state"] == expected_state
            assert original["handoff"][0]["code"] == expected_first_step
            assert calls == []
            result = unpack(await client.call_tool(
                "resolve_free_connection_status",
                {"target_project_name": target, "timeout_seconds": 0.1},
            ))
            assert result["connected"] is True
            assert result["native_request_attempted"] is True
            assert result["native_roundtrip_verified"] is True
            assert result["project"] == NATIVE_PROJECT
            diagnostics = result["startup"]
            assert diagnostics["state"] == expected_state
            assert diagnostics["project_state"] == "verified"
            assert diagnostics["native_connection_verified"] is True
            assert diagnostics["target_project_matches"] is target_matches
            assert diagnostics["recovery"]["inbox_occupied"] is False
            assert diagnostics["recovery"]["pending_receipt_count"] == (
                1 if receipt_kind == "pending" else None
            )
            if target_matches:
                assert diagnostics["handoff"] == original["handoff"]
            else:
                assert diagnostics["handoff"][:-1] == original["handoff"]
                assert diagnostics["handoff"][-1]["code"] == (
                    "open_existing_target_then_verify"
                )
                assert target in diagnostics["handoff"][-1]["instruction"]

    asyncio.run(exercise())
    assert calls == ["get_current_project"]
    assert runtime_bytes(root) == before
    assert not (root / "inbox").exists()


@pytest.mark.parametrize("receipt_kind", ["pending", "malformed"])
def test_inprocess_terminal_recovery_evidence_refuses_native_dispatch(
    tmp_path: Path, isolated_appdata: Path, monkeypatch: pytest.MonkeyPatch,
    receipt_kind: str,
) -> None:
    root = tmp_path / "runtime"
    prepare_marker(root)
    appdata_menu(root, isolated_appdata)
    recovery_receipt(root, receipt_kind)
    lifecycle_export(root / "marker-session-consumed.drp")
    lifecycle_export(root / "marker-exit-acknowledged_stop.drp")
    before = runtime_bytes(root)
    calls: list[str] = []

    def forbidden_request(
        self: LuaSnapshotClient, action: str, timeout_seconds: float, **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(action)
        raise AssertionError("Terminal recovery evidence must refuse native dispatch.")

    monkeypatch.setattr(LuaSnapshotClient, "request", forbidden_request)

    async def exercise() -> None:
        async with Client(create_server(root)) as client:
            result = unpack(await client.call_tool(
                "resolve_free_connection_status",
                {"target_project_name": TARGET_PROJECT, "timeout_seconds": 0.1},
            ))
            assert result["connected"] is False
            assert result["failure_code"] == "runtime_recovery_required"
            assert result["native_request_attempted"] is False
            assert result["native_roundtrip_verified"] is False
            assert result["liveness_source"] == "local_runtime_diagnostics"
            diagnostics = result["startup"]
            assert diagnostics["terminal"] is True
            assert diagnostics["project_state"] == "unverified"
            assert diagnostics["state"] == (
                "recovery_required" if receipt_kind == "pending"
                else "inspection_required"
            )
            assert diagnostics["recovery"]["pending_receipt_count"] == (
                1 if receipt_kind == "pending" else None
            )
            assert diagnostics["handoff"][0]["code"] == (
                "reconcile_original_operation" if receipt_kind == "pending"
                else "audit_local_evidence"
            )

    asyncio.run(exercise())
    assert calls == []
    assert runtime_bytes(root) == before
    assert not (root / "inbox").exists()
