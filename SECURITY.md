# Security

UniFi Stock Watcher updates itself from this repository's releases, so a security problem in it reaches everyone
who uses it. Please report one privately, not in a public issue.

## Reporting a problem

Use GitHub's private reporting:
**[Report a vulnerability](https://github.com/jamesccupps/UnifiStockWatcher/security/advisories/new)** (the
repository's Security tab). Only the maintainer sees it. Please include:

- what's affected, and what someone could do with it;
- how to make it happen (a watch list file or a store response that shows it helps);
- the version (Settings → Updates) and which copy: installed, portable, or from source.

Once it's fixed, the release notes say what was fixed (and who found it, if you'd like that).

## What's covered

- `UnifiStockWatcher-Setup.exe`, `UnifiStockWatcher-win64.zip` and `latest.json` from the
  [releases](https://github.com/jamesccupps/UnifiStockWatcher/releases), built and signed by this repository's
  GitHub Actions workflow;
- the updater: it installs a download only if `latest.json` is signed with the Ed25519 key built into the app
  (`update_key.py`, checked by `ed25519.py`) and the download matches the size and SHA-256 that file lists;
- what the app does with data from outside it: store responses, and watch lists imported from a file (product
  ids go into store URLs, so they are validated; notification text reaches PowerShell only as data);
- the website at `https://jamesccupps.github.io/UnifiStockWatcher/`.

Only the latest release gets fixes: the app offers its updates by itself.
