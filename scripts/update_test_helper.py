"""The Python half of scripts/test_update.ps1, which updates the previous
release to this build for real.

    python scripts/update_test_helper.py previous
        The newest published release older than this build, as X.Y.Z, or
        nothing if there is none. GITHUB_TOKEN, if set, goes to the API.

    python scripts/update_test_helper.py key
        A new throwaway Ed25519 key: its public half (base64) on the first
        line, then its PEM text. CI's rehearsal of the release's signing job
        signs with it.

    python scripts/update_test_helper.py sign RELEASE_DIR
        Write RELEASE_DIR/latest.json for this build's version, signed with a
        new throwaway key, and print the key's public half. Never the release
        key, so the folder is harmless if it's left behind.

    python scripts/update_test_helper.py drive OLD_SOURCE RELEASE_DIR KIND APP_DIR OLD_PID PUBLIC_KEY [DATA_DIR]
        Run the old release's own updater (its source, OLD_SOURCE, never this
        build's) the way its app does when Install is clicked: check
        RELEASE_DIR, download, verify, apply. Then end OLD_PID, the old copy
        that is running, as the app ends itself after apply. KIND and APP_DIR
        are what the frozen old copy would find for itself; PUBLIC_KEY stands
        in for the one built into it. The copy the update starts finds its data
        in DATA_DIR (installed), or beside it (portable).
"""

import atexit
import base64
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _version(text):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def previous():
    sys.path.insert(0, str(ROOT))
    from unifi_core import __version__, REPO
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases?per_page=50",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "UniFiStockWatcher-update-test"})
    if os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    with urllib.request.urlopen(req, timeout=30) as r:
        releases = json.load(r)
    current = _version(__version__)
    older = [(_version(r["tag_name"]), r["tag_name"][1:]) for r in releases
             if not r.get("draft") and not r.get("prerelease")
             and _version(r["tag_name"]) and _version(r["tag_name"]) < current
             # Releases before 3.0 have no in-app updater to test.
             and any(a.get("name") == "latest.json" for a in r.get("assets", []))]
    if older:
        print(max(older)[1])


def throwaway_key():
    """A new Ed25519 key: its PEM text, and its public half as base64."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pem, base64.b64encode(raw).decode()


def key():
    print("\n".join(reversed(throwaway_key())))


def sign(release_dir):
    sys.path.insert(0, str(ROOT))
    from scripts.sign_release import sign_release
    from unifi_core import __version__
    pem, public = throwaway_key()
    sign_release(release_dir, __version__, key_pem=pem)
    print(public)


def drive(old_source, release_dir, kind, app_dir, old_pid, public_key, data_dir=None):
    # The old code's own data folder is a scratch one, so nothing real is read
    # or written; and only the old code is importable, never this build's.
    scratch = tempfile.mkdtemp(prefix="usw-old-updater-")
    atexit.register(shutil.rmtree, scratch, True)
    os.environ["USW_DATA_DIR"] = scratch
    sys.path[0] = str(Path(old_source).resolve())
    import updater
    print(f"the {updater.__version__} updater, as a {kind} copy in {app_dir}")
    updater.UPDATE_PUBLIC_KEY = public_key
    updater.install_kind = lambda: kind
    updater.program_dir = lambda: Path(app_dir)
    u = updater.check(source=release_dir)
    if u is None:
        raise SystemExit(f"the {updater.__version__} updater found nothing newer in {release_dir}")
    print(f"found {u.version}: {u.asset['file']}, {u.asset['size']:,} bytes")
    path = updater.download(u)
    print(f"downloaded and verified: {path}")
    if data_dir:
        os.environ["USW_DATA_DIR"] = data_dir
    else:
        del os.environ["USW_DATA_DIR"]
    updater.apply(u, path)
    print("applied; ending the old copy")
    os.kill(int(old_pid), 9)                    # TerminateProcess on Windows
    if not updater._wait_for_exit(int(old_pid), timeout=30):
        raise SystemExit(f"the old copy ({old_pid}) didn't exit")


if __name__ == "__main__":
    command, args = sys.argv[1], sys.argv[2:]
    {"previous": previous, "key": key, "sign": sign, "drive": drive}[command](*args)
