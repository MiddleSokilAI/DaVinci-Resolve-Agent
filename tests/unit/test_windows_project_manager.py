"""Offline Project Manager boundary tests; never inspect or operate live UI."""

from __future__ import annotations

import base64
import copy
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from providers.resolve import windows_project_manager as module
from providers.resolve.lua_transport import read_project_snapshot
from providers.resolve.windows_project_manager import (
    WindowsProjectManagerAdapter,
    WindowsProjectManagerError,
)

ENTRY = Path(module.__file__).with_name("ResolveProjectManagerUI.ps1")
POWERSHELL = shutil.which("powershell.exe")


def process(**changes: Any) -> dict[str, Any]:
    return {
        "pid": 123,
        "creation_time": "639288861153596149",
        "executable_path": (
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
        ),
        "window_handle": 456,
        "window_title": "Project Manager",
        "project_name_hint": None,
        "window_state": "normal",
        **changes,
    }


def node(**changes: Any) -> dict[str, Any]:
    return {
        "id": 0,
        "parent": None,
        "depth": 0,
        "name": "Project Manager",
        "automation_id": "UiProjectManagerDialog",
        "class_name": "ProjectManager::UiProjectManagerDialogImp",
        "control_type": "ControlType.Window",
        "native_handle": 456,
        "enabled": True,
        "offscreen": False,
        "selected": None,
        "value": None,
        "expand_state": None,
        "invoke": False,
        "selection_item": False,
        "selection": False,
        "expand_collapse": False,
        "value_pattern": False,
        "toggle": False,
        "toggle_state": None,
        "help_text": "",
        **changes,
    }


def response(**changes: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": "inspect",
        "backend": "windows_project_manager",
        "state": "project_manager",
        "eligible": True,
        "process": process(),
        "project_state": "unverified",
        "native_connection_verified": False,
        "dispatch_started": False,
        "tree": [node()],
        "tree_truncated": False,
        "raw_tree": [node()],
        "raw_tree_truncated": False,
        "container_accessibility": [],
        "context": {
            "library_name": None,
            "folder_path": None,
            "project_names": None,
            "verification": "accessibility_tree_only",
        },
        **changes,
    }


@pytest.fixture
def driver(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMW6432", r"C:\Program Files")
    calls: list[dict[str, Any]] = []
    state: dict[str, Any] = {"returncode": 0, "result": response()}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        error = state.get("error")
        if isinstance(error, Exception):
            raise error
        payload = json.loads(kwargs["input"])
        if state.get("echo_atomic"):
            assert payload["operation"] == "export_candidate"
            assert "last_input_tick" not in payload
            state["result"] = {
                "ok": True,
                "operation": "export_candidate",
                "backend": "windows_project_manager",
                "state": "candidate_export_saved",
                "eligible": True,
                "process": payload["expected_process"],
                "project_state": "unverified",
                "native_connection_verified": False,
                "dispatch_started": False,
                "project_open_requested": False,
                "export_requested": True,
                "candidate_export_saved": True,
                "destination": payload["destination"],
                "ownership_token": payload["ownership_token"],
                "last_input_tick": state.get("atomic_tick", 12345),
                "file_type": "Export files (*.drp)",
            }
        if state.get("echo_export"):
            state["result"] = {
                "ok": True,
                "operation": "begin_candidate_export",
                "backend": "windows_project_manager",
                "state": "candidate_export_dialog",
                "eligible": False,
                "process": payload["expected_process"],
                "project_state": "unverified",
                "native_connection_verified": False,
                "dispatch_started": False,
                "export_requested": True,
                "project_open_requested": False,
                "last_input_tick": 12345,
                "dialog": {
                    "ownership_token": payload["ownership_token"],
                    "pid": 123,
                    "creation_time": process()["creation_time"],
                    "handle": 789,
                    "title": "Export Project",
                    "class_name": "QFileDialog",
                    "automation_id": "ExportDialog",
                    "runtime_id": [42, 789],
                    "owner_handles": [456],
                },
                "dialog_tree": [node(name="Export Project", native_handle=789)],
                "dialog_tree_truncated": False,
            }
        return subprocess.CompletedProcess(
            command,
            state["returncode"],
            state.get("stdout", json.dumps(state["result"], ensure_ascii=False)),
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    return calls, state


def test_requires_opt_in_and_windows_without_starting_driver(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = driver
    with pytest.raises(WindowsProjectManagerError, match="OPT_IN_REQUIRED"):
        WindowsProjectManagerAdapter().inspect()
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(WindowsProjectManagerError, match="WINDOWS_REQUIRED"):
        WindowsProjectManagerAdapter(enabled=True).inspect()
    assert calls == []


@pytest.mark.parametrize("value", [True, 0, -1, 121, float("nan"), float("inf")])
def test_timeout_is_bounded(value: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        WindowsProjectManagerAdapter(enabled=True, timeout_seconds=value)


def test_inspection_is_fixed_data_only_and_not_native_identity(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, _ = driver
    result = WindowsProjectManagerAdapter(enabled=True).inspect()
    assert result["native_connection_verified"] is False
    assert result["context"]["verification"] == "accessibility_tree_only"
    assert len(calls) == 1
    call = calls[0]
    assert Path(call["command"][-1]).name == "ResolveProjectManagerUI.ps1"
    assert call["command"][1:-1] == [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]
    assert json.loads(call["input"]) == {"operation": "inspect"}
    assert call["shell"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"native_connection_verified": True},
        {"project_state": "verified"},
        {"dispatch_started": True},
        {"process": process(executable_path=r"C:\Temp\Resolve.exe")},
        {"process": None},
        {"tree": [node(id=1, parent=99)]},
        {"tree": [node(), node()]},
        {"tree": [node(depth=11)]},
        {"tree": [node(name="x" * 513)]},
        {"tree": [node(selected=1)]},
        {"tree_truncated": "false"},
        {"tree": [node(selector="anything")]},
    ],
)
def test_malformed_or_invented_ui_evidence_is_not_trusted(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    change: dict[str, Any],
) -> None:
    _, state = driver
    state["result"].update(change)
    with pytest.raises(
        WindowsProjectManagerError, match="UNVERIFIED_RESPONSE"
    ) as error:
        WindowsProjectManagerAdapter(enabled=True).inspect()
    assert not error.value.uncertain


def test_bounded_selected_nodes_are_diagnostic_only(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["result"]["tree"].append(
        node(
            id=1,
            parent=0,
            depth=1,
            name="Приклад відео — Українська",
            control_type="ControlType.ListItem",
            selection_item=True,
            selected=True,
        )
    )
    result = WindowsProjectManagerAdapter(enabled=True).inspect()
    assert result["tree"][1]["selected"] is True
    assert result["context"]["project_names"] is None


def prepared_response() -> dict[str, Any]:
    return response(
        operation="prepare_context_view",
        project_open_requested=False,
        refresh_view=False,
        view_changed=True,
        view_changes=[
            {
                "control": control,
                "before": "Off",
                "after": "On",
                "changed": True,
                "press_sequence": [control],
                "verification": "windows_uia_invoke_toggle_readback",
            }
            for control in ("library_sidebar", "list_view")
        ],
    )


def test_prepare_view_uses_fixed_semantic_request_and_never_claims_open(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    state["result"] = prepared_response()
    result = WindowsProjectManagerAdapter(enabled=True).prepare_context_view(process())
    assert result["project_open_requested"] is False
    assert result["dispatch_started"] is False
    assert result["view_changed"] is True
    assert json.loads(calls[0]["input"]) == {
        "operation": "prepare_context_view",
        "expected_process": process(),
        "refresh_view": False,
    }


@pytest.mark.parametrize("kind", ["readback", "summary", "opened", "process"])
def test_prepare_view_rejects_unverified_readback_without_claiming_project_open(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    kind: str,
) -> None:
    _, state = driver
    result = prepared_response()
    if kind == "readback":
        result["view_changes"][1]["after"] = "Off"
    elif kind == "summary":
        result["view_changed"] = False
    elif kind == "opened":
        result["project_open_requested"] = True
    else:
        result["process"]["creation_time"] = "639288861153596150"
    state["result"] = result
    with pytest.raises(
        WindowsProjectManagerError, match="UNVERIFIED_RESPONSE"
    ) as error:
        WindowsProjectManagerAdapter(enabled=True).prepare_context_view(process())
    assert error.value.uncertain
    assert error.value.project_open_requested is False
    assert error.value.view_changes_uncertain


def test_prepare_timeout_requires_view_reinspection_and_is_never_retried(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    state["error"] = subprocess.TimeoutExpired("driver", 45)
    with pytest.raises(WindowsProjectManagerError, match="TIMEOUT") as error:
        WindowsProjectManagerAdapter(enabled=True).prepare_context_view(process())
    assert error.value.uncertain and error.value.view_changes_uncertain
    assert error.value.project_open_requested is False
    assert len(calls) == 1


def test_begin_export_invokes_no_open_and_requires_new_owned_dialog(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    state["echo_export"] = True
    result = WindowsProjectManagerAdapter(enabled=True).begin_candidate_export(
        process()
    )
    assert result["project_open_requested"] is False
    assert result["export_requested"] is True
    assert result["dialog"]["owner_handles"] == [456]
    request = json.loads(calls[0]["input"])
    assert set(request) == {"operation", "expected_process", "ownership_token"}
    assert len(request["ownership_token"]) == 32


@pytest.mark.parametrize(
    "kind", ["unowned", "wrong_process", "same_window", "open_claim"]
)
def test_begin_export_refuses_wrong_dialog_identity(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    kind: str,
) -> None:
    _, state = driver
    state["echo_export"] = True
    adapter = WindowsProjectManagerAdapter(enabled=True)
    result = adapter.begin_candidate_export(process())
    payload = {
        "operation": "begin_candidate_export",
        "expected_process": process(),
        "ownership_token": result["dialog"]["ownership_token"],
    }
    if kind == "unowned":
        result["dialog"]["owner_handles"] = [999]
    elif kind == "wrong_process":
        result["dialog"]["pid"] = 999
    elif kind == "same_window":
        result["dialog"]["handle"] = 456
    else:
        result["project_open_requested"] = True
    with pytest.raises(ValueError, match="candidate export dialog"):
        adapter._validate_response(result, payload)


def test_begin_export_timeout_is_export_uncertainty_without_open_claim(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    state["error"] = subprocess.TimeoutExpired("driver", 45)
    with pytest.raises(WindowsProjectManagerError, match="TIMEOUT") as error:
        WindowsProjectManagerAdapter(enabled=True).begin_candidate_export(process())
    assert error.value.candidate_export_uncertain
    assert error.value.project_open_requested is False
    assert error.value.view_changes_uncertain is False
    assert len(calls) == 1


def test_export_dialog_inspection_never_repeats_export_or_claims_ownership(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    state["result"] = {
        "ok": True,
        "operation": "inspect_export_dialog",
        "backend": "windows_project_manager",
        "state": "export_dialog_inspection",
        "eligible": False,
        "process": process(),
        "project_state": "unverified",
        "native_connection_verified": False,
        "dispatch_started": False,
        "project_open_requested": False,
        "windows_truncated": False,
        "pm_found": True,
        "windows": [
            {
                "handle": 456,
                "pid": 123,
                "name": "Project Manager",
                "class_name": "ProjectManager::UiProjectManagerDialogImp",
                "automation_id": "UiProjectManagerDialog",
                "control_type": "ControlType.Window",
                "enabled": True,
                "offscreen": False,
                "modal": False,
                "owner_handles": [],
                "owner_is_pm": False,
                "tree": [],
                "tree_truncated": False,
            }
        ],
    }
    result = WindowsProjectManagerAdapter(enabled=True).inspect_export_dialog(process())
    assert result["windows"][0]["owner_is_pm"] is False
    assert json.loads(calls[0]["input"]) == {
        "operation": "inspect_export_dialog",
        "expected_process": process(),
    }
    assert len(calls) == 1


def test_readonly_export_diagnostic_timeout_is_not_export_or_open_uncertainty(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["error"] = subprocess.TimeoutExpired("driver", 45)
    with pytest.raises(WindowsProjectManagerError) as error:
        WindowsProjectManagerAdapter(enabled=True).inspect_export_dialog(process())
    assert not error.value.uncertain
    assert not error.value.project_open_requested
    assert not error.value.candidate_export_uncertain


@pytest.mark.parametrize("before", ["On", "Off"])
def test_begin_search_reports_only_fixed_view_change(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    before: str,
) -> None:
    calls, state = driver
    state["result"] = response(
        operation="begin_candidate_search",
        project_open_requested=False,
        search_toggle_before=before,
        search_toggle_after="On",
        search_requested=True,
        view_changed=before == "Off",
    )
    result = WindowsProjectManagerAdapter(enabled=True).begin_candidate_search(
        process()
    )
    assert not result["project_open_requested"]
    assert json.loads(calls[0]["input"]) == {
        "operation": "begin_candidate_search",
        "expected_process": process(),
    }


@pytest.mark.parametrize("problem", [None, "before", "changed", "wrong_readback"])
def test_exact_filter_is_catalogue_fenced_but_never_selection_or_open_proof(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    problem: str | None,
) -> None:
    calls, state = driver
    target_name = "Приклад відео — Українська"
    identity = "11111111-2222-3333-4444-555555555555"
    catalogue_calls: list[dict[str, Any]] = []

    def catalogue(**arguments: Any) -> dict[str, Any]:
        catalogue_calls.append(arguments)
        if problem == "before":
            raise RuntimeError("catalogue refused")
        return {
            "catalogue_signature": (
                "changed"
                if problem == "changed" and len(catalogue_calls) > 1
                else "stable"
            ),
            "ui_selection_verified": False,
        }

    monkeypatch.setattr(module, "verify_project_library_target", catalogue)
    state["result"] = response(
        operation="filter_candidate",
        project_name=target_name,
        project_open_requested=False,
        ui_selection_verified=False,
        search_value="Other" if problem == "wrong_readback" else target_name,
        search_before="",
        search_criteria="Name",
        view_changed=True,
    )
    adapter = WindowsProjectManagerAdapter(enabled=True)
    arguments: dict[str, Any] = {
        "project_name": target_name,
        "expected_project_id": identity,
        "expected_database": {"DbType": "Disk", "DbName": "Local Database"},
    }
    if problem:
        with pytest.raises(WindowsProjectManagerError) as error:
            adapter.filter_candidate(process(), **arguments)
        assert error.value.project_open_requested is False
        assert error.value.uncertain is (problem != "before")
        assert len(calls) == (0 if problem == "before" else 1)
    else:
        result = adapter.filter_candidate(process(), **arguments)
        assert result["ui_selection_verified"] is False
        assert result["project_open_requested"] is False
        assert catalogue_calls == [arguments, arguments]
        assert json.loads(calls[0]["input"]) == {
            "operation": "filter_candidate",
            "expected_process": process(),
            "project_name": target_name,
        }


@pytest.mark.parametrize(
    "name", ["resolve_not_running", "process_ambiguous", "desktop_unavailable"]
)
def test_inspection_distinguishes_not_ready_states(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    name: str,
) -> None:
    _, state = driver
    state["result"] = response(state=name, process=None, eligible=False, tree=[])
    assert WindowsProjectManagerAdapter(enabled=True).inspect()["state"] == name


def test_open_target_names_are_data_not_shell_source(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    title = 'Проєкт $(literal); & "example"'
    target: dict[str, Any] = {
        "library_name": "Local Database",
        "folder_path": [],
        "project_name": title,
    }
    state["result"] = {
        "ok": True,
        "operation": "open_existing",
        "backend": "windows_project_manager",
        "state": "open_requested",
        "eligible": True,
        "process": process(),
        "project_state": "unverified",
        "native_connection_verified": False,
        "dispatch_started": True,
        **target,
    }
    result = WindowsProjectManagerAdapter(enabled=True)._request(
        {
            "operation": "open_existing",
            "expected_process": process(),
            **target,
        }
    )
    assert result["state"] == "open_requested"
    assert json.loads(calls[0]["input"])["project_name"] == title
    assert title not in " ".join(calls[0]["command"])


@pytest.mark.parametrize(
    "change",
    [
        {"library_name": ""},
        {"project_name": "bad\nname"},
        {"folder_path": "root"},
        {"folder_path": ["bad\0name"]},
        {"folder_path": ["a"] * 17},
    ],
)
def test_invalid_targets_never_launch_driver(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    change: dict[str, Any],
) -> None:
    calls, _ = driver
    target: dict[str, Any] = {
        "library_name": "Local Database",
        "folder_path": [],
        "project_name": "Existing",
    }
    target.update(change)
    with pytest.raises(ValueError):
        WindowsProjectManagerAdapter(enabled=True).open_existing(
            process(),
            **target,
            expected_project_id="test-project-id",
            expected_database={"DbType": "Disk", "DbName": "Local Database"},
        )
    assert calls == []


@pytest.mark.parametrize("uncertain", [False, True])
def test_preserves_open_dispatch_error_boundary(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    uncertain: bool,
) -> None:
    calls, state = driver
    state["returncode"] = 1
    state["result"] = {
        "ok": False,
        "error": {
            "code": "CONTEXT_MISMATCH",
            "uncertain": uncertain,
            "phase": "after_dispatch" if uncertain else "before_dispatch",
        },
    }
    with pytest.raises(WindowsProjectManagerError, match="CONTEXT_MISMATCH") as error:
        WindowsProjectManagerAdapter(enabled=True)._request(
            {
                "operation": "open_existing",
                "expected_process": process(),
                "library_name": "Local Database",
                "folder_path": [],
                "project_name": "Existing",
            }
        )
    assert error.value.uncertain is uncertain
    assert len(calls) == 1


def test_timeout_after_request_does_not_retry_open(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    state["error"] = subprocess.TimeoutExpired("driver", 45)
    with pytest.raises(WindowsProjectManagerError, match="TIMEOUT") as error:
        WindowsProjectManagerAdapter(enabled=True)._request(
            {
                "operation": "open_existing",
                "expected_process": process(),
                "library_name": "Local Database",
                "folder_path": [],
                "project_name": "Existing",
            }
        )
    assert error.value.uncertain
    assert len(calls) == 1


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _offline(script: str, payload: Any = None) -> Any:
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is required for offline driver tests")
    assert POWERSHELL is not None
    source = (
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest; "
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        "[Console]::InputEncoding=[Text.UTF8Encoding]::new($false); "
        "$tokens=$null; $errors=$null; "
        f"$ast=[Management.Automation.Language.Parser]::ParseFile({_quote(str(ENTRY))},"
        "[ref]$tokens,[ref]$errors); if($errors.Count){throw ($errors|Out-String)}; "
        "$definitions=@($ast.FindAll({param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)); "
        '$functions=($definitions|ForEach-Object {$_.Extent.Text}) -join "`n"; '
        ". ([scriptblock]::Create($functions)); "
    )
    encoded = base64.b64encode((source + script).encode("utf-16-le")).decode()
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        input=json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
        capture_output=True,
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.lstrip("\ufeff"))


def test_driver_syntax_and_request_schema_without_running_entrypoint() -> None:
    target: dict[str, Any] = {
        "operation": "open_existing",
        "expected_process": process(),
        "library_name": "Local Database",
        "folder_path": [],
        "project_name": "Приклад відео — Українська",
        "last_input_tick": 123,
        "deadline_utc_ms": 1000,
        "candidate_export": {
            "path": "C:\\private\\" + "a" * 32 + "\\candidate.drp",
            "sha256": "a" * 64,
            "size": 100,
            "mtime_ns": 12300,
        },
    }
    valid = [
        {"operation": "inspect"},
        target,
        {
            "operation": "prepare_context_view",
            "expected_process": process(),
            "refresh_view": False,
        },
        {
            "operation": "begin_candidate_export",
            "expected_process": process(),
            "ownership_token": "a" * 32,
        },
        {"operation": "inspect_export_dialog", "expected_process": process()},
        {"operation": "begin_candidate_search", "expected_process": process()},
        {
            "operation": "filter_candidate",
            "expected_process": process(),
            "project_name": "Existing",
        },
        {
            "operation": "select_filtered_candidate",
            "expected_process": process(),
            "project_name": "Existing",
            "advance_once": False,
        },
    ]
    bad_process = copy.deepcopy(target)
    bad_process["expected_process"]["pid"] = True
    invalid = [
        {"operation": "inspect", "selector": "anything"},
        {"operation": "inspect", "coordinates": [1, 2]},
        {"operation": "inspect", "keys": "anything"},
        {"operation": "delete"},
        {"operation": ["inspect"]},
        bad_process,
        {**target, "folder_path": "root"},
        {**target, "project_name": "bad\nname"},
        {**target, "script": "anything"},
        {
            **target,
            "candidate_export": {
                **target["candidate_export"],
                "path": "C:\\candidate.drp",
            },
        },
        {
            **target,
            "candidate_export": {**target["candidate_export"], "sha256": "wrong"},
        },
        {**target, "last_input_tick": True},
        {**target, "deadline_utc_ms": True},
    ]
    texts = [json.dumps(value, ensure_ascii=False) for value in valid + invalid]
    texts.append('{"operation":"inspect","Operation":"inspect"}')
    result = _offline(
        "$texts=ConvertFrom-Json ([Console]::In.ReadToEnd());$out=@(); "
        "foreach($text in $texts){try{"
        "$null=ConvertTo-ProjectManagerRequest $text;$out+=,$true} "
        "catch{$out+=,$false}};ConvertTo-Json -InputObject @($out) -Compress",
        texts,
    )
    assert result == [True] * len(valid) + [False] * (len(texts) - len(valid))


@pytest.mark.parametrize(
    "mode", ["observed", "nested", "ambiguous", "foreign", "group", "wrong_id"]
)
def test_fixed_pm_discovery_uses_observed_identity_and_guards(mode: str) -> None:
    result = _offline(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;Add-Type -AssemblyName WindowsBase;
        $mode=ConvertFrom-Json ([Console]::In.ReadToEnd());
        function New-Element {
            param($Id,$Class,$AutomationId,$Handle,$PidValue,$Type,$Children)
            $item=[pscustomobject]@{Identity=$Id;Children=@($Children);Current=[pscustomobject]@{
                ProcessId=$PidValue;ClassName=$Class;AutomationId=$AutomationId;
                Name='Project Manager';NativeWindowHandle=$Handle;ControlType=$Type
            }};
            $item|Add-Member ScriptMethod GetRuntimeId {
                return [int[]]@($this.Identity)
            };
            $item|Add-Member ScriptMethod FindAll {
                param($scope,$condition)return $this.Children
            };
            return $item
        }
        $window=[System.Windows.Automation.ControlType]::Window;
        $pm=New-Element 1 'ProjectManager::UiProjectManagerDialogImp' `
            'UiProjectManagerDialog' 456 123 $window @();
        if($mode -ceq 'foreign'){$pm.Current.ProcessId=999}
        if($mode -ceq 'group'){
            $pm.Current.ControlType=[System.Windows.Automation.ControlType]::Group
        }
        if($mode -ceq 'wrong_id'){$pm.Current.AutomationId='AnotherDialog'}
        $roots=@($pm);
        if($mode -ceq 'nested'){
            $roots=@(New-Element 2 'QtContainer' '' 789 123 $window @($pm))
        }
        if($mode -ceq 'ambiguous'){
            $roots+=,(New-Element 3 'ProjectManager::UiProjectManagerDialogImp' `
                'UiProjectManagerDialog' 999 123 $window @())
        }
        $failed=$false;$handle=$null;
        try{
            $found=Find-ProjectManager $roots 123;
            if($null -ne $found){$handle=$found.Current.NativeWindowHandle}
        }
        catch{$failed=$true}
        @{failed=$failed;handle=$handle}|ConvertTo-Json -Compress
    """,
        mode,
    )
    assert result["failed"] is (mode == "ambiguous")
    assert result["handle"] == (456 if mode in {"observed", "nested"} else None)


@pytest.mark.parametrize(
    "mode",
    [
        "both_off",
        "already_on",
        "sidebar_pattern_missing",
        "list_pattern_missing",
        "indeterminate",
        "readback_failed",
        "context_changed",
        "invoke_missing",
        "refresh_on",
        "refresh_conflict",
    ],
)
def test_synthetic_view_preparation_reads_before_toggle_and_rechecks_after(
    mode: str,
) -> None:
    result = _offline(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;Add-Type -AssemblyName WindowsBase;
        $data=ConvertFrom-Json ([Console]::In.ReadToEnd());
        $script:Mode=$data.mode;$script:OpenDispatched=$false;$script:ViewTouched=$false;
        $script:Toggles=0;$script:Checks=0;
        $script:States=@{library_sidebar='Off';list_view='Off';thumbnail_view='On'};
        if($script:Mode -cin @('already_on','refresh_on','refresh_conflict')){
            $script:States.library_sidebar='On';$script:States.list_view='On'
            $script:States.thumbnail_view='Off'
        }
        if($script:Mode -ceq 'refresh_conflict'){$script:States.thumbnail_view='On'}
        $script:Evidence=@{operation='inspect';dispatch_started=$false;
            state='project_manager';eligible=$true;process=@{}};
        foreach($property in $data.process.PSObject.Properties){
            $script:Evidence.process[$property.Name]=$property.Value
        }
        $script:TreeWalks=0;
        function Get-ProjectManagerInspection {
            $script:TreeWalks++;return $script:Evidence
        }
        function Assert-ProjectManagerContext {
            param($expected) $script:Checks++;
            if($script:Mode -ceq 'context_changed' -and $script:Toggles -gt 0){
                Fail-ProjectManager 'PROCESS_OR_WINDOW_CHANGED'
            }
            return @{Manager=[pscustomobject]@{};Evidence=$script:Evidence}
        }
        function Find-ProjectManagerViewControl {
            param($manager,$kind,$processId)
            $control=[pscustomobject]@{Kind=$kind};
            $control|Add-Member ScriptMethod TryGetCurrentPattern {
                param($pattern,$output)
                if($script:Mode -ceq 'invoke_missing'){return $false}
                $press=[pscustomobject]@{Kind=$this.Kind};
                $press|Add-Member ScriptMethod Invoke {
                    $script:Toggles++;
                    if($script:Mode -cne 'readback_failed'){
                        if($this.Kind -ceq 'library_sidebar'){
                            $script:States.library_sidebar=if(
                                $script:States.library_sidebar -ceq 'On'){
                                    'Off'
                                }else{'On'}
                        }elseif($this.Kind -ceq 'list_view'){
                            $script:States.list_view='On';$script:States.thumbnail_view='Off'
                        }else{
                            $script:States.list_view='Off';$script:States.thumbnail_view='On'
                        }
                    }
                };
                $output.Value=$press;return $true
            };
            return $control
        }
        function Read-ProjectManagerViewToggle {
            param($control)
            if(($script:Mode -ceq 'sidebar_pattern_missing' -and
                $control.Kind -ceq 'library_sidebar') -or
                ($script:Mode -ceq 'list_pattern_missing' -and
                $control.Kind -ceq 'list_view')){
                Fail-ProjectManager 'VIEW_TOGGLE_PATTERN_UNAVAILABLE'
            }
            if($script:Mode -ceq 'indeterminate'){
                Fail-ProjectManager 'VIEW_TOGGLE_STATE_UNAVAILABLE'
            }
            return @{State=$script:States[$control.Kind];Pattern=$null}
        }
        function Start-Sleep {param($Milliseconds)}
        $failed=$false;$result=$null;
        try{
            $request=[pscustomobject]@{expected_process=$data.process;
                refresh_view=[bool]($script:Mode -cin @(
                    'refresh_on','refresh_conflict'))};
            $result=Prepare-ProjectManagerContextView $request
        }
        catch{$failed=$true}
        @{failed=$failed;toggles=$script:Toggles;view_touched=$script:ViewTouched;
          open_dispatched=$script:OpenDispatched;checks=$script:Checks;
          tree_walks=$script:TreeWalks;result=$result}|
            ConvertTo-Json -Depth 6 -Compress
    """,
        {"mode": mode, "process": process()},
    )
    assert result["open_dispatched"] is False
    assert result["failed"] is (mode not in {"both_off", "already_on", "refresh_on"})
    assert result["toggles"] == (
        4
        if mode == "refresh_on"
        else 2
        if mode in {"both_off", "refresh_conflict"}
        else 1
        if mode in {"list_pattern_missing", "readback_failed", "context_changed"}
        else 0
    )
    assert result["view_touched"] is (result["toggles"] > 0)
    if not result["failed"]:
        assert result["tree_walks"] == 1
        assert result["result"]["project_open_requested"] is False
        assert result["result"]["view_changed"] is (mode in {"both_off", "refresh_on"})


@pytest.mark.parametrize("state_name", ["On", "Off", "Indeterminate", "unsupported"])
def test_view_pattern_reader_refuses_unknown_state_or_missing_toggle(
    state_name: str,
) -> None:
    result = _offline(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;Add-Type -AssemblyName WindowsBase;
        $script:RequestedState=ConvertFrom-Json ([Console]::In.ReadToEnd());
        $control=[pscustomobject]@{};
        $control|Add-Member ScriptMethod TryGetCurrentPattern {
            param($pattern,$output)
            if($script:RequestedState -ceq 'unsupported'){return $false}
            $state=[Enum]::Parse(
                [System.Windows.Automation.ToggleState],$script:RequestedState);
            $output.Value=[pscustomobject]@{Current=[pscustomobject]@{ToggleState=$state}};
            return $true
        };
        $failed=$false;$state=$null;
        try{$read=Read-ProjectManagerViewToggle $control;$state=$read.State}
        catch{$failed=$true}
        @{failed=$failed;state=$state}|ConvertTo-Json -Compress
    """,
        state_name,
    )
    assert result["failed"] is (state_name in {"Indeterminate", "unsupported"})
    assert result["state"] == (state_name if state_name in {"On", "Off"} else None)


@pytest.mark.parametrize("mode", ["none", "grid", "item_container"])
def test_alternative_patterns_are_read_only_and_enumeration_is_bounded(
    mode: str,
) -> None:
    result = _offline(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;Add-Type -AssemblyName WindowsBase;
        $script:Mode=ConvertFrom-Json ([Console]::In.ReadToEnd());
        function New-Item([string]$Name,[int]$Id) {
            $item=[pscustomobject]@{Identity=$Id;Current=[pscustomobject]@{
                Name=$Name;AutomationId='';ControlType=[System.Windows.Automation.ControlType]::DataItem
            }};
            $item|Add-Member ScriptMethod TryGetCurrentPattern {
                param($pattern,$output)return $false
            };
            $item|Add-Member ScriptMethod GetRuntimeId {
                return [int[]]@($this.Identity)
            };
            return $item
        }
        $script:Map=@{};
        if($script:Mode -ceq 'grid'){
            $grid=[pscustomobject]@{Current=[pscustomobject]@{RowCount=100;ColumnCount=10}};
            $grid|Add-Member ScriptMethod GetItem {
                param($row,$column)
                return New-Item ('Project '+$row+'/'+$column) ($row*10+$column)
            };
            $script:Map[[System.Windows.Automation.GridPattern]::Pattern.Id]=$grid
        }
        if($script:Mode -ceq 'item_container'){
            $items=[pscustomobject]@{Next=0};
            $items|Add-Member ScriptMethod FindItemByProperty {
                param($after,$property,$value)
                if($null -ne $property -or $null -ne $value){
                    throw 'unexpected selector'
                }
                $this.Next++;return New-Item ('Project '+$this.Next) $this.Next
            };
            $script:Map[[System.Windows.Automation.ItemContainerPattern]::Pattern.Id]=$items
        }
        $element=[pscustomobject]@{};
        $element|Add-Member ScriptMethod GetSupportedPatterns {
            return @([pscustomobject]@{
                ProgrammaticName='LegacyIAccessiblePatternIdentifiers.Pattern'
            })
        };
        $element|Add-Member ScriptMethod TryGetCurrentPattern {
            param($pattern,$output)
            if($script:Map.ContainsKey($pattern.Id)){
                $output.Value=$script:Map[$pattern.Id];return $true
            }
            return $false
        };
        Get-ProjectManagerContainerPatterns $element|ConvertTo-Json -Depth 7 -Compress
    """,
        mode,
    )
    assert result["legacy"]["advertised"] is True
    assert result["errors"] == []
    assert result["table"]["available"] is False
    assert result["text"]["available"] is False
    if mode == "grid":
        assert result["grid"]["available"] is True
        assert result["grid"]["row_count"] == 100
        assert len(result["grid"]["cells"]) == 24
        assert result["grid"]["truncated"] is True
    elif mode == "item_container":
        assert result["item_container"]["available"] is True
        assert len(result["item_container"]["items"]) == 12
        assert result["item_container"]["truncated"] is True
    else:
        assert result["grid"]["available"] is False
        assert result["item_container"]["available"] is False


def test_pattern_diagnostic_response_is_structurally_bounded(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    records: list[dict[str, Any]] = []
    for kind, identifier, class_name in module._CONTAINERS:
        records.append(
            {
                "kind": kind,
                "automation_id": identifier,
                "class_name": class_name,
                "present": True,
                "ambiguous": False,
                "patterns": {
                    "supported_patterns": [
                        "LegacyIAccessiblePatternIdentifiers.Pattern"
                    ],
                    "pattern_list_truncated": False,
                    "errors": [],
                    "legacy": {
                        "available": False,
                        "advertised": True,
                        "reason": "managed_pattern_api_unavailable",
                    },
                    "grid": {"available": False},
                    "table": {"available": False},
                    "item_container": {"available": False},
                    "text": {"available": False},
                },
            }
        )
    state["result"]["container_accessibility"] = records
    result = WindowsProjectManagerAdapter(enabled=True).inspect()
    assert result["container_accessibility"][0]["patterns"]["legacy"]["advertised"]
    records[0]["patterns"]["grid"] = {"available": True, "cells": ["x"] * 33}
    with pytest.raises(WindowsProjectManagerError, match="UNVERIFIED_RESPONSE"):
        WindowsProjectManagerAdapter(enabled=True).inspect()


@pytest.mark.parametrize(
    "mode", ["owned", "ambiguous", "input_changed", "process_changed", "invoke_failed"]
)
def test_export_begin_uses_only_export_and_accepts_one_new_owned_dialog(
    mode: str,
) -> None:
    result = _offline(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;Add-Type -AssemblyName WindowsBase;
        $data=ConvertFrom-Json ([Console]::In.ReadToEnd());
        $script:Mode=$data.mode;$script:Clicked=$false;$script:Clicks=0;
        $script:ExportTouched=$false;$script:OpenDispatched=$false;$script:ModeChecks=0;
        function New-Window($Handle) {
            $item=[pscustomobject]@{Handle=$Handle;Current=[pscustomobject]@{
                ProcessId=123;NativeWindowHandle=$Handle;IsOffscreen=$false;IsEnabled=$true;
                ControlType=[System.Windows.Automation.ControlType]::Window;
                Name='Export Project';ClassName='QFileDialog';
                AutomationId='ExportDialog'
            }};
            $item|Add-Member ScriptMethod GetRuntimeId {
                return [int[]]@(42,$this.Handle)
            };
            return $item
        }
        $script:Manager=New-Window 456;$script:Dialog=New-Window 789;
        function Assert-ProjectManagerContext {
            param($expected)return @{Manager=$script:Manager}
        }
        function Assert-ProjectManagerLocalMode {
            param($manager,$pidValue)$script:ModeChecks++
        }
        function Find-ProjectManagerActionButton {
            param($manager,$action,$pidValue)
            if($action -cne 'Export'){throw 'non-export action'}
            $button=[pscustomobject]@{};
            $button|Add-Member ScriptMethod TryGetCurrentPattern {
                param($pattern,$output)$output.Value=[pscustomobject]@{};return $true
            };
            return $button
        }
        function Start-ProjectManagerExportInvoke {
            param($pattern)$script:Clicks++;$script:Clicked=$true;
            return @{Completed=($script:Mode -ceq 'invoke_failed');
                Succeeded=$false;Error='fake'}
        }
        function Get-ProjectManagerWindowElements {
            param($manager,$pidValue)
            $items=@($script:Manager);
            if($script:Clicked -and $script:Mode -cne 'invoke_failed'){
                $items+=,$script:Dialog;
                if($script:Mode -ceq 'ambiguous'){$items+=,(New-Window 999)}
            }
            return $items
        }
        function Get-ProjectManagerOwnerChain {param($handle)return @(456)}
        function Get-ProjectManagerInputTick {
            if($script:Mode -ceq 'input_changed' -and $script:Clicked){return 124}
            return 123
        }
        function Get-Process {param($Id)return [pscustomobject]@{}}
        function Get-ProjectManagerProcess {
            param($process)
            $creation=if($script:Mode -ceq 'process_changed'){
                'changed'
            }else{$data.process.creation_time};
            return @{pid=123;creation_time=$creation}
        }
        function Get-ProjectManagerTree {
            param($dialog)return @{Nodes=@();Truncated=$false}
        }
        function Start-Sleep {param($Milliseconds)}
        $failed=$false;$evidence=$null;
        try{$evidence=Begin-ProjectManagerCandidateExport ([pscustomobject]@{
            expected_process=$data.process;ownership_token=('a'*32)})}
        catch{$failed=$true}
        @{failed=$failed;clicks=$script:Clicks;export_touched=$script:ExportTouched;
          open_dispatched=$script:OpenDispatched;mode_checks=$script:ModeChecks;evidence=$evidence}|
            ConvertTo-Json -Depth 7 -Compress
    """,
        {"mode": mode, "process": process()},
    )
    assert result["clicks"] == 1
    assert result["mode_checks"] == 1
    assert result["open_dispatched"] is False
    assert result["export_touched"] is True
    assert result["failed"] is (mode != "owned")
    if mode == "owned":
        assert result["evidence"]["project_open_requested"] is False
        assert result["evidence"]["dialog"]["handle"] == 789


def test_background_export_dispatch_compiles_and_invokes_only_synthetic_pattern() -> (
    None
):
    result = _offline(r"""
        Initialize-ProjectManagerRuntime;
        Add-Type 'public sealed class SyntheticExportPattern {
            public static int Calls;
            public void Invoke(){System.Threading.Interlocked.Increment(ref Calls);}
        }';
        $state=[ProjectManagerNative]::BeginInvoke([SyntheticExportPattern]::new());
        $clock=[Diagnostics.Stopwatch]::StartNew();
        while(-not $state.Completed -and $clock.Elapsed.TotalSeconds -lt 2){
            [Threading.Thread]::Sleep(10)
        }
        @{completed=$state.Completed;succeeded=$state.Succeeded;calls=[SyntheticExportPattern]::Calls}|
            ConvertTo-Json -Compress
    """)
    assert result == {"completed": True, "succeeded": True, "calls": 1}


@pytest.mark.parametrize("advance_once", [False, True])
def test_selection_request_is_catalogue_fenced_and_still_requires_export_identity(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    advance_once: bool,
) -> None:
    calls, state = driver
    checks: list[dict[str, Any]] = []

    def catalogue(**arguments: Any) -> dict[str, Any]:
        checks.append(arguments)
        return {"catalogue_signature": "stable", "ui_selection_verified": False}

    monkeypatch.setattr(module, "verify_project_library_target", catalogue)
    state["result"] = {
        "ok": True,
        "operation": "select_filtered_candidate",
        "backend": "windows_project_manager",
        "state": "selection_requested",
        "eligible": True,
        "process": process(),
        "project_state": "unverified",
        "native_connection_verified": False,
        "dispatch_started": False,
        "project_open_requested": False,
        "ui_selection_verified": False,
        "selection_requested": True,
        "project_name": "Existing",
        "search_value": "Existing",
        "search_criteria": "Name",
        "last_input_tick": 123,
        "selection_method": "fixed_pm_home_then_down_messages"
        if advance_once
        else "fixed_pm_home_message",
        "home_messages_posted": 2,
        "advance_once": advance_once,
        "down_messages_posted": 2 if advance_once else 0,
    }
    result = WindowsProjectManagerAdapter(enabled=True).select_filtered_candidate(
        process(),
        project_name="Existing",
        expected_project_id="test-id",
        expected_database={"DbType": "Disk", "DbName": "Local Database"},
        advance_once=advance_once,
    )
    assert len(checks) == 2
    assert result["ui_selection_verified"] is False
    assert result["project_open_requested"] is False
    assert json.loads(calls[0]["input"]) == {
        "operation": "select_filtered_candidate",
        "expected_process": process(),
        "project_name": "Existing",
        "advance_once": advance_once,
    }


def test_export_failure_retains_bounded_dispatch_diagnostics(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    detail = {
        "invoke_completed": True,
        "invoke_succeeded": True,
        "invoke_error": "",
        "pattern_type": "System.Windows.Automation.InvokePattern",
    }
    state.update(
        returncode=1,
        result={
            "ok": False,
            "error": {
                "code": "EXPORT_DIALOG_NOT_OBSERVED",
                "uncertain": True,
                "phase": "after_dispatch",
                "diagnostics": detail,
            },
        },
    )
    with pytest.raises(WindowsProjectManagerError) as error:
        WindowsProjectManagerAdapter(enabled=True).begin_candidate_export(process())
    assert error.value.diagnostics == detail
    assert error.value.candidate_export_uncertain
    assert error.value.project_open_requested is False


@pytest.mark.parametrize(
    "mode",
    [
        "scoped",
        "wrong_query",
        "focus_wrong",
        "input_changed",
        "changed_after_home",
        "activation_denied",
        "input_changed_activation",
        "input_changed_bin_focus",
        "home_down",
        "input_changed_down",
        "input_changed_reactivation",
        "reactivation_denied",
    ],
)
def test_fixed_home_is_never_posted_without_verified_filter_and_bin_focus(
    mode: str,
) -> None:
    result = _offline(
        r"""
        $data=ConvertFrom-Json ([Console]::In.ReadToEnd());
        $script:Mode=$data.mode;$script:Homes=0;$script:Downs=0;$script:FocusChecks=0;
        $script:Activations=0;$script:BinFocusCalls=0;$script:ManagerFocusCalls=0;
        $script:ViewTouched=$false;$script:OpenDispatched=$false;
        $script:OperationDiagnostics=@{};
        $script:Manager=[pscustomobject]@{};$script:Bin=[pscustomobject]@{};
        $script:Manager|Add-Member ScriptMethod SetFocus {$script:ManagerFocusCalls++};
        $script:Bin|Add-Member ScriptMethod SetFocus {$script:BinFocusCalls++};
        function Assert-ProjectManagerContext {
            param($expected)return @{Manager=$script:Manager}
        }
        function Assert-ProjectManagerLocalMode {param($manager,$pidValue)}
        function Activate-ProjectManagerWindow {
            param($expected)$script:Activations++;
            if($script:Mode -ceq 'activation_denied'){
                Fail-ProjectManager 'PROJECT_MANAGER_ACTIVATION_DENIED'
            }
            if($script:Mode -ceq 'reactivation_denied' -and $script:Activations -gt 1){
                Fail-ProjectManager 'PROJECT_MANAGER_ACTIVATION_DENIED'
            }
        }
        function Read-ProjectManagerSearchState {
            param($manager,$pidValue)
            $value=if($script:Mode -ceq 'wrong_query'){'Other'}else{'Existing'};
            return @{Criteria='Name';Value=$value}
        }
        function Find-ProjectManagerProjectBin {
            param($manager,$pidValue)return $script:Bin
        }
        function Assert-ProjectManagerBinFocus {
            param($bin,$pidValue)$script:FocusChecks++;
            if($script:Mode -ceq 'focus_wrong'){
                Fail-ProjectManager 'PROJECT_BIN_FOCUS_UNVERIFIED'
            }
        }
        function Get-ProjectManagerInputTick {
            if($script:Mode -ceq 'input_changed_reactivation' -and
                $script:Activations -gt 1){return 124}
            if($script:Mode -ceq 'input_changed_down' -and $script:Downs -gt 0){
                return 124
            }
            if($script:Mode -ceq 'input_changed_bin_focus' -and
                $script:BinFocusCalls -gt 0){
                return 124
            }
            if($script:Mode -ceq 'input_changed_activation' -and
                $script:Activations -gt 0){
                return 124
            }
            if($script:Mode -ceq 'input_changed' -and $script:FocusChecks -gt 0){
                return 124
            }
            if($script:Mode -ceq 'changed_after_home' -and $script:Homes -gt 0){
                return 124
            }
            return 123
        }
        function Post-ProjectManagerHome {
            param($expected)
            if($expected.window_handle -ne 456 -or $expected.pid -ne 123){
                throw 'wrong target'
            }
            $script:Homes++
        }
        function Post-ProjectManagerDown {
            param($expected)
            if($expected.window_handle -ne 456 -or $expected.pid -ne 123){
                throw 'wrong target'
            }
            $script:Downs++
        }
        function Start-Sleep {param($Milliseconds)}
        $failed=$false;$evidence=$null;
        try{$evidence=Select-ProjectManagerFilteredCandidate ([pscustomobject]@{
            expected_process=$data.process;project_name='Existing';
            advance_once=($script:Mode -cin @('home_down','input_changed_down'))})}
        catch{$failed=$true}
        @{failed=$failed;homes=$script:Homes;downs=$script:Downs;
          open_dispatched=$script:OpenDispatched;
          manager_focus_calls=$script:ManagerFocusCalls;
          activations=$script:Activations;
          view_touched=$script:ViewTouched;evidence=$evidence;
          diagnostics=$script:OperationDiagnostics}|
            ConvertTo-Json -Depth 5 -Compress
    """,
        {"mode": mode, "process": process()},
    )
    assert result["failed"] is (mode not in {"scoped", "home_down"})
    assert result["homes"] == (
        1
        if mode in {"scoped", "changed_after_home", "home_down", "input_changed_down"}
        else 0
    )
    assert result["downs"] == (1 if mode in {"home_down", "input_changed_down"} else 0)
    assert result["open_dispatched"] is False
    assert result["manager_focus_calls"] == 0
    if mode in {"scoped", "home_down"}:
        assert result["activations"] == 2
        assert result["evidence"]["ui_selection_verified"] is False
        assert result["evidence"]["home_messages_posted"] == 2
        assert result["evidence"]["down_messages_posted"] == (
            2 if mode == "home_down" else 0
        )
    if mode in {
        "input_changed",
        "input_changed_activation",
        "changed_after_home",
        "input_changed_bin_focus",
        "input_changed_down",
        "input_changed_reactivation",
    }:
        assert (
            result["diagnostics"]["stage"]
            == {
                "input_changed": "after_focus",
                "input_changed_activation": "after_activation",
                "changed_after_home": "after_home",
                "input_changed_bin_focus": "after_bin_focus",
                "input_changed_down": "after_down",
                "input_changed_reactivation": "after_bin_reactivation",
            }[mode]
        )
        assert result["diagnostics"]["tick_before"] == 123
        assert result["diagnostics"]["home_posted"] is (
            mode in {"changed_after_home", "input_changed_down"}
        )


def _write_candidate(
    path: Path, *, name: str = "Existing", identity: str = "id"
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "project.xml",
            f'<SM_Project DbId="{identity}"><ProjectName>{name}</ProjectName>'
            "<Config/></SM_Project>",
        )


@pytest.mark.parametrize(
    "name,identity", [("Existing", "id"), ("Other", "id"), ("Existing", "other")]
)
def test_candidate_drp_identity_requires_both_exact_name_and_uuid(
    tmp_path: Path,
    name: str,
    identity: str,
) -> None:
    candidate = tmp_path / "candidate.drp"
    _write_candidate(candidate, name=name, identity=identity)
    if (name, identity) != ("Existing", "id"):
        with pytest.raises(
            WindowsProjectManagerError, match="CANDIDATE_PROJECT_MISMATCH"
        ):
            module._candidate_export_proof(
                candidate, project_name="Existing", expected_project_id="id"
            )
    else:
        proof = module._candidate_export_proof(
            candidate, project_name="Existing", expected_project_id="id"
        )
        assert proof["project"] == {"name": "Existing", "id": "id"}
        assert proof["size"] == candidate.stat().st_size
        assert len(proof["sha256"]) == 64
        assert proof["native_connection_verified"] is False


def test_candidate_changed_after_identity_parse_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate.drp"
    _write_candidate(candidate)
    reader = read_project_snapshot

    def changed(path: Path) -> dict[str, str]:
        identity = reader(path)
        path.write_bytes(b"replaced after identity parse")
        return identity

    monkeypatch.setattr(module, "read_project_snapshot", changed)
    with pytest.raises(WindowsProjectManagerError, match="CANDIDATE_EXPORT_CHANGED"):
        module._candidate_export_proof(
            candidate, project_name="Existing", expected_project_id="id"
        )


@pytest.fixture
def composite(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[WindowsProjectManagerAdapter, list[str], dict[str, Any]]:
    calls: list[str] = []
    state: dict[str, Any] = {"clock": 100.0, "catalogue_reads": 0}
    monkeypatch.setattr(
        "providers.resolve.windows_project_manager.time.monotonic",
        lambda: state["clock"],
    )

    def catalogue(**arguments: Any) -> dict[str, Any]:
        state["catalogue_reads"] += 1
        changed = state.get("catalogue_changed") and state["catalogue_reads"] > 1
        return {"catalogue_signature": "changed" if changed else "stable"}

    monkeypatch.setattr(module, "verify_project_library_target", catalogue)

    def phase(operation: str) -> Any:
        def perform(self: Any, token: Any, **arguments: Any) -> dict[str, Any]:
            calls.append(operation)
            if state.get("failure") == operation:
                error = WindowsProjectManagerError(
                    "FIXTURE_FAILURE",
                    uncertain=True,
                    operation=operation,
                )
                error.diagnostics = dict(state.get("failure_diagnostics", {}))
                raise error
            return {"dialog": {"ownership_token": "a" * 32}}

        return perform

    for operation in (
        "begin_candidate_search",
        "filter_candidate",
        "select_filtered_candidate",
        "begin_candidate_export",
    ):
        monkeypatch.setattr(WindowsProjectManagerAdapter, operation, phase(operation))

    def save(
        self: Any, token: Any, *, exported: Any, destination: Path
    ) -> dict[str, Any]:
        calls.append("save")
        if state.get("failure") == "save":
            raise WindowsProjectManagerError(
                "FIXTURE_FAILURE",
                uncertain=True,
                operation="begin_candidate_export",
            )
        _write_candidate(
            destination,
            name=state.get("candidate_name", "Existing"),
            identity=state.get("candidate_id", "test-project-id"),
        )
        if state.get("expire_during_save"):
            state["clock"] += 1000
        return {
            "project_open_requested": False,
            "candidate_export_saved": True,
            "destination": str(destination),
            "ownership_token": exported["dialog"]["ownership_token"],
            "last_input_tick": 123,
        }

    def dispatch(self: Any, token: Any, **arguments: Any) -> dict[str, Any]:
        calls.append("open")
        state["dispatch_arguments"] = arguments
        if state.get("failure") == "open":
            raise WindowsProjectManagerError("FIXTURE_FAILURE", uncertain=True)
        return {
            "state": "open_requested",
            "dispatch_started": True,
            "native_connection_verified": False,
        }

    monkeypatch.setattr(WindowsProjectManagerAdapter, "_save_candidate_export", save)

    def export_atomic(self: Any, token: Any, *, destination: Path) -> dict[str, Any]:
        begun = self.begin_candidate_export(token)
        return save(self, token, exported=begun, destination=destination)

    monkeypatch.setattr(
        WindowsProjectManagerAdapter, "_export_candidate", export_atomic
    )
    monkeypatch.setattr(
        WindowsProjectManagerAdapter, "_dispatch_verified_open", dispatch
    )
    return (
        WindowsProjectManagerAdapter(
            enabled=True, evidence_directory=tmp_path / "evidence"
        ),
        calls,
        state,
    )


def _open_composite(adapter: WindowsProjectManagerAdapter) -> dict[str, Any]:
    return adapter.open_existing(
        process(),
        library_name="Local Database",
        folder_path=[],
        project_name="Existing",
        expected_project_id="test-project-id",
        expected_database={"DbType": "Disk", "DbName": "Local Database"},
        timeout_seconds=60,
    )


def test_composite_requires_export_identity_and_fresh_catalogue_before_one_open(
    composite: tuple[WindowsProjectManagerAdapter, list[str], dict[str, Any]],
) -> None:
    adapter, calls, state = composite
    result = _open_composite(adapter)
    assert calls == [
        "begin_candidate_search",
        "filter_candidate",
        "select_filtered_candidate",
        "begin_candidate_export",
        "save",
        "open",
    ]
    assert result["candidate_export_proof"]["project"] == {
        "name": "Existing",
        "id": "test-project-id",
    }
    assert state["dispatch_arguments"]["last_input_tick"] == 123
    assert state["catalogue_reads"] == 2
    assert result["native_connection_verified"] is False


@pytest.mark.parametrize(
    "phase",
    ["filter_candidate", "select_filtered_candidate", "begin_candidate_export", "save"],
)
def test_preflight_uncertainty_never_becomes_project_open_uncertainty(
    composite: tuple[WindowsProjectManagerAdapter, list[str], dict[str, Any]],
    phase: str,
) -> None:
    adapter, calls, state = composite
    state["failure"] = phase
    with pytest.raises(WindowsProjectManagerError, match="FIXTURE_FAILURE") as error:
        _open_composite(adapter)
    assert error.value.operation == "open_existing"
    assert not error.value.uncertain
    assert not error.value.project_open_requested
    assert error.value.candidate_export_uncertain is (
        phase in {"begin_candidate_export", "save"}
    )
    assert "open" not in calls


@pytest.mark.parametrize(
    "change,code",
    [
        ({"candidate_id": "wrong"}, "CANDIDATE_PROJECT_MISMATCH"),
        ({"candidate_name": "Other"}, "CANDIDATE_PROJECT_MISMATCH"),
        ({"catalogue_changed": True}, "CATALOGUE_CHANGED_DURING_PREFLIGHT"),
        ({"expire_during_save": True}, "TIMEOUT"),
    ],
)
def test_preflight_mismatch_or_expiry_prevents_open(
    composite: tuple[WindowsProjectManagerAdapter, list[str], dict[str, Any]],
    change: dict[str, Any],
    code: str,
) -> None:
    adapter, calls, state = composite
    state.update(change)
    with pytest.raises(WindowsProjectManagerError, match=code) as error:
        _open_composite(adapter)
    assert not error.value.uncertain
    assert not error.value.project_open_requested
    assert "open" not in calls


def test_uncertain_final_open_is_preserved_without_retry(
    composite: tuple[WindowsProjectManagerAdapter, list[str], dict[str, Any]],
) -> None:
    adapter, calls, state = composite
    state["failure"] = "open"
    with pytest.raises(WindowsProjectManagerError) as error:
        _open_composite(adapter)
    assert error.value.uncertain and error.value.project_open_requested
    assert calls.count("open") == 1


def test_preflight_wrapper_preserves_numeric_foreground_stage_diagnostics(
    composite: tuple[WindowsProjectManagerAdapter, list[str], dict[str, Any]],
) -> None:
    adapter, calls, state = composite
    state["failure"] = "select_filtered_candidate"
    detail = {
        "stage": "after_bin_reactivation",
        "foreground_handle": 999,
        "foreground_pid": 456,
        "foreground_root_owner": 999,
    }
    state["failure_diagnostics"] = detail
    with pytest.raises(WindowsProjectManagerError) as error:
        _open_composite(adapter)
    assert error.value.diagnostics == {
        **detail,
        "preflight_operation": "select_filtered_candidate",
    }
    assert not error.value.uncertain and not error.value.project_open_requested
    assert "open" not in calls


@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "hash",
        "query",
        "input",
        "expired",
        "late_input",
        "late_expired",
        "invoke_failed",
    ],
)
def test_final_actor_checks_artifact_and_ui_fences_before_exactly_one_open(
    tmp_path: Path,
    mode: str,
) -> None:
    attempt = tmp_path / ("a" * 32)
    attempt.mkdir()
    candidate = attempt / "candidate.drp"
    _write_candidate(candidate)
    proof = module._candidate_export_proof(
        candidate, project_name="Existing", expected_project_id="id"
    )
    packet = {
        "expected_process": process(),
        "library_name": "Local Database",
        "project_name": "Existing",
        "last_input_tick": 123,
        "deadline_utc_ms": 10000,
        "candidate_export": {
            "path": str(candidate),
            "sha256": proof["sha256"],
            "size": proof["size"],
            "mtime_ns": proof["file_facts"][1],
        },
    }
    if mode == "hash":
        packet["candidate_export"]["sha256"] = "0" * 64  # type: ignore[index]
    if mode == "expired":
        packet["deadline_utc_ms"] = 999
    result = _offline(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;Add-Type -AssemblyName WindowsBase;
        $data=ConvertFrom-Json ([Console]::In.ReadToEnd());$script:Mode=$data.mode;
        $script:OpenDispatched=$false;$script:Clicks=0;$script:LookedUp=$false;
        $script:OperationDiagnostics=@{};
        function Assert-ProjectManagerContext {
            param($expected)return @{Manager='fixed'}
        }
        function Assert-ProjectManagerLocalMode {param($manager,$processId)}
        function Read-ProjectManagerSearchState {
            param($manager,$processId)
            return @{Value=if($script:Mode -ceq 'query'){'Other'}else{'Existing'}}
        }
        function Get-ProjectManagerInputTick {
            if($script:Mode -ceq 'input' -or
                ($script:Mode -ceq 'late_input' -and $script:LookedUp)){return 124}
            return 123
        }
        function Get-ProjectManagerUnixMilliseconds {
            if($script:Mode -ceq 'late_expired' -and $script:LookedUp){return 20000}
            return 1000
        }
        function Find-ProjectManagerActionButton {
            param($manager,$action,$processId)
            if($action -cne 'Open'){throw 'unexpected action'}
            $script:LookedUp=$true;$button=[pscustomobject]@{};
            $button|Add-Member ScriptMethod TryGetCurrentPattern {
                param($pattern,$output)$output.Value=[pscustomobject]@{};return $true
            };return $button
        }
        function Start-ProjectManagerOpenInvoke {
            param($pattern)$script:Clicks++;
            return @{Completed=$true;Succeeded=($script:Mode -cne 'invoke_failed');
                Error='fake'}
        }
        $failed=$false;$code='';$evidence=$null;
        try{$evidence=Open-ProjectManagerExisting $data.packet}
        catch{$failed=$true;$code=$_.Exception.Message}
        @{failed=$failed;code=$code;clicks=$script:Clicks;
          open_dispatched=$script:OpenDispatched;evidence=$evidence}|
            ConvertTo-Json -Depth 5 -Compress
    """,
        {"packet": packet, "mode": mode},
    )
    assert result["failed"] is (mode != "valid")
    assert result["clicks"] == (1 if mode in {"valid", "invoke_failed"} else 0)
    assert result["open_dispatched"] is (mode in {"valid", "invoke_failed"})
    if mode == "valid":
        assert result["evidence"]["native_connection_verified"] is False
        assert result["evidence"]["state"] == "open_requested"


@pytest.mark.parametrize("mode", ["save", "input", "cancel", "overwrite"])
def test_owned_export_completion_never_opens_or_overwrites_and_cancel_needs_no_input(
    tmp_path: Path,
    mode: str,
) -> None:
    attempt = tmp_path / ("a" * 32)
    attempt.mkdir()
    destination = attempt / "candidate.drp"
    if mode == "overwrite":
        destination.write_bytes(b"preserve")
    result = _offline(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;
        Add-Type -AssemblyName WindowsBase;
        $data=ConvertFrom-Json ([Console]::In.ReadToEnd());$script:Mode=$data.mode;
        $script:OpenDispatched=$false;$script:ExportTouched=$false;$script:Closed=$false;
        $script:Clicks=0;$script:Text='Existing.drp';
        $script:Request=[pscustomobject]@{operation=if($script:Mode -ceq 'cancel'){
            'cancel_candidate_export'}else{'save_candidate_export'};
            expected_process=$data.process;dialog=@{ownership_token=('a'*32);handle=789};
            last_input_tick=123;destination=$data.destination};
        function Get-ProjectManagerOwnedExport {
            param($request)return @{Manager='fixed';Dialog='fixed'}
        }
        function Get-ProjectManagerInputTick {
            if($script:Mode -cin @('input','cancel')){return 124};return 123
        }
        function Read-ProjectManagerExportFields {
            param($dialog,$processId)
            $pattern=[pscustomobject]@{};
            $pattern|Add-Member ScriptMethod SetValue {
                param($value)$script:Text=$value
            };
            return @{Value=$pattern;Text=$script:Text}
        }
        function Find-ProjectManagerExportControl {
            param($dialog,$kind,$processId)
            if($kind -cne $(if($script:Mode -ceq 'cancel'){'cancel'}else{'save'})){
                throw 'unexpected control'
            }
            $button=[pscustomobject]@{};
            $button|Add-Member ScriptMethod TryGetCurrentPattern {
                param($pattern,$output)$output.Value='fixed';return $true
            };return $button
        }
        function Start-ProjectManagerExportInvoke {
            param($pattern)$script:Clicks++;$script:Closed=$true;
            if($script:Mode -ceq 'save'){
                [IO.File]::WriteAllBytes($script:Request.destination,[byte[]]@(1,2,3))
            }
            return @{Completed=$true;Succeeded=$true;Error=''}
        }
        function Get-Process {param($Id)return 'fixed'}
        function Get-ProjectManagerProcess {
            param($process)return @{creation_time=$data.process.creation_time}
        }
        function Get-ProjectManagerWindowElements {param($manager,$processId)return @()}
        function Assert-ProjectManagerContext {
            param($expected)return @{Manager='fixed'}
        }
        $failed=$false;$evidence=$null;
        try{$evidence=Complete-ProjectManagerCandidateExport $script:Request}
        catch{$failed=$true}
        @{failed=$failed;clicks=$script:Clicks;open_dispatched=$script:OpenDispatched;
          evidence=$evidence}|ConvertTo-Json -Depth 4 -Compress
    """,
        {"mode": mode, "destination": str(destination), "process": process()},
    )
    assert result["failed"] is (mode in {"input", "overwrite"})
    assert result["clicks"] == (0 if mode in {"input", "overwrite"} else 1)
    assert not result["open_dispatched"]
    assert destination.exists() is (mode in {"save", "overwrite"})
    if mode == "overwrite":
        assert destination.read_bytes() == b"preserve"
    if mode not in {"input", "overwrite"}:
        assert result["evidence"]["project_open_requested"] is False


@pytest.mark.parametrize("mode", ["valid", "wrong_type", "address_toolbar"])
def test_export_fields_use_direct_filename_edit_and_selected_drp_virtual_item(
    mode: str,
) -> None:
    result = _offline(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;
        Add-Type -AssemblyName WindowsBase;
        $script:Mode=ConvertFrom-Json ([Console]::In.ReadToEnd());
        $script:Value=[pscustomobject]@{Current=[pscustomobject]@{Value='Existing.drp';IsReadOnly=$false}};
        $script:Edit=[pscustomobject]@{Current=[pscustomobject]@{ProcessId=123;
            ClassName=if($script:Mode -ceq 'address_toolbar'){
                'ToolbarWindow32'}else{'Edit'};
            ControlType=[System.Windows.Automation.ControlType]::Edit;
            IsEnabled=$true;IsOffscreen=$false}};
        $script:Edit|Add-Member ScriptMethod TryGetCurrentPattern {
            param($pattern,$output)$output.Value=$script:Value;return $true
        };
        $script:Filename=[pscustomobject]@{};
        $script:Filename|Add-Member ScriptMethod FindAll {
            param($scope,$condition)
            if($scope -ne [System.Windows.Automation.TreeScope]::Children){
                throw 'wrong scope'
            }
            return @($script:Edit)
        };
        $script:Filename|Add-Member ScriptMethod TryGetCurrentPattern {
            param($pattern,$output)$output.Value=$script:Value;return $true
        };
        $script:Selection=[pscustomobject]@{Current=[pscustomobject]@{}};
        $script:Selection.Current|Add-Member ScriptMethod GetSelection {
            return @([pscustomobject]@{Current=[pscustomobject]@{
                ProcessId=123;IsOffscreen=$true;
                ControlType=[System.Windows.Automation.ControlType]::ListItem;
                Name=if($script:Mode -ceq 'wrong_type'){
                    'All files'}else{'Export files (*.drp)'}}})
        };
        $script:Type=[pscustomobject]@{};
        $script:Type|Add-Member ScriptMethod TryGetCurrentPattern {
            param($pattern,$output)
            if($pattern -ne [System.Windows.Automation.SelectionPattern]::Pattern){
                throw 'wrong pattern'
            }
            $output.Value=$script:Selection;return $true
        };
        function Find-ProjectManagerExportControl {
            param($dialog,$kind,$processId)
            if($kind -ceq 'filename'){return $script:Filename};return $script:Type
        }
        $failed=$false;$text='';
        try{$fields=Read-ProjectManagerExportFields 'fixed' 123;$text=$fields.Text}
        catch{$failed=$true}
        @{failed=$failed;text=$text}|ConvertTo-Json -Compress
    """,
        mode,
    )
    assert result["failed"] is (mode != "valid")
    if mode == "valid":
        assert result["text"] == "Existing.drp"


@pytest.mark.parametrize("mode", ["saved", "invoke_failed"])
def test_atomic_export_retains_original_invoker_until_save_completes(
    tmp_path: Path, mode: str
) -> None:
    attempt = tmp_path / ("a" * 32)
    attempt.mkdir()
    result = _offline(
        r"""
        $data=ConvertFrom-Json ([Console]::In.ReadToEnd());$script:Mode=$data.mode;
        $script:OpenDispatched=$false;$script:Saves=0;$script:AliveAtSave=$false;
        function Begin-ProjectManagerCandidateExport {
            param($request,$includeTree)
            if($includeTree){throw 'unneeded tree'}
            $script:ActiveExportDispatch=@{Completed=$false;Succeeded=$false;Error=''};
            return @{dialog=@{ownership_token=('a'*32)};last_input_tick=123}
        }
        function Complete-ProjectManagerCandidateExport {
            param($request)$script:Saves++;
            $script:AliveAtSave=-not $script:ActiveExportDispatch.Completed;
            $script:ActiveExportDispatch.Completed=$true;
            $script:ActiveExportDispatch.Succeeded=$script:Mode -ceq 'saved';
            return @{operation='save_candidate_export';project_open_requested=$false}
        }
        $failed=$false;$evidence=$null;
        try{$evidence=Export-ProjectManagerCandidate ([pscustomobject]@{
            expected_process=$data.process;ownership_token=('a'*32);destination=$data.destination})}
        catch{$failed=$true}
        @{failed=$failed;saves=$script:Saves;alive_at_save=$script:AliveAtSave;
          open_dispatched=$script:OpenDispatched;evidence=$evidence}|
            ConvertTo-Json -Depth 4 -Compress
    """,
        {
            "mode": mode,
            "destination": str(attempt / "candidate.drp"),
            "process": process(),
        },
    )
    assert result["failed"] is (mode != "saved")
    assert result["saves"] == 1 and result["alive_at_save"] is True
    assert not result["open_dispatched"]
    if mode == "saved":
        assert result["evidence"]["operation"] == "export_candidate"


def test_orphan_recovery_derives_filename_from_previous_native_witness_only(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, state = driver
    state["echo_export"] = True
    adapter = WindowsProjectManagerAdapter(enabled=True)
    previous = adapter.begin_candidate_export(process())
    previous["dialog"].update(
        title="Export Project File", class_name="#32770", automation_id=""
    )
    previous["dialog_tree"] = [
        node(),
        node(
            id=1,
            parent=0,
            depth=1,
            automation_id="FileNameControlHost",
            class_name="AppControlHost",
            control_type="ControlType.ComboBox",
            value="Existing.drp",
        ),
        node(
            id=2,
            parent=1,
            depth=2,
            automation_id="1001",
            class_name="Edit",
            control_type="ControlType.Edit",
            value="Existing.drp",
        ),
    ]
    state["echo_export"] = False
    state["result"] = {
        "ok": True,
        "operation": "cancel_orphaned_probe_export",
        "backend": "windows_project_manager",
        "state": "orphaned_probe_export_cancelled",
        "eligible": True,
        "process": process(),
        "project_state": "unverified",
        "native_connection_verified": False,
        "dispatch_started": False,
        "project_open_requested": False,
        "orphan_cancelled": True,
        "orphan_witness": {
            "verification": "explicit_unowned_probe_recovery",
            "previous_dialog_handle": 789,
            "previous_ownership_token": previous["dialog"]["ownership_token"],
            "recovered_dialog_handle": 999,
            "recovered_runtime_id": [42, 999],
            "owner_handles": [],
            "default_filename": "Existing.drp",
        },
    }
    result = adapter.cancel_orphaned_probe_export(process(), previous_exported=previous)
    request = json.loads(calls[-1]["input"])
    assert request["expected_default_filename"] == "Existing.drp"
    assert request["previous_dialog"] == previous["dialog"]
    assert (
        result["orphan_witness"]["recovered_dialog_handle"]
        != previous["dialog"]["handle"]
    )
    assert not result["project_open_requested"]


def test_orphan_actor_invokes_only_cancel_and_labels_a_new_unowned_witness() -> None:
    result = _offline(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;
        Add-Type -AssemblyName WindowsBase;
        $data=ConvertFrom-Json ([Console]::In.ReadToEnd());
        $script:Clicks=0;$script:OpenDispatched=$false;
        $script:Dialog=[pscustomobject]@{Current=[pscustomobject]@{NativeWindowHandle=999}};
        $script:Dialog|Add-Member ScriptMethod GetRuntimeId {return [int[]]@(42,999)};
        function Get-ProjectManagerOrphanedProbe {
            param($request)return @{Manager='fixed';Dialog=$script:Dialog}
        }
        function Find-ProjectManagerExportControl {
            param($dialog,$kind,$processId)
            if($kind -cne 'cancel'){throw 'unexpected action'}
            $button=[pscustomobject]@{};
            $button|Add-Member ScriptMethod TryGetCurrentPattern {
                param($pattern,$output)$output.Value='fixed';return $true
            };return $button
        }
        function Start-ProjectManagerExportInvoke {
            param($pattern)$script:Clicks++;
            return @{Completed=$true;Succeeded=$true;Error=''}
        }
        function Get-Process {param($Id)return 'fixed'}
        function Get-ProjectManagerProcess {
            param($process)return @{creation_time=$data.creation_time}
        }
        function Get-ProjectManagerWindowElements {param($manager,$processId)return @()}
        function Assert-ProjectManagerContext {
            param($expected)return @{Manager='fixed'}
        }
        $evidence=Cancel-ProjectManagerOrphanedProbe ([pscustomobject]@{
            expected_process=$data;previous_dialog=@{handle=789;ownership_token=('a'*32)};
            expected_default_filename='Existing.drp'});
        @{clicks=$script:Clicks;open_dispatched=$script:OpenDispatched;evidence=$evidence}|
            ConvertTo-Json -Depth 6 -Compress
    """,
        process(),
    )
    assert result["clicks"] == 1 and not result["open_dispatched"]
    assert (
        result["evidence"]["orphan_witness"]["verification"]
        == "explicit_unowned_probe_recovery"
    )
    assert result["evidence"]["orphan_witness"]["owner_handles"] == []


def test_managed_uia_selection_and_table_contracts_match_installed_api_without_ui() -> (
    None
):
    result = _offline(r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes;
        $selection=[System.Windows.Automation.SelectionPattern];
        $information=$selection.GetProperty('Current').PropertyType;
        $flags=[Reflection.BindingFlags]'Public,Instance,DeclaredOnly';
        $table=[System.Windows.Automation.TablePattern].GetProperty('Current',$flags).PropertyType;
        $selected=$information.GetMethod('GetSelection',[Type[]]@());
        $missing=$selection.GetMethod('GetCurrentSelection',[Type[]]@());
        $rows=$table.GetMethod('GetRowHeaders',[Type[]]@());
        $columns=$table.GetMethod('GetColumnHeaders',[Type[]]@());
        @{selection_info_get_selection=($null -ne $selected);
          nonexistent_get_current_selection=($null -ne $missing);
          table_info_row_headers=($null -ne $rows);
          table_info_column_headers=($null -ne $columns)}|
            ConvertTo-Json -Compress
    """)
    assert result == {
        "selection_info_get_selection": True,
        "nonexistent_get_current_selection": False,
        "table_info_row_headers": True,
        "table_info_column_headers": True,
    }


def test_generic_failure_has_bounded_private_managed_exception_evidence() -> None:
    result = _offline(r"""
        try{throw [InvalidOperationException]::new(('x'*2000))}
        catch{Get-ProjectManagerExceptionDiagnostics $_|ConvertTo-Json -Compress}
    """)
    assert result["exception_type"] == "System.InvalidOperationException"
    assert result["exception_message"] == "x" * 384
    assert isinstance(result["function"], str) and len(result["function"]) <= 128
    assert isinstance(result["line"], int) and result["line"] > 0


@pytest.mark.parametrize("tick", [12345, True, -1, 2**32, "12345"])
def test_atomic_export_validates_actor_baseline_without_a_caller_tick(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    tmp_path: Path,
    tick: Any,
) -> None:
    calls, state = driver
    state.update(echo_atomic=True, atomic_tick=tick)
    adapter = WindowsProjectManagerAdapter(
        enabled=True, evidence_directory=tmp_path / "private"
    )
    if type(tick) is int and 0 <= tick <= 0xFFFFFFFF:
        result = adapter.export_candidate(process())
        assert result["last_input_tick"] == tick
        assert (
            not result["project_open_requested"]
            and not result["native_connection_verified"]
        )
    else:
        with pytest.raises(
            WindowsProjectManagerError, match="UNVERIFIED_RESPONSE"
        ) as error:
            adapter.export_candidate(process())
        assert error.value.diagnostics["validation_exception"] == "ValueError"
        assert "last_input_tick" not in error.value.diagnostics["payload_fields"]
        assert "last_input_tick" in error.value.diagnostics["result_fields"]
    payload = json.loads(calls[0]["input"])
    assert set(payload) == {
        "operation",
        "expected_process",
        "ownership_token",
        "destination",
    }
    assert "last_input_tick" not in payload


@pytest.mark.parametrize("environment", ["missing", "wrong"])
def test_official_executable_uses_os_known_folder_without_programfiles_environment(
    environment: str,
) -> None:
    result = _offline(
        r"""
        $mode=ConvertFrom-Json ([Console]::In.ReadToEnd());
        if($mode -ceq 'missing'){
            [Environment]::SetEnvironmentVariable('ProgramFiles',$null,'Process');
            [Environment]::SetEnvironmentVariable('ProgramW6432',$null,'Process');
        }else{
            $env:ProgramFiles='C:\untrusted-program-files';
            $env:ProgramW6432='C:\untrusted-program-files-x64';
        }
        $known=[Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles);
        $relative='Blackmagic Design\DaVinci Resolve\Resolve.exe';
        $expected=[IO.Path]::GetFullPath((Join-Path $known $relative));
        @{actual=(Get-ProjectManagerExecutable);expected=$expected;
          missing_env=([string]::IsNullOrEmpty($env:ProgramFiles) -and
            [string]::IsNullOrEmpty($env:ProgramW6432))}|ConvertTo-Json -Compress
    """,
        environment,
    )
    assert result["actual"] == result["expected"]
    assert "untrusted-program-files" not in result["actual"]
    assert result["missing_env"] is (environment == "missing")
