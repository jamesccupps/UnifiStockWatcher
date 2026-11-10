# Authenticode-sign one file as James Cupps, through Azure Artifact Signing (the account and
# certificate profile Slate, NoteForge and PingerPlot sign with), with an RFC 3161 timestamp so
# the signature stays valid after the short-lived certificate expires.
#
# build_windows.py --package --authenticode runs it for UnifiStockWatcher.exe and
# UnifiStockWatcher-cli.exe, and Inno Setup (its SignTool) for the uninstaller and Setup.exe.
# It needs an Azure sign-in that may use the certificate profile (the release workflow's
# azure/login, from GitHub's OIDC token: no password or key anywhere) and the ArtifactSigning
# module at the version below, the one azure/artifact-signing-action v2.0.0 uses.
#
#   pwsh scripts/authenticode.ps1 <file>
param([Parameter(Mandatory)][string]$File)
$ErrorActionPreference = 'Stop'

Import-Module ArtifactSigning -RequiredVersion 0.1.8
Invoke-ArtifactSigning -Endpoint 'https://eus.codesigning.azure.net/' `
    -CodeSigningAccountName 'SlateAccount' -CertificateProfileName 'Slate' `
    -Files $File -FileDigest SHA256 `
    -TimestampRfc3161 'http://timestamp.acs.microsoft.com' -TimestampDigest SHA256

# A signing failure must stop the build here, not surface as an unsigned file in the release.
# (Inno Setup hands over the uninstaller under a temporary name; the release checks it once
# installed.)
if ([IO.Path]::GetExtension($File) -eq '.exe') {
    $s = Get-AuthenticodeSignature -LiteralPath $File
    if ($s.Status -ne 'Valid') { throw "$File isn't validly signed after signing: $($s.Status) $($s.StatusMessage)" }
}
