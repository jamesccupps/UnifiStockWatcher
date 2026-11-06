"""Check a release folder the way installed copies will.

    python scripts/verify_release.py [dist] [expected-version] [--public-key KEY]

latest.json's signature against the public key built into the app
(update_key.py), then the size and SHA-256 of every file it lists. Exits
non-zero on any mismatch, so the release workflow stops before publishing - a
wrong UPDATE_SIGNING_KEY secret is caught here, not by users' updaters.

--public-key (base64 of a raw Ed25519 public key) checks against another key
instead: CI's rehearsal of the signing job signs with a throwaway one. The
release workflow never passes it.
"""

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from updater import MANIFEST_NAME, verify_manifest  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Check a release folder's latest.json and files.")
    ap.add_argument("dist", nargs="?", default=str(ROOT / "dist"))
    ap.add_argument("expected", nargs="?", help="the version latest.json must name (a leading v is ignored)")
    ap.add_argument("--public-key", help="check against this key, not the app's (CI's rehearsal only)")
    args = ap.parse_args()
    dist = Path(args.dist)
    expected = args.expected.lstrip("v") if args.expected else None
    m = verify_manifest((dist / MANIFEST_NAME).read_text(encoding="utf-8"), args.public_key)
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
    who = "a throwaway key (--public-key)" if args.public_key else "the key this app trusts"
    print(f"ok  latest.json  version {m['version']}, signed by {who}")


if __name__ == "__main__":
    main()
