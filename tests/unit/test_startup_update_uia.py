"""Run fixed PowerShell guards on synthetic elements, never the desktop."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UIA enum types")
def test_only_the_exact_skip_can_be_invoked_with_stable_window_identity(
    tmp_path: Path,
) -> None:
    driver = Path(__file__).parents[2] / "providers/resolve/ResolveProjectCreateUI.ps1"
    harness = tmp_path / "startup-update.ps1"
    harness.write_text(r'''
param([string]$Driver)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Driver,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Driver parse failed'}
$functions=$ast.FindAll({param($n)
    $n -is [Management.Automation.Language.FunctionDefinitionAst]
},$false)
. ([scriptblock]::Create(($functions|ForEach-Object {$_.Extent.Text}) -join "`n"))
$visualType=[System.Windows.Automation.WindowVisualState]
$stateType=[System.Windows.Automation.WindowInteractionState]
$windowPattern=[System.Windows.Automation.WindowPattern]::Pattern
$invokePattern=[System.Windows.Automation.InvokePattern]::Pattern
$controlTypeProperty=[System.Windows.Automation.AutomationElement]::ControlTypeProperty
$windowType='ControlType.Window'
$managerClass='ProjectManager::UiProjectManagerDialogImp'
$skipId='UiAutoUpdateDialog.DialogFrame.frameAutuUpdateContents.'+
    'frameAutoUpdateButtonsContainer.btnSkipUpdate'
function New-Element($Identity,$Id,$Name,$Class,$Type) {
    $item=[pscustomobject]@{Identity=$Identity;Children=@();Nested=@();CanInvoke=$true;
        Current=[pscustomobject]@{ProcessId=123;Name=$Name;AutomationId=$Id;
            ClassName=$Class;ControlType=[pscustomobject]@{ProgrammaticName=$Type};
            NativeWindowHandle=$Identity;IsOffscreen=$false;IsEnabled=$true};
        Window=[pscustomobject]@{Current=[pscustomobject]@{
            WindowVisualState=$visualType::Normal;
            WindowInteractionState=$stateType::ReadyForUserInteraction}}
    }
    $invoke=[pscustomobject]@{}
    $invoke|Add-Member ScriptMethod Invoke {$script:invocations++}
    $item|Add-Member NoteProperty InvokePattern $invoke
    $item|Add-Member ScriptMethod GetRuntimeId {return ,@([int]$this.Identity)}
    $item|Add-Member ScriptMethod FindAll {
        param($scope,$condition)
        if($condition.Property.Id -eq $controlTypeProperty.Id){
            return ,([object[]]$this.Nested)
        }
        return ,([object[]]@($this.Children|Where-Object {
            $_.Current.AutomationId -ceq $condition.Value
        }))
    }
    $item|Add-Member ScriptMethod TryGetCurrentPattern {
        param($pattern,$output)
        if($pattern.Id -eq $windowPattern.Id){
            $output.Value=$this.Window;return $true
        }
        if($pattern.Id -eq $invokePattern.Id -and $this.CanInvoke){
            $output.Value=$this.InvokePattern;return $true
        }
        return $false
    }
    return $item
}
$results=@()
foreach($mode in @('valid','absent','duplicate','foreign','name','class','id',
    'button_name','button_id','button_class','disabled','hidden','no_invoke',
    'not_window','no_manager','manager_handle','editor','other_modal','nested',
    'minimized','update_minimized','closing','bound','changed','gone')){
    $script:p=[pscustomobject]@{Id=123}
    $script:dispatchStarted=$false;$script:invocations=0
    $expected=[pscustomobject]@{pid=123;window_handle=456}
    $managerArgs=@(456,'UiProjectManagerDialog','Project Manager',
        $managerClass,$windowType)
    $manager=New-Element @managerArgs
    $updateArgs=@(789,'UiAutoUpdateDialog','Software Update',
        'UiAutoUpdateDialogImp',$windowType)
    $update=New-Element @updateArgs
    $skip=New-Element 790 $skipId 'Skip' 'QPushButton' 'ControlType.Button'
    $update.Children=@($skip)
    $roots=@($manager,$update)
    switch($mode){
        'absent' {$roots=@($manager)}
        'duplicate' {$roots+=,$update}
        'foreign' {$update.Current.ProcessId=999}
        'name' {$update.Current.Name='Other dialog'}
        'class' {$update.Current.ClassName='OtherDialog'}
        'id' {$update.Current.AutomationId='OtherDialog'}
        'button_name' {$skip.Current.Name='Download'}
        'button_id' {$skip.Current.AutomationId='btnDownloadUpdate'}
        'button_class' {$skip.Current.ClassName='OtherButton'}
        'disabled' {$skip.Current.IsEnabled=$false}
        'hidden' {$skip.Current.IsOffscreen=$true}
        'no_invoke' {$skip.CanInvoke=$false}
        'not_window' {$update.Current.ControlType.ProgrammaticName='ControlType.Group'}
        'no_manager' {$roots=@($update)}
        'manager_handle' {$expected.window_handle=777}
        'editor' {
            $args2=@(999,'UiMainWindow','Existing','UiMainWindowImp',$windowType)
            $roots+=,(New-Element @args2)
        }
        'other_modal' {
            $roots+=,(New-Element 999 'SaveDialog' 'Save' 'OtherDialog' $windowType)
        }
        'nested' {
            $child=New-Element 999 'SaveDialog' 'Save' 'OtherDialog' $windowType
            $update.Nested=@($child)
        }
        'minimized' {$manager.Window.Current.WindowVisualState=$visualType::Minimized}
        'update_minimized' {
            $update.Window.Current.WindowVisualState=$visualType::Minimized
        }
        'closing' {$update.Window.Current.WindowInteractionState=$stateType::Closing}
        'bound' {$roots=@($manager)*33}
    }
    $failed=$false;$detail=$null;$skipped=$false
    try {
        $found=Find-StartupUpdateSkip $roots $expected
        $confirmed=Find-StartupUpdateSkip $roots $expected
        if($mode -eq 'changed'){$confirmed.identity='changed'}
        if($mode -eq 'gone'){$confirmed=$null}
        $skipped=Invoke-ConfirmedStartupSkip $found $confirmed
    } catch {$failed=$true;$detail=$_.Exception.Message}
    $results+=@{mode=$mode;failed=$failed;detail=$detail;skipped=$skipped;
        dispatched=[bool]$script:dispatchStarted;invocations=$script:invocations}
}
$results|ConvertTo-Json -Compress
''', encoding="utf-8")
    shell = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe")
    result = subprocess.run([str(shell), "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(harness), str(driver)],
        capture_output=True, text=True, timeout=20, shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr
    cases = json.loads(result.stdout)
    assert len(cases) == 25
    for case in cases:
        if case["mode"] == "valid":
            assert not case["failed"], case
            assert case["skipped"] and case["dispatched"]
            assert case["invocations"] == 1
        else:
            assert not case["skipped"] and not case["dispatched"], case
            assert case["invocations"] == 0, case
            assert case["failed"] is (case["mode"] not in {"absent", "gone"}), case
