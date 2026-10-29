"""Exercise the fixed patch parser and driver workflow without a live UI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

MODULE = (
    Path(__file__).resolve().parents[2]
    / "providers"
    / "resolve"
    / "ResolveFairlightPatch.psm1"
)

# All UI entry points are replaced before the tested public functions run.
# The interpreter is the real PowerShell interpreter; JSON fixtures are data.
HARNESS = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Fail-Fairlight([string]$Code) { throw ('WRUI_' + $Code) }
$case = [Console]::In.ReadToEnd() | ConvertFrom-Json
if ($case.operation -ceq 'parse') {
    $tokens=$null; $errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile(
        $env:FAIRLIGHT_PATCH_TEST_MODULE,[ref]$tokens,[ref]$errors)
    @{ errors=@($errors | ForEach-Object { $_.Message });
      statements=@($ast.EndBlock.Statements | ForEach-Object { $_.GetType().Name }) } |
        ConvertTo-Json -Compress
    exit
}
. ([scriptblock]::Create([IO.File]::ReadAllText($env:FAIRLIGHT_PATCH_TEST_MODULE)))
$script:RealPatchTableSnapshot=${function:Get-PatchTableSnapshot}
$script:RealPatchSingleSelection=${function:Assert-PatchSingleSelection}
$script:actions = [System.Collections.Generic.List[string]]::new()
$script:opened = 0
$script:FairlightMutationTouched = $false
function Assert-FairlightContext { }
function Open-PatchDialog {
    $script:opened++
    $script:actions.Add('open:'+$script:opened)
    return $script:opened
}
function Close-FairlightDialog($Dialog) { $script:actions.Add('close:'+$Dialog) }
function Get-PatchPane($Dialog,[string]$Side) { return $Side }
function Get-PatchTableSnapshot($Pane) {
    $snapshot=$case.$Pane
    if ($Pane -ceq 'destination' -and $script:opened -ge 2) {
        $snapshot=$case.reopened_destination
    }
    if ($Pane -ceq 'destination' -and $script:opened -eq 1 -and
        $script:actions.Contains('click:source:1:0') -and
        $null -ne $case.PSObject.Properties['destination_after_selection']) {
        $snapshot=$case.destination_after_selection
    }
    $rows=@(); $index=0
    foreach ($row in $snapshot.rows) {
        $elements=@()
        for ($column=0;$column -lt @($row.cells).Count;$column++) {
            $elements += $Pane+':'+$index+':'+$column
        }
        $rows += @{cells=@($row.cells);elements=$elements;row_element=$elements[0]}
        $index++
    }
    return @{headers=@($snapshot.headers);rows=$rows;element=$Pane}
}
function Invoke-FairlightControl($Target) { $script:actions.Add('click:'+$Target) }
function Assert-PatchSingleSelection($Pane,$Row,[int]$Column) {
    $script:actions.Add('selection:'+$Pane)
    if ($case.selection_failure) { Fail-Fairlight 'PATCH_SELECTION_NOT_SINGLE' }
}
function Find-FairlightControl($Scope,$Names,$Type,$Class) { return 'patch_button' }
function Invoke-FairlightActionButton($Button,[switch]$Mutation) {
    if (-not $Mutation) { throw 'Patch must carry the mutation marker.' }
    $script:FairlightMutationTouched=$true
    $script:actions.Add('patch')
}
function New-FakePatchCell([string]$Name,[int]$Row,[int]$Column) {
    $cell=[pscustomobject]@{Current=[pscustomobject]@{Name=$Name};
        Item=[pscustomobject]@{Current=[pscustomobject]@{
            Row=$Row;Column=$Column;RowSpan=1;ColumnSpan=1}}}
    $cell | Add-Member ScriptMethod TryGetCurrentPattern {
        param($Kind,[ref]$Result)
        if ($Kind.Id -ne [System.Windows.Automation.GridItemPattern]::Pattern.Id) {
            return $false
        }
        $Result.Value=$this.Item
        return $true
    }
    return $cell
}
function New-FakePatchTable($Snapshot) {
    $headerInfo=[pscustomobject]@{Headers=@()}
    foreach ($name in $Snapshot.headers) {
        $headerInfo.Headers += New-FakePatchCell $name -1 -1
    }
    $headerInfo | Add-Member ScriptMethod GetColumnHeaders { return $this.Headers }
    $grid=[pscustomobject]@{Current=[pscustomobject]@{
        RowCount=@($Snapshot.rows).Count;ColumnCount=@($Snapshot.headers).Count};Items=@{}}
    $grid | Add-Member ScriptMethod GetItem {
        param($Row,$Column)
        return $this.Items[([string]$Row+':'+$Column)]
    }
    for ($row=0;$row -lt @($Snapshot.rows).Count;$row++) {
        for ($column=0;$column -lt @($Snapshot.headers).Count;$column++) {
            $cell=New-FakePatchCell $Snapshot.rows[$row].cells[$column] $row $column
            $grid.Items[([string]$row+':'+$column)]=$cell
        }
    }
    $table=[pscustomobject]@{Current=[pscustomobject]@{
        ClassName='QTreeView';IsOffscreen=$false};
        Table=[pscustomobject]@{Current=$headerInfo};Grid=$grid}
    $table | Add-Member ScriptMethod TryGetCurrentPattern {
        param($Kind,[ref]$Result)
        if ($Kind.Id -eq [System.Windows.Automation.TablePattern]::Pattern.Id) {
            $Result.Value=$this.Table;return $true
        }
        if ($Kind.Id -eq [System.Windows.Automation.GridPattern]::Pattern.Id) {
            $Result.Value=$this.Grid;return $true
        }
        return $false
    }
    return $table
}
try {
    switch -CaseSensitive ($case.operation) {
        'table' {
            $result=Resolve-PatchTable $case.snapshot $case.kind $case.target_name `
                $case.source_name $case.source_channel
        }
        'selection' {
            function Test-PatchSameElement($Left,$Right) { return $Left -ceq $Right }
            Assert-PatchSelectionMembers @($case.selected) $case.row
            $result=@{verified=$true}
        }
        'selection_pattern' {
            Add-Type -AssemblyName UIAutomationClient
            function Test-PatchSameElement($Left,$Right) { return $Left -ceq $Right }
            $info=[pscustomobject]@{Selected=@($case.selected)}
            $info | Add-Member ScriptMethod GetSelection { return $this.Selected }
            $pane=[pscustomobject]@{Pattern=[pscustomobject]@{Current=$info}}
            $pane | Add-Member ScriptMethod TryGetCurrentPattern {
                param($Kind,[ref]$Result)
                $Result.Value=$this.Pattern;return $true
            }
            & $script:RealPatchSingleSelection $pane $case.row 0
            $result=@{verified=$true}
        }
        'snapshot_pattern' {
            Add-Type -AssemblyName UIAutomationClient
            $script:FakeTable=New-FakePatchTable $case.snapshot
            function Get-FairlightDescendants($Scope) { return $script:FakeTable }
            $snapshot=& $script:RealPatchTableSnapshot 'synthetic-pane'
            $result=(Resolve-PatchTable $snapshot 'destination' 'Voice').input_patch
        }
        'get' { $result=Read-FairlightPatch $case.target }
        'set' {
            $result=Set-FairlightPatch $case.target $case.source_name `
                $case.source_channel
        }
        default { throw 'Unsupported offline test operation.' }
    }
    @{ok=$true;result=$result;actions=@($script:actions);
      mutation=$script:FairlightMutationTouched} | ConvertTo-Json -Depth 20 -Compress
} catch {
    @{ok=$false;error=$_.Exception.Message;actions=@($script:actions);
      mutation=$script:FairlightMutationTouched} | ConvertTo-Json -Depth 20 -Compress
}
"""


def run_case(
    case: dict[str, Any], *, executable: str | None = None
) -> dict[str, Any]:
    """Run only the synthetic harness, never the production entrypoint."""
    executable = executable or shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is needed for offline patch module checks")
    environment = os.environ.copy()
    environment["FAIRLIGHT_PATCH_TEST_MODULE"] = str(MODULE)
    completed = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", HARNESS],
        input=json.dumps(case),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=20,
        env=environment,
    )
    result: dict[str, Any] = json.loads(completed.stdout)
    return result


def table(headers: list[str], *rows: list[str]) -> dict[str, Any]:
    """Represent genuine logical cells rather than flattened source choices."""
    return {"headers": headers, "rows": [{"cells": row} for row in rows]}


def table_case(snapshot: dict[str, Any], kind: str = "destination") -> dict[str, Any]:
    return {
        "operation": "table",
        "snapshot": snapshot,
        "kind": kind,
        "target_name": "Voice",
        "source_name": "Example USB Input",
        "source_channel": 2,
    }


def workflow_case(operation: str = "set") -> dict[str, Any]:
    return {
        "operation": operation,
        "target": {
            "timeline_name": "Test Timeline",
            "scope": "track",
            "name": "Voice",
            "index": 2,
            "audio_track_count": 3,
        },
        "source_name": "Example USB Input",
        "source_channel": 2,
        "source": table(["Name"], ["1: Example USB Input"], ["2: Example USB Input"]),
        "destination": table(["Track", "Input"], ["Voice", "No Input"]),
        "reopened_destination": table(
            ["Track", "Input"], ["Voice", "2: Example USB Input"]
        ),
        "selection_failure": False,
    }


def test_module_parses_without_loading_uia_or_contacting_resolve() -> None:
    result = run_case({"operation": "parse"})
    assert result["errors"] == []
    assert set(result["statements"]) == {"FunctionDefinitionAst"}


@pytest.mark.parametrize("assignment", ["No Input", "Unpatched", "Not Patched", "None"])
def test_explicit_unpatched_assignment(assignment: str) -> None:
    result = run_case(table_case(table(["Name", "Input"], ["Voice", assignment])))
    assert result["ok"]
    assert result["result"]["input_patch"] == {
        "patched": False,
        "source_name": None,
        "source_channel": None,
    }
    assert result["mutation"] is False


def test_destination_assignment_is_read_independently_of_source_choices() -> None:
    result = run_case(
        table_case(table(["Track", "Source"], ["Voice", "7: Other Input"]))
    )
    assert result["ok"]
    assert result["result"]["input_patch"] == {
        "patched": True,
        "source_name": "Other Input",
        "source_channel": 7,
    }


@pytest.mark.parametrize(
    ("snapshot", "error"),
    [
        (table(["Name"], ["Voice"]), "HEADERS_UNSUPPORTED"),
        (table(["Name", "Route"], ["Voice", "2: Example USB Input"]), "HEADERS"),
        (table(["Name", "Input"], ["Voice", ""]), "TEXT_UNAVAILABLE"),
        (table(["Name", "Input"], ["Voice", "Example USB Input"]), "CHANNEL"),
        (table(["Name", "Input"], ["Voice", "65: Example USB Input"]), "CHANNEL"),
        (table(["Name", "Input"], ["Voice", "2: Example... Input"]), "TRUNCATED"),
        (
            table(["Name", "Input"], ["Voice", "No Input"], ["Voice", "No Input"]),
            "DESTINATION_UNAVAILABLE_OR_AMBIGUOUS",
        ),
        (
            table(["Name", "Input"], ["Voice-L", "No Input"], ["Voice-R", "No Input"]),
            "MULTICHANNEL_DESTINATION",
        ),
        (
            table(["Track", "Input", "Channel"], ["Voice", "No Input", "2"]),
            "MULTICHANNEL",
        ),
        (table(["Name", "Input"], ["Voice"]), "CELLS_UNAVAILABLE"),
    ],
)
def test_unknown_or_ambiguous_destination_fails_closed(
    snapshot: dict[str, Any], error: str
) -> None:
    result = run_case(table_case(snapshot))
    assert result["ok"] is False
    assert error in result["error"]
    assert result["mutation"] is False


def test_source_row_matches_exact_name_and_numeric_channel() -> None:
    result = run_case(
        table_case(
            table(["Name"], ["1: Example USB Input"], ["2: Example USB Input"]),
            "source",
        )
    )
    assert result["ok"]
    assert result["result"]["row"]["cells"] == ["2: Example USB Input"]


@pytest.mark.parametrize(
    "snapshot",
    [
        table(["Name"], ["1: Example USB Input"]),
        table(["Name"], ["2: example USB Input"]),
        table(["Name"], ["2: Example USB In..."]),
        table(["Name"], ["2: Example USB Input"], ["2: Example USB Input"]),
        table(["Name", "Channel"], ["2: Example USB Input", "1"]),
    ],
)
def test_wrong_ambiguous_or_truncated_source_is_rejected(
    snapshot: dict[str, Any],
) -> None:
    result = run_case(table_case(snapshot, "source"))
    assert result["ok"] is False
    assert result["mutation"] is False


def test_explicit_source_channel_column_is_supported() -> None:
    result = run_case(
        table_case(table(["Name", "Channel"], ["Example USB Input", "2"]), "source")
    )
    assert result["ok"]


def test_set_clicks_exact_pair_then_reopens_and_reads_real_assignment() -> None:
    result = run_case(workflow_case())
    assert result["ok"]
    assert result["actions"] == [
        "open:1", "click:source:1:0", "selection:source",
        "click:destination:0:0", "selection:destination", "selection:source",
        "patch", "close:1", "open:2", "close:2",
    ]
    assert result["result"] == {
        "changed": True,
        "input_patch": {
            "patched": True,
            "source_name": "Example USB Input",
            "source_channel": 2,
            "verification": "windows_uia_patch_reopened",
        },
    }
    assert result["mutation"] is True


def test_workflow_is_compatible_with_windows_powershell() -> None:
    executable = shutil.which("powershell")
    if executable is None:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    result = run_case(workflow_case(), executable=executable)
    assert result["ok"]
    assert result["result"]["changed"] is True


@pytest.mark.skipif(os.name != "nt", reason="Windows UIA assembly reflection")
def test_actual_snapshot_extractor_uses_uia_current_pattern_interfaces() -> None:
    result = run_case(
        {
            "operation": "snapshot_pattern",
            "snapshot": table(["Track", "Input"], ["Voice", "2: Example USB Input"]),
        }
    )
    assert result["ok"], result.get("error")
    assert result["result"] == {
        "patched": True,
        "source_name": "Example USB Input",
        "source_channel": 2,
    }
    assert result["actions"] == []
    assert result["mutation"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows UIA assembly reflection")
def test_actual_selection_reader_uses_current_pattern_interface() -> None:
    result = run_case(
        {
            "operation": "selection_pattern",
            "selected": ["cell:1:0", "cell:1:1"],
            "row": {"elements": ["cell:1:0", "cell:1:1"], "row_element": "row:1"},
        }
    )
    assert result["ok"], result.get("error")
    assert result["mutation"] is False


@pytest.mark.parametrize(
    ("selected", "accepted"),
    [
        (["row:1"], True),
        (["cell:1:0"], True),
        (["cell:1:0", "cell:1:1"], True),
        ([], False),
        (["cell:1:0", "cell:2:0"], False),
        (["row:1", "row:2"], False),
        (["cell:2:0"], False),
    ],
)
def test_selection_contains_only_one_proved_row(
    selected: list[str], accepted: bool
) -> None:
    result = run_case(
        {
            "operation": "selection",
            "selected": selected,
            "row": {"elements": ["cell:1:0", "cell:1:1"], "row_element": "row:1"},
        }
    )
    assert result["ok"] is accepted
    assert result["mutation"] is False


def test_same_assignment_is_noop_but_still_requires_reopened_readback() -> None:
    case = workflow_case()
    case["destination"] = case["reopened_destination"]
    result = run_case(case)
    assert result["ok"]
    assert result["result"]["changed"] is False
    assert result["actions"] == ["open:1", "close:1", "open:2", "close:2"]
    assert result["mutation"] is False


def test_get_reopens_and_never_mutates() -> None:
    case = workflow_case("get")
    case["reopened_destination"] = case["destination"]
    result = run_case(case)
    assert result["ok"]
    assert result["result"]["input_patch"]["patched"] is False
    assert result["actions"] == ["open:1", "close:1", "open:2", "close:2"]
    assert result["mutation"] is False


@pytest.mark.parametrize(
    "reopened", ["No Input", "1: Example USB Input", "", "2: Wrong Input"]
)
def test_post_mutation_failure_does_not_report_success(reopened: str) -> None:
    case = workflow_case()
    case["reopened_destination"] = table(["Track", "Input"], ["Voice", reopened])
    result = run_case(case)
    assert result["ok"] is False
    assert result["mutation"] is True
    assert result["actions"][-1] == "close:2"


def test_multiple_selected_rows_refuse_before_patch_and_close_owned_dialog() -> None:
    case = workflow_case()
    case["selection_failure"] = True
    result = run_case(case)
    assert result["ok"] is False
    assert "SELECTION_NOT_SINGLE" in result["error"]
    assert "patch" not in result["actions"]
    assert result["actions"][-1] == "close:1"
    assert result["mutation"] is False


def test_unreadable_current_route_never_attempts_patch_or_unpatch() -> None:
    case = workflow_case()
    case["destination"] = table(["Track", "Input"], ["Voice", ""])
    result = run_case(case)
    assert result["ok"] is False
    assert result["actions"] == ["open:1", "close:1"]
    assert result["mutation"] is False


def test_concurrent_assignment_change_refuses_before_patch() -> None:
    case = workflow_case()
    case["destination_after_selection"] = table(
        ["Track", "Input"], ["Voice", "3: Other Input"]
    )
    result = run_case(case)
    assert result["ok"] is False
    assert "ASSIGNMENT_CHANGED" in result["error"]
    assert "patch" not in result["actions"]
    assert result["actions"][-1] == "close:1"
    assert result["mutation"] is False


def test_get_refuses_assignment_that_changes_while_reopening() -> None:
    result = run_case(workflow_case("get"))
    assert result["ok"] is False
    assert "REOPENED_READBACK_MISMATCH" in result["error"]
    assert result["actions"] == ["open:1", "close:1", "open:2", "close:2"]
    assert result["mutation"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [("scope", "bus"), ("index", True), ("index", 4), ("name", "Voice...")],
)
def test_unsupported_target_refuses_before_opening(field: str, value: Any) -> None:
    case = workflow_case()
    case["target"][field] = value
    result = run_case(case)
    assert result["ok"] is False
    assert result["actions"] == []
    assert result["mutation"] is False


def test_module_contains_no_automatic_execution_or_unpatch_fallback() -> None:
    source = MODULE.read_text(encoding="utf-8")
    assert "Un-Patch" not in source
    assert "SetValue(" not in source
    assert ".Select(" not in source
    assert "UiFLPatchIODialogImp" in source
    assert "m_BtnListView" in source
    assert "GridItemPattern" in source
    assert "SourceName" in source
