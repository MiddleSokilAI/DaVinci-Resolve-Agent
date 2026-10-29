[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ScriptsPath = (
        Join-Path (Split-Path -Parent $PSScriptRoot) "installer"
    )
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

try {
    $resolvedPath = [System.IO.Path]::GetFullPath($ScriptsPath)
    if (-not (Test-Path -LiteralPath $resolvedPath -PathType Container)) {
        throw "PowerShell scripts directory not found: $resolvedPath"
    }

    $scriptFiles = @(
        Get-ChildItem -LiteralPath $resolvedPath -File |
            Where-Object { $_.Extension -in @('.ps1', '.psm1') } |
            Sort-Object FullName
    )
    if ($scriptFiles.Count -eq 0) {
        throw "No PowerShell scripts found in: $resolvedPath"
    }

    $parseErrors = @()
    foreach ($scriptFile in $scriptFiles) {
        $tokens = $null
        $fileErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $scriptFile.FullName,
            [ref]$tokens,
            [ref]$fileErrors
        ) | Out-Null
        foreach ($fileError in @($fileErrors)) {
            $parseErrors += [PSCustomObject]@{
                File = $scriptFile.FullName
                Line = $fileError.Extent.StartLineNumber
                Message = $fileError.Message
            }
        }
    }

    if ($parseErrors.Count -gt 0) {
        $parseErrors | Format-Table -AutoSize | Out-String | Write-Error
        exit 1
    }

    Write-Host (
        "PowerShell syntax OK: parsed $($scriptFiles.Count) scripts."
    )
    exit 0
} catch {
    Write-Error "PowerShell syntax check failed: $($_.Exception.Message)"
    exit 1
}
