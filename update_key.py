"""Public half of the release-signing key (scripts/make_signing_key.py).

The updater installs only releases whose latest.json is signed by the
matching private key, which never leaves the maintainer's machine and the
repository secret UPDATE_SIGNING_KEY.
"""

UPDATE_PUBLIC_KEY = "vmv0qGO0l1WB4ftAmx8/Nte+13rYf+nV6fCBaP3YhwQ="
