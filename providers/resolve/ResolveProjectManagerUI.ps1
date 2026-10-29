# Fixed semantic Project Manager adapter. No caller script, selector, point or key.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$script:OpenDispatched=$false
$script:ViewTouched=$false
$script:ExportTouched=$false
$script:OperationDiagnostics=@{}

function Fail-ProjectManager([string]$Code) { throw ('PMUI_'+$Code) }

function Limit-ProjectManagerText($Value,[int]$Maximum) {
    $text=[string]$Value
    return $text.Substring(0,[Math]::Min($text.Length,$Maximum))
}

function Get-ProjectManagerExceptionDiagnostics($Record) {
    $functionName='';$lineNumber=0;$invocation=$Record.InvocationInfo
    if($null -ne $invocation){
        $lineNumber=[int]$invocation.ScriptLineNumber
        if($null -ne $invocation.MyCommand){$functionName=[string]$invocation.MyCommand.Name}
    }
    return @{exception_type=(Limit-ProjectManagerText $Record.Exception.GetType().FullName 128);
        exception_message=(Limit-ProjectManagerText $Record.Exception.Message 384);
        function=(Limit-ProjectManagerText $functionName 128);line=$lineNumber}
}

function Assert-ProjectManagerKeys($Object,[string[]]$Keys) {
    if ($Object -isnot [pscustomobject] -or
        ((@($Object.PSObject.Properties.Name|Sort-Object)-join ',') -cne
         (@($Keys|Sort-Object)-join ','))) { Fail-ProjectManager 'INVALID_REQUEST' }
}

function Assert-ProjectManagerUniqueKeys([string]$Text) {
    $objects=[Collections.Generic.Stack[hashtable]]::new()
    for ($i=0;$i -lt $Text.Length;$i++) {
        if ($Text[$i] -ceq '{') { $objects.Push(@{}); continue }
        if ($Text[$i] -ceq '}') { if($objects.Count -gt 0){$null=$objects.Pop()}; continue }
        if ($Text[$i] -cne '"') { continue }
        $start=$i; $i++
        while ($i -lt $Text.Length -and $Text[$i] -cne '"') {
            if($Text[$i] -ceq '\'){$i++}; $i++
        }
        if ($i -ge $Text.Length) { Fail-ProjectManager 'INVALID_REQUEST' }
        $next=$i+1
        while($next -lt $Text.Length -and [char]::IsWhiteSpace($Text[$next])){$next++}
        if ($next -lt $Text.Length -and $Text[$next] -ceq ':' -and $objects.Count -gt 0) {
            try {$key=ConvertFrom-Json $Text.Substring($start,$i-$start+1)}
            catch {Fail-ProjectManager 'INVALID_REQUEST'}
            $current=$objects.Peek()
            if($current.ContainsKey($key)){Fail-ProjectManager 'INVALID_REQUEST'}
            $current[$key]=$true
        }
    }
}

function Test-ProjectManagerName($Value) {
    return $Value -is [string] -and $Value.Length -gt 0 -and $Value.Length -le 256 -and
        $Value -ceq $Value.Trim() -and $Value -notmatch '[\x00-\x1f\x7f]'
}

function Assert-ProjectManagerExportDialogToken($Dialog,$Expected) {
    Assert-ProjectManagerKeys $Dialog @('ownership_token','pid','creation_time','handle','title',
        'class_name','automation_id','runtime_id','owner_handles')
    if($Dialog.ownership_token -isnot [string] -or $Dialog.ownership_token -cnotmatch '^[0-9a-f]{32}\z' -or
        ($Dialog.pid -isnot [int] -and $Dialog.pid -isnot [long]) -or
        $Dialog.pid -ne $Expected.pid -or $Dialog.creation_time -isnot [string] -or
        $Dialog.creation_time -cne $Expected.creation_time -or
        ($Dialog.handle -isnot [int] -and $Dialog.handle -isnot [long]) -or $Dialog.handle -le 0 -or
        $Dialog.handle -eq $Expected.window_handle -or $Dialog.title -cne 'Export Project File' -or
        $Dialog.class_name -cne '#32770' -or $Dialog.automation_id -cne '' -or
        $Dialog.runtime_id -isnot [array] -or $Dialog.runtime_id.Count -lt 1 -or $Dialog.runtime_id.Count -gt 16 -or
        $Dialog.owner_handles -isnot [array] -or $Dialog.owner_handles.Count -lt 1 -or $Dialog.owner_handles.Count -gt 8 -or
        $Expected.window_handle -notin $Dialog.owner_handles){Fail-ProjectManager 'INVALID_REQUEST'}
    foreach($value in $Dialog.runtime_id){
        if(($value -isnot [int] -and $value -isnot [long]) -or
            $value -lt [int32]::MinValue -or $value -gt [int32]::MaxValue){Fail-ProjectManager 'INVALID_REQUEST'}
    }
    foreach($value in $Dialog.owner_handles){
        if(($value -isnot [int] -and $value -isnot [long]) -or $value -le 0){Fail-ProjectManager 'INVALID_REQUEST'}
    }
}

function Assert-ProjectManagerExportDestinationSyntax($Path) {
    if($Path -isnot [string] -or $Path.Length -gt 2048 -or $Path -match '[\x00-\x1f\x7f]' -or
        -not [IO.Path]::IsPathRooted($Path) -or [IO.Path]::GetPathRoot($Path) -cnotmatch '^[A-Za-z]:\\\z' -or
        -not [string]::Equals([IO.Path]::GetFullPath($Path),$Path,[StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($Path) -cne 'candidate.drp' -or
        [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($Path)) -cnotmatch '^[0-9a-f]{32}\z'){
        Fail-ProjectManager 'INVALID_REQUEST'
    }
}

function Assert-ProjectManagerExpectedProcess($Process) {
    Assert-ProjectManagerKeys $Process @('pid','creation_time','executable_path',
        'window_handle','window_title','project_name_hint','window_state')
    if(($Process.pid -isnot [int] -and $Process.pid -isnot [long]) -or $Process.pid -le 0 -or
        $Process.pid -gt [uint32]::MaxValue -or $Process.creation_time -isnot [string] -or
        $Process.creation_time -cnotmatch '^[1-9][0-9]{0,18}\z' -or
        [decimal]$Process.creation_time -gt 3155378975999999999 -or
        $Process.executable_path -isnot [string] -or $Process.executable_path.Length -gt 1024 -or
        -not [string]::Equals([IO.Path]::GetFullPath($Process.executable_path),
            (Get-ProjectManagerExecutable),[StringComparison]::OrdinalIgnoreCase) -or
        ($Process.window_handle -isnot [int] -and $Process.window_handle -isnot [long]) -or
        $Process.window_handle -le 0 -or $Process.window_title -cne 'Project Manager' -or
        $null -ne $Process.project_name_hint -or
        $Process.window_state -cnotin @('normal','minimized','maximized')) {
        Fail-ProjectManager 'INVALID_REQUEST'
    }
}

function ConvertTo-ProjectManagerRequest([string]$Text) {
    if(-not $Text -or $Text.Length -gt 16384){Fail-ProjectManager 'INVALID_REQUEST'}
    Assert-ProjectManagerUniqueKeys $Text
    try {$request=ConvertFrom-Json $Text -ErrorAction Stop}
    catch {Fail-ProjectManager 'INVALID_REQUEST'}
    if($request -isnot [pscustomobject] -or $null -eq $request.PSObject.Properties['operation'] -or
        $request.operation -isnot [string]){Fail-ProjectManager 'INVALID_REQUEST'}
    switch -CaseSensitive ($request.operation) {
        'inspect' {Assert-ProjectManagerKeys $request @('operation')}
        'prepare_context_view' {
            Assert-ProjectManagerKeys $request @('operation','expected_process','refresh_view')
            Assert-ProjectManagerExpectedProcess $request.expected_process
            if($request.refresh_view -isnot [bool]){Fail-ProjectManager 'INVALID_REQUEST'}
        }
        'begin_candidate_export' {
            Assert-ProjectManagerKeys $request @('operation','expected_process','ownership_token')
            Assert-ProjectManagerExpectedProcess $request.expected_process
            if($request.ownership_token -isnot [string] -or
                $request.ownership_token -cnotmatch '^[0-9a-f]{32}\z'){Fail-ProjectManager 'INVALID_REQUEST'}
        }
        'export_candidate' {
            Assert-ProjectManagerKeys $request @('operation','expected_process','ownership_token','destination')
            Assert-ProjectManagerExpectedProcess $request.expected_process
            if($request.ownership_token -isnot [string] -or
                $request.ownership_token -cnotmatch '^[0-9a-f]{32}\z'){Fail-ProjectManager 'INVALID_REQUEST'}
            Assert-ProjectManagerExportDestinationSyntax $request.destination
        }
        'cancel_orphaned_probe_export' {
            Assert-ProjectManagerKeys $request @('operation','expected_process','previous_dialog','expected_default_filename')
            Assert-ProjectManagerExpectedProcess $request.expected_process
            Assert-ProjectManagerExportDialogToken $request.previous_dialog $request.expected_process
            if($request.expected_default_filename -isnot [string] -or
                $request.expected_default_filename.Length -le 4 -or $request.expected_default_filename.Length -gt 260 -or
                -not $request.expected_default_filename.EndsWith('.drp',[StringComparison]::Ordinal) -or
                $request.expected_default_filename -match '[/\\\x00-\x1f\x7f]'){Fail-ProjectManager 'INVALID_REQUEST'}
        }
        'inspect_export_dialog' {
            Assert-ProjectManagerKeys $request @('operation','expected_process')
            Assert-ProjectManagerExpectedProcess $request.expected_process
        }
        {$_ -cin @('save_candidate_export','cancel_candidate_export')} {
            $keys=@('operation','expected_process','dialog','last_input_tick')
            if($request.operation -ceq 'save_candidate_export'){$keys+=,'destination'}
            Assert-ProjectManagerKeys $request $keys
            Assert-ProjectManagerExpectedProcess $request.expected_process
            Assert-ProjectManagerExportDialogToken $request.dialog $request.expected_process
            if(($request.last_input_tick -isnot [int] -and $request.last_input_tick -isnot [long]) -or
                $request.last_input_tick -lt 0 -or $request.last_input_tick -gt [uint32]::MaxValue){
                Fail-ProjectManager 'INVALID_REQUEST'
            }
            if($request.operation -ceq 'save_candidate_export'){
                Assert-ProjectManagerExportDestinationSyntax $request.destination
            }
        }
        'begin_candidate_search' {
            Assert-ProjectManagerKeys $request @('operation','expected_process')
            Assert-ProjectManagerExpectedProcess $request.expected_process
        }
        'filter_candidate' {
            Assert-ProjectManagerKeys $request @('operation','expected_process','project_name')
            Assert-ProjectManagerExpectedProcess $request.expected_process
            if(-not(Test-ProjectManagerName $request.project_name)){Fail-ProjectManager 'INVALID_REQUEST'}
        }
        'select_filtered_candidate' {
            Assert-ProjectManagerKeys $request @('operation','expected_process','project_name','advance_once')
            Assert-ProjectManagerExpectedProcess $request.expected_process
            if(-not(Test-ProjectManagerName $request.project_name) -or
                $request.advance_once -isnot [bool]){Fail-ProjectManager 'INVALID_REQUEST'}
        }
        'open_existing' {
            Assert-ProjectManagerKeys $request @('operation','expected_process','library_name','folder_path',
                'project_name','last_input_tick','deadline_utc_ms','candidate_export')
            Assert-ProjectManagerExpectedProcess $request.expected_process
            if(-not(Test-ProjectManagerName $request.library_name) -or
                -not(Test-ProjectManagerName $request.project_name) -or
                $request.folder_path -isnot [array] -or $request.folder_path.Count -gt 16) {
                Fail-ProjectManager 'INVALID_REQUEST'
            }
            foreach($folder in $request.folder_path){
                if(-not(Test-ProjectManagerName $folder)){Fail-ProjectManager 'INVALID_REQUEST'}
            }
            if($request.folder_path.Count -ne 0 -or
                ($request.last_input_tick -isnot [int] -and $request.last_input_tick -isnot [long]) -or
                $request.last_input_tick -lt 0 -or $request.last_input_tick -gt [uint32]::MaxValue -or
                ($request.deadline_utc_ms -isnot [int] -and $request.deadline_utc_ms -isnot [long]) -or
                $request.deadline_utc_ms -le 0 -or $request.deadline_utc_ms -gt 253402300799999){
                Fail-ProjectManager 'INVALID_REQUEST'
            }
            Assert-ProjectManagerKeys $request.candidate_export @('path','sha256','size','mtime_ns')
            $proof=$request.candidate_export
            if($proof.path -isnot [string] -or $proof.path.Length -gt 2048 -or
                $proof.path -match '[\x00-\x1f\x7f]' -or
                -not [IO.Path]::IsPathRooted($proof.path) -or
                [IO.Path]::GetPathRoot($proof.path) -cnotmatch '^[A-Za-z]:\\\z' -or
                -not [string]::Equals([IO.Path]::GetFullPath($proof.path),$proof.path,
                    [StringComparison]::OrdinalIgnoreCase) -or
                [IO.Path]::GetFileName($proof.path) -cne 'candidate.drp' -or
                [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($proof.path)) -cnotmatch '^[0-9a-f]{32}\z' -or
                $proof.sha256 -isnot [string] -or $proof.sha256 -cnotmatch '^[0-9a-f]{64}\z' -or
                ($proof.size -isnot [int] -and $proof.size -isnot [long]) -or
                $proof.size -le 0 -or $proof.size -gt 536870912 -or
                ($proof.mtime_ns -isnot [int] -and $proof.mtime_ns -isnot [long]) -or
                $proof.mtime_ns -le 0){Fail-ProjectManager 'INVALID_REQUEST'}
        }
        default {Fail-ProjectManager 'INVALID_OPERATION'}
    }
    return $request
}

function Get-ProjectManagerExecutable {
    # Resolve's fixed installation boundary comes from the OS known folder,
    # independent of filtered stdio environments or caller-supplied values.
    $knownProgramFiles=[Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
    if(-not $knownProgramFiles -or -not [IO.Path]::IsPathRooted($knownProgramFiles)){
        Fail-ProjectManager 'INSTALLATION_PATH_UNAVAILABLE'
    }
    return [IO.Path]::GetFullPath((Join-Path $knownProgramFiles 'Blackmagic Design\DaVinci Resolve\Resolve.exe'))
}

function Initialize-ProjectManagerRuntime {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName WindowsBase
    if(-not('ProjectManagerNative' -as [type])) {
        Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Reflection;
public sealed class ProjectManagerDispatchState {
    public volatile bool Completed;
    public volatile bool Succeeded;
    public string Error="";
}
public static class ProjectManagerNative {
    [DllImport("user32.dll",SetLastError=true)] private static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] private static extern bool GetUserObjectInformation(IntPtr obj,int index,StringBuilder value,uint length,out uint needed);
    [DllImport("user32.dll")] private static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr window,uint command);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window,out uint process);
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr window,uint flags);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window,int command);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr window);
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint source,uint target,bool attach);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll",SetLastError=true)] private static extern bool PostMessage(IntPtr window,uint message,UIntPtr wparam,IntPtr lparam);
    [StructLayout(LayoutKind.Sequential)] private struct LastInput {
        public uint Size; public uint Tick;
    }
    [DllImport("user32.dll")] private static extern bool GetLastInputInfo(ref LastInput info);
    public static uint LastInputTick() {
        var info=new LastInput();info.Size=(uint)Marshal.SizeOf(info);
        if(!GetLastInputInfo(ref info))throw new InvalidOperationException("PMUI_INPUT_STATE_UNAVAILABLE");
        return info.Tick;
    }
    public static uint ForegroundProcess() {
        uint process;GetWindowThreadProcessId(GetForegroundWindow(),out process);return process;
    }
    public static long ForegroundHandle() { return GetForegroundWindow().ToInt64(); }
    public static long ForegroundRootOwner() {
        return GetAncestor(GetForegroundWindow(),3).ToInt64();
    }
    private static bool IsProjectManagerForeground(IntPtr window,uint process) {
        return ForegroundProcess()==process && ForegroundRootOwner()==window.ToInt64();
    }
    private static bool WaitProjectManagerForeground(IntPtr window,uint process) {
        for(int index=0;index<5;index++) {
            if(IsProjectManagerForeground(window,process))return true;
            Thread.Sleep(20);
        }
        return IsProjectManagerForeground(window,process);
    }
    public static void ActivateProjectManager(IntPtr window,uint expectedProcess) {
        uint process;GetWindowThreadProcessId(window,out process);
        if(process!=expectedProcess || GetAncestor(window,3)!=window)
            throw new InvalidOperationException("PMUI_PROCESS_OR_WINDOW_CHANGED");
        if(IsIconic(window))ShowWindow(window,9); // Restore only a minimized PM.
        if(IsProjectManagerForeground(window,process))return;
        BringWindowToTop(window);SetForegroundWindow(window);
        if(WaitProjectManagerForeground(window,process))return;
        uint foregroundProcess;
        uint foregroundThread=GetWindowThreadProcessId(GetForegroundWindow(),out foregroundProcess);
        uint currentThread=GetCurrentThreadId();bool attached=false;
        try {
            if(foregroundThread!=0 && foregroundThread!=currentThread) {
                attached=AttachThreadInput(currentThread,foregroundThread,true);
                if(!attached)throw new InvalidOperationException("PMUI_ACTIVATION_DENIED");
            }
            BringWindowToTop(window);SetForegroundWindow(window);
        } finally {
            if(attached && !AttachThreadInput(currentThread,foregroundThread,false))
                throw new InvalidOperationException("PMUI_ACTIVATION_DETACH_FAILED");
        }
        if(!WaitProjectManagerForeground(window,process))
            throw new InvalidOperationException("PMUI_ACTIVATION_DENIED");
    }
    private static void AssertSelectionWindow(IntPtr window,uint expectedProcess) {
        uint process;GetWindowThreadProcessId(window,out process);
        if(process!=expectedProcess || !IsProjectManagerForeground(window,process))
            throw new InvalidOperationException("PMUI_PROCESS_OR_WINDOW_CHANGED");
        foreach(int modifier in new int[]{0x10,0x11,0x12,0x5B,0x5C})
            if((GetAsyncKeyState(modifier)&0x8000)!=0)
                throw new InvalidOperationException("PMUI_MODIFIER_HELD");
    }
    public static void PostHome(IntPtr window,uint expectedProcess) {
        AssertSelectionWindow(window,expectedProcess);
        // Exactly Home down/up, directed to the verified Project Manager. No
        // global input and no caller-controlled key/message are exposed.
        if(!PostMessage(window,0x100,new UIntPtr(0x24),new IntPtr(0x01470001)))
            throw new InvalidOperationException("PMUI_HOME_POST_FAILED");
        if(!PostMessage(window,0x101,new UIntPtr(0x24),new IntPtr(unchecked((int)0xC1470001))))
            throw new InvalidOperationException("PMUI_HOME_POST_FAILED");
    }
    public static void PostDown(IntPtr window,uint expectedProcess) {
        AssertSelectionWindow(window,expectedProcess);
        // One fixed Down pair; no caller-selected key or repetition.
        if(!PostMessage(window,0x100,new UIntPtr(0x28),new IntPtr(0x01500001)))
            throw new InvalidOperationException("PMUI_DOWN_POST_FAILED");
        if(!PostMessage(window,0x101,new UIntPtr(0x28),new IntPtr(unchecked((int)0xC1500001))))
            throw new InvalidOperationException("PMUI_DOWN_POST_FAILED");
    }
    public static ProjectManagerDispatchState BeginInvoke(object pattern) {
        var state=new ProjectManagerDispatchState();
        var thread=new Thread(delegate() {
            try {
                pattern.GetType().GetMethod("Invoke",Type.EmptyTypes).Invoke(pattern,null);
                state.Succeeded=true;
            } catch(Exception error) {
                state.Succeeded=false;
                var cause=error.GetBaseException();state.Error=cause.GetType().Name+": "+cause.Message;
            }
            finally { state.Completed=true; }
        });
        thread.IsBackground=true;thread.SetApartmentState(ApartmentState.MTA);
        thread.Start();return state;
    }
    public static bool InteractiveDesktop() {
        IntPtr desktop=OpenInputDesktop(0,false,1);
        if(desktop==IntPtr.Zero)return false;
        try {
            uint needed; var name=new StringBuilder(256);
            return GetUserObjectInformation(desktop,2,name,512,out needed)
                && String.Equals(name.ToString(),"Default",StringComparison.OrdinalIgnoreCase);
        } finally {CloseDesktop(desktop);}
    }
}
'@ | Out-Null
    }
}

function Get-ProjectManagerProcess($Process) {
    $Process.Refresh()
    if($Process.HasExited -or $Process.ProcessName -cne 'Resolve'){Fail-ProjectManager 'PROCESS_CHANGED'}
    $path=[IO.Path]::GetFullPath($Process.MainModule.FileName)
    if(-not [string]::Equals($path,(Get-ProjectManagerExecutable),[StringComparison]::OrdinalIgnoreCase)) {
        Fail-ProjectManager 'WRONG_PROCESS'
    }
    return @{
        pid=[long]$Process.Id; creation_time=$Process.StartTime.ToUniversalTime().Ticks.ToString();
        executable_path=$path; window_handle=[long]0; window_title='';
        project_name_hint=$null; window_state='normal'
    }
}

function Get-ProjectManagerRoots([long]$ProcessId) {
    $roots=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty,[int]$ProcessId)))
    if($roots.Count -gt 64){Fail-ProjectManager 'WINDOW_TREE_LIMIT'}
    return $roots
}

function Find-ProjectManager($Roots,[long]$ProcessId) {
    $found=@(); $seen=@{}
    $condition=[System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,'UiProjectManagerDialog')
    foreach($root in $Roots) {
        $candidates=@($root)+@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition))
        if($candidates.Count -gt 64){Fail-ProjectManager 'WINDOW_TREE_LIMIT'}
        foreach($item in $candidates) {
            if($item.Current.ProcessId -ne $ProcessId -or
                $item.Current.ClassName -cne 'ProjectManager::UiProjectManagerDialogImp' -or
                $item.Current.AutomationId -cne 'UiProjectManagerDialog' -or
                $item.Current.Name -cne 'Project Manager' -or
                $item.Current.ControlType -ne [System.Windows.Automation.ControlType]::Window -or
                $item.Current.NativeWindowHandle -le 0){continue}
            $id=$item.GetRuntimeId()-join ':'
            if(-not $seen.ContainsKey($id)){$seen[$id]=$true;$found+=,$item}
        }
    }
    if($found.Count -gt 1){Fail-ProjectManager 'PROJECT_MANAGER_AMBIGUOUS'}
    if($found.Count -eq 0){return $null}
    return $found[0]
}

function Get-ProjectManagerTree($Manager,[bool]$RawView=$false,[int]$MaxNodes=384) {
    $nodes=@();$elements=@();$truncated=$false
    $queue=[Collections.Generic.Queue[object]]::new()
    $queue.Enqueue(@{Element=$Manager;Parent=$null;Depth=0})
    $walker=if($RawView){[System.Windows.Automation.TreeWalker]::RawViewWalker}
        else{[System.Windows.Automation.TreeWalker]::ControlViewWalker}
    $maxDepth=if($RawView){14}else{10}
    while($queue.Count -gt 0 -and $nodes.Count -lt $MaxNodes) {
        $entry=$queue.Dequeue();$item=$entry.Element;$current=$item.Current
        $index=$nodes.Count
        $node=@{
            id=$index;parent=$entry.Parent;depth=$entry.Depth;
            name=(Limit-ProjectManagerText $current.Name 512);
            automation_id=(Limit-ProjectManagerText $current.AutomationId 256);
            class_name=(Limit-ProjectManagerText $current.ClassName 128);
            control_type=[string]$current.ControlType.ProgrammaticName;
            native_handle=[long]$current.NativeWindowHandle;enabled=[bool]$current.IsEnabled;
            offscreen=[bool]$current.IsOffscreen;selected=$null;value=$null;expand_state=$null;
            invoke=$false;selection_item=$false;selection=$false;expand_collapse=$false;value_pattern=$false;
            toggle=$false;toggle_state=$null;
            help_text=(Limit-ProjectManagerText $current.HelpText 512)
        }
        foreach($pair in @(@('invoke',[System.Windows.Automation.InvokePattern]::Pattern),
            @('selection_item',[System.Windows.Automation.SelectionItemPattern]::Pattern),
            @('selection',[System.Windows.Automation.SelectionPattern]::Pattern),
            @('expand_collapse',[System.Windows.Automation.ExpandCollapsePattern]::Pattern),
            @('toggle',[System.Windows.Automation.TogglePattern]::Pattern),
            @('value_pattern',[System.Windows.Automation.ValuePattern]::Pattern))) {
            $pattern=$null
            if($item.TryGetCurrentPattern($pair[1],[ref]$pattern)) {
                $node[$pair[0]]=$true
                if($pair[0] -ceq 'selection_item'){$node.selected=[bool]$pattern.Current.IsSelected}
                if($pair[0] -ceq 'value_pattern'){$node.value=Limit-ProjectManagerText $pattern.Current.Value 512}
                if($pair[0] -ceq 'expand_collapse'){$node.expand_state=$pattern.Current.ExpandCollapseState.ToString()}
                if($pair[0] -ceq 'toggle'){$node.toggle_state=$pattern.Current.ToggleState.ToString()}
            }
        }
        $nodes+=,$node;$elements+=,$item
        $child=$walker.GetFirstChild($item);$children=0
        while($null -ne $child) {
            if($entry.Depth -ge $maxDepth -or $children -ge 128 -or $queue.Count+$nodes.Count -ge $MaxNodes) {
                $truncated=$true;break
            }
            $queue.Enqueue(@{Element=$child;Parent=$index;Depth=$entry.Depth+1})
            $children++;$child=$walker.GetNextSibling($child)
        }
    }
    if($queue.Count -gt 0){$truncated=$true}
    return @{Nodes=@($nodes);Elements=@($elements);Truncated=$truncated}
}

function Get-ProjectManagerBlocker($Roots,$Manager,[long]$ProcessId) {
    foreach($root in $Roots) {
        if([System.Windows.Automation.Automation]::Compare($root,$Manager)){continue}
        $current=$root.Current
        if($current.ProcessId -ne $ProcessId){continue}
        if(-not $current.IsOffscreen -and $current.ClassName -ceq '#32770' -and
            $current.Name -ceq 'Export Project File'){return 'modal_blocked'}
        if(($current.AutomationId -ceq 'UiMainWindow' -or
            $current.ClassName -cin @('UiMainWindow','UiMainWindowImp')) -and
            ($current.Name -match '^DaVinci Resolve - .+' -or -not $current.IsOffscreen)) {
            return 'other_project_open'
        }
        $window=$null
        if(-not $current.IsOffscreen -and
            $root.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$window) -and
            $window.Current.IsModal){return 'modal_blocked'}
    }
    if(-not $Manager.Current.IsEnabled){return 'modal_blocked'}
    return $null
}

function Get-ProjectManagerAccessibleItem($Element) {
    $current=$Element.Current
    $value=$null;$selected=$null;$pattern=$null
    if($Element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern)){
        $value=Limit-ProjectManagerText $pattern.Current.Value 512
    }
    $pattern=$null
    if($Element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)){
        $selected=[bool]$pattern.Current.IsSelected
    }
    return @{name=(Limit-ProjectManagerText $current.Name 512);
        automation_id=(Limit-ProjectManagerText $current.AutomationId 256);
        control_type=[string]$current.ControlType.ProgrammaticName;value=$value;selected=$selected}
}

function Get-ProjectManagerContainerPatterns($Element) {
    $result=@{supported_patterns=@();pattern_list_truncated=$false;errors=@();
        legacy=@{available=$false;advertised=$false;reason='managed_pattern_api_unavailable'};
        grid=@{available=$false};table=@{available=$false};
        item_container=@{available=$false};text=@{available=$false}}
    try {
        $supported=@($Element.GetSupportedPatterns())
        $result.pattern_list_truncated=$supported.Count -gt 32
        $result.supported_patterns=@($supported|Select-Object -First 32|
            ForEach-Object {Limit-ProjectManagerText $_.ProgrammaticName 128})
        $result.legacy.advertised=[bool](@($result.supported_patterns|
            Where-Object {$_ -match 'LegacyIAccessible'}).Count -gt 0)
    }catch{$result.errors+=,'SUPPORTED_PATTERN_READ_FAILED'}
    $legacyType=[Type]::GetType('System.Windows.Automation.LegacyIAccessiblePattern, UIAutomationClient',$false)
    if($null -ne $legacyType){
        try {
            $legacyPattern=$legacyType.GetField('Pattern').GetValue($null);$pattern=$null
            $result.legacy=@{available=$false;advertised=$result.legacy.advertised;reason='provider_unsupported'}
            if($Element.TryGetCurrentPattern($legacyPattern,[ref]$pattern)){
                $info=$pattern.Current
                $result.legacy=@{available=$true;advertised=$true;reason=$null;
                    name=(Limit-ProjectManagerText $info.Name 512);
                    value=(Limit-ProjectManagerText $info.Value 512);
                    description=(Limit-ProjectManagerText $info.Description 512);
                    role=[long]$info.Role;state=[long]$info.State;child_id=[long]$info.ChildId}
            }
        }catch{$result.errors+=,'LEGACY_PATTERN_READ_FAILED'}
    }
    try {
        $pattern=$null
        if($Element.TryGetCurrentPattern([System.Windows.Automation.GridPattern]::Pattern,[ref]$pattern)){
            $result.grid=@{available=$true}
            $rows=[int]$pattern.Current.RowCount;$columns=[int]$pattern.Current.ColumnCount
            if($rows -lt 0 -or $columns -lt 0){Fail-ProjectManager 'INVALID_GRID_DIMENSIONS'}
            $grid=@{available=$true;row_count=$rows;column_count=$columns;
                cells=@();truncated=($rows -gt 6 -or $columns -gt 4)}
            for($row=0;$row -lt [Math]::Min(6,$rows);$row++){
                for($column=0;$column -lt [Math]::Min(4,$columns);$column++){
                    $cell=Get-ProjectManagerAccessibleItem ($pattern.GetItem($row,$column))
                    $cell.row=$row;$cell.column=$column;$grid.cells+=,$cell
                }
            }
            $result.grid=$grid
        }
    }catch{$result.errors+=,'GRID_PATTERN_READ_FAILED'}
    try {
        $pattern=$null
        if($Element.TryGetCurrentPattern([System.Windows.Automation.TablePattern]::Pattern,[ref]$pattern)){
            $result.table=@{available=$true}
            $rowHeaders=@($pattern.Current.GetRowHeaders());$columnHeaders=@($pattern.Current.GetColumnHeaders())
            $result.table=@{available=$true;row_headers=@($rowHeaders|Select-Object -First 12|
                    ForEach-Object {Get-ProjectManagerAccessibleItem $_});
                column_headers=@($columnHeaders|Select-Object -First 12|
                    ForEach-Object {Get-ProjectManagerAccessibleItem $_});
                truncated=($rowHeaders.Count -gt 12 -or $columnHeaders.Count -gt 12)}
        }
    }catch{$result.errors+=,'TABLE_PATTERN_READ_FAILED'}
    try {
        $pattern=$null
        if($Element.TryGetCurrentPattern([System.Windows.Automation.ItemContainerPattern]::Pattern,[ref]$pattern)){
            $result.item_container=@{available=$true}
            $items=@();$seen=@{};$after=$null;$truncated=$false
            for($index=0;$index -lt 13;$index++){
                # Null property is the API's next-item enumeration, within this
                # fixed container only; no caller controls a property or selector.
                $item=$pattern.FindItemByProperty($after,$null,$null)
                if($null -eq $item){break}
                if($index -ge 12){$truncated=$true;break}
                $identity=$item.GetRuntimeId()-join ':'
                if($seen.ContainsKey($identity)){Fail-ProjectManager 'ITEM_CONTAINER_CYCLE'}
                $seen[$identity]=$true;$items+=,(Get-ProjectManagerAccessibleItem $item);$after=$item
            }
            $result.item_container=@{available=$true;items=@($items);truncated=$truncated}
        }
    }catch{$result.errors+=,'ITEM_CONTAINER_PATTERN_READ_FAILED'}
    try {
        $pattern=$null
        if($Element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$pattern)){
            $result.text=@{available=$true}
            $text=$pattern.DocumentRange.GetText(2049)
            $selection=@($pattern.GetSelection());$selected=@()
            foreach($range in @($selection|Select-Object -First 8)){
                $selected+=,(Limit-ProjectManagerText ($range.GetText(513)) 512)
            }
            $result.text=@{available=$true;text=(Limit-ProjectManagerText $text 2048);
                selection=@($selected);truncated=($text.Length -gt 2048 -or $selection.Count -gt 8)}
        }
    }catch{$result.errors+=,'TEXT_PATTERN_READ_FAILED'}
    return $result
}

function Get-ProjectManagerContainerAccessibility($Manager,[long]$ProcessId) {
    $prefix='UiProjectManagerDialog.frameProjectManagerBorder.m_ProjectManagerView.projManagerContents.splitterProjects.'
    $known=@(
        @{kind='projects';automation_id=$prefix+'m_ViewProjects.frame_8.m_ProjectsWidget';
            class_name='ProjectManager::UiProjectManagerBinView'},
        @{kind='libraries';automation_id=$prefix+'m_ViewDatabase.m_DbBinWidgetListView';
            class_name='ProjectManager::UiProjectManagerDatabaseBinView'})
    $result=@()
    foreach($descriptor in $known){
        $items=@($Manager.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$descriptor.automation_id))|
            Where-Object {$_.Current.ProcessId -eq $ProcessId -and
                $_.Current.ClassName -ceq $descriptor.class_name -and
                $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Group})
        $record=@{kind=$descriptor.kind;automation_id=$descriptor.automation_id;
            class_name=$descriptor.class_name;present=($items.Count -eq 1);
            ambiguous=($items.Count -gt 1);patterns=$null}
        if($items.Count -eq 1){$record.patterns=Get-ProjectManagerContainerPatterns $items[0]}
        $result+=,$record
    }
    return $result
}

function Get-ProjectManagerInspection {
    $result=@{
        ok=$true;operation='inspect';backend='windows_project_manager';state='resolve_not_running';
        eligible=$false;process=$null;project_state='unverified';native_connection_verified=$false;
        dispatch_started=$false;tree=@();tree_truncated=$false;raw_tree=@();raw_tree_truncated=$false;
        container_accessibility=@();
        context=@{library_name=$null;folder_path=$null;project_names=$null;verification='accessibility_tree_only'}
    }
    if(-not [Environment]::UserInteractive -or -not [ProjectManagerNative]::InteractiveDesktop()) {
        $result.state='desktop_unavailable';return $result
    }
    $enumerationErrors=@()
    $processes=@(Get-Process -Name Resolve -ErrorAction SilentlyContinue -ErrorVariable enumerationErrors)
    foreach($enumerationError in $enumerationErrors) {
        if($enumerationError.FullyQualifiedErrorId -cne
            'NoProcessFoundForGivenName,Microsoft.PowerShell.Commands.GetProcessCommand'){
            Fail-ProjectManager 'PROCESS_ENUMERATION_UNAVAILABLE'
        }
    }
    if($processes.Count -gt 1){$result.state='process_ambiguous';return $result}
    if($processes.Count -eq 0){return $result}
    $token=Get-ProjectManagerProcess $processes[0];$result.process=$token
    $roots=@(Get-ProjectManagerRoots $token.pid)
    $manager=Find-ProjectManager $roots $token.pid
    if($null -eq $manager){$result.state='project_manager_unavailable';return $result}
    $token.window_handle=[long]$manager.Current.NativeWindowHandle
    $token.window_title=[string]$manager.Current.Name
    $pattern=$null
    if($manager.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$pattern)){
        $token.window_state=$pattern.Current.WindowVisualState.ToString().ToLowerInvariant()
    }
    $tree=Get-ProjectManagerTree $manager
    $result.tree=@($tree.Nodes);$result.tree_truncated=[bool]$tree.Truncated
    $raw=Get-ProjectManagerTree $manager $true
    $result.raw_tree=@($raw.Nodes);$result.raw_tree_truncated=[bool]$raw.Truncated
    $result.container_accessibility=@(Get-ProjectManagerContainerAccessibility $manager $token.pid)
    $blocker=Get-ProjectManagerBlocker $roots $manager $token.pid
    $again=Get-ProjectManagerProcess $processes[0]
    if($again.creation_time -cne $token.creation_time){Fail-ProjectManager 'PROCESS_CHANGED'}
    $result.state=if($null -ne $blocker){$blocker}else{'project_manager'}
    $result.eligible=$result.state -ceq 'project_manager'
    return $result
}

function Get-ProjectManagerUnixMilliseconds {
    return [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
}

function Assert-ProjectManagerOpenFence($Request) {
    if((Get-ProjectManagerUnixMilliseconds) -ge $Request.deadline_utc_ms){
        Fail-ProjectManager 'TIMEOUT'
    }
    $binding=Assert-ProjectManagerContext $Request.expected_process
    Assert-ProjectManagerLocalMode $binding.Manager $Request.expected_process.pid
    $search=Read-ProjectManagerSearchState $binding.Manager $Request.expected_process.pid
    if($search.Value -cne $Request.project_name){Fail-ProjectManager 'EXACT_NAME_FILTER_REQUIRED'}
    if((Get-ProjectManagerInputTick) -ne $Request.last_input_tick){Fail-ProjectManager 'USER_INPUT_CHANGED'}
    return $binding
}

function Assert-ProjectManagerCandidateFileToken($Proof,$Stream) {
    $cursor=[IO.FileInfo]::new($Proof.path)
    for($index=0;$null -ne $cursor -and $index -lt 128;$index++){
        if(($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){
            Fail-ProjectManager 'LINKED_CANDIDATE_EVIDENCE'
        }
        $cursor=if($cursor -is [IO.FileInfo]){$cursor.Directory}else{$cursor.Parent}
    }
    if($null -ne $cursor){Fail-ProjectManager 'CANDIDATE_PATH_LIMIT'}
    $file=[IO.FileInfo]::new($Proof.path);$file.Refresh()
    $mtime=($file.LastWriteTimeUtc.Ticks-621355968000000000)*100
    if(-not $file.Exists -or $file.Length -ne $Proof.size -or $Stream.Length -ne $Proof.size -or
        $mtime -ne $Proof.mtime_ns){Fail-ProjectManager 'CANDIDATE_EXPORT_CHANGED'}
}

function Open-ProjectManagerCandidateEvidence($Proof) {
    $stream=$null
    try{
        # Read sharing prevents changes/deletion while the final Open is fenced.
        $stream=[IO.File]::Open($Proof.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        Assert-ProjectManagerCandidateFileToken $Proof $stream
        $hasher=[Security.Cryptography.SHA256]::Create()
        try{$actual=[BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}
        finally{$hasher.Dispose()}
        if($actual -cne $Proof.sha256){Fail-ProjectManager 'CANDIDATE_EXPORT_CHANGED'}
        Assert-ProjectManagerCandidateFileToken $Proof $stream
        return $stream
    }catch{if($null -ne $stream){$stream.Dispose()};throw}
}

function Start-ProjectManagerOpenInvoke($Pattern) {
    return [ProjectManagerNative]::BeginInvoke($Pattern)
}

function Open-ProjectManagerExisting($Request) {
    $null=Assert-ProjectManagerOpenFence $Request
    $stream=Open-ProjectManagerCandidateEvidence $Request.candidate_export
    try{
        $binding=Assert-ProjectManagerOpenFence $Request
        $button=Find-ProjectManagerActionButton $binding.Manager 'Open' $Request.expected_process.pid
        $invoke=$null
        if(-not $button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)){
            Fail-ProjectManager 'OPEN_INVOKE_UNAVAILABLE'
        }
        $null=Assert-ProjectManagerOpenFence $Request
        Assert-ProjectManagerCandidateFileToken $Request.candidate_export $stream
        if((Get-ProjectManagerInputTick) -ne $Request.last_input_tick){Fail-ProjectManager 'USER_INPUT_CHANGED'}
        if((Get-ProjectManagerUnixMilliseconds) -ge $Request.deadline_utc_ms){Fail-ProjectManager 'TIMEOUT'}
        $script:OpenDispatched=$true
        $dispatch=Start-ProjectManagerOpenInvoke $invoke
        $clock=[Diagnostics.Stopwatch]::StartNew()
        while(-not $dispatch.Completed -and $clock.Elapsed.TotalSeconds -lt 12){
            Start-Sleep -Milliseconds 20
        }
        if(-not $dispatch.Completed -or -not $dispatch.Succeeded){
            $script:OperationDiagnostics=@{invoke_completed=[bool]$dispatch.Completed;
                invoke_succeeded=[bool]$dispatch.Succeeded;
                invoke_error=(Limit-ProjectManagerText $dispatch.Error 256)}
            Fail-ProjectManager 'OPEN_DISPATCH_UNCERTAIN'
        }
        return @{ok=$true;operation='open_existing';backend='windows_project_manager';
            state='open_requested';eligible=$true;process=$Request.expected_process;
            project_state='unverified';native_connection_verified=$false;dispatch_started=$true;
            library_name=$Request.library_name;folder_path=@();project_name=$Request.project_name}
    }finally{$stream.Dispose()}
}

function Find-ProjectManagerActionButton($Manager,[string]$Action,[long]$ProcessId) {
    $prefix='UiProjectManagerDialog.frameProjectManagerBorder.m_ProjectManagerView.projManagerContents.splitterProjects.m_ViewProjects.projActionBottomFrame.'
    $identifier=switch -CaseSensitive ($Action){
        'Export' {$prefix+'btnExport'}
        'Open' {$prefix+'btnLoad'}
        default {Fail-ProjectManager 'INVALID_INTERNAL_ACTION'}
    }
    $items=@($Manager.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$identifier))|
        Where-Object {$_.Current.ProcessId -eq $ProcessId -and
            $_.Current.ClassName -ceq 'QPushButton' -and $_.Current.Name -ceq $Action -and
            $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
            $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
    if($items.Count -ne 1){Fail-ProjectManager 'ACTION_BUTTON_UNAVAILABLE_OR_AMBIGUOUS'}
    return $items[0]
}

function Assert-ProjectManagerLocalMode($Manager,[long]$ProcessId) {
    $prefix='UiProjectManagerDialog.frameProjectManagerBorder.m_ProjectManagerView.projManagerHeaderFrame.frameProjectManagerTitle.frameProjectManagerTitleLeft.frameDbTypeTabBar.'
    foreach($pair in @(@('Local','On'),@('Network','Off'),@('Cloud','Off'))){
        $items=@($Manager.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$prefix+'btn'+$pair[0]))|
            Where-Object {$_.Current.ProcessId -eq $ProcessId -and
                $_.Current.ClassName -ceq 'QPushButton' -and $_.Current.Name -ceq $pair[0] -and
                $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::CheckBox -and
                $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
        if($items.Count -ne 1){Fail-ProjectManager 'LIBRARY_MODE_UNAVAILABLE_OR_AMBIGUOUS'}
        $state=Read-ProjectManagerViewToggle $items[0]
        if($state.State -cne $pair[1]){Fail-ProjectManager 'LOCAL_LIBRARY_MODE_REQUIRED'}
    }
}

function Get-ProjectManagerWindowElements($Manager,[long]$ProcessId) {
    $items=@(Get-ProjectManagerRoots $ProcessId)
    if($null -ne $Manager){$items+=@($Manager.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Window)))}
    if($items.Count -gt 64){Fail-ProjectManager 'WINDOW_TREE_LIMIT'}
    $result=@();$seen=@{}
    foreach($item in $items){
        if($item.Current.ProcessId -ne $ProcessId -or $item.Current.NativeWindowHandle -le 0){continue}
        $identity=$item.GetRuntimeId()-join ':'
        if(-not $seen.ContainsKey($identity)){$seen[$identity]=$true;$result+=,$item}
    }
    return $result
}

function Get-ProjectManagerOwnerChain([long]$Handle) {
    $owners=@();$cursor=[IntPtr]::new($Handle)
    for($index=0;$index -lt 8;$index++){
        $cursor=[ProjectManagerNative]::GetWindow($cursor,4)
        if($cursor -eq [IntPtr]::Zero){break}
        $owner=$cursor.ToInt64()
        if($owner -in $owners){Fail-ProjectManager 'WINDOW_OWNER_CYCLE'}
        $owners+=,$owner
    }
    return $owners
}

function Get-ProjectManagerInputTick { return [long][ProjectManagerNative]::LastInputTick() }

function Start-ProjectManagerExportInvoke($Pattern) {
    return [ProjectManagerNative]::BeginInvoke($Pattern)
}

function Inspect-ProjectManagerExportDialog($Request) {
    if(-not [Environment]::UserInteractive -or -not [ProjectManagerNative]::InteractiveDesktop()){
        Fail-ProjectManager 'DESKTOP_UNAVAILABLE'
    }
    $token=Get-ProjectManagerProcess (Get-Process -Id $Request.expected_process.pid -ErrorAction Stop)
    if($token.creation_time -cne $Request.expected_process.creation_time){Fail-ProjectManager 'PROCESS_CHANGED'}
    $roots=@(Get-ProjectManagerRoots $token.pid)
    $manager=Find-ProjectManager $roots $token.pid
    $items=@(Get-ProjectManagerWindowElements $manager $token.pid)
    $records=@();$treeCount=0;$truncated=$items.Count -gt 16
    foreach($item in @($items|Select-Object -First 16)){
        $current=$item.Current;$owners=@(Get-ProjectManagerOwnerChain $current.NativeWindowHandle)
        $modal=$null;$pattern=$null
        if($item.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$pattern)){
            $modal=[bool]$pattern.Current.IsModal
        }
        $record=@{handle=[long]$current.NativeWindowHandle;pid=[long]$current.ProcessId;
            name=(Limit-ProjectManagerText $current.Name 512);
            class_name=(Limit-ProjectManagerText $current.ClassName 128);
            automation_id=(Limit-ProjectManagerText $current.AutomationId 256);
            control_type=[string]$current.ControlType.ProgrammaticName;
            enabled=[bool]$current.IsEnabled;offscreen=[bool]$current.IsOffscreen;modal=$modal;
            owner_handles=@($owners);owner_is_pm=[bool]($Request.expected_process.window_handle -in $owners);
            tree=@();tree_truncated=$false}
        if(-not $current.IsOffscreen -and $current.NativeWindowHandle -ne $Request.expected_process.window_handle){
            if($treeCount -lt 4){
                $tree=Get-ProjectManagerTree $item $false 128
                $record.tree=@($tree.Nodes);$record.tree_truncated=[bool]$tree.Truncated;$treeCount++
            }else{$truncated=$true}
        }
        $records+=,$record
    }
    return @{ok=$true;operation='inspect_export_dialog';backend='windows_project_manager';
        state='export_dialog_inspection';eligible=$false;process=$Request.expected_process;
        project_state='unverified';native_connection_verified=$false;dispatch_started=$false;
        project_open_requested=$false;windows=@($records);windows_truncated=[bool]$truncated;
        pm_found=[bool]($null -ne $manager)}
}

function Begin-ProjectManagerCandidateExport($Request,[bool]$IncludeTree=$true) {
    $binding=Assert-ProjectManagerContext $Request.expected_process
    Assert-ProjectManagerLocalMode $binding.Manager $Request.expected_process.pid
    $before=@(Get-ProjectManagerWindowElements $binding.Manager $Request.expected_process.pid|
        ForEach-Object {$_.GetRuntimeId()-join ':'})
    $button=Find-ProjectManagerActionButton $binding.Manager 'Export' $Request.expected_process.pid
    $invoke=$null
    if(-not $button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)){
        Fail-ProjectManager 'EXPORT_INVOKE_UNAVAILABLE'
    }
    $tick=Get-ProjectManagerInputTick
    $script:ExportTouched=$true
    $dispatch=Start-ProjectManagerExportInvoke $invoke
    $script:ActiveExportDispatch=$dispatch
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while($clock.Elapsed.TotalSeconds -lt 12){
        $process=Get-Process -Id $Request.expected_process.pid -ErrorAction Stop
        $token=Get-ProjectManagerProcess $process
        if($token.creation_time -cne $Request.expected_process.creation_time){Fail-ProjectManager 'PROCESS_CHANGED'}
        $candidates=@()
        foreach($item in @(Get-ProjectManagerWindowElements $binding.Manager $token.pid)){
            $identity=$item.GetRuntimeId()-join ':'
            if($identity -in $before -or $item.Current.IsOffscreen -or -not $item.Current.IsEnabled -or
                $item.Current.ControlType -ne [System.Windows.Automation.ControlType]::Window){continue}
            $owners=@(Get-ProjectManagerOwnerChain $item.Current.NativeWindowHandle)
            if($Request.expected_process.window_handle -in $owners){
                $candidates+=,@{Element=$item;Owners=$owners}
            }
        }
        if($candidates.Count -gt 1){Fail-ProjectManager 'EXPORT_DIALOG_AMBIGUOUS'}
        if($candidates.Count -eq 1){
            $candidate=$candidates[0];$dialog=$candidate.Element;$current=$dialog.Current
            if((Get-ProjectManagerInputTick) -ne $tick){Fail-ProjectManager 'USER_INPUT_CHANGED'}
            $tree=if($IncludeTree){Get-ProjectManagerTree $dialog}else{@{Nodes=@();Truncated=$false}}
            return @{
                ok=$true;operation='begin_candidate_export';backend='windows_project_manager';
                state='candidate_export_dialog';eligible=$false;process=$Request.expected_process;
                project_state='unverified';native_connection_verified=$false;dispatch_started=$false;
                export_requested=$true;project_open_requested=$false;last_input_tick=$tick;
                invoke_completed=[bool]$dispatch.Completed;invoke_succeeded=[bool]$dispatch.Succeeded;
                dialog=@{ownership_token=$Request.ownership_token;pid=$token.pid;
                    creation_time=$token.creation_time;handle=[long]$current.NativeWindowHandle;
                    title=(Limit-ProjectManagerText $current.Name 512);
                    class_name=(Limit-ProjectManagerText $current.ClassName 128);
                    automation_id=(Limit-ProjectManagerText $current.AutomationId 256);
                    runtime_id=@($dialog.GetRuntimeId());owner_handles=@($candidate.Owners)};
                dialog_tree=@($tree.Nodes);dialog_tree_truncated=[bool]$tree.Truncated
            }
        }
        if($dispatch.Completed -and -not $dispatch.Succeeded){
            $script:OperationDiagnostics=@{invoke_completed=[bool]$dispatch.Completed;
                invoke_succeeded=[bool]$dispatch.Succeeded;invoke_error=(Limit-ProjectManagerText $dispatch.Error 256);
                pattern_type=(Limit-ProjectManagerText $invoke.GetType().FullName 128)}
            Fail-ProjectManager 'EXPORT_INVOKE_FAILED'
        }
        Start-Sleep -Milliseconds 100
    }
    $script:OperationDiagnostics=@{invoke_completed=[bool]$dispatch.Completed;
        invoke_succeeded=[bool]$dispatch.Succeeded;invoke_error=(Limit-ProjectManagerText $dispatch.Error 256);
        pattern_type=(Limit-ProjectManagerText $invoke.GetType().FullName 128)}
    Fail-ProjectManager 'EXPORT_DIALOG_NOT_OBSERVED'
}

function Get-ProjectManagerOwnedExport($Request) {
    if(-not [Environment]::UserInteractive -or -not [ProjectManagerNative]::InteractiveDesktop()){
        Fail-ProjectManager 'DESKTOP_UNAVAILABLE'
    }
    $expected=$Request.expected_process
    $token=Get-ProjectManagerProcess (Get-Process -Id $expected.pid -ErrorAction Stop)
    if($token.creation_time -cne $expected.creation_time -or
        -not [string]::Equals($token.executable_path,$expected.executable_path,[StringComparison]::OrdinalIgnoreCase)){
        Fail-ProjectManager 'PROCESS_CHANGED'
    }
    $roots=@(Get-ProjectManagerRoots $expected.pid)
    $manager=Find-ProjectManager $roots $expected.pid
    if($null -eq $manager -or $manager.Current.NativeWindowHandle -ne $expected.window_handle -or
        $manager.Current.IsOffscreen){Fail-ProjectManager 'WINDOW_UNAVAILABLE'}
    $dialogs=@()
    foreach($item in @(Get-ProjectManagerWindowElements $manager $expected.pid)){
        $current=$item.Current
        if($current.IsOffscreen -or $current.NativeWindowHandle -eq $expected.window_handle){continue}
        if($current.NativeWindowHandle -ne $Request.dialog.handle -or
            $current.Name -cne 'Export Project File' -or $current.ClassName -cne '#32770' -or
            $current.AutomationId -cne '' -or -not $current.IsEnabled -or
            $current.ControlType -ne [System.Windows.Automation.ControlType]::Window -or
            ($item.GetRuntimeId()-join ':') -cne ($Request.dialog.runtime_id-join ':')){
            Fail-ProjectManager 'EXPORT_DIALOG_BLOCKED_OR_CHANGED'
        }
        $owners=@(Get-ProjectManagerOwnerChain $current.NativeWindowHandle)
        if(($owners-join ':') -cne ($Request.dialog.owner_handles-join ':') -or
            $expected.window_handle -notin $owners){Fail-ProjectManager 'EXPORT_DIALOG_OWNER_CHANGED'}
        $dialogs+=,$item
    }
    if($dialogs.Count -ne 1){Fail-ProjectManager 'EXPORT_DIALOG_NOT_CURRENT'}
    return @{Manager=$manager;Dialog=$dialogs[0]}
}

function Find-ProjectManagerExportControl($Dialog,[string]$Kind,[long]$ProcessId) {
    $identifier=switch -CaseSensitive ($Kind){
        'filename' {'FileNameControlHost'} 'filetype' {'FileTypeControlHost'}
        'save' {'1'} 'cancel' {'2'} default {Fail-ProjectManager 'INVALID_INTERNAL_ACTION'}
    }
    $button=$Kind -cin @('save','cancel')
    $scope=if($button){[System.Windows.Automation.TreeScope]::Children}
        else{[System.Windows.Automation.TreeScope]::Descendants}
    $items=@($Dialog.FindAll($scope,[System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$identifier))|
        Where-Object {$_.Current.ProcessId -eq $ProcessId -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen -and
            $_.Current.ClassName -ceq $(if($button){'Button'}else{'AppControlHost'}) -and
            $_.Current.ControlType -eq $(if($button){[System.Windows.Automation.ControlType]::Button}
                else{[System.Windows.Automation.ControlType]::ComboBox})})
    if($items.Count -ne 1){Fail-ProjectManager 'EXPORT_CONTROL_UNAVAILABLE_OR_AMBIGUOUS'}
    if($Kind -ceq 'save' -and $items[0].Current.Name -cne 'Save'){Fail-ProjectManager 'EXPORT_CONTROL_PROFILE_CHANGED'}
    if($Kind -ceq 'cancel'){
        $caption=-join [char[]]@(0x421,0x43a,0x430,0x441,0x443,0x432,0x430,0x442,0x438)
        if($items[0].Current.Name -cne $caption){Fail-ProjectManager 'EXPORT_CONTROL_PROFILE_CHANGED'}
    }
    return $items[0]
}

function Read-ProjectManagerExportFields($Dialog,[long]$ProcessId) {
    $filenameHost=Find-ProjectManagerExportControl $Dialog 'filename' $ProcessId
    $edits=@($filenameHost.FindAll([System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,'1001'))|
        Where-Object {$_.Current.ProcessId -eq $ProcessId -and $_.Current.ClassName -ceq 'Edit' -and
            $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and
            $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
    if($edits.Count -ne 1){Fail-ProjectManager 'EXPORT_FILENAME_EDIT_UNAVAILABLE'}
    $value=$null;$hostValue=$null
    if(-not $edits[0].TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value) -or
        $value.Current.IsReadOnly -or
        -not $filenameHost.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$hostValue)){
        Fail-ProjectManager 'EXPORT_FILENAME_VALUE_UNAVAILABLE'
    }
    $type=Find-ProjectManagerExportControl $Dialog 'filetype' $ProcessId
    $selection=$null
    if(-not $type.TryGetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern,[ref]$selection)){
        Fail-ProjectManager 'EXPORT_FILETYPE_SELECTION_UNAVAILABLE'
    }
    $selected=@($selection.Current.GetSelection())
    if($selected.Count -ne 1 -or $selected[0].Current.ProcessId -ne $ProcessId -or
        $selected[0].Current.ControlType -ne [System.Windows.Automation.ControlType]::ListItem -or
        $selected[0].Current.Name -cne 'Export files (*.drp)'){Fail-ProjectManager 'EXPORT_DRP_TYPE_REQUIRED'}
    if($value.Current.Value -cne $hostValue.Current.Value){Fail-ProjectManager 'EXPORT_FILENAME_READBACK_MISMATCH'}
    return @{Value=$value;Text=[string]$value.Current.Value}
}

function Assert-ProjectManagerFreshExportDestination([string]$Path) {
    Assert-ProjectManagerExportDestinationSyntax $Path
    if([IO.File]::Exists($Path) -or [IO.Directory]::Exists($Path)){
        Fail-ProjectManager 'EXPORT_OVERWRITE_REFUSED'
    }
    $cursor=[IO.DirectoryInfo]::new([IO.Path]::GetDirectoryName($Path))
    if(-not $cursor.Exists){Fail-ProjectManager 'EXPORT_PARENT_UNAVAILABLE'}
    for($index=0;$null -ne $cursor -and $index -lt 128;$index++){
        if(($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){Fail-ProjectManager 'LINKED_EXPORT_DESTINATION'}
        $cursor=$cursor.Parent
    }
    if($null -ne $cursor){Fail-ProjectManager 'EXPORT_PATH_LIMIT'}
}

function Test-ProjectManagerExportFileClosed([string]$Path) {
    if(-not [IO.File]::Exists($Path)){return $false}
    $stream=$null
    try{
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        return $stream.Length -gt 0 -and $stream.Length -le 536870912
    }catch{return $false}finally{if($null -ne $stream){$stream.Dispose()}}
}

function Complete-ProjectManagerCandidateExport($Request) {
    $binding=Get-ProjectManagerOwnedExport $Request
    $saving=$Request.operation -ceq 'save_candidate_export'
    if($saving){
        if((Get-ProjectManagerInputTick) -ne $Request.last_input_tick){Fail-ProjectManager 'USER_INPUT_CHANGED'}
        Assert-ProjectManagerFreshExportDestination $Request.destination
        $fields=Read-ProjectManagerExportFields $binding.Dialog $Request.expected_process.pid
        $script:ExportTouched=$true
        $fields.Value.SetValue($Request.destination)
        $binding=Get-ProjectManagerOwnedExport $Request
        $fields=Read-ProjectManagerExportFields $binding.Dialog $Request.expected_process.pid
        if(-not [string]::Equals($fields.Text,$Request.destination,[StringComparison]::OrdinalIgnoreCase)){
            Fail-ProjectManager 'EXPORT_FILENAME_READBACK_MISMATCH'
        }
        Assert-ProjectManagerFreshExportDestination $Request.destination
        if((Get-ProjectManagerInputTick) -ne $Request.last_input_tick){Fail-ProjectManager 'USER_INPUT_CHANGED'}
    }
    $button=Find-ProjectManagerExportControl $binding.Dialog $(if($saving){'save'}else{'cancel'}) $Request.expected_process.pid
    $invoke=$null
    if(-not $button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)){
        Fail-ProjectManager 'EXPORT_COMPLETION_INVOKE_UNAVAILABLE'
    }
    $null=Get-ProjectManagerOwnedExport $Request
    if($saving){
        Assert-ProjectManagerFreshExportDestination $Request.destination
        if((Get-ProjectManagerInputTick) -ne $Request.last_input_tick){Fail-ProjectManager 'USER_INPUT_CHANGED'}
    }
    $script:ExportTouched=$true
    $dispatch=Start-ProjectManagerExportInvoke $invoke
    $script:ActiveSaveDispatch=$dispatch
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while($clock.Elapsed.TotalSeconds -lt 12){
        $token=Get-ProjectManagerProcess (Get-Process -Id $Request.expected_process.pid -ErrorAction Stop)
        if($token.creation_time -cne $Request.expected_process.creation_time){Fail-ProjectManager 'PROCESS_CHANGED'}
        $windows=@(Get-ProjectManagerWindowElements $binding.Manager $Request.expected_process.pid)
        $present=@($windows|Where-Object {$_.Current.NativeWindowHandle -eq $Request.dialog.handle -and -not $_.Current.IsOffscreen})
        if($present.Count -eq 0){
            $null=Assert-ProjectManagerContext $Request.expected_process
            if(-not $saving){
                return @{ok=$true;operation=$Request.operation;backend='windows_project_manager';
                    state='candidate_export_cancelled';eligible=$true;process=$Request.expected_process;
                    project_state='unverified';native_connection_verified=$false;dispatch_started=$false;
                    project_open_requested=$false;export_requested=$true;candidate_export_cancelled=$true;
                    ownership_token=$Request.dialog.ownership_token}
            }
            if((Get-ProjectManagerInputTick) -ne $Request.last_input_tick){Fail-ProjectManager 'USER_INPUT_CHANGED'}
            if($dispatch.Completed -and $dispatch.Succeeded -and (Test-ProjectManagerExportFileClosed $Request.destination)){
                return @{ok=$true;operation=$Request.operation;backend='windows_project_manager';
                    state='candidate_export_saved';eligible=$true;process=$Request.expected_process;
                    project_state='unverified';native_connection_verified=$false;dispatch_started=$false;
                    project_open_requested=$false;export_requested=$true;candidate_export_saved=$true;
                    ownership_token=$Request.dialog.ownership_token;destination=$Request.destination;
                    last_input_tick=$Request.last_input_tick;file_type='Export files (*.drp)'}
            }
        }
        if($dispatch.Completed -and -not $dispatch.Succeeded){Fail-ProjectManager 'EXPORT_COMPLETION_INVOKE_FAILED'}
        Start-Sleep -Milliseconds 100
    }
    Fail-ProjectManager 'EXPORT_COMPLETION_UNCERTAIN'
}

function Export-ProjectManagerCandidate($Request) {
    Assert-ProjectManagerFreshExportDestination $Request.destination
    $begun=Begin-ProjectManagerCandidateExport ([pscustomobject]@{
        expected_process=$Request.expected_process;ownership_token=$Request.ownership_token}) $false
    $script:OperationDiagnostics=@{owned_export_dialog=$begun.dialog;destination=$Request.destination;
        begin_invoke_completed=[bool]$script:ActiveExportDispatch.Completed;
        begin_invoke_succeeded=[bool]$script:ActiveExportDispatch.Succeeded}
    $saved=Complete-ProjectManagerCandidateExport ([pscustomobject]@{
        operation='save_candidate_export';expected_process=$Request.expected_process;
        dialog=$begun.dialog;last_input_tick=$begun.last_input_tick;destination=$Request.destination})
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while(-not $script:ActiveExportDispatch.Completed -and $clock.Elapsed.TotalSeconds -lt 12){
        Start-Sleep -Milliseconds 20
    }
    if(-not $script:ActiveExportDispatch.Completed -or -not $script:ActiveExportDispatch.Succeeded){
        Fail-ProjectManager 'EXPORT_INVOKE_COMPLETION_UNCERTAIN'
    }
    $saved.operation='export_candidate'
    return $saved
}

function Get-ProjectManagerOrphanedProbe($Request) {
    if(-not [Environment]::UserInteractive -or -not [ProjectManagerNative]::InteractiveDesktop()){
        Fail-ProjectManager 'DESKTOP_UNAVAILABLE'
    }
    $expected=$Request.expected_process
    $token=Get-ProjectManagerProcess (Get-Process -Id $expected.pid -ErrorAction Stop)
    if($token.creation_time -cne $expected.creation_time -or
        -not [string]::Equals($token.executable_path,$expected.executable_path,[StringComparison]::OrdinalIgnoreCase)){
        Fail-ProjectManager 'PROCESS_CHANGED'
    }
    $roots=@(Get-ProjectManagerRoots $expected.pid)
    $manager=Find-ProjectManager $roots $expected.pid
    if($null -eq $manager -or $manager.Current.NativeWindowHandle -ne $expected.window_handle -or
        -not $manager.Current.IsEnabled -or $manager.Current.IsOffscreen){Fail-ProjectManager 'WINDOW_UNAVAILABLE'}
    $candidates=@()
    foreach($item in @(Get-ProjectManagerWindowElements $manager $expected.pid)){
        $current=$item.Current
        if($current.IsOffscreen -or $current.NativeWindowHandle -eq $expected.window_handle){continue}
        if($current.ClassName -cne '#32770' -or $current.Name -cne 'Export Project File' -or
            $current.AutomationId -cne '' -or -not $current.IsEnabled -or
            $current.ControlType -ne [System.Windows.Automation.ControlType]::Window -or
            $current.NativeWindowHandle -eq $Request.previous_dialog.handle){Fail-ProjectManager 'ORPHAN_PROBE_AMBIGUOUS_OR_BLOCKED'}
        $owners=@(Get-ProjectManagerOwnerChain $current.NativeWindowHandle)
        $window=$null
        if($owners.Count -ne 0 -or
            -not $item.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$window) -or
            $window.Current.IsModal){Fail-ProjectManager 'ORPHAN_PROBE_PROFILE_CHANGED'}
        $fields=Read-ProjectManagerExportFields $item $expected.pid
        if($fields.Text -cne $Request.expected_default_filename){Fail-ProjectManager 'ORPHAN_PROBE_FILENAME_MISMATCH'}
        $candidates+=,$item
    }
    if($candidates.Count -ne 1){Fail-ProjectManager 'ORPHAN_PROBE_NOT_UNIQUE'}
    return @{Manager=$manager;Dialog=$candidates[0]}
}

function Cancel-ProjectManagerOrphanedProbe($Request) {
    $binding=Get-ProjectManagerOrphanedProbe $Request
    $handle=[long]$binding.Dialog.Current.NativeWindowHandle
    $runtime=@($binding.Dialog.GetRuntimeId())
    $button=Find-ProjectManagerExportControl $binding.Dialog 'cancel' $Request.expected_process.pid
    $invoke=$null
    if(-not $button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)){
        Fail-ProjectManager 'ORPHAN_CANCEL_INVOKE_UNAVAILABLE'
    }
    $again=Get-ProjectManagerOrphanedProbe $Request
    if($again.Dialog.Current.NativeWindowHandle -ne $handle -or
        ($again.Dialog.GetRuntimeId()-join ':') -cne ($runtime-join ':')){Fail-ProjectManager 'ORPHAN_PROBE_CHANGED'}
    $script:ExportTouched=$true
    $dispatch=Start-ProjectManagerExportInvoke $invoke
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while($clock.Elapsed.TotalSeconds -lt 12){
        $token=Get-ProjectManagerProcess (Get-Process -Id $Request.expected_process.pid -ErrorAction Stop)
        if($token.creation_time -cne $Request.expected_process.creation_time){Fail-ProjectManager 'PROCESS_CHANGED'}
        $present=@(Get-ProjectManagerWindowElements $binding.Manager $Request.expected_process.pid|
            Where-Object {$_.Current.NativeWindowHandle -eq $handle -and -not $_.Current.IsOffscreen})
        if($present.Count -eq 0 -and $dispatch.Completed -and $dispatch.Succeeded){
            $null=Assert-ProjectManagerContext $Request.expected_process
            return @{ok=$true;operation='cancel_orphaned_probe_export';backend='windows_project_manager';
                state='orphaned_probe_export_cancelled';eligible=$true;process=$Request.expected_process;
                project_state='unverified';native_connection_verified=$false;dispatch_started=$false;
                project_open_requested=$false;orphan_cancelled=$true;
                orphan_witness=@{verification='explicit_unowned_probe_recovery';
                    previous_dialog_handle=$Request.previous_dialog.handle;
                    previous_ownership_token=$Request.previous_dialog.ownership_token;
                    recovered_dialog_handle=$handle;recovered_runtime_id=@($runtime);owner_handles=@();
                    default_filename=$Request.expected_default_filename}}
        }
        if($dispatch.Completed -and -not $dispatch.Succeeded){Fail-ProjectManager 'ORPHAN_CANCEL_UNCERTAIN'}
        Start-Sleep -Milliseconds 100
    }
    Fail-ProjectManager 'ORPHAN_CANCEL_UNCERTAIN'
}

function Assert-ProjectManagerContext($Expected) {
    # Keep each action guard independent of the costly diagnostic tree walk.
    # Full Control/Raw evidence is collected once after all fixed view actions.
    if(-not [Environment]::UserInteractive -or -not [ProjectManagerNative]::InteractiveDesktop()){
        Fail-ProjectManager 'DESKTOP_UNAVAILABLE'
    }
    $process=Get-Process -Id $Expected.pid -ErrorAction Stop
    $token=Get-ProjectManagerProcess $process
    if($token.pid -ne $Expected.pid -or $token.creation_time -cne $Expected.creation_time -or
        -not [string]::Equals($token.executable_path,$Expected.executable_path,
            [StringComparison]::OrdinalIgnoreCase)){Fail-ProjectManager 'PROCESS_OR_WINDOW_CHANGED'}
    $roots=@(Get-ProjectManagerRoots $Expected.pid)
    $manager=Find-ProjectManager $roots $Expected.pid
    if($null -eq $manager -or $manager.Current.IsOffscreen -or
        $manager.Current.NativeWindowHandle -ne $Expected.window_handle -or
        $manager.Current.Name -cne $Expected.window_title){Fail-ProjectManager 'WINDOW_UNAVAILABLE'}
    $pattern=$null
    if($manager.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$pattern) -and
        $pattern.Current.WindowVisualState -eq [System.Windows.Automation.WindowVisualState]::Minimized){
        Fail-ProjectManager 'WINDOW_MINIMIZED'
    }
    $blocker=Get-ProjectManagerBlocker $roots $manager $Expected.pid
    if($null -ne $blocker){Fail-ProjectManager 'PROJECT_MANAGER_NOT_READY'}
    return @{Manager=$manager;Process=$token}
}

function Find-ProjectManagerViewControl($Manager,[string]$Kind,[long]$ProcessId) {
    $prefix='UiProjectManagerDialog.frameProjectManagerBorder.m_ProjectManagerView.projManagerContents.splitterProjects.m_ViewProjects.frameTopContainer.frameToolbar'
    $identifier=switch -CaseSensitive ($Kind) {
        'library_sidebar' {$prefix+'.m_BtnSidePanel'}
        'list_view' {$prefix+'.frameTopToolbar.frame_6.btnListView'}
        'thumbnail_view' {$prefix+'.frameTopToolbar.frame_6.btnThumbView'}
        'project_search' {$prefix+'.frameTopToolbar.btnSearch'}
        default {Fail-ProjectManager 'INVALID_INTERNAL_VIEW_CONTROL'}
    }
    $items=@($Manager.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$identifier))|
        Where-Object {$_.Current.ProcessId -eq $ProcessId -and
            $_.Current.ClassName -ceq 'QPushButton' -and
            $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::CheckBox -and
            $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
    if($items.Count -ne 1){Fail-ProjectManager 'VIEW_CONTROL_UNAVAILABLE_OR_AMBIGUOUS'}
    return $items[0]
}

function Read-ProjectManagerViewToggle($Control) {
    $pattern=$null
    if(-not $Control.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$pattern)){
        Fail-ProjectManager 'VIEW_TOGGLE_PATTERN_UNAVAILABLE'
    }
    $state=$pattern.Current.ToggleState.ToString()
    if($state -cnotin @('On','Off')){Fail-ProjectManager 'VIEW_TOGGLE_STATE_UNAVAILABLE'}
    return @{Pattern=$pattern;State=$state}
}

function Press-ProjectManagerViewStep($Request,[string]$PressKind,[string]$ReadKind,
    [string]$ExpectedBefore,[string]$ExpectedAfter) {
    $binding=Assert-ProjectManagerContext $Request.expected_process
    $control=Find-ProjectManagerViewControl $binding.Manager $PressKind $Request.expected_process.pid
    $pressedBefore=Read-ProjectManagerViewToggle $control
    $readBefore=Read-ProjectManagerViewToggle (
        Find-ProjectManagerViewControl $binding.Manager $ReadKind $Request.expected_process.pid)
    if($readBefore.State -cne $ExpectedBefore -or
        ($PressKind -cne $ReadKind -and $pressedBefore.State -cne 'Off')){
        Fail-ProjectManager 'VIEW_MODE_CHANGED_OR_CONFLICTING'
    }
    $invoke=$null
    if(-not $control.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)){
        Fail-ProjectManager 'VIEW_INVOKE_PATTERN_UNAVAILABLE'
    }
    $script:ViewTouched=$true
    $invoke.Invoke()
    Start-Sleep -Milliseconds 200
    $binding=Assert-ProjectManagerContext $Request.expected_process
    $readAfter=Read-ProjectManagerViewToggle (
        Find-ProjectManagerViewControl $binding.Manager $ReadKind $Request.expected_process.pid)
    if($readAfter.State -cne $ExpectedAfter){Fail-ProjectManager 'VIEW_TOGGLE_READBACK_FAILED'}
    if($PressKind -cne $ReadKind){
        $pressedAfter=Read-ProjectManagerViewToggle (
            Find-ProjectManagerViewControl $binding.Manager $PressKind $Request.expected_process.pid)
        if($pressedAfter.State -cne 'On'){Fail-ProjectManager 'VIEW_TOGGLE_READBACK_FAILED'}
    }
}

function Prepare-ProjectManagerContextView($Request) {
    $changes=@()
    foreach($kind in @('library_sidebar','list_view')) {
        $binding=Assert-ProjectManagerContext $Request.expected_process
        $control=Find-ProjectManagerViewControl $binding.Manager $kind $Request.expected_process.pid
        $before=Read-ProjectManagerViewToggle $control
        $presses=@()
        if($before.State -ceq 'On' -and $Request.refresh_view) {
            # Qt's state-only Toggle action need not emit its clicked handler.
            # Use only the observed fixed button's Invoke press, and verify each
            # state transition. The list button may be exclusive: thumbnail is
            # the fixed alternative that moves list view to Off before On.
            $offControl=if($kind -ceq 'list_view'){'thumbnail_view'}else{$kind}
            Press-ProjectManagerViewStep $Request $offControl $kind 'On' 'Off'
            $presses+=,$offControl
            Press-ProjectManagerViewStep $Request $kind $kind 'Off' 'On'
            $presses+=,$kind
        } elseif($before.State -ceq 'Off') {
            Press-ProjectManagerViewStep $Request $kind $kind 'Off' 'On'
            $presses+=,$kind
        }
        $binding=Assert-ProjectManagerContext $Request.expected_process
        $after=Read-ProjectManagerViewToggle (
            Find-ProjectManagerViewControl $binding.Manager $kind $Request.expected_process.pid)
        if($after.State -cne 'On'){Fail-ProjectManager 'VIEW_TOGGLE_READBACK_FAILED'}
        $changes+=,@{control=$kind;before=$before.State;after=$after.State;
            changed=[bool]($presses.Count -gt 0);press_sequence=@($presses);
            verification='windows_uia_invoke_toggle_readback'}
    }
    $null=Assert-ProjectManagerContext $Request.expected_process
    $result=Get-ProjectManagerInspection
    if($result.state -cne 'project_manager' -or -not $result.eligible -or
        $null -eq $result.process){Fail-ProjectManager 'PROJECT_MANAGER_NOT_READY'}
    foreach($key in @('pid','creation_time','window_handle','window_title')){
        if($result.process[$key] -cne $Request.expected_process.$key){
            Fail-ProjectManager 'PROCESS_OR_WINDOW_CHANGED'
        }
    }
    $result.operation='prepare_context_view'
    $result.view_changes=@($changes)
    $result.view_changed=[bool](@($changes|Where-Object {$_.changed}).Count -gt 0)
    $result.project_open_requested=$false
    $result.refresh_view=[bool]$Request.refresh_view
    return $result
}

function Begin-ProjectManagerCandidateSearch($Request) {
    $binding=Assert-ProjectManagerContext $Request.expected_process
    Assert-ProjectManagerLocalMode $binding.Manager $Request.expected_process.pid
    $before=Read-ProjectManagerViewToggle (
        Find-ProjectManagerViewControl $binding.Manager 'project_search' $Request.expected_process.pid)
    if($before.State -ceq 'Off'){
        Press-ProjectManagerViewStep $Request 'project_search' 'project_search' 'Off' 'On'
    }
    $null=Assert-ProjectManagerContext $Request.expected_process
    $result=Get-ProjectManagerInspection
    if($result.state -cne 'project_manager' -or -not $result.eligible){Fail-ProjectManager 'PROJECT_MANAGER_NOT_READY'}
    $result.operation='begin_candidate_search';$result.project_open_requested=$false;
    $result.search_toggle_before=$before.State;$result.search_toggle_after='On';
    $result.view_changed=$before.State -ceq 'Off';$result.search_requested=$true;
    return $result
}

function Read-ProjectManagerSearchState($Manager,[long]$ProcessId) {
    $toggle=Read-ProjectManagerViewToggle (
        Find-ProjectManagerViewControl $Manager 'project_search' $ProcessId)
    if($toggle.State -cne 'On'){Fail-ProjectManager 'PROJECT_SEARCH_VIEW_REQUIRED'}
    $prefix='UiProjectManagerDialog.frameProjectManagerBorder.m_ProjectManagerView.projManagerContents.splitterProjects.m_ViewProjects.frameSearchWidget.widgetSearch.'
    $fields=@($Manager.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$prefix+'editSearchKeywordFilter'))|
        Where-Object {$_.Current.ProcessId -eq $ProcessId -and $_.Current.ClassName -ceq 'UiLineEdit' -and
            $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and
            $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
    $criteria=@($Manager.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$prefix+'frameSearchCriterion.comboSearchCriteria'))|
        Where-Object {$_.Current.ProcessId -eq $ProcessId -and
            $_.Current.ClassName -ceq 'BmdDavUI::UiElidedComboBox' -and
            $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ComboBox -and
            $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
    if($fields.Count -ne 1 -or $criteria.Count -ne 1){Fail-ProjectManager 'SEARCH_CONTROL_UNAVAILABLE_OR_AMBIGUOUS'}
    $criterion=$null;$value=$null
    if(-not $criteria[0].TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$criterion) -or
        $criterion.Current.Value -cne 'Name'){Fail-ProjectManager 'EXACT_NAME_SEARCH_REQUIRED'}
    if(-not $fields[0].TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value) -or
        $value.Current.IsReadOnly){Fail-ProjectManager 'SEARCH_VALUE_PATTERN_UNAVAILABLE'}
    if(([string]$value.Current.Value).Length -gt 512){Fail-ProjectManager 'SEARCH_VALUE_BOUND_EXCEEDED'}
    return @{Pattern=$value;Value=[string]$value.Current.Value;Criteria='Name';Field=$fields[0]}
}

function Filter-ProjectManagerCandidate($Request) {
    $binding=Assert-ProjectManagerContext $Request.expected_process
    Assert-ProjectManagerLocalMode $binding.Manager $Request.expected_process.pid
    $before=Read-ProjectManagerSearchState $binding.Manager $Request.expected_process.pid
    $changed=$before.Value -cne $Request.project_name
    if($changed){
        $script:ViewTouched=$true
        $before.Pattern.SetValue($Request.project_name)
        Start-Sleep -Milliseconds 300
    }
    $binding=Assert-ProjectManagerContext $Request.expected_process
    Assert-ProjectManagerLocalMode $binding.Manager $Request.expected_process.pid
    $after=Read-ProjectManagerSearchState $binding.Manager $Request.expected_process.pid
    if($after.Value -cne $Request.project_name -or $after.Criteria -cne 'Name'){
        Fail-ProjectManager 'SEARCH_VALUE_READBACK_FAILED'
    }
    $result=Get-ProjectManagerInspection
    if($result.state -cne 'project_manager' -or -not $result.eligible){Fail-ProjectManager 'PROJECT_MANAGER_NOT_READY'}
    $result.operation='filter_candidate';$result.project_open_requested=$false;
    $result.project_name=$Request.project_name;$result.search_value=$after.Value;
    $result.search_criteria='Name';$result.search_before=$before.Value;
    $result.view_changed=[bool]$changed;$result.ui_selection_verified=$false;
    return $result
}

function Find-ProjectManagerProjectBin($Manager,[long]$ProcessId) {
    $identifier='UiProjectManagerDialog.frameProjectManagerBorder.m_ProjectManagerView.projManagerContents.splitterProjects.m_ViewProjects.frame_8.m_ProjectsWidget'
    $items=@($Manager.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$identifier))|
        Where-Object {$_.Current.ProcessId -eq $ProcessId -and
            $_.Current.ClassName -ceq 'ProjectManager::UiProjectManagerBinView' -and
            $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Group -and
            $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
    if($items.Count -ne 1){Fail-ProjectManager 'PROJECT_BIN_UNAVAILABLE_OR_AMBIGUOUS'}
    return $items[0]
}

function Activate-ProjectManagerWindow($Expected,[string]$Stage='activate_pm') {
    $null=Assert-ProjectManagerContext $Expected
    try{[ProjectManagerNative]::ActivateProjectManager(
        [IntPtr]::new($Expected.window_handle),[uint32]$Expected.pid)}
    catch{
        $script:OperationDiagnostics=@{
            stage=$Stage;foreground_handle=[long][ProjectManagerNative]::ForegroundHandle();
            activation_error=(Limit-ProjectManagerText $_.Exception.Message 256);
            foreground_pid=[long][ProjectManagerNative]::ForegroundProcess();
            foreground_root_owner=[long][ProjectManagerNative]::ForegroundRootOwner();
            expected_pm_handle=[long]$Expected.window_handle}
        Fail-ProjectManager 'PROJECT_MANAGER_ACTIVATION_DENIED'
    }
    $null=Assert-ProjectManagerContext $Expected
}

function Assert-ProjectManagerBinFocus($Bin,[long]$ProcessId,[long]$WindowHandle,[string]$Stage='verify_bin_focus') {
    $foreground=[long][ProjectManagerNative]::ForegroundProcess()
    $rootOwner=[long][ProjectManagerNative]::ForegroundRootOwner()
    if($foreground -ne $ProcessId -or $rootOwner -ne $WindowHandle){
        $script:OperationDiagnostics=@{stage=$Stage;foreground_pid=$foreground;expected_pid=$ProcessId;
            foreground_handle=[long][ProjectManagerNative]::ForegroundHandle();
            foreground_root_owner=$rootOwner;expected_pm_handle=$WindowHandle}
        Fail-ProjectManager 'PROJECT_MANAGER_NOT_FOREGROUND'
    }
    $focused=[System.Windows.Automation.AutomationElement]::FocusedElement
    $path=@()
    for($depth=0;$depth -lt 16 -and $null -ne $focused;$depth++){
        if($focused.Current.ProcessId -ne $ProcessId){break}
        $path+=,@{class_name=(Limit-ProjectManagerText $focused.Current.ClassName 128);
            automation_id=(Limit-ProjectManagerText $focused.Current.AutomationId 256);
            name=(Limit-ProjectManagerText $focused.Current.Name 512)}
        if([System.Windows.Automation.Automation]::Compare($focused,$Bin)){return}
        $focused=[System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($focused)
    }
    $script:OperationDiagnostics=@{stage=$Stage;foreground_pid=$foreground;focus_path=@($path);
        foreground_handle=[long][ProjectManagerNative]::ForegroundHandle();foreground_root_owner=$rootOwner;
        expected_pm_handle=$WindowHandle}
    Fail-ProjectManager 'PROJECT_BIN_FOCUS_UNVERIFIED'
}

function Post-ProjectManagerHome($Expected) {
    [ProjectManagerNative]::PostHome([IntPtr]::new($Expected.window_handle),[uint32]$Expected.pid)
}

function Post-ProjectManagerDown($Expected) {
    [ProjectManagerNative]::PostDown([IntPtr]::new($Expected.window_handle),[uint32]$Expected.pid)
}

function Select-ProjectManagerFilteredCandidate($Request) {
    $binding=Assert-ProjectManagerContext $Request.expected_process
    Assert-ProjectManagerLocalMode $binding.Manager $Request.expected_process.pid
    $search=Read-ProjectManagerSearchState $binding.Manager $Request.expected_process.pid
    if($search.Value -cne $Request.project_name){Fail-ProjectManager 'EXACT_NAME_FILTER_REQUIRED'}
    $bin=Find-ProjectManagerProjectBin $binding.Manager $Request.expected_process.pid
    $tick=Get-ProjectManagerInputTick
    $inputProof=@{stage='before_activation';tick_before=$tick;tick_after_activation=$null;
        tick_before_bin_focus=$null;tick_after_bin_focus=$null;tick_after_focus=$null;
        tick_after_home=$null;manager_focus_requested=$false;bin_focus_requested=$false;
        home_posted=$false;down_posted=$false;tick_after_down=$null}
    $script:ViewTouched=$true
    Activate-ProjectManagerWindow $Request.expected_process 'before_bin_focus'
    $inputProof.stage='after_activation';$inputProof.tick_after_activation=Get-ProjectManagerInputTick
    if($inputProof.tick_after_activation -ne $tick){
        $script:OperationDiagnostics=$inputProof;Fail-ProjectManager 'USER_INPUT_CHANGED'
    }
    $binding=Assert-ProjectManagerContext $Request.expected_process
    $bin=Find-ProjectManagerProjectBin $binding.Manager $Request.expected_process.pid
    $inputProof.stage='before_bin_focus';$inputProof.tick_before_bin_focus=Get-ProjectManagerInputTick
    if($inputProof.tick_before_bin_focus -ne $tick){
        $script:OperationDiagnostics=$inputProof;Fail-ProjectManager 'USER_INPUT_CHANGED'
    }
    # Native activation already proved canonical PM foreground. Focusing the
    # window again is unnecessary; focus only the exact observed project bin.
    try{$inputProof.bin_focus_requested=$true;$bin.SetFocus()}
    catch{
        $script:OperationDiagnostics=@{stage='bin_set_focus';focus_error=(Limit-ProjectManagerText $_.Exception.Message 256)}
        Fail-ProjectManager 'PROJECT_BIN_FOCUS_FAILED'
    }
    $inputProof.stage='after_bin_focus';$inputProof.tick_after_bin_focus=Get-ProjectManagerInputTick
    if($inputProof.tick_after_bin_focus -ne $tick){
        $script:OperationDiagnostics=$inputProof;Fail-ProjectManager 'USER_INPUT_CHANGED'
    }
    # Some providers move activation during SetFocus. Reactivate only the same
    # verified PM, then require the bin focus and unchanged input again.
    Activate-ProjectManagerWindow $Request.expected_process 'after_bin_focus'
    $inputProof.stage='after_bin_reactivation'
    $inputProof.tick_after_bin_reactivation=Get-ProjectManagerInputTick
    if($inputProof.tick_after_bin_reactivation -ne $tick){
        $script:OperationDiagnostics=$inputProof;Fail-ProjectManager 'USER_INPUT_CHANGED'
    }
    Assert-ProjectManagerBinFocus $bin $Request.expected_process.pid $Request.expected_process.window_handle 'after_bin_reactivation'
    $binding=Assert-ProjectManagerContext $Request.expected_process
    $inputProof.stage='after_focus';$inputProof.tick_after_focus=Get-ProjectManagerInputTick
    if($inputProof.tick_after_focus -ne $tick){
        $script:OperationDiagnostics=$inputProof;Fail-ProjectManager 'USER_INPUT_CHANGED'
    }
    Post-ProjectManagerHome $Request.expected_process
    $inputProof.home_posted=$true
    Start-Sleep -Milliseconds 200
    $binding=Assert-ProjectManagerContext $Request.expected_process
    Assert-ProjectManagerLocalMode $binding.Manager $Request.expected_process.pid
    $bin=Find-ProjectManagerProjectBin $binding.Manager $Request.expected_process.pid
    Assert-ProjectManagerBinFocus $bin $Request.expected_process.pid $Request.expected_process.window_handle 'after_home'
    $search=Read-ProjectManagerSearchState $binding.Manager $Request.expected_process.pid
    $inputProof.stage='after_home';$inputProof.tick_after_home=Get-ProjectManagerInputTick
    if($search.Value -cne $Request.project_name -or $inputProof.tick_after_home -ne $tick){
        $script:OperationDiagnostics=$inputProof
        Fail-ProjectManager 'SELECTION_SCOPE_CHANGED'
    }
    if($Request.advance_once){
        Post-ProjectManagerDown $Request.expected_process
        $inputProof.down_posted=$true
        Start-Sleep -Milliseconds 200
        $binding=Assert-ProjectManagerContext $Request.expected_process
        Assert-ProjectManagerLocalMode $binding.Manager $Request.expected_process.pid
        $bin=Find-ProjectManagerProjectBin $binding.Manager $Request.expected_process.pid
        Assert-ProjectManagerBinFocus $bin $Request.expected_process.pid $Request.expected_process.window_handle 'after_down'
        $search=Read-ProjectManagerSearchState $binding.Manager $Request.expected_process.pid
        $inputProof.stage='after_down';$inputProof.tick_after_down=Get-ProjectManagerInputTick
        if($search.Value -cne $Request.project_name -or $inputProof.tick_after_down -ne $tick){
            $script:OperationDiagnostics=$inputProof;Fail-ProjectManager 'SELECTION_SCOPE_CHANGED'
        }
    }
    return @{ok=$true;operation='select_filtered_candidate';backend='windows_project_manager';
        state='selection_requested';eligible=$true;process=$Request.expected_process;
        project_state='unverified';native_connection_verified=$false;dispatch_started=$false;
        project_open_requested=$false;ui_selection_verified=$false;selection_requested=$true;
        project_name=$Request.project_name;search_value=$search.Value;search_criteria='Name';
        last_input_tick=$tick;selection_method=if($Request.advance_once){'fixed_pm_home_then_down_messages'}else{'fixed_pm_home_message'};
        home_messages_posted=2;advance_once=[bool]$Request.advance_once;
        down_messages_posted=if($Request.advance_once){2}else{0}}
}

try {
    $buffer=[char[]]::new(16385)
    $count=[Console]::In.ReadBlock($buffer,0,$buffer.Length)
    if($count -gt 16384){Fail-ProjectManager 'INVALID_REQUEST'}
    $request=ConvertTo-ProjectManagerRequest ([string]::new($buffer,0,$count))
    Initialize-ProjectManagerRuntime
    $result=switch -CaseSensitive ($request.operation) {
        'inspect' {Get-ProjectManagerInspection}
        'prepare_context_view' {Prepare-ProjectManagerContextView $request}
        'begin_candidate_export' {Begin-ProjectManagerCandidateExport $request}
        'inspect_export_dialog' {Inspect-ProjectManagerExportDialog $request}
        'save_candidate_export' {Complete-ProjectManagerCandidateExport $request}
        'cancel_candidate_export' {Complete-ProjectManagerCandidateExport $request}
        'export_candidate' {Export-ProjectManagerCandidate $request}
        'cancel_orphaned_probe_export' {Cancel-ProjectManagerOrphanedProbe $request}
        'begin_candidate_search' {Begin-ProjectManagerCandidateSearch $request}
        'filter_candidate' {Filter-ProjectManagerCandidate $request}
        'select_filtered_candidate' {Select-ProjectManagerFilteredCandidate $request}
        'open_existing' {Open-ProjectManagerExisting $request}
    }
    [Console]::WriteLine(($result|ConvertTo-Json -Depth 8 -Compress))
    exit 0
} catch {
    $code='DRIVER_FAILURE'
    if($_.Exception.Message -match 'PMUI_([A-Z][A-Z0-9_]+)'){$code=$Matches[1]}
    $uncertain=[bool]($script:OpenDispatched -or $script:ViewTouched -or $script:ExportTouched)
    $phase=if($uncertain){'after_dispatch'}else{'before_dispatch'}
    $detail=@{code=$code;uncertain=$uncertain;phase=$phase}
    if($code -ceq 'DRIVER_FAILURE'){
        $failure=Get-ProjectManagerExceptionDiagnostics $_
        foreach($key in $failure.Keys){$script:OperationDiagnostics[$key]=$failure[$key]}
    }
    if($script:OperationDiagnostics.Count -gt 0){$detail.diagnostics=$script:OperationDiagnostics}
    [Console]::WriteLine((@{ok=$false;error=$detail}|
        ConvertTo-Json -Depth 4 -Compress))
    exit 1
}
