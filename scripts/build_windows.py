"""Build the Windows release files into dist/.

    python scripts/build_windows.py [--no-installer]

    dist/UnifiStockWatcher/            the program folder (PyInstaller)
    dist/UnifiStockWatcher-win64.zip   that folder plus portable.txt
    dist/UnifiStockWatcher-Setup.exe   the installer (needs Inno Setup 6)
    dist/latest.json                   the signed update manifest, when a
                                       signing key is available
    dist/SHA256SUMS.txt                hashes of the three files above

Needs requirements-dev.txt (PyInstaller; cryptography to sign). The signing
key is USW_SIGNING_KEY (a file path; CI writes the UPDATE_SIGNING_KEY secret
there) or %USERPROFILE%\\.unifi-stock-watcher\\update-signing-key.pem. Without
one, everything but latest.json is built, which is fine for testing.
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
    for exe in ("UnifiStockWatcher.exe", "UnifiStockWatcher-cli.exe"):
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


def installer():
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup 6 not found (set ISCC, or install it); skipping the installer")
        return None
    subprocess.run([iscc, f"/DAppVersion={__version__}", "/Q",
                    str(ROOT / "installer" / "UnifiStockWatcher.iss")], check=True, cwd=ROOT)
    return DIST / INSTALLER_NAME


def sign():
    key = os.environ.get("USW_SIGNING_KEY") or (DEFAULT_KEY if DEFAULT_KEY.exists() else None)
    if not key:
        print("no signing key; latest.json not written")
        return None
    from scripts.sign_release import sign_release
    return sign_release(DIST, __version__, key)


def checksums():
    lines = []
    for name in (INSTALLER_NAME, PORTABLE_NAME, MANIFEST_NAME):
        p = DIST / name
        if p.is_file():
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}")
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")


def main():
    shutil.rmtree(DIST, ignore_errors=True)
    version_resource()
    pyinstaller()
    print("zip:", portable_zip())
    if "--no-installer" not in sys.argv:
        print("installer:", installer())
    print("manifest:", sign())
    checksums()
    for p in sorted(DIST.iterdir()):
        if p.is_file():
            print(f"  {p.name:36} {p.stat().st_size:>12,} bytes")


if __name__ == "__main__":
    main()
