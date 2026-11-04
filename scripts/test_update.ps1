# Update the previous release to dist\'s build for real. The newest published release older than
# this build (N) is unzipped (portable) or installed (installer) and started. Then N's own updater
# code (its source, from its tag) checks a release folder holding dist\'s files and a latest.json
# signed with a throwaway key, downloads, verifies and applies, and N is ended the way it ends
# itself. The test checks that this build replaced N, started, and kept N's watch list.
# CI runs it (ci.yml); on a PC of your own -Kinds installed replaces an installed copy, so quit and
# back up first, or use -Kinds portable.
#
#   pwsh scripts/test_update.ps1 [-Kinds portable,installed] [-From X.Y.Z] [-Python python]
param(
    [string[]]$Kinds = @('portable', 'installed'),
    [string]$From = '',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # Windows PowerShell's progress bar slows downloads
$repo = 'jamesccupps/UnifiStockWatcher'
$helper = Join-Path $PSScriptRoot 'update_test_helper.py'
$dist = Join-Path (Split-Path $PSScriptRoot) 'dist'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{5B1E0F7A-3C2D-4E8B-9A61-7D2C4F0B8E35}_is1'

# On a runner, also as an annotation: job logs need a signed-in session, annotations don't.
function Write-Result([string]$level, [string]$message) {
    $message
    if ($env:GITHUB_ACTIONS) { "::${level} title=Update test::$($message -replace "`r?`n", '%0A')" }
}

$version = (& $Python -c "import sys; sys.path.insert(0, r'$(Split-Path $PSScriptRoot)'); import unifi_core; print(unifi_core.__version__)").Trim()
if (-not $From) { $From = "$(& $Python $helper previous)".Trim() }
if (-not $From) { Write-Result warning "no published release older than $version has an updater; nothing to test"; exit 0 }
"updating $From to this build, ${version}: $($Kinds -join ', ')"

# Not GetTempPath() on a runner: it can be an 8.3 short path, and processes report long ones.
$temp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$work = Join-Path $temp "usw-update-$PID"
$release = Join-Path $work 'release'
New-Item -ItemType Directory -Force $release | Out-Null
foreach ($f in 'UnifiStockWatcher-Setup.exe', 'UnifiStockWatcher-win64.zip') {
    if (Test-Path (Join-Path $dist $f)) { Copy-Item (Join-Path $dist $f) $release }
}
$key = "$(& $Python $helper sign $release)".Trim()
if ($LASTEXITCODE -ne 0 -or -not $key) { throw "couldn't sign the test release" }

function Get-Release([string]$name) {
    $out = Join-Path $work "v$From-$name"
    Invoke-WebRequest -UseBasicParsing "https://github.com/$repo/releases/download/v$From/$name" -OutFile $out
    $out
}
Invoke-WebRequest -UseBasicParsing "https://github.com/$repo/archive/refs/tags/v$From.zip" -OutFile "$work\source.zip"
Expand-Archive "$work\source.zip" "$work\source"
$oldSource = (Get-ChildItem "$work\source" -Directory | Select-Object -First 1).FullName

function Wait-Until([scriptblock]$done, [int]$seconds, [string]$what) {
    $end = (Get-Date).AddSeconds($seconds)
    while (-not (& $done)) {
        if ((Get-Date) -gt $end) { throw "timed out after $seconds s waiting for $what" }
        Start-Sleep -Milliseconds 500
    }
}
function Test-Log([string]$file, [string]$pattern) {
    (Test-Path $file) -and (Select-String -Path $file -Pattern $pattern -SimpleMatch -Quiet)
}
function Get-Copies([string]$dir) {
    @(Get-Process UnifiStockWatcher -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) })
}
function Stop-Copies([string]$dir) {
    Get-Copies $dir | Stop-Process -Force
    Start-Sleep -Seconds 1
}
function Get-InstalledVersion {
    (Get-ItemProperty $uninstallKey -ErrorAction SilentlyContinue).DisplayVersion
}
function Show-Logs([string]$data) {
    foreach ($name in 'update.log', 'unifi-stock-watcher.log') {
        $f = Join-Path $data $name
        if (Test-Path $f) { "--- $name"; Get-Content $f -Tail 20 }
    }
}
# N's data, as a user would have it: a watch list, and no update check of its own (it would ask
# GitHub, not the test release).
function Write-Data([string]$data) {
    New-Item -ItemType Directory -Force $data | Out-Null
    $list = '[{"slug": "udr7", "title": "Dream Router 7", "favourite": true, "price": "$279.00", "added_at": "2026-10-28T12:00:00"}]'
    [IO.File]::WriteAllText((Join-Path $data 'watched_items.json'), $list)
    [IO.File]::WriteAllText((Join-Path $data 'settings.json'), '{"check_updates": false}')
    (Get-FileHash (Join-Path $data 'watched_items.json')).Hash
}
function Start-Old([string]$dir, [string]$data) {
    $p = Start-Process (Join-Path $dir 'UnifiStockWatcher.exe') -WorkingDirectory $dir -PassThru
    Wait-Until { Test-Log (Join-Path $data 'unifi-stock-watcher.log') "$From starting" } 60 "$From to start"
    $p
}
function Assert-Updated([string]$dir, [string]$data, [string]$hash) {
    Wait-Until { Test-Log (Join-Path $data 'unifi-stock-watcher.log') "Updated to $version." } 120 "$version to start after the update"
    $exe = Join-Path $dir 'UnifiStockWatcher.exe'
    $fileVersion = (Get-Item $exe).VersionInfo.ProductVersion
    if ($fileVersion -ne $version) { throw "the exe is $fileVersion after the update, not $version" }
    $running = Get-Copies $dir
    if ($running.Count -ne 1) { throw "$($running.Count) copies running after the update" }
    if ((Get-FileHash (Join-Path $data 'watched_items.json')).Hash -ne $hash) { throw "the watch list changed" }
    $old = @(Get-ChildItem $dir -Filter *.old)
    if ($old.Count) { throw "the update left $($old.Name -join ', ')" }
}

foreach ($kind in $Kinds) {
    "--- $kind"
    if ($kind -eq 'portable') {
        Expand-Archive (Get-Release 'UnifiStockWatcher-win64.zip') "$work\portable"
        $dir = Join-Path $work 'portable\UnifiStockWatcher'
        $data = $dir
        Remove-Item Env:\USW_DATA_DIR -ErrorAction SilentlyContinue
    } elseif ($kind -eq 'installed') {
        $p = Start-Process (Get-Release 'UnifiStockWatcher-Setup.exe') -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "the $From installer exited with $($p.ExitCode)" }
        if ((Get-InstalledVersion) -ne $From) { throw "$From didn't install" }
        $dir = (Get-ItemProperty $uninstallKey).InstallLocation.TrimEnd('\')
        $data = Join-Path $work 'installed-data'
        $env:USW_DATA_DIR = $data
    } else { throw "unknown kind $kind" }
    $hash = Write-Data $data
    try {
        $old = Start-Old $dir $data
        $dataArg = if ($kind -eq 'installed') { $data } else { '' }
        & $Python $helper drive $oldSource $release $kind $dir $old.Id $key $dataArg
        if ($LASTEXITCODE -ne 0) { throw "the $From updater failed" }
        if ($kind -eq 'portable') {
            Wait-Until { Test-Log (Join-Path $data 'update.log') 'update: done' } 120 'the swap'
            if (-not (Test-Path (Join-Path $dir 'portable.txt'))) { throw "the update lost portable.txt" }
        } else {
            Wait-Until { (Get-InstalledVersion) -eq $version } 180 "Installed apps to say $version"
            if (Test-Path (Join-Path $dir 'portable.txt')) { throw "the installed copy gained portable.txt" }
        }
        Assert-Updated $dir $data $hash
        Show-Logs $data
        Write-Result notice "$kind ok: $From updated itself to $version"
    } catch {
        Show-Logs $data
        Write-Result error "$kind, $From to ${version}: $($_.Exception.Message)"
        throw
    } finally {
        Stop-Copies $dir
        Remove-Item Env:\USW_DATA_DIR -ErrorAction SilentlyContinue
        if ($kind -eq 'installed' -and (Test-Path $uninstallKey)) {
            $unins = Join-Path $dir 'unins000.exe'
            Start-Process $unins -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
            Wait-Until { -not (Test-Path $unins) } 60 'the uninstaller'
        }
    }
}
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
"update ok: $From to $version ($($Kinds -join ', '))"
