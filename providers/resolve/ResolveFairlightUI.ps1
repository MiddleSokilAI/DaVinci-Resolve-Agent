# Fixed, opt-in Fairlight driver. Stdin is data; it never selects executable code.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:FairlightMutationTouched = $false
$script:BoundProcessId = 0
$script:BoundWindowHandle = 0
$script:FairlightContext = $null
$script:FairlightOwnedDialogs = @{}
$script:FairlightPreviousDpiContext = [IntPtr]::Zero

try {
    # PSM1 is not directly dot-sourceable on Windows. Only these two shipped,
    # literal paths are compiled; no request field enters the source or path.
    . ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'ResolveFairlightCore.psm1'))))
    . ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'ResolveFairlightPatch.psm1'))))
    $request = ConvertTo-FairlightRequest ([Console]::In.ReadToEnd())
    Initialize-FairlightContext $request
    $result = @{
        ok = $true
        operation = $request.operation
        backend = 'windows_fairlight'
        project_name = $request.expected_project_name
        window = @{ pid = $script:BoundProcessId; handle = $script:BoundWindowHandle }
        target = $request.target
        recording_ready = $null
        compatibility = @{ status = 'live_pending'; profile = 'resolve-21.1-english' }
    }
    switch -CaseSensitive ($request.operation) {
        'inspect_processing' { $result.processing = Read-FairlightProcessing $request.target $request.module }
        'set_processing' {
            $value = Set-FairlightProcessing $request.target $request.module $request.enabled
            $result.processing = $value.processing
            $result.changed = $value.changed
        }
        'inspect_arm' { $result.record_arm = Read-FairlightArm $request.target }
        'set_arm' {
            $value = Set-FairlightArm $request.target $request.armed
            $result.record_arm = $value.record_arm
            $result.changed = $value.changed
        }
        'inspect_patch' { $result.input_patch = (Read-FairlightPatch $request.target).input_patch }
        'set_patch' {
            $before = (Read-FairlightPatch $request.target).input_patch
            $same = $before.source_name -ceq $request.source_name -and
                $before.source_channel -eq $request.source_channel
            if (-not $same) {
                $null = Set-FairlightPatch $request.target $request.source_name $request.source_channel
                # Reopening is mandatory even if the patch implementation also reads back.
                $after = (Read-FairlightPatch $request.target).input_patch
                if ($after.source_name -cne $request.source_name -or
                    $after.source_channel -ne $request.source_channel) { Fail-Fairlight 'PATCH_READBACK_MISMATCH' }
                $result.input_patch = $after
            } else { $result.input_patch = $before }
            $result.changed = -not $same
        }
        default { Fail-Fairlight 'INVALID_OPERATION' }
    }
    if ($request.operation.StartsWith('set_') -and $result.changed) {
        Save-FairlightProject
    }
    Assert-FairlightContext
    $result.transport = Get-FairlightTransport
    $result | ConvertTo-Json -Depth 12 -Compress
    exit 0
} catch {
    $message = $_.Exception.Message
    $code = 'DRIVER_FAILURE'
    if ($message -match 'FLUI_([A-Z][A-Z0-9_]+)') { $code = $Matches[1] }
    @{ ok = $false; error = @{ code = $code; uncertain = [bool]$script:FairlightMutationTouched } } |
        ConvertTo-Json -Depth 4 -Compress
    exit 1
} finally {
    # Only windows registered by this invocation may be closed. Existing user
    # dialogs are rejected before any action and are never registered here.
    if (Get-Command Close-FairlightOwnedDialogs -ErrorAction SilentlyContinue) {
        Close-FairlightOwnedDialogs
    }
    if (Get-Command Restore-FairlightDpi -ErrorAction SilentlyContinue) {
        Restore-FairlightDpi
    }
}
