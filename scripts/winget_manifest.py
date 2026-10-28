"""Write the winget manifests for a published release.

    python scripts/winget_manifest.py 3.0.0 [--setup path\\to\\UnifiStockWatcher-Setup.exe]

Writes build/winget/manifests/j/JamesCupps/UnifiStockWatcher/<version>/ (three
files) for a pull request to microsoft/winget-pkgs. The installer's SHA-256
is taken from --setup, or from the published release's SHA256SUMS.txt. Check
with `winget validate --manifest <that folder>` (and `winget install --manifest`
in a sandbox) before opening the PR. See CLAUDE.md.
"""

import hashlib
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
ID = "JamesCupps.UnifiStockWatcher"
REPO = "https://github.com/jamesccupps/UnifiStockWatcher"
APP_ID = "{5B1E0F7A-3C2D-4E8B-9A61-7D2C4F0B8E35}_is1"      # the .iss AppId
SCHEMA = "1.10.0"


def installer_sha(version, setup=None):
    if setup:
        return hashlib.sha256(Path(setup).read_bytes()).hexdigest().upper()
    url = f"{REPO}/releases/download/v{version}/SHA256SUMS.txt"
    with urlopen(url, timeout=30) as r:
        for line in r.read().decode().splitlines():
            sha, _, name = line.partition("  ")
            if name.strip() == "UnifiStockWatcher-Setup.exe":
                return sha.upper()
    raise SystemExit(f"no UnifiStockWatcher-Setup.exe in {url}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    version = sys.argv[1].lstrip("v")
    setup = sys.argv[sys.argv.index("--setup") + 1] if "--setup" in sys.argv else None
    sha = installer_sha(version, setup)
    out = ROOT / "build" / "winget" / "manifests" / "j" / "JamesCupps" / "UnifiStockWatcher" / version
    out.mkdir(parents=True, exist_ok=True)
    head = f"# yaml-language-server: $schema=https://aka.ms/winget-manifest.{{}}.{SCHEMA}.schema.json\n"
    (out / f"{ID}.yaml").write_text(head.format("version") + f"""
PackageIdentifier: {ID}
PackageVersion: {version}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: {SCHEMA}
""", encoding="utf-8")
    (out / f"{ID}.installer.yaml").write_text(head.format("installer") + f"""
PackageIdentifier: {ID}
PackageVersion: {version}
InstallerType: inno
Scope: user
InstallModes:
  - silent
  - silentWithProgress
UpgradeBehavior: install
ProductCode: '{APP_ID}'
ReleaseDate: null
Installers:
  - Architecture: x64
    InstallerUrl: {REPO}/releases/download/v{version}/UnifiStockWatcher-Setup.exe
    InstallerSha256: {sha}
ManifestType: installer
ManifestVersion: {SCHEMA}
""".replace("ReleaseDate: null\n", ""), encoding="utf-8")
    (out / f"{ID}.locale.en-US.yaml").write_text(head.format("defaultLocale") + f"""
PackageIdentifier: {ID}
PackageVersion: {version}
PackageLocale: en-US
Publisher: James Cupps
PublisherUrl: https://github.com/jamesccupps
PublisherSupportUrl: {REPO}/issues
Author: James Cupps
PackageName: UniFi Stock Watcher
PackageUrl: https://jamesccupps.github.io/UnifiStockWatcher/
License: MIT
LicenseUrl: {REPO}/blob/main/LICENSE
ShortDescription: Alerts you the moment a product is back in stock on the Ubiquiti Store.
Description: |-
  Watches the Ubiquiti Store (store.ui.com) for the products you pick and alerts you when one comes back in
  stock, with a notification, a sound and the product's page. Checks the whole catalog on a timer, shows
  restock estimates and a history of changes, runs in the notification area and can start with Windows.
  An independent project, not made or endorsed by Ubiquiti.
Moniker: unifi-stock-watcher
Tags:
  - ubiquiti
  - unifi
  - stock
  - restock
  - notifications
ReleaseNotesUrl: {REPO}/releases/tag/v{version}
ManifestType: defaultLocale
ManifestVersion: {SCHEMA}
""", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
