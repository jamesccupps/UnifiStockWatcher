# Roadmap

What's planned or been asked for, roughly in order. Ideas welcome as
[issues](https://github.com/jamesccupps/UnifiStockWatcher/issues/new?template=feature_request.yml).

## Next

- **winget.** `JamesCupps.UnifiStockWatcher` 3.2.0 is submitted to winget-pkgs
  ([microsoft/winget-pkgs#470772](https://github.com/microsoft/winget-pkgs/pull/470772)): validated, waiting for a
  moderator to approve it. Later versions go in as their own pull requests (CLAUDE.md has the steps).

## Later, or if asked

- More than one phone service at once, and Telegram (which needs a chat id as well as a token).
- Toast notifications with buttons (*Open in store*, *Snooze*) through WinRT. Today's notifications are the tray
  icon's balloons, which Windows shows as toasts but without buttons.
- More stores, if Ubiquiti adds regions with the same site (only the four in `STORE_REGIONS` exist today).
- Linux and macOS: the core and the CLI already run there; the window does too, without a tray icon or
  notifications.

## Done

- **Code signing** (3.2.4): both programs, the installer and its uninstaller are signed as James Cupps through
  Azure Artifact Signing. Updates are still checked against the app's own release key as well.
- **Scoop**: `scoop install jamesccupps/unifistockwatcher` from the bucket
  [jamesccupps/scoop-bucket](https://github.com/jamesccupps/scoop-bucket), which picks up each release by itself.
- **SourceForge**: [unifistockwatcher](https://sourceforge.net/projects/unifistockwatcher/) mirrors each
  release's files from GitHub.

## Not planned

- Buying for you, or anything that logs in to a UI.com account: the app only reads the public catalog.
- Polling faster than every 15 seconds.
