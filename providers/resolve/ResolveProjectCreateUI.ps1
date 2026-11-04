# Fixed Project Manager creation only. No focus, keyboard, mouse or generic UI API.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
function Fail([string]$Code) { throw $Code }
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
    $pattern.Invoke()
}
try {
    $buffer=[char[]]::new(8193)
    $count=[Console]::In.ReadBlock($buffer,0,$buffer.Length)
    if($count -gt 8192){Fail 'REQUEST_BOUND'}
    $r=[string]::new($buffer,0,$count) | ConvertFrom-Json
    if($r.operation -cnotin @('begin','inspect_dialog','create')){Fail 'INVALID_OPERATION'}
    $keys=@('operation','process')
    if($r.operation -ceq 'create'){$keys+=@('dialog','name')}
    if(@($r.PSObject.Properties.Name).Count -ne $keys.Count -or
       @($r.PSObject.Properties.Name | Where-Object {$_ -cnotin $keys}).Count){Fail 'INVALID_REQUEST'}
    $script:p=Get-Process -Id $r.process.pid
    $official=Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Blackmagic Design\DaVinci Resolve\Resolve.exe'
    if($p.ProcessName -cne 'Resolve' -or $p.MainModule.FileName -ine $official -or
       $p.StartTime.ToUniversalTime().Ticks.ToString() -cne $r.process.creation_time){Fail 'PROCESS_CHANGED'}
    $roots=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$p.Id))
    if($roots.Count -gt 32){Fail 'ROOT_BOUND'}
    $dialogs=@($roots | Where-Object {$_.Current.AutomationId -ceq 'UiNewProjectOptionsDlg'})
    if($r.operation -ceq 'begin'){
        if($dialogs.Count){Fail 'DIALOG_ALREADY_PRESENT'}
        $main=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($r.process.window_handle))
        if($main.Current.ProcessId -ne $p.Id -or $main.Current.AutomationId -cne 'UiProjectManagerDialog' -or
           $main.Current.Name -cne 'Project Manager' -or $main.Current.IsOffscreen -or !$main.Current.IsEnabled){Fail 'MANAGER_CHANGED'}
        $window=$null
        if(!$main.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$window) -or
           $window.Current.WindowVisualState -eq [System.Windows.Automation.WindowVisualState]::Minimized -or
           $window.Current.WindowInteractionState -ne [System.Windows.Automation.WindowInteractionState]::ReadyForUserInteraction){Fail 'MANAGER_BLOCKED'}
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
    @{ok=$false;code='PROJECT_CREATE_UI_FAILED';detail=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}
