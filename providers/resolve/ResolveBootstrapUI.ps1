# Fixed opt-in bootstrap driver. Stdin contains data, never executable source.
# Inspection changes no UI. Invocation opens only our fixed managed Scripts item.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$script:DispatchStarted = $false
$script:ExpectedProcess = $null
$script:ExpectedSha256 = ''
$script:RestoredMinimized = $false
$script:WindowCandidates = @()
$script:WindowCandidatesTruncated = $false

function Fail-Bootstrap([string]$Code) { throw ('BSUI_' + $Code) }

function Assert-BootstrapKeys($Object, [string[]]$Keys) {
    if ($Object -isnot [pscustomobject] -or
        ((@($Object.PSObject.Properties.Name | Sort-Object) -join ',') -cne
         (@($Keys | Sort-Object) -join ','))) { Fail-Bootstrap 'INVALID_REQUEST' }
}

function Assert-BootstrapUniqueKeys([string]$Text) {
    # ConvertFrom-Json overwrites duplicate/case-variant properties. Scan string
    # tokens before decoding, including escaped spellings of duplicate keys.
    $objects = [Collections.Generic.Stack[hashtable]]::new()
    for ($i=0; $i -lt $Text.Length; $i++) {
        if ($Text[$i] -ceq '{') { $objects.Push(@{}); continue }
        if ($Text[$i] -ceq '}') {
            if ($objects.Count -gt 0) { $null=$objects.Pop() }
            continue
        }
        if ($Text[$i] -cne '"') { continue }
        $start=$i; $i++
        while ($i -lt $Text.Length -and $Text[$i] -cne '"') {
            if ($Text[$i] -ceq '\') { $i++ }
            $i++
        }
        if ($i -ge $Text.Length) { Fail-Bootstrap 'INVALID_REQUEST' }
        $next=$i+1
        while ($next -lt $Text.Length -and [char]::IsWhiteSpace($Text[$next])) { $next++ }
        if ($next -lt $Text.Length -and $Text[$next] -ceq ':' -and $objects.Count -gt 0) {
            try { $key=ConvertFrom-Json -InputObject $Text.Substring($start,$i-$start+1) }
            catch { Fail-Bootstrap 'INVALID_REQUEST' }
            $current=$objects.Peek()
            if ($current.ContainsKey($key)) { Fail-Bootstrap 'INVALID_REQUEST' }
            $current[$key]=$true
        }
    }
}

function Get-BootstrapOfficialExecutable {
    $knownProgramFiles=[Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
    if(-not $knownProgramFiles -or -not [IO.Path]::IsPathRooted($knownProgramFiles)){
        Fail-Bootstrap 'INSTALLATION_PATH_UNAVAILABLE'
    }
    return [IO.Path]::GetFullPath((Join-Path $knownProgramFiles 'Blackmagic Design\DaVinci Resolve\Resolve.exe'))
}

function Get-BootstrapProjectHint([string]$Title) {
    $prefix='DaVinci Resolve - '
    if (-not $Title.StartsWith($prefix,[StringComparison]::Ordinal)) { return $null }
    $name=$Title.Substring($prefix.Length)
    if (-not $name -or $name.Length -gt 256 -or $name -cne $name.Trim() -or
        $name -ceq 'Untitled Project' -or $name -match '[\x00-\x1f\x7f]') { return $null }
    return $name
}

function Assert-BootstrapProcessData($Process, [bool]$RequireWindow = $false) {
    Assert-BootstrapKeys $Process @('pid','creation_time','executable_path','window_handle',
        'window_title','project_name_hint','window_state')
    if (($Process.pid -isnot [int] -and $Process.pid -isnot [long]) -or
        $Process.pid -le 0 -or $Process.pid -gt [uint32]::MaxValue -or
        $Process.creation_time -isnot [string] -or
        $Process.creation_time -cnotmatch '^[1-9][0-9]{0,18}\z' -or
        [decimal]$Process.creation_time -gt 3155378975999999999 -or
        $Process.executable_path -isnot [string] -or $Process.executable_path.Length -gt 1024 -or
        -not [string]::Equals([IO.Path]::GetFullPath($Process.executable_path),
            (Get-BootstrapOfficialExecutable),[StringComparison]::OrdinalIgnoreCase) -or
        ($Process.window_handle -isnot [int] -and $Process.window_handle -isnot [long]) -or
        $Process.window_handle -lt 0 -or
        $Process.window_title -isnot [string] -or $Process.window_title.Length -gt 512 -or
        $Process.window_title -match '[\x00-\x1f\x7f]' -or
        $Process.window_state -cnotin @('normal','maximized','minimized')) {
        Fail-Bootstrap 'INVALID_REQUEST'
    }
    $hint=Get-BootstrapProjectHint $Process.window_title
    if (($null -eq $hint -and $null -ne $Process.project_name_hint) -or
        ($null -ne $hint -and ($Process.project_name_hint -isnot [string] -or
            $Process.project_name_hint -cne $hint)) -or
        ($RequireWindow -and ($Process.window_handle -le 0 -or $null -eq $hint))) {
        Fail-Bootstrap 'INVALID_REQUEST'
    }
}

function ConvertTo-BootstrapRequest([string]$Text) {
    if (-not $Text -or $Text.Length -gt 8192) { Fail-Bootstrap 'INVALID_REQUEST' }
    Assert-BootstrapUniqueKeys $Text
    try { $request=ConvertFrom-Json -InputObject $Text -ErrorAction Stop }
    catch { Fail-Bootstrap 'INVALID_REQUEST' }
    if ($request -isnot [pscustomobject] -or
        $null -eq $request.PSObject.Properties['operation']) { Fail-Bootstrap 'INVALID_REQUEST' }
    if ($request.operation -isnot [string]) { Fail-Bootstrap 'INVALID_REQUEST' }
    switch -CaseSensitive ($request.operation) {
        'inspect' { Assert-BootstrapKeys $request @('operation') }
        'invoke_managed_marker' {
            Assert-BootstrapKeys $request @('operation','expected_process','expected_sha256')
            Assert-BootstrapProcessData $request.expected_process $true
            if ($request.expected_sha256 -isnot [string] -or
                $request.expected_sha256 -cnotmatch '^[0-9a-f]{64}\z') { Fail-Bootstrap 'INVALID_REQUEST' }
        }
        default { Fail-Bootstrap 'INVALID_OPERATION' }
    }
    return $request
}

function Initialize-BootstrapRuntime {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName WindowsBase
    if (-not ('BootstrapNative' -as [type])) {
        Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class BootstrapNative {
    [DllImport("user32.dll", SetLastError=true)] private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Unicode)] private static extern bool GetUserObjectInformation(IntPtr obj, int index, StringBuilder value, uint length, out uint needed);
    [DllImport("user32.dll")] private static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint id);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr window);
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint source,uint target,bool attach);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    public static void ActivateWindow(IntPtr window,uint expectedProcess) {
        uint process;
        GetWindowThreadProcessId(window,out process);
        if(process!=expectedProcess)throw new InvalidOperationException("BSUI_PROCESS_CHANGED");
        uint foregroundProcess;
        uint foregroundThread=GetWindowThreadProcessId(GetForegroundWindow(),out foregroundProcess);
        if(foregroundProcess==expectedProcess)return;
        BringWindowToTop(window);SetForegroundWindow(window);
        GetWindowThreadProcessId(GetForegroundWindow(),out foregroundProcess);
        if(foregroundProcess==expectedProcess)return;
        uint currentThread=GetCurrentThreadId();bool attached=false;
        try {
            if(foregroundThread!=0 && foregroundThread!=currentThread) {
                attached=AttachThreadInput(currentThread,foregroundThread,true);
                if(!attached)throw new InvalidOperationException("BSUI_ACTIVATION_DENIED");
            }
            BringWindowToTop(window);SetForegroundWindow(window);
        } finally {
            if(attached && !AttachThreadInput(currentThread,foregroundThread,false))
                throw new InvalidOperationException("BSUI_ACTIVATION_DETACH_FAILED");
        }
    }
    public static bool InteractiveDesktop() {
        IntPtr desktop=OpenInputDesktop(0,false,1);
        if(desktop==IntPtr.Zero) return false;
        try {
            uint needed; var name=new StringBuilder(256);
            return GetUserObjectInformation(desktop,2,name,512,out needed)
                && String.Equals(name.ToString(),"Default",StringComparison.OrdinalIgnoreCase);
        } finally { CloseDesktop(desktop); }
    }
}
'@ | Out-Null
    }
}

function Assert-BootstrapDesktop {
    if (-not [Environment]::UserInteractive -or -not [BootstrapNative]::InteractiveDesktop()) {
        Fail-Bootstrap 'DESKTOP_UNAVAILABLE'
    }
}

function Get-BootstrapProcessToken($Process) {
    $Process.Refresh()
    if ($Process.HasExited -or $Process.ProcessName -cne 'Resolve') { Fail-Bootstrap 'PROCESS_CHANGED' }
    $path=[IO.Path]::GetFullPath($Process.MainModule.FileName)
    if (-not [string]::Equals($path,(Get-BootstrapOfficialExecutable),
        [StringComparison]::OrdinalIgnoreCase)) { Fail-Bootstrap 'WRONG_PROCESS' }
    $result=@{
        pid=[long]$Process.Id; creation_time=$Process.StartTime.ToUniversalTime().Ticks.ToString();
        executable_path=$path; window_handle=[long]0; window_title='';
        project_name_hint=$null; window_state='normal'
    }
    $Process.Refresh()
    if ($Process.HasExited) { Fail-Bootstrap 'PROCESS_CHANGED' }
    return $result
}

function Add-BootstrapWindowCandidate($Window) {
    if ($script:WindowCandidates.Count -ge 16) { $script:WindowCandidatesTruncated=$true; return }
    $current=$Window.Current
    $script:WindowCandidates+=,@{
        pid=[long]$current.ProcessId; class_name=([string]$current.ClassName).Substring(0,[Math]::Min(128,([string]$current.ClassName).Length));
        automation_id=([string]$current.AutomationId).Substring(0,[Math]::Min(256,([string]$current.AutomationId).Length));
        name=([string]$current.Name).Substring(0,[Math]::Min(512,([string]$current.Name).Length));
        native_handle=[long]$current.NativeWindowHandle; control_type=[string]$current.ControlType.ProgrammaticName;
        is_offscreen=[bool]$current.IsOffscreen
    }
}

function Find-BootstrapMainInRoots([object[]]$Roots,[long]$ProcessId) {
    if ($Roots.Count -gt 64) { Fail-Bootstrap 'WINDOW_TREE_LIMIT' }
    $classCondition=[System.Windows.Automation.OrCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ClassNameProperty,'UiMainWindowImp'),
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ClassNameProperty,'UiMainWindow'))
    $editorCondition=[System.Windows.Automation.OrCondition]::new($classCondition,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,'UiMainWindow'))
    $managerCondition=[System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,'UiProjectManagerDialog')
    $windowCondition=[System.Windows.Automation.OrCondition]::new($editorCondition,$managerCondition)
    $windows=@(); $seen=@{}
    foreach ($root in $Roots) {
        # Qt can expose the editor below a generic top-level Resolve container.
        # Search only that process's roots and the observed fixed editor identity.
        $candidates=@($root)
        $candidates+=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$windowCondition))
        if ($candidates.Count -gt 64) { Fail-Bootstrap 'WINDOW_TREE_LIMIT' }
        foreach ($candidate in $candidates) {
            Add-BootstrapWindowCandidate $candidate
            # On a cold launch only the Project Manager window exists. Bind its
            # exact observed identity; generic Qt containers are not editors.
            $manager=($candidate.Current.ClassName -ceq 'ProjectManager::UiProjectManagerDialogImp' -and
                $candidate.Current.AutomationId -ceq 'UiProjectManagerDialog' -and
                $candidate.Current.Name -ceq 'Project Manager' -and -not $candidate.Current.IsOffscreen)
            if ($candidate.Current.ProcessId -ne $ProcessId -or
                ($candidate.Current.ClassName -cnotin @('UiMainWindowImp','UiMainWindow') -and
                    $candidate.Current.AutomationId -cne 'UiMainWindow' -and -not $manager) -or
                $candidate.Current.ControlType -ne [System.Windows.Automation.ControlType]::Window -or
                $candidate.Current.NativeWindowHandle -le 0) { continue }
            $identity=$candidate.GetRuntimeId() -join ':'
            if (-not $seen.ContainsKey($identity)) { $seen[$identity]=$true; $windows+=,$candidate }
        }
    }
    if ($windows.Count -gt 1) { Fail-Bootstrap 'MAIN_WINDOW_AMBIGUOUS' }
    if ($windows.Count -eq 0) { return $null }
    return $windows[0]
}

function Get-BootstrapMain([long]$ProcessId) {
    # MainWindowHandle can identify Resolve's generic container instead. Bind the
    # unique actual editor by UIA class and process, including minimized UI.
    $script:WindowCandidates=@(); $script:WindowCandidatesTruncated=$false
    $roots=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty,[int]$ProcessId)))
    return Find-BootstrapMainInRoots $roots $ProcessId
}

function Get-BootstrapWindowState($Main) {
    $pattern=$null
    if (-not $Main.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$pattern)) {
        Fail-Bootstrap 'WINDOW_STATE_UNAVAILABLE'
    }
    switch ($pattern.Current.WindowVisualState.ToString()) {
        'Normal' { return 'normal' }
        'Maximized' { return 'maximized' }
        'Minimized' { return 'minimized' }
        default { Fail-Bootstrap 'WINDOW_STATE_UNAVAILABLE' }
    }
}

function Test-BootstrapModal($Main,[long]$ProcessId) {
    if (-not $Main.Current.IsEnabled) { return $true }
    $condition=[System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ProcessIdProperty,[int]$ProcessId)
    $items=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children,$condition))
    $items+=@($Main.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Window)))
    if ($items.Count -gt 256) { Fail-Bootstrap 'WINDOW_TREE_LIMIT' }
    foreach ($item in $items) {
        if ($item.Current.IsOffscreen -or [System.Windows.Automation.Automation]::Compare($item,$Main)) { continue }
        $pattern=$null
        if ($item.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$pattern) -and
            $pattern.Current.IsModal) { return $true }
        if ($item.Current.ClassName -match 'Dialog|MessageBox') { return $true }
    }
    return $false
}

function Get-BootstrapInspection {
    $result=@{
        ok=$true; operation='inspect'; backend='windows_bootstrap';
        state='resolve_not_running'; eligible=$false; process=$null;
        project_state='unverified'; native_connection_verified=$false; dispatch_started=$false;
        window_candidates=@(); window_candidates_truncated=$false
    }
    try { Assert-BootstrapDesktop }
    catch { $result.state='desktop_unavailable'; return $result }
    $enumerationErrors=@()
    $processes=@(Get-Process -Name Resolve -ErrorAction SilentlyContinue -ErrorVariable enumerationErrors)
    foreach ($enumerationError in $enumerationErrors) {
        if ($enumerationError.FullyQualifiedErrorId -cne
            'NoProcessFoundForGivenName,Microsoft.PowerShell.Commands.GetProcessCommand') {
            Fail-Bootstrap 'PROCESS_ENUMERATION_UNAVAILABLE'
        }
    }
    if ($processes.Count -gt 1) { $result.state='process_ambiguous'; return $result }
    if ($processes.Count -eq 0) { return $result }
    $token=Get-BootstrapProcessToken $processes[0]
    $result.process=$token
    $main=Get-BootstrapMain $token.pid
    $result.window_candidates=@($script:WindowCandidates)
    $result.window_candidates_truncated=[bool]$script:WindowCandidatesTruncated
    if ($null -eq $main) { $result.state='window_unavailable'; return $result }
    $token.window_handle=[long]$main.Current.NativeWindowHandle
    $token.window_title=[string]$main.Current.Name
    $token.window_state=Get-BootstrapWindowState $main
    $token.project_name_hint=Get-BootstrapProjectHint $token.window_title
    if (Test-BootstrapModal $main $token.pid) { $result.state='modal_blocked'; return $result }
    if ($null -eq $token.project_name_hint) { $result.state='project_manager'; return $result }
    $result.state='project_window'; $result.eligible=$true
    return $result
}

function Assert-BootstrapContext {
    Assert-BootstrapDesktop
    $process=Get-Process -Id $script:ExpectedProcess.pid -ErrorAction Stop
    $token=Get-BootstrapProcessToken $process
    if ($token.creation_time -cne $script:ExpectedProcess.creation_time -or
        -not [string]::Equals($token.executable_path,$script:ExpectedProcess.executable_path,
            [StringComparison]::OrdinalIgnoreCase)) { Fail-Bootstrap 'PROCESS_CHANGED' }
    $main=Get-BootstrapMain $token.pid
    if ($null -eq $main -or [long]$main.Current.NativeWindowHandle -ne $script:ExpectedProcess.window_handle -or
        $main.Current.Name -cne $script:ExpectedProcess.window_title) { Fail-Bootstrap 'PROJECT_WINDOW_CHANGED' }
    if (Test-BootstrapModal $main $token.pid) { Fail-Bootstrap 'MODAL_BLOCKED' }
    return $main
}

function Assert-BootstrapForeground {
    [uint32]$foregroundProcess=0
    [void][BootstrapNative]::GetWindowThreadProcessId([BootstrapNative]::GetForegroundWindow(),[ref]$foregroundProcess)
    if ($foregroundProcess -ne $script:ExpectedProcess.pid) { Fail-Bootstrap 'FOCUS_LOST' }
}

function Activate-BootstrapWindow {
    $null=Assert-BootstrapContext
    [BootstrapNative]::ActivateWindow([IntPtr]::new($script:ExpectedProcess.window_handle),
        [uint32]$script:ExpectedProcess.pid)
    Start-Sleep -Milliseconds 150
    $null=Assert-BootstrapContext
    Assert-BootstrapForeground
}

function Get-BootstrapScriptHash {
    if (-not $env:APPDATA) { Fail-Bootstrap 'APPDATA_UNAVAILABLE' }
    $path=Join-Path $env:APPDATA 'Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\Resolve Agent Marker.lua'
    $entry=Get-Item -LiteralPath $path -Force -ErrorAction Stop
    $cursor=$entry
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Fail-Bootstrap 'LINKED_SCRIPT_PATH' }
        $cursor=if ($cursor -is [IO.FileInfo]) { $cursor.Directory } else { $cursor.Parent }
    }
    if ($entry -isnot [IO.FileInfo] -or $entry.Length -gt 4194304) { Fail-Bootstrap 'MANAGED_SCRIPT_UNVERIFIED' }
    $stream=[IO.File]::OpenRead($path)
    try {
        $buffer=[byte[]]::new(4194305); $count=0
        while ($count -lt $buffer.Length) {
            $read=$stream.Read($buffer,$count,$buffer.Length-$count)
            if ($read -eq 0) { break }
            $count+=$read
        }
        if ($count -gt 4194304) { Fail-Bootstrap 'MANAGED_SCRIPT_UNVERIFIED' }
        $data=[byte[]]::new($count)
        [Array]::Copy($buffer,$data,$count)
    } finally { $stream.Dispose() }
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($data).TrimStart([char]0xfeff)
    if (-not $text.Replace("`r`n","`n").Replace("`r","`n").StartsWith(
        "-- DaVinci Resolve Agent: managed Free marker menu script`n",[StringComparison]::Ordinal)) {
        Fail-Bootstrap 'UNMANAGED_SCRIPT'
    }
    $sha=[Security.Cryptography.SHA256]::Create()
    try { $hash=([BitConverter]::ToString($sha.ComputeHash($data))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($hash -cne $script:ExpectedSha256) { Fail-Bootstrap 'MANAGED_SCRIPT_CHANGED' }
    return $hash
}

function Find-BootstrapMenuItem([string[]]$Names, [bool]$Optional = $false) {
    $null=Assert-BootstrapContext
    $roots=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
            [int]$script:ExpectedProcess.pid)))
    if ($roots.Count -gt 64) { Fail-Bootstrap 'WINDOW_TREE_LIMIT' }
    $matches=@(); $seen=@{}
    foreach ($root in $roots) {
        $items=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::MenuItem)))
        if ($items.Count -gt 1024) { Fail-Bootstrap 'MENU_TREE_LIMIT' }
        foreach ($item in $items) {
            if ($item.Current.ProcessId -ne $script:ExpectedProcess.pid -or $item.Current.IsOffscreen -or
                -not $item.Current.IsEnabled -or $item.Current.Name -cnotin $Names) { continue }
            $identity=$item.GetRuntimeId() -join ':'
            if (-not $seen.ContainsKey($identity)) { $seen[$identity]=$true; $matches+=,$item }
        }
    }
    if ($matches.Count -gt 1) { Fail-Bootstrap 'MENU_ITEM_AMBIGUOUS' }
    if ($matches.Count -eq 0) {
        if ($Optional) { return $null }
        Fail-Bootstrap 'MENU_ITEM_UNAVAILABLE'
    }
    return $matches[0]
}

function Expand-BootstrapMenu($Item) {
    $null=Assert-BootstrapContext
    Assert-BootstrapForeground
    $expand=$null
    if ($Item.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expand)) {
        if ($expand.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Collapsed) {
            $expand.Expand()
        } elseif ($expand.Current.ExpandCollapseState -ne [System.Windows.Automation.ExpandCollapseState]::Expanded) {
            Fail-Bootstrap 'MENU_EXPANSION_UNAVAILABLE'
        }
    } else {
        $invoke=$null
        if (-not $Item.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)) {
            Fail-Bootstrap 'MENU_PATTERN_UNAVAILABLE'
        }
        $invoke.Invoke()
    }
    Start-Sleep -Milliseconds 200
}

function Invoke-BootstrapManagedMarker($Request) {
    $script:ExpectedProcess=$Request.expected_process
    $script:ExpectedSha256=$Request.expected_sha256
    $main=Assert-BootstrapContext
    $hash=Get-BootstrapScriptHash
    $state=Get-BootstrapWindowState $main
    if ($state -cne $script:ExpectedProcess.window_state) { Fail-Bootstrap 'WINDOW_STATE_CHANGED' }
    if ($state -ceq 'minimized') {
        $pattern=$null
        if (-not $main.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$pattern)) {
            Fail-Bootstrap 'WINDOW_RESTORE_UNAVAILABLE'
        }
        $pattern.SetWindowVisualState([System.Windows.Automation.WindowVisualState]::Normal)
        $script:RestoredMinimized=$true
        Start-Sleep -Milliseconds 200
        $main=Assert-BootstrapContext
        if ((Get-BootstrapWindowState $main) -ceq 'minimized') { Fail-Bootstrap 'WINDOW_RESTORE_FAILED' }
    }
    if ($main.Current.IsOffscreen) { Fail-Bootstrap 'WINDOW_OFFSCREEN' }
    Activate-BootstrapWindow
    Expand-BootstrapMenu (Find-BootstrapMenuItem @('Workspace'))
    Expand-BootstrapMenu (Find-BootstrapMenuItem @('Scripts'))
    $menuPath=@('Workspace','Scripts')
    $utility=Find-BootstrapMenuItem @('Utility') $true
    if ($null -ne $utility) { Expand-BootstrapMenu $utility; $menuPath+=,'Utility' }
    $item=Find-BootstrapMenuItem @('Resolve Agent Marker','Resolve Agent Marker.lua')
    $invoke=$null
    if (-not $item.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)) {
        Fail-Bootstrap 'SCRIPT_INVOKE_UNAVAILABLE'
    }
    $null=Assert-BootstrapContext
    Assert-BootstrapForeground
    $null=Get-BootstrapScriptHash
    # No retry after this boundary, even when Invoke blocks or throws. A native
    # round trip, not this driver response, determines whether startup succeeded.
    $script:DispatchStarted=$true
    $invoke.Invoke()
    $menuPath+=,'Resolve Agent Marker'
    $process=@{}
    foreach ($property in $script:ExpectedProcess.PSObject.Properties) { $process[$property.Name]=$property.Value }
    if ($script:RestoredMinimized) { $process.window_state='normal' }
    return @{
        ok=$true; operation='invoke_managed_marker'; backend='windows_bootstrap';
        state='dispatch_requested'; eligible=$true; process=$process;
        project_state='unverified'; native_connection_verified=$false; dispatch_started=$true;
        script_sha256=$hash; menu_path=@($menuPath); minimized_restored=[bool]$script:RestoredMinimized
    }
}

try {
    $inputBuffer=[char[]]::new(8193)
    $inputLength=[Console]::In.ReadBlock($inputBuffer,0,$inputBuffer.Length)
    if ($inputLength -gt 8192) { Fail-Bootstrap 'INVALID_REQUEST' }
    $request=ConvertTo-BootstrapRequest ([string]::new($inputBuffer,0,$inputLength))
    Initialize-BootstrapRuntime
    $result=if ($request.operation -ceq 'inspect') { Get-BootstrapInspection }
        else { Invoke-BootstrapManagedMarker $request }
    [Console]::WriteLine(($result | ConvertTo-Json -Depth 6 -Compress))
    exit 0
} catch {
    $code='DRIVER_FAILURE'
    if ($_.Exception.Message -match 'BSUI_([A-Z][A-Z0-9_]+)') { $code=$Matches[1] }
    $phase=if ($script:DispatchStarted) { 'after_dispatch' } else { 'before_dispatch' }
    [Console]::WriteLine((@{ ok=$false; error=@{ code=$code;
        uncertain=[bool]$script:DispatchStarted; phase=$phase } } | ConvertTo-Json -Depth 4 -Compress))
    exit 1
}
