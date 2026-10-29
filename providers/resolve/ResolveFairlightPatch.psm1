# Fixed semantic Fairlight input-patch implementation, loaded by the trusted driver.
# Loading this file performs no UI work. UI helpers and guards belong to Core.
# The English list/table profile deliberately refuses missing accessibility data.

function Assert-PatchText([object]$Text) {
    if ($Text -isnot [string] -or -not $Text -or $Text.Length -gt 128 -or
        $Text -cne $Text.Trim() -or $Text -match '[\x00-\x1f\x7f]' -or
        $Text.Contains('...') -or $Text.Contains([string][char]0x2026)) {
        Fail-Fairlight 'PATCH_TEXT_UNAVAILABLE_OR_TRUNCATED'
    }
}

function Assert-PatchTarget($Target) {
    if ($null -eq $Target -or
        (@($Target.PSObject.Properties.Name | Sort-Object) -join ',') -cne
            'audio_track_count,index,name,scope,timeline_name' -or
        $Target.scope -cne 'track') { Fail-Fairlight 'PATCH_TARGET_UNSUPPORTED' }
    Assert-PatchText $Target.name
    Assert-PatchText $Target.timeline_name
    foreach ($value in @($Target.index, $Target.audio_track_count)) {
        if (($value -isnot [int] -and $value -isnot [long]) -or $value -lt 1 -or
            $value -gt 1024) { Fail-Fairlight 'PATCH_TARGET_UNSUPPORTED' }
    }
    if ($Target.index -gt $Target.audio_track_count) { Fail-Fairlight 'PATCH_TARGET_UNSUPPORTED' }
}

function ConvertFrom-PatchSource([string]$Text, [object]$Channel = $null) {
    Assert-PatchText $Text
    $number = 0
    $name = $Text
    if ($Text -cmatch '^([1-9][0-9]?): (.+)$') {
        $number = [int]$Matches[1]
        $name = $Matches[2]
    }
    if ($null -ne $Channel) {
        if ([string]$Channel -cnotmatch '^[1-9][0-9]?$') {
            Fail-Fairlight 'PATCH_CHANNEL_UNAVAILABLE'
        }
        if ($number -ne 0 -and $number -ne [int]$Channel) {
            Fail-Fairlight 'PATCH_CHANNEL_MISMATCH'
        }
        $number = [int]$Channel
    }
    if ($number -lt 1 -or $number -gt 64) { Fail-Fairlight 'PATCH_CHANNEL_UNAVAILABLE' }
    Assert-PatchText $name
    return @{ source_name=$name; source_channel=$number }
}

function Get-PatchHeaderIndex([object[]]$Headers, [string[]]$Names, [switch]$Optional) {
    $found = @()
    for ($column=0; $column -lt $Headers.Count; $column++) {
        if ($Headers[$column] -cin $Names) { $found += $column }
    }
    if ($found.Count -eq 0 -and $Optional) { return -1 }
    if ($found.Count -ne 1) { Fail-Fairlight 'PATCH_HEADERS_UNSUPPORTED_OR_AMBIGUOUS' }
    return $found[0]
}

function Resolve-PatchTable($Snapshot, [string]$Kind, [string]$TargetName,
    [string]$SourceName = '', [int]$SourceChannel = 0) {
    # Pure snapshot interpretation: tests exercise this without UIA or Resolve.
    $headers = @($Snapshot.headers)
    $rows = @($Snapshot.rows)
    if ($headers.Count -lt 1 -or $headers.Count -gt 16 -or $rows.Count -gt 1024) {
        Fail-Fairlight 'PATCH_TABLE_UNSUPPORTED'
    }
    foreach ($header in $headers) { Assert-PatchText $header }
    if ($Kind -ceq 'source') {
        $nameIndex = Get-PatchHeaderIndex $headers @('Name','Source','Input','Audio Input','Audio Inputs')
        $channelIndex = Get-PatchHeaderIndex $headers @('Channel','Input Channel','Source Channel') -Optional
        $matches = @()
        foreach ($row in $rows) {
            $cells = @($row.cells)
            if ($cells.Count -ne $headers.Count) { Fail-Fairlight 'PATCH_CELLS_UNAVAILABLE' }
            $channel = $null
            if ($channelIndex -ge 0) { $channel = $cells[$channelIndex] }
            $source = ConvertFrom-PatchSource $cells[$nameIndex] $channel
            if ($source.source_name -ceq $SourceName -and $source.source_channel -eq $SourceChannel) {
                $matches += $row
            }
        }
        if ($matches.Count -ne 1) { Fail-Fairlight 'PATCH_SOURCE_UNAVAILABLE_OR_AMBIGUOUS' }
        return @{ row=$matches[0]; name_column=$nameIndex }
    }
    if ($Kind -cne 'destination') { Fail-Fairlight 'PATCH_TABLE_UNSUPPORTED' }
    $nameIndex = Get-PatchHeaderIndex $headers @('Name','Track','Track Name','Destination')
    $assignmentIndex = Get-PatchHeaderIndex $headers @('Input','Source','Patch','Patched Source','Audio Input')
    $channelIndex = Get-PatchHeaderIndex $headers @('Channel','Track Channel') -Optional
    $matches = @()
    foreach ($row in $rows) {
        $cells = @($row.cells)
        if ($cells.Count -ne $headers.Count) { Fail-Fairlight 'PATCH_CELLS_UNAVAILABLE' }
        Assert-PatchText $cells[$nameIndex]
        if ($cells[$nameIndex] -cin @(($TargetName+'-L'),($TargetName+'-R'),
            ($TargetName+' L'),($TargetName+' R'))) { Fail-Fairlight 'PATCH_MULTICHANNEL_DESTINATION' }
        if ($cells[$nameIndex] -ceq $TargetName) { $matches += $row }
    }
    if ($matches.Count -ne 1) { Fail-Fairlight 'PATCH_DESTINATION_UNAVAILABLE_OR_AMBIGUOUS' }
    $row = $matches[0]
    $cells = @($row.cells)
    if ($channelIndex -ge 0 -and $cells[$channelIndex] -cnotin @('1','Mono')) {
        Fail-Fairlight 'PATCH_MULTICHANNEL_DESTINATION'
    }
    $assignment = $cells[$assignmentIndex]
    Assert-PatchText $assignment
    if ($assignment -cin @('No Input','Unpatched','Not Patched','None')) {
        $patch = @{ patched=$false; source_name=$null; source_channel=$null }
    } else {
        $source = ConvertFrom-PatchSource $assignment
        $patch = @{ patched=$true; source_name=$source.source_name; source_channel=$source.source_channel }
    }
    return @{ row=$row; name_column=$nameIndex; input_patch=$patch }
}

function Get-PatchCellText($Cell) {
    $value = $null
    if ($Cell.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$value)) {
        return [string]$value.Current.Value
    }
    return [string]$Cell.Current.Name
}

function Get-PatchChildren($Scope) {
    return $Scope.FindAll([System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition)
}

function Get-PatchTableSnapshot($Pane) {
    $tables = @(Get-FairlightDescendants $Pane | Where-Object {
        $_.Current.ClassName -cin @('QTreeView','QTableView') -and -not $_.Current.IsOffscreen
    })
    if ($tables.Count -ne 1) { Fail-Fairlight 'PATCH_LIST_TABLE_UNAVAILABLE_OR_AMBIGUOUS' }
    $table = $tables[0]
    $pattern = $null
    $headerCells = @()
    if ($table.TryGetCurrentPattern([System.Windows.Automation.TablePattern]::Pattern, [ref]$pattern)) {
        $headerCells = @($pattern.Current.GetColumnHeaders())
    } else {
        $headerRows = @(Get-PatchChildren $table | Where-Object {
            $_.Current.ControlType.ProgrammaticName -ceq 'ControlType.Header'
        })
        if ($headerRows.Count -eq 1) {
            $headerCells = @(Get-PatchChildren $headerRows[0] | Where-Object {
                $_.Current.ControlType.ProgrammaticName -ceq 'ControlType.HeaderItem'
            })
        }
    }
    if ($headerCells.Count -lt 1 -or $headerCells.Count -gt 16) {
        Fail-Fairlight 'PATCH_HEADERS_UNAVAILABLE'
    }
    $headers = @($headerCells | ForEach-Object { Get-PatchCellText $_ })
    $grid = $null
    $rows = @()
    if ($table.TryGetCurrentPattern([System.Windows.Automation.GridPattern]::Pattern, [ref]$grid)) {
        if ($grid.Current.ColumnCount -ne $headers.Count -or $grid.Current.RowCount -gt 1024) {
            Fail-Fairlight 'PATCH_GRID_SHAPE_UNSUPPORTED'
        }
        for ($row=0; $row -lt $grid.Current.RowCount; $row++) {
            $cells = @(); $elements = @()
            for ($column=0; $column -lt $headers.Count; $column++) {
                $cell = $grid.GetItem($row,$column)
                $item = $null
                if ($null -eq $cell -or -not $cell.TryGetCurrentPattern(
                    [System.Windows.Automation.GridItemPattern]::Pattern, [ref]$item) -or
                    $item.Current.Row -ne $row -or $item.Current.Column -ne $column -or
                    $item.Current.RowSpan -ne 1 -or $item.Current.ColumnSpan -ne 1) {
                    Fail-Fairlight 'PATCH_GRID_CELL_UNAVAILABLE'
                }
                $cells += Get-PatchCellText $cell
                $elements += $cell
            }
            $rows += @{ cells=$cells; elements=$elements; row_element=$elements[0] }
        }
    } else {
        # A logical row is usable only when the provider exposes every ordered
        # cell as a direct child. Flattened descendant names are never a table.
        $logicalRows = @(Get-PatchChildren $table | Where-Object {
            $_.Current.ControlType.ProgrammaticName -cin @('ControlType.DataItem','ControlType.ListItem')
        })
        if ($logicalRows.Count -eq 0 -or $logicalRows.Count -gt 1024) {
            Fail-Fairlight 'PATCH_GRID_OR_LOGICAL_ROWS_UNAVAILABLE'
        }
        foreach ($logicalRow in $logicalRows) {
            $elements = @(Get-PatchChildren $logicalRow | Where-Object {
                $_.Current.ControlType.ProgrammaticName -cin @('ControlType.Text','ControlType.DataItem')
            })
            if ($elements.Count -ne $headers.Count) { Fail-Fairlight 'PATCH_LOGICAL_CELLS_UNAVAILABLE' }
            $rows += @{ cells=@($elements | ForEach-Object { Get-PatchCellText $_ });
                elements=$elements; row_element=$logicalRow }
        }
    }
    return @{ headers=$headers; rows=$rows; element=$table }
}

function Get-PatchDialog {
    $windows = @(Get-FairlightOwnedWindows | Where-Object {
        $_.Current.ClassName -ceq 'UiFLPatchIODialogImp'
    })
    if ($windows.Count -gt 1) { Fail-Fairlight 'PATCH_DIALOG_AMBIGUOUS' }
    if ($windows.Count -eq 1) { return $windows[0] }
    return $null
}

function Get-PatchComboValue($Combo) {
    $value = $null
    if ($Combo.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$value)) {
        return [string]$value.Current.Value
    }
    $selection = $null
    if ($Combo.TryGetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern, [ref]$selection)) {
        $selected = @($selection.Current.GetSelection())
        if ($selected.Count -eq 1) { return [string]$selected[0].Current.Name }
    }
    Fail-Fairlight 'PATCH_SELECTOR_READBACK_UNAVAILABLE'
}

function Set-PatchCombo($Dialog, [string]$Side, [string]$Choice) {
    $combos = @(Get-FairlightDescendants $Dialog | Where-Object {
        if ($_.Current.ControlType.ProgrammaticName -cne 'ControlType.ComboBox' -or
            $_.Current.IsOffscreen) { return $false }
        $label = $_.Current.LabeledBy
        return ($null -ne $label -and $label.Current.Name -ceq $Side) -or
            $_.Current.Name -ceq $Side -or
            ($Side -ceq 'Source' -and $_.Current.AutomationId -cmatch 'm_Source[^.]*$') -or
            ($Side -ceq 'Destination' -and $_.Current.AutomationId -cmatch 'm_Dest[^.]*$')
    })
    if ($combos.Count -ne 1) { Fail-Fairlight 'PATCH_SELECTOR_UNAVAILABLE_OR_AMBIGUOUS' }
    $combo = $combos[0]
    if ((Get-PatchComboValue $combo) -ceq $Choice) { return }
    Invoke-FairlightControl $combo
    $scopes = @($Dialog) + @(Get-FairlightOwnedWindows)
    $choices = @(); $seen = @{}
    foreach ($scope in $scopes) {
        foreach ($control in @(Get-FairlightDescendants $scope)) {
            if ($control.Current.Name -ceq $Choice -and -not $control.Current.IsOffscreen -and
                $control.Current.ControlType.ProgrammaticName -ceq 'ControlType.ListItem') {
                $key = $control.GetRuntimeId() -join ':'
                if (-not $seen.ContainsKey($key)) { $seen[$key]=$true; $choices += $control }
            }
        }
    }
    if ($choices.Count -ne 1) { Fail-Fairlight 'PATCH_CHOICE_UNAVAILABLE_OR_AMBIGUOUS' }
    # Qt Value.SetValue and SelectionItem.Select may be no-ops. Use guarded clicks.
    Invoke-FairlightControl $choices[0]
    if ((Get-PatchComboValue $combo) -cne $Choice) { Fail-Fairlight 'PATCH_CHOICE_NOT_APPLIED' }
}

function Get-PatchPane($Dialog, [string]$Side) {
    $suffix = 'm_SourceViewContainer'
    if ($Side -ceq 'destination') { $suffix = 'm_DestViewContainer' }
    $panes = @(Get-FairlightDescendants $Dialog | Where-Object {
        $_.Current.AutomationId.EndsWith($suffix) -and -not $_.Current.IsOffscreen
    })
    if ($panes.Count -ne 1) { Fail-Fairlight 'PATCH_PANE_UNAVAILABLE_OR_AMBIGUOUS' }
    return $panes[0]
}

function Open-PatchDialog {
    Assert-FairlightContext
    if (@(Get-FairlightOwnedWindows).Count -ne 0) { Fail-Fairlight 'PATCH_EXISTING_WINDOW' }
    Open-FairlightMenuItem 'Fairlight' 'Patch Input/Output...'
    $dialog = Get-PatchDialog
    if ($null -eq $dialog) { Fail-Fairlight 'PATCH_DIALOG_DID_NOT_OPEN' }
    Register-FairlightDialog $dialog
    try {
        Set-PatchCombo $dialog 'Source' 'Audio Inputs'
        Set-PatchCombo $dialog 'Destination' 'Track Input'
        $buttons = @(Get-FairlightDescendants $dialog | Where-Object {
            $_.Current.AutomationId.EndsWith('m_BtnListView') -and
            $_.Current.ControlType.ProgrammaticName -ceq 'ControlType.Button' -and
            -not $_.Current.IsOffscreen
        })
        if ($buttons.Count -ne 1) { Fail-Fairlight 'PATCH_LIST_VIEW_UNAVAILABLE' }
        Invoke-FairlightControl $buttons[0]
        return $dialog
    } catch {
        try { Close-FairlightDialog $dialog } catch { }
        throw
    }
}

function Read-PatchDestination($Dialog, $Target) {
    $snapshot = Get-PatchTableSnapshot (Get-PatchPane $Dialog 'destination')
    return Resolve-PatchTable $snapshot 'destination' $Target.name
}

function Test-PatchEqual($Left, $Right) {
    return $Left.patched -eq $Right.patched -and $Left.source_name -ceq $Right.source_name -and
        $Left.source_channel -eq $Right.source_channel
}

function Read-PatchReopened($Target, $Expected) {
    $dialog = Open-PatchDialog
    try {
        $actual = (Read-PatchDestination $dialog $Target).input_patch
        if (-not (Test-PatchEqual $actual $Expected)) { Fail-Fairlight 'PATCH_REOPENED_READBACK_MISMATCH' }
        $actual.verification = 'windows_uia_patch_reopened'
        return $actual
    } finally { Close-FairlightDialog $dialog }
}

function Test-PatchSameElement($Left, $Right) {
    return [System.Windows.Automation.Automation]::Compare($Left,$Right)
}

function Assert-PatchSelectionMembers([object[]]$Selected, $Row) {
    # Qt can report either the row object or every cell in the selected row.
    # Every selected object must belong to this one proven row.
    if ($Selected.Count -lt 1 -or $Selected.Count -gt @($Row.elements).Count) {
        Fail-Fairlight 'PATCH_SELECTION_NOT_SINGLE'
    }
    foreach ($item in $Selected) {
        $matched = Test-PatchSameElement $item $Row.row_element
        foreach ($cell in @($Row.elements)) {
            if (Test-PatchSameElement $item $cell) { $matched=$true; break }
        }
        if (-not $matched) { Fail-Fairlight 'PATCH_SELECTION_MISMATCH' }
    }
}

function Assert-PatchSingleSelection($Pane, $Row, [int]$Column) {
    $selection = $null
    if (-not $Pane.TryGetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern,
        [ref]$selection)) { Fail-Fairlight 'PATCH_SELECTION_UNAVAILABLE' }
    Assert-PatchSelectionMembers @($selection.Current.GetSelection()) $Row
}

function Read-FairlightPatch($Target) {
    Assert-PatchTarget $Target
    Assert-FairlightContext
    $dialog = Open-PatchDialog
    try { $before = (Read-PatchDestination $dialog $Target).input_patch }
    finally { Close-FairlightDialog $dialog }
    return @{ input_patch=(Read-PatchReopened $Target $before) }
}

function Set-FairlightPatch($Target, [string]$SourceName, [int]$SourceChannel) {
    Assert-PatchTarget $Target
    Assert-PatchText $SourceName
    if ($SourceChannel -lt 1 -or $SourceChannel -gt 64) { Fail-Fairlight 'PATCH_CHANNEL_UNAVAILABLE' }
    Assert-FairlightContext
    $expected = @{ patched=$true; source_name=$SourceName; source_channel=$SourceChannel }
    $dialog = Open-PatchDialog
    $changed = $false
    try {
        $destination = Read-PatchDestination $dialog $Target
        if (-not (Test-PatchEqual $destination.input_patch $expected)) {
            $beforePatch = $destination.input_patch
            $sourceTable = Get-PatchTableSnapshot (Get-PatchPane $dialog 'source')
            $source = Resolve-PatchTable $sourceTable 'source' '' $SourceName $SourceChannel
            Invoke-FairlightControl $source.row.elements[$source.name_column]
            Assert-PatchSingleSelection $sourceTable.element $source.row $source.name_column
            # Reacquire destination after selection instead of using stale UIA cells.
            $destinationTable = Get-PatchTableSnapshot (Get-PatchPane $dialog 'destination')
            $destination = Resolve-PatchTable $destinationTable 'destination' $Target.name
            if (-not (Test-PatchEqual $destination.input_patch $beforePatch)) {
                Fail-Fairlight 'PATCH_ASSIGNMENT_CHANGED'
            }
            Invoke-FairlightControl $destination.row.elements[$destination.name_column]
            Assert-PatchSingleSelection $destinationTable.element $destination.row $destination.name_column
            Assert-PatchSingleSelection $sourceTable.element $source.row $source.name_column
            $patch = Find-FairlightControl $dialog @('Patch') 'Button' 'QPushButton'
            Invoke-FairlightActionButton $patch -Mutation
            $changed = $true
        }
    } finally { Close-FairlightDialog $dialog }
    # Only assignment cells in a fresh dialog verify persistence. Source choices
    # and a successful button invocation never establish a patch result.
    return @{ input_patch=(Read-PatchReopened $Target $expected); changed=$changed }
}
