# Fixed per-user Startup registration. No application UI, arbitrary commands,
# project edits, recording, scheduled tasks, administrator access or credentials.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$managedDescription = 'Managed by DaVinci-Resolve-Agent marker supervisor v1'

try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if ($request.operation -notin @('install', 'remove', 'status')) {
        throw 'Unknown Startup operation.'
    }
    $profilePath = [IO.Path]::GetFullPath([string]$request.profile_path)
    $pythonw = [IO.Path]::GetFullPath([string]$request.pythonw_executable)
    if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf) -or
        [IO.Path]::GetExtension($profilePath) -ine '.json' -or
        $profilePath.Contains('"') -or $profilePath.Contains("`r") -or
        $profilePath.Contains("`n") -or
        [IO.Path]::GetFileName($pythonw) -ine 'pythonw.exe') {
        throw 'Invalid installed watcher/profile.'
    }
    $startupDirectory = [Environment]::GetFolderPath('Startup')
    if (-not (Test-Path -LiteralPath $startupDirectory -PathType Container)) {
        throw 'User Startup directory unavailable.'
    }
    $shortcutPath = Join-Path $startupDirectory 'DaVinci Resolve Agent MCP.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $exists = Test-Path -LiteralPath $shortcutPath
    if ($exists) {
        if ((Get-Item -LiteralPath $shortcutPath).Attributes -band
            [IO.FileAttributes]::ReparsePoint) { throw 'Symbolic Startup entry refused.' }
        $previous = $shell.CreateShortcut($shortcutPath)
        if ($previous.Description -cne $managedDescription -or
            -not $previous.Arguments.StartsWith('-m agent.resolve_watch --profile "')) {
            throw 'Refusing an unmanaged Startup shortcut.'
        }
    }
    $legacyBackup = $null
    if ($request.operation -eq 'install') {
        if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
            throw 'Windowless Python executable unavailable.'
        }
        # Preserve and disable the former experimental hook. Its unmanaged
        # neighbours and any differently identified hook remain untouched.
        $legacyPath = Join-Path $env:APPDATA 'Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\ResolveAgentStartup.scriptlib'
        if (Test-Path -LiteralPath $legacyPath -PathType Leaf) {
            $legacyItem = Get-Item -LiteralPath $legacyPath
            if ($legacyItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Symbolic legacy hook refused.'
            }
            if ($legacyItem.Length -gt 65536) { throw 'Oversized legacy hook refused.' }
            $legacyText = [IO.File]::ReadAllText($legacyPath)
            if ($legacyText.StartsWith('-- Managed by DaVinci-Resolve-Agent lua_launcher v1')) {
                $backupDirectory = Join-Path ([IO.Path]::GetDirectoryName($profilePath)) 'legacy-startup'
                if (-not (Test-Path -LiteralPath $backupDirectory)) {
                    [void](New-Item -ItemType Directory -Path $backupDirectory)
                }
                if ((Get-Item -LiteralPath $backupDirectory).Attributes -band
                    [IO.FileAttributes]::ReparsePoint) { throw 'Symbolic backup directory refused.' }
                $legacyBackup = Join-Path $backupDirectory (([Guid]::NewGuid().ToString('N')) + '.scriptlib.disabled')
                Move-Item -LiteralPath $legacyPath -Destination $legacyBackup
            } else {
                throw 'Unmanaged competing startup hook requires operator review.'
            }
        }
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $pythonw
        $shortcut.Arguments = '-m agent.resolve_watch --profile "' + $profilePath + '"'
        $shortcut.WorkingDirectory = [IO.Path]::GetDirectoryName($profilePath)
        $shortcut.Description = $managedDescription
        $shortcut.WindowStyle = 7
        $shortcut.Save()
    } elseif ($request.operation -eq 'remove' -and $exists) {
        Remove-Item -LiteralPath $shortcutPath
    }
    [Console]::WriteLine((@{ok=$true;operation=$request.operation;
        startup_entry=$shortcutPath;installed=(Test-Path -LiteralPath $shortcutPath);
        legacy_hook_backup=$legacyBackup;recording_started=$false} | ConvertTo-Json -Compress))
} catch {
    [Console]::WriteLine((@{ok=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress))
    exit 1
}
