"""Offline bootstrap boundary checks; no Resolve discovery or UI is performed.

Subprocess adapter tests replace the driver entirely. PowerShell tests load only
function definitions and use synthetic objects, never the executable entrypoint.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import ntpath
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from providers.resolve import windows_bootstrap as module
from providers.resolve.marker_transport import prepare_marker
from providers.resolve.startup_diagnostics import (
    MAX_SCRIPT_BYTES,
    MENU_FILE,
    MENU_HEADER,
)
from providers.resolve.windows_bootstrap import (
    WindowsBootstrapAdapter,
    WindowsBootstrapError,
)

ENTRY = Path(module.__file__).with_name("ResolveBootstrapUI.ps1")
POWERSHELL = shutil.which("powershell.exe")


@pytest.mark.parametrize("environment", ["missing", "wrong"])
def test_python_official_path_uses_injectable_os_folder_not_environment(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    monkeypatch.setattr(module, "_known_program_files", lambda: r"D:\OS Program Files")
    for key in ("PROGRAMFILES", "PROGRAMW6432"):
        if environment == "missing":
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, r"C:\caller-controlled")
    assert module._official_executable() == (
        r"D:\OS Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
    )


@pytest.mark.parametrize("environment", ["missing", "wrong"])
def test_bootstrap_ps_official_path_ignores_filtered_or_wrong_environment(
    environment: str,
) -> None:
    result = _offline_powershell(
        r"""
        $mode=ConvertFrom-Json ([Console]::In.ReadToEnd());
        if($mode -ceq 'missing'){
            [Environment]::SetEnvironmentVariable('ProgramFiles',$null,'Process');
            [Environment]::SetEnvironmentVariable('ProgramW6432',$null,'Process');
        }else{
            $env:ProgramFiles='C:\untrusted-program-files';
            $env:ProgramW6432='C:\untrusted-program-files-x64';
        }
        $folder=[Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles);
        $relative='Blackmagic Design\DaVinci Resolve\Resolve.exe';
        $expected=[IO.Path]::GetFullPath((Join-Path $folder $relative));
        @{actual=(Get-BootstrapOfficialExecutable);expected=$expected}|
            ConvertTo-Json -Compress
    """,
        environment,
    )
    assert result["actual"] == result["expected"]
    assert "untrusted-program-files" not in result["actual"]


@pytest.mark.skipif(os.name != "nt", reason="Windows OS metadata only")
@pytest.mark.parametrize("environment", ["missing", "wrong"])
def test_native_python_known_folder_matches_64bit_powershell_without_ui(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    for key in ("PROGRAMFILES", "PROGRAMW6432"):
        if environment == "missing":
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, r"C:\untrusted-root")
    expected = _offline_powershell(r"""
        [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)|
            ConvertTo-Json -Compress
    """)
    assert ntpath.normcase(module._known_program_files()) == ntpath.normcase(expected)


def process(**changes: Any) -> dict[str, Any]:
    """Create fixed synthetic process/UI evidence, without contacting Windows."""
    return {
        "pid": 123,
        "creation_time": "639288864000000000",
        "executable_path": (
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
        ),
        "window_handle": 456,
        "window_title": "DaVinci Resolve - Проєкт",
        "project_name_hint": "Проєкт",
        "window_state": "normal",
        **changes,
    }


def response(operation: str = "inspect", **changes: Any) -> dict[str, Any]:
    """Return a complete evidence schema; it never claims a native connection."""
    result = {
        "ok": True,
        "operation": operation,
        "backend": "windows_bootstrap",
        "state": "project_window",
        "eligible": True,
        "process": process(),
        "project_state": "unverified",
        "native_connection_verified": False,
        "dispatch_started": False,
        **changes,
    }
    if operation == "inspect":
        result.update(window_candidates=[], window_candidates_truncated=False)
    return result


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Prepare source and its fixed menu copy without any native API or import."""
    root = tmp_path / "runtime"
    source = prepare_marker(root)
    appdata = tmp_path / "AppData"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("ProgramW6432", r"C:\Program Files")
    menu = (
        appdata
        / ("Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Utility")
        / MENU_FILE
    )
    menu.parent.mkdir(parents=True)
    # The normal Windows installer may use CRLF while the source uses LF.
    text = MENU_HEADER + source.read_text(encoding="utf-8")
    menu.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    return root, menu


@pytest.fixture
def driver(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Intercept every subprocess call, even when tests run on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(module, "_known_program_files", lambda: r"C:\Program Files")
    monkeypatch.setenv("ProgramW6432", r"C:\Program Files")
    calls: list[dict[str, Any]] = []
    state: dict[str, Any] = {"returncode": 0}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        error = state.get("error")
        if isinstance(error, Exception):
            raise error
        payload = json.loads(kwargs["input"])
        result = response()
        if payload["operation"] == "invoke_managed_marker":
            target = copy.deepcopy(payload["expected_process"])
            restored = target["window_state"] == "minimized"
            if restored:
                target["window_state"] = "normal"
            result = response(
                "invoke_managed_marker",
                state="dispatch_requested",
                process=target,
                dispatch_started=True,
                script_sha256=payload["expected_sha256"],
                menu_path=["Workspace", "Scripts", "Utility", "Resolve Agent Marker"],
                minimized_restored=restored,
            )
        result.update(state.get("changes", {}))
        result = state.get("result", result)
        output = state.get("stdout", json.dumps(result, ensure_ascii=False))
        return subprocess.CompletedProcess(command, state["returncode"], output, "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls, state


def test_disabled_adapter_does_not_start_driver_or_read_script(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    tmp_path: Path,
) -> None:
    calls, _ = driver
    adapter = WindowsBootstrapAdapter()
    assert adapter.capabilities()["available"] is False
    for action in (
        adapter.inspect,
        lambda: adapter.invoke_managed_marker(tmp_path, {}),
    ):
        with pytest.raises(WindowsBootstrapError, match="OPT_IN_REQUIRED") as error:
            action()
        assert error.value.uncertain is False
    assert calls == []


def test_non_windows_does_not_launch_a_driver(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = driver
    monkeypatch.setattr(sys, "platform", "linux")
    adapter = WindowsBootstrapAdapter(enabled=True)
    assert not adapter.capabilities()["available"]
    with pytest.raises(WindowsBootstrapError, match="WINDOWS_REQUIRED"):
        adapter.inspect()
    assert calls == []


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), 121])
def test_timeout_is_bounded(value: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        WindowsBootstrapAdapter(enabled=True, timeout_seconds=value)


def test_inspection_uses_only_fixed_driver_and_data(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    calls, _ = driver
    result = WindowsBootstrapAdapter(enabled=True).inspect()
    assert result["native_connection_verified"] is False
    assert result["project_state"] == "unverified"
    assert result["process"]["creation_time"] == process()["creation_time"]
    assert len(calls) == 1
    call = calls[0]
    assert Path(call["command"][-1]).name == "ResolveBootstrapUI.ps1"
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
    assert call["encoding"] == "utf-8"
    assert call["timeout"] == 45


def test_dispatch_verifies_newline_normalized_source_and_sends_raw_sha_only(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    root, menu = runtime
    calls, _ = driver
    result = WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(
        root, process()
    )
    payload = json.loads(calls[0]["input"])
    assert set(payload) == {"operation", "expected_process", "expected_sha256"}
    assert payload["expected_sha256"] == hashlib.sha256(menu.read_bytes()).hexdigest()
    assert str(root) not in calls[0]["input"]
    assert "Проєкт" not in " ".join(calls[0]["command"])
    assert result["state"] == "dispatch_requested"
    assert result["native_connection_verified"] is False


def test_fixed_flattened_menu_and_minimized_restore_are_accepted(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, state = driver
    state["changes"] = {"menu_path": ["Workspace", "Scripts", "Resolve Agent Marker"]}
    result = WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(
        runtime[0],
        process(window_state="minimized"),
    )
    assert result["minimized_restored"] is True
    assert result["process"]["window_state"] == "normal"


@pytest.mark.parametrize("kind", ["missing", "unmanaged", "changed", "large"])
def test_wrong_installed_script_cannot_dispatch(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    kind: str,
) -> None:
    root, menu = runtime
    calls, _ = driver
    if kind == "missing":
        menu.unlink()
    elif kind == "unmanaged":
        menu.write_text("print('foreign')", encoding="utf-8")
    elif kind == "changed":
        menu.write_bytes(menu.read_bytes() + b"\nprint('changed')\n")
    else:
        menu.write_bytes(b"x" * (MAX_SCRIPT_BYTES + 1))
    with pytest.raises(WindowsBootstrapError) as error:
        WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(root, process())
    assert not error.value.uncertain
    assert calls == []


@pytest.mark.parametrize("name", ["marker-session-consumed.drp", "stopped.json"])
def test_consumed_session_is_never_invoked(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    name: str,
) -> None:
    root, _ = runtime
    calls, _ = driver
    (root / name).touch()
    with pytest.raises(WindowsBootstrapError, match="SESSION_ALREADY_CONSUMED"):
        WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(root, process())
    assert calls == []


def test_prepared_source_session_mismatch_cannot_dispatch(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    root, _ = runtime
    calls, _ = driver
    metadata = json.loads((root / "session.json").read_text())
    metadata["session"] = "0" * 32
    (root / "session.json").write_text(json.dumps(metadata))
    with pytest.raises(WindowsBootstrapError, match="MANAGED_SCRIPT_UNVERIFIED"):
        WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(root, process())
    assert calls == []


def test_linked_menu_source_is_refused(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    root, menu = runtime
    calls, _ = driver
    retained = menu.with_suffix(".retained")
    menu.rename(retained)
    try:
        menu.symlink_to(retained)
    except OSError:
        pytest.skip("Creating symlinks is not enabled on this host")
    with pytest.raises(WindowsBootstrapError, match="MANAGED_SCRIPT_UNVERIFIED"):
        WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(root, process())
    assert calls == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("pid", True),
        ("creation_time", "0"),
        ("creation_time", "639288864000000000\n"),
        ("executable_path", r"C:\Temp\Resolve.exe"),
        ("window_handle", 0),
        ("project_name_hint", "Other"),
        ("window_state", "unknown"),
        ("selector", "Workspace"),
    ],
)
def test_bad_process_tokens_cannot_dispatch(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    key: str,
    value: Any,
) -> None:
    calls, _ = driver
    with pytest.raises(ValueError, match="process evidence"):
        WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(
            runtime[0],
            process(**{key: value}),
        )
    assert calls == []


@pytest.mark.parametrize(
    "title",
    ["Project Manager", "DaVinci Resolve", "DaVinci Resolve - Untitled Project"],
)
def test_project_manager_or_placeholder_is_not_an_eligible_native_project(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    title: str,
) -> None:
    calls, _ = driver
    with pytest.raises(ValueError):
        WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(
            runtime[0],
            process(window_title=title, project_name_hint=None),
        )
    assert calls == []


@pytest.mark.parametrize(
    "state_name",
    [
        "resolve_not_running",
        "desktop_unavailable",
        "process_ambiguous",
    ],
)
def test_inspection_distinguishes_unavailable_from_an_eligible_project(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    state_name: str,
) -> None:
    _, state = driver
    state["result"] = response(state=state_name, eligible=False, process=None)
    result = WindowsBootstrapAdapter(enabled=True).inspect()
    assert not result["eligible"]
    assert result["process"] is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("native_connection_verified", True),
        ("project_state", "verified"),
        ("dispatch_started", True),
        ("state", "connected"),
        ("eligible", 1),
        ("process", process(executable_path=r"C:\Temp\Resolve.exe")),
        ("connected", True),
    ],
)
def test_inspection_does_not_trust_invalid_process_or_native_claims(
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    key: str,
    value: Any,
) -> None:
    _, state = driver
    state["changes"] = {key: value}
    with pytest.raises(WindowsBootstrapError, match="UNVERIFIED_RESPONSE") as error:
        WindowsBootstrapAdapter(enabled=True).inspect()
    assert not error.value.uncertain


@pytest.mark.parametrize(
    "changes",
    [
        {"process": process(pid=999)},
        {"process": process(creation_time="639288864000000001")},
        {"script_sha256": "0" * 64},
        {"dispatch_started": False},
        {"menu_path": ["File", "Other"]},
        {"native_connection_verified": True},
        {"minimized_restored": True},
    ],
)
def test_dispatch_response_requires_original_process_hash_and_fixed_menu(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    changes: dict[str, Any],
) -> None:
    _, state = driver
    state["changes"] = changes
    with pytest.raises(WindowsBootstrapError, match="UNVERIFIED_RESPONSE") as error:
        WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(
            runtime[0], process()
        )
    assert error.value.uncertain


@pytest.mark.parametrize("uncertain", [False, True])
def test_driver_errors_preserve_before_or_after_dispatch_uncertainty(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    uncertain: bool,
) -> None:
    calls, state = driver
    state.update(
        returncode=1,
        result={
            "ok": False,
            "error": {
                "code": "MENU_ITEM_UNAVAILABLE",
                "uncertain": uncertain,
                "phase": "after_dispatch" if uncertain else "before_dispatch",
            },
        },
    )
    with pytest.raises(WindowsBootstrapError, match="MENU_ITEM_UNAVAILABLE") as error:
        WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(
            runtime[0], process()
        )
    assert error.value.uncertain is uncertain
    assert len(calls) == 1


@pytest.mark.parametrize(
    "failure", ["timeout", "invalid", "duplicate", "phase", "large"]
)
def test_ambiguous_dispatch_is_never_retried(
    runtime: tuple[Path, Path],
    driver: tuple[list[dict[str, Any]], dict[str, Any]],
    failure: str,
) -> None:
    calls, state = driver
    if failure == "timeout":
        state["error"] = subprocess.TimeoutExpired("driver", 45)
    elif failure == "invalid":
        state["stdout"] = "not JSON"
    elif failure == "duplicate":
        state["stdout"] = '{"ok":true,"ok":false}'
    elif failure == "large":
        state["stdout"] = "x" * 65537
    else:
        state["result"] = {
            "ok": False,
            "error": {
                "code": "FAILED",
                "uncertain": False,
                "phase": "after_dispatch",
            },
        }
    with pytest.raises(WindowsBootstrapError) as error:
        WindowsBootstrapAdapter(enabled=True).invoke_managed_marker(
            runtime[0], process()
        )
    assert error.value.uncertain
    assert len(calls) == 1


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _offline_powershell(script: str, payload: Any = None) -> Any:
    """Load only function ASTs; the driver entrypoint/UI initialization never runs."""
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is required for offline driver checks")
    assert POWERSHELL is not None
    bootstrap = (
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest; "
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        "[Console]::InputEncoding=[Text.UTF8Encoding]::new($false); "
        "$tokens=$null; $errors=$null; "
        f"$ast=[Management.Automation.Language.Parser]::ParseFile({_ps_quote(str(ENTRY))},"
        "[ref]$tokens,[ref]$errors); "
        "if($errors.Count -gt 0) { throw ($errors|Out-String) }; "
        "$definitions=@($ast.FindAll({param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)); "
        '$functions=($definitions|ForEach-Object {$_.Extent.Text}) -join "`n"; '
        ". ([scriptblock]::Create($functions)); "
    )
    encoded = base64.b64encode((bootstrap + script).encode("utf-16-le")).decode()
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
        check=False,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.lstrip("\ufeff"))


def test_driver_parses_without_running_its_entrypoint() -> None:
    result = _offline_powershell(
        "@{functions=$definitions.Count;errors=$errors.Count}|ConvertTo-Json -Compress",
    )
    assert result["errors"] == 0
    assert result["functions"] >= 15


@pytest.mark.parametrize(
    "mode", ["nested", "automation-id", "duplicate", "ambiguous", "foreign", "group"]
)
def test_synthetic_discovery_finds_only_unique_editor_identity(mode: str) -> None:
    result = _offline_powershell(
        r"""
        Add-Type -AssemblyName UIAutomationClient;
        Add-Type -AssemblyName UIAutomationTypes; Add-Type -AssemblyName WindowsBase;
        $mode=ConvertFrom-Json ([Console]::In.ReadToEnd());
        $script:WindowCandidates=@(); $script:WindowCandidatesTruncated=$false;
        function New-Element {
            param($Id,$Class,$AutomationId,$Handle,$PidValue,$ControlType,$Children)
            $item=[pscustomobject]@{Identity=$Id;Children=@($Children);Current=[pscustomobject]@{
                ProcessId=$PidValue; ClassName=$Class; AutomationId=$AutomationId;
                Name='DaVinci Resolve - Example'; NativeWindowHandle=$Handle;
                ControlType=$ControlType; IsOffscreen=$false
            }};
            $item|Add-Member ScriptMethod GetRuntimeId {
                return [int[]]@($this.Identity)
            };
            $item|Add-Member ScriptMethod FindAll {
                param($scope,$condition) return $this.Children
            };
            return $item
        }
        $window=[System.Windows.Automation.ControlType]::Window;
        $editor=New-Element 2 'UiMainWindowImp' '' 456 123 $window @();
        if($mode -ceq 'automation-id') {
            $editor.Current.ClassName='QtWindow';
            $editor.Current.AutomationId='UiMainWindow'
        }
        if($mode -ceq 'foreign') { $editor.Current.ProcessId=999 }
        if($mode -ceq 'group') {
            $editor.Current.ControlType=[System.Windows.Automation.ControlType]::Group
        }
        $generic=New-Element 1 'QtContainer' '' 111 123 $window @($editor);
        $roots=@($generic);
        if($mode -ceq 'duplicate') { $roots+=,$editor }
        if($mode -ceq 'ambiguous') {
            $roots+=,(New-Element 3 'UiMainWindow' '' 789 123 $window @())
        }
        $failed=$false; $handle=$null;
        try {
            $found=Find-BootstrapMainInRoots $roots 123;
            if($null -ne $found) { $handle=$found.Current.NativeWindowHandle }
        }
        catch { $failed=$true }
        @{failed=$failed;handle=$handle;candidates=@($script:WindowCandidates)}|
            ConvertTo-Json -Depth 5 -Compress
    """,
        mode,
    )
    assert result["failed"] is (mode == "ambiguous")
    assert result["handle"] == (
        456 if mode in {"nested", "automation-id", "duplicate"} else None
    )
    assert len(result["candidates"]) >= 2


@pytest.mark.parametrize("matches", [True, False])
def test_driver_rechecks_only_fixed_menu_file_hash(
    runtime: tuple[Path, Path],
    matches: bool,
) -> None:
    _, menu = runtime
    digest = hashlib.sha256(menu.read_bytes()).hexdigest()
    result = _offline_powershell(
        "$data=ConvertFrom-Json ([Console]::In.ReadToEnd()); "
        "$env:APPDATA=$data.appdata; $script:ExpectedSha256=$data.digest; "
        "$failed=$false; $hash=$null; try { $hash=Get-BootstrapScriptHash } "
        "catch { $failed=$true }; "
        "@{failed=$failed;hash=$hash}|ConvertTo-Json -Compress",
        {"appdata": str(menu.parents[6]), "digest": digest if matches else "0" * 64},
    )
    assert result["failed"] is not matches
    assert result["hash"] == (digest if matches else None)


def test_driver_request_schema_rejects_generic_inputs_and_duplicate_keys() -> None:
    token = process()
    valid: list[dict[str, Any]] = [
        {"operation": "inspect"},
        {
            "operation": "invoke_managed_marker",
            "expected_process": token,
            "expected_sha256": "a" * 64,
        },
    ]
    invalid = [
        {"operation": "inspect", "selector": "Workspace"},
        {"operation": "invoke_managed_marker", "script": "arbitrary"},
        {"operation": "record"},
        {"operation": ["inspect"]},
        {"operation": "inspect", "x": 10, "y": 20},
        {"operation": "inspect", "keys": "anything"},
        {**valid[1], "expected_process": process(pid=True)},
        {
            **valid[1],
            "expected_process": process(executable_path=r"C:\Temp\Resolve.exe"),
        },
        {**valid[1], "expected_process": process(creation_time="0")},
        {**valid[1], "expected_process": process(window_handle=0)},
        {**valid[1], "expected_process": process(project_name_hint="Other")},
        {**valid[1], "expected_process": process(window_state="unknown")},
        {**valid[1], "expected_sha256": "A" * 64},
        {**valid[1], "expected_sha256": "a" * 64 + "\n"},
    ]
    texts = [json.dumps(item, ensure_ascii=False) for item in valid + invalid]
    texts.extend(
        [
            '{"operation":"inspect","operation":"inspect"}',
            '{"operation":"inspect","Operation":"inspect"}',
            '{"operation":"inspect","operati\\u006fn":"inspect"}',
        ]
    )
    result = _offline_powershell(
        "$texts=ConvertFrom-Json ([Console]::In.ReadToEnd()); $out=@(); "
        "foreach($text in $texts) { try { "
        "$null=ConvertTo-BootstrapRequest $text; $out+=,$true } "
        "catch { $out+=,$false } }; ConvertTo-Json -InputObject @($out) -Compress",
        texts,
    )
    assert result == [True] * len(valid) + [False] * (len(texts) - len(valid))


@pytest.mark.parametrize(
    "mode", ["normal", "flat", "minimized", "invoke-throws", "no-pattern"]
)
def test_synthetic_ui_dispatch_uses_fixed_path_once_and_marks_boundary(
    mode: str,
) -> None:
    # Assembly loading and script-method objects are safe; none of the actual
    # discovery, desktop, native-input, process or file functions are called.
    script = r"""
        Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName WindowsBase;
        $inputData=ConvertFrom-Json ([Console]::In.ReadToEnd());
        $script:Mode=$inputData.mode; $script:DispatchStarted=$false;
        $script:RestoredMinimized=$false; $script:Calls=0; $script:HashChecks=0;
        $script:Menus=@(); $script:State=$inputData.process.window_state;
        $script:Main=[pscustomobject]@{Current=[pscustomobject]@{IsOffscreen=$false}};
        $script:Main|Add-Member ScriptMethod SetFocus {};
        function Assert-BootstrapContext { return $script:Main }
        function Assert-BootstrapForeground {}
        function Activate-BootstrapWindow {}
        function Get-BootstrapScriptHash { $script:HashChecks++; return ('a'*64) }
        function Get-BootstrapWindowState { param($main) return $script:State }
        function Start-Sleep { param($Milliseconds) }
        $script:Pattern=[pscustomobject]@{};
        $script:Pattern|Add-Member ScriptMethod SetWindowVisualState {
            param($value) $script:State='normal'
        };
        $script:Main|Add-Member ScriptMethod TryGetCurrentPattern {
            param($pattern,$output) $output.Value=$script:Pattern; return $true
        };
        function Expand-BootstrapMenu { param($item) $script:Menus+=,$item.Name }
        function Find-BootstrapMenuItem {
            param($Names,$Optional=$false)
            $name=$Names[0];
            if($name -ceq 'Utility' -and $script:Mode -ceq 'flat') { return $null }
            $item=[pscustomobject]@{Name=$name};
            $item|Add-Member ScriptMethod TryGetCurrentPattern {
                param($pattern,$output)
                if($script:Mode -ceq 'no-pattern') { return $false }
                $invoke=[pscustomobject]@{};
                $invoke|Add-Member ScriptMethod Invoke {
                    $script:Calls++;
                    if(-not $script:DispatchStarted) { throw 'boundary missing' }
                    if($script:Mode -ceq 'invoke-throws') {
                        throw 'synthetic after-dispatch failure'
                    }
                };
                $output.Value=$invoke; return $true
            };
            return $item
        }
        $request=[pscustomobject]@{expected_process=$inputData.process;expected_sha256=('a'*64)};
        $failed=$false;
        try { $result=Invoke-BootstrapManagedMarker $request } catch { $failed=$true }
        @{calls=$script:Calls;menus=@($script:Menus);started=$script:DispatchStarted;
          restored=$script:RestoredMinimized;hash_checks=$script:HashChecks;failed=$failed}|
          ConvertTo-Json -Depth 4 -Compress
    """
    result = _offline_powershell(
        script,
        {
            "mode": mode,
            "process": process(
                window_state="minimized" if mode == "minimized" else "normal",
            ),
        },
    )
    assert result["menus"] == (
        ["Workspace", "Scripts"]
        if mode == "flat"
        else ["Workspace", "Scripts", "Utility"]
    )
    assert result["calls"] == (0 if mode == "no-pattern" else 1)
    assert result["started"] is (mode != "no-pattern")
    assert result["failed"] is (mode in {"invoke-throws", "no-pattern"})
    assert result["restored"] is (mode == "minimized")
    assert result["hash_checks"] == (1 if mode == "no-pattern" else 2)


@pytest.mark.parametrize("changed", [False, True])
def test_activation_rechecks_context_before_accepting_foreground(changed: bool) -> None:
    result = _offline_powershell(
        r"""
        $changed=ConvertFrom-Json ([Console]::In.ReadToEnd());
        Add-Type 'using System; public static class BootstrapNative {
            public static int Calls;
            public static void ActivateWindow(IntPtr handle,uint pid) { Calls++; }
        }';
        $script:ExpectedProcess=@{window_handle=456;pid=123};
        $script:Contexts=0;$script:ForegroundChecks=0;
        function Assert-BootstrapContext {
            $script:Contexts++;
            if($changed -and $script:Contexts -eq 2){throw 'BSUI_PROCESS_CHANGED'}
        }
        function Assert-BootstrapForeground {$script:ForegroundChecks++}
        function Start-Sleep {param($Milliseconds)}
        $failed=$false;
        try {Activate-BootstrapWindow} catch {$failed=$true}
        @{failed=$failed;contexts=$script:Contexts;
          foreground=$script:ForegroundChecks;calls=[BootstrapNative]::Calls}|
          ConvertTo-Json -Compress
        """,
        changed,
    )
    assert result == {
        "failed": changed, "contexts": 2,
        "foreground": 0 if changed else 1, "calls": 1,
    }
