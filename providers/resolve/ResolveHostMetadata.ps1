# Fixed, read-only process metadata. No caller parameters, UI, or native SDK.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

function Write-MetadataResult {
    param([hashtable]$Result)
    [Console]::WriteLine(($Result | ConvertTo-Json -Compress -Depth 5))
}

function Write-Unavailable {
    param([string]$ReasonCode)
    Write-MetadataResult @{
        status = 'unavailable'
        reason_code = $ReasonCode
    }
}

if ($args.Count -ne 0) {
    Write-Unavailable 'invalid_driver_invocation'
    exit 0
}

try {
    $enumerationErrors = @()
    $resolveProcesses = @(
        Get-Process -Name Resolve -ErrorAction SilentlyContinue `
            -ErrorVariable enumerationErrors
    )
    foreach ($enumerationError in $enumerationErrors) {
        # This exact no-match result is the sole accepted enumeration error.
        if ($enumerationError.FullyQualifiedErrorId -ne
            'NoProcessFoundForGivenName,Microsoft.PowerShell.Commands.GetProcessCommand') {
            Write-Unavailable 'process_enumeration_unavailable'
            exit 0
        }
    }
} catch {
    Write-Unavailable 'process_enumeration_unavailable'
    exit 0
}

if ($resolveProcesses.Count -gt 8) {
    Write-Unavailable 'process_limit_exceeded'
    exit 0
}

$metadata = @()
foreach ($resolveProcess in $resolveProcesses) {
    try {
        $resolveProcess.Refresh()
        if ($resolveProcess.HasExited) {
            Write-Unavailable 'process_metadata_unavailable'
            exit 0
        }
        $identifier = $resolveProcess.Id
        $title = [string]$resolveProcess.MainWindowTitle
        $windowHandle = $resolveProcess.MainWindowHandle.ToInt64()
        $responding = $resolveProcess.Responding
        if ($title.Length -gt 512) {
            Write-Unavailable 'metadata_bounds_exceeded'
            exit 0
        }
        $executablePath = $null
        try {
            $candidatePath = $resolveProcess.Path
            if ($null -ne $candidatePath -and $candidatePath.Length -le 1024) {
                $executablePath = [string]$candidatePath
            }
        } catch {
            # Path can require additional access. It is optional evidence.
            $executablePath = $null
        }
        $resolveProcess.Refresh()
        if ($resolveProcess.HasExited) {
            Write-Unavailable 'process_metadata_unavailable'
            exit 0
        }
        $metadata += @{
            pid = $identifier
            title = $title
            main_window_handle = $windowHandle
            responding = $responding
            executable_path = $executablePath
        }
    } catch {
        # Exit/access races leave evidence unavailable, never "not running".
        Write-Unavailable 'process_metadata_unavailable'
        exit 0
    }
}

Write-MetadataResult @{
    status = 'observed'
    processes = @($metadata)
}
