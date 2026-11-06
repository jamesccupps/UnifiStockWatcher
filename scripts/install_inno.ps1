# Install the Inno Setup that builds the installer, pinned by version and SHA-256, and point
# scripts/build_windows.py at it (ISCC, through GITHUB_ENV). CI and the release run this, so both
# build with the same compiler, whatever the runner image happens to have preinstalled.
#
#     pwsh scripts/install_inno.ps1
#
# To move to a newer Inno Setup 6: take the version and the installer's SHA-256 from its release
# at https://github.com/jrsoftware/issrc/releases (GitHub shows each file's sha256), change both
# defaults below, and let CI build and installer-test it before a release uses it.
param(
    [string]$Version = '6.7.4',
    [string]$Sha256 = 'a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$temp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$setup = Join-Path $temp "innosetup-$Version.exe"
$dir = Join-Path $temp "inno-setup-$Version"
$log = Join-Path $temp "inno-setup-$Version-install.log"

# Job logs need a signed-in session; annotations don't. So a failure says what it was there,
# with the end of Inno Setup's own install log.
$script:reported = $false
function Fail([string]$message) {
    $script:reported = $true
    if ($env:GITHUB_ACTIONS) {
        $text = $message
        if (Test-Path $log) { $text += "`n--- the end of ${log}:`n" + ((Get-Content $log -Tail 30) -join "`n") }
        $text = $text.Replace('%', '%25').Replace("`r", '').Replace("`n", '%0A')
        Write-Host "::error title=Inno Setup::$text"
    }
    throw $message
}

try {
    $tag = 'is-' + $Version.Replace('.', '_')
    Invoke-WebRequest "https://github.com/jrsoftware/issrc/releases/download/$tag/innosetup-$Version.exe" -OutFile $setup -UseBasicParsing
    $got = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $Sha256) {
        Remove-Item -Force $setup
        Fail "innosetup-$Version.exe has SHA-256 $got, expected $Sha256"
    }

    # Into a folder of its own, so the runner's preinstalled copy (if any) is never what runs.
    $p = Start-Process $setup -Wait -PassThru -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS', "/DIR=`"$dir`"", "/LOG=`"$log`"")
    if ($p.ExitCode -ne 0) { Fail "Inno Setup's installer exited with $($p.ExitCode)" }

    $iscc = Join-Path $dir 'ISCC.exe'
    if (-not (Test-Path $iscc)) {
        $found = (Get-ChildItem $dir -ErrorAction SilentlyContinue | Select-Object -First 15 -ExpandProperty Name) -join ', '
        Fail "no ISCC.exe in $dir (it holds: $found)"
    }
    $info = (Get-Item $iscc).VersionInfo
    $have = '{0}.{1}.{2}' -f $info.FileMajorPart, $info.FileMinorPart, $info.FileBuildPart
    if ($have -ne $Version) { Fail "$iscc is version $have (file version '$($info.FileVersion)'), expected $Version" }
    Write-Host "Inno Setup $have at $iscc"
    if ($env:GITHUB_ENV) { "ISCC=$iscc" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8 }
} catch {
    if (-not $script:reported) { Fail "$_" }
    throw
}
