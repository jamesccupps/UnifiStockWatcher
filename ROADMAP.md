# Roadmap

What's planned or been asked for, roughly in order. Ideas welcome as
[issues](https://github.com/jamesccupps/UnifiStockWatcher/issues/new?template=feature_request.yml).

## Next

- **Price changes.** Note when a watched product's price changes, in the history and optionally as an alert.
- **Per-product alerts.** Favourites only, or silence one product without removing it.
- **An automated update test.** Build N and N+1 in CI and update one to the other for real, installer and portable
  (CLAUDE.md has the manual procedure).
- **winget and SourceForge.** Submit `JamesCupps.UnifiStockWatcher` to winget-pkgs; set up the SourceForge mirror
  (both described in CLAUDE.md).

## Later, or if asked

- More than one phone service at once, and Telegram (which needs a chat id as well as a token).
- Toast notifications with buttons (*Open in store*, *Snooze*) through WinRT. Today's notifications are the tray
  icon's balloons, which Windows shows as toasts but without buttons.
- More stores, if Ubiquiti adds regions with the same site (only the four in `STORE_REGIONS` exist today).
- Linux and macOS: the core and the CLI already run there; the window does too, without a tray icon or
  notifications.
- Code signing (Authenticode), as Slate has through Azure Artifact Signing, to quiet SmartScreen on first run.
  Not needed for updates, which are checked against the app's own release key.

## Not planned

- Buying for you, or anything that logs in to a UI.com account: the app only reads the public catalog.
- Polling faster than every 15 seconds.
