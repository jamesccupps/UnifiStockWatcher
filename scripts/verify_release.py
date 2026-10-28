"""Check a release folder the way installed copies will.

    python scripts/verify_release.py [dist] [expected-version]

latest.json's signature against the public key built into the app
(update_key.py), then the size and SHA-256 of every file it lists. Exits
non-zero on any mismatch, so the release workflow stops before publishing - a
wrong UPDATE_SIGNING_KEY secret is caught here, not by users' updaters.
"""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from updater import MANIFEST_NAME, verify_manifest  # noqa: E402


def main():
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist")
    expected = sys.argv[2].lstrip("v") if len(sys.argv) > 2 else None
    m = verify_manifest((dist / MANIFEST_NAME).read_text(encoding="utf-8"))
    if expected and m["version"] != expected:
        sys.exit(f"latest.json says {m['version']}, expected {expected}")
    for kind in ("installer", "portable"):
        a = m[kind]
        if a is None:
            sys.exit(f"latest.json lists no {kind}")
        data = (dist / a["file"]).read_bytes()
        if len(data) != a["size"] or hashlib.sha256(data).hexdigest() != a["sha256"]:
            sys.exit(f"{a['file']}: size/sha256 don't match latest.json")
        print(f"ok  {a['file']}  {len(data)} bytes  sha256 {a['sha256']}")
    print(f"ok  latest.json  version {m['version']}, signed by the key this app trusts")


if __name__ == "__main__":
    main()
