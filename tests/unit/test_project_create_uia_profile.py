"""Exercise fixed creation readiness without querying or invoking a UI element."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.windows_project_create import WindowsProjectCreateAdapter


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UIA enum types")
def test_running_and_ready_windows_are_accepted_but_blocked_states_are_not(
    tmp_path: Path,
) -> None:
    driver = Path(__file__).parents[2] / "providers/resolve/ResolveProjectCreateUI.ps1"
    harness = tmp_path / "readiness.ps1"
    harness.write_text(
        r"""
param([string]$Driver)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationTypes
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Driver,[ref]$tokens,[ref]$errors)
$fn=$ast.Find({
 param($node)
 $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
 $node.Name -eq 'Test-CreationWindowReady'
},$true)
if($null -eq $fn){throw 'Missing creation readiness function'}
. ([scriptblock]::Create($fn.Extent.Text))
$results=@()
foreach($visual in [Enum]::GetValues([System.Windows.Automation.WindowVisualState])){
 $states=[Enum]::GetValues([System.Windows.Automation.WindowInteractionState])
 foreach($state in $states){
  $pattern=[pscustomobject]@{Current=[pscustomobject]@{
    WindowVisualState=$visual;WindowInteractionState=$state
  }}
  $results+=@{visual=$visual.ToString();state=$state.ToString();
    ready=[bool](Test-CreationWindowReady $pattern)}
 }
}
$results | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    shell = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = subprocess.run(
        [
            str(shell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            str(driver),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    cases = json.loads(result.stdout)
    assert len(cases) == 15
    for case in cases:
        assert case["ready"] is (
            case["visual"] != "Minimized"
            and case["state"] in {"Running", "ReadyForUserInteraction"}
        ), case


@pytest.mark.parametrize("dispatched", [False, True])
def test_driver_failure_preserves_phase_and_detail(
    monkeypatch: pytest.MonkeyPatch,
    dispatched: bool,
) -> None:
    process = {
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

    def run(command: Any, **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(
            command,
            1,
            json.dumps(
                {
                    "ok": False,
                    "code": "PROJECT_CREATE_UI_FAILED",
                    "detail": "MANAGER_BLOCKED",
                    "dispatch_started": dispatched,
                }
            ),
            "",
        )

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="MANAGER_BLOCKED") as failure:
        WindowsProjectCreateAdapter().request("begin", process)
    assert getattr(failure.value, "uncertain", None) is dispatched
