"""Build the Windows release files into dist/.

    python scripts/build_windows.py [--no-installer] [--no-sign]
    python scripts/build_windows.py --program
    python scripts/build_windows.py --package [--authenticode] [--no-sign]

    dist/UnifiStockWatcher/            the program folder (PyInstaller)
    dist/UnifiStockWatcher-win64.zip   that folder plus portable.txt
    dist/UnifiStockWatcher-Setup.exe   the installer (needs Inno Setup 6)
    dist/latest.json                   the signed update manifest, when a
                                       signing key is available
    dist/SHA256SUMS.txt                hashes of the three files above

Needs requirements-dev.txt (PyInstaller; cryptography to sign). The signing
key is the one scripts/sign_release.py finds (USW_SIGNING_KEY_PEM, the file
USW_SIGNING_KEY, or %USERPROFILE%\\.unifi-stock-watcher\\update-signing-key.pem).
Without one, or with --no-sign, everything but latest.json is built, which is
fine for testing. The release workflow builds with --no-sign and signs in a
job of its own, so the key is never on the machine where PyInstaller and the
installer's tools run; a local build signs, for a release made by hand.

The release also splits the build in two. --program makes only the program
folder (PyInstaller). --package makes the zip, the installer and the checksums
from a program folder already in dist/, with the standard library and Inno
Setup alone. With --authenticode it first has the two exes code-signed, and
Inno Setup the uninstaller and Setup.exe, through scripts/authenticode.ps1
(Azure Artifact Signing; it needs an Azure sign-in). The release's authenticode
job runs --package --authenticode --no-sign, so nothing from PyPI runs where
the code-signing identity is.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unifi_core import __version__, APP_NAME, PORTABLE_MARKER  # noqa: E402
from updater import INSTALLER_NAME, PORTABLE_NAME, MANIFEST_NAME  # noqa: E402

DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_DIR = DIST / "UnifiStockWatcher"
EXES = ("UnifiStockWatcher.exe", "UnifiStockWatcher-cli.exe")
AUTHENTICODE = ROOT / "scripts" / "authenticode.ps1"
DEFAULT_KEY = Path.home() / ".unifi-stock-watcher" / "update-signing-key.pem"

PORTABLE_NOTE = f"""This file makes {APP_NAME} portable: it keeps its watch list,
settings and history in this folder rather than in %APPDATA%.
Delete this file to use %APPDATA%\\{APP_NAME} instead.
"""


def version_resource():
    major, minor, patch = (int(x) for x in __version__.split("."))
    BUILD.mkdir(exist_ok=True)
    (BUILD / "version_info.txt").write_text(f"""# Written by scripts/build_windows.py
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({major}, {minor}, {patch}, 0), prodvers=({major}, {minor}, {patch}, 0),
                    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'James Cupps'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'UnifiStockWatcher'),
      StringStruct('LegalCopyright', 'Copyright (c) James Cupps. MIT license.'),
      StringStruct('OriginalFilename', 'UnifiStockWatcher.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def pyinstaller():
    subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "UnifiStockWatcher.spec"),
                    "--noconfirm", "--clean", "--distpath", str(DIST),
                    "--workpath", str(BUILD / "pyinstaller")], check=True, cwd=ROOT)
    for exe in EXES:
        if not (APP_DIR / exe).is_file():
            raise SystemExit(f"PyInstaller produced no {exe}")
    if (APP_DIR / PORTABLE_MARKER).exists():
        raise SystemExit("portable.txt must not be in the installer's folder")


def portable_zip():
    """The program folder, under a top folder of its own, plus portable.txt."""
    out = DIST / PORTABLE_NAME
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(APP_DIR.rglob("*")):
            if p.is_file():
                z.write(p, Path("UnifiStockWatcher") / p.relative_to(APP_DIR))
        z.writestr(f"UnifiStockWatcher/{PORTABLE_MARKER}", PORTABLE_NOTE)
        for doc in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
            if (ROOT / doc).exists():
                z.write(ROOT / doc, f"UnifiStockWatcher/{doc}")
    return out


def find_iscc():
    for p in (os.environ.get("ISCC"),
              shutil.which("iscc"),
              Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"):
        if p and Path(p).is_file():
            return str(p)
    return None


def authenticode(path):
    subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(AUTHENTICODE), str(path)],
                   check=True)


def installer(code_sign=False):
    iscc = find_iscc()
    if not iscc:
        if code_sign:
            raise SystemExit("Inno Setup 6 not found: a code-signed build must include the installer")
        print("Inno Setup 6 not found (set ISCC, or install it); skipping the installer")
        return None
    args = [iscc, f"/DAppVersion={__version__}", "/Q"]
    if code_sign:
        # Inno Setup runs this for the uninstaller and Setup.exe: $f is the file (quoted), $q a
        # quote. No literal quotes in the argument: Python would escape them as \", which ISCC's
        # own command-line parsing doesn't undo.
        args += ["/DSign", f"/Sauthenticode=pwsh -NoProfile -NonInteractive -File $q{AUTHENTICODE}$q $f"]
    subprocess.run(args + [str(ROOT / "installer" / "UnifiStockWatcher.iss")], check=True, cwd=ROOT)
    return DIST / INSTALLER_NAME


def sign(no_sign=False):
    if no_sign:
        print("--no-sign; latest.json not written")
        return None
    pem = os.environ.get("USW_SIGNING_KEY_PEM")
    key = os.environ.get("USW_SIGNING_KEY") or (DEFAULT_KEY if DEFAULT_KEY.exists() else None)
    if not pem and not key:
        print("no signing key; latest.json not written")
        return None
    from scripts.sign_release import sign_release
    return sign_release(DIST, __version__, key, pem)


def checksums():
    lines = []
    for name in (INSTALLER_NAME, PORTABLE_NAME, MANIFEST_NAME):
        p = DIST / name
        if p.is_file():
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}")
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    flags = set(argv)
    if flags - {"--program", "--package", "--authenticode", "--no-installer", "--no-sign"} \
            or {"--program", "--package"} <= flags:
        raise SystemExit(__doc__)
    code_sign = "--authenticode" in flags
    if code_sign and ("--package" not in flags or "--no-installer" in flags):
        raise SystemExit("--authenticode goes with --package, and a code-signed build includes the installer")

    if "--package" in flags:
        for exe in EXES:
            if not (APP_DIR / exe).is_file():
                raise SystemExit(f"--package needs the program folder: no {APP_DIR / exe}")
        for name in (PORTABLE_NAME, INSTALLER_NAME, MANIFEST_NAME, "SHA256SUMS.txt"):
            (DIST / name).unlink(missing_ok=True)
    else:
        shutil.rmtree(DIST, ignore_errors=True)
        version_resource()
        pyinstaller()

    if "--program" not in flags:
        if code_sign:
            for exe in EXES:
                authenticode(APP_DIR / exe)
        print("zip:", portable_zip())
        if "--no-installer" not in flags:
            print("installer:", installer(code_sign))
        print("manifest:", sign("--no-sign" in flags))
        checksums()
    for p in sorted(DIST.iterdir()):
        if p.is_file():
            print(f"  {p.name:36} {p.stat().st_size:>12,} bytes")


if __name__ == "__main__":
    main()
