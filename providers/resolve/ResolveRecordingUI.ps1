# Trusted semantic Windows UI driver. Requests are JSON data on stdin.
# No recording, arbitrary selectors, coordinates, scripts, or keystrokes are accepted.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:InputChoiceTouched = $false
$script:BoundProcessId = 0
$script:BoundWindowHandle = 0
$script:ProjectName = ''

function Fail([string]$Code) { throw ('WRUI_' + $Code) }

try {
    $text = [Console]::In.ReadToEnd()
    if ($text.Length -gt 4096) { Fail 'INVALID_REQUEST' }
    $request = $text | ConvertFrom-Json
    $keys = @($request.PSObject.Properties.Name | Sort-Object)
    if ($request.operation -in @('inspect_transport','inspect_input_device')) {
        if (($keys -join ',') -cne 'expected_project_name,operation') { Fail 'INVALID_REQUEST' }
    } elseif ($request.operation -eq 'set_input_device') {
        if (($keys -join ',') -cne 'device_name,expected_project_name,operation') { Fail 'INVALID_REQUEST' }
        if ($request.device_name -isnot [string] -or $request.device_name.Length -gt 256 -or
            $request.device_name -cne $request.device_name.Trim() -or
            $request.device_name -match '[\x00-\x1f\x7f]' -or -not $request.device_name) { Fail 'INVALID_REQUEST' }
    } else { Fail 'INVALID_OPERATION' }
    if ($request.expected_project_name -isnot [string] -or
        $request.expected_project_name.Length -gt 128 -or
        $request.expected_project_name -cne $request.expected_project_name.Trim() -or
        $request.expected_project_name -match '[\x00-\x1f\x7f]' -or
        -not $request.expected_project_name) { Fail 'INVALID_REQUEST' }
    $script:ProjectName = $request.expected_project_name

    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName WindowsBase
    Add-Type @'
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)] public struct RecordingPoint { public int X,Y; }
public static class RecordingNative {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint id);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool GetPhysicalCursorPos(out RecordingPoint point);
    [DllImport("user32.dll")] private static extern void mouse_event(uint flags, uint x, uint y, uint data, UIntPtr extra);
    public static RecordingPoint Position() {
        RecordingPoint result;
        if (!GetPhysicalCursorPos(out result)) throw new InvalidOperationException("WRUI_CURSOR_UNAVAILABLE");
        return result;
    }
    public static void Click() {
        mouse_event(2,0,0,0,UIntPtr.Zero);
        System.Threading.Thread.Sleep(80);
        mouse_event(4,0,0,0,UIntPtr.Zero);
    }
}
'@

    function Get-MainWindow {
        $title = 'DaVinci Resolve - ' + $script:ProjectName
        $windows = @([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::NameProperty, $title)) |
            Where-Object { $_.Current.ClassName -ceq 'UiMainWindowImp' -and -not $_.Current.IsOffscreen })
        if ($windows.Count -ne 1) { Fail 'PROJECT_WINDOW_UNAVAILABLE' }
        $window = $windows[0]
        $process = Get-Process -Id $window.Current.ProcessId -ErrorAction Stop
        if ($process.ProcessName -cne 'Resolve' -or
            [IO.Path]::GetFileName($process.MainModule.FileName) -ine 'Resolve.exe') { Fail 'WRONG_PROCESS' }
        if ($script:BoundProcessId -ne 0 -and
            ($window.Current.ProcessId -ne $script:BoundProcessId -or
            $window.Current.NativeWindowHandle -ne $script:BoundWindowHandle)) { Fail 'PROJECT_WINDOW_CHANGED' }
        return $window
    }

    function Get-Children($Scope) {
        return $Scope.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
    }

    function Get-One($Scope, [string[]]$Names, [string]$Type, [string]$Class = '') {
        $items = @(Get-Children $Scope | Where-Object {
            $_.Current.Name -cin $Names -and -not $_.Current.IsOffscreen -and
            $_.Current.ProcessId -eq $script:BoundProcessId -and
            $_.Current.ControlType.ProgrammaticName -ceq ('ControlType.' + $Type) -and
            (-not $Class -or $_.Current.ClassName -ceq $Class)
        })
        if ($items.Count -ne 1) { Fail 'CONTROL_UNAVAILABLE_OR_AMBIGUOUS' }
        return $items[0]
    }

    function Get-Preferences {
        $main = Get-MainWindow
        $found = @(Get-Children $main | Where-Object {
            $_.Current.ClassName -ceq 'UiPreferencesDialogImp' -and -not $_.Current.IsOffscreen
        })
        # Qt may expose an owned modal as a top-level native window.
        if ($found.Count -eq 0) {
            $found = @([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
                [System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.PropertyCondition]::new(
                    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $script:BoundProcessId)) |
                Where-Object { $_.Current.ClassName -ceq 'UiPreferencesDialogImp' -and -not $_.Current.IsOffscreen })
        }
        if ($found.Count -gt 1) { Fail 'PREFERENCES_AMBIGUOUS' }
        if ($found.Count -eq 1) { return $found[0] }
        return $null
    }

    function Get-Transport([bool]$RequireEnabled = $false) {
        $main = Get-MainWindow
        $result = @{}
        $all = Get-Children $main
        foreach ($pair in @(@('recording','Record'), @('playing','Play'), @('stopped','Stop'))) {
            $suffix = '.playbackControlsBar.UiTransportControlsWidget.btnTransport' + $pair[1]
            $controls = @($all | Where-Object { $_.Current.AutomationId.EndsWith($suffix) })
            if ($controls.Count -ne 1) { Fail 'TRANSPORT_UNAVAILABLE' }
            $control = $controls[0]
            if ($RequireEnabled -and -not $control.Current.IsEnabled) { Fail 'TRANSPORT_DISABLED' }
            $toggle = $null
            if (-not $control.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$toggle)) {
                Fail 'TRANSPORT_STATE_UNAVAILABLE'
            }
            $state = $toggle.Current.ToggleState
            if ($state -eq [System.Windows.Automation.ToggleState]::Indeterminate) { Fail 'TRANSPORT_STATE_UNAVAILABLE' }
            $result[$pair[0]] = $state -eq [System.Windows.Automation.ToggleState]::On
        }
        $result.verification = 'windows_uia_toggle_state'
        return $result
    }

    function Assert-Stopped([bool]$RequireEnabled = $false) {
        $state = Get-Transport $RequireEnabled
        if ($state.recording -or $state.playing -or -not $state.stopped) { Fail 'TRANSPORT_NOT_STOPPED' }
    }

    function Assert-Foreground {
        [void](Get-MainWindow)
        [uint32]$foregroundId = 0
        [void][RecordingNative]::GetWindowThreadProcessId([RecordingNative]::GetForegroundWindow(), [ref]$foregroundId)
        if ($foregroundId -ne $script:BoundProcessId) { Fail 'FOCUS_LOST' }
    }

    function Same-Element($Left, $Right) {
        return [System.Windows.Automation.Automation]::Compare($Left, $Right)
    }

    function Click-Control($Target) {
        Assert-Stopped
        Assert-Foreground
        if ($Target.Current.ProcessId -ne $script:BoundProcessId -or
            -not $Target.Current.IsEnabled -or $Target.Current.IsOffscreen -or
            $Target.Current.AutomationId -match 'btnTransportRecord' -or
            $Target.Current.Name -ceq 'Record') { Fail 'FORBIDDEN_OR_DISABLED_TARGET' }
        $rectangle = $Target.Current.BoundingRectangle
        if ($rectangle.IsEmpty -or $rectangle.Width -lt 2 -or $rectangle.Height -lt 2) { Fail 'TARGET_GEOMETRY_UNAVAILABLE' }
        $before = [RecordingNative]::Position()
        Start-Sleep -Milliseconds 120
        $stable = [RecordingNative]::Position()
        if ([Math]::Abs($before.X-$stable.X) -gt 3 -or [Math]::Abs($before.Y-$stable.Y) -gt 3) { Fail 'CURSOR_INTERFERENCE' }
        $x = [int]($rectangle.X + $rectangle.Width/2)
        $y = [int]($rectangle.Y + $rectangle.Height/2)
        if (-not [RecordingNative]::SetCursorPos($x,$y)) { Fail 'CURSOR_MOVE_FAILED' }
        Start-Sleep -Milliseconds 120
        $position = [RecordingNative]::Position()
        if ([Math]::Abs($position.X-$x) -gt 3 -or [Math]::Abs($position.Y-$y) -gt 3) { Fail 'CURSOR_CLIPPED_OR_MOVED' }
        $hit = [System.Windows.Automation.AutomationElement]::FromPoint([System.Windows.Point]::new($position.X,$position.Y))
        if ($hit.Current.ProcessId -ne $script:BoundProcessId -or
            $hit.Current.AutomationId -match 'btnTransportRecord' -or $hit.Current.Name -ceq 'Record') { Fail 'WRONG_TARGET' }
        $ancestor = $hit
        $matched = $false
        for ($depth=0; $depth -lt 5 -and $null -ne $ancestor; $depth++) {
            if (Same-Element $ancestor $Target) { $matched=$true; break }
            $ancestor = [System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($ancestor)
        }
        if (-not $matched) { Fail 'WRONG_TARGET' }
        Assert-Foreground
        $last = [RecordingNative]::Position()
        if ([Math]::Abs($last.X-$position.X) -gt 3 -or [Math]::Abs($last.Y-$position.Y) -gt 3) { Fail 'CURSOR_INTERFERENCE' }
        [RecordingNative]::Click()
        Start-Sleep -Milliseconds 150
    }

    function Invoke-PreferencesButton($Preferences, [string]$Name) {
        Assert-Stopped
        Assert-Foreground
        $button = Get-One $Preferences @($Name) 'Button' 'QPushButton'
        if (-not $button.Current.IsEnabled) { Fail 'PREFERENCES_BUTTON_DISABLED' }
        $invoke = $null
        if (-not $button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$invoke)) {
            Fail 'PREFERENCES_BUTTON_UNAVAILABLE'
        }
        $invoke.Invoke()
        Start-Sleep -Milliseconds 150
    }

    function Open-Preferences {
        if ($null -ne (Get-Preferences)) { Fail 'PREFERENCES_ALREADY_OPEN' }
        $main = Get-MainWindow
        Click-Control (Get-One $main @('DaVinci Resolve') 'MenuItem')
        $main = Get-MainWindow
        Click-Control (Get-One $main @('Preferences','Preferences...',('Preferences'+[char]0x2026)) 'MenuItem')
        $preferences = Get-Preferences
        if ($null -eq $preferences) { Fail 'PREFERENCES_DID_NOT_OPEN' }
        $system = @(Get-Children $preferences | Where-Object {
            $_.Current.Name -ceq 'System' -and -not $_.Current.IsOffscreen -and
            $_.Current.ControlType -ne [System.Windows.Automation.ControlType]::Text
        })
        if ($system.Count -ne 1) { Fail 'SYSTEM_TAB_UNAVAILABLE' }
        Click-Control $system[0]
        Click-Control (Get-One $preferences @('Video and Audio I/O') 'ListItem')
        return (Get-Preferences)
    }

    function Get-InputCombo($Preferences) {
        $label = Get-One $Preferences @('Input device') 'Text' 'QLabel'
        $labelRectangle = $label.Current.BoundingRectangle
        $middle = $labelRectangle.Y + $labelRectangle.Height/2
        $combos = @(Get-Children $Preferences | Where-Object {
            if ($_.Current.ControlType -ne [System.Windows.Automation.ControlType]::ComboBox -or
                $_.Current.IsOffscreen) { return $false }
            $rectangle = $_.Current.BoundingRectangle
            return $rectangle.X -ge ($labelRectangle.Right-2) -and
                [Math]::Abs(($rectangle.Y+$rectangle.Height/2)-$middle) -le [Math]::Max(3,$labelRectangle.Height/2)
        })
        if ($combos.Count -ne 1) { Fail 'INPUT_SELECTOR_UNAVAILABLE_OR_AMBIGUOUS' }
        return $combos[0]
    }

    function Read-Input($Combo) {
        $value = $null
        if (-not $Combo.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$value)) {
            Fail 'INPUT_READBACK_UNAVAILABLE'
        }
        $name = $value.Current.Value
        if (-not $name) { Fail 'INPUT_READBACK_UNAVAILABLE' }
        return $name
    }

    $main = Get-MainWindow
    $script:BoundProcessId = $main.Current.ProcessId
    $script:BoundWindowHandle = $main.Current.NativeWindowHandle
    $windowResult = @{ pid=$script:BoundProcessId; handle=$script:BoundWindowHandle }
    if ($request.operation -eq 'inspect_transport') {
        $result = @{ ok=$true; operation='inspect_transport'; backend='windows_uia';
            project_name=$script:ProjectName; window=$windowResult;
            transport=(Get-Transport); recording_ready=$null }
    } else {
        Assert-Stopped $true
        if ($null -ne (Get-Preferences)) { Fail 'PREFERENCES_ALREADY_OPEN' }
        [void][RecordingNative]::SetForegroundWindow([IntPtr]$script:BoundWindowHandle)
        # UIA activation can succeed when the hidden child process cannot use
        # SetForegroundWindow directly. The actual foreground PID is still
        # checked below; activation never substitutes for that check.
        try { (Get-MainWindow).SetFocus() } catch { Fail 'FOCUS_ACTIVATION_FAILED' }
        Start-Sleep -Milliseconds 150
        Assert-Foreground
        $preferences = Open-Preferences
        $combo = Get-InputCombo $preferences
        $before = Read-Input $combo
        if ($request.operation -eq 'inspect_input_device') {
            Invoke-PreferencesButton $preferences 'Cancel'
            if ($null -ne (Get-Preferences)) { Fail 'PREFERENCES_DID_NOT_CLOSE' }
            Assert-Stopped $true
            [void](Get-MainWindow)
            @{ ok=$true; operation='inspect_input_device'; backend='windows_uia';
                project_name=$script:ProjectName; window=$windowResult;
                transport=(Get-Transport $true); recording_ready=$null;
                input_device=@{ name=$before; verification='windows_uia_preferences_readback' } } |
                ConvertTo-Json -Depth 6 -Compress
            exit 0
        }
        $changed = $before -cne $request.device_name
        if ($changed) {
            Click-Control $combo
            $choice = Get-One $combo @($request.device_name) 'ListItem'
            $script:InputChoiceTouched = $true
            Click-Control $choice
            if ((Read-Input $combo) -cne $request.device_name) { Fail 'INPUT_SELECTION_NOT_APPLIED' }
            Invoke-PreferencesButton $preferences 'Save'
        } else {
            Invoke-PreferencesButton $preferences 'Cancel'
        }
        if ($null -ne (Get-Preferences)) { Fail 'PREFERENCES_DID_NOT_CLOSE' }
        Assert-Stopped $true
        $preferences = Open-Preferences
        $reopened = Read-Input (Get-InputCombo $preferences)
        if ($reopened -cne $request.device_name) { Fail 'INPUT_REOPENED_READBACK_MISMATCH' }
        Invoke-PreferencesButton $preferences 'Cancel'
        if ($null -ne (Get-Preferences)) { Fail 'PREFERENCES_DID_NOT_CLOSE' }
        Assert-Stopped $true
        [void](Get-MainWindow)
        $result = @{ ok=$true; operation='set_input_device'; backend='windows_uia';
            project_name=$script:ProjectName; window=$windowResult;
            transport=(Get-Transport $true); recording_ready=$null;
            input_device=@{ name=$reopened; changed=$changed; verification='windows_uia_reopened' };
            input_patching_verified=$false; routing_may_require_repatch=$changed }
    }
    $result | ConvertTo-Json -Depth 6 -Compress
    exit 0
} catch {
    $code = 'UI_AUTOMATION_FAILED'
    if ($_.Exception.Message -match 'WRUI_([A-Z_]{1,80})') { $code = $Matches[1] }
    @{ ok=$false; error=@{ code=$code; uncertain=$script:InputChoiceTouched } } |
        ConvertTo-Json -Depth 4 -Compress
    exit 1
}
