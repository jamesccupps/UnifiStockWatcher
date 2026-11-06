"""Write latest.json for a release folder: the signed manifest the updater reads.

    python scripts/sign_release.py [dist] [version]

defaults: dist/, and unifi_core.__version__. Describes
UnifiStockWatcher-Setup.exe and UnifiStockWatcher-win64.zip (whichever are
there) by size and SHA-256, and signs the payload with the Ed25519 key in
USW_SIGNING_KEY_PEM (the key's text: the release workflow's signing job puts
the UPDATE_SIGNING_KEY secret there, so it's never written to disk), else
the file USW_SIGNING_KEY names, else
%USERPROFILE%\\.unifi-stock-watcher\\update-signing-key.pem.

Needs the `cryptography` package (requirements-sign.txt).
"""

import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_private_key

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unifi_core import __version__  # noqa: E402
from updater import APP_KEY, INSTALLER_NAME, PORTABLE_NAME, MANIFEST_NAME  # noqa: E402

DEFAULT_KEY = Path.home() / ".unifi-stock-watcher" / "update-signing-key.pem"


def describe(path):
    data = path.read_bytes()
    return {"file": path.name, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def sign_release(dist, version, key_file=None, key_pem=None):
    """Write dist/latest.json, signed with the PEM text `key_pem` or the key
    in the file `key_file`."""
    dist = Path(dist)
    payload = {"app": APP_KEY, "version": version,
               "published": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for key, name in (("installer", INSTALLER_NAME), ("portable", PORTABLE_NAME)):
        if (dist / name).is_file():
            payload[key] = describe(dist / name)
    if "installer" not in payload and "portable" not in payload:
        raise SystemExit(f"neither {INSTALLER_NAME} nor {PORTABLE_NAME} is in {dist}")
    raw = json.dumps(payload, separators=(",", ":")).encode()
    pem = key_pem.strip().encode() + b"\n" if key_pem else Path(key_file).read_bytes()
    key = load_pem_private_key(pem, password=None)
    out = dist / MANIFEST_NAME
    out.write_text(json.dumps({"payload": base64.b64encode(raw).decode(),
                               "signature": base64.b64encode(key.sign(raw)).decode()},
                              indent=2) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    dist = sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist"
    version = sys.argv[2] if len(sys.argv) > 2 else __version__
    key_pem = os.environ.get("USW_SIGNING_KEY_PEM")
    key_file = os.environ.get("USW_SIGNING_KEY") or DEFAULT_KEY
    print("signed:", sign_release(dist, version.lstrip("v"), key_file, key_pem))
