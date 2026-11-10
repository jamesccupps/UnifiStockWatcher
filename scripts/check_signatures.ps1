# Fail unless every file is validly Authenticode-signed by -Publisher (the start of the signing
# certificate's subject) and timestamped. On a runner each result is also an annotation, which
# can be read without signing in to GitHub.
#
#   pwsh scripts/check_signatures.ps1 -Publisher 'CN=James Cupps, O=James Cupps' -Path <file>, <file>...
param(
    [Parameter(Mandatory)][string]$Publisher,
    [Parameter(Mandatory)][string[]]$Path
)
$ErrorActionPreference = 'Stop'

$bad = @()
foreach ($p in $Path) {
    $name = Split-Path $p -Leaf
    $s = Get-AuthenticodeSignature -LiteralPath $p
    $subject = if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '' }
    if ($s.Status -eq 'NotSigned') { $problem = 'not signed' }
    elseif ($s.Status -ne 'Valid') { $problem = "not validly signed ($($s.Status): $($s.StatusMessage))" }
    elseif (-not $subject.StartsWith($Publisher)) { $problem = "signed by $subject" }
    elseif (-not $s.TimeStamperCertificate) { $problem = 'signed but not timestamped' }
    else { $problem = '' }
    if ($problem) {
        $bad += $name
        "${name}: $problem"
        if ($env:GITHUB_ACTIONS) { "::error title=Signature::${name}: $problem" }
    } else {
        "${name}: signed by $Publisher, timestamped"
        if ($env:GITHUB_ACTIONS) { "::notice title=Signature::${name}: signed by $Publisher, timestamped" }
    }
}
if ($bad) { throw "not signed as they should be: $($bad -join ', ')" }
