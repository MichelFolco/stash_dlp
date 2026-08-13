# Watches Stash DLP project files and restarts tray_launcher.py when they change.
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $ProjectRoot 'tray_launcher.py'

# Runtime/generated files are intentionally ignored.
$IgnoredNames = @('tray_launcher.log', 'stash_dlp.log')
$IgnoredDirectories = @('.git', '__pycache__', '.venv', 'venv', 'node_modules', 'downloads', 'library_data')
$WatchedExtensions = @('.py', '.js', '.css', '.html', '.json', '.toml', '.ini', '.bat', '.ps1')

function Get-ProjectSnapshot {
    $snapshot = @{}
    Get-ChildItem -LiteralPath $ProjectRoot -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            $relative = $_.FullName.Substring($ProjectRoot.Length).TrimStart('\','/')
            $parts = $relative -split '[\\/]'
            ($parts.Count -eq 0 -or $IgnoredDirectories -notcontains $parts[0]) -and
            ($IgnoredNames -notcontains $_.Name) -and
            ($WatchedExtensions -contains $_.Extension.ToLowerInvariant())
        } |
        ForEach-Object {
            $snapshot[$_.FullName] = '{0}|{1}' -f $_.LastWriteTimeUtc.Ticks, $_.Length
        }
    return $snapshot
}

function Start-Server {
    Write-Host "Starting Stash DLP..."
    Start-Process -FilePath 'pythonw.exe' `
        -ArgumentList ('"{0}"' -f $Launcher) `
        -WorkingDirectory $ProjectRoot `
        -PassThru
}

function Stop-Server($Process) {
    if ($Process -and -not $Process.HasExited) {
        Write-Host "Stopping Stash DLP (PID $($Process.Id))..."
        try { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue } catch {}
        try { $Process.WaitForExit(3000) } catch {}
    }
}

$Server = Start-Server
$PreviousSnapshot = Get-ProjectSnapshot

try {
    while ($true) {
        Start-Sleep -Milliseconds 1000
        $CurrentSnapshot = Get-ProjectSnapshot

        $changed = $false
        if ($PreviousSnapshot.Count -ne $CurrentSnapshot.Count) {
            $changed = $true
        } else {
            foreach ($path in $CurrentSnapshot.Keys) {
                if (-not $PreviousSnapshot.ContainsKey($path) -or $PreviousSnapshot[$path] -ne $CurrentSnapshot[$path]) {
                    $changed = $true
                    break
                }
            }
        }

        if ($changed) {
            # Wait briefly so editors that save by replacing a file have finished
            # their write before the application is restarted.
            Start-Sleep -Milliseconds 750
            Write-Host "Project change detected. Restarting Stash DLP..."
            Stop-Server $Server
            $Server = Start-Server
            $PreviousSnapshot = Get-ProjectSnapshot
        } else {
            $PreviousSnapshot = $CurrentSnapshot
        }
    }
}
finally {
    Stop-Server $Server
}
