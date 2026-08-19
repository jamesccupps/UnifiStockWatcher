"""Dependency bootstrap.

Imported by the entry points *before* unifi_core, so that a pip install here is
visible to unifi_core's own module-level ``import requests``. Installing after
that import has already failed only binds the name in the caller's namespace,
which is what previously produced a bare "NameError: name 'requests' is not
defined" on every store call.
"""

import importlib
import subprocess
import sys

_MISSING_MSG = (
    "UniFi Stock Watcher needs the 'requests' package.\n\n"
    "Install it with:\n"
    "    {py} -m pip install requests"
)


def _importable(name):
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def ensure_requests(auto_install=True):
    """Return True if `requests` is importable, installing it once if needed."""
    if _importable("requests"):
        return True
    if not auto_install:
        return False

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "requests", "--quiet"])
    except Exception:
        return False

    # A package installed after interpreter start is invisible until the
    # import system re-scans sys.path.
    importlib.invalidate_caches()
    return _importable("requests")


def missing_message():
    return _MISSING_MSG.format(py=sys.executable)
