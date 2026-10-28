"""In-app updates: a signed release manifest, a verified download, an install.

Each release carries `latest.json`:

    {"payload": base64(JSON), "signature": base64(Ed25519 over the payload)}

    payload = {"app": "unifi-stock-watcher", "version": "3.0.0",
               "published": "...",
               "installer": {"file": "UnifiStockWatcher-Setup.exe", "size": N, "sha256": "..."},
               "portable":  {"file": "UnifiStockWatcher-win64.zip",  "size": N, "sha256": "..."}}

The app checks the signature against the public key built into it
(update_key.py), and only then trusts the sizes and hashes; a download that
doesn't match them is deleted. Nothing is downloaded without a click.

Installed copies (Setup.exe; Inno Setup's unins000.exe beside the program) run
the new installer silently, which waits for this copy to exit and starts the
new one. Portable copies unzip the new version to a temporary folder and start
*that* copy with --finish-update: it waits for this one to exit, swaps the
program files in place (never the watch list beside them), and starts the
updated app. If the swap fails, everything is put back and the old version is
started again.

What this reads from a release can never change, as every installed copy
reads it: the asset names, the manifest's shape, the tag `vX.Y.Z`.
"""

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import ed25519
from update_key import UPDATE_PUBLIC_KEY
from unifi_core import (__version__, REPO, BASE_DIR, PORTABLE_MARKER,
                        program_dir, is_portable, _user_data_dir)

log = logging.getLogger("unifi_watcher.update")

APP_KEY        = "unifi-stock-watcher"
MANIFEST_NAME  = "latest.json"
INSTALLER_NAME = "UnifiStockWatcher-Setup.exe"
PORTABLE_NAME  = "UnifiStockWatcher-win64.zip"
GUI_EXE        = "UnifiStockWatcher.exe"
DEFAULT_SOURCE = f"https://github.com/{REPO}/releases/latest/download/{MANIFEST_NAME}"
RELEASES_URL   = f"https://github.com/{REPO}/releases"
UPDATE_LOG     = BASE_DIR / "update.log"

MAX_MANIFEST = 64 * 1024
MAX_DOWNLOAD = 300 * 1024 * 1024

_VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,6})$")
_FILE_RE    = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SHA_RE     = re.compile(r"^[0-9a-f]{64}$")
_GITHUB_LATEST = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/releases/latest/download/latest\.json$")


class UpdateError(Exception):
    """Anything that stops an update: a bad signature, a short download..."""


def parse_version(v):
    m = _VERSION_RE.match(v) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def update_source():
    """Where latest.json comes from: USW_UPDATE_URL (a URL or a folder, for
    testing and for anyone mirroring releases) or this repo's latest release."""
    return os.environ.get("USW_UPDATE_URL") or DEFAULT_SOURCE


def install_kind():
    """'source', 'installed' (Setup.exe), 'portable' (the zip), or 'folder'
    (a frozen build that is neither, e.g. copied by hand)."""
    if not getattr(sys, "frozen", False):
        return "source"
    if is_portable():
        return "portable"
    if (program_dir() / "unins000.exe").exists():
        return "installed"
    return "folder"


# ── Manifest ─────────────────────────────────────────────────────────────────

def _check_asset(asset, name):
    if asset is None:
        return None
    if not isinstance(asset, dict):
        raise UpdateError(f"{name}: not an object")
    f, size, sha = asset.get("file"), asset.get("size"), asset.get("sha256")
    # A bare file name: the manifest says what to fetch, never from where.
    if not (isinstance(f, str) and _FILE_RE.match(f)):
        raise UpdateError(f"{name}: bad file name {f!r}")
    if not (isinstance(size, int) and not isinstance(size, bool) and 0 < size <= MAX_DOWNLOAD):
        raise UpdateError(f"{name}: bad size {size!r}")
    if not (isinstance(sha, str) and _SHA_RE.match(sha)):
        raise UpdateError(f"{name}: bad sha256")
    return {"file": f, "size": size, "sha256": sha}


def verify_manifest(text, public_key=None):
    """The manifest's payload as a dict, if and only if it is signed by
    `public_key` (base64 of the raw 32-byte key; the one built into the app
    by default) and well-formed."""
    public_key = public_key or UPDATE_PUBLIC_KEY
    try:
        outer = json.loads(text)
        payload = base64.b64decode(outer["payload"], validate=True)
        signature = base64.b64decode(outer["signature"], validate=True)
        key = base64.b64decode(public_key, validate=True)
    except (ValueError, KeyError, TypeError) as e:
        raise UpdateError(f"latest.json is malformed: {e}") from e
    if not ed25519.verify(key, payload, signature):
        raise UpdateError("latest.json isn't signed with the key this app trusts")
    try:
        m = json.loads(payload)
    except ValueError as e:
        raise UpdateError("latest.json's payload isn't JSON") from e
    if not isinstance(m, dict) or m.get("app") != APP_KEY:
        raise UpdateError("latest.json is for another app")
    if parse_version(m.get("version")) is None:
        raise UpdateError(f"latest.json has a bad version {m.get('version')!r}")
    return {
        "version":   m["version"],
        "published": m.get("published") if isinstance(m.get("published"), str) else None,
        "installer": _check_asset(m.get("installer"), "installer"),
        "portable":  _check_asset(m.get("portable"), "portable"),
    }


# ── Fetching ─────────────────────────────────────────────────────────────────

def _is_url(source):
    return urlsplit(source).scheme in ("http", "https")


def asset_location(source, version, name):
    """Where a release file lives, next to its manifest.

    For GitHub's /releases/latest/ URL the file comes from that version's own
    tag instead: a release published between reading the manifest and the
    download would otherwise hand over the next version's file (refused by
    its hash, but a failed update nonetheless).
    """
    m = _GITHUB_LATEST.match(source)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}/releases/download/v{version}/{name}"
    if _is_url(source):
        return urljoin(source, name)
    p = Path(source)
    return str((p.parent if p.suffix.lower() == ".json" else p) / name)


def _session():
    from unifi_core import get_session
    return get_session()


def _read_manifest(source, session=None):
    if _is_url(source):
        r = (session or _session()).get(source, timeout=20, stream=True)
        if r.status_code == 404:
            # The latest release has no latest.json: one from before 3.0, or
            # a release made by hand without it. Nothing to install.
            return None
        r.raise_for_status()
        data = r.raw.read(MAX_MANIFEST + 1, decode_content=True)
    else:
        p = Path(source)
        if p.is_dir():
            p = p / MANIFEST_NAME
        with open(p, "rb") as fh:
            data = fh.read(MAX_MANIFEST + 1)
    if len(data) > MAX_MANIFEST:
        raise UpdateError("latest.json is too big")
    return data.decode("utf-8")


@dataclass
class Update:
    version: str
    published: str | None
    kind: str            # install_kind() at check time
    asset: dict | None   # what this copy would download, None for source runs
    source: str

    @property
    def can_install(self):
        return self.asset is not None


def check(current=__version__, source=None, session=None):
    """The newer release, or None when this copy is up to date. Raises
    UpdateError (or a network error) when it can't tell."""
    source = source or update_source()
    text = _read_manifest(source, session)
    if text is None:
        log.info("no latest.json at %s; nothing to install", source)
        return None
    m = verify_manifest(text)
    if parse_version(m["version"]) <= (parse_version(current) or (0, 0, 0)):
        return None
    kind = install_kind()
    asset = {"installed": m["installer"], "portable": m["portable"],
             "folder": m["portable"]}.get(kind)
    return Update(m["version"], m["published"], kind, asset, source)


def download(update, dest_dir=None, progress=None, session=None):
    """Fetch the update's file, check its size and SHA-256, return its path.

    progress(done_bytes, total_bytes) is called as it arrives.
    """
    if not update.can_install:
        raise UpdateError("nothing to download for this kind of copy")
    a = update.asset
    dest_dir = Path(dest_dir or Path(tempfile.gettempdir()) / f"UnifiStockWatcher-update-{update.version}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    final, part = dest_dir / a["file"], dest_dir / (a["file"] + ".part")
    where = asset_location(update.source, update.version, a["file"])
    digest, done = hashlib.sha256(), 0
    try:
        with open(part, "wb") as out:
            if _is_url(where):
                r = (session or _session()).get(where, timeout=60, stream=True)
                r.raise_for_status()
                chunks = r.iter_content(256 * 1024)
            else:
                src = open(where, "rb")
                chunks = iter(lambda: src.read(256 * 1024), b"")
            try:
                for chunk in chunks:
                    done += len(chunk)
                    if done > a["size"]:
                        raise UpdateError(f"{a['file']} is bigger than latest.json says")
                    digest.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(done, a["size"])
            finally:
                if not _is_url(where):
                    src.close()
        if done != a["size"] or digest.hexdigest() != a["sha256"]:
            raise UpdateError(f"{a['file']} doesn't match latest.json "
                              f"({done} bytes, sha256 {digest.hexdigest()[:12]}…)")
        os.replace(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    log.info("%s %s downloaded and verified (%d bytes)", update.version, a["file"], done)
    return final


# ── Installing ───────────────────────────────────────────────────────────────

_DETACHED = 0x00000008 | 0x00000200     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def _spawn(args, cwd):
    kwargs = {"cwd": str(cwd), "close_fds": True,
              "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = _DETACHED
    return subprocess.Popen([str(a) for a in args], **kwargs)


def folder_is_writable(folder=None):
    folder = Path(folder or program_dir())
    probe = folder / f".write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _safe_extract(zip_path, dest):
    dest = Path(dest).resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise UpdateError(f"{info.filename}: outside the update folder")
        z.extractall(dest)


def stage(update, path):
    """Unzip a portable update and return the folder holding its exe."""
    staged = path.parent / "staged"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    _safe_extract(path, staged)
    for exe in [staged / GUI_EXE, *staged.glob(f"*/{GUI_EXE}")]:
        if exe.is_file():
            return exe.parent
    raise UpdateError(f"{path.name} has no {GUI_EXE}")


def apply(update, path):
    """Start installing `path`. The caller must exit straight away after."""
    if update.kind == "installed":
        log.info("starting the %s installer", update.version)
        _spawn([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/relaunch=1"],
               cwd=path.parent)
        return
    if not folder_is_writable():
        raise UpdateError(f"{program_dir()} can't be written to. Move the app to a "
                          "folder of your own, or download the new version by hand.")
    staged = stage(update, path)
    log.info("starting the staged %s to swap it in", update.version)
    # The helper's working folder is the staging area's parent, never the
    # program folder, which it is about to rename.
    _spawn([staged / GUI_EXE, "--finish-update", program_dir(), os.getpid()],
           cwd=staged.parent)


def _ulog(msg, path=UPDATE_LOG):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _wait_for_exit(pid, timeout=60):
    if sys.platform != "win32":
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.2)
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
    if not h:
        return True                                       # already gone
    try:
        return k32.WaitForSingleObject(h, int(timeout * 1000)) == 0
    finally:
        k32.CloseHandle(h)


def _retry(fn, tries=40, delay=0.25):
    for i in range(tries):
        try:
            return fn()
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(delay)


def _remove(p):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def swap_in(app_dir, staged):
    """Replace app_dir's program files with staged's. Data files stay put.

    Each item is renamed to <name>.old first and only then copied in; on any
    failure the copies are removed and the .old items renamed back.
    """
    app_dir, staged = Path(app_dir), Path(staged)
    names = [e.name for e in staged.iterdir()
             if not (e.name == PORTABLE_MARKER and not (app_dir / PORTABLE_MARKER).exists())]
    renamed, placed = [], []
    try:
        for n in names:
            target, old = app_dir / n, app_dir / (n + ".old")
            if old.exists():
                _retry(lambda: _remove(old))
            if target.exists():
                _retry(lambda: target.rename(old))
                renamed.append(n)
            src = staged / n
            if src.is_dir():
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
            placed.append(n)
    except BaseException:
        for n in placed:
            try:
                _retry(lambda: _remove(app_dir / n), tries=10)
            except OSError:
                pass
        for n in renamed:
            try:
                _retry(lambda: (app_dir / (n + ".old")).rename(app_dir / n), tries=10)
            except OSError:
                pass
        raise


def finish_update(app_dir, pid):
    """Run by the staged copy: swap it into app_dir, then start it there.
    Returns the process exit code."""
    app_dir, staged = Path(app_dir), program_dir()
    # This copy runs from the staging folder, whose own portable.txt would
    # point its data folder there; the log belongs with the app's.
    logfile = (app_dir if (app_dir / PORTABLE_MARKER).exists()
               else _user_data_dir()) / UPDATE_LOG.name
    _ulog(f"update: {__version__} waiting for {pid} to exit", logfile)
    if not _wait_for_exit(int(pid)):
        _ulog("update: the old copy didn't exit; giving up", logfile)
        return 1
    try:
        swap_in(app_dir, staged)
    except Exception as e:
        _ulog(f"update: failed, old version restored: {e!r}", logfile)
        _spawn([app_dir / GUI_EXE, "--update-failed"], cwd=app_dir)
        return 1
    _ulog(f"update: done, starting {__version__}", logfile)
    _spawn([app_dir / GUI_EXE, "--updated"], cwd=app_dir)
    return 0


def cleanup_after_update():
    """Remove what a swap leaves behind (*.old beside the program, staging
    folders in %TEMP%). Run by the updated app; anything still locked is
    left for next time."""
    for p in program_dir().glob("*.old"):
        try:
            _remove(p)
        except OSError:
            pass
    for p in Path(tempfile.gettempdir()).glob("UnifiStockWatcher-update-*"):
        shutil.rmtree(p, ignore_errors=True)
