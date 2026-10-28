"""One-time setup: create the Ed25519 key that signs releases for the updater.

    python scripts/make_signing_key.py

The private key is written to %USERPROFILE%\\.unifi-stock-watcher\\update-signing-key.pem
(or USW_SIGNING_KEY) and never goes in the repo; the same text goes in the
repository secret UPDATE_SIGNING_KEY. The public key is written into
update_key.py and ships inside the app, which refuses any update not signed by
the private key. Back the private key up: lose it and installed copies can't
update in place any more (they would need one manual install of a build that
carries a new public key).

Needs the `cryptography` package (pip install cryptography); the app itself
does not.
"""

import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = Path(os.environ.get("USW_SIGNING_KEY")
                or Path.home() / ".unifi-stock-watcher" / "update-signing-key.pem")
PUBLIC_FILE = ROOT / "update_key.py"


def main():
    if KEY_FILE.exists():
        sys.exit(f"{KEY_FILE} already exists; refusing to replace it. Releases signed "
                 "with a new key can't update copies that trust the old one.")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    raw = key.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    PUBLIC_FILE.write_text(
        '"""Public half of the release-signing key (scripts/make_signing_key.py).\n\n'
        "The updater installs only releases whose latest.json is signed by the\n"
        "matching private key, which never leaves the maintainer's machine and the\n"
        'repository secret UPDATE_SIGNING_KEY.\n"""\n\n'
        f'UPDATE_PUBLIC_KEY = "{base64.b64encode(raw).decode()}"\n',
        encoding="utf-8", newline="\n")
    print(f"private key: {KEY_FILE}  (back this up; keep it out of the repo)")
    print(f"public key:  {PUBLIC_FILE}")


if __name__ == "__main__":
    main()
