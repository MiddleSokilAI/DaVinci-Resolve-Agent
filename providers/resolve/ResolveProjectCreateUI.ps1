# Fixed Project Manager creation only. No focus, keyboard, mouse or generic UI API.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$script:dispatchStarted=$false
function Fail([string]$Code) { throw $Code }
function Test-CreationWindowReady($Pattern) {
    return ($Pattern.Current.WindowVisualState -ne [System.Windows.Automation.WindowVisualState]::Minimized -and
        $Pattern.Current.WindowInteractionState -in @(
            [System.Windows.Automation.WindowInteractionState]::Running,
            [System.Windows.Automation.WindowInteractionState]::ReadyForUserInteraction))
}
function Find-Control($Root,[string]$Id,[string]$Type) {
    $items=$Root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty,$Id))
    if($items.Count -ne 1){Fail 'CONTROL_AMBIGUOUS'}
    $item=$items[0]
    if($item.Current.ProcessId -ne $script:p.Id -or !$item.Current.IsEnabled -or
        $item.Current.IsOffscreen -or $item.Current.ControlType.ProgrammaticName -cne $Type){Fail 'CONTROL_UNAVAILABLE'}
    return $item
}
function Invoke-Control($Item) {
    $pattern=$null
    if(!$Item.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)){Fail 'INVOKE_UNAVAILABLE'}
    $script:dispatchStarted=$true
    $pattern.Invoke()
}
function Find-StartupUpdateSkip([object[]]$Roots,$Expected) {
    if($Roots.Count -gt 32){Fail 'ROOT_BOUND'}
    $managers=@(); $updates=@()
    foreach($root in $Roots){
        if($root.Current.IsOffscreen){continue}
        if($root.Current.ProcessId -ne $Expected.pid -or
           $root.Current.ControlType.ProgrammaticName -cne 'ControlType.Window'){
            Fail 'STARTUP_WINDOW_CHANGED'
        }
        if($root.Current.AutomationId -ceq 'UiProjectManagerDialog' -and
           $root.Current.ClassName -ceq 'ProjectManager::UiProjectManagerDialogImp' -and
           $root.Current.Name -ceq 'Project Manager' -and
           $root.Current.NativeWindowHandle -eq $Expected.window_handle){
            $managers+=,$root
        } elseif($root.Current.AutomationId -ceq 'UiAutoUpdateDialog' -and
                 $root.Current.ClassName -ceq 'UiAutoUpdateDialogImp' -and
                 $root.Current.Name -ceq 'Software Update' -and
                 $root.Current.NativeWindowHandle -gt 0){
            $updates+=,$root
        } else {Fail 'UNRECOGNIZED_STARTUP_WINDOW'}
        $nested=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Window))
        if($nested.Count -gt 32){Fail 'ROOT_BOUND'}
        foreach($child in $nested){
            if(!$child.Current.IsOffscreen){Fail 'NESTED_STARTUP_WINDOW'}
        }
    }
    if($managers.Count -ne 1 -or $updates.Count -gt 1){Fail 'STARTUP_WINDOW_AMBIGUOUS'}
    $managerWindow=$null
    if(!$managers[0].TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$managerWindow) -or
       $managerWindow.Current.WindowVisualState -eq [System.Windows.Automation.WindowVisualState]::Minimized -or
       $managerWindow.Current.WindowInteractionState -notin @(
            [System.Windows.Automation.WindowInteractionState]::Running,
            [System.Windows.Automation.WindowInteractionState]::ReadyForUserInteraction,
            [System.Windows.Automation.WindowInteractionState]::BlockedByModalWindow)){
        Fail 'MANAGER_BLOCKED'
    }
    if(!$updates.Count){return $null}
    $update=$updates[0]; $updateWindow=$null
    if(!$update.Current.IsEnabled -or
       !$update.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$updateWindow) -or
       !(Test-CreationWindowReady $updateWindow)){Fail 'UPDATE_DIALOG_BLOCKED'}
    $id='UiAutoUpdateDialog.DialogFrame.frameAutuUpdateContents.frameAutoUpdateButtonsContainer.btnSkipUpdate'
    $button=Find-Control $update $id 'ControlType.Button'
    if($button.Current.Name -cne 'Skip' -or $button.Current.ClassName -cne 'QPushButton'){
        Fail 'SKIP_BUTTON_CHANGED'
    }
    return @{button=$button;identity=(($update.GetRuntimeId() -join ',')+';'+($button.GetRuntimeId() -join ','))}
}
function Invoke-ConfirmedStartupSkip($Found,$Confirmed) {
    if($null -eq $Found -or $null -eq $Confirmed){return $false}
    if($Found.identity -cne $Confirmed.identity){Fail 'UPDATE_DIALOG_CHANGED'}
    Invoke-Control $Confirmed.button
    return $true
}
try {
    $buffer=[char[]]::new(8193)
    $count=[Console]::In.ReadBlock($buffer,0,$buffer.Length)
    if($count -gt 8192){Fail 'REQUEST_BOUND'}
    $r=[string]::new($buffer,0,$count) | ConvertFrom-Json
    if($r.operation -cnotin @('begin','inspect_dialog','create','skip_startup_update')){Fail 'INVALID_OPERATION'}
    $keys=@('operation','process')
    if($r.operation -ceq 'create'){$keys+=@('dialog','name')}
    if(@($r.PSObject.Properties.Name).Count -ne $keys.Count -or
       @($r.PSObject.Properties.Name | Where-Object {$_ -cnotin $keys}).Count){Fail 'INVALID_REQUEST'}
    $script:p=Get-Process -Id $r.process.pid
    $official=Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Blackmagic Design\DaVinci Resolve\Resolve.exe'
    if($p.ProcessName -cne 'Resolve' -or $p.MainModule.FileName -ine $official -or
       $p.StartTime.ToUniversalTime().Ticks.ToString() -cne $r.process.creation_time){Fail 'PROCESS_CHANGED'}
    if($r.process.window_title -cne 'Project Manager' -or $r.process.window_handle -le 0 -or
       $r.process.project_name_hint -ne $null -or $r.process.window_state -ceq 'minimized'){
        Fail 'MANAGER_CHANGED'
    }
    $roots=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$p.Id))
    if($roots.Count -gt 32){Fail 'ROOT_BOUND'}
    if($r.operation -ceq 'skip_startup_update'){
        $running=@(Get-Process -Name Resolve -ErrorAction Stop)
        if($running.Count -ne 1 -or $running[0].Id -ne $p.Id){Fail 'PROCESS_AMBIGUOUS'}
        $found=Find-StartupUpdateSkip @($roots) $r.process
        if($null -ne $found){
            $running=@(Get-Process -Name Resolve -ErrorAction Stop)
            if($running.Count -ne 1 -or $running[0].Id -ne $p.Id){Fail 'PROCESS_AMBIGUOUS'}
            $again=Get-Process -Id $r.process.pid
            if($again.ProcessName -cne 'Resolve' -or $again.MainModule.FileName -ine $official -or
               $again.StartTime.ToUniversalTime().Ticks.ToString() -cne $r.process.creation_time){Fail 'PROCESS_CHANGED'}
            $fresh=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$p.Id))
            $confirmed=Find-StartupUpdateSkip @($fresh) $r.process
            $null=Invoke-ConfirmedStartupSkip $found $confirmed
        }
        @{ok=$true;operation='skip_startup_update';skipped=[bool]$script:dispatchStarted} | ConvertTo-Json -Compress
        exit 0
    }
    $dialogs=@($roots | Where-Object {$_.Current.AutomationId -ceq 'UiNewProjectOptionsDlg'})
    if($r.operation -ceq 'begin'){
        if($dialogs.Count){Fail 'DIALOG_ALREADY_PRESENT'}
        $main=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($r.process.window_handle))
        if($main.Current.ProcessId -ne $p.Id -or $main.Current.AutomationId -cne 'UiProjectManagerDialog' -or
           $main.Current.Name -cne 'Project Manager' -or $main.Current.IsOffscreen -or !$main.Current.IsEnabled){Fail 'MANAGER_CHANGED'}
        $window=$null
        if(!$main.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$window) -or
           !(Test-CreationWindowReady $window)){Fail 'MANAGER_BLOCKED'}
        $id='UiProjectManagerDialog.frameProjectManagerBorder.m_ProjectManagerView.projManagerContents.splitterProjects.m_ViewProjects.projActionBottomFrame.btnAddProject'
        $button=Find-Control $main $id 'ControlType.Button'
        if($button.Current.Name -cne 'New Project'){Fail 'BUTTON_CHANGED'}
        Invoke-Control $button
        @{ok=$true;operation='begin'} | ConvertTo-Json -Compress
    } else {
        if($dialogs.Count -eq 0 -and $r.operation -ceq 'inspect_dialog'){
            @{ok=$true;operation='inspect_dialog';dialog=$null} | ConvertTo-Json -Compress
            exit 0
        }
        if($dialogs.Count -ne 1){Fail 'DIALOG_AMBIGUOUS'}
        $dialog=$dialogs[0]
        if($dialog.Current.ClassName -cne 'UiNewProjectOptionsDlgImp' -or
           $dialog.Current.Name -cne 'Create New Project' -or $dialog.Current.IsOffscreen -or
           !$dialog.Current.IsEnabled){Fail 'DIALOG_CHANGED'}
        $token=@{handle=[long]$dialog.Current.NativeWindowHandle;runtime_id=@($dialog.GetRuntimeId())}
        if($r.operation -ceq 'create'){
            if($token.handle -ne $r.dialog.handle -or
               ($token.runtime_id -join ',') -cne ($r.dialog.runtime_id -join ',')){Fail 'OWNED_DIALOG_CHANGED'}
            if($r.name -isnot [string] -or $r.name.Length -lt 1 -or $r.name.Length -gt 128 -or
               $r.name -cne $r.name.Trim() -or $r.name -match '[\x00-\x1f\x7f]'){Fail 'INVALID_NAME'}
            $prefix='UiNewProjectOptionsDlg.frameBackground.frameBody.'
            $edit=Find-Control $dialog ($prefix+'frame_ProjectName.lineEdit_ProjectName') 'ControlType.Edit'
            $value=$null
            if(!$edit.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value) -or
               $value.Current.IsReadOnly){Fail 'VALUE_UNAVAILABLE'}
            $value.SetValue($r.name)
            if($value.Current.Value -cne $r.name){Fail 'NAME_READBACK_FAILED'}
            $create=Find-Control $dialog ($prefix+'frame.pushButton_Create') 'ControlType.Button'
            if($create.Current.Name -cne 'Create'){Fail 'CREATE_BUTTON_CHANGED'}
            Invoke-Control $create
        }
        @{ok=$true;operation=$r.operation;dialog=$token} | ConvertTo-Json -Depth 4 -Compress
    }
} catch {
    @{ok=$false;code='PROJECT_CREATE_UI_FAILED';detail=$_.Exception.Message;dispatch_started=[bool]$script:dispatchStarted} | ConvertTo-Json -Compress
    exit 1
}
