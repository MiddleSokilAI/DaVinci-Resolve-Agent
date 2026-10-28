<#
.SYNOPSIS
Read-only desktop capture of the explicitly selected Resolve main window.
.DESCRIPTION
An optional local diagnostic for operators whose Windows Graphics Capture
client fails. It never clicks controls, sends keys, records audio, invokes
menus, changes application settings, or exposes a desktop automation MCP tool.
Windows-only opt-in diagnostic; it is not used by the marker data transport.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [int]$ResolveProcessId,
    [long]$WindowHandle = 0,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedTitle,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [switch]$Maximize,
    [switch]$KeepFocus
)

$ErrorActionPreference = 'Stop'
# Per-thread DPI awareness keeps UIA's physical bounds and GDI coordinates
# consistent on mixed-DPI monitors. This changes no Windows display setting.
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ResolveCaptureDpi {
    [DllImport("user32.dll")]
    public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
    [DllImport("dwmapi.dll")]
    public static extern int DwmGetWindowAttribute(IntPtr window, int attribute,
        out ResolveCaptureRect rectangle, int size);
}
[StructLayout(LayoutKind.Sequential)]
public struct ResolveCaptureRect {
    public int Left, Top, Right, Bottom;
}
'@
$previousResolveDpi = [ResolveCaptureDpi]::SetThreadDpiAwarenessContext([IntPtr](-4))
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing

$resolveProcess = Get-Process -Id $ResolveProcessId
if ($WindowHandle -eq 0) {
    $WindowHandle = $resolveProcess.MainWindowHandle.ToInt64()
}
if ($resolveProcess.ProcessName -ne 'Resolve' -or $WindowHandle -eq 0) {
    throw 'The selected Resolve process/window no longer matches.'
}
$resolveWindow = [System.Windows.Automation.AutomationElement]::FromHandle(
    [IntPtr]$WindowHandle
)
if ($resolveWindow.Current.ProcessId -ne $ResolveProcessId -or
    $resolveWindow.Current.Name -cne $ExpectedTitle) {
    throw 'UI Automation returned a different window.'
}
if ($Maximize) {
    $resolveWindowPattern = $resolveWindow.GetCurrentPattern(
        [System.Windows.Automation.WindowPattern]::Pattern
    )
    $resolveWindowPattern.SetWindowVisualState(
        [System.Windows.Automation.WindowVisualState]::Maximized
    )
}
$resolveBounds = $resolveWindow.Current.BoundingRectangle
$resolvePhysicalRect = New-Object ResolveCaptureRect
$resolveDwmStatus = [ResolveCaptureDpi]::DwmGetWindowAttribute(
    [IntPtr]$WindowHandle, 9, [ref]$resolvePhysicalRect,
    [Runtime.InteropServices.Marshal]::SizeOf([type][ResolveCaptureRect])
)
if ($resolveDwmStatus -ne 0) {
    throw 'DWM physical bounds are unavailable; refusing an ambiguous capture.'
}
$resolveBounds = [pscustomobject]@{
    Left = $resolvePhysicalRect.Left
    Top = $resolvePhysicalRect.Top
    Width = $resolvePhysicalRect.Right - $resolvePhysicalRect.Left
    Height = $resolvePhysicalRect.Bottom - $resolvePhysicalRect.Top
    IsEmpty = $false
}
if ($resolveBounds.IsEmpty -or $resolveBounds.Width -lt 200 -or
    $resolveBounds.Height -lt 100 -or $resolveBounds.Width -gt 10000 -or
    $resolveBounds.Height -gt 10000) {
    throw 'The selected Resolve window has no valid visible bounds.'
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
if ([IO.Path]::GetExtension($resolvedOutput) -ine '.png' -or
    -not (Test-Path -LiteralPath ([IO.Path]::GetDirectoryName($resolvedOutput))) -or
    (Test-Path -LiteralPath $resolvedOutput)) {
    throw 'Choose a new PNG file inside an existing output directory.'
}

# Focus only the selected main window. No control is invoked or toggled.
if (-not $KeepFocus) {
    $resolveWindow.SetFocus()
}
$resolveWidth = [int][Math]::Ceiling($resolveBounds.Width)
$resolveHeight = [int][Math]::Ceiling($resolveBounds.Height)
$resolveBitmap = [System.Drawing.Bitmap]::new($resolveWidth, $resolveHeight)
$resolveGraphics = [System.Drawing.Graphics]::FromImage($resolveBitmap)
try {
    $resolveGraphics.CopyFromScreen(
        [int][Math]::Floor($resolveBounds.Left),
        [int][Math]::Floor($resolveBounds.Top),
        0, 0, $resolveBitmap.Size,
        [System.Drawing.CopyPixelOperation]::SourceCopy
    )
    $resolveBitmap.Save($resolvedOutput, [System.Drawing.Imaging.ImageFormat]::Png)
}
finally {
    $resolveGraphics.Dispose()
    $resolveBitmap.Dispose()
    if ($previousResolveDpi -ne [IntPtr]::Zero) {
        [void][ResolveCaptureDpi]::SetThreadDpiAwarenessContext($previousResolveDpi)
    }
}
[pscustomobject]@{
    ProcessId = $ResolveProcessId
    WindowHandle = $WindowHandle
    Title = $ExpectedTitle
    OutputPath = $resolvedOutput
    Left = $resolveBounds.Left
    Top = $resolveBounds.Top
    Width = $resolveWidth
    Height = $resolveHeight
    ReadOnly = $true
} | ConvertTo-Json -Compress
