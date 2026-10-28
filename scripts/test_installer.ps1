# Install UnifiStockWatcher-Setup.exe silently, check the install the app and
# Windows rely on, run the installed copy's self-test, then uninstall silently.
# CI runs it on a throwaway runner (ci.yml, release.yml); on a PC of your own it
# replaces any installed copy, so quit and back up first.
#
#   pwsh scripts/test_installer.ps1 -Setup dist\UnifiStockWatcher-Setup.exe -Version 3.0.0
param(
    [string]$Setup = "dist\UnifiStockWatcher-Setup.exe",
    [Parameter(Mandatory = $true)][string]$Version
)
$ErrorActionPreference = 'Stop'
$quiet = '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'

$p = Start-Process $Setup -ArgumentList $quiet -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "installer exited with $($p.ExitCode)" }

# The Add/Remove Programs entry, the way Windows finds the install (AppId in the .iss).
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{5B1E0F7A-3C2D-4E8B-9A61-7D2C4F0B8E35}_is1'
$entry = Get-ItemProperty $key
"entry: $($entry.DisplayName) $($entry.DisplayVersion) at $($entry.InstallLocation)"
if ($entry.DisplayVersion -ne $Version) { throw "Installed apps says $($entry.DisplayVersion), not $Version" }
$dir = $entry.InstallLocation.TrimEnd('\')
$exe = Join-Path $dir 'UnifiStockWatcher.exe'
foreach ($f in 'UnifiStockWatcher.exe', 'UnifiStockWatcher-cli.exe', 'unins000.exe') {
    if (-not (Test-Path (Join-Path $dir $f))) { throw "no $f in $dir" }
}
# install_kind() tells an installed copy by unins000.exe and a portable one by this file.
if (Test-Path (Join-Path $dir 'portable.txt')) { throw "the installed copy carries portable.txt" }
if (-not $dir.StartsWith((Join-Path $env:LOCALAPPDATA 'Programs'))) { throw "installed outside %LOCALAPPDATA%\Programs: $dir" }
$fileVersion = (Get-Item $exe).VersionInfo.ProductVersion
if ($fileVersion -ne $Version) { throw "the exe says $fileVersion, not $Version" }
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\UniFi Stock Watcher.lnk'
if (-not (Test-Path $lnk)) { throw "no Start menu shortcut at $lnk" }

# The installed copy, started the way Windows starts it, in its self-test mode.
$data = Join-Path ([IO.Path]::GetTempPath()) "usw-installed-$PID"
New-Item -ItemType Directory -Force $data | Out-Null
$env:USW_DATA_DIR = $data
$t = Start-Process $exe -ArgumentList '--self-test' -WorkingDirectory $dir -Wait -PassThru
Remove-Item Env:\USW_DATA_DIR
$report = Get-Content (Join-Path $data 'selftest.json') -Raw | ConvertFrom-Json
"self-test: $($report | ConvertTo-Json -Compress)"
if ($t.ExitCode -ne 0 -or -not $report.ok) { throw "the installed copy's self-test failed" }
if ($report.kind -ne 'installed') { throw "the installed copy thinks it is '$($report.kind)'" }
if ($report.version -ne $Version) { throw "the installed copy is $($report.version)" }

# Inno's uninstaller copies itself to %TEMP% and returns at once: wait for the files to go.
Start-Process (Join-Path $dir 'unins000.exe') -ArgumentList $quiet -Wait
for ($i = 0; $i -lt 60 -and (Test-Path $exe); $i++) { Start-Sleep -Seconds 1 }
if (Test-Path $exe) { throw "uninstall left $exe behind" }
if (Test-Path $lnk) { throw "uninstall left the Start menu shortcut" }
if (Test-Path $key) { throw "uninstall left the Installed apps entry" }
"installer ok: $Version installed, self-tested and uninstalled"
