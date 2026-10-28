# Unzip UnifiStockWatcher-win64.zip to a scratch folder, check it is a portable
# copy (its files stay beside it, never in %TEMP% or %APPDATA%), and run the
# GUI's self-test and the CLI there.
#
#   pwsh scripts/test_portable.ps1 [-Zip dist\UnifiStockWatcher-win64.zip]
param([string]$Zip = "dist\UnifiStockWatcher-win64.zip")
$ErrorActionPreference = 'Stop'

$root = Join-Path ([IO.Path]::GetTempPath()) "usw-portable-$PID"
Expand-Archive $Zip -DestinationPath $root -Force
$dir = Join-Path $root 'UnifiStockWatcher'
foreach ($f in 'UnifiStockWatcher.exe', 'UnifiStockWatcher-cli.exe', 'portable.txt', 'LICENSE') {
    if (-not (Test-Path (Join-Path $dir $f))) { throw "the zip has no UnifiStockWatcher\$f" }
}

# The CLI reports where it keeps its files: beside the exe, for a portable copy. A frozen app that
# resolved them into PyInstaller's extraction folder would lose the watch list on every run.
$out = & (Join-Path $dir 'UnifiStockWatcher-cli.exe') --version | Out-String
$out
if ($LASTEXITCODE -ne 0) { throw "the CLI exited with $LASTEXITCODE" }
if ($out -notmatch 'portable: yes') { throw "the zip's copy isn't portable" }
# (By name: a runner's %TEMP% can be an 8.3 short path, and the app prints the long one.)
if ($out -notmatch '(?m)^config directory: .*\\UnifiStockWatcher\s*$') { throw "config not beside the exe" }

$t = Start-Process (Join-Path $dir 'UnifiStockWatcher.exe') -ArgumentList '--self-test' -WorkingDirectory $dir -Wait -PassThru
$report = Get-Content (Join-Path $dir 'selftest.json') -Raw | ConvertFrom-Json
"self-test: $($report | ConvertTo-Json -Compress)"
if ($t.ExitCode -ne 0 -or -not $report.ok) { throw "the portable copy's self-test failed" }
if ($report.kind -ne 'portable') { throw "the zip's copy thinks it is '$($report.kind)'" }
if (-not $report.tray) { throw "the tray icon didn't start" }
Remove-Item -Recurse -Force $root
"portable ok"
